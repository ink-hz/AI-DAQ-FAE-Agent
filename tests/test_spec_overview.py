"""A broad product-spec question must expose all reviewed fields for one SKU."""

from daq_fae.domain_tools import DaqToolBox
from tests.test_section_tools import make_view
from test_knowledge_releases import SNAPSHOT, REVIEW, _entity, _record, _reviewed
from daq_fae.knowledge import releases
from daq_fae.knowledge.reviewed_view import ReviewedKnowledge
import json
from copy import deepcopy


def test_overview_returns_each_verified_field_with_its_own_source(tmp_path):
    view = make_view(tmp_path)
    box = DaqToolBox(knowledge=view, role='internal_fae', requirements=[{
        'id': 'all_specs', 'capability': 'lookup_spec',
        'entities': ['EGO 1600'], 'field': 'all_reviewed_specs', 'conditions': {},
    }])
    result = box.dispatch('lookup_spec', {
        'entity': 'EGO 1600', 'field': 'all_reviewed_specs',
    })
    assert result.status == 'ok'
    assert result.content['matched_requirement_ids'] == ['all_specs']
    assert result.content['matches']
    assert all(row['kind'] == 'claim' and row['status'] == 'verified'
               for row in result.content['matches'])
    assert {source['source_id'] for source in result.sources} == {
        row['id'] for row in result.content['matches']}


def test_overview_does_not_mix_unselected_variants_or_roles(tmp_path):
    view = make_view(tmp_path)
    assert DaqToolBox(knowledge=view, role='channel').dispatch(
        'lookup_spec', {'entity': 'EGO 1600', 'field': 'all_reviewed_specs'}
    ).status == 'not_found'
    assert DaqToolBox(knowledge=view, role='internal_fae').dispatch(
        'lookup_spec', {'entity': 'EGO', 'field': 'all_reviewed_specs'}
    ).status == 'not_found'


def test_overview_selects_named_variant_and_refuses_family_mix(tmp_path):
    first = _entity()
    first['data']['name'] = 'EGO'
    first['scope'] = {'product': 'ego', 'resolution_variant': '1600x1200',
                      'required_selectors': ['resolution_variant']}
    first = _reviewed(first)
    second = deepcopy(first)
    second['id'] = 'entity:ego-1920'
    second['scope']['resolution_variant'] = '1920x1200'
    second = _reviewed(second)
    left = _record()
    left['scope'] = deepcopy(first['scope'])
    left = _reviewed(left)
    right = deepcopy(left)
    right['id'] = 'claim:ego-1920-resolution'
    right['scope'] = deepcopy(second['scope'])
    right['data']['entity_id'] = second['id']
    right['data']['value'] = '1920x1200'
    right = _reviewed(right)
    rid = releases._publish_release(tmp_path, SNAPSHOT,
                                   [first, second, left, right], None, REVIEW)
    manifest = json.loads((tmp_path / 'releases' / rid / 'manifest.json').read_text())
    view = ReviewedKnowledge.from_manifest(rid, manifest)
    box = DaqToolBox(knowledge=view, role='internal_fae')
    left_result = box.dispatch('lookup_spec', {
        'entity': 'EGO 1600', 'field': 'all_reviewed_specs'})
    assert [row['id'] for row in left_result.content['matches']] == [left['id']]
    right_result = box.dispatch('lookup_spec', {
        'entity': 'EGO', 'field': 'all_reviewed_specs',
        'conditions': {'resolution_variant': '1920x1200'}})
    assert [row['id'] for row in right_result.content['matches']] == [right['id']]
    assert box.dispatch('lookup_spec', {
        'entity': 'EGO', 'field': 'all_reviewed_specs'}).status == 'not_found'
