"""Offline section/record/source consistency, independent of runtime publication.

Assertions are complete typed projections, rendered deterministically. Arbitrary
prose is never declared consistent by matching a few numbers or a body checksum.
Unmapped prose remains a coverage gap until independently reconciled.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json

from .candidate_sections import validate_section
from .records import _valid_source_ref, validate_records

_CORE = ('id', 'kind', 'status', 'scope', 'source_refs', 'data')


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def render_record(row: dict) -> str:
    """Lossless typed assertion block; provenance remains structured metadata.

    A candidate block is for private review only. A source_text claim preserves
    the original wording without pretending that it is a normalized hard fact.
    """
    projection = {key: row[key] for key in ('id', 'kind', 'status', 'scope', 'data')}
    return '```daq-record\n' + _json(projection) + '\n```'


def _data(row):
    value = row.get('data')
    return value if isinstance(value, dict) else {}


def _metadata_findings(section, rows, by_id):
    """Compare explicitly declared section metadata with typed assertions.

    Entity membership is the union across assertions, not equality to each
    claim. Procedure arrays concatenate in assertion order. Common applicability
    metadata applies to every assertion; distinct conditions belong in the
    individual record blocks or in separately scoped sections.
    """
    findings = []

    def add(field, suffix, rid=None):
        findings.append({'section_id': section['section_id'], 'record_id': rid,
                         'code': 'section_' + field + '_' + suffix})

    entities, topologies = set(), set()
    for row in rows:
        data = _data(row)
        if row['kind'] == 'entity':
            entities.add(row['id'])
        if isinstance(data.get('entity_id'), str):
            entities.add(data['entity_id'])
        if isinstance(data.get('entity_ids'), list):
            entities.update(i for i in data['entity_ids'] if isinstance(i, str))
        if row['kind'] == 'topology':
            topologies.add(row['id'])
            entities.update(i for i in data.get('members', []) if isinstance(i, str))
        if isinstance(data.get('topology_id'), str):
            topologies.add(data['topology_id'])
            topology = by_id.get(data['topology_id'])
            if topology and topology.get('kind') == 'topology':
                entities.update(i for i in _data(topology).get('members', []) if isinstance(i, str))
    for field in ('entity_id', 'entity_ids'):
        if field not in section:
            continue
        if not entities:
            add(field, 'unreconciled')
        elif field == 'entity_id':
            if not isinstance(section[field], str) or section[field] not in entities:
                add(field, 'mismatch')
        elif not isinstance(section[field], list) or not all(isinstance(i, str) for i in section[field]) \
                or set(section[field]) != entities:
            add(field, 'mismatch')
    if 'topology_id' in section:
        if not topologies:
            add('topology_id', 'unreconciled')
        elif not isinstance(section['topology_id'], str) or topologies != {section['topology_id']}:
            add('topology_id', 'mismatch')

    for field in ('conditions', 'hardware_revision', 'software_versions'):
        if field not in section:
            continue
        if not rows:
            add(field, 'unreconciled')
        for row in rows:
            data = _data(row)
            conditions = data.get('conditions')
            conditions = conditions if isinstance(conditions, dict) else {}
            if field == 'conditions' and 'conditions' in data:
                expected = conditions.get('section_conditions', data['conditions'])
            elif field in data:
                expected = data[field]
            elif field in conditions:
                expected = conditions[field]
            elif field == 'software_versions' and row['kind'] == 'software' and 'version' in data:
                expected = data['version']
            else:
                add(field, 'unreconciled', row['id'])
                continue
            if _json(section[field]) != _json(expected):
                add(field, 'mismatch', row['id'])

    procedures = [row for row in rows if row['kind'] == 'procedure']
    fields = {'steps': 'steps', 'preparation_requirements': 'prerequisites',
              'prerequisites': 'prerequisites', 'checks': 'checks',
              'failure_branches': 'failure_branches'}
    for section_field, record_field in fields.items():
        if section_field not in section:
            continue
        if not procedures or any(not isinstance(_data(row).get(record_field), list) for row in procedures):
            add(section_field, 'unreconciled')
            continue
        expected = [item for row in procedures for item in _data(row)[record_field]]
        if _json(section[section_field]) != _json(expected):
            add(section_field, 'mismatch')
    steps = section.get('steps')
    if isinstance(steps, list) and steps and all(isinstance(step, dict) for step in steps):
        for step_field, record_field in (('checkpoint', 'checks'), ('failure_branch', 'failure_branches')):
            if not all(step_field in step for step in steps) or not procedures:
                add('step_' + record_field, 'unreconciled')
                continue
            expected = [step[step_field] for step in steps if step[step_field] is not None]
            if any(not isinstance(_data(row).get(record_field), list) for row in procedures):
                add('step_' + record_field, 'unreconciled')
            else:
                actual = [item for row in procedures for item in _data(row)[record_field]]
                if _json(actual) != _json(expected):
                    add('step_' + record_field, 'mismatch')
    return findings


def audit_sections(sections, bodies, records, snapshot, *, historical_records=None):
    """Audit supplied immutable inputs. Findings are fatal to consistency.

    This result is NOT a release or answerability decision. Context dependencies
    establish impact edges only; record_assertions establish exact equality.
    """
    normalized, record_findings = validate_records(records, snapshot)
    sources = {row['path']: row for row in snapshot.get('sources', [])}
    chunks = {(row['source_path'], row['source_sha256'], json.dumps(row['locator'], sort_keys=True))
              for row in snapshot.get('chunks', [])}
    findings = [dict(f, section_id=None) for f in record_findings]
    by_id = {r['id']: r for r in normalized if isinstance(r.get('id'), str)}
    dependencies, coverage, section_dependencies = [], [], []
    section_ids = {s.get('section_id') for s in sections if isinstance(s.get('section_id'), str)}

    def finding(sid, rid, code):
        findings.append({'section_id': sid, 'record_id': rid, 'code': code})

    if historical_records is not None:
        original = {r['id']: r for r in historical_records}
        current = {r['id']: r for r in records}
        for rid, row in original.items():
            if current.get(rid) != row:
                finding(None, rid, 'historical_record_changed')
    seen = set()
    for section in sections:
        sid = section.get('section_id')
        if not isinstance(sid, str):
            finding(None, None, 'section_id_invalid')
            continue
        if sid in seen:
            finding(sid, None, 'section_duplicate')
        seen.add(sid)
        body = bodies.get(sid)
        if not isinstance(body, str):
            finding(sid, None, 'body_missing')
            body = ''
        try:
            validate_section(section, body)
        except (ValueError, TypeError, AttributeError) as exc:
            finding(sid, None, 'section_invalid:' + str(exc))
        refs = section.get('source_refs', [])
        if isinstance(refs, list):
            for ref in refs:
                code = _valid_source_ref(ref, sources, chunks)
                if code:
                    finding(sid, None, 'section_' + code)
        assertions = section.get('record_assertions', [])
        if not isinstance(assertions, list):
            finding(sid, None, 'assertions_invalid')
            assertions = []
        deps = section.get('dependency_claim_ids', [])
        if not isinstance(deps, list) or not all(isinstance(i, str) for i in deps):
            finding(sid, None, 'dependencies_invalid')
            deps = []
        elif len(set(deps)) != len(deps):
            finding(sid, None, 'dependencies_duplicate')
        section_deps = section.get('dependency_section_ids', [])
        if isinstance(section_deps, list):
            for dep in section_deps:
                if not isinstance(dep, str):
                    finding(sid, None, 'section_dependency_invalid')
                    continue
                section_dependencies.append({'section_id': sid, 'dependency_section_id': dep})
                if dep not in section_ids:
                    finding(sid, None, 'section_dependency_missing')
        else:
            finding(sid, None, 'section_dependencies_invalid')
        asserted, assertion_rows, remainder = set(), [], body
        for assertion in assertions:
            if not isinstance(assertion, dict) or not all(k in assertion for k in _CORE):
                finding(sid, None, 'assertion_invalid')
                continue
            rid = assertion['id']
            if not isinstance(rid, str):
                finding(sid, None, 'assertion_id_invalid')
                continue
            if rid in asserted:
                finding(sid, rid, 'assertion_duplicate')
            asserted.add(rid)
            if rid not in deps:
                finding(sid, rid, 'assertion_dependency_missing')
            row = by_id.get(rid)
            if row is None:
                finding(sid, rid, 'assertion_record_missing')
            else:
                assertion_rows.append(row)
                for key in _CORE:
                    if _json(assertion[key]) != _json(row[key]):
                        finding(sid, rid, 'record_' + key + '_mismatch')
                all_refs = list(row['source_refs']) + [candidate['source_ref']
                    for candidate in _data(row).get('candidates', [])
                    if isinstance(candidate, dict) and 'source_ref' in candidate]
                if not all(ref in section.get('source_refs', []) for ref in all_refs):
                    finding(sid, rid, 'assertion_source_not_in_section')
                if _json(section.get('scope')) != _json(row['scope']):
                    finding(sid, rid, 'section_scope_mismatch')
                row_scope = row.get('scope') if isinstance(row.get('scope'), dict) else {}
                selectors = row_scope.get('required_selectors', [])
                if any(section.get('scope', {}).get(key) != row['scope'].get(key)
                       for key in selectors):
                    finding(sid, rid, 'section_variant_mismatch')
            rendered = render_record(assertion)
            if rendered not in remainder:
                finding(sid, rid, 'rendered_assertion_missing')
            else:
                remainder = remainder.replace(rendered, '', 1)
        findings.extend(_metadata_findings(section, assertion_rows, by_id))
        for rid in deps:
            row = by_id.get(rid)
            if row is None:
                finding(sid, rid, 'dependency_missing')
                continue
            refs = deepcopy(row['source_refs'])
            candidates = deepcopy(_data(row).get('candidates', []))
            for candidate in candidates:
                ref = candidate.get('source_ref')
                if ref and ref not in refs:
                    refs.append(ref)
            dependencies.append({'section_id': sid, 'record_id': rid, 'kind': row['kind'],
                'status': row['status'], 'source_refs': refs, 'asserted': rid in asserted,
                'conflict_candidates': candidates, 'answerable': False})
        if remainder.strip():
            finding(sid, None, 'uncovered_body')
        coverage.append({'section_id': sid, 'assertions': len(asserted),
                         'context_dependencies': len(set(deps) - asserted),
                         'uncovered_body': bool(remainder.strip()), 'answerable': False})
    used = {d['record_id'] for d in dependencies}
    return {'format_version': 'section-consistency-v1', 'consistent': not findings,
            'online_eligible': False, 'fact_review': None, 'permission_review': None,
            'findings': findings, 'dependencies': dependencies, 'section_coverage': coverage,
            'section_dependencies': section_dependencies,
            'coverage': {'sections': len(sections), 'records': len(records),
                         'record_statuses': dict(Counter(r.get('status') for r in records)),
                         'assertions': sum(c['assertions'] for c in coverage),
                         'uncovered_body_sections': sum(c['uncovered_body'] for c in coverage),
                         'unreferenced_record_ids': sorted(set(by_id) - used)},
            'input_sha256': hashlib.sha256(_json([sections, bodies, records, snapshot]).encode()).hexdigest()}
