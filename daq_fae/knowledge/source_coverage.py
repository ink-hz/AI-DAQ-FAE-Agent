"""Offline bidirectional coverage; intake and inherited reviews grant no new rights."""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import date
import hashlib
import json
from pathlib import Path
import re

COVERAGE_STATUSES = frozenset({'reviewed_published', 'candidate_pending', 'conflict_pending',
                               'audited_no_source', 'not_audited', 'verified_unsupported'})


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def source_audit_fingerprint(row):
    """Bind a bounded absence audit to exact coverage and audit evidence identity."""
    audit = row.get('source_audit', {})
    basis = {
        'format_version': 'daq-source-audit/v1',
        'coverage': {key: deepcopy(row.get(key, {} if key in {'scope', 'conditions'} else None))
                     for key in ('coverage_id', 'coverage_kind', 'entity_id', 'topology_id',
                                 'field_id', 'scope', 'conditions', 'source_refs')},
        'audit': {key: deepcopy(value) for key, value in audit.items() if key != 'audit_sha256'},
    }
    return hashlib.sha256(_canonical(basis).encode()).hexdigest()


def _valid_audit_ref(ref):
    if not isinstance(ref, dict) or set(ref) != {'path', 'sha256', 'locator'}:
        return False
    path, digest, locator = ref['path'], ref['sha256'], ref['locator']
    if (not isinstance(path, str) or not path or '\\' in path or ':' in path
            or any(ord(char) < 32 or ord(char) == 127 for char in path)
            or any(part in {'', '.', '..'} for part in path.split('/'))
            or not isinstance(digest, str) or re.fullmatch(r'[0-9a-f]{64}', digest) is None
            or not isinstance(locator, dict)):
        return False
    if locator.get('kind') == 'page':
        return (set(locator) == {'kind', 'page'} and type(locator['page']) is int
                and locator['page'] > 0)
    if locator.get('kind') == 'lines':
        return (set(locator) == {'kind', 'start', 'end'}
                and type(locator['start']) is int and type(locator['end']) is int
                and 0 < locator['start'] <= locator['end'])
    return False


def _unique(rows, key, label):
    result = {}
    for row in rows:
        if row[key] in result:
            raise ValueError('duplicate ' + label)
        result[row[key]] = row
    return result


def _overlaps(left, right):
    if left.get('kind') != right.get('kind'):
        return False
    if left['kind'] == 'lines':
        return left['start'] <= right['end'] and right['start'] <= left['end']
    return left == right


def build_source_coverage(snapshot, dispositions, assets, sections, candidate_records,
                          published_records):
    """Map every extraction unit, retaining unresolved pages and unique asset decisions."""
    sources = _unique(snapshot['sources'], 'path', 'source path')
    intake = _unique(dispositions['text_dispositions'] + assets['asset_inventory'], 'path', 'disposition path')
    if sources.keys() != intake.keys() or any(
            any(source[k] != intake[path][k] for k in ('sha256', 'size'))
            for path, source in sources.items()):
        raise ValueError('disposition inventory identity mismatch')
    for group in (dispositions, assets):
        if (group.get('archive_manifest_sha256', snapshot['archive_manifest_sha256'])
                != snapshot['archive_manifest_sha256']):
            raise ValueError('disposition archive mismatch')
    chunks = _unique(snapshot['chunks'], 'chunk_id', 'chunk identity')
    by_path = defaultdict(list)
    for chunk in chunks.values():
        path = chunk['source_path']
        if (path not in sources or sources[path]['kind'] == 'asset' or
                chunk['source_sha256'] != sources[path]['sha256']):
            raise ValueError('chunk source identity mismatch')
        by_path[path].append(chunk)
    mapped = {key: {'candidate_sections': set(), 'candidate_records': set(), 'published_records': set()}
              for key in chunks}
    file_maps = {path: {'candidate_sections': set(), 'candidate_records': set(), 'published_records': set()}
                 for path in sources}
    for rows, key, target in ((sections, 'section_id', 'candidate_sections'),
                              (candidate_records, 'id', 'candidate_records'),
                              (published_records, 'id', 'published_records')):
        _unique(rows, key, target)
        for row in rows:
            for ref in row.get('source_refs', []):
                path = ref['path']
                if path not in sources or sources[path]['sha256'] != ref['sha256']:
                    raise ValueError('reference source hash mismatch')
                location = ref['locator']
                hits = [chunk for chunk in by_path[path] if _overlaps(chunk['locator'], location)]
                if location.get('kind') != 'file' and not hits:
                    raise ValueError('reference locator outside extraction')
                if location.get('kind') == 'lines':
                    # The extractor trims blank separator lines between chunks.
                    maximum = max(chunk['locator']['end'] for chunk in by_path[path])
                    if not 1 <= location['start'] <= location['end'] <= maximum:
                        raise ValueError('reference lines outside extraction')
                file_maps[path][target].add(row[key])
                for hit in hits:
                    mapped[hit['chunk_id']][target].add(row[key])
    files, units = [], []
    same_hash, same_name = defaultdict(list), defaultdict(list)
    for path, source in sources.items():
        same_hash[source['sha256']].append(path)
        same_name[Path(path).name].append(path)
    for path, source in sorted(sources.items()):
        row = deepcopy(source)
        row.update({key: sorted(value) for key, value in file_maps[path].items()})
        row['same_bytes_sources'] = sorted(p for p in same_hash[source['sha256']] if p != path)
        row['same_name_different_bytes'] = sorted(p for p in same_name[Path(path).name]
                                                if sources[p]['sha256'] != source['sha256'])
        row['source_review_status'] = 'not_audited'
        row['fact_review'] = None
        row['permission_review'] = None
        row['view_roles'], row['forward_roles'] = [], []
        if source['kind'] == 'asset':
            category = intake[path].get('category', 'unclassified_asset')
            refs = intake[path].get('document_references', [])
            row['disposition'] = ('duplicate_bytes' if row['same_bytes_sources'] else
                                  'technical_candidate' if refs else 'index_only')
            row.update(asset_category=category, document_references=deepcopy(refs),
                       disposition_basis='byte_identity_or_existing_reference_metadata_only',
                       visual_review_status='not_audited', sensitivity_status='not_audited',
                       compatibility_status='not_established')
        else:
            row['disposition'] = intake[path]['status']
            row['disposition_reason'] = intake[path].get('reason', 'Inherited intake decision; not a fact review')
        row['unpublished_reason'] = 'No new fact or permission review; candidate mappings do not establish publication'
        row['responsible_roles'] = ['source_cleaner', 'product_fact_reviewer', 'material_permission_reviewer']
        files.append(row)
        for chunk in by_path[path]:
            unit = {k: deepcopy(chunk[k]) for k in ('chunk_id', 'source_path', 'source_sha256', 'locator', 'text_sha256')}
            unit.update({key: sorted(value) for key, value in mapped[chunk['chunk_id']].items()})
            unit['source_review_status'] = 'not_audited'
            unit['gap'] = None if any(mapped[chunk['chunk_id']].values()) else {
                'reason': 'no_candidate_or_published_mapping', 'status': 'not_audited',
                'responsible_role': 'source_cleaner'}
            units.append(unit)
    texts = sum(row['kind'] != 'asset' for row in files)
    return {'format_version': 'daq-source-coverage/v1',
            'archive_manifest_sha256': snapshot['archive_manifest_sha256'],
            'extractor_version': snapshot['extractor_version'], 'authority': 'private_intake_only',
            'files': files, 'chunks': units,
            'summary': {'texts': texts, 'assets': len(files) - texts, 'chunks': len(units),
                        'mapped_chunks': sum(row['gap'] is None for row in units),
                        'gap_chunks': sum(row['gap'] is not None for row in units)}}


def build_field_matrix(candidate_records, published_records, sections, software, legacy_coverage):
    """Keep entity/field/condition cells distinct and software evidence levels unpromoted."""
    cells = {}
    def cell_for(entity, field, scope, conditions, kind='claim'):
        identity = [kind, entity, field, scope, conditions]
        key = hashlib.sha256(_canonical(identity).encode()).hexdigest()
        if key not in cells:
            cells[key] = {'coverage_id': 'kq:' + key, 'dimension': kind, 'entity_id': entity,
                          'field_id': field, 'scope': deepcopy(scope), 'conditions': deepcopy(conditions),
                          'candidate_records': [], 'published_records': [], 'candidate_sections': [],
                          'source_refs': [], 'values': [], 'status': 'not_audited',
                          'source_review_status': 'not_audited', 'audit_scope': 'input_record_inventory_only',
                          'legacy_coverage_ids': [], 'inventory_gaps': [],
                          'fact_review': None, 'permission_review': None}
        return cells[key]
    for rows, published in ((candidate_records, False), (published_records, True)):
        _unique(rows, 'id', 'record identity')
        for row in rows:
            data, kind = row.get('data', {}), row['kind']
            if kind == 'entity':
                field = 'identity'
                entity = row['id']
            elif kind == 'claim':
                field = data['field']
                entity = data['entity_id']
            else:
                field = row['id']
                entity = data.get('entity_id', data.get('topology_id', row['id']))
            cell = cell_for(entity, field, row.get('scope', {}), data.get('conditions', {}), kind)
            cell['published_records' if published else 'candidate_records'].append(row['id'])
            for ref in row.get('source_refs', []):
                if ref not in cell['source_refs']: cell['source_refs'].append(deepcopy(ref))
            cell['values'].append({'record_id': row['id'], 'source': 'published' if published else 'candidate',
                                   'data': deepcopy(data), 'record_status': row.get('status')})
            if published:
                if row.get('status') == 'unsupported':
                    if not row.get('source_refs') or not row.get('fact_review'):
                        raise ValueError('negative assertion lacks reviewed source evidence')
                    if cell['status'] != 'conflict_pending':
                        cell['status'] = 'verified_unsupported'
                elif (row.get('status') == 'verified' and row.get('answerable') is True
                      and row.get('fact_review') and row.get('access_review', {}).get('view_roles')):
                    if cell['status'] != 'conflict_pending':
                        cell['status'] = 'reviewed_published'
                else:
                    raise ValueError('published evidence is not reviewed and authorized')
                cell['source_review_status'] = 'inherited_review_not_reperformed'
                cell['audit_scope'] = 'explicit_input_release_only; current_environment_activity_unconfirmed'
            elif cell['status'] not in {'reviewed_published', 'verified_unsupported'}:
                cell['status'] = ('conflict_pending' if row.get('status') == 'conflict'
                                  or cell['status'] == 'conflict_pending' else 'candidate_pending')
    for row in legacy_coverage:
        # Preserve the old audit's actual domain; inventory absence is not document absence.
        cell = cell_for(row.get('entity_id', row.get('topology_id', 'unresolved')),
                        row.get('field_id', row['coverage_id']), row.get('scope', {}),
                        row.get('conditions', {}), 'claim' if row.get('coverage_kind') == 'entity_field'
                        else row.get('coverage_kind', 'relation'))
        cell['legacy_coverage_ids'].append(row['coverage_id'])
        if row['status'] == 'audited_no_source':
            audit = row.get('source_audit', {})
            if not isinstance(audit, dict):
                audit = {}
            valid_audit = (audit.get('decision') == 'audited_no_source'
                           and all(isinstance(audit.get(key), str) and audit[key].strip()
                                   for key in ('reviewer', 'reviewed_at', 'audit_scope'))
                           and audit.get('audit_scope') != 'supplied_record_inventory_only'
                           and isinstance(row.get('scope', {}), dict)
                           and isinstance(row.get('conditions', {}), dict)
                           and isinstance(audit.get('source_refs'), list)
                           and bool(audit['source_refs'])
                           and audit['source_refs'] == row.get('source_refs')
                           and all(_valid_audit_ref(ref) for ref in audit['source_refs'])
                           and isinstance(audit.get('audit_sha256'), str)
                           and re.fullmatch(r'[0-9a-f]{64}', audit['audit_sha256']) is not None
                           and audit['audit_sha256'] == source_audit_fingerprint(row))
            try:
                date.fromisoformat(audit.get('reviewed_at', ''))
            except (TypeError, ValueError):
                valid_audit = False
            if valid_audit:
                cell['source_audit'] = deepcopy(audit)
                if cell['status'] == 'not_audited':
                    cell.update(status='audited_no_source',
                                source_review_status='bounded_source_audit_recorded',
                                audit_scope=audit['audit_scope'])
            else:
                # No records in an inventory says nothing about uninspected originals.
                cell['inventory_gaps'].append({
                    'coverage_id': row['coverage_id'],
                    'audit_scope': row.get('audit_scope', 'unspecified_legacy_inventory'),
                    'record_ids': deepcopy(row.get('record_ids', [])),
                    'legacy_status': row['status']})
        elif cell['status'] == 'not_audited':
            cell['status'] = 'candidate_pending' if row.get('source_refs') else 'not_audited'
        for ref in row.get('source_refs', []):
            if ref not in cell['source_refs']: cell['source_refs'].append(deepcopy(ref))
    for cell in cells.values():
        ids = set(cell['candidate_records'] + cell['published_records'])
        cell['candidate_sections'] = sorted(section['section_id'] for section in sections
                                            if ids.intersection(section.get('dependency_claim_ids', [])))
        cell['unresolved_reason'] = ('Existing reviewed input release; no fresh source review performed'
                                     if cell['status'] == 'reviewed_published' else
                                     'Source/field semantics and separate permission review require decisions')
    software_rows = []
    for row in software:
        entry = deepcopy(row)
        entry.update(status='candidate_pending', device_support='unknown',
                     fact_review=None, permission_review=None, view_roles=[], forward_roles=[])
        software_rows.append(entry)
    result = sorted(cells.values(), key=lambda row: row['coverage_id'])
    return {'format_version': 'daq-field-matrix/v1', 'authority': 'private_candidate_only',
            'cells': result, 'software': software_rows,
            'summary': {'cells': len(result), 'statuses': dict(sorted(Counter(c['status'] for c in result).items())),
                        'published_claims': sum(len(c['published_records']) for c in result if c['dimension'] == 'claim'),
                        'software_candidates': len(software_rows)}}


def write_quality_batch(output: Path, artifacts: dict):
    """Create one immutable private batch; never overwrite historical output."""
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    hashes = {}
    for filename, value in artifacts.items():
        if Path(filename).name != filename or not filename.endswith('.json'):
            raise ValueError('artifact must be a JSON basename')
        raw = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()
        with (output / filename).open('xb') as stream:
            (output / filename).chmod(0o600)
            stream.write(raw)
        hashes[filename] = hashlib.sha256(raw).hexdigest()
    return hashes
