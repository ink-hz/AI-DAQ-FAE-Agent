"""Navigate published passages without promoting neighbours into read evidence."""
from __future__ import annotations

from collections import Counter, defaultdict
import json
import re

from daq_fae.document_references import section_documents

_HEADING = re.compile(r'^\s{0,3}(#{1,6})[ \t]+(.+?)\s*#*\s*$')
_ESCAPE = re.compile(r'\\([\\`*_{}\[\]()#+\-.!>])')
_MAX_NEIGHBOURS = 12


def document_navigation(sections: list[dict], records: list[dict], *,
                        source_chunks: list[dict] | None = None) -> dict[str, dict]:
    """Use exact approved source, subject and scope boundaries, after role filtering."""
    by_record = {r['id']: r for r in records}
    groups = defaultdict(list)
    boundaries = defaultdict(list)
    for chunk in source_chunks or []:
        loc = chunk.get('locator', {})
        if (loc.get('kind') == 'lines' and type(loc.get('start')) is int
                and type(loc.get('end')) is int):
            boundaries[(chunk.get('source_path'), chunk.get('source_sha256'))].append(
                (loc['start'], loc['end']))
    for section in sections:
        rows = section_documents(section, by_record)
        if section.get('review_status') != 'verified' or len(rows) != 1:
            continue
        row = rows[0]
        refs = row.get('source_refs', [])
        if (len(refs) != 1 or refs != section.get('source_refs')
                or row['scope'] != section['scope']):
            continue
        ref = refs[0];loc = ref.get('locator', {})
        kind = loc.get('kind')
        position = loc.get('start') if kind == 'lines' else loc.get('page')
        if kind not in {'lines', 'page'} or type(position) is not int or position < 1:
            continue
        key = (ref['path'], ref['sha256'], kind, row['data']['entity_id'],
               json.dumps(row['scope'], sort_keys=True, ensure_ascii=False))
        text = row['data']['value']
        first = next((line for line in text.splitlines() if line.strip()), '')
        heading = _HEADING.fullmatch(first) if kind == 'lines' else None
        ambiguous = kind == 'lines' and sum(
            bool(_HEADING.fullmatch(line)) for line in text.splitlines()) > 1
        groups[key].append({'section_id': section['section_id'], 'position': position,
            'end': loc.get('end', position),
            'title': _ESCAPE.sub(r'\1', heading[2]) if heading else section['title'],
            'level': len(heading[1]) if heading else None, 'ambiguous': ambiguous})
    result = {}
    for key, entries in groups.items():
        counts = Counter(e['position'] for e in entries)
        # Ambiguous positions cannot form a trustworthy parent/ordering relation.
        ordered = sorted(entries, key=lambda e: e['position'])
        entries = []
        stack = [];children = defaultdict(list);covered_end = 0
        for entry in ordered:
            overlaps = entry['position'] <= covered_end
            covered_end = max(covered_end, entry['end'])
            if counts[entry['position']] != 1 or entry['ambiguous']:
                stack.clear()
                continue
            if overlaps:
                stack.clear()
            if entries and any(entries[-1]['end'] < start <= end < entry['position']
                               for start, end in boundaries[key[:2]]):
                # A hidden/unpublished source chunk may contain a new ancestor.
                # Its location is a barrier; never read or expose its text.
                stack.clear()
            entries.append(entry)
            level = entry['level']
            if level is not None:
                while stack and stack[-1]['level'] >= level:
                    stack.pop()
            entry['parent'] = stack[-1] if stack else None
            entry['heading_path'] = [e['title'] for e in stack] + (
                [entry['title']] if level is not None else [])
            children[entry['parent']['section_id'] if entry['parent'] else None].append(entry)
            if level is not None:
                stack.append(entry)
        def projected(entry, *, include_path=False):
            return {'section_id': entry['section_id'], 'title': entry['title'],
                    **({'heading_path': entry['heading_path']} if include_path else {})}
        for i, entry in enumerate(entries):
            siblings = [e for e in children[entry['parent']['section_id'] if entry['parent'] else None]
                        if e['section_id'] != entry['section_id']]
            kids = children[entry['section_id']]
            result[entry['section_id']] = {
                'heading_path': entry['heading_path'],
                'parent': projected(entry['parent']) if entry['parent'] else None,
                'children': [projected(e) for e in kids[:_MAX_NEIGHBOURS]],
                'children_count': len(kids),
                'children_truncated': len(kids) > _MAX_NEIGHBOURS,
                'siblings': [projected(e) for e in siblings[:_MAX_NEIGHBOURS]],
                'siblings_count': len(siblings),
                'siblings_truncated': len(siblings) > _MAX_NEIGHBOURS,
                'previous': projected(entries[i-1], include_path=True) if i else None,
                'next': projected(entries[i+1], include_path=True) if i+1 < len(entries) else None,
                'requires_read_doc': True,
                'navigation_scope': 'published_passages_only',
            }
    return result
