from daq_fae.response_language import localize_answer


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
