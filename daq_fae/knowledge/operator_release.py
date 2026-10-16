"""Owner-directed, source-checked DAQ pilot releases without external approval.

This is an explicit alternative to signed D3 for the narrow internal pilot. It
never promotes candidate sections, links, software or an unscoped role.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from copy import deepcopy
from datetime import date
from pathlib import Path

from scripts.archive_candidate_snapshot import verify_archive

from . import releases
from .adjudication import digest
from .release_readiness import _lock, _switch

MODE = 'owner_directed_source_checked_v1'
MODE_PRO_V2 = 'owner_directed_source_checked_v2'
PRO_BASELINE_RELEASE = 'ee9083f9891630f6727766c276e6dc123ab0b6b79641dada133159871eeaacdb'
PRO_BASELINE_COUNT = 25
PRO_RECORD_FINGERPRINTS = {
    'topology:ego-pro-standalone': 'a83ad704af0da3877648f40d2f41df781c4aa3d402ee86ae0235f39a5da61bee',
    'procedure:ego-pro-standalone': '95ae04f96d4a248c3ce2d22366095aa4eae138e3be44d43b167acc85908eee80',
}
PRO_SOURCE_REF = {
    'path': '中文-EGO Pro 四目快速使用说明文档/EGO Pro 四目快速使用说明文档.md',
    'sha256': 'ded0378e7f5560760cd3bfbb833a64536776e9ad59682a98547c28dfecb55c7a',
    'locator': {'kind': 'lines', 'start': 54, 'end': 90},
}
PRO_VETO_ENTITIES = [
    {'id': 'entity:wristcam', 'name': 'WristCam', 'aliases': ['腕部相机']},
    {'id': 'entity:external-hub', 'name': 'HUB', 'aliases': ['集线器']},
]
TASK_BINDING_CONTRACT = 'daq-task-role-release-v1'
TASK_HTTP_CONTRACT = 'orbbec-http-task/v1'
EXCLUDED = {'software', 'links', 'module_projection', 'topology',
            'procedure', 'candidate_sections'}
_SHA = re.compile(r'^[0-9a-f]{64}$')


def _prompt_sha256() -> str:
    return hashlib.sha256((Path(__file__).resolve().parents[2] /
                           'prompts/reviewed_knowledge_system.md').read_bytes()).hexdigest()


def _schema_prompt_sha256() -> str:
    return hashlib.sha256((Path(__file__).resolve().parents[2] /
                           'prompts/daq_schema_extraction.md').read_bytes()).hexdigest()


def _code_sha256() -> str:
    from .code_identity import daq_code_sha
    return daq_code_sha()


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


def _task_release_input(bundle):
    return digest({key: bundle[key] for key in ('snapshot', 'records', 'scope', 'runtime')})


def _check_task_dev_batch(bundle, report):
    batch = bundle.get('task_dev_batch')
    _require(isinstance(batch, dict) and batch.get('environment') == 'dev'
             and batch.get('frozen') is True and isinstance(batch.get('id'), str)
             and bool(batch['id'].strip()) and batch.get('contract') == TASK_HTTP_CONTRACT
             and batch.get('release_input_sha256') == _task_release_input(bundle),
             'task_dev_batch')
    questions = batch.get('questions')
    required = {'answerable', 'missing_evidence', 'role_revoked', 'release_changed'}
    _require(isinstance(questions, list) and len(questions) == len(required)
             and all(isinstance(item, dict) and isinstance(item.get('id'), str)
                     and item['id'].strip() and isinstance(item.get('question'), str)
                     and item['question'].strip() for item in questions)
             and {item.get('kind') for item in questions} == required
             and len({item['id'] for item in questions}) == len(questions),
             'task_dev_questions')
    _require(isinstance(report, dict) and report.get('environment') == 'dev'
             and report.get('batch_id') == batch['id']
             and report.get('contract') == TASK_HTTP_CONTRACT
             and report.get('release_input_sha256') == batch['release_input_sha256']
             and report.get('review_method') in {'codex', 'human_fae'},
             'task_dev_report')
    cases = report.get('cases')
    _require(isinstance(cases, list) and len(cases) == len(questions)
             and {item.get('id') for item in cases if isinstance(item, dict)}
             == {item['id'] for item in questions}, 'task_dev_cases')
    kinds = {item['id']: item['kind'] for item in questions}
    expected_outcomes = {'answerable': 'resolved', 'missing_evidence': 'safe_abstained',
                         'role_revoked': 'scope_denied', 'release_changed': 'scope_denied'}
    for case in cases:
        kind = kinds[case['id']]
        try:
            from uuid import UUID
            UUID(case['platform_task_id'])
            UUID(case['request_id'])
        except (KeyError, TypeError, ValueError):
            raise ValueError('operator release rejected: task_dev_cases') from None
        _require(case.get('passed') is True and case.get('outcome') == expected_outcomes[kind]
                 and case.get('fallback_used') is False and isinstance(case.get('sources'), list),
                 'task_dev_cases')
        if kind == 'answerable':
            _require(case.get('http_status') == 200 and isinstance(case.get('answer'), str)
                     and bool(case['answer'].strip()) and bool(case['sources'])
                     and isinstance(case.get('trace_id'), str) and bool(case['trace_id'].strip()),
                     'task_dev_cases')
        elif kind == 'missing_evidence':
            _require(case.get('http_status') == 200 and case['sources'] == []
                     and isinstance(case.get('trace_id'), str) and bool(case['trace_id'].strip()),
                     'task_dev_cases')
        else:
            _require(case.get('http_status') == 403 and case['sources'] == []
                     and not case.get('answer'), 'task_dev_cases')


def _operator_evidence(bundle, report, task_dev_report=None):
    snapshot, records = bundle['snapshot'], bundle['records']
    evidence = {
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
    if bundle['runtime'].get('tasks_enabled') is True:
        evidence.update(task_binding_contract=TASK_BINDING_CONTRACT,
                        task_dev_batch_id=bundle['task_dev_batch']['id'],
                        task_dev_batch_sha256=digest(bundle['task_dev_batch']),
                        task_dev_report_sha256=digest(task_dev_report),
                        task_release_input_sha256=_task_release_input(bundle))
    return evidence


def stage_operator_release(root: Path, bundle: dict, archive: Path, dev_report: dict, *,
                           runtime_release: str, upstream_sha: str,
                           task_dev_report: dict | None = None) -> str:
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
             and type(runtime.get('tasks_enabled')) is bool
             and runtime.get('generic_trace_enabled') is False,
             'runtime_identity')
    if runtime['tasks_enabled']:
        _require(runtime.get('task_binding_contract') == TASK_BINDING_CONTRACT,
                 'task_binding_contract')
        _check_task_dev_batch(b, task_dev_report)
    _check_dev_batch(b['dev_batch'], dev_report)
    active = releases.read_active_release(root)
    _require(b['previous_release'] == (active['release_id'] if active else None),
             'previous_release')
    review = {'reviewer': 'operator-source-check', 'reviewed_at': date.today().isoformat(),
              'dev_batch': b['dev_batch']['id'],
              'operator_release': _operator_evidence(b, dev_report, task_dev_report)}
    return releases._publish_release(root, snapshot, b['records'], b['previous_release'],
                                     review, sections=[], bodies={})


def _pro_dev_evidence(bundle: dict, semantic_review: dict) -> dict:
    """Bind the exact three-question Dev run and a separate semantic verdict."""
    identity = bundle.get('replay_identity')
    ledger = bundle.get('source_check')
    _require(isinstance(identity, dict) and isinstance(ledger, dict), 'dev_identity')
    _require(identity.get('mode') == 'loopback_dev_candidate_only'
             and identity.get('environment') == 'development'
             and identity.get('code_unchanged_during_replay') is True
             and identity.get('daq_code_sha256') == _code_sha256()
             and identity.get('daq_code_sha256_after') == _code_sha256()
             and identity.get('prompt_sha256') == _prompt_sha256()
             and identity.get('schema_prompt_sha256') == _schema_prompt_sha256()
             and identity.get('graph_sha256') == ledger.get('graph_sha256'), 'dev_identity')
    expected = {
        'pro_single_topology': ('resolved', 'full', 'topology:ego-pro-standalone'),
        'pro_single_procedure': ('resolved', 'full', 'procedure:ego-pro-standalone'),
        'ego_resolution_conflict_negative': ('safe_abstained', 'partial', None),
    }
    cases = identity.get('cases')
    reviews = semantic_review.get('cases') if isinstance(semantic_review, dict) else None
    files = bundle.get('bound_input_files', {})
    identity_path = files.get('replay_identity') if isinstance(files, dict) else None
    raw_identity_sha = (hashlib.sha256(Path(identity_path).read_bytes()).hexdigest()
                        if isinstance(identity_path, str) and Path(identity_path).is_file()
                        and not Path(identity_path).is_symlink()
                        else None)
    _require(isinstance(cases, list) and len(cases) == len(expected)
             and isinstance(reviews, list) and len(reviews) == len(expected)
             and {case.get('id') for case in cases if isinstance(case, dict)} == set(expected)
             and {case.get('case_id') for case in reviews if isinstance(case, dict)} == set(expected)
             and semantic_review.get('all_passed') is True
             and semantic_review.get('review_method') == 'independent_codex_semantic_review'
             and semantic_review.get('environment') == 'loopback_dev'
             and semantic_review.get('code_sha256') == _code_sha256()
             and semantic_review.get('identity_sha256') in {
                 digest(identity), raw_identity_sha}, 'dev_cases')
    by_id = {case['id']: case for case in cases}
    by_review = {case['case_id']: case for case in reviews}
    for case_id, (outcome, coverage, source_id) in expected.items():
        case, reviewed = by_id[case_id], by_review[case_id]
        _require(case.get('http_status') == 200 and case.get('outcome') == outcome
                 and case.get('coverage_status') == coverage
                 and isinstance(case.get('trace_id'), str) and case['trace_id']
                 and reviewed.get('trace_id') == case['trace_id']
                 and reviewed.get('http_status') == 200
                 and reviewed.get('outcome') == outcome
                 and reviewed.get('coverage_status') == coverage
                 and reviewed.get('fallback_used') is False
                 and reviewed.get('verdict') == 'pass'
                 and isinstance(reviewed.get('source_ids'), list)
                 and (source_id in reviewed['source_ids'] if source_id else
                      not set(PRO_RECORD_FINGERPRINTS) & set(reviewed['source_ids'])),
                 'dev_cases')
    return {
        'daq_code_sha256': _code_sha256(),
        'prompt_sha256': _prompt_sha256(),
        'schema_prompt_sha256': _schema_prompt_sha256(),
        'source_check_sha256': digest(ledger),
        'replay_identity_sha256': digest(identity),
        'semantic_review_sha256': digest(semantic_review),
        'dev_trace_ids': {case_id: by_id[case_id]['trace_id'] for case_id in expected},
    }


def _bound_pro_input_hashes(bundle: dict, semantic_review: dict) -> dict:
    files = bundle.get('bound_input_files')
    _require(isinstance(files, dict) and set(files) == {
        'source_check', 'replay_identity', 'semantic_review'}, 'bound_inputs')
    expected = {'source_check': bundle['source_check'],
                'replay_identity': bundle['replay_identity'],
                'semantic_review': semantic_review}
    hashes = {}
    for name, path_text in files.items():
        _require(isinstance(path_text, str) and path_text, 'bound_inputs')
        path = Path(path_text)
        _require(path.is_file() and not path.is_symlink(), 'bound_inputs')
        raw = path.read_bytes()
        try:
            parsed = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError('operator release rejected: bound_inputs') from None
        _require(parsed == expected[name], 'bound_inputs')
        hashes[name + '_file_sha256'] = hashlib.sha256(raw).hexdigest()
    return hashes


def stage_operator_pro_release(root: Path, bundle: dict, archive: Path,
                               semantic_review: dict, *, runtime_release: str,
                               upstream_sha: str) -> str:
    """Stage exactly two Pro standalone facts on the known 25-record production base."""
    from .records import record_fingerprint
    b = deepcopy(bundle)
    _require(isinstance(b, dict) and all(key in b for key in
             ('snapshot', 'records', 'sections', 'bodies', 'scope', 'runtime',
              'previous_release', 'source_check', 'replay_identity')), 'bundle_schema')
    _require(isinstance(b['previous_release'], str)
             and b['previous_release'] != PRO_BASELINE_RELEASE, 'production_baseline')
    active = releases.read_active_release(root)
    _require(active is not None and active['release_id'] == b['previous_release'],
             'production_baseline')
    previous, _ = releases._read_release(root, b['previous_release'])
    origin, _ = releases._read_release(root, PRO_BASELINE_RELEASE)
    _require(len(previous['records']) == len(origin['records']) == PRO_BASELINE_COUNT
             and previous.get('review', {}).get('operator_release', {}).get('mode') == MODE
             and previous.get('previous_release') == PRO_BASELINE_RELEASE
             and previous['archive_manifest_sha256'] == origin['archive_manifest_sha256']
             and _inventory(previous['sources']) == _inventory(origin['sources'])
             and all(_record_core(a) == _record_core(b) for a, b in
                     zip(sorted(previous['records'], key=lambda row: row['id']),
                         sorted(origin['records'], key=lambda row: row['id']))),
             'production_baseline')
    verify_operator_manifest(origin,
                             runtime_release=origin['review']['operator_release']['runtime']['release'],
                             upstream_sha=upstream_sha)
    verify_operator_manifest(previous, runtime_release=runtime_release,
                             upstream_sha=upstream_sha)
    snapshot = b['snapshot']
    summary = verify_archive(archive, snapshot['archive_manifest_sha256'])
    original = json.loads((archive / 'manifest.json').read_text(encoding='utf-8'))
    _require(summary['files'] == len(snapshot['sources'])
             and snapshot.get('source_date') == original.get('source_date')
             and sorted(_inventory(snapshot['sources']), key=lambda x: x['path'])
             == sorted(_inventory(original['files']), key=lambda x: x['path'])
             and snapshot['archive_manifest_sha256'] == previous['archive_manifest_sha256']
             and _inventory(snapshot['sources']) == _inventory(previous['sources']),
             'archive_inventory')
    _require(b['sections'] == [] and b['bodies'] == {} and isinstance(b['records'], list),
             'narrow_content')
    by_id = {row.get('id'): row for row in b['records'] if isinstance(row, dict)}
    previous_by_id = {row['id']: row for row in previous['records']}
    _require(len(by_id) == len(b['records']) == PRO_BASELINE_COUNT + 2
             and set(by_id) - set(previous_by_id) == set(PRO_RECORD_FINGERPRINTS)
             and all(_record_core(by_id[rid]) == _record_core(row)
                     for rid, row in previous_by_id.items()), 'pro_delta')
    new_rows = {rid: by_id[rid] for rid in PRO_RECORD_FINGERPRINTS}
    _require(all(row.get('source_refs') == [PRO_SOURCE_REF] for row in new_rows.values())
             and any(item.get('path') == PRO_SOURCE_REF['path'] and
                     item.get('sha256') == PRO_SOURCE_REF['sha256']
                     for item in snapshot['sources']), 'pro_source')
    _require(all(row.get('status') == 'verified' and
                 row.get('kind') == rid.split(':', 1)[0] and
                 record_fingerprint(row) == PRO_RECORD_FINGERPRINTS[rid]
                 for rid, row in new_rows.items()), 'pro_record')
    _require(all(row.get('access_review', {}).get('view_roles') == ['internal_fae']
                 and row.get('access_review', {}).get('forward_roles') == ['internal_fae']
                 for row in new_rows.values()), 'internal_scope')
    ledger = b['source_check']
    checked_rows = ledger.get('records') if isinstance(ledger, dict) else None
    _require(ledger.get('format_version') == 'daq-dev-source-check/v1'
             and ledger.get('scope') == 'loopback_dev_internal_fae_only'
             and set(ledger.get('new_record_ids', [])) == set(PRO_RECORD_FINGERPRINTS)
             and isinstance(checked_rows, dict)
             and all(checked_rows.get(rid) == PRO_RECORD_FINGERPRINTS[rid]
                     for rid in PRO_RECORD_FINGERPRINTS), 'source_check')
    prev_evidence = previous['review']['operator_release']
    scope = b['scope']
    _require(sorted(scope.get('answerable_ids', [])) == sorted(by_id)
             and len(scope['answerable_ids']) == len(by_id)
             and set(scope.get('excluded_topics', [])) >= EXCLUDED - {'topology', 'procedure'}
             and not set(scope['excluded_topics']) & {'topology', 'procedure'}
             and b.get('field_aliases') == prev_evidence['field_aliases']
             and b.get('scoped_field_aliases') == prev_evidence['scoped_field_aliases'],
             'answerable_scope')
    runtime = b['runtime']
    old_runtime = prev_evidence['runtime']
    _require(runtime.get('release') == runtime_release
             and runtime_release == old_runtime['release']
             and runtime.get('upstream_sha') == upstream_sha
             and runtime.get('tasks_enabled') == old_runtime['tasks_enabled']
             and runtime.get('generic_trace_enabled') is False, 'runtime_identity')
    dev_evidence = _pro_dev_evidence(b, semantic_review)
    dev_evidence.update(_bound_pro_input_hashes(b, semantic_review))
    evidence = {
        'mode': MODE_PRO_V2, 'authorization_ref': 'project-owner-direction-2026-10-16',
        'previous_release': b['previous_release'],
        'origin_release': PRO_BASELINE_RELEASE,
        'baseline_records_sha256': digest(sorted((_record_core(row) for row in previous['records']),
                                                 key=lambda row: row['id'])),
        'archive_manifest_sha256': snapshot['archive_manifest_sha256'],
        'source_inventory_sha256': digest(_inventory(snapshot['sources'])),
        'records_sha256': digest(sorted((_record_core(row) for row in b['records']),
                                        key=lambda row: row['id'])),
        'scope': deepcopy(scope), 'runtime': deepcopy(runtime),
        'field_aliases': deepcopy(b['field_aliases']),
        'scoped_field_aliases': deepcopy(b['scoped_field_aliases']),
        'pro_record_fingerprints': deepcopy(PRO_RECORD_FINGERPRINTS),
        'pro_source_ref': deepcopy(PRO_SOURCE_REF),
        'veto_entities': deepcopy(PRO_VETO_ENTITIES),
        **dev_evidence,
    }
    review = {'reviewer': 'operator-source-check', 'reviewed_at': date.today().isoformat(),
              'dev_batch': 'pro-standalone-v2', 'operator_release': evidence}
    return releases._publish_release(root, snapshot, b['records'], b['previous_release'],
                                     review, sections=[], bodies={})


def verify_operator_manifest(manifest: dict, *, runtime_release: str, upstream_sha: str) -> None:
    """Recheck immutable scope and build identity at activation and startup."""
    evidence = manifest.get('review', {}).get('operator_release', {})
    if evidence.get('mode') == MODE_PRO_V2:
        _verify_pro_manifest(manifest, runtime_release=runtime_release,
                             upstream_sha=upstream_sha)
        return
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
             and type(runtime.get('tasks_enabled')) is bool
             and runtime.get('generic_trace_enabled') is False,
             'runtime_identity')
    if runtime['tasks_enabled']:
        _require(runtime.get('task_binding_contract') == TASK_BINDING_CONTRACT
                 and evidence.get('task_binding_contract') == TASK_BINDING_CONTRACT
                 and isinstance(evidence.get('task_dev_batch_id'), str)
                 and bool(evidence['task_dev_batch_id'].strip())
                 and all(isinstance(evidence.get(key), str)
                         and _SHA.fullmatch(evidence[key]) is not None
                         for key in ('task_dev_batch_sha256', 'task_dev_report_sha256',
                                     'task_release_input_sha256')),
                 'task_dev_evidence')


def _verify_pro_manifest(manifest: dict, *, runtime_release: str,
                         upstream_sha: str) -> None:
    from .records import record_fingerprint
    evidence = manifest['review']['operator_release']
    rows = manifest['records']
    by_id = {row['id']: row for row in rows}
    scope = evidence.get('scope', {})
    runtime = evidence.get('runtime', {})
    _require(manifest.get('sections') == [] and len(rows) == PRO_BASELINE_COUNT + 2
             and len(by_id) == len(rows)
             and evidence.get('authorization_ref') == 'project-owner-direction-2026-10-16'
             and isinstance(manifest.get('previous_release'), str)
             and manifest.get('previous_release') != PRO_BASELINE_RELEASE
             and evidence.get('previous_release') == manifest.get('previous_release')
             and evidence.get('origin_release') == PRO_BASELINE_RELEASE
             and isinstance(evidence.get('baseline_records_sha256'), str)
             and _SHA.fullmatch(evidence['baseline_records_sha256'])
             and evidence.get('archive_manifest_sha256') == manifest.get('archive_manifest_sha256')
             and evidence.get('source_inventory_sha256') == digest(_inventory(manifest['sources']))
             and evidence.get('records_sha256') == digest(sorted(
                 (_record_core(row) for row in rows), key=lambda row: row['id']))
             and evidence.get('pro_record_fingerprints') == PRO_RECORD_FINGERPRINTS
             and evidence.get('pro_source_ref') == PRO_SOURCE_REF
             and evidence.get('veto_entities') == PRO_VETO_ENTITIES,
             'manifest_binding')
    _require(set(PRO_RECORD_FINGERPRINTS) <= set(by_id)
             and all(row['kind'] == rid.split(':', 1)[0]
                     and row['status'] == 'verified'
                     and row['source_refs'] == [PRO_SOURCE_REF]
                     and record_fingerprint(row) == PRO_RECORD_FINGERPRINTS[rid]
                     and row['access_review']['view_roles'] == ['internal_fae']
                     and row['access_review']['forward_roles'] == ['internal_fae']
                     for rid, row in ((rid, by_id[rid]) for rid in PRO_RECORD_FINGERPRINTS))
             and sorted(scope.get('answerable_ids', [])) == sorted(by_id)
             and set(scope.get('excluded_topics', [])) >= EXCLUDED - {'topology', 'procedure'}
             and not set(scope['excluded_topics']) & {'topology', 'procedure'},
             'manifest_scope')
    _require(runtime.get('release') == runtime_release
             and runtime.get('upstream_sha') == upstream_sha
             and runtime.get('tasks_enabled') is False
             and runtime.get('generic_trace_enabled') is False, 'runtime_identity')
    _require(evidence.get('daq_code_sha256') == _code_sha256()
             and evidence.get('prompt_sha256') == _prompt_sha256()
             and evidence.get('schema_prompt_sha256') == _schema_prompt_sha256()
             and all(isinstance(evidence.get(key), str) and _SHA.fullmatch(evidence[key])
                     for key in ('source_check_sha256', 'replay_identity_sha256',
                                 'semantic_review_sha256', 'source_check_file_sha256',
                                 'replay_identity_file_sha256',
                                 'semantic_review_file_sha256'))
             and set(evidence.get('dev_trace_ids', {})) == {
                 'pro_single_topology', 'pro_single_procedure',
                 'ego_resolution_conflict_negative'}, 'dev_identity')


def _checked_operator(root: Path, release_id: str, *, runtime_release: str,
                      upstream_sha: str) -> dict:
    manifest, _ = releases._read_release(root, release_id)
    verify_operator_manifest(manifest, runtime_release=runtime_release,
                             upstream_sha=upstream_sha)
    if manifest['review']['operator_release']['mode'] == MODE_PRO_V2:
        previous, _ = releases._read_release(root, manifest['previous_release'])
        origin, _ = releases._read_release(root, PRO_BASELINE_RELEASE)
        by_id = {row['id']: row for row in manifest['records']}
        _require(previous.get('previous_release') == PRO_BASELINE_RELEASE
                 and len(previous['records']) == len(origin['records']) == PRO_BASELINE_COUNT
                 and previous['archive_manifest_sha256'] == origin['archive_manifest_sha256']
                 and _inventory(previous['sources']) == _inventory(origin['sources'])
                 and all(_record_core(a) == _record_core(b) for a, b in
                         zip(sorted(previous['records'], key=lambda row: row['id']),
                             sorted(origin['records'], key=lambda row: row['id'])))
                 and manifest['review']['operator_release']['baseline_records_sha256'] ==
                 digest(sorted((_record_core(row) for row in previous['records']),
                               key=lambda row: row['id'])) and all(
            _record_core(by_id.get(row['id'], {})) == _record_core(row)
            for row in previous['records']), 'production_baseline')
    return manifest


def _active_sdk_notes_main_binding() -> str | None:
    """A notes-bearing SDK pointer must move with its bound main release."""
    root_text = os.getenv('DAQ_SDK_RELEASE_ROOT')
    if not root_text:
        return None
    root = Path(root_text)
    pointer = root / 'active.json'
    _require(not root.is_symlink() and not pointer.is_symlink(), 'sdk_pointer_invalid')
    if not pointer.exists():
        return None
    payload = json.loads(pointer.read_text(encoding='utf-8'))
    _require(isinstance(payload, dict) and set(payload) == {'release_id'},
             'sdk_pointer_invalid')
    from .sdk_release import _read
    manifest = _read(root, payload['release_id'])
    return manifest.get('knowledge_release') if 'release_notes' in manifest else None


def activate_operator_release(root: Path, release_id: str, *, observe) -> dict:
    _require(callable(observe), 'dev_observer_required')
    with _lock(root):
        manifest, _ = releases._read_release(root, release_id)
        runtime = manifest['review']['operator_release']['runtime']
        manifest = _checked_operator(root, release_id,
                                     runtime_release=runtime['release'],
                                     upstream_sha=runtime['upstream_sha'])
        if manifest['review']['operator_release']['mode'] == MODE_PRO_V2:
            sdk_main = _active_sdk_notes_main_binding()
            _require(sdk_main is None or sdk_main == release_id,
                     'sdk_coordinated_activation_required')
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
        if current['review']['operator_release']['mode'] == MODE_PRO_V2:
            sdk_main = _active_sdk_notes_main_binding()
            _require(sdk_main is None or sdk_main == target_id,
                     'sdk_coordinated_rollback_required')
        target = _checked_operator(root, target_id,
                                   runtime_release=runtime['release'],
                                   upstream_sha=runtime['upstream_sha'])
        return _switch(root, target_id, target, active, observe)
