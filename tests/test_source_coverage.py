"""Coverage bookkeeping must preserve source identity and evidence boundaries."""
from copy import deepcopy

import pytest

from daq_fae.knowledge.source_coverage import build_source_coverage, build_field_matrix, write_quality_batch, source_audit_fingerprint


def fixture():
    ref = {'path': 'spec.pdf', 'sha256': 'a' * 64, 'locator': {'kind': 'page', 'page': 1}}
    snapshot = {'archive_manifest_sha256': 'b' * 64, 'extractor_version': '1',
                'sources': [{'path': 'spec.pdf', 'sha256': 'a' * 64, 'size': 5, 'kind': 'pdf'},
                            {'path': 'other/spec.pdf', 'sha256': 'c' * 64, 'size': 6, 'kind': 'pdf'},
                            {'path': 'image.png', 'sha256': 'd' * 64, 'size': 7, 'kind': 'asset'},
                            {'path': 'copy.png', 'sha256': 'd' * 64, 'size': 7, 'kind': 'asset'}],
                'chunks': [{'chunk_id': 'chunk1', 'source_path': 'spec.pdf', 'source_sha256': 'a' * 64,
                            'locator': deepcopy(ref['locator']), 'text_sha256': 'e' * 64},
                           {'chunk_id': 'chunk2', 'source_path': 'other/spec.pdf', 'source_sha256': 'c' * 64,
                            'locator': {'kind': 'page', 'page': 2}, 'text_sha256': 'f' * 64}]}
    dispositions = {'text_dispositions': [dict(snapshot['sources'][i], status='needs_fact_adjudication') for i in (0, 1)]}
    assets = {'asset_inventory': [dict(s, document_references=[]) for s in snapshot['sources'][2:]]}
    record = {'id': 'claim:one', 'kind': 'claim', 'status': 'candidate', 'scope': {'variant': 'one'},
              'data': {'entity_id': 'entity:one', 'field': 'rate', 'value': 30, 'unit': 'fps', 'conditions': {'mode': 'one'}},
              'source_refs': [ref]}
    section = {'section_id': 'section:one', 'source_refs': [ref], 'entity_id': 'entity:one'}
    return snapshot, dispositions, assets, record, section


def test_every_chunk_maps_to_exact_candidates_or_explicit_gap():
    snapshot, dispositions, assets, record, section = fixture()
    ledger = build_source_coverage(snapshot, dispositions, assets, [section], [record], [])
    assert ledger['summary'] == {'texts': 2, 'assets': 2, 'chunks': 2, 'mapped_chunks': 1, 'gap_chunks': 1}
    chunks = {row['chunk_id']: row for row in ledger['chunks']}
    assert chunks['chunk1']['candidate_sections'] == ['section:one']
    assert chunks['chunk1']['candidate_records'] == ['claim:one']
    assert chunks['chunk1']['source_review_status'] == 'not_audited'
    assert chunks['chunk2']['gap']['reason'] == 'no_candidate_or_published_mapping'
    assert all(row['same_name_different_bytes'] for row in ledger['files'] if row['kind'] == 'pdf')
    assert all(row['disposition'] == 'duplicate_bytes' for row in ledger['files'] if row['kind'] == 'asset')


@pytest.mark.parametrize('change', ['missing', 'duplicate', 'hash', 'locator', 'chunk_hash'])
def test_rejects_partial_stale_or_unlocatable_inputs(change):
    snapshot, dispositions, assets, record, section = fixture()
    if change == 'missing': dispositions['text_dispositions'].pop()
    if change == 'duplicate': assets['asset_inventory'].append(deepcopy(assets['asset_inventory'][0]))
    if change == 'hash': record['source_refs'][0]['sha256'] = '0' * 64
    if change == 'locator': record['source_refs'][0]['locator']['page'] = 9
    if change == 'chunk_hash': snapshot['chunks'][0]['source_sha256'] = '0' * 64
    with pytest.raises(ValueError):
        build_source_coverage(snapshot, dispositions, assets, [section], [record], [])


def test_matrix_keeps_conditions_and_existing_review_separate_from_candidates():
    _, _, _, candidate, section = fixture()
    published = deepcopy(candidate)
    published.update(status='verified', answerable=True, fact_review={'reviewer': 'prior source check'},
                     access_review={'view_roles': ['internal_fae']})
    conflict = deepcopy(candidate)
    conflict['id'] = 'claim:conflict'
    conflict['status'] = 'conflict'
    conflict['data']['conditions'] = {'mode': 'two'}
    matrix = build_field_matrix([candidate, conflict], [published], [section], [], [])
    assert len(matrix['cells']) == 2
    assert {cell['status'] for cell in matrix['cells']} == {'reviewed_published', 'conflict_pending'}
    reviewed = next(c for c in matrix['cells'] if c['status'] == 'reviewed_published')
    assert reviewed['scope'] == {'variant': 'one'}
    assert reviewed['conditions'] == {'mode': 'one'}
    assert reviewed['source_review_status'] == 'inherited_review_not_reperformed'
    assert reviewed['published_records'] == ['claim:one']
    assert reviewed['candidate_records'] == ['claim:one']


def test_inventory_absence_is_scoped_and_software_presence_is_not_support():
    gap = {'coverage_id': 'old:gap', 'coverage_kind': 'entity_field', 'entity_id': 'entity:one',
           'field_id': 'unknown', 'scope': {}, 'status': 'audited_no_source',
           'audit_scope': 'supplied_record_inventory_only', 'source_refs': [], 'record_ids': []}
    software = {'id': 'package:one', 'evidence_level': 'package_present', 'status': 'candidate',
                'conditions': {'entity': None}, 'source_refs': []}
    matrix = build_field_matrix([], [], [], [software], [gap])
    assert matrix['cells'][0]['status'] == 'not_audited'
    assert matrix['cells'][0]['inventory_gaps'] == [{'coverage_id': 'old:gap',
        'audit_scope': 'supplied_record_inventory_only', 'record_ids': [],
        'legacy_status': 'audited_no_source'}]
    assert matrix['cells'][0]['source_review_status'] == 'not_audited'
    assert matrix['software'][0]['status'] == 'candidate_pending'
    assert matrix['software'][0]['device_support'] == 'unknown'
    assert matrix['software'][0]['evidence_level'] == 'package_present'


def test_unreviewed_or_unsourced_negative_never_becomes_verified_unsupported():
    _, _, _, record, _ = fixture()
    record.update(status='unsupported', answerable=True)
    with pytest.raises(ValueError, match='negative'):
        build_field_matrix([], [record], [], [], [])


def test_private_batch_is_exclusive_and_keeps_approval_empty(tmp_path):
    batch = tmp_path / 'batch'
    hashes = write_quality_batch(batch, {'source-coverage.json': {'status': 'candidate'}})
    assert len(hashes['source-coverage.json']) == 64
    assert (batch / 'source-coverage.json').stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        write_quality_batch(batch, {'source-coverage.json': {}})


def test_cli_requires_explicit_release_and_never_defaults_to_active():
    from scripts.audit_daq_knowledge_quality import argument_parser
    parser = argument_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(['--archive', '/archive', '--output', '/out'])


def test_local_baseline_reports_pointer_difference_without_claiming_production():
    from scripts.audit_daq_knowledge_quality import baseline_identity
    current = {'record_count': 65, 'section_count': 0, 'record_kind_counts': {'claim': 55}}
    baseline = baseline_identity('reviewed', current, {'release_id': 'stale'})
    assert baseline['release_id'] == 'reviewed'
    assert baseline['local_active_release_id'] == 'stale'
    assert baseline['local_pointer_differs'] is True
    assert baseline['production_observation'] == 'not_performed'


def test_markdown_references_can_span_trimmed_blank_separator_lines():
    snapshot, dispositions, assets, record, section = fixture()
    snapshot['chunks'][0]['locator'] = {'kind': 'lines', 'start': 1, 'end': 2}
    snapshot['chunks'].append(dict(snapshot['chunks'][0], chunk_id='chunk3', locator={'kind': 'lines', 'start': 4, 'end': 5}))
    record['source_refs'][0]['locator'] = {'kind': 'lines', 'start': 1, 'end': 5}
    ledger = build_source_coverage(snapshot, dispositions, assets, [section], [record], [])
    assert ledger['summary']['mapped_chunks'] == 2


def test_candidate_conflict_remains_visible_beside_previously_published_value():
    _, _, _, candidate, _ = fixture()
    published = deepcopy(candidate)
    published.update(status='verified', answerable=True, fact_review={'reviewer': 'prior'},
                     access_review={'view_roles': ['internal_fae']})
    candidate.update(status='conflict')
    matrix = build_field_matrix([candidate], [published], [], [], [])
    assert matrix['cells'][0]['status'] == 'conflict_pending'
    assert matrix['cells'][0]['published_records'] == ['claim:one']


def test_system_inventory_uses_actual_source_candidate_topology_ids():
    from scripts.audit_daq_knowledge_quality import SYSTEMS
    assert 'ego-two-wristcam-hub-pc' in SYSTEMS
    assert len(SYSTEMS) == 5


def test_candidate_conflict_remains_visible_beside_published_negative_evidence():
    _, _, _, candidate, _ = fixture()
    published = deepcopy(candidate)
    published.update(id='claim:negative', status='unsupported', answerable=True,
                     fact_review={'reviewer': 'prior negative source check'},
                     access_review={'view_roles': ['internal_fae']})
    published['data']['value'] = False
    candidate.update(status='conflict')
    matrix = build_field_matrix([candidate], [published], [], [], [])
    assert len(matrix['cells']) == 1
    cell = matrix['cells'][0]
    assert cell['status'] == 'conflict_pending'
    assert cell['candidate_records'] == ['claim:one']
    assert cell['published_records'] == ['claim:negative']
    assert cell['source_refs'] == published['source_refs']
    assert cell['values'][1] == {'record_id': 'claim:negative', 'source': 'published',
                                  'data': published['data'], 'record_status': 'unsupported'}


def test_explicit_bounded_source_audit_can_record_no_source_without_support_claim():
    _, _, _, record, _ = fixture()
    gap = {'coverage_id': 'source:gap', 'coverage_kind': 'entity_field', 'entity_id': 'entity:one',
           'field_id': 'firmware_version', 'scope': {}, 'status': 'audited_no_source',
           'source_refs': record['source_refs'], 'record_ids': [],
           'source_audit': {'decision': 'audited_no_source', 'reviewer': 'Independent source reviewer',
                            'reviewed_at': '2026-10-21', 'audit_scope': 'spec.pdf physical page 1 only',
                            'source_refs': record['source_refs']}}
    gap['source_audit']['audit_sha256'] = source_audit_fingerprint(gap)
    matrix = build_field_matrix([], [], [], [], [gap])
    cell = matrix['cells'][0]
    assert cell['status'] == 'audited_no_source'
    assert cell['source_review_status'] == 'bounded_source_audit_recorded'
    assert cell['source_audit'] == gap['source_audit']
    assert cell['published_records'] == []


def test_incomplete_source_audit_does_not_upgrade_inventory_absence():
    gap = {'coverage_id': 'old:gap', 'coverage_kind': 'entity_field', 'entity_id': 'entity:one',
           'field_id': 'firmware_version', 'scope': {}, 'status': 'audited_no_source',
           'audit_scope': 'supplied_record_inventory_only', 'source_refs': [], 'record_ids': [],
           'source_audit': {'decision': 'audited_no_source', 'reviewer': 'name_only'}}
    matrix = build_field_matrix([], [], [], [], [gap])
    assert matrix['cells'][0]['status'] == 'not_audited'
    assert matrix['cells'][0]['source_review_status'] == 'not_audited'


def _source_audit_gap():
    ref = {'path': 'spec.pdf', 'sha256': 'a' * 64, 'locator': {'kind': 'page', 'page': 1}}
    gap = {'coverage_id': 'source:gap', 'coverage_kind': 'entity_field', 'entity_id': 'entity:one',
            'field_id': 'firmware_version', 'scope': {'product': 'one'}, 'conditions': {'mode': 'one'},
            'status': 'audited_no_source', 'record_ids': [], 'source_refs': [ref],
            'source_audit': {'decision': 'audited_no_source', 'reviewer': 'Independent source reviewer',
                             'reviewed_at': '2026-10-21', 'audit_scope': 'spec.pdf physical page 1 only',
                             'source_refs': [deepcopy(ref)]}}
    gap['source_audit']['audit_sha256'] = source_audit_fingerprint(gap)
    return gap


@pytest.mark.parametrize('changed', ['coverage_id', 'entity_id', 'field_id', 'scope', 'conditions', 'reference', 'audit_scope'])
def test_source_audit_cannot_be_reused_for_changed_coverage(changed):
    gap = _source_audit_gap()
    if changed in {'coverage_id', 'entity_id', 'field_id'}:
        gap[changed] += '-changed'
    elif changed in {'scope', 'conditions'}:
        gap[changed]['new_selector'] = 'different'
    elif changed == 'reference':
        gap['source_refs'][0]['locator']['page'] = 2
        gap['source_audit']['source_refs'][0]['locator']['page'] = 2
    else:
        gap['source_audit']['audit_scope'] = 'other page scope'
    cell = build_field_matrix([], [], [], [], [gap])['cells'][0]
    assert cell['status'] == 'not_audited'
    assert cell['source_review_status'] == 'not_audited'


def test_legacy_source_audit_without_fingerprint_stays_not_audited():
    gap = _source_audit_gap()
    del gap['source_audit']['audit_sha256']
    assert build_field_matrix([], [], [], [], [gap])['cells'][0]['status'] == 'not_audited'


@pytest.mark.parametrize('bad_ref', [
    {'path': '../spec.pdf'}, {'path': '/spec.pdf'}, {'path': 'sub/../spec.pdf'},
    {'path': 'sub//spec.pdf'}, {'path': 'sub\\spec.pdf'}, {'path': 'C:/spec.pdf'},
    {'path': 'spec\x00.pdf'}, {'sha256': 'g' * 64}, {'sha256': 'a' * 63},
    {'locator': {'kind': 'page', 'page': 0}}, {'locator': {'kind': 'page', 'page': True}},
    {'locator': {'kind': 'page', 'page': 1, 'extra': 2}}, {'locator': {'kind': 'page'}},
    {'locator': {'kind': 'lines', 'start': 0, 'end': 2}},
    {'locator': {'kind': 'lines', 'start': 5, 'end': 3}},
    {'locator': {'kind': 'lines', 'start': 1, 'end': True}},
    {'extra': 'unrecognized source attribute'},
])
def test_source_audit_rejects_malformed_references_even_when_bound(bad_ref):
    gap = _source_audit_gap()
    ref = deepcopy(gap['source_refs'][0])
    ref.update(bad_ref)
    gap['source_refs'] = [ref]
    gap['source_audit']['source_refs'] = [deepcopy(ref)]
    gap['source_audit']['audit_sha256'] = source_audit_fingerprint(gap)
    assert build_field_matrix([], [], [], [], [gap])['cells'][0]['status'] == 'not_audited'
