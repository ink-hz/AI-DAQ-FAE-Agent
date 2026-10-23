"""Qualified source statements, independently bound to the published role view."""
from .supporting_evidence import supporting_specs


def is_document_statement(row: dict) -> bool:
    data = row.get('data', {})
    return (row.get('kind') == 'claim' and data.get('field') == 'source_text'
            and data.get('evidence_scope') == 'document_statement_only')


def section_documents(section: dict, records: dict[str, dict]) -> list[dict]:
    return [records[key] for key in section.get('dependency_claim_ids', [])
            if key in records and records[key].get('status') == 'verified'
            and is_document_statement(records[key])]


def observed_documents(content: dict, visible_ids: set[str], requirements: list[dict],
                       entities: dict[str, dict], records: dict[str, dict],
                       sections: dict[str, dict]) -> dict[str, dict]:
    """Recompute support from trusted bodies/records, never returned claim values."""
    candidates = []
    for item in content.get('matches', []):
        if not isinstance(item, dict):
            continue
        section = sections.get(item.get('section_id'))
        if (section is None or item.get('body') != section.get('body')
                or item.get('status') != 'verified'
                or item.get('evidence_layer') != 'reviewed_section'):
            continue
        if any(item.get(key) != section.get(key) for key in
               ('scope', 'entity_id', 'entity_ids', 'conditions',
                'title', 'knowledge_type', 'evidence_scope', 'source_limits')):
            continue
        candidates.extend(section_documents(section, records))
    expected = {item['id']: item for item in supporting_specs(
        candidates, requirements, entities, set())}
    return {item['id']: item for item in content.get('supporting_evidence', [])
            if isinstance(item, dict) and isinstance(item.get('id'), str)
            and item == expected.get(item['id'])
            and set(item['source_ids']) <= visible_ids}
