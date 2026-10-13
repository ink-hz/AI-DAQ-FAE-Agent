"""DAQ-only presentation localization after the shared answer contract renders."""
from __future__ import annotations


_EN_HEADINGS = {
    '结论': 'Conclusion', '依据': 'Basis', '注意': 'Cautions',
    '验证与下一步': 'Next steps', '当前无法下结论': 'Unable to conclude yet',
    '已确认信息': 'Confirmed information', '建议': 'Next steps',
    '已确认部分': 'Confirmed portion', '需研发确认': 'Needs R&D confirmation',
    '建议转人工 FAE': 'FAE handoff recommended', '下一步': 'Next steps',
    '候选性质': 'Candidate status', '未闭合约束': 'Open constraints',
}


def localize_answer(done: dict, language: str) -> None:
    if language not in {'zh', 'en'}:
        raise ValueError('daq_answer_language_invalid')
    done['answer_language'] = language
    if language == 'zh' or not isinstance(done.get('answer'), str):
        return
    failure = {
        'schema_error': 'I could not reliably understand this request. Please try again.',
        'provider_configuration_error': 'The DAQ model request failed because of a provider configuration error.',
        'provider_rate_limited': 'The DAQ model provider is rate limiting requests. Please try again later.',
        'provider_unavailable': 'The DAQ model provider is unavailable. Please try again later.',
        'provider_protocol_error': 'The DAQ model provider returned an incomplete response. Please try again.',
        'provider_error': 'The DAQ model request failed. Please try again.',
        'internal_error': 'The DAQ service could not complete this request. Please try again.',
        'budget_exhausted': 'The DAQ model reached its reasoning limit before completing an answer.',
        'max_duration': 'The DAQ model did not complete the answer in time. Please try again.',
        'empty_answer': 'The DAQ model returned no usable answer. Please try again.',
        'truncated_answer': 'The DAQ model returned an incomplete answer. Please try again.',
        'tool_xml_leak': 'The DAQ model returned an invalid answer format. Please try again.',
        'unstructured_final': 'The DAQ model returned an invalid answer format. Please try again.',
        'invalid_answer_contract': 'The DAQ model returned an answer that did not pass validation.',
    }.get(done.get('outcome'))
    if failure:
        done['answer'] = failure
        return
    if done.get('refusal_category'):
        done['answer'] = ('I cannot help with that request. Please ask an Orbbec DAQ '
                          'technical question about products, software, setup or troubleshooting.')
        return
    lines = done['answer'].split('\n')
    done['answer'] = '\n'.join(
        '**' + _EN_HEADINGS[line[2:-2]] + '**'
        if line.startswith('**') and line.endswith('**')
        and line[2:-2] in _EN_HEADINGS else line
        for line in lines
    )
