"""Detect local source provenance before publication and model delivery."""
from __future__ import annotations

import json
import re
import unicodedata

_BLOCK = re.compile(r'```daq-record\n(.*?)\n```', re.DOTALL)
# Explicit archive/workspace roots avoid treating product alternatives such as
# Viewer/SDK, USB/以太网 or RGB-D/IMU as filesystem paths.
_ROOT = re.compile(
    r'(?<![a-z0-9_])(?:\./|\.\./)*(?:tmp|temp|downloads|documents|desktop|'
    r'data/knowledge|knowledge/private)/[^\s/"\'`<>]+'
)
_ABSOLUTE = re.compile(
    r'(?:[a-z]:/|~/|//[^/\s]+/|'
    r'/(?:private|home|users|var|opt|mnt|volumes|tmp|etc|usr|srv)/[^\s/"\'`<>]+)'
)


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _strings(item)


def _normalize(text):
    return unicodedata.normalize('NFKC', text).casefold().replace('\\', '/')


def contains_source_path(section: dict) -> bool:
    """Inspect deliverable metadata/body, including decoded typed JSON strings.

    Structured source_refs are permitted on disk; their paths become exact
    sentinels for content checks, not searchable or model-visible prose.
    """
    values = list(_strings({k: v for k, v in section.items()
                           if k not in {'source_refs', 'record_assertions',
                                        'fact_review', 'permission_review', 'body'}}))
    body = section.get('body', '')
    values.append(body)
    for block in _BLOCK.finditer(body):
        try:
            values.extend(_strings(json.loads(block.group(1))))
        except (ValueError, TypeError):
            # Publication consistency separately rejects malformed typed blocks.
            continue
    paths = [_normalize(ref['path']) for ref in section.get('source_refs', [])
             if isinstance(ref, dict) and isinstance(ref.get('path'), str) and ref['path']]
    return any(_ROOT.search(text) or _ABSOLUTE.search(text)
               or any(path in text for path in paths)
               for value in values for text in [_normalize(value)])


def record_contains_source_path(row: dict) -> bool:
    """Check only typed fields that can be delivered, preserving structured refs.

    Governed link URLs have their own review gate. Conflict candidates are never
    delivered; only the approved conflict notice's identity/scope/conditions are.
    """
    if row.get('status') not in {'verified', 'unsupported', 'conflict'}:
        return False
    data = row.get('data', {})
    if row.get('status') == 'conflict':
        data = {key: data.get(key) for key in ('entity_id', 'field', 'conditions')}
    if row.get('kind') == 'link':
        approved_url = data.get('url')
        def without_url(value):
            if isinstance(value, str) and value == approved_url:
                return ''
            if isinstance(value, dict):
                return {key: without_url(child) for key, child in value.items()}
            if isinstance(value, list):
                return [without_url(child) for child in value]
            return value
        data = without_url(data)
    return contains_source_path({'id': row.get('id'), 'scope': row.get('scope'),
                                 'data': data, 'source_refs': row.get('source_refs', [])})
