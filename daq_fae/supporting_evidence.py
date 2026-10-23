"""Source-bound input facts that do not certify the user's requested output.

The model may discover a needed specification after the initial goal extraction.
Expose that actual typed fact for partial delivery; never infer compatibility,
arithmetic results, or a new user assertion from it.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata


def support_id(parent_id: str, source_id: str) -> str:
    return 'support_' + hashlib.sha256(
        (parent_id + '\0' + source_id).encode()).hexdigest()[:20]


def _label(value: str) -> str:
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value).casefold()
                  .replace('×', 'x'))


def supporting_specs(matches: list[dict], requirements: list[dict],
                     entities: dict[str, dict], bound_ids: set[str]) -> list[dict]:
    supporting = []
    for row in matches:
        if (row.get('kind') != 'claim' or row.get('status') != 'verified'
                or not isinstance(row.get('id'), str) or row['id'] in bound_ids):
            continue
        entity = entities.get(row['data'].get('entity_id'))
        if entity is None:
            continue
        labels = {_label(str(value)) for value in (
            entity['id'], entity['data']['name'], entity['scope'].get('product', ''),
            *entity['data'].get('aliases', [])) if value}
        document = (row['data'].get('field') == 'source_text'
                    and row['data'].get('evidence_scope') == 'document_statement_only')
        family_document = document and entity['data'].get('entity_type') == 'family'
        if family_document:
            # Family text may contextualize a child, never certify its variant.
            for child in entities.values():
                if child['data'].get('family_entity_id') == entity['id']:
                    labels.update(_label(str(value)) for value in (
                        child['id'], child['data']['name'],
                        *child['data'].get('aliases', [])) if value)
        for parent in requirements:
            if (not isinstance(parent, dict) or not isinstance(parent.get('id'), str)
                    or parent.get('evidence_class', 'governed_required') != 'governed_required'
                    or parent.get('reason') == 'intent_or_entity_not_grounded'
                    or parent.get('conditions_authority') == 'platform_context_unverified'):
                continue
            wanted = parent.get('entities') or []
            if not isinstance(wanted, list) or not any(
                    isinstance(value, str) and _label(value) in labels for value in wanted):
                continue
            conditions = parent.get('conditions') or {}
            if not isinstance(conditions, dict) or (not family_document and any(
                    key in conditions and _label(str(conditions[key])) !=
                    _label(str(row['scope'].get(key, '')))
                    for key in ('variant', 'resolution_variant', 'hardware_revision'))):
                continue
            supporting.append({
                'id': support_id(parent['id'], row['id']),
                'parent_requirement_id': parent['id'], 'capability': 'lookup_spec',
                'source_ids': [row['id']], 'entity_id': entity['id'],
                'field': row['data']['field'],
                'scope': row['scope'],
                'conditions': row['data'].get('conditions', {}),
                'satisfies_parent': False,
                **({'evidence_scope': 'document_statement_only',
                    'source_limits': row['data'].get('source_limits', '')}
                   if document else {}),
            })
            break
    return supporting


def observed_support(content: dict, visible_source_ids: set[str], requirements: list[dict],
                     entities: dict[str, dict],
                     *, allow_dev_source_check: bool = False) -> dict[str, set[str]]:
    """Validate tool metadata against the actual verified typed rows, not text."""
    rows = {row.get('id'): row for row in content.get('matches', [])
            if isinstance(row, dict) and row.get('kind') == 'claim'
            and row.get('data', {}).get('field') != 'source_text'
            and row.get('data', {}).get('evidence_scope') != 'document_statement_only'
            and (row.get('status') == 'verified' or (
                allow_dev_source_check and row.get('status') == 'source_checked'))}
    expected = {item['id']: item for item in supporting_specs(
        [{**row, 'status': 'verified'} for row in rows.values()], requirements, entities, set())}
    accepted = {}
    for item in content.get('supporting_evidence', []):
        if (not isinstance(item, dict) or not isinstance(item.get('id'), str)
                or item != expected.get(item['id'])):
            continue
        source_ids = item.get('source_ids')
        if (not isinstance(source_ids, list) or len(source_ids) != 1
                or not isinstance(source_ids[0], str)
                or source_ids[0] not in visible_source_ids):
            continue
        source_id = source_ids[0]
        row = rows.get(source_id)
        data = row.get('data', {}) if row else {}
        if (item.get('id') != support_id(item['parent_requirement_id'], source_id)
                or item.get('capability') != 'lookup_spec'
                or item.get('satisfies_parent') is not False
                or data.get('entity_id') != item.get('entity_id')
                or data.get('field') != item.get('field') or not data.get('field')
                or row.get('scope') != item.get('scope')
                or data.get('conditions', {}) != item.get('conditions')):
            continue
        accepted[item['id']] = {source_id}
    return accepted
