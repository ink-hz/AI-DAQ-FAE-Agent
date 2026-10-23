"""Validate reviewed family membership and documented configuration prerequisites."""
import re
import unicodedata


def mentions_variant(entity: dict, text: str) -> bool:
    """Detect explicit full or short selectors from the reviewed entity registry."""
    text = unicodedata.normalize('NFKC', text).casefold().replace('×', 'x')
    compact = re.sub(r'\s+', '', text)
    if entity['id'].casefold() in compact:
        return True
    for key in entity['scope'].get('required_selectors', []):
        value = re.sub(r'\s+', '', str(entity['scope'][key])).casefold().replace('×', 'x')
        resolution = re.fullmatch(r'(\d{3,4})x\d{3,4}', value)
        tokens = [value, resolution.group(1)] if resolution else [value]
        if any(token and re.search(r'(?<![0-9])' + re.escape(token) + r'(?![0-9])', compact)
               for token in tokens):
            return True
    return False


def family_findings(records: list[dict]) -> list[dict]:
    entities = {row['id']: row for row in records if row.get('kind') == 'entity'
                and isinstance(row.get('data'), dict) and isinstance(row.get('scope'), dict)}
    findings = []
    for row in records:
        data = row.get('data', {})
        if not isinstance(data, dict):
            continue
        parent_id = data.get('family_entity_id') if row.get('kind') == 'entity' else None
        if parent_id is not None:
            parent = entities.get(parent_id) if isinstance(parent_id, str) else None
            if (parent is None or parent['data'].get('entity_type') != 'family'
                    or row['data'].get('entity_type') == 'family'
                    or parent['scope'].get('product') != row['scope'].get('product')
                    or parent.get('status') != 'verified'):
                findings.append({'record_id': row['id'], 'code': 'entity_family_invalid'})
        if row.get('kind') != 'topology' or not any(
                key in data for key in ('query_members', 'prerequisite_members')):
            continue
        query, prerequisites = data.get('query_members'), data.get('prerequisite_members')
        if (not isinstance(query, list) or not query or not isinstance(prerequisites, list)
                or any(not isinstance(value, str) for value in query + prerequisites)
                or len(set(query)) != len(query) or len(set(prerequisites)) != len(prerequisites)
                or set(query) & set(prerequisites)
                or set(query) | set(prerequisites) != set(data.get('members', []))):
            findings.append({'record_id': row['id'], 'code': 'topology_query_members_invalid'})
    return findings
