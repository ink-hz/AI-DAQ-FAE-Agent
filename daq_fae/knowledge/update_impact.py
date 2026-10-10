"""Private offline dependency impact; never publishes or grants an approval.

Bundles contain snapshot, sections, records, coverage and questions. Stable IDs
and explicit dependencies are required; inferred identity/field edges supplement
those declarations. The union of old/new graphs retains removed dependencies.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
import hashlib
import json

from .records import _valid_source_ref, access_fingerprint, impact_report, record_fingerprint
from .source_import import compare_snapshots

_KEYS = {'source': ('sources', 'path'), 'section': ('sections', 'section_id'),
         'record': ('records', 'id'), 'coverage': ('coverage', 'coverage_id'),
         'question': ('questions', 'question_id')}
_DIMENSIONS = ('entity_ids', 'field_ids', 'topology_ids', 'capabilities')
_NEGATIVE = {'unknown', 'unsupported', 'audited_no_source'}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _refs(row):
    return row.get('source_refs', []) + [c['source_ref'] for c in row.get('data', {}).get('candidates', [])
                                       if 'source_ref' in c]


def _roles(row):
    access = row.get('access_review') or row
    return {key: sorted(access.get(key, [])) for key in ('view_roles', 'forward_roles')}


def _scope(row):
    declared = row.get('impact_scope', {})
    data = row.get('data', {})
    result = {key: set(declared.get(key, [])) for key in _DIMENSIONS}
    for plural, singular in (('entity_ids', 'entity_id'), ('field_ids', 'field'),
                             ('topology_ids', 'topology_id'), ('capabilities', 'capability')):
        for part in (row, data):
            result[plural].update(part.get(plural, []))
            if part.get(singular): result[plural].add(part[singular])
    if row.get('field_id'): result['field_ids'].add(row['field_id'])
    if row.get('kind') == 'entity': result['entity_ids'].add(row['id'])
    if row.get('kind') == 'topology':
        result['topology_ids'].add(row['id'])
        result['entity_ids'].update(data.get('members', []))
    return result


def _overlaps(left, right):
    # An explicitly disjoint shared dimension proves irrelevance. Missing
    # dimensions cannot prove irrelevance and therefore keep the review open.
    return not any(left[k] and right[k] and not left[k] & right[k] for k in _DIMENSIONS)


def _index(bundle):
    result = {}
    for kind, (key, identity) in _KEYS.items():
        rows = bundle['snapshot'].get(key, []) if kind == 'source' else bundle.get(key, [])
        for row in rows:
            value = row.get(identity)
            if not isinstance(value, str) or not value or (kind, value) in result:
                raise ValueError('invalid or duplicate ' + kind + ' identity')
            result[kind, value] = row
    return result


def _graph(bundle, historical_nodes=()):
    nodes = _index(bundle)
    edges = set()

    def edge(dependency, consumer):
        if dependency not in nodes and dependency not in historical_nodes:
            raise ValueError('missing dependency: ' + dependency[0])
        edges.add((dependency, consumer))

    for node, row in nodes.items():
        kind, identity = node
        if kind == 'source': continue
        for ref in _refs(row): edge(('source', ref['path']), node)
        fields = [('record_ids', 'record'), ('section_ids', 'section'), ('coverage_ids', 'coverage'),
                  ('dependency_claim_ids', 'record'), ('dependency_record_ids', 'record'),
                  ('dependency_section_ids', 'section')]
        for field, dependency_kind in fields:
            ids = row.get(field, [])
            if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
                raise ValueError('invalid dependency list')
            for dep in ids:
                edge((dependency_kind, dep), node)
        if kind == 'section':
            for assertion in row.get('record_assertions', []):
                rid = assertion['id']
                if rid not in row.get('dependency_claim_ids', []):
                    raise ValueError('assertion dependency missing')
                # Only asserted facts inherit section applicability; context-only
                # references must not propagate changes back into shared facts.
                edge(('record', rid), node)
                edges.add((node, ('record', rid)))
        if kind in {'section', 'record'}:
            scope = _scope(row)
            for rid in scope['entity_ids'] | scope['topology_ids']:
                if ('record', rid) in nodes and ('record', rid) != node:
                    edge(('record', rid), node)
    return nodes, edges


def _closure(seeds, edges):
    outgoing = defaultdict(set)
    for dependency, consumer in edges: outgoing[dependency].add(consumer)
    seen = set(seeds); pending = list(seeds)
    while pending:
        for node in outgoing[pending.pop()] - seen:
            seen.add(node); pending.append(node)
    return seen


def _lists(nodes):
    return {kind + ('_paths' if kind == 'source' else '_ids'): sorted(i for k, i in nodes if k == kind)
            for kind in _KEYS}


def _valid_signature(row):
    try:
        for field, fingerprint in (('fact_review', record_fingerprint), ('access_review', access_fingerprint)):
            review = row.get(field) or {}
            if not isinstance(review.get('reviewer'), str) or not review['reviewer'].strip(): return False
            date.fromisoformat(review['reviewed_at'])
            if review.get('record_sha256') != fingerprint(row): return False
        return row.get('status') in {'verified', 'unsupported'}
    except (KeyError, TypeError, ValueError):
        return False



def _source_context(snapshot):
    sources = {s['path']: s for s in snapshot['sources']}
    chunks = {(c['source_path'], c['source_sha256'], json.dumps(c['locator'], sort_keys=True))
              for c in snapshot.get('chunks', [])}
    return sources, chunks


def _extractions(snapshot):
    by_source = defaultdict(list)
    for chunk in snapshot.get('chunks', []):
        by_source[chunk['source_path']].append(_json(chunk))
    return {path: sorted(chunks) for path, chunks in by_source.items()}

def plan_update(previous: dict, current: dict) -> dict:
    """Return dependency review closures without changing inputs or release state.

    Scope on new sources must be a deliberately maintained impact_scope, never
    inferred from filenames. An absent scope conservatively revisits all gaps.
    Signature reuse is eligibility only, and requires valid existing signatures.
    """
    old, old_edges = _graph(previous)
    new, new_edges = _graph(current, old)
    edges = old_edges | new_edges
    change = compare_snapshots(previous['snapshot'], current['snapshot'])
    seeds = defaultdict(set)
    old_extraction, new_extraction = _extractions(previous['snapshot']), _extractions(current['snapshot'])
    for path in old_extraction.keys() | new_extraction.keys():
        if old_extraction.get(path) != new_extraction.get(path):
            if ('source', path) not in old and ('source', path) not in new:
                raise ValueError('missing extraction source dependency')
            seeds['extraction_changed'].add(('source', path))
    for label, reason in (('added', 'source_added'), ('changed', 'source_changed'), ('removed', 'source_removed')):
        seeds[reason].update(('source', p) for p in change[label])
    # Reuse the established record impact calculation, then refine new-source
    # negative review using declared scope rather than reviewing unrelated rows.
    legacy = impact_report(previous.get('records', []), change)
    for rid in legacy['impacted_record_ids']:
        reason = 'source_removed' if any(r['path'] in change['removed'] for r in _refs(old['record', rid])) else 'source_changed'
        seeds[reason].add(('record', rid))
    negative = set()
    for node, row in {**old, **new}.items():
        if node[0] not in {'record', 'coverage'} or row.get('status') not in _NEGATIVE: continue
        for path in change['added']:
            if _overlaps(_scope(new['source', path]), _scope(row)):
                seeds['source_added'].add(node); negative.add(node)
    if previous['snapshot'].get('extractor_version') != current['snapshot'].get('extractor_version'):
        seeds['extractor_changed'].update(n for n, r in {**old, **new}.items()
                                         if n[0] == 'source' and r.get('kind') != 'asset')
    for node in old.keys() | new.keys():
        before, after = old.get(node), new.get(node)
        if node[0] == 'source' and before is not None and after is not None:
            before = {k: v for k, v in before.items() if k != 'modified_at_utc'}
            after = {k: v for k, v in after.items() if k != 'modified_at_utc'}
        if before == after: continue
        reason = node[0] + ('_added' if before is None else '_removed' if after is None else '_changed')
        seeds[reason].add(node)
        if before is not None and after is not None:
            if any(set(_roles(after)[k]) - set(_roles(before)[k]) for k in ('view_roles', 'forward_roles')):
                seeds['role_expanded'].add(node)
    for key, dimension, reason in (('sku_definitions', 'entity_ids', 'sku_changed'),
                                   ('field_definitions', 'field_ids', 'field_changed')):
        before, after = previous.get(key, {}), current.get(key, {})
        changed = {k for k in before.keys() | after.keys() if before.get(k) != after.get(k)}
        for node, row in list(old.items()) + list(new.items()):
            if _scope(row)[dimension] & changed: seeds[reason].add(node)
    closures = {reason: _closure(nodes, edges) for reason, nodes in sorted(seeds.items()) if nodes}
    affected = set().union(*closures.values()) if closures else set()
    withdrawn = [rid for kind, rid in (closures.get('source_removed', set()) | closures.get('record_removed', set()))
                 if kind == 'record' and old.get((kind, rid), {}).get('status') == 'verified']
    reusable = []
    old_sources, old_chunks = _source_context(previous['snapshot'])
    new_sources, new_chunks = _source_context(current['snapshot'])
    for node in old.keys() & new.keys():
        if (node[0] != 'record' or node in affected
                or not previous['snapshot'].get('extractor_version')
                or not current['snapshot'].get('extractor_version')): continue
        before, after = old[node], new[node]
        if (_valid_signature(before) and _valid_signature(after)
                and _refs(before) and _refs(after)
                and all(_valid_source_ref(ref, old_sources, old_chunks) is None for ref in _refs(before))
                and all(_valid_source_ref(ref, new_sources, new_chunks) is None for ref in _refs(after))
                and record_fingerprint(before) == record_fingerprint(after)
                and _roles(before) == _roles(after)):
            reusable.append(node[1])
    return {'format_version': 'update-impact-v1', 'online_eligible': False,
            'changes': change, 'review': {r: _lists(n) for r, n in closures.items()},
            'affected': _lists(affected), 'withdraw_positive_record_ids': sorted(withdrawn),
            'recheck_negative_ids': sorted(i for k, i in negative if k == 'record'),
            'reusable_signature_record_ids': sorted(reusable),
            'graph': {'nodes': [{'kind': k, 'id': i} for k, i in sorted(old.keys() | new.keys())],
                      'edges': [{'from': list(a), 'to': list(b)} for a, b in sorted(edges)]},
            'input_sha256': hashlib.sha256(_json([previous, current]).encode()).hexdigest()}


def bind_coverage_questions(cells: list[dict]) -> tuple[list[dict], list[dict]]:
    """Bind draft questions to stable typed coverage dimensions, never row order.

    Identity excludes status, evidence and dependency membership so changed
    evidence updates the same logical cell. Callers put additional applicability
    dimensions in scope. Equal duplicates collapse; conflicting duplicates fail.
    """
    rows, identities = {}, {}
    for supplied in cells:
        row = json.loads(_json(supplied))
        kind = row.get('coverage_kind')
        scope = row.get('scope', {})
        if not isinstance(scope, dict):
            raise ValueError('invalid coverage identity scope')
        identity = {'version': 1, 'kind': kind, 'scope': scope}
        if kind == 'entity_field':
            for key in ('entity_id', 'field_id'):
                if not isinstance(row.get(key), str) or not row[key].strip():
                    raise ValueError('missing coverage identity dimension: ' + key)
                identity[key] = row[key]
        elif kind == 'section':
            ids = row.get('section_ids')
            if not isinstance(ids, list) or not ids or not all(isinstance(i, str) and i.strip() for i in ids):
                raise ValueError('missing section coverage identity')
            identity['section_ids'] = sorted(set(ids))
            row['section_ids'] = identity['section_ids']
        else:
            raise ValueError('unsupported coverage identity kind')
        digest = hashlib.sha256(_json(identity).encode()).hexdigest()
        cid = 'coverage:' + kind + ':' + digest
        if cid in identities and identities[cid] != identity:
            raise ValueError('coverage identity hash collision')
        if 'coverage_id' in row and row['coverage_id'] != cid:
            raise ValueError('supplied coverage identity differs from dimensions')
        row['coverage_id'] = cid
        row['scope'] = scope
        if cid in rows and rows[cid] != row:
            raise ValueError('conflicting coverage identity')
        identities[cid] = identity
        rows[cid] = row
    coverage = [rows[cid] for cid in sorted(rows)]
    family = 'coverage_state_and_boundaries'
    questions = [{
        'question_id': 'dev:draft:' + hashlib.sha256(_json([family, cell['coverage_id']]).encode()).hexdigest(),
        'coverage_ids': [cell['coverage_id']], 'status': 'draft',
        'replay_status': 'not_replayed', 'approval_status': 'not_approved',
        'question': 'Check evidence status, exact applicability and sources for this coverage cell; preserve gaps and conflicts.',
        'question_family': family, 'frozen': False,
    } for cell in coverage]
    return coverage, questions
