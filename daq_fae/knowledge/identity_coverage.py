"""Offline identity and bounded inventory coverage compiler.

This is a private candidate audit, never a runtime resolver or permission grant.
Product vocabulary is supplied by a private configuration, not embedded in code.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unicodedata


def normalize_name(value: str) -> str:
    """Normalize typography only; never remove variant selectors or punctuation."""
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().split())


def _unique(rows):
    return [json.loads(s) for s in sorted({json.dumps(r, sort_keys=True, ensure_ascii=False) for r in rows})]


def _status(rows):
    statuses = {r['status'] for r in rows}
    if 'conflict' in statuses:
        return 'conflict'
    if 'verified' in statuses:
        return 'verified'
    return 'candidate' if rows else 'audited_no_source'


def build_dictionary(records: list[dict], sources: list[dict], sections: list[dict], config: dict) -> dict:
    """Index existing status without adjudicating it or licensing any content.

    Every absence is bounded to these records, not a claim about source contents.
    Full source/fact validation and approval remain upstream/downstream gates.
    """
    records = deepcopy(sorted(records, key=lambda r: r['id']))
    by_id = {r['id']: r for r in records}
    if len(by_id) != len(records):
        raise ValueError('duplicate record identity')
    source_keys = {(s['path'], s['sha256']) for s in sources}
    if len(source_keys) != len(sources):
        raise ValueError('duplicate source identity')
    entities = {r['id']: r for r in records if r['kind'] == 'entity'}
    topologies = {r['id']: r for r in records if r['kind'] == 'topology'}

    def refs_valid(refs):
        if not refs or any((r.get('path'), r.get('sha256')) not in source_keys or not r.get('locator') for r in refs):
            raise ValueError('source reference not in supplied source inventory')

    def entities_valid(ids):
        if not ids or any(i not in entities for i in ids):
            raise ValueError('unknown entity reference')

    def ids_valid(ids):
        if not ids or any(i not in by_id for i in ids):
            raise ValueError('unknown record reference')

    # The configuration cannot smuggle an authorization into generated metadata.
    def no_permissions(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {'view_roles', 'forward_roles', 'fact_review', 'permission_review', 'access_review'} and child:
                    raise ValueError('permission or approval not allowed in dictionary configuration')
                no_permissions(child)
        elif isinstance(value, list):
            for child in value:
                no_permissions(child)
    no_permissions(config)
    for row in records:
        if row['status'] not in {'verified', 'candidate', 'conflict'}:
            raise ValueError('record status outside B1 inventory contract')
        refs_valid(row['source_refs'])
        data = row['data']
        if data.get('entity_id'):
            entities_valid([data['entity_id']])
        if row['kind'] == 'topology':
            entities_valid(data['members'])
        if row['kind'] == 'procedure' and data['topology_id'] not in topologies:
            raise ValueError('unknown topology')
        for alternative in data.get('candidates', []):
            refs_valid([alternative['source_ref']])

    names = []
    for identity, row in sorted(entities.items()):
        data = row['data']
        names.append({'name': data['name'], 'name_kind': 'formal_name_as_recorded', 'entity_ids': [identity],
                      'record_ids': [identity], 'status': row['status'], 'source_refs': row['source_refs']})
        if data.get('variant_selector'):
            names.append({'name': f"{data['name']} {data['variant_selector']}", 'name_kind': 'qualified_display_name',
                          'entity_ids': [identity], 'record_ids': [identity], 'status': row['status'], 'source_refs': row['source_refs']})
    for alias in config.get('aliases', []):
        entities_valid(alias['entity_ids']); ids_valid(alias['record_ids'])
        if alias['status'] != 'candidate':
            raise ValueError('alias requires adjudication outside B1')
        names.append({**deepcopy(alias), 'name_kind': 'candidate_alias',
                      'source_refs': _unique([ref for i in alias['record_ids'] for ref in by_id[i]['source_refs']])})
    ambiguities = []
    for item in config.get('ambiguities', []):
        entities_valid(item['entity_ids']); ids_valid(item['record_ids']); refs_valid(item['source_refs'])
        ambiguities.append({**deepcopy(item), 'status': 'ambiguous', 'adjudication': 'pending', 'automatic_resolution': False})
        for name in item.get('names', []):
            names.append({'name': name, 'name_kind': 'ambiguous_name', 'entity_ids': item['entity_ids'],
                          'record_ids': item['record_ids'], 'source_refs': item['source_refs'], 'status': 'ambiguous', 'ambiguity_id': item['id']})
    for name in names:
        name['normalized_name'] = normalize_name(name['name'])

    claims = [r for r in records if r['kind'] == 'claim']
    fields = []
    for field in sorted({r['data']['field'] for r in claims}):
        matching = [r for r in claims if r['data']['field'] == field]
        vocabulary = deepcopy(config.get('fields', {}).get(field, {}))
        fields.append({**vocabulary, 'field_id': field, 'vocabulary_status': 'candidate',
                       'units': sorted({r['data']['unit'] for r in matching}),
                       'comparators': sorted({r['data']['conditions'].get('comparison', '=') for r in matching}),
                       'comparator_policy': 'preserve_each_record_conditions; missing comparison means stated value, not a bound',
                       'record_ids': [r['id'] for r in matching],
                       'source_refs': _unique([ref for r in matching for ref in r['source_refs']])})

    def cell(matching, **dimensions):
        return {**dimensions, 'status': _status(matching), 'audit_scope': 'supplied_record_inventory_only',
                'record_ids': sorted(r['id'] for r in matching),
                'status_counts': dict(sorted(Counter(r['status'] for r in matching).items())),
                'source_refs': _unique([ref for r in matching for ref in r['source_refs']])}
    coverage = [cell([r for r in claims if r['data']['entity_id'] == entity and r['data']['field'] == field['field_id']],
                     entity_id=entity, field_id=field['field_id']) for entity in sorted(entities) for field in fields]
    relations = []
    for identity, row in sorted(topologies.items()):
        relations.append({'relation_id': identity, 'relation_type': 'topology_membership',
                          'status': row['status'], 'record_ids': [identity], 'source_refs': row['source_refs'],
                          'scope': row['scope'], **deepcopy(row['data'])})
    for relation in config.get('relations', []):
        ids_valid(relation['record_ids']); refs_valid(relation['source_refs']); entities_valid(relation['entity_ids'])
        if relation.get('status') != 'candidate':
            raise ValueError('relation requires separate adjudication')
        relations.append(deepcopy(relation))

    section_coverage = []
    seen_sections = set()
    for section in sorted(sections, key=lambda s: s['section_id']):
        if section['section_id'] in seen_sections:
            raise ValueError('duplicate section identity')
        seen_sections.add(section['section_id'])
        refs_valid(section['source_refs'])
        ids = section.get('entity_ids') or ([section['entity_id']] if section.get('entity_id') else [])
        if ids:
            entities_valid(ids)
        elif section.get('knowledge_type') != 'topic' or not section.get('scope'):
            raise ValueError('section needs entity or topic scope')
        if section['review_status'] != 'candidate' or section.get('view_roles') or section.get('forward_roles'):
            raise ValueError('candidate section permission boundary')
        section_coverage.append({'section_id': section['section_id'], 'entity_ids': ids,
                                 'topology_id': section.get('topology_id'), 'status': 'candidate',
                                 'scope': deepcopy(section.get('scope', {})),
                                 'capability': section.get('capability'), 'source_refs': deepcopy(section['source_refs']),
                                 'gap_ids': deepcopy(section.get('gap_ids', []))})
    return {'format_version': 'b1-private-dictionary-v1', 'online_eligible': False,
            'fact_review': None, 'permission_review': None, 'view_roles': [], 'forward_roles': [],
            'record_inventory': records, 'names': _unique(names), 'ambiguities': sorted(ambiguities, key=lambda r: r['id']),
            'fields': fields, 'relations': sorted(relations, key=lambda r: r['relation_id']), 'coverage': coverage,
            'coverage_by_entity': [cell([r for r in records if r['id'] == e or r['data'].get('entity_id') == e or e in r['data'].get('members', [])], entity_id=e) for e in sorted(entities)],
            'coverage_by_field': [cell([r for r in claims if r['data']['field'] == f['field_id']], field_id=f['field_id']) for f in fields],
            'coverage_by_topology': [cell([r for r in records if r['id'] == t or r['data'].get('topology_id') == t], topology_id=t) for t in sorted(topologies)],
            'coverage_by_source': [cell([r for r in records if any(ref['path'] == s['path'] and ref['sha256'] == s['sha256'] for ref in r['source_refs'])], source_path=s['path'], source_sha256=s['sha256']) for s in sorted(sources, key=lambda s: (s['path'], s['sha256']))],
            'coverage_by_role': [{'role': role, 'permission_status': 'not_assessed_by_b1', 'inventory_status': _status(records), 'granted_view_records': [], 'granted_forward_records': [], 'record_ids': sorted(by_id)} for role in ('internal_fae', 'tmall_support', 'channel')],
            'section_coverage': section_coverage}


def resolve_name(dictionary: dict, name: str) -> dict:
    """Private audit lookup only; candidate matches remain candidate."""
    matches = [r for r in dictionary['names'] if r['normalized_name'] == normalize_name(name)]
    ids = sorted({i for r in matches for i in r['entity_ids']})
    ambiguous = len(ids) > 1 or any(r['status'] in {'ambiguous', 'conflict'} for r in matches)
    status = 'ambiguous' if ambiguous else ('candidate' if matches else 'not_found')
    return {'entity_ids': ids, 'status': status, 'automatic_resolution': False,
            'record_ids': sorted({i for r in matches for i in r['record_ids']})}


def write_private_dictionary(path: Path, result: dict) -> None:
    """Atomic 0600 artifact beneath a 0700 Git-ignored directory."""
    path = Path(path).absolute()
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('symlink destination')
    existing = next(p for p in path.parents if p.exists())
    check = subprocess.run(['git', '-C', str(existing), 'check-ignore', '-q', '--', str(path)], capture_output=True)
    if check.returncode:
        raise ValueError('private output must be Git ignored')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    fd, temporary = tempfile.mkstemp(prefix='.b1-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write('\n')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
