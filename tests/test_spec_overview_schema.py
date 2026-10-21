from daq_fae.request_schema import extract_request_schema
from daq_fae.request_schema import DaqRequestSchema
from daq_fae.knowledge.reviewed_view import ReviewedKnowledge
from daq_fae.knowledge.releases import _publish_release
from daq_fae.domain_tools import DaqToolBox
from daq_fae.domain_evidence import DaqEvidencePolicy
from daq_fae.task_context import prepare_turn
from src.agent.loop.answer_contract import AnswerDeliveryUnit, AnswerSubmission
from tests.test_knowledge_releases import SNAPSHOT, REVIEW, _entity, _record, _reviewed
from copy import deepcopy
import json


class Adapter:
    def __init__(self):
        self.calls = 0

    def chat(self, messages, **kwargs):
        self.calls += 1
        field = 'rgb_resolution' if self.calls == 1 else 'all_reviewed_specs'
        yield {'type': 'tool_call', 'name': 'submit_daq_request_schema',
               'arguments': {'confidence': 'high', 'goals': [{
                   'kind': 'product', 'capability': 'lookup_spec',
                   'entities': ['EG-DB'], 'field': field, 'conditions': {},
               }]}}


def test_broad_product_specs_retries_narrow_single_field_plan():
    adapter = Adapter()
    schema = extract_request_schema('介绍一下：EG-DB 规格 参数，越详细越好',
                                    adapter=adapter)
    assert adapter.calls == 2
    assert [goal.field for goal in schema.goals if goal.capability == 'lookup_spec'] == [
        'all_reviewed_specs']


def test_specific_field_does_not_expand_to_all_specs():
    adapter = Adapter()
    schema = extract_request_schema('EG-DB 的 RGB 分辨率是多少？', adapter=adapter)
    assert adapter.calls == 1
    assert [goal.field for goal in schema.goals if goal.capability == 'lookup_spec'] == [
        'rgb_resolution']


def _two_ego_variant_view(tmp_path):
    rows = []
    for suffix, value in (('1600', '1600x1200'), ('1920', '1920x1200')):
        entity = _entity()
        entity['id'] = f'entity:ego-{suffix}'
        entity['data'] = {'name': 'EGO', 'entity_type': 'variant'}
        entity['scope'] = {'product': 'ego', 'resolution_variant': value,
                           'required_selectors': ['resolution_variant']}
        claim = _record(value=value)
        claim['id'] = f'claim:ego-{suffix}-rgb-resolution'
        claim['data']['entity_id'] = entity['id']
        claim['data']['field'] = 'rgb_resolution'
        claim['scope'] = deepcopy(entity['scope'])
        rows.extend((_reviewed(entity), _reviewed(claim)))
    release = _publish_release(tmp_path, SNAPSHOT, rows, None, REVIEW)
    manifest = json.loads((tmp_path / 'releases' / release / 'manifest.json').read_text())
    return ReviewedKnowledge.from_manifest(release, manifest)


def test_generic_ego_overview_expands_to_two_source_bound_variant_goals(tmp_path):
    view = _two_ego_variant_view(tmp_path)
    schema = DaqRequestSchema.from_candidate('给我详细介绍一下 EGO', {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'resolve_entity',
             'entities': ['EGO'], 'conditions': {}},
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO'], 'field': 'all_reviewed_specs', 'conditions': {}},
        ],
    }, knowledge=view)
    scopes = [goal.condition_map() for goal in schema.goals
              if goal.capability == 'lookup_spec']
    assert scopes == [
        {'resolution_variant': '1600x1200'},
        {'resolution_variant': '1920x1200'},
    ]


def test_unknown_ego_name_never_inherits_reviewed_variant_overview(tmp_path):
    view = _two_ego_variant_view(tmp_path)
    schema = DaqRequestSchema.from_candidate('给我详细介绍一下 EGO Max', {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'lookup_spec',
            'entities': ['EGO Max'], 'field': 'all_reviewed_specs',
            'conditions': {},
        }],
    }, knowledge=view)
    assert len(schema.goals) == 1
    assert schema.goals[0].condition_map() == {}


def test_unverified_variant_claim_does_not_create_a_spec_overview_goal(tmp_path):
    view = _two_ego_variant_view(tmp_path)
    manifest = deepcopy(view.manifest)
    row = next(row for row in manifest['records']
               if row['id'] == 'claim:ego-1920-rgb-resolution')
    row['status'] = 'unknown'
    row['answerable'] = False
    manifest['answerable_count'] -= 1
    view = ReviewedKnowledge.from_manifest(view.release_id, manifest)
    assert view.verified_variant_scopes('EGO', 'all_reviewed_specs', role='internal_fae') == {
        'resolution_variant': ['1600x1200']}


def test_variant_overview_is_resolved_only_after_both_scoped_sources_are_delivered(tmp_path):
    view = _two_ego_variant_view(tmp_path)
    question = '给我详细介绍一下 EGO'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'resolve_entity',
             'entities': ['EGO'], 'conditions': {}},
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO'], 'field': 'all_reviewed_specs', 'conditions': {}},
        ],
    }, knowledge=view)
    plan = prepare_turn(question, request_schema=schema)
    requirements = list(plan.requirements)
    box = DaqToolBox(question=question, knowledge=view, role='internal_fae',
                     requirements=requirements)
    policy = DaqEvidencePolicy(question=question).begin({'requirements': requirements})
    identity = box.dispatch('resolve_entity', {'text': 'EGO'})
    policy.observe('resolve_entity', identity)
    units = []
    for variant in ('1600x1200', '1920x1200'):
        result = box.dispatch('lookup_spec', {
            'entity': 'EGO', 'field': 'all_reviewed_specs',
            'conditions': {'resolution_variant': variant},
        })
        assert result.status == 'ok'
        assert len(result.content['matched_requirement_ids']) == 1
        requirement_id = result.content['matched_requirement_ids'][0]
        source_ids = tuple(result.content['requirement_source_ids'][requirement_id])
        units.append(AnswerDeliveryUnit(
            requirement_id, source_ids,
            f'EGO {variant} 版 RGB 分辨率为 {variant}。'))
        policy.observe('lookup_spec', result)
        submission = AnswerSubmission(
            outcome='resolved', conclusion='EGO 有两组已核实的变体规格。',
            delivery_units=tuple(units))
        if len(units) == 1:
            assert policy.evaluate(submission).action != 'allow'
    assert all(value == 'satisfied' for value in
               policy.snapshot().requirement_status.values())
    assert policy.evaluate(submission).action == 'allow'
