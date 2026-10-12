"""Owner-directed source-checked pilot release; no invented external approval."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from scripts.archive_candidate_snapshot import create_archive
from test_knowledge_releases import LOCATOR, _entity, _record, _reviewed


def fixture(tmp_path):
    source = tmp_path / 'originals'
    source.mkdir()
    (source / 'spec.md').write_text('EGO 1600 confirmed source\n', encoding='utf-8')
    archive = tmp_path / 'archive'
    summary = create_archive(source, archive, '2026-10-12')
    original = json.loads((archive / 'manifest.json').read_text())['files'][0]
    from daq_fae.knowledge.records import access_fingerprint
    rows = []
    for row in (_entity(), _record()):
        row['source_refs'][0]['sha256'] = original['sha256']
        row = _reviewed(row)
        row['access_review']['forward_roles'] = ['internal_fae']
        row['access_review']['record_sha256'] = access_fingerprint(row)
        rows.append(row)
    snapshot = {
        'archive_manifest_sha256': summary['manifest_sha256'], 'source_date': '2026-10-12',
        'sources': [{**original, 'kind': 'markdown'}],
        'chunks': [{'source_path': 'spec.md', 'source_sha256': original['sha256'],
                    'locator': LOCATOR, 'text': 'EGO 1600 confirmed source'}],
    }
    bundle = {
        'snapshot': snapshot, 'records': rows, 'sections': [], 'bodies': {},
        'previous_release': None,
        'field_aliases': {'resolution': ['分辨率']},
        'scoped_field_aliases': {},
        'scope': {'answerable_ids': [row['id'] for row in rows],
                  'excluded_topics': ['software', 'links', 'module_projection',
                                      'topology', 'procedure', 'candidate_sections']},
        'runtime': {'release': 'fae-runtime-1', 'upstream_sha': 'a' * 40,
                    'tasks_enabled': False, 'generic_trace_enabled': False},
        'dev_batch': {'id': 'pilot-1', 'environment': 'dev', 'frozen': True,
                      'questions': [{'id': 'q1', 'question': 'EGO 1600 是什么？'}]},
    }
    report = {'environment': 'dev', 'batch_id': 'pilot-1', 'review_method': 'codex',
              'cases': [{'id': 'q1', 'passed': True, 'answer': 'EGO 1600 是设备变体。',
                         'trace_id': 'trace-1', 'outcome': 'resolved', 'fallback_used': False}]}
    return archive, bundle, report


def _stage(root, archive, bundle, report):
    from daq_fae.knowledge.operator_release import stage_operator_release
    return stage_operator_release(root, bundle, archive, report,
                                  runtime_release='fae-runtime-1', upstream_sha='a' * 40)


def test_operator_release_stages_activates_and_loads_without_signature(tmp_path):
    from daq_fae.knowledge.operator_release import activate_operator_release
    from daq_fae.knowledge.reviewed_view import ReviewedKnowledge
    from daq_fae.knowledge.releases import read_active_release
    archive, bundle, report = fixture(tmp_path)
    root = tmp_path / 'published'
    rid = _stage(root, archive, bundle, report)
    assert read_active_release(root) is None
    observe = lambda release: {
        'health': {'agent_id': 'ai-daq-fae-agent', 'knowledge_release': release,
                   'runtime_release': 'fae-runtime-1'},
        'trace': {'agent_id': 'ai-daq-fae-agent', 'knowledge_release': release,
                  'runtime_release': 'fae-runtime-1', 'trace_id': 'trace-2'},
    }
    activate_operator_release(root, rid, observe=observe)
    loaded = ReviewedKnowledge.load_active(root, runtime_release='fae-runtime-1',
                                           upstream_sha='a' * 40)
    assert loaded.release_id == rid
    assert len(loaded.records_for('internal_fae')) == 2
    assert loaded.records_for('channel') == []
    with pytest.raises(ValueError, match='runtime_identity'):
        ReviewedKnowledge.load_active(root, runtime_release='other-runtime',
                                      upstream_sha='a' * 40)


def test_operator_release_grounds_natural_name_and_field_for_evidence(tmp_path):
    from daq_fae.knowledge.operator_release import activate_operator_release
    from daq_fae.knowledge.reviewed_view import ReviewedKnowledge
    from daq_fae.domain_tools import DaqToolBox
    from daq_fae.task_context import prepare_turn
    archive, bundle, report = fixture(tmp_path)
    root = tmp_path / 'published'
    rid = _stage(root, archive, bundle, report)
    activate_operator_release(root, rid, observe=lambda release: {
        'health': {'agent_id': 'ai-daq-fae-agent', 'knowledge_release': release,
                   'runtime_release': 'fae-runtime-1'},
        'trace': {'agent_id': 'ai-daq-fae-agent', 'knowledge_release': release,
                  'runtime_release': 'fae-runtime-1', 'trace_id': 'trace-2'},
    })
    knowledge = ReviewedKnowledge.load_active(root, runtime_release='fae-runtime-1',
                                              upstream_sha='a' * 40)
    question = 'EGO 1600 的分辨率是多少？'
    assert knowledge.ground_question(question, role='channel') == {}
    target = knowledge.ground_question(question, role='internal_fae')
    assert target['entities'] == ['entity:ego-1600']
    plan = prepare_turn(question, grounded_targets=target)
    result = DaqToolBox(question=question, knowledge=knowledge, role='internal_fae',
                        requirements=plan.requirements).dispatch(
                            'lookup_spec', {'entity': 'EGO 1600', 'field': '分辨率', 'conditions': {}})
    assert result.status == 'ok'
    assert result.content['matches'][0]['data']['value'] == '1600x1200'
    assert result.content['matched_requirement_ids']


def test_operator_release_rolls_back_only_to_its_recorded_predecessor(tmp_path):
    from daq_fae.knowledge.operator_release import activate_operator_release, rollback_operator_release
    from daq_fae.knowledge.releases import read_active_release
    archive, bundle, report = fixture(tmp_path)
    root = tmp_path / 'published'
    observe = lambda release: {
        'health': {'agent_id': 'ai-daq-fae-agent', 'knowledge_release': release,
                   'runtime_release': 'fae-runtime-1'},
        'trace': {'agent_id': 'ai-daq-fae-agent', 'knowledge_release': release,
                  'runtime_release': 'fae-runtime-1', 'trace_id': 'trace-2'},
    }
    first = _stage(root, archive, bundle, report)
    activate_operator_release(root, first, observe=observe)
    bundle['previous_release'] = first
    second = _stage(root, archive, bundle, report)
    activate_operator_release(root, second, observe=observe)
    assert read_active_release(root)['release_id'] == second
    with pytest.raises(ValueError, match='observation'):
        rollback_operator_release(root, observe=lambda _: observe(second))
    assert read_active_release(root)['release_id'] == second
    rollback_operator_release(root, observe=observe)
    assert read_active_release(root)['release_id'] == first


def test_task_enabled_operator_release_requires_independently_reviewed_dev_task_batch(tmp_path):
    from daq_fae.knowledge.adjudication import digest
    from daq_fae.knowledge.operator_release import stage_operator_release, verify_operator_manifest
    from daq_fae.knowledge.releases import _read_release

    archive, bundle, report = fixture(tmp_path)
    bundle['runtime']['tasks_enabled'] = True
    bundle['runtime']['task_binding_contract'] = 'daq-task-role-release-v1'
    binding = digest({key: bundle[key] for key in ('snapshot', 'records', 'scope', 'runtime')})
    cases = [
        ('answerable', 'resolved'),
        ('missing_evidence', 'safe_abstained'),
        ('role_revoked', 'scope_denied'),
        ('release_changed', 'scope_denied'),
    ]
    bundle['task_dev_batch'] = {
        'id': 'task-pilot-1', 'environment': 'dev', 'frozen': True,
        'contract': 'orbbec-http-task/v1', 'release_input_sha256': binding,
        'questions': [{'id': kind, 'kind': kind, 'question': 'EGO 1600 规格？'}
                      for kind, _ in cases],
    }
    task_report = {
        'environment': 'dev', 'batch_id': 'task-pilot-1',
        'contract': 'orbbec-http-task/v1', 'release_input_sha256': binding,
        'review_method': 'codex',
        'cases': [{'id': kind, 'passed': True, 'outcome': outcome,
                   'answer': '有据回答' if kind == 'answerable' else '',
                   'trace_id': f'trace-{kind}' if kind in {'answerable', 'missing_evidence'} else None,
                   'request_id': str(uuid4()), 'platform_task_id': str(uuid4()),
                   'http_status': 403 if kind in {'role_revoked', 'release_changed'} else 200,
                   'sources': [{'source_id': 'claim:verified'}] if kind == 'answerable' else [],
                   'fallback_used': False} for kind, outcome in cases],
    }
    root = tmp_path / 'published'
    with pytest.raises(ValueError, match='task_dev_report'):
        stage_operator_release(root, bundle, archive, report,
                               runtime_release='fae-runtime-1', upstream_sha='a' * 40)
    release_id = stage_operator_release(
        root, bundle, archive, report, task_dev_report=task_report,
        runtime_release='fae-runtime-1', upstream_sha='a' * 40,
    )
    manifest, _ = _read_release(root, release_id)
    verify_operator_manifest(manifest, runtime_release='fae-runtime-1', upstream_sha='a' * 40)
    assert manifest['review']['operator_release']['task_dev_batch_id'] == 'task-pilot-1'
    task_report['cases'][2]['passed'] = False
    with pytest.raises(ValueError, match='task_dev_cases'):
        stage_operator_release(tmp_path / 'rejected', bundle, archive, report,
                               task_dev_report=task_report,
                               runtime_release='fae-runtime-1', upstream_sha='a' * 40)
    malformed = deepcopy(bundle)
    malformed['task_dev_batch']['questions'][0].pop('kind')
    with pytest.raises(ValueError, match='task_dev_questions'):
        stage_operator_release(tmp_path / 'malformed', malformed, archive, report,
                               task_dev_report=task_report,
                               runtime_release='fae-runtime-1', upstream_sha='a' * 40)


def test_operator_stage_cli_routes_task_report_to_task_gate(tmp_path, monkeypatch):
    from scripts.stage_operator_pilot import main

    archive, bundle, report = fixture(tmp_path)
    proposal = tmp_path / 'proposal.json'
    proposal.write_text(json.dumps(bundle))
    aliases = tmp_path / 'aliases.json'
    aliases.write_text(json.dumps({'field_aliases': bundle['field_aliases'],
                                   'scoped_field_aliases': bundle['scoped_field_aliases']}))
    browser_report = tmp_path / 'browser-report.json'
    browser_report.write_text(json.dumps({**report, 'batch': bundle['dev_batch']}))
    task_report = tmp_path / 'task-report.json'
    task_report.write_text(json.dumps({'batch': {'id': 'task-pilot-1'}}))
    monkeypatch.setattr('sys.argv', [
        'stage_operator_pilot', '--proposal', str(proposal), '--archive', str(archive),
        '--aliases', str(aliases), '--dev-report', str(browser_report),
        '--task-dev-report', str(task_report), '--release-root', str(tmp_path / 'published'),
        '--runtime-release', 'fae-runtime-1', '--upstream-sha', 'a' * 40,
    ])
    with pytest.raises(ValueError, match='task_dev_batch'):
        main()


@pytest.mark.parametrize('change', ['source', 'role', 'scope', 'dev', 'runtime', 'section'])
def test_operator_release_rejects_unscoped_or_unverified_inputs(tmp_path, change):
    archive, bundle, report = fixture(tmp_path)
    if change == 'source':
        bundle['snapshot']['sources'][0]['sha256'] = '0' * 64
    elif change == 'role':
        bundle['records'][0]['access_review']['view_roles'] = ['channel']
    elif change == 'scope':
        bundle['scope']['answerable_ids'] = []
    elif change == 'dev':
        report['cases'][0]['passed'] = False
    elif change == 'runtime':
        bundle['runtime']['release'] = 'wrong-runtime'
    else:
        bundle['sections'] = [{'section_id': 'unreviewed'}]
    with pytest.raises(ValueError):
        _stage(tmp_path / 'published', archive, bundle, report)
    assert not (tmp_path / 'published' / 'active.json').exists()
