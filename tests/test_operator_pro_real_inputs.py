"""Local-only static sentinel using the current 25-record release and Pro rows.

The Dev answers below are fabricated test fixtures. This does not stage a
publishable release or replace the final identity-bound real model replay.
"""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from daq_fae.domain_tools import DaqToolBox
from daq_fae.knowledge.adjudication import digest
from daq_fae.knowledge.code_identity import daq_code_sha
from daq_fae.knowledge.operator_release import (
    PRO_BASELINE_RELEASE,
    _candidate_prompt_sha256,
    _prompt_sha256,
    _schema_prompt_sha256,
    stage_operator_pro_release,
    stage_operator_release,
)
from daq_fae.knowledge.releases import _write_active_pointer
from daq_fae.knowledge.reviewed_view import ReviewedKnowledge

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / 'data/knowledge/curated/topology-procedure-review-20261016'
CURRENT_PROD_RELEASE = 'ee9083f9891630f6727766c276e6dc123ab0b6b79641dada133159871eeaacdb'
ORIGIN = (ROOT / 'data/knowledge/curated/catalog-regression-20261016/formal-main-root-v2'
          / 'releases' / CURRENT_PROD_RELEASE / 'manifest.json')
ARCHIVE = ROOT / 'data/knowledge/local_archive/candidate-originals-v2-local-copy-20261010'


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def test_current_25_records_and_pro_rows_formal_loader_static_sentinel(tmp_path):
    assert PRO_BASELINE_RELEASE == CURRENT_PROD_RELEASE
    paths = (ORIGIN, ARCHIVE / 'manifest.json',
             PRIVATE / 'pro-offline-reviewed-graph-v6/dependency-bundle.json',
             PRIVATE / 'v6-pro-reviewed-records-prod-ee908.json',
             PRIVATE / 'dev-source-checked-pro-offline-v6.json',
             PRIVATE / 'v7-ego-pro-identity-alias-row.json')
    if any(not path.is_file() for path in paths):
        pytest.skip('private reviewed originals absent from this checkout')
    origin = _json(ORIGIN)
    snapshot = _json(paths[2])['snapshot']
    prep = _json(paths[3])
    ledger = _json(paths[4])
    alias_row = _json(paths[5])
    assert alias_row['data']['aliases'] == ['EGO Pro 四目']
    assert alias_row['source_refs'][-1]['locator'] == {
        'kind': 'lines', 'start': 1, 'end': 5}
    assert len(prep['records']) == 2
    topology = next(row for row in prep['records'] if row['kind'] == 'topology')
    assert topology['data']['sync_target'] == 'unknown'
    assert any('外部移动电源' in connection and 'Device' in connection
               for connection in topology['data']['connections'])
    procedure = next(row for row in prep['records'] if row['kind'] == 'procedure')
    assert '绿灯常亮即完成录制' in procedure['data']['completion_boundary']
    steps = '\n'.join(procedure['data']['steps'])
    assert '设备已就绪 / Device ready' in steps
    assert '3，2，1 开始录制' in steps
    assert '录制已停止，数据已保存' in steps
    assert len(origin['records']) == 25
    assert {'entity:eg-db', 'entity:wristcam'} <= {row['id'] for row in origin['records']}
    root = tmp_path / 'isolated-test-release'
    directory = root / 'releases' / PRO_BASELINE_RELEASE
    directory.mkdir(parents=True)
    (directory / 'manifest.json').write_bytes(ORIGIN.read_bytes())
    _write_active_pointer(root, PRO_BASELINE_RELEASE)
    old = origin['review']['operator_release']
    upstream = old['runtime']['upstream_sha']
    new_runtime = 'daq-test-only-real-pro-static'
    base = {
        'snapshot': snapshot,
        'records': [{k: v for k, v in row.items() if k != 'answerable'}
                    for row in origin['records']],
        'sections': [], 'bodies': {}, 'previous_release': PRO_BASELINE_RELEASE,
        'field_aliases': deepcopy(old['field_aliases']),
        'scoped_field_aliases': deepcopy(old['scoped_field_aliases']),
        'scope': deepcopy(old['scope']),
        'runtime': {'release': new_runtime, 'upstream_sha': upstream,
                    'tasks_enabled': False, 'generic_trace_enabled': False},
        'dev_batch': {'id': 'synthetic-static-test-only', 'environment': 'dev',
                      'frozen': True,
                      'questions': [{'id': 'static', 'question': 'EGO Pro 四目是什么？'}]},
    }
    fake_base_review = {'environment': 'dev', 'batch_id': 'synthetic-static-test-only',
                        'review_method': 'codex',
                        'cases': [{'id': 'static', 'passed': True,
                                   'answer': '仅供静态测试的占位回答',
                                   'trace_id': 'synthetic-static-test-only',
                                   'outcome': 'resolved', 'fallback_used': False}]}
    new_base25 = stage_operator_release(root, base, ARCHIVE, fake_base_review,
                                        runtime_release=new_runtime, upstream_sha=upstream)
    _write_active_pointer(root, new_base25)
    bundle = deepcopy(base)
    bundle['records'] = [deepcopy(alias_row) if row['id'] == alias_row['id']
                         else row for row in bundle['records']]
    bundle['records'].extend(deepcopy(prep['records']))
    bundle['previous_release'] = new_base25
    bundle['scope']['answerable_ids'] = [row['id'] for row in bundle['records']]
    bundle['scope']['excluded_topics'] = [value for value in
                                          bundle['scope']['excluded_topics']
                                          if value not in {'topology', 'procedure'}]
    bundle['source_check'] = ledger
    bundle['replay_identity'] = {
        'mode': 'loopback_dev_candidate_only', 'environment': 'development',
        'code_unchanged_during_replay': True,
        'daq_code_sha256': daq_code_sha(),
        'daq_code_sha256_after': daq_code_sha(),
        'prompt_sha256': _prompt_sha256(),
        'candidate_prompt_sha256': _candidate_prompt_sha256(),
        'schema_prompt_sha256': _schema_prompt_sha256(),
        'graph_sha256': ledger['graph_sha256'],
        'cases': [{'id': case, 'trace_id': 'synthetic-' + case, 'http_status': 200,
                   'outcome': outcome, 'coverage_status': coverage}
                  for case, outcome, coverage in (
                      ('pro_single_topology', 'resolved', 'full'),
                      ('pro_single_procedure', 'resolved', 'full'),
                      ('ego_resolution_conflict_negative', 'safe_abstained', 'partial'))],
    }
    fake_review = {
        'environment': 'loopback_dev',
        'review_method': 'independent_codex_semantic_review',
        'code_sha256': daq_code_sha(),
        'identity_sha256': digest(bundle['replay_identity']),
        'all_passed': True,
        'cases': [{'case_id': case['id'], 'trace_id': case['trace_id'],
                   'http_status': 200, 'outcome': case['outcome'],
                   'coverage_status': case['coverage_status'],
                   'fallback_used': False, 'verdict': 'pass',
                   'source_ids': ([prep['records'][0]['id']]
                                  if case['id'] == 'pro_single_topology' else
                                  [prep['records'][1]['id']]
                                  if case['id'] == 'pro_single_procedure' else
                                  ['claim:ego-1600-resolution'])}
                  for case in bundle['replay_identity']['cases']],
    }
    bundle['bound_input_files'] = {'source_check': str(paths[4])}
    for name, value in (('replay_identity', bundle['replay_identity']),
                        ('semantic_review', fake_review)):
        path = tmp_path / (name + '.json')
        path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True) + '\n')
        bundle['bound_input_files'][name] = str(path)
    pro27 = stage_operator_pro_release(root, bundle, ARCHIVE, fake_review,
                                      runtime_release=new_runtime, upstream_sha=upstream)
    _write_active_pointer(root, pro27)
    knowledge = ReviewedKnowledge.load_active(root, runtime_release=new_runtime,
                                              upstream_sha=upstream)
    assert len(knowledge.records_for('internal_fae')) == 27
    assert {'entity:eg-db', 'entity:wristcam'} <= {
        row['id'] for row in knowledge.records_for('internal_fae')}
    assert knowledge.ground_question(
        '一台 EGO Pro 四目不连接电脑或手机时，单机采集如何供电？',
        role='internal_fae')['entities'] == ['entity:ego-pro']
    def dispatch(question, tool, arguments, role='internal_fae'):
        return DaqToolBox(question=question, knowledge=knowledge, role=role,
                          requirements=[]).dispatch(tool, arguments)
    question = '一台 EGO Pro 四目不连接电脑或手机，单机独立采集如何供电与存储？'
    result = dispatch(question, 'inspect_topology', {'query': question})
    assert result.status == 'ok'
    assert [source['source_id'] for source in result.sources] == [
        'topology:ego-pro-standalone']
    assert result.sources[0]['type'] == 'daq_governed_claim'
    question = '一台 EGO Pro 四目用本机 TF 卡独立录制，如何开机、开始和停止？'
    result = dispatch(question, 'lookup_procedure',
                      {'task': 'local_recording', 'entity': 'EGO Pro 四目'})
    assert result.status == 'ok'
    assert [source['source_id'] for source in result.sources] == [
        'procedure:ego-pro-standalone']
    assert dispatch(question, 'lookup_procedure',
                    {'task': 'local_recording', 'entity': 'EGO Pro 四目'},
                    role='channel').status == 'not_found'
    for extra in ('WristCam', 'HUB', '固件 v1.0.3', '通过 USB 与 PC 通信', '组合采集'):
        query = '一台 EGO Pro 四目单机独立采集，' + extra
        assert dispatch(query, 'inspect_topology',
                        {'query': 'EGO Pro 四目单机独立采集'}).status == 'not_found'
