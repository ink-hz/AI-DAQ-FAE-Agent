"""A family guide supplies a conditional configuration, never SKU test proof."""
from copy import deepcopy

import pytest

from daq_fae.domain_tools import DaqToolBox
from daq_fae.knowledge.records import access_fingerprint, record_fingerprint
from daq_fae.knowledge.operator_release import PRO_VETO_ENTITIES
from daq_fae.knowledge.releases import _activate_release, _publish_release
from daq_fae.knowledge.reviewed_view import ReviewedKnowledge
from daq_fae.request_schema import DaqRequestSchema
from synthetic_release_helpers import load_fixture_active
from test_catalog_collection_contract import _directory, _row, SNAPSHOT, RELEASE_REVIEW


def _family_view(tmp_path):
    original = _directory(tmp_path / 'old')
    rows = deepcopy(original.manifest['records'])
    for row in rows:
        if row['kind'] == 'entity' and row['scope'].get('product') == 'nova':
            row['data']['family_entity_id'] = 'entity:nova-family'
    family = _row('entity:nova-family', 'entity', {
        'name': 'NOVA Series', 'entity_type': 'family', 'aliases': []})
    family['scope'] = {'product': 'nova'}
    adapter = _row('entity:adapter', 'entity', {'name': 'Adapter', 'entity_type': 'hub',
                                             'aliases': ['HUB']})
    adapter['scope'] = {'product': 'adapter'}
    other = _row('entity:nova-pro', 'entity', {'name': 'NOVA Pro', 'entity_type': 'device'})
    other['scope'] = {'product': 'nova-pro'}
    topo = _row('topology:nova-probe', 'topology', {
        'members': ['entity:nova-family', 'entity:probe', 'entity:adapter'],
        'query_members': ['entity:nova-family', 'entity:probe'],
        'prerequisite_members': ['entity:adapter'],
        'roles': {'entity:nova-family': 'master', 'entity:probe': 'slave'},
        'connections': ['Connect NOVA and Probe through Adapter.'],
        'power': {}, 'platform': 'pc', 'storage': 'device cards',
        'sync_target': 'documented pairing, precision not specified',
        'retrieval_aliases': ['同步采集', 'sync'],
        'task_intents': ['sync', 'synchronized_capture'],
        'evidence_conditions': {'task': 'synchronized_capture'},
    })
    topo['scope'] = {'product': 'nova-probe', 'mode': 'documented_pairing'}
    rows.extend([family, adapter, other, topo])
    for row in rows:
        row['fact_review']['record_sha256'] = record_fingerprint(row)
        row['access_review']['record_sha256'] = access_fingerprint(row)
    review = {**RELEASE_REVIEW, 'readiness': {'scope': {'domain_evidence': {
        'contract': 'daq-reviewed-operating-scope/v1', 'veto_entities': PRO_VETO_ENTITIES,
        'field_aliases': {}, 'scoped_field_aliases': {}}}}}
    root = tmp_path / 'family'
    release = _publish_release(root, SNAPSHOT, rows, None, review)
    _activate_release(root, release)
    return load_fixture_active(root)


def test_family_guide_can_be_discovered_when_the_user_omits_prerequisite_component(tmp_path):
    view = _family_view(tmp_path)
    question = 'NOVA 和 Probe 可以同步采集吗？'
    result = DaqToolBox(question=question, knowledge=view, role='internal_fae', requirements=[
        {'id': 'sync', 'capability': 'inspect_topology', 'entities': ['NOVA', 'Probe'],
         'conditions': {'task': 'sync'}},
    ]).dispatch('inspect_topology', {'query': question})
    assert result.status == 'ok'
    assert result.content['matched_requirement_ids'] == ['sync']
    data = result.content['matches'][0]['data']
    assert data['prerequisite_members'] == ['entity:adapter']
    catalog = DaqToolBox(knowledge=view, role='internal_fae').dispatch('catalog', {'query': ''})
    assert 'entity:nova-family' not in {row['id'] for row in catalog.content['matches']}


def test_old_exclusion_label_does_not_shadow_a_now_reviewed_component(tmp_path):
    view = _family_view(tmp_path)
    question = 'NOVA 和 Probe 通过 HUB 同步采集可以吗？'
    result = DaqToolBox(question=question, knowledge=view, role='internal_fae').dispatch(
        'inspect_topology', {'query': question})
    assert result.status == 'ok'


@pytest.mark.parametrize('label', ['NOVA Series', 'entity:nova-family'])
def test_named_family_catalog_expands_reviewed_members_without_adding_a_product(tmp_path, label):
    view = _family_view(tmp_path)
    result = DaqToolBox(knowledge=view, role='internal_fae', requirements=[
        {'id': 'family', 'capability': 'catalog', 'entities': [label]},
    ]).dispatch('catalog', {'query': label})
    assert set(result.content['requirement_source_ids']['family']) == {
        'entity:nova-small', 'entity:nova-large'}
    assert result.content['catalog_by_requirement']['family']['distinct_product_name_count'] == 1


def test_rewritten_probe_cannot_drop_a_cross_sentence_prerequisite_exclusion(tmp_path):
    view = _family_view(tmp_path)
    question = 'NOVA 和 Probe 可以同步采集吗？我不用 HUB。'
    result = DaqToolBox(question=question, knowledge=view, role='internal_fae', requirements=[
        {'id': 'sync', 'capability': 'inspect_topology', 'entities': ['NOVA', 'Probe'],
         'conditions': {'task': 'sync'}},
    ]).dispatch('inspect_topology', {'query': 'NOVA 和 Probe 同步采集'})
    assert result.status != 'ok' or 'sync' not in result.content['matched_requirement_ids']


def test_exclusion_for_a_separately_named_product_does_not_veto_another_goal(tmp_path):
    view = _family_view(tmp_path)
    question = 'NOVA Pro 单机不用 HUB。NOVA 和 Probe 通过 HUB 同步采集可以吗？'
    result = DaqToolBox(question=question, knowledge=view, role='internal_fae', requirements=[
        {'id': 'sync', 'capability': 'inspect_topology', 'entities': ['NOVA', 'Probe'],
         'conditions': {'task': 'sync'}},
    ]).dispatch('inspect_topology', {'query': 'NOVA 和 Probe HUB 同步采集'})
    assert result.status == 'ok'
    assert 'sync' in result.content['matched_requirement_ids']


@pytest.mark.parametrize('question', [
    'NOVA Pro 和 Probe 可以同步采集吗？',
    'NOVA 和 Probe 不用 Adapter 可以同步采集吗？',
    'NOVA 1280×960 版本和 Probe 可以同步采集吗？',
    'NOVA 1280 版本和 Probe 可以同步采集吗？',
])
def test_generic_guide_cannot_certify_other_products_excluded_prerequisites_or_exact_sku(
        tmp_path, question):
    view = _family_view(tmp_path)
    result = DaqToolBox(question=question, knowledge=view, role='internal_fae', requirements=[
        {'id': 'sync', 'capability': 'inspect_topology', 'entities': ['NOVA', 'Probe'],
         'conditions': {'task': 'sync', **({'resolution_variant': '1280x960'}
                                         if '1280' in question else {})}},
    ]).dispatch('inspect_topology', {'query': question})
    assert result.status != 'ok' or 'sync' not in result.content['matched_requirement_ids']


def test_a_canonical_sku_requirement_cannot_be_certified_by_family_membership(tmp_path):
    view = _family_view(tmp_path)
    question = 'NOVA 和 Probe 同步采集'
    result = DaqToolBox(question=question, knowledge=view, role='internal_fae', requirements=[
        {'id': 'specific', 'capability': 'inspect_topology',
         'entities': ['entity:nova-large', 'Probe'], 'conditions': {'task': 'sync'}},
    ]).dispatch('inspect_topology', {'query': question})
    assert 'specific' not in result.content.get('matched_requirement_ids', [])


def test_task_is_a_model_proposed_intent_not_a_literal_user_parameter():
    schema = DaqRequestSchema.from_candidate('NOVA 和 Probe 能配对吗？', {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'inspect_topology',
             'entities': ['NOVA', 'Probe'], 'conditions': {'task': 'pairing'}}],
    })
    assert schema.goals[0].condition_map()['task'] == 'pairing'
    assert 'task' not in schema.goals[0].unmapped_condition_keys


@pytest.mark.parametrize('bad_kind', ['family', 'prerequisite'])
@pytest.mark.parametrize('gate', ['publish', 'read'])
def test_invalid_family_or_prerequisite_graph_fails_both_publication_and_reading(
        tmp_path, bad_kind, gate):
    view = _family_view(tmp_path)
    manifest = deepcopy(view.manifest)
    if bad_kind == 'family':
        row = next(row for row in manifest['records'] if row['id'] == 'entity:nova-small')
        row['data']['family_entity_id'] = 'entity:probe'
    else:
        row = next(row for row in manifest['records'] if row['kind'] == 'topology')
        row['data']['prerequisite_members'] = ['entity:unknown']
    row['fact_review']['record_sha256'] = record_fingerprint(row)
    row['access_review']['record_sha256'] = access_fingerprint(row)
    with pytest.raises(ValueError):
        if gate == 'read':
            ReviewedKnowledge.from_manifest(view.release_id, manifest)
        else:
            _publish_release(tmp_path / 'invalid', SNAPSHOT,
                             manifest['records'], None, RELEASE_REVIEW)
