"""Directory membership is a collection, not a selected-variant spec claim."""
from copy import deepcopy
import pytest

from daq_fae.domain_evidence import DaqEvidencePolicy
from daq_fae.domain_tools import DaqToolBox
from daq_fae.knowledge.records import access_fingerprint, record_fingerprint
from daq_fae.knowledge.releases import _activate_release, _publish_release
from src.agent.loop.answer_contract import AnswerDeliveryUnit, AnswerSubmission
from src.agent.loop.tools import ToolResult
from synthetic_release_helpers import load_fixture_active

_REF = {'path': 'fixture.md', 'sha256': 'a' * 64,
        'locator': {'kind': 'lines', 'start': 1, 'end': 2}}
SNAPSHOT = {'archive_manifest_sha256': 'f' * 64,
            'sources': [{'path': 'fixture.md', 'sha256': 'a' * 64,
                         'size': 20, 'kind': 'markdown'}],
            'chunks': [{'source_path': 'fixture.md', 'source_sha256': 'a' * 64,
                        'locator': _REF['locator'], 'text': 'Synthetic directory.'}]}
RELEASE_REVIEW = {'reviewer': 'fixture', 'reviewed_at': '2026-10-23',
                  'dev_batch': 'synthetic-collection'}


def _row(record_id, kind, data):
    return {'id': record_id, 'kind': kind, 'status': 'verified', 'data': data,
            'source_refs': [deepcopy(_REF)],
            'fact_review': {'reviewer': 'fixture', 'reviewed_at': '2026-10-23'},
            'access_review': {'reviewer': 'fixture', 'reviewed_at': '2026-10-23',
                              'view_roles': ['internal_fae'], 'forward_roles': []}}


def _directory(tmp_path):
    rows = []
    for suffix, resolution in [('small', '960x720'), ('large', '1280x960')]:
        row = _row('entity:nova-' + suffix, 'entity', {
            'name': 'NOVA', 'entity_type': 'variant', 'aliases': ['Nova Recorder']})
        row['scope'] = {'product': 'nova', 'resolution_variant': resolution,
                        'required_selectors': ['resolution_variant']}
        rows.append(row)
        claim = _row('claim:nova-' + suffix, 'claim', {
            'entity_id': row['id'], 'field': 'horizontal_fov',
            'value': 90, 'unit': 'deg', 'conditions': {}})
        claim['scope'] = deepcopy(row['scope'])
        rows.append(claim)
    component = _row('entity:probe', 'entity', {
        'name': 'Probe', 'entity_type': 'component'})
    component['scope'] = {'product': 'probe'}
    rows.append(component)
    for row in rows:
        row['fact_review']['record_sha256'] = record_fingerprint(row)
        row['access_review']['record_sha256'] = access_fingerprint(row)
    release = _publish_release(tmp_path, SNAPSHOT, rows, None, RELEASE_REVIEW)
    _activate_release(tmp_path, release)
    return load_fixture_active(tmp_path)


def test_directory_binds_all_variants_without_requiring_a_selected_variant(tmp_path):
    view = _directory(tmp_path)
    result = DaqToolBox(knowledge=view, role='internal_fae', requirements=[
        {'id': 'directory', 'capability': 'catalog', 'conditions': {}},
    ]).dispatch('catalog', {'query': '有哪些产品和部件？'})
    members = {'entity:nova-small', 'entity:nova-large', 'entity:probe'}
    assert {row['id'] for row in result.content['matches']} == members
    assert set(result.content['requirement_source_ids']['directory']) == members
    assert set(result.content['required_delivery_source_ids']['directory']) == members
    assert result.content['catalog_summary']['distinct_product_name_count'] == 1
    assert result.content['catalog_summary']['component_entry_count'] == 1


def test_directory_membership_does_not_certify_platform_or_selected_spec(tmp_path):
    view = _directory(tmp_path)
    box = DaqToolBox(knowledge=view, role='internal_fae', requirements=[
        {'id': 'platform', 'capability': 'catalog', 'conditions': {'platform': 'android'}},
        {'id': 'selected', 'capability': 'catalog',
         'conditions': {'resolution_variant': '960x720'}},
    ])
    result = box.dispatch('catalog', {'query': '目录'})
    assert 'platform' not in result.content['requirement_source_ids']
    assert result.content['requirement_source_ids']['selected'] == ['entity:nova-small']
    assert box.dispatch('lookup_spec', {'entity': 'NOVA',
                                      'field': 'horizontal_fov'}).status == 'not_found'


def test_named_catalog_is_a_union_with_a_separate_summary_and_no_unknown_member_certification(tmp_path):
    view = _directory(tmp_path)
    box = DaqToolBox(knowledge=view, role='internal_fae', requirements=[
        {'id': 'both', 'capability': 'catalog', 'entities': ['NOVA', 'Probe']},
        {'id': 'component', 'capability': 'catalog', 'entities': ['Probe']},
        {'id': 'unknown', 'capability': 'catalog', 'entities': ['Probe', 'Unreleased Model']},
    ])
    content = box.dispatch('catalog', {'query': '指定的产品和部件'}).content
    assert len(content['requirement_source_ids']['both']) == 3
    assert content['requirement_source_ids']['component'] == ['entity:probe']
    assert content['catalog_by_requirement']['component']['distinct_product_name_count'] == 0
    assert content['catalog_by_requirement']['component']['component_entry_count'] == 1
    assert 'unknown' not in content['requirement_source_ids']


def test_specification_overview_exposes_the_same_complete_collection_contract(tmp_path):
    view = _directory(tmp_path)
    conditions = {'resolution_variant': '960x720'}
    content = DaqToolBox(knowledge=view, role='internal_fae', requirements=[
        {'id': 'overview', 'capability': 'lookup_spec', 'field': 'all_reviewed_specs',
         'entities': ['entity:nova-small'], 'conditions': conditions},
    ]).dispatch('lookup_spec', {'entity': 'entity:nova-small',
                               'field': 'all_reviewed_specs', 'conditions': conditions}).content
    assert content['required_delivery_source_ids']['overview'] == ['claim:nova-small']


@pytest.mark.parametrize('extra', [
    {'conditions': {'resolution_variant': '960x720', 'resolution': '400x300'}},
    {'reason': 'intent_or_entity_not_grounded'},
    {'conditions_authority': 'platform_context_unverified'},
])
def test_overview_cannot_certify_unknown_conditions_or_ungrounded_scope(tmp_path, extra):
    view = _directory(tmp_path)
    requirement = {'id': 'overview', 'capability': 'lookup_spec',
                   'field': 'all_reviewed_specs', 'entities': ['entity:nova-small'],
                   'conditions': {'resolution_variant': '960x720'}, **extra}
    result = DaqToolBox(knowledge=view, role='internal_fae', requirements=[requirement]).dispatch(
        'lookup_spec', {'entity': 'entity:nova-small', 'field': 'all_reviewed_specs',
                        'conditions': requirement['conditions']})
    assert 'overview' not in result.content.get('matched_requirement_ids', [])


def _observed_directory():
    session = DaqEvidencePolicy().begin({'requirements': [
        {'id': 'directory', 'capability': 'catalog', 'delivery_required': True},
    ]})
    members = ['entity:nova-small', 'entity:nova-large', 'entity:probe']
    session.observe('catalog', ToolResult(status='ok', content={
        'matched_requirement_ids': ['directory'],
        'requirement_source_ids': {'directory': members},
        'required_delivery_source_ids': {'directory': members},
    }, sources=[{'type': 'daq_governed_claim', 'verification_status': 'verified',
                 'release_id': 'fixture-v1', 'source_id': member} for member in members]))
    return session


def test_partial_directory_delivery_is_rejected_even_when_goal_is_satisfied():
    session = _observed_directory()
    decision = session.evaluate(AnswerSubmission(outcome='resolved',
        conclusion='目录如下。', delivery_units=(AnswerDeliveryUnit(
            'directory', ('entity:probe',), 'Probe 是一个部件。'),)))
    assert decision.action == 'request_evidence'
    assert decision.reason_code == 'daq_collection_delivery_incomplete'
    assert 'entity:nova-small' in decision.notice
    assert 'entity:nova-large' in decision.notice


def test_complete_directory_can_merge_variants_but_not_omit_their_sources():
    session = _observed_directory()
    decision = session.evaluate(AnswerSubmission(outcome='resolved',
        conclusion='目录有一款产品，另有一个部件。', delivery_units=(
            AnswerDeliveryUnit('directory', ('entity:nova-small', 'entity:nova-large'),
                               'NOVA 有两个已收录变体，按产品计为一款。'),
            AnswerDeliveryUnit('directory', ('entity:probe',), 'Probe 为部件。'),
        )))
    assert decision.action == 'allow'


def test_safe_abstention_does_not_waive_known_directory_members():
    session = _observed_directory()
    decision = session.evaluate(AnswerSubmission(outcome='safe_abstained',
        missing='公司其他产品是否已全部收录未知。', delivery_units=(
            AnswerDeliveryUnit('directory', ('entity:probe',), '只有 Probe。'),
        )))
    assert decision.reason_code == 'daq_collection_delivery_incomplete'
