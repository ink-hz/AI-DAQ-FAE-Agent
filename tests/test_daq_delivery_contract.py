"""DAQ answer units bind delivered prose to this turn's governed evidence."""

import pytest

from src.agent.loop import answer_contract
from src.agent.loop.answer_contract import (
    AnswerSubmission, render_submission, submit_answer_tool_schema,
    validate_submission,
)
from src.agent.loop.tools import ToolResult

from daq_fae.domain_evidence import DaqEvidencePolicy
from daq_fae.request_schema import DaqRequestSchema
from daq_fae.task_context import prepare_turn


def _session():
    session = DaqEvidencePolicy(question='EGO 双目两个版本的水平视场角是多少？').begin({
        'requirements': [
            {'id': 'fov1600', 'capability': 'lookup_spec',
             'evidence_class': 'governed_required', 'delivery_required': True,
             'field': 'horizontal_fov', 'entities': ['EGO 双目'],
             'conditions': {'resolution_variant': '1600x1200'}},
            {'id': 'fov1920', 'capability': 'lookup_spec',
             'evidence_class': 'governed_required', 'delivery_required': True,
             'field': 'horizontal_fov', 'entities': ['EGO 双目'],
             'conditions': {'resolution_variant': '1920x1200'}},
            {'id': 'selection', 'capability': 'clarify',
             'evidence_class': 'clarification_required'},
        ],
    })
    for requirement_id, source_id in (
        ('fov1600', 'claim:ego-1600-horizontal-fov'),
        ('fov1920', 'claim:ego-1920-horizontal-fov'),
    ):
        session.observe('lookup_spec', ToolResult(
            status='ok',
            content={'matched_requirement_ids': [requirement_id],
                     'requirement_source_ids': {requirement_id: [source_id]}},
            sources=[{'type': 'daq_governed_claim',
                      'verification_status': 'verified',
                      'release_id': 'knowledge-v1', 'source_id': source_id}],
        ))
    return session


def test_daq_submission_schema_adds_units_without_changing_camera_default():
    ordinary = submit_answer_tool_schema()['function']['parameters']['properties']
    daq = submit_answer_tool_schema(include_delivery_units=True)[
        'function']['parameters']['properties']
    assert 'delivery_units' not in ordinary
    assert 'delivery_units' in daq
    submission, errors = validate_submission({
        'outcome': 'safe_abstained', 'missing': '请确认您的分辨率版本。',
        'delivery_units': [{'requirement_id': 'fov1600',
                            'source_ids': ['claim:ego-1600-horizontal-fov'],
                            'text': '1600×1200 版水平视场角为 165°。'}],
    }, allow_delivery_units=True)
    assert errors == []
    assert submission.delivery_units[0].requirement_id == 'fov1600'
    assert '1600×1200 版水平视场角为 165°' in render_submission(submission)
    _, ordinary_errors = validate_submission({
        'outcome': 'safe_abstained', 'missing': '未知',
        'delivery_units': [{'requirement_id': 'fov1600',
                            'source_ids': ['claim:ego-1600-horizontal-fov'],
                            'text': '165°'}],
    })
    assert 'unknown_fields:delivery_units' in ordinary_errors


def test_satisfied_goals_require_visible_units_bound_to_their_own_sources():
    AnswerDeliveryUnit = answer_contract.AnswerDeliveryUnit
    session = _session()
    omitted = session.evaluate(AnswerSubmission(
        outcome='safe_abstained', basis='有两个版本。',
        missing='请确认您用的版本。'))
    assert (omitted.action, omitted.reason_code) == (
        'request_evidence', 'daq_known_goal_delivery_missing')
    assert 'fov1600' in omitted.notice
    assert 'claim:ego-1600-horizontal-fov' in omitted.notice

    one = AnswerDeliveryUnit('fov1600',
                             ('claim:ego-1600-horizontal-fov',),
                             '1600×1200 版水平视场角为 165°。')
    missing_second = session.evaluate(AnswerSubmission(
        outcome='safe_abstained', missing='请确认您用的版本。',
        delivery_units=(one,)))
    assert missing_second.reason_code == 'daq_known_goal_delivery_missing'

    wrong = session.evaluate(AnswerSubmission(
        outcome='safe_abstained', missing='请确认您用的版本。',
        delivery_units=(
            one,
            AnswerDeliveryUnit('fov1920',
                               ('claim:ego-1600-horizontal-fov',),
                               '1920×1200 版水平视场角为 149°。'),
        )))
    assert wrong.reason_code == 'daq_delivery_source_mismatch'

    full = AnswerSubmission(
        outcome='safe_abstained', missing='请确认您用的版本。',
        delivery_units=(
            one,
            AnswerDeliveryUnit('fov1920',
                               ('claim:ego-1920-horizontal-fov',),
                               '1920×1200 版水平视场角为 149°。'),
        ))
    assert session.evaluate(full).action == 'allow'
    answer = render_submission(full)
    assert '165°' in answer and '149°' in answer


def test_unverified_goal_cannot_be_declared_as_delivered():
    AnswerDeliveryUnit = answer_contract.AnswerDeliveryUnit
    session = _session()
    decision = session.evaluate(AnswerSubmission(
        outcome='safe_abstained', missing='请确认您的变体。',
        delivery_units=(AnswerDeliveryUnit(
            'selection', ('claim:ego-1600-horizontal-fov',),
            '您的设备就是 1600×1200 版。'),)))
    assert decision.reason_code == 'daq_delivery_requirement_unverified'


def test_verified_identity_may_be_delivered_but_does_not_replace_catalog_goal():
    session = DaqEvidencePolicy(question='UMI 视觉模块算产品吗，目录有几款？').begin({
        'requirements': [
            {'id': 'catalog', 'capability': 'catalog',
             'evidence_class': 'governed_required', 'delivery_required': True},
            {'id': 'identity', 'capability': 'resolve_entity',
             'evidence_class': 'governed_required', 'delivery_required': False,
             'entities': ['UMI 视觉模块']},
        ],
    })
    for capability, requirement_id, source_id in (
        ('catalog', 'catalog', 'entity:umi-vision-module'),
        ('resolve_entity', 'identity', 'entity:umi-vision-module'),
    ):
        session.observe(capability, ToolResult(
            status='ok', content={
                'matched_requirement_ids': [requirement_id],
                'requirement_source_ids': {requirement_id: [source_id]}},
            sources=[{'type': 'daq_governed_claim',
                      'verification_status': 'verified',
                      'release_id': 'knowledge-v1', 'source_id': source_id}],
        ))
    identity_only = session.evaluate(AnswerSubmission(
        outcome='resolved', conclusion='目录计数见下。',
        delivery_units=(answer_contract.AnswerDeliveryUnit(
            'identity', ('entity:umi-vision-module',),
            'UMI 视觉模块是目录中的部件条目。'),)))
    assert identity_only.reason_code == 'daq_known_goal_delivery_missing'


def test_mixed_tool_sources_cannot_bind_an_unverified_delivery_source():
    session = DaqEvidencePolicy(question='EGO Pro 单机录制步骤是什么？').begin({
        'requirements': [{'id': 'procedure', 'capability': 'lookup_procedure',
                          'evidence_class': 'governed_required',
                          'delivery_required': True, 'entities': ['EGO Pro']}],
    })
    session.observe('lookup_procedure', ToolResult(
        status='ok', content={
            'matched_requirement_ids': ['procedure'],
            'requirement_source_ids': {'procedure': ['reviewed', 'draft']}},
        sources=[
            {'type': 'daq_governed_claim', 'verification_status': 'verified',
             'release_id': 'knowledge-v1', 'source_id': 'reviewed'},
            {'type': 'daq_governed_claim', 'verification_status': 'candidate',
             'release_id': 'knowledge-v1', 'source_id': 'draft'},
        ],
    ))
    decision = session.evaluate(AnswerSubmission(
        outcome='resolved', conclusion='单机步骤已核实。',
        delivery_units=(answer_contract.AnswerDeliveryUnit(
            'procedure', ('draft',), '按未审草稿执行。'),)))
    assert decision.reason_code == 'daq_delivery_source_mismatch'


def test_planned_product_fact_is_delivery_required_but_identity_is_not():
    question = 'EGO 双目的水平视场角是多少？'
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'resolve_entity',
             'entities': ['EGO 双目']},
            {'kind': 'product', 'capability': 'lookup_spec',
             'entities': ['EGO 双目'], 'field': 'horizontal_fov',
             'conditions': {}},
        ],
    })
    by_capability = {item['capability']: item for item in
                     prepare_turn(question, request_schema=schema).requirements}
    assert by_capability['lookup_spec']['delivery_required'] is True
    assert by_capability['resolve_entity'].get('delivery_required') is not True


@pytest.mark.parametrize('case_id,capability,source_ids,unknown', [
    ('CASE-002', 'lookup_procedure', ('claim:ego-pro-standalone-recording',), True),
    ('CASE-003', 'lookup_procedure', ('claim:ego-pro-local-tf-recording',), False),
    ('CASE-004', 'lookup_spec', (
        'claim:ego-1600-horizontal-fov', 'claim:ego-1600-vertical-fov',
        'claim:ego-1920-horizontal-fov', 'claim:ego-1920-vertical-fov'), True),
])
def test_evd007_known_delivery_and_omission_pairs(case_id, capability,
                                                   source_ids, unknown):
    """Each original case has a known part that must survive safe abstention."""
    requirements = [
        {'id': f'{case_id}:{index}', 'capability': capability,
         'evidence_class': 'governed_required', 'delivery_required': True,
         'entities': ['EGO Pro' if capability == 'lookup_procedure' else 'EGO 双目']}
        for index in range(len(source_ids))
    ]
    if unknown:
        requirements.append({'id': f'{case_id}:unknown',
                             'capability': 'clarify',
                             'evidence_class': 'clarification_required'})
    session = DaqEvidencePolicy(question='请按原问题交付已知部分').begin({
        'requirements': requirements})
    units = []
    for requirement, source_id in zip(requirements, source_ids):
        session.observe(capability, ToolResult(
            status='ok',
            content={'matched_requirement_ids': [requirement['id']],
                     'requirement_source_ids': {requirement['id']: [source_id]}},
            sources=[{'type': 'daq_governed_claim',
                      'verification_status': 'verified',
                      'release_id': 'knowledge-v1', 'source_id': source_id}],
        ))
        delivered = ('1600×1200 或 1920×1200 版的视场角已按版本核实。'
                     if capability == 'lookup_spec' else
                     'EGO Pro 单机 TF 卡录制的开机和停止步骤已核实。')
        units.append(answer_contract.AnswerDeliveryUnit(
            requirement['id'], (source_id,), delivered))
    outcome = 'safe_abstained' if unknown else 'resolved'
    missing = '组合或变体选择尚未核实。' if unknown else None
    omitted = session.evaluate(AnswerSubmission(
        outcome=outcome, conclusion='已查询。', missing=missing))
    assert (omitted.action, omitted.reason_code) == (
        'request_evidence', 'daq_known_goal_delivery_missing')
    complete = session.evaluate(AnswerSubmission(
        outcome=outcome, conclusion='已查询。', missing=missing,
        delivery_units=tuple(units)))
    assert complete.action == 'allow', complete.reason_code
