from daq_fae.response_language import localize_answer


def test_english_presentation_translates_only_renderer_headings():
    answer = '**结论**\n\nI am the DAQ assistant.\n\n**依据**\n\nService contract.'
    done = {'answer': answer, 'sources': [{'source_id': 'service'}], 'outcome': 'resolved'}
    localize_answer(done, 'en')
    assert done['answer'] == (
        '**Conclusion**\n\nI am the DAQ assistant.\n\n**Basis**\n\nService contract.')
    assert done['sources'] == [{'source_id': 'service'}]


def test_chinese_presentation_preserves_original_answer():
    done = {'answer': '**结论**\n\n我是数采助手。'}
    localize_answer(done, 'zh')
    assert done['answer'] == '**结论**\n\n我是数采助手。'
