"""Owner-directed, source-checked DAQ pilot releases without external approval.

This is an explicit alternative to signed D3 for the narrow internal pilot. It
never promotes candidate sections, links, software or an unscoped role.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import date
from pathlib import Path
import json
import re

from scripts.archive_candidate_snapshot import verify_archive

from .adjudication import digest
from . import releases
from .release_readiness import _lock, _switch


MODE = 'owner_directed_source_checked_v1'
EXCLUDED = {'software', 'links', 'module_projection', 'topology',
            'procedure', 'candidate_sections'}
_SHA = re.compile(r'^[0-9a-f]{64}$')


def _require(condition, reason):
    if not condition:
        raise ValueError('operator release rejected: ' + reason)


def _record_core(row):
    return {key: value for key, value in row.items() if key != 'answerable'}


def _inventory(sources):
    return [{key: row[key] for key in ('path', 'sha256', 'size')}
            for row in sources]


def _check_dev_batch(batch, report):
    questions = batch.get('questions')
    cases = report.get('cases') if isinstance(report, dict) else None
    _require(batch.get('environment') == 'dev' and batch.get('frozen') is True
             and isinstance(batch.get('id'), str) and batch['id'].strip()
             and isinstance(questions, list) and questions, 'dev_batch')
    question_ids = [item.get('id') for item in questions if isinstance(item, dict)]
    _require(len(question_ids) == len(questions) and all(isinstance(item, str) and item
             for item in question_ids) and len(set(question_ids)) == len(question_ids), 'dev_questions')
    _require(isinstance(report, dict) and report.get('environment') == 'dev'
             and report.get('batch_id') == batch['id']
             and report.get('review_method') in {'codex', 'human_fae'}
             and isinstance(cases, list) and len(cases) == len(questions), 'dev_report')
    _require({item.get('id') for item in cases if isinstance(item, dict)} == set(question_ids)
             and all(isinstance(item, dict) and item.get('passed') is True
                     and isinstance(item.get('answer'), str) and item['answer'].strip()
                     and isinstance(item.get('trace_id'), str) and item['trace_id'].strip()
                     and isinstance(item.get('outcome'), str) and item['outcome'].strip()
                     and item.get('fallback_used') is False for item in cases), 'dev_cases')


def _operator_evidence(bundle, report):
    snapshot, records = bundle['snapshot'], bundle['records']
    return {
        'mode': MODE, 'authorization_ref': 'project-owner-direction-2026-10-12',
        'archive_manifest_sha256': snapshot['archive_manifest_sha256'],
        'source_inventory_sha256': digest(_inventory(snapshot['sources'])),
        'records_sha256': digest(sorted((_record_core(row) for row in records),
                                        key=lambda row: row['id'])),
        'scope': deepcopy(bundle['scope']), 'runtime': deepcopy(bundle['runtime']),
        'field_aliases': deepcopy(bundle.get('field_aliases', {})),
        'scoped_field_aliases': deepcopy(bundle.get('scoped_field_aliases', {})),
        'dev_batch_id': bundle['dev_batch']['id'], 'dev_report_sha256': digest(report),
        'previous_release': bundle['previous_release'],
    }


def stage_operator_release(root: Path, bundle: dict, archive: Path, dev_report: dict, *,
                           runtime_release: str, upstream_sha: str) -> str:
    """Stage an immutable narrow release after checking actual local originals."""
    b = deepcopy(bundle)
    _require(isinstance(b, dict) and all(key in b for key in
             ('snapshot', 'records', 'sections', 'bodies', 'scope', 'runtime',
              'dev_batch', 'previous_release')), 'bundle_schema')
    snapshot = b['snapshot']
    summary = verify_archive(archive, snapshot['archive_manifest_sha256'])
    original = json.loads((archive / 'manifest.json').read_text(encoding='utf-8'))
    _require(summary['files'] == len(snapshot['sources'])
             and snapshot.get('source_date') == original.get('source_date')
             and sorted(_inventory(snapshot['sources']), key=lambda item: item['path'])
             == sorted(_inventory(original['files']), key=lambda item: item['path']),
             'archive_inventory')
    _require(b['sections'] == [] and b['bodies'] == {} and isinstance(b['records'], list)
             and b['records'], 'narrow_content')
    _require(all(row.get('kind') in {'entity', 'claim'} and row.get('status') == 'verified'
                 and row.get('access_review', {}).get('view_roles') == ['internal_fae']
                 and row.get('access_review', {}).get('forward_roles') in ([], ['internal_fae'])
                 for row in b['records']), 'internal_scope')
    fields = {row['data']['field'] for row in b['records'] if row['kind'] == 'claim'}
    aliases = b.get('field_aliases', {})
    scoped = b.get('scoped_field_aliases', {})
    _require(isinstance(aliases, dict) and all(
        field in fields and isinstance(values, list) and all(
            isinstance(value, str) and value.strip() for value in values)
        for field, values in aliases.items()), 'field_aliases')
    entity_ids = {row['id'] for row in b['records'] if row['kind'] == 'entity'}
    _require(isinstance(scoped, dict) and all(
        entity in entity_ids and isinstance(mapping, dict) and all(
            isinstance(alias, str) and alias.strip() and field in fields
            for alias, field in mapping.items())
        for entity, mapping in scoped.items()), 'scoped_field_aliases')
    scope = b['scope']
    _require(set(scope.get('excluded_topics', [])) >= EXCLUDED
             and sorted(scope.get('answerable_ids', [])) == sorted(row['id'] for row in b['records'])
             and len(set(scope['answerable_ids'])) == len(b['records']), 'answerable_scope')
    runtime = b['runtime']
    _require(runtime.get('release') == runtime_release
             and runtime.get('upstream_sha') == upstream_sha
             and runtime.get('tasks_enabled') is False
             and runtime.get('generic_trace_enabled') is False,
             'runtime_identity')
    _check_dev_batch(b['dev_batch'], dev_report)
    active = releases.read_active_release(root)
    _require(b['previous_release'] == (active['release_id'] if active else None),
             'previous_release')
    review = {'reviewer': 'operator-source-check', 'reviewed_at': date.today().isoformat(),
              'dev_batch': b['dev_batch']['id'],
              'operator_release': _operator_evidence(b, dev_report)}
    return releases._publish_release(root, snapshot, b['records'], b['previous_release'],
                                     review, sections=[], bodies={})


def verify_operator_manifest(manifest: dict, *, runtime_release: str, upstream_sha: str) -> None:
    """Recheck immutable scope and build identity at activation and startup."""
    evidence = manifest.get('review', {}).get('operator_release', {})
    _require(evidence.get('mode') == MODE
             and evidence.get('authorization_ref') == 'project-owner-direction-2026-10-12'
             and evidence.get('archive_manifest_sha256') == manifest.get('archive_manifest_sha256')
             and evidence.get('source_inventory_sha256') == digest(_inventory(manifest['sources']))
             and evidence.get('records_sha256') == digest(sorted(
                 (_record_core(row) for row in manifest['records']), key=lambda row: row['id']))
             and evidence.get('previous_release') == manifest.get('previous_release')
             and isinstance(evidence.get('dev_report_sha256'), str)
             and _SHA.fullmatch(evidence['dev_report_sha256']) is not None,
             'manifest_binding')
    scope, runtime = evidence['scope'], evidence['runtime']
    _require(isinstance(evidence.get('field_aliases'), dict)
             and isinstance(evidence.get('scoped_field_aliases'), dict), 'manifest_aliases')
    _require(manifest.get('sections') == []
             and set(scope.get('excluded_topics', [])) >= EXCLUDED
             and sorted(scope.get('answerable_ids', [])) == sorted(
                 row['id'] for row in manifest['records'] if row['answerable'])
             and all(row['kind'] in {'entity', 'claim'} and row['status'] == 'verified'
                     and row['access_review']['view_roles'] == ['internal_fae']
                     and row['access_review']['forward_roles'] in ([], ['internal_fae'])
                     for row in manifest['records']), 'manifest_scope')
    _require(runtime.get('release') == runtime_release
             and runtime.get('upstream_sha') == upstream_sha
             and runtime.get('tasks_enabled') is False
             and runtime.get('generic_trace_enabled') is False,
             'runtime_identity')


def _checked_operator(root: Path, release_id: str, *, runtime_release: str,
                      upstream_sha: str) -> dict:
    manifest, _ = releases._read_release(root, release_id)
    verify_operator_manifest(manifest, runtime_release=runtime_release,
                             upstream_sha=upstream_sha)
    return manifest


def activate_operator_release(root: Path, release_id: str, *, observe) -> dict:
    _require(callable(observe), 'dev_observer_required')
    with _lock(root):
        manifest, _ = releases._read_release(root, release_id)
        runtime = manifest['review']['operator_release']['runtime']
        manifest = _checked_operator(root, release_id,
                                     runtime_release=runtime['release'],
                                     upstream_sha=runtime['upstream_sha'])
        active = releases.read_active_release(root)
        _require(manifest['previous_release'] == (active['release_id'] if active else None),
                 'previous_release')
        return _switch(root, release_id, manifest, active, observe)


def rollback_operator_release(root: Path, *, observe) -> dict:
    _require(callable(observe), 'dev_observer_required')
    with _lock(root):
        active = releases.read_active_release(root)
        _require(active is not None and active['manifest'].get('previous_release'),
                 'previous_release_unavailable')
        current = active['manifest']
        runtime = current['review']['operator_release']['runtime']
        _checked_operator(root, active['release_id'],
                          runtime_release=runtime['release'],
                          upstream_sha=runtime['upstream_sha'])
        target_id = current['previous_release']
        target = _checked_operator(root, target_id,
                                   runtime_release=runtime['release'],
                                   upstream_sha=runtime['upstream_sha'])
        return _switch(root, target_id, target, active, observe)
