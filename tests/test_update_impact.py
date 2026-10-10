from copy import deepcopy

import pytest

from daq_fae.knowledge import update_impact as impact
from daq_fae.knowledge.records import record_fingerprint, access_fingerprint
from test_knowledge_releases import SNAPSHOT, REVIEW, _entity, _record
from daq_fae.knowledge.releases import _publish_release as publish_release, _activate_release as activate_release, read_active_release


def bundle():
    records = []
    sections = []
    coverage = []
    questions = []
    sources = []
    for suffix in ('one', 'two'):
        path = suffix + '.md'
        ref = {'path': path, 'sha256': 'a' * 64, 'locator': {'kind': 'lines', 'start': 1, 'end': 2}}
        row = {'id': 'claim:' + suffix, 'kind': 'claim', 'status': 'verified', 'scope': {},
               'source_refs': [ref], 'data': {'entity_id': 'entity:' + suffix,
               'field': 'rate', 'value': 10, 'unit': 'Hz', 'conditions': {}}}
        row['fact_review'] = {'reviewer': 'fae', 'reviewed_at': '2026-10-10', 'record_sha256': record_fingerprint(row)}
        row['access_review'] = {'reviewer': 'owner', 'reviewed_at': '2026-10-10', 'view_roles': ['internal_fae'], 'forward_roles': []}
        row['access_review']['record_sha256'] = access_fingerprint(row)
        records.append(row)
        sources.append({'path': path, 'sha256': 'a' * 64, 'kind': 'markdown'})
        sections.append({'section_id': 'section:' + suffix, 'source_refs': [ref], 'body_sha256': 'b' * 64,
                         'dependency_claim_ids': [row['id']], 'record_assertions': [{'id': row['id']}], 'view_roles': ['internal_fae']})
        coverage.append({'coverage_id': 'coverage:' + suffix, 'record_ids': [row['id']],
                         'section_ids': ['section:' + suffix], 'status': 'verified'})
        questions.append({'question_id': 'dev:' + suffix, 'coverage_ids': ['coverage:' + suffix]})
    return {'snapshot': {'sources': sources, 'extractor_version': '1',
            'chunks': [{'source_path': s['path'], 'source_sha256': s['sha256'],
                        'locator': {'kind': 'lines', 'start': 1, 'end': 2}} for s in sources]}, 'records': records,
            'sections': sections, 'coverage': coverage, 'questions': questions,
            'sku_definitions': {}, 'field_definitions': {'rate': {'unit': 'Hz'}}}


def test_changed_source_propagates_only_to_connected_question_and_no_input_mutation():
    before = bundle(); after = deepcopy(before)
    after['snapshot']['sources'][0]['sha256'] = 'c' * 64
    frozen = deepcopy((before, after))
    report = impact.plan_update(before, after)
    assert report['changes']['changed'] == ['one.md']
    assert report['review']['source_changed']['record_ids'] == ['claim:one']
    assert report['affected']['question_ids'] == ['dev:one']
    assert report['reusable_signature_record_ids'] == ['claim:two']
    assert (before, after) == frozen


def test_removal_withdraws_verified_evidence_even_when_row_already_removed():
    before = bundle(); after = deepcopy(before)
    after['snapshot']['sources'].pop(0)
    after['records'].pop(0); after['sections'].pop(0); after['coverage'].pop(0); after['questions'].pop(0)
    report = impact.plan_update(before, after)
    assert report['withdraw_positive_record_ids'] == ['claim:one']
    assert report['affected']['question_ids'] == ['dev:one']
    assert report['reusable_signature_record_ids'] == ['claim:two']


@pytest.mark.parametrize('status', ['unknown', 'unsupported'])
def test_new_source_revisits_only_matching_negative_scope(status):
    before = bundle()
    for r in before['records']: r['status'] = status
    after = deepcopy(before)
    after['snapshot']['sources'].append({'path': 'new.md', 'sha256': 'd' * 64, 'kind': 'markdown',
        'impact_scope': {'entity_ids': ['entity:one'], 'field_ids': ['rate']}})
    report = impact.plan_update(before, after)
    assert report['recheck_negative_ids'] == ['claim:one']
    assert report['affected']['question_ids'] == ['dev:one']
    assert after['records'][0]['status'] == status


def test_unscoped_new_source_conservatively_revisits_all_negatives():
    before = bundle()
    for r in before['records']: r['status'] = 'unknown'
    after = deepcopy(before)
    after['snapshot']['sources'].append({'path': 'new.md', 'sha256': 'd' * 64})
    assert impact.plan_update(before, after)['recheck_negative_ids'] == ['claim:one', 'claim:two']


@pytest.mark.parametrize('mutation,reason', [
    (lambda b: b['snapshot'].update(extractor_version='2'), 'extractor_changed'),
    (lambda b: b['records'][0]['access_review']['view_roles'].append('channel'), 'role_expanded'),
    (lambda b: b['sku_definitions'].update({'entity:one': {'sku': 'rev2'}}), 'sku_changed'),
    (lambda b: b['field_definitions'].update({'rate': {'unit': 'kHz'}}), 'field_changed'),
    (lambda b: b['records'][0]['source_refs'][0]['locator'].update(start=2), 'record_changed'),
    (lambda b: b['records'][0]['data']['conditions'].update(platform='test-os'), 'record_changed'),
])
def test_review_types_and_no_stale_signature_reuse(mutation, reason):
    before = bundle(); after = deepcopy(before); mutation(after)
    report = impact.plan_update(before, after)
    assert 'claim:one' in report['review'][reason]['record_ids']
    assert 'claim:one' not in report['reusable_signature_record_ids']


def test_unchanged_without_valid_existing_signature_is_not_reusable():
    before = bundle(); before['records'][0]['fact_review']['record_sha256'] = '0' * 64
    before['records'][1].pop('access_review')
    assert impact.plan_update(before, deepcopy(before))['reusable_signature_record_ids'] == []


def test_section_and_transitive_topology_dependencies_reach_question():
    before = bundle()
    before['records'][1]['dependency_record_ids'] = ['claim:one']
    before['sections'][1]['dependency_section_ids'] = ['section:one']
    after = deepcopy(before); after['sections'][0]['body_sha256'] = 'f' * 64
    report = impact.plan_update(before, after)
    assert report['affected']['question_ids'] == ['dev:one', 'dev:two']
    assert report['reusable_signature_record_ids'] == []


@pytest.mark.parametrize('field', ['record_ids', 'section_ids'])
def test_missing_graph_references_fail_closed(field):
    before = bundle(); before['coverage'][0][field] = ['missing']
    with pytest.raises(ValueError, match='dependency'):
        impact.plan_update(before, deepcopy(before))


def test_failed_impact_or_stale_release_leaves_active_pointer(tmp_path):
    first = publish_release(tmp_path, SNAPSHOT, [_entity(), _record()], None, REVIEW)
    activate_release(tmp_path, first)
    before = bundle(); after = deepcopy(before)
    after['questions'][0]['coverage_ids'] = ['missing']
    with pytest.raises(ValueError): impact.plan_update(before, after)
    changed = deepcopy(SNAPSHOT); changed['sources'] = []
    with pytest.raises(ValueError): publish_release(tmp_path, changed, [_entity(), _record()], first, REVIEW)
    assert read_active_release(tmp_path)['release_id'] == first


def test_deleted_source_with_surviving_stale_record_produces_withdrawal_plan():
    before = bundle(); after = deepcopy(before)
    after['snapshot']['sources'].pop(0)
    report = impact.plan_update(before, after)
    assert report['withdraw_positive_record_ids'] == ['claim:one']
    assert report['affected']['question_ids'] == ['dev:one']


def test_removed_record_with_surviving_section_produces_withdrawal_plan():
    before = bundle(); after = deepcopy(before); after['records'].pop(0)
    report = impact.plan_update(before, after)
    assert report['withdraw_positive_record_ids'] == ['claim:one']
    assert report['affected']['question_ids'] == ['dev:one']


def test_source_permission_expansion_without_byte_change_invalidates_dependents():
    before = bundle(); after = deepcopy(before)
    after['snapshot']['sources'][0]['view_roles'] = ['channel']
    report = impact.plan_update(before, after)
    assert report['review']['role_expanded']['record_ids'] == ['claim:one']
    assert report['reusable_signature_record_ids'] == ['claim:two']


def test_missing_extractor_identity_prevents_signature_reuse():
    before = bundle(); before['snapshot'].pop('extractor_version')
    assert impact.plan_update(before, deepcopy(before))['reusable_signature_record_ids'] == []


def test_extractor_change_does_not_invalidate_metadata_only_asset_record():
    before = bundle(); before['snapshot']['sources'][1]['kind'] = 'asset'
    after = deepcopy(before); after['snapshot']['extractor_version'] = '2'
    report = impact.plan_update(before, after)
    assert report['review']['extractor_changed']['record_ids'] == ['claim:one']
    assert report['reusable_signature_record_ids'] == ['claim:two']


def test_changed_definition_reaches_empty_coverage_cell_and_question():
    before = bundle()
    before['coverage'][0].update(record_ids=[], section_ids=[], field_id='rate', status='audited_no_source')
    after = deepcopy(before); after['field_definitions']['rate']['unit'] = 'kHz'
    assert 'dev:one' in impact.plan_update(before, after)['affected']['question_ids']


def test_source_timestamp_only_does_not_invalidate_unchanged_signatures():
    before = bundle(); after = deepcopy(before)
    after['snapshot']['sources'][0]['modified_at_utc'] = '2026-10-11T00:00:00Z'
    report = impact.plan_update(before, after)
    assert report['affected']['record_ids'] == []
    assert report['reusable_signature_record_ids'] == ['claim:one', 'claim:two']


def test_stale_source_hash_never_qualifies_as_reusable_signature():
    before = bundle(); before['snapshot']['sources'][0]['sha256'] = 'e' * 64
    assert impact.plan_update(before, deepcopy(before))['reusable_signature_record_ids'] == ['claim:two']


def test_conflict_alternative_source_changes_reach_coverage():
    before = bundle()
    before['records'][0]['data']['candidates'] = [{'value': 12, 'source_ref': deepcopy(before['records'][1]['source_refs'][0])}]
    after = deepcopy(before); after['snapshot']['sources'][1]['sha256'] = 'f' * 64
    assert impact.plan_update(before, after)['affected']['question_ids'] == ['dev:one', 'dev:two']


def test_permission_contraction_also_prevents_reuse():
    before = bundle(); after = deepcopy(before)
    after['records'][0]['access_review']['view_roles'] = []
    report = impact.plan_update(before, after)
    assert report['reusable_signature_record_ids'] == ['claim:two']
    assert 'role_expanded' not in report['review']


def test_missing_exact_locator_in_extraction_prevents_reuse():
    before = bundle(); before['snapshot']['chunks'].pop(0)
    assert impact.plan_update(before, deepcopy(before))['reusable_signature_record_ids'] == ['claim:two']


def test_changed_extraction_at_same_source_hash_and_version_requires_review():
    before = bundle(); after = deepcopy(before)
    after['snapshot']['chunks'][0]['text_sha256'] = '9' * 64
    report = impact.plan_update(before, after)
    assert report['review']['extraction_changed']['record_ids'] == ['claim:one']
    assert report['reusable_signature_record_ids'] == ['claim:two']


def test_changed_section_does_not_invalidate_context_only_record_or_other_section():
    before = bundle()
    before['sections'][0]['dependency_claim_ids'].append('claim:two')
    after = deepcopy(before); after['sections'][0]['body_sha256'] = 'c' * 64
    report = impact.plan_update(before, after)
    assert report['affected']['record_ids'] == ['claim:one']
    assert report['affected']['question_ids'] == ['dev:one']
    assert report['reusable_signature_record_ids'] == ['claim:two']


def stable_cells():
    return [{'coverage_kind': 'entity_field', 'entity_id': 'entity:' + key,
             'field_id': 'rate', 'status': 'audited_no_source', 'record_ids': []}
            for key in ('one', 'two')]


def bound_bundle(cells):
    coverage, questions = impact.bind_coverage_questions(cells)
    return {'snapshot': {'sources': [], 'extractor_version': '1'},
            'coverage': coverage, 'questions': questions}


@pytest.mark.parametrize('operation', ['insert', 'delete', 'reorder'])
def test_coverage_identity_survives_insert_delete_and_reorder(operation):
    cells = stable_cells(); before = bound_bundle(cells)
    changed = deepcopy(cells)
    if operation == 'insert':
        changed.insert(0, dict(cells[0], entity_id='entity:new'))
    elif operation == 'delete':
        changed.pop(0)
    else:
        changed.reverse()
    after = bound_bundle(changed)
    old_ids = {c['entity_id']: c['coverage_id'] for c in before['coverage']}
    new_ids = {c['entity_id']: c['coverage_id'] for c in after['coverage']}
    for entity in old_ids.keys() & new_ids.keys():
        assert old_ids[entity] == new_ids[entity]
    report = impact.plan_update(before, after)
    assert len(report['affected']['coverage_ids']) == (0 if operation == 'reorder' else 1)
    assert len(report['affected']['question_ids']) == (0 if operation == 'reorder' else 1)
    assert not report['affected']['record_ids']
    assert all(q['status'] == 'draft' and q['replay_status'] == 'not_replayed'
               and q['approval_status'] == 'not_approved' and q['frozen'] is False
               for q in after['questions'])


def test_same_coverage_identity_deduplicates_equal_rows_but_rejects_conflicts():
    cells = stable_cells()
    rows, questions = impact.bind_coverage_questions(cells + [deepcopy(cells[0])])
    assert len(rows) == len(questions) == 2
    with pytest.raises(ValueError, match='conflicting coverage identity'):
        impact.bind_coverage_questions(cells + [dict(cells[0], status='candidate')])


def test_identity_includes_typed_scope_but_not_status_or_dependency_membership():
    cell = stable_cells()[0]
    original = impact.bind_coverage_questions([cell])[0][0]['coverage_id']
    changed = impact.bind_coverage_questions([dict(cell, status='candidate', record_ids=['claim:new'])])[0][0]['coverage_id']
    assert original == changed
    scoped = impact.bind_coverage_questions([dict(cell, scope={'variant':'one'}),
                                            dict(cell, scope={'variant':'two'})])[0]
    assert len({c['coverage_id'] for c in scoped}) == 2


def test_section_identity_uses_stable_section_id_and_rejects_missing_dimensions():
    section = {'coverage_kind': 'section', 'section_ids': ['section:one'], 'status': 'candidate'}
    rows, _ = impact.bind_coverage_questions([section])
    assert rows == impact.bind_coverage_questions([section, deepcopy(section)])[0]
    for bad in ({}, {'coverage_kind': 'entity_field', 'entity_id':'entity:one'},
                {'coverage_kind':'section','section_ids':[]}):
        with pytest.raises(ValueError, match='coverage identity'):
            impact.bind_coverage_questions([bad])
