"""Local custody waiver is explicit and never accepted by default loaders."""
from copy import deepcopy
import json

import pytest

from daq_fae.knowledge import releases
from daq_fae.knowledge.reviewed_view import ReviewedKnowledge
from test_release_readiness import candidate, sign, verify, observe
from scripts.archive_candidate_snapshot import create_archive


def local_bundle(tmp_path):
    b = candidate()
    source = tmp_path / 'originals'
    source.mkdir()
    (source / 'spec.md').write_text('Synthetic local source\n')
    archive = tmp_path / 'archive'
    create_archive(source, archive, '2026-10-21')
    # Local custody declaration stays bound by the same content approval.
    b['archive']['independent_durable'] = False
    b['archive']['custody_mode'] = 'local_verified'
    b['archive']['authorization_ref'] = 'project-owner-direction-2026-10-12'
    b['archive']['custody_ref'] = str(archive)
    b['scope']['execution_scope'] = 'loopback_dev_internal_fae_only'
    import hashlib
    from daq_fae.knowledge.records import access_fingerprint, record_fingerprint
    from daq_fae.knowledge.adjudication import digest
    from test_section_releases import approved_section
    identity = hashlib.sha256((archive / 'manifest.json').read_bytes()).hexdigest()
    original = json.loads((archive / 'manifest.json').read_text())['files'][0]
    b['snapshot']['archive_manifest_sha256'] = identity
    b['snapshot']['sources'] = [{**original, 'kind': 'markdown'}]
    b['snapshot']['chunks'][0]['source_sha256'] = original['sha256']
    b['snapshot']['chunks'][0]['text'] = 'Synthetic local source'
    for row in b['records']:
        row['source_refs'][0]['sha256'] = original['sha256']
        row['fact_review']['record_sha256'] = record_fingerprint(row)
        row['access_review']['record_sha256'] = access_fingerprint(row)
    section, body = approved_section(b['records'])
    b['sections'] = [section]
    b['bodies'] = {section['section_id']: body}
    source_hash = digest(b['snapshot']['sources'])
    b['archive'].update(manifest_sha256=identity, sources_sha256=source_hash,
                        retrieved_sources_sha256=source_hash)
    return sign(b)


def test_local_custody_does_not_weaken_existing_default(tmp_path):
    from daq_fae.knowledge.release_readiness import audit_readiness
    b = local_bundle(tmp_path)
    report = audit_readiness(b, verify_approval=verify)
    assert not report['ready'] and 'archive_custody' in report['reasons']


def test_explicit_dev_mode_requires_archive_and_exact_inventory(tmp_path):
    from daq_fae.knowledge.release_readiness import audit_readiness
    b = local_bundle(tmp_path)
    b['snapshot']['sources'][0]['size'] += 1
    sign(b)
    report = audit_readiness(b, verify_approval=verify, allow_local_dev=True)
    assert not report['ready'] and 'local_archive_inventory' in report['reasons']



def test_source_reviewed_sections_use_public_writer_and_real_active_loader(tmp_path):
    b = local_bundle(tmp_path)
    root = tmp_path / 'dev-release'
    review = {**b['review'], 'readiness': b}
    rid = releases.publish_release(root, b['snapshot'], b['records'], None, review,
        sections=b['sections'], bodies=b['bodies'], verify_approval=verify, allow_local_dev=True)
    assert releases.read_active_release(root) is None
    releases.activate_release(root, rid, verify_approval=verify, observe=observe, allow_local_dev=True)
    with pytest.raises(ValueError, match='local Dev'):
        ReviewedKnowledge.load_active(root, verify_approval=verify,
            runtime_release='synthetic-runtime', upstream_sha='a'*40)
    view = ReviewedKnowledge.load_active(root, verify_approval=verify,
        runtime_release='synthetic-runtime', upstream_sha='a'*40, allow_local_dev=True)
    assert view.release_id == rid and len(view.sections_for('internal_fae')) == 1
    assert view.sections_for('channel') == []


@pytest.mark.parametrize('field,value', [
    ('scope.execution_scope', 'production'),
    ('archive.authorization_ref', 'made-up-waiver'),
    ('archive.custody_mode', 'unchecked'),
    ('runtime.tasks_enabled', True),
])
def test_local_waiver_is_narrow_and_requires_explicit_scope(tmp_path, field, value):
    from daq_fae.knowledge.release_readiness import audit_readiness
    b = local_bundle(tmp_path)
    parent, leaf = field.split('.')
    b[parent][leaf] = value
    sign(b)
    assert not audit_readiness(b, verify_approval=verify, allow_local_dev=True)['ready']


def test_dev_source_change_and_failed_observation_preserve_pointer(tmp_path):
    from daq_fae.knowledge.release_readiness import stage_release, activate_checked
    b = local_bundle(tmp_path)
    root = tmp_path / 'dev-release'
    first = stage_release(root, b, verify_approval=verify, allow_local_dev=True)
    activate_checked(root, first, verify_approval=verify, observe=observe, allow_local_dev=True)
    b['previous_release'] = first
    second = stage_release(root, sign(b), verify_approval=verify, allow_local_dev=True)
    with pytest.raises(ValueError, match='observation'):
        activate_checked(root, second, verify_approval=verify,
            observe=lambda _: observe(first), allow_local_dev=True)
    assert releases.read_active_release(root)['release_id'] == first
    archive = __import__('pathlib').Path(b['archive']['custody_ref'])
    source = archive / 'files' / 'spec.md'
    source.chmod(0o600)
    source.write_text('changed bytes')
    source.chmod(0o400)
    with pytest.raises(ValueError, match='local_archive'):
        stage_release(root, b, verify_approval=verify, allow_local_dev=True)
    assert json.loads((root/'active.json').read_text())['release_id'] == first


def test_narrative_release_preserves_only_referenced_chunk_text_for_loader(tmp_path):
    from test_narrative_units import fixture
    from test_knowledge_releases import REVIEW
    s, body, rows, snap = fixture()
    extra = deepcopy(snap['chunks'][0])
    extra['locator'] = {'kind': 'lines', 'start': 3, 'end': 4}
    extra['text'] = 'unreviewed private source remainder'
    extra['text_sha256'] = __import__('hashlib').sha256(extra['text'].encode()).hexdigest()
    snap['chunks'].append(extra)
    rid = releases._publish_release(tmp_path, snap, rows, None, REVIEW,
                                   sections=[s], bodies={s['section_id']: body})
    manifest = releases._read_release(tmp_path, rid)[0]
    locations = manifest['source_locations']
    assert len(locations) == 2
    selected = next(c for c in locations if c['locator']['start'] == 1)
    assert selected['text'] == snap['chunks'][0]['text']
    unselected = next(c for c in locations if c['locator']['start'] == 3)
    assert 'text' not in unselected
    assert ReviewedKnowledge.from_manifest(rid, manifest).sections_for('internal_fae')[0]['body'] == body


def test_runtime_section_projection_hides_private_unit_provenance_and_remains_readable(tmp_path):
    from test_narrative_units import fixture
    from test_knowledge_releases import REVIEW
    from daq_fae.domain_tools import DaqToolBox
    s, body, rows, snap = fixture()
    # A real-looking source path is valid structured provenance, not answer text.
    name = '中文-产品规格书/EGO/spec.md'
    old_ref = s['source_refs'][0]
    old_ref['path'] = name
    snap['sources'][0]['path'] = name
    snap['chunks'][0]['source_path'] = name
    s['narrative_units'][0]['source_evidence'][0]['source_ref']['path'] = name
    for row in rows:
        row['source_refs'][0]['path'] = name
        from daq_fae.knowledge.records import record_fingerprint, access_fingerprint
        row['fact_review']['record_sha256'] = record_fingerprint(row)
        row['access_review']['record_sha256'] = access_fingerprint(row)
    from test_narrative_units import sign_unit, sign_section
    sign_unit(s['narrative_units'][0], body, rows)
    sign_section(s, body, rows)
    rid = releases._publish_release(tmp_path, snap, rows, None, REVIEW,
                                   sections=[s], bodies={s['section_id']: body})
    view = ReviewedKnowledge.from_manifest(rid, releases._read_release(tmp_path, rid)[0])
    assert 'narrative_units' not in view.sections_for('internal_fae')[0]
    assert view.sections_for('channel') == []
    result = DaqToolBox(question='EGO 1600', knowledge=view, role='internal_fae').dispatch(
        'read_doc', {'section_id': s['section_id']})
    assert result.status == 'ok' and result.content['matches'][0]['body'] == body
    assert 'narrative_units' not in json.dumps(result.content)


def test_local_role_limit_uses_status_instead_of_caller_answerability(tmp_path):
    from daq_fae.knowledge.release_readiness import audit_readiness
    from daq_fae.knowledge.records import access_fingerprint
    b = local_bundle(tmp_path)
    b['sections'], b['bodies'] = [], {}
    for row in b['records']:
        row['answerable'] = False
        row['access_review']['view_roles'] = ['channel']
        row['access_review']['record_sha256'] = access_fingerprint(row)
    report = audit_readiness(sign(b), verify_approval=verify, allow_local_dev=True)
    assert not report['ready'] and 'local_dev_roles' in report['reasons']


def test_readiness_release_keeps_reviewed_field_aliases_and_operating_guards(tmp_path):
    from daq_fae.knowledge.operator_release import PRO_VETO_ENTITIES
    b = local_bundle(tmp_path)
    b['scope']['domain_evidence'] = {
        'contract': 'daq-reviewed-operating-scope/v1',
        'field_aliases': {'resolution': ['分辨率']},
        'scoped_field_aliases': {'entity:ego-1600': {'像素': 'resolution'}},
        'veto_entities': deepcopy(PRO_VETO_ENTITIES),
    }
    from daq_fae.knowledge.release_readiness import stage_release
    rid = stage_release(tmp_path/'release', sign(b), verify_approval=verify, allow_local_dev=True)
    view = ReviewedKnowledge.from_manifest(rid, releases._read_release(tmp_path/'release',rid)[0])
    assert view.resolve_field('分辨率') == 'resolution'
    assert view.resolve_field('像素', 'entity:ego-1600') == 'resolution'
    assert view.is_operator_pro_v2
    assert view.operating_veto_entity_records()
    assert view.records_for('channel') == []


def test_invalid_domain_alias_or_veto_cannot_enter_readiness_release(tmp_path):
    b = local_bundle(tmp_path)
    b['scope']['domain_evidence'] = {
        'contract': 'daq-reviewed-operating-scope/v1',
        'field_aliases': {'nonexistent_field': ['分辨率']},
        'scoped_field_aliases': {}, 'veto_entities': [],
    }
    from daq_fae.knowledge.release_readiness import audit_readiness
    report = audit_readiness(sign(b), verify_approval=verify, allow_local_dev=True)
    assert not report['ready'] and 'content_contract' in report['reasons']
