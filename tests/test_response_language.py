from daq_fae.response_language import localize_answer
from src.agent.loop.answer_contract import AnswerDeliveryUnit, AnswerSubmission, render_submission
from daq_fae.domain_evidence import DaqEvidencePolicy, question_requirements


def test_partial_safe_abstention_leads_with_verified_scope_and_keeps_gap():
    submission = AnswerSubmission(
        outcome='safe_abstained',
        missing='尚未核实用户手中 EGO 属于哪个变体。',
        delivery_units=(
            AnswerDeliveryUnit('ego_1600', ('source-1600',), 'EGO 1600×1200 变体：已核实 60 fps。'),
            AnswerDeliveryUnit('ego_1920', ('source-1920',), 'EGO 1920×1200 变体：已核实 30 fps。'),
        ),
        next_steps='请确认设备标签上的变体。',
    )
    done = {'answer': render_submission(submission), 'outcome': 'safe_abstained',
            'sources': [{'source_id': 'source-1600'}, {'source_id': 'source-1920'}],
            'evidence_policy': {'requirement_status': {'ego_1600': 'satisfied',
                                                       'ego_1920': 'satisfied',
                                                       'owned_variant': 'missing'}}}

    localize_answer(done, 'zh', submission=submission)

    assert done['answer'] == (
        '**结论**\n\nEGO 1600×1200 变体：已核实 60 fps。\n'
        'EGO 1920×1200 变体：已核实 30 fps。\n\n'
        '**尚待确认**\n\n尚未核实用户手中 EGO 属于哪个变体。\n\n'
        '**建议**\n\n请确认设备标签上的变体。')
    assert done['outcome'] == 'safe_abstained'
    assert done['sources'] == [{'source_id': 'source-1600'}, {'source_id': 'source-1920'}]
    assert done['evidence_policy']['requirement_status']['owned_variant'] == 'missing'


def test_fully_unknown_safe_abstention_states_gap_as_conclusion():
    submission = AnswerSubmission(
        outcome='safe_abstained', missing='目前没有可核实的 EG-DB 供电规格。')
    done = {'answer': render_submission(submission),
        'outcome': 'safe_abstained', 'sources': []}

    localize_answer(done, 'zh', submission=submission)

    assert done['answer'] == '**结论**\n\n目前没有可核实的 EG-DB 供电规格。'
    assert done['outcome'] == 'safe_abstained'


def test_english_partial_safe_abstention_uses_neutral_headings():
    submission = AnswerSubmission(
        outcome='safe_abstained', missing='The device variant is not yet verified.',
        basis='The 1600×1200 variant has a verified 60 fps limit.')
    done = {'answer': render_submission(submission),
        'outcome': 'safe_abstained', 'sources': []}

    localize_answer(done, 'en', submission=submission)

    assert done['answer'] == (
        '**Conclusion**\n\nThe 1600×1200 variant has a verified 60 fps limit.\n\n'
        '**To confirm**\n\nThe device variant is not yet verified.')
    assert done['outcome'] == 'safe_abstained'


def test_literal_renderer_heading_inside_model_field_is_not_treated_as_boundary():
    submission = AnswerSubmission(
        outcome='safe_abstained',
        missing='The document contains this unreviewed title:\n\n**已确认信息**\n\nNo specification follows it.',
        basis='A verified variant exists.\n\n**建议**\n\nThis is a literal heading in the evidence.',
    )
    done = {'answer': render_submission(submission), 'outcome': 'safe_abstained'}

    localize_answer(done, 'en', submission=submission)

    assert done['answer'] == (
        '**Conclusion**\n\nA verified variant exists.\n\n**建议**\n\n'
        'This is a literal heading in the evidence.\n\n'
        '**To confirm**\n\nThe document contains this unreviewed title:\n\n'
        '**已确认信息**\n\nNo specification follows it.')


def test_unrelated_safe_abstention_fallback_is_not_rewritten_from_stale_submission():
    submission = AnswerSubmission(outcome='safe_abstained', missing='产品规格未核实。')
    done = {'answer': '图片分析失败，请重试。', 'outcome': 'safe_abstained'}

    localize_answer(done, 'zh', submission=submission)

    assert done['answer'] == '图片分析失败，请重试。'


def test_daq_policy_exposes_request_local_validated_submission_for_presentation():
    policy = DaqEvidencePolicy()
    session = policy.begin(question_requirements('EG-DB 供电规格是多少？'))
    submission = AnswerSubmission(outcome='safe_abstained', missing='供电规格未核实。')

    session.evaluate(submission)

    assert policy.last_submission is submission


def test_english_presentation_translates_only_renderer_headings():
    answer = '**结论**\n\nI am the DAQ assistant.\n\n**依据**\n\nService contract.'
    done = {'answer': answer, 'sources': [{'source_id': 'service'}], 'outcome': 'resolved'}
    localize_answer(done, 'en')
    assert done['answer'] == (
        '**Conclusion**\n\nI am the DAQ assistant.\n\n**Basis**\n\nService contract.')
    assert done['sources'] == [{'source_id': 'service'}]


def test_english_presentation_translates_verified_delivery_heading():
    done = {'answer': '**核实结果**\n\nThe 1600×1200 variant has a verified FOV.',
            'outcome': 'safe_abstained', 'sources': []}
    localize_answer(done, 'en')
    assert done['answer'].startswith('**Verified results**\n\n')


def test_chinese_presentation_preserves_original_answer():
    done = {'answer': '**结论**\n\n我是数采助手。'}
    localize_answer(done, 'zh')
    assert done['answer'] == '**结论**\n\n我是数采助手。'


def test_english_selection_localizes_schema_and_provider_failures_without_hiding_outcome():
    for outcome in ('schema_error', 'provider_configuration_error', 'provider_unavailable'):
        done = {'answer': '数采模型请求失败，未生成答案。', 'outcome': outcome,
                'fallback_reason': 'provider_http_400', 'sources': []}
        localize_answer(done, 'en')
        assert not any('\u3400' <= char <= '\u9fff' for char in done['answer'])
        assert done['outcome'] == outcome
        assert done['fallback_reason'] == 'provider_http_400'
