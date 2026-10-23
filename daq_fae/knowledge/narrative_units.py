"""Exact narrative spans and independently reviewed source evidence.

This contract checks immutable evidence and review bindings. Natural-language
entailment remains an explicit independent review decision, never a keyword or
numeric-token heuristic. Unreviewed prose cannot acquire consistency by checksum.
"""
from __future__ import annotations

import hashlib
import json
import re

from .records import ROLES, _review_valid, access_fingerprint, record_fingerprint

CORE = ('id', 'kind', 'status', 'scope', 'source_refs', 'data')


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def unit_fingerprint(unit, body, records):
    """Bind exact body slice, all unit metadata and record/access dependencies."""
    core = {k: v for k, v in unit.items() if k not in {'fact_review', 'permission_review'}}
    deps = {r['id']: {'record': record_fingerprint(r), 'access': access_fingerprint(r)}
            for r in records if r['id'] in unit['dependency_record_ids']}
    return _sha(_json([core, body[unit['start']:unit['end']], deps]))


def _without_source_refs(value):
    if isinstance(value, dict):
        return {k: _without_source_refs(v) for k, v in value.items() if k != 'source_refs'}
    if isinstance(value, list):
        return [_without_source_refs(v) for v in value]
    return value


def render_narrative_record(row):
    """Lossless fact fields in stable label/value lines; provenance stays metadata.

    Only source_refs are omitted from prose, including nested procedure sources.
    Exact full data and refs remain in the independently compared assertion.
    No value, qualifier, scope, step, checkpoint or failure branch is omitted.
    """
    return '\n'.join(['scope: ' + _json(row['scope'])] + [
        key + ': ' + (value if key == 'value' and isinstance(value, str)
                     and row['data'].get('field') == 'source_text'
                     and row['data'].get('evidence_scope') == 'document_statement_only'
                     else _json(_without_source_refs(value)))
        for key, value in sorted(row['data'].items()) if key != 'source_refs'])


def render_narrative_unit(unit):
    """Render exact facts as paragraphs, a table row, or one selected procedure step."""
    assertions = unit['record_assertions']
    if unit['unit_type'] == 'step':
        _require(len(assertions) == 1 and assertions[0]['kind'] == 'procedure', 'step_record')
        row = assertions[0]
        index = unit.get('step_index')
        steps = row['data']['steps']
        _require(type(index) is int and 0 <= index < len(steps), 'step_index')
        row = {**row, 'data': {**row['data'], 'steps': [steps[index]]}}
        return str(index + 1) + '. ' + render_narrative_record(row).replace('\n', '; ')
    _require('step_index' not in unit, 'unexpected_step_index')
    text = '\n\n'.join(render_narrative_record(row) for row in assertions)
    if unit['unit_type'] == 'table_row':
        # JSON contains escaped rather than literal newlines; escape Markdown pipes.
        return '| ' + ' | '.join(text.replace('|', '\\|').splitlines()) + ' |'
    return text


def narrative_source_keys(sections):
    """Exact snapshot chunk identities needed for immutable load verification."""
    return {_chunk_key(e['source_ref']) for section in sections
            for unit in section.get('narrative_units', [])
            for e in unit.get('source_evidence', [])}


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _ids(values, *, nonempty=False):
    return (isinstance(values, list) and (bool(values) or not nonempty)
            and all(isinstance(v, str) and bool(re.fullmatch(r'[a-z][a-z0-9_.:-]*', v)) for v in values)
            and len(values) == len(set(values)))


def _span(start, end, text):
    return type(start) is int and type(end) is int and 0 <= start < end <= len(text)


def _chunk_key(ref):
    return (ref['path'], ref['sha256'], _json(ref['locator']))


def _validate_unit(unit, section, body, records, snapshot):
    _require(isinstance(unit, dict), 'schema')
    _require(_ids([unit.get('unit_id')]), 'identity')
    _require(unit.get('unit_type') in {'paragraph', 'table_row', 'step'}, 'type')
    _require(unit.get('mode') in {'source_excerpt', 'reviewed_narrative', 'record_render'}, 'mode')
    _require(unit.get('knowledge_type') in {'explanation', 'claim', 'procedure', 'topology', 'entity', 'software'}, 'knowledge_type')
    _require(_span(unit.get('start'), unit.get('end'), body), 'span')
    text = body[unit['start']:unit['end']]
    _require(text.strip() and unit.get('body_sha256') == _sha(text), 'body_hash')
    _require(isinstance(unit.get('scope'), dict) and unit['scope']
             and unit['scope'] == section.get('scope'), 'scope')
    _require(_ids(unit.get('entity_ids'), nonempty=True), 'entities')
    _require('topology_id' in unit and (unit['topology_id'] is None or _ids([unit['topology_id']])), 'topology')
    _require(isinstance(unit.get('conditions'), dict), 'conditions')
    _require(isinstance(unit.get('authored_by'), str) and unit['authored_by'].strip(), 'author')
    for field in ('entity_ids', 'topology_id'):
        if field in section:
            _require(unit[field] == section[field], 'section_' + field)
    for key in ('view_roles', 'forward_roles'):
        _require(_ids(unit.get(key)) and not set(unit[key]) - ROLES, 'roles')
    _require(unit['view_roles'] and set(unit['forward_roles']) <= set(unit['view_roles']), 'roles')
    assertions = unit.get('record_assertions')
    deps = unit.get('dependency_record_ids')
    _require(_ids(deps) and isinstance(assertions, list), 'assertions')
    by_id = {r['id']: r for r in records}
    asserted_rows = []
    if unit['mode'] == 'record_render':
        _require(unit['knowledge_type'] != 'explanation' and assertions, 'knowledge_type')
        _require(all(isinstance(a, dict) and set(a) == set(CORE) for a in assertions), 'assertion_schema')
        _require(_ids([a['id'] for a in assertions], nonempty=True)
                 and set(deps) == {a['id'] for a in assertions}, 'assertion_dependencies')
        _require(set(deps) <= set(section.get('dependency_claim_ids', [])), 'section_dependencies')
        for assertion in assertions:
            row = by_id.get(assertion['id'])
            _require(row is not None and row['status'] == 'verified', 'record_not_verified')
            _require(_json(assertion) == _json({k: row[k] for k in CORE}), 'record_mismatch')
            _require(row['scope'] == unit['scope'], 'record_scope')
            _require(all(ref in section.get('source_refs', []) for ref in row['source_refs']), 'record_source')
            asserted_rows.append(row)
        _require(any(r['kind'] == unit['knowledge_type'] for r in asserted_rows), 'record_kind')
        _require(text == render_narrative_unit(unit), 'render_mismatch')
        _require(unit['conditions'] == {r['id']: r['data'].get('conditions', {}) for r in asserted_rows}, 'record_conditions')
        # Reuse the existing union-based entity/topology metadata contract.
        from .section_consistency import _metadata_findings
        metadata = {'section_id': section['section_id'], 'entity_ids': unit['entity_ids']}
        if unit['topology_id'] is not None:
            metadata['topology_id'] = unit['topology_id']
        _require(not _metadata_findings(metadata, asserted_rows, by_id), 'record_identity')
        if any(r['kind'] in {'procedure', 'topology'} for r in asserted_rows):
            _require(unit['topology_id'] is not None, 'record_topology')
    else:
        _require(unit['knowledge_type'] == 'explanation' and deps == [] and assertions == []
                 and unit['unit_type'] != 'step' and 'step_index' not in unit, 'explanation_assertions')
        if 'conditions' in section:
            _require(unit['conditions'] == section['conditions'], 'section_conditions')

    evidence = unit.get('source_evidence')
    _require(isinstance(evidence, list) and evidence, 'evidence_missing')
    _require(all(isinstance(e, dict) for e in evidence), 'evidence_schema')
    _require(_ids([e.get('evidence_id') for e in evidence]), 'evidence_identity')
    sources = {s['path']: s for s in snapshot.get('sources', [])}
    excerpts = []
    for e in evidence:
        ref = e.get('source_ref')
        _require(isinstance(ref, dict) and ref in section.get('source_refs', []), 'source_not_in_section')
        source = sources.get(ref.get('path'))
        _require(source is not None and source['sha256'] == ref.get('sha256'), 'source_hash')
        matches = [c for c in snapshot.get('chunks', [])
                   if (c.get('source_path'), c.get('source_sha256'), _json(c.get('locator'))) == _chunk_key(ref)]
        _require(len(matches) == 1, 'source_chunk_missing_or_ambiguous')
        chunk = matches[0]
        original = chunk.get('text')
        _require(isinstance(original, str) and original.strip(), 'source_text_missing')
        _require(chunk.get('text_sha256') == _sha(original) == e.get('chunk_sha256'), 'chunk_hash')
        _require(_span(e.get('start'), e.get('end'), original), 'source_span')
        excerpt = original[e['start']:e['end']]
        _require(excerpt.strip() and e.get('excerpt') == excerpt and e.get('excerpt_sha256') == _sha(excerpt), 'source_excerpt')
        excerpts.append(excerpt)
    for row in asserted_rows:
        _require(all(ref in [e['source_ref'] for e in evidence] for ref in row['source_refs']), 'record_evidence')
    if unit['mode'] == 'source_excerpt':
        _require(text == '\n\n'.join(excerpts), 'excerpt_body_mismatch')

    digest = unit_fingerprint(unit, body, records)
    for field in ('fact_review', 'permission_review'):
        review = unit.get(field)
        _require(_review_valid(review) and review.get('decision') == 'approved'
                 and review.get('unit_sha256') == digest, field + '_missing_or_stale')
    fact = unit['fact_review']
    _require(fact['reviewer'].strip().casefold() != unit['authored_by'].strip().casefold(), 'independent_review')
    _require(fact.get('semantic_checks') == {
        'complete': True, 'scope_preserved': True, 'conditions_preserved': True, 'no_added_claims': True}, 'semantic_review')
    assessments = fact.get('evidence_assessments')
    _require(isinstance(assessments, list) and all(isinstance(a, dict) for a in assessments), 'evidence_assessments')
    _require(len(assessments) == len(evidence)
             and {a.get('evidence_id') for a in assessments} == {e['evidence_id'] for e in evidence}
             and all(isinstance(a.get('assessment'), str) and a['assessment'].strip() for a in assessments), 'evidence_assessments')
    rationale = unit['permission_review'].get('rationale')
    _require(isinstance(rationale, str) and rationale.strip(), 'permission_rationale')
    return unit['start'], unit['end'], asserted_rows


def audit_narrative_units(section, body, records, snapshot):
    """Return validated spans, exact asserted rows and structured fatal findings."""
    units = section.get('narrative_units', [])
    findings, spans, rows, seen = [], [], [], set()
    roles = None
    if not isinstance(units, list):
        return [], [], [{'section_id': section['section_id'], 'record_id': None,
                         'code': 'narrative_units_invalid'}]
    for unit in units:
        uid = unit.get('unit_id') if isinstance(unit, dict) else None
        try:
            start, end, asserted = _validate_unit(unit, section, body, records, snapshot)
            unit_roles = (unit['view_roles'], unit['forward_roles'])
            _require(roles is None or roles == unit_roles, 'mixed_permissions')
            roles = unit_roles
            _require(uid not in seen, 'duplicate')
            _require(not any(start < other_end and end > other_start for other_start, other_end in spans), 'overlap')
            seen.add(uid)
            spans.append((start, end))
            rows.extend(asserted)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            findings.append({'section_id': section['section_id'], 'record_id': None,
                             'unit_id': uid, 'code': 'narrative_' + str(exc)})
    return spans, rows, findings


def validate_unit_roles(section):
    """One section is one visibility and applicability boundary; split otherwise."""
    for unit in section.get('narrative_units', []):
        if not isinstance(unit, dict):
            raise ValueError('narrative unit schema')
        for key in ('view_roles', 'forward_roles'):
            if not isinstance(unit.get(key), list) or unit[key] != section[key]:
                raise ValueError('section mixed narrative permissions; split sections')
