"""Synthetic release tests; no product approval or real activation."""
from copy import deepcopy
import hashlib
import json

import pytest

from daq_fae.knowledge import releases
from synthetic_release_helpers import load_fixture_active
from daq_fae.knowledge.reviewed_view import ReviewedKnowledge
from daq_fae.knowledge.section_consistency import render_record
from test_knowledge_releases import SNAPSHOT, REVIEW, _entity, _record


def approved_section(records):
    row = records[1]
    body = render_record(row)
    section = {
        'section_id': 'section:synthetic-resolution', 'title': 'Synthetic resolution',
        'knowledge_type': 'product', 'scope': deepcopy(row['scope']),
        'source_refs': deepcopy(row['source_refs']),
        'body_sha256': hashlib.sha256(body.encode()).hexdigest(),
        'review_status': 'verified', 'view_roles': ['internal_fae'], 'forward_roles': [],
        'dependency_claim_ids': [row['id']], 'dependency_section_ids': [], 'link_ids': [],
        'record_assertions': [{k: deepcopy(row[k]) for k in
                               ('id', 'kind', 'status', 'scope', 'source_refs', 'data')}],
    }
    sign(section, body, records)
    return section, body


def sign(section, body, records):
    fingerprint = releases.section_fingerprint(section, body, records)
    for field in ('fact_review', 'permission_review'):
        section[field] = {'reviewer': 'synthetic-owner', 'reviewed_at': '2026-10-10',
                          'section_sha256': fingerprint}


def publish(root, records, section, body, previous=None):
    return releases._publish_release(root, SNAPSHOT, records, previous, REVIEW,
                                    sections=[section], bodies={section['section_id']: body})


def test_reviewed_sections_are_deterministic_versioned_and_role_filtered(tmp_path):
    rows = [_entity(), _record()]
    section, body = approved_section(rows)
    release = publish(tmp_path, rows, section, body)
    assert publish(tmp_path, rows, section, body) == release
    assert releases.read_active_release(tmp_path) is None
    releases._activate_release(tmp_path, release)
    view = load_fixture_active(tmp_path)
    assert view.manifest['format_version'] == 2
    assert view.manifest['section_count'] == 1
    assert view.manifest['dependency_index'][section['section_id']]['records'] == [rows[1]['id']]
    assert view.manifest['role_summary']['internal_fae']['sections'] == 1
    visible = view.sections_for('internal_fae')
    assert visible[0]['body'] == body
    assert view.sections_for('channel') == []
    visible[0]['body'] = 'changed'
    assert view.sections_for('internal_fae')[0]['body'] == body


@pytest.mark.parametrize('mutation', ['body', 'source', 'roles', 'candidate', 'mixed', 'missing', 'uncovered'])
def test_failed_section_publication_keeps_previous_pointer(tmp_path, mutation):
    rows = [_entity(), _record()]
    section, body = approved_section(rows)
    first = publish(tmp_path, rows, section, body)
    releases._activate_release(tmp_path, first)
    if mutation == 'body':
        body += '\nChanged prose'
        section['body_sha256'] = hashlib.sha256(body.encode()).hexdigest()
    elif mutation == 'source':
        section['source_refs'][0]['sha256'] = 'b' * 64
    elif mutation == 'roles':
        section['view_roles'] = ['administrator']
    elif mutation == 'candidate':
        section['review_status'] = 'candidate'
    elif mutation == 'mixed':
        section['view_roles'].append('channel')
        sign(section, body, rows)
    elif mutation == 'missing':
        section['dependency_section_ids'] = ['section:missing']
        sign(section, body, rows)
    else:
        body += '\nReviewed but unreconciled hard parameter: 900 Hz'
        section['body_sha256'] = hashlib.sha256(body.encode()).hexdigest()
        sign(section, body, rows)
    with pytest.raises(ValueError):
        publish(tmp_path, rows, section, body, first)
    assert releases.read_active_release(tmp_path)['release_id'] == first


@pytest.mark.parametrize('url', ['https://example.com/file', '//example.com/file', 'www.example.com', '[get](https://example.com)', 'mailto:user@example.com'])
def test_body_url_rejected_even_with_fresh_reviews(tmp_path, url):
    rows = [_entity(), _record()]
    section, body = approved_section(rows)
    body += '\n' + url
    section['body_sha256'] = hashlib.sha256(body.encode()).hexdigest()
    sign(section, body, rows)
    with pytest.raises(ValueError, match='URL'):
        publish(tmp_path, rows, section, body)


def test_link_id_must_be_reviewed_and_deliverable(tmp_path):
    rows = [_entity(), _record()]
    section, body = approved_section(rows)
    section['link_ids'] = ['link:unreviewed']
    sign(section, body, rows)
    with pytest.raises(ValueError, match='link'):
        publish(tmp_path, rows, section, body)


def test_v1_is_records_only_and_future_versions_are_rejected(tmp_path):
    rid = releases._publish_release(tmp_path, SNAPSHOT, [_entity(), _record()], None, REVIEW)
    releases._activate_release(tmp_path, rid)
    view = load_fixture_active(tmp_path)
    assert view.sections_for('internal_fae') == []
    for version in (1, 3):
        manifest = deepcopy(view.manifest)
        manifest['format_version'] = version
        manifest['sections'] = [{'body': 'unreviewed'}]
        with pytest.raises(ValueError, match='format'):
            ReviewedKnowledge.from_manifest(rid, manifest)


def test_v2_reader_rechecks_reviews_and_indices(tmp_path):
    rows = [_entity(), _record()]
    section, body = approved_section(rows)
    rid = publish(tmp_path, rows, section, body)
    manifest = json.loads((tmp_path/'releases'/rid/'manifest.json').read_text())
    for key, value in [('role_summary', {}), ('dependency_index', {}), ('section_count', 2)]:
        changed = deepcopy(manifest)
        changed[key] = value
        with pytest.raises(ValueError):
            ReviewedKnowledge.from_manifest(rid, changed)


def test_public_manifest_mutation_does_not_change_section_view(tmp_path):
    rows = [_entity(), _record()]
    section, body = approved_section(rows)
    rid = publish(tmp_path, rows, section, body)
    releases._activate_release(tmp_path, rid)
    view = load_fixture_active(tmp_path)
    view.manifest['sections'][0]['view_roles'] = ['channel']
    view.manifest['sections'][0]['body'] = 'injected'
    assert view.sections_for('internal_fae')[0]['body'] == body
    assert view.sections_for('channel') == []


def reviewed_link():
    from daq_fae.knowledge.records import record_fingerprint, access_fingerprint
    row = _record()
    row.update(id='link:synthetic', kind='link', data={
        'url': 'https://example.com/guide', 'title': 'Synthetic guide', 'link_type': 'documentation'})
    if row['kind'] == 'link':
        row['data']['page_evidence'] = {
            'title': 'Synthetic reviewed page', 'version': 'not_stated',
            'captured_at': '2026-10-08', 'valid_until': '2099-12-31',
            'snapshot_sha256': 'c' * 64, 'sku_scope': deepcopy(row['scope']),
        }
    row['access_review']['forward_roles'] = ['internal_fae']
    row['fact_review']['record_sha256'] = record_fingerprint(row)
    row['access_review']['record_sha256'] = access_fingerprint(row)
    row['link_review'] = {'reviewer': 'synthetic-link-owner', 'reviewed_at': '2026-10-10',
                          'final_url': row['data']['url'], 'record_sha256': record_fingerprint(row)}
    return row


def test_reviewed_link_ids_roundtrip_without_inline_url(tmp_path):
    rows = [_entity(), _record(), reviewed_link()]
    section, body = approved_section(rows)
    section['link_ids'] = ['link:synthetic']
    sign(section, body, rows)
    rid = publish(tmp_path, rows, section, body)
    releases._activate_release(tmp_path, rid)
    view = load_fixture_active(tmp_path)
    assert view.sections_for('internal_fae')[0]['link_ids'] == ['link:synthetic']
    assert 'https://' not in view.sections_for('internal_fae')[0]['body']
    assert view.records_for('internal_fae')[-1]['kind'] == 'link'


@pytest.mark.parametrize('mutation', ['candidate', 'unforwardable', 'url_changed'])
def test_referenced_link_must_retain_review_and_delivery_permission(tmp_path, mutation):
    from daq_fae.knowledge.records import access_fingerprint
    rows = [_entity(), _record(), reviewed_link()]
    section, body = approved_section(rows)
    section['link_ids'] = ['link:synthetic']
    if mutation == 'candidate':
        rows[-1]['status'] = 'candidate'
    elif mutation == 'unforwardable':
        rows[-1]['access_review']['forward_roles'] = []
        rows[-1]['access_review']['record_sha256'] = access_fingerprint(rows[-1])
    else:
        rows[-1]['data']['url'] += '/changed'
    sign(section, body, rows)
    with pytest.raises(ValueError):
        publish(tmp_path, rows, section, body)


def test_valid_new_source_still_invalidates_section_review(tmp_path):
    rows = [_entity(), _record()]
    section, body = approved_section(rows)
    snap = deepcopy(SNAPSHOT)
    new_ref = deepcopy(section['source_refs'][0])
    new_ref['locator']['end'] = 3
    snap['chunks'].append({'source_path': new_ref['path'], 'source_sha256': new_ref['sha256'],
                           'locator': new_ref['locator']})
    section['source_refs'].append(new_ref)
    with pytest.raises(ValueError, match='review.*stale'):
        releases._publish_release(tmp_path, snap, rows, None, REVIEW,
                                  sections=[section], bodies={section['section_id']: body})


def test_io_failure_preserves_pointer_and_cleans_stage(tmp_path, monkeypatch):
    rows = [_entity(), _record()]
    section, body = approved_section(rows)
    first = publish(tmp_path, rows, section, body)
    releases._activate_release(tmp_path, first)
    def fail(*args):
        raise OSError('synthetic storage failure')
    monkeypatch.setattr(releases.os, 'replace', fail)
    with pytest.raises(OSError):
        publish(tmp_path, rows, section, body, first)
    assert releases.read_active_release(tmp_path)['release_id'] == first
    assert not list((tmp_path/'releases').glob('.release-*'))


def test_b2_candidate_projection_is_still_ineligible(tmp_path):
    rows = [_entity(), _record()]
    section, body = approved_section(rows)
    section.update(review_status='candidate', fact_review=None, permission_review=None,
                   view_roles=[], forward_roles=[])
    with pytest.raises(ValueError, match='not reviewed'):
        publish(tmp_path, rows, section, body)


def stage_manifest(root, manifest):
    """A valid content hash does not replace contract validation."""
    data = releases._json_bytes(manifest)
    rid = hashlib.sha256(data).hexdigest()
    directory = root / 'releases' / rid
    directory.mkdir()
    (directory / 'manifest.json').write_bytes(data)
    return rid


@pytest.mark.parametrize('key,value', [
    ('sections', []), ('section_count', 0), ('source_locations', []),
    ('runtime_contract', 'daq-reviewed-sections-v2'), ('section_status_counts', {}),
    ('record_kind_counts', {}), ('role_summary', {}), ('source_index', {}),
    ('dependency_index', {}),
])
def test_v1_reserved_v2_fields_fail_before_pointer_change(tmp_path, key, value):
    first = releases._publish_release(tmp_path, SNAPSHOT, [_entity(), _record()], None, REVIEW)
    releases._activate_release(tmp_path, first)
    changed = deepcopy(releases.read_active_release(tmp_path)['manifest'])
    changed[key] = value
    forged = stage_manifest(tmp_path, changed)
    with pytest.raises(ValueError, match='format'):
        releases._activate_release(tmp_path, forged)
    assert load_fixture_active(tmp_path).release_id == first
    with pytest.raises(ValueError, match='format'):
        ReviewedKnowledge.from_manifest(forged, changed)


@pytest.mark.parametrize('key,value', [
    ('status_counts', {'candidate': 999}), ('section_status_counts', {'candidate': 1}),
    ('record_kind_counts', {'entity': 99}), ('role_summary', {}),
    ('source_index', {}), ('dependency_index', {}), ('section_count', 9),
    ('record_count', 9), ('answerable_count', 0), ('source_count', 0),
])
def test_v2_summary_tampering_fails_activation_and_loading(tmp_path, key, value):
    rows = [_entity(), _record()]
    section, body = approved_section(rows)
    first = publish(tmp_path, rows, section, body)
    releases._activate_release(tmp_path, first)
    changed = deepcopy(releases.read_active_release(tmp_path)['manifest'])
    changed[key] = value
    forged = stage_manifest(tmp_path, changed)
    with pytest.raises(ValueError):
        releases._activate_release(tmp_path, forged)
    assert load_fixture_active(tmp_path).release_id == first
    with pytest.raises(ValueError):
        ReviewedKnowledge.from_manifest(forged, changed)


def test_v1_unreadable_record_cannot_replace_previous_pointer(tmp_path):
    from daq_fae.knowledge.records import record_fingerprint, access_fingerprint
    rows = [_entity(), _record()]
    first = releases._publish_release(tmp_path, SNAPSHOT, rows, None, REVIEW)
    releases._activate_release(tmp_path, first)
    rows[1]['data']['value'] = 'https://example.com/unreviewed'
    rows[1]['fact_review']['record_sha256'] = record_fingerprint(rows[1])
    rows[1]['access_review']['record_sha256'] = access_fingerprint(rows[1])
    staged = releases._publish_release(tmp_path, SNAPSHOT, rows, None, REVIEW)
    with pytest.raises(ValueError, match='URL'):
        releases._activate_release(tmp_path, staged)
    assert load_fixture_active(tmp_path).release_id == first
