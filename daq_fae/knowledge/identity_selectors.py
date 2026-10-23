"""Resolve compositional model labels only through unique reviewed selectors."""
from collections import defaultdict
import re
import unicodedata


def _text(value):
    return unicodedata.normalize('NFKC', value).casefold().replace('×', 'x')


def _key(value):
    return re.sub(r'\s+', '', _text(value))


def _pattern(alias):
    return r'(?<![a-z0-9_-])' + r'\s*'.join(map(re.escape, alias)) + r'(?![a-z0-9_.])'


def _marked(alias):
    return alias.endswith(('版', '版本', '变体', 'variant', 'version'))


def composite_name_present(label: str, text: str) -> bool:
    return bool(re.search(_pattern(_key(label)), _text(text)))


def _measurement_context(text):
    return bool(re.search(r'\b(?:pixels?|resolution|fps)\b|frame\s*rate|'
                          r'output\s*(?:mode|width|size)|像素|分辨率|帧率|取流|输出模式|输出宽度',
                          _text(text)))


def _index(records):
    rows = [r for r in records if r.get('kind') == 'entity' and r.get('status') == 'verified']
    owners = defaultdict(set);by_id = {r['id']: r for r in rows};composed = set()
    for r in rows:
        labels = [r['data']['name'], *r['data'].get('aliases', [])]
        for label in [r['id'], *labels]:owners[_key(label)].add(r['id'])
        scope = r['scope']
        if scope.get('required_selectors') != ['resolution_variant']:
            continue
        value = _text(str(scope.get('resolution_variant', '')))
        if not re.fullmatch(r'\d{3,4}x\d{3,4}', value):
            continue
        for label in labels:
            for selector in (value, value.split('x')[0]):
                for suffix in ('', '版', '版本', '变体', 'variant', 'version'):
                    alias = _key(label + selector + suffix)
                    owners[alias].add(r['id']);composed.add(alias)
    return {alias: by_id[next(iter(ids))] for alias, ids in owners.items()
            if alias in composed and len(ids) == 1}


def composite_identity(label: str, records: list[dict], *, allow_full: bool = False,
                       context_text: str | None = None) -> dict | None:
    """No product-specific string, guessed height or ambiguous width mapping."""
    index = _index(records)
    row = index.get(_key(label))
    if row and context_text is not None and _measurement_context(context_text):
        if not any(r['id'] == row['id'] and _marked(alias)
                   and re.search(_pattern(alias), _text(context_text))
                   for alias, r in index.items()):
            return None
    if row and not allow_full and _key(row['scope']['resolution_variant']) in _key(label):
        return None
    return row


def composite_versions_in_text(text: str, records: list[dict]) -> set[str]:
    normalized = _text(text)
    selected = set()
    for alias, row in _index(records).items():
        # A bare full resolution may be an output mode. Only width-only
        # compositional names extend the explicit-variant inventory here;
        # a full name in entities is handled separately as a declared identity.
        if _key(row['scope']['resolution_variant']) in alias:
            continue
        if _measurement_context(text) and not _marked(alias):continue
        if re.search(_pattern(alias), normalized):selected.add(row['scope']['resolution_variant'])
    return selected
