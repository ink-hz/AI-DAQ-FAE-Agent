"""Deterministic retrieval of an already authorized immutable section view."""
from __future__ import annotations

import json
import re
import unicodedata

MAX_QUERY_CHARS = 2000
MAX_BODY_CHARS = 32000
MAX_MATCHES = 20
EXCERPT_CHARS = 600
_BLOCK = re.compile(r'```daq-record\n(.*?)\n```', re.DOTALL)
_WORD = re.compile(r'[a-z0-9]+(?:[._+-][a-z0-9]+)*|[\u3400-\u9fff]+')
_LOCAL_PATH = re.compile(r"(?:[A-Za-z]:\\|(?<![\w/])(?:/|~/)[^\s/]+/)")
_CJK = re.compile(r'^[\u3400-\u9fff]+$')


def normalize(value: str) -> str:
    return unicodedata.normalize('NFKC', value).casefold()


def terms(value: str) -> set[str]:
    result = set()
    for word in _WORD.findall(normalize(value)):
        if _CJK.fullmatch(word):
            # Two to four character phrases recall unsegmented Chinese questions.
            result.update(word[i:i + n] for n in range(2, min(4, len(word)) + 1)
                          for i in range(len(word) - n + 1))
        else:
            result.add(word)
    return result


def strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [s for item in value for s in strings(item)]
    if isinstance(value, dict):
        return [s for key, item in value.items() if not key.endswith('_id')
                for s in strings(item)]
    return []


def body_text(body: str) -> str:
    # C1 stores lossless typed blocks. Search their readable values, not JSON
    # keys, provenance, governance states or stable internal record IDs.
    def readable(match):
        row = json.loads(match.group(1))
        data = row['data']
        if data.get('field') == 'source_text':
            return str(data.get('value', ''))
        return ' '.join(strings(data))
    return _BLOCK.sub(readable, body)


def rank_sections(sections: list[dict], records: list[dict], query: str) -> list[dict]:
    query_terms = terms(query)
    if not query_terms:
        return []
    entities = {r['id']: r for r in records if r['kind'] == 'entity'}
    by_record = {r['id']: r for r in records}
    ranked = []
    for section in sections:
        entity_ids = set(section.get('entity_ids', []))
        if section.get('entity_id'):
            entity_ids.add(section['entity_id'])
        for rid in section['dependency_claim_ids']:
            row = by_record.get(rid, {})
            if row.get('kind') == 'entity':
                entity_ids.add(rid)
            if row.get('data', {}).get('entity_id'):
                entity_ids.add(row['data']['entity_id'])
        aliases = strings(section.get('aliases', []))
        for eid in sorted(entity_ids):
            data = entities.get(eid, {}).get('data', {})
            aliases.extend(strings(data.get('name', '')) + strings(data.get('aliases', [])))
        fields = [(8, section['title']), (6, ' '.join(aliases)),
                  (5, ' '.join(strings(section.get('domain_terms', [])))),
                  (1, body_text(section['body']))]
        score = sum(weight * sum(len(term) for term in query_terms & terms(text))
                    for weight, text in fields)
        if score:
            ranked.append((score, section))
    return [s for _, s in sorted(ranked, key=lambda item: (-item[0], item[1]['section_id']))
            ][:MAX_MATCHES]


def contains_source_path(section: dict) -> bool:
    """Do not deliver local provenance embedded in reviewed content as prose."""
    text = ' '.join(strings({k: v for k, v in section.items()
                            if k not in {'source_refs', 'record_assertions',
                                         'fact_review', 'permission_review', 'body'}}))
    text += ' ' + section['body'] + ' ' + body_text(section['body'])
    return bool(_LOCAL_PATH.search(text)) or any(
        ref['path'] in text for ref in section['source_refs'])
