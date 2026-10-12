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
    lines = done['answer'].split('\n')
    done['answer'] = '\n'.join(
        '**' + _EN_HEADINGS[line[2:-2]] + '**'
        if line.startswith('**') and line.endswith('**')
        and line[2:-2] in _EN_HEADINGS else line
        for line in lines
    )
