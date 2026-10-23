"""A useful observed input may be delivered without certifying its parent task."""
from daq_fae.domain_evidence import DaqEvidencePolicy
from daq_fae.domain_tools import DaqToolBox
from daq_fae.request_schema import DaqRequestSchema
from daq_fae.workflow import allowed_tools_for
from src.agent.loop.answer_contract import AnswerDeliveryUnit, AnswerSubmission
from test_catalog_collection_contract import _directory


def _parent(**changes):
    return {'id': 'combo', 'capability': 'inspect_topology',
            'entities': ['NOVA', 'Probe'], 'conditions': {},
            'delivery_required': True, **changes}


def _support(tmp_path, parent=None):
    parent = parent or _parent()
    view = _directory(tmp_path)
    result = DaqToolBox(knowledge=view, role='internal_fae', requirements=[parent]).dispatch(
        'lookup_spec', {'entity': 'entity:nova-small', 'field': 'horizontal_fov',
                        'conditions': {'resolution_variant': '960x720'}})
    return parent, result, view


def test_product_loop_can_probe_inputs_without_the_schema_guessing_every_tool():
    schema = DaqRequestSchema.from_candidate('NOVA 和 Probe 可以组合使用吗？', {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'inspect_topology',
             'entities': ['NOVA', 'Probe']}],
    })
    assert {'lookup_spec', 'search_knowledge', 'read_doc'} <= allowed_tools_for(schema)
    identity = DaqRequestSchema.from_candidate('我用 NOVA。', {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'resolve_entity', 'entities': ['NOVA']},
            {'kind': 'unknown', 'capability': 'clarify'}],
    })
    assert allowed_tools_for(identity) == frozenset({'resolve_entity', 'session_state'})


def test_actual_typed_input_is_bound_for_partial_delivery_not_parent_coverage(tmp_path):
    parent, result, view = _support(tmp_path)
    assert result.content['matched_requirement_ids'] == []
    support = result.content['supporting_evidence'][0]
    assert support['parent_requirement_id'] == parent['id']
    assert support['source_ids'] == ['claim:nova-small']
    session = DaqEvidencePolicy(knowledge=view, role='internal_fae').begin({'requirements': [parent]})
    session.observe('lookup_spec', result)
    assert session.snapshot().requirement_status['combo'] == 'unknown'
    unit = AnswerDeliveryUnit(support['id'], tuple(support['source_ids']),
                              'NOVA 的 960×720 变体水平视场角为 90°。')
    decision = session.evaluate(AnswerSubmission(outcome='safe_abstained',
        basis='上述数值是单设备输入，不证明组合能力。',
        missing='该组合的适用性未核实。', delivery_units=(unit,)))
    assert decision.action == 'allow'
    assert session.evaluate(AnswerSubmission(outcome='resolved',
        conclusion='组合已验证。', delivery_units=(unit,))).action != 'allow'


def test_support_cannot_cross_the_users_explicit_variant_or_product(tmp_path):
    for parent in [_parent(conditions={'resolution_variant': '1280x960'}),
                   _parent(entities=['Probe'])]:
        _, result, _ = _support(tmp_path, parent)
        assert result.content.get('supporting_evidence', []) == []


def test_unread_or_other_support_source_cannot_be_delivered(tmp_path):
    parent, result, view = _support(tmp_path)
    support = result.content['supporting_evidence'][0]
    session = DaqEvidencePolicy(knowledge=view, role='internal_fae').begin({'requirements': [parent]})
    session.observe('lookup_spec', result)
    decision = session.evaluate(AnswerSubmission(outcome='safe_abstained',
        missing='组合仍未知。', delivery_units=(AnswerDeliveryUnit(
            support['id'], ('claim:nova-large',), '另一个变体已确认。'),)))
    assert decision.reason_code == 'daq_delivery_source_mismatch'


def test_candidate_support_is_not_authoritative_without_explicit_dev_opt_in(tmp_path):
    parent, result, view = _support(tmp_path)
    support = result.content['supporting_evidence'][0]
    result.sources[0]['verification_status'] = 'candidate'
    session = DaqEvidencePolicy(knowledge=view, role='internal_fae').begin({'requirements': [parent]})
    session.observe('lookup_spec', result)
    decision = session.evaluate(AnswerSubmission(outcome='safe_abstained',
        missing='未知。', delivery_units=(AnswerDeliveryUnit(
            support['id'], tuple(support['source_ids']), '90° 已确认。'),)))
    assert decision.reason_code == 'daq_delivery_requirement_unverified'


def test_observation_rechecks_forged_support_against_parent_scope_and_role_view(tmp_path):
    parent, result, view = _support(tmp_path)
    support = result.content['supporting_evidence'][0]
    for changed in [_parent(entities=['Probe']),
                    _parent(conditions={'resolution_variant': '1280x960'}),
                    _parent(evidence_class='clarification_required')]:
        session = DaqEvidencePolicy(knowledge=view, role='internal_fae').begin({
            'requirements': [changed]})
        session.observe('lookup_spec', result)
        decision = session.evaluate(AnswerSubmission(outcome='safe_abstained',
            missing='组合未知。', delivery_units=(AnswerDeliveryUnit(
                support['id'], tuple(support['source_ids']), '90° 已核实。'),)))
        assert decision.reason_code == 'daq_delivery_requirement_unverified'
