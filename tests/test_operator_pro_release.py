"""The Pro release adds two operating facts and one reviewed entity alias."""
import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from test_operator_release import _stage, fixture


def _proposal(tmp_path, monkeypatch):
    from daq_fae.knowledge import operator_release as op
    from daq_fae.knowledge import releases
    from daq_fae.knowledge.code_identity import daq_code_sha
    from daq_fae.knowledge.records import access_fingerprint, record_fingerprint

    archive, base, old_report = fixture(tmp_path)
    root = tmp_path / 'published'
    origin = _stage(root, archive, base, old_report)
    releases._write_active_pointer(root, origin)
    refreshed = deepcopy(base)
    refreshed['previous_release'] = origin
    refreshed['runtime']['release'] = 'fae-runtime-2'
    previous = op.stage_operator_release(root, refreshed, archive, old_report,
                                         runtime_release='fae-runtime-2', upstream_sha='a' * 40)
    releases._write_active_pointer(root, previous)
    # Purely synthetic rows keep the release contract covered in clean CI.
    # The separate real-input sentinel binds the private Pro source and hash.
    added = [
        {
            'id': 'topology:ego-pro-standalone', 'kind': 'topology',
            'status': 'verified',
            'scope': {'product': 'ego-1600', 'mode': 'standalone',
                      'firmware_version': 'unknown'},
            'source_refs': [],
            'data': {
                'members': ['entity:ego-1600'],
                'roles': {'entity:ego-1600': 'recorder'},
                'connections': [], 'power': {'entity:ego-1600': 'internal battery'},
                'platform': 'standalone', 'sync_target': 'unknown',
                'storage': 'EGO 1600 TF card',
                'firmware_version': 'unknown',
                'retrieval_aliases': ['单机独立采集', 'standalone'],
                'evidence_conditions': {
                    'host_connection': 'none', 'standalone': True,
                    'task': 'standalone_capture',
                    'topology_aspects': ['power', 'connection', 'storage'],
                },
            },
        },
        {
            'id': 'procedure:ego-pro-standalone', 'kind': 'procedure',
            'status': 'verified',
            'scope': {'product': 'ego-1600', 'mode': 'standalone',
                      'firmware_version': 'unknown'},
            'source_refs': [],
            'data': {
                'task': '录制单机 EGO Pro 数据',
                'topology_id': 'topology:ego-pro-standalone',
                'prerequisites': ['设备断电时插入 TF 卡'],
                'steps': ['断电插卡', '开机', '开始录制', '停止录制'],
                'checks': ['停止后确认保存'], 'failure_branches': [],
                'firmware_version': 'unknown',
                'retrieval_aliases': ['本机 TF 卡独立录制', '独立录制'],
                'task_intents': ['recording', 'local_recording'],
                'evidence_conditions': {
                    'host_connection': 'none', 'standalone': True,
                    'recording_mode': 'local_tf_card', 'storage': 'tf_card',
                    'steps': ['insert_card_power_off', 'power_on',
                              'start_recording', 'stop_recording'],
                    'indicators': ['voice_prompt', 'green_led'],
                },
            },
        },
    ]
    for row in added:
        row['source_refs'] = [deepcopy(base['records'][0]['source_refs'][0])]
    added[0]['data']['members'] = ['entity:ego-1600']
    added[0]['data']['roles'] = {'entity:ego-1600': 'recorder'}
    added[0]['data']['power'] = {'entity:ego-1600': 'internal battery'}
    added[1]['data']['topology_id'] = added[0]['id']
    for row in added:
        row['fact_review'] = {'reviewer': 'synthetic-source-review',
                              'reviewed_at': '2026-10-16'}
        row['access_review'] = {'reviewer': 'synthetic-access-review',
                                'reviewed_at': '2026-10-16',
                                'view_roles': ['internal_fae'],
                                'forward_roles': ['internal_fae']}
        row['fact_review']['record_sha256'] = record_fingerprint(row)
        row['access_review']['record_sha256'] = access_fingerprint(row)
    fingerprints = {row['id']: record_fingerprint(row) for row in added}
    monkeypatch.setattr(op, 'PRO_BASELINE_RELEASE', origin)
    monkeypatch.setattr(op, 'PRO_RECORD_FINGERPRINTS', fingerprints)
    monkeypatch.setattr(op, 'PRO_SOURCE_REF', deepcopy(added[0]['source_refs'][0]))
    monkeypatch.setattr(op, 'PRO_BASELINE_COUNT', len(base['records']))
    new = deepcopy(refreshed)
    identity = next(row for row in new['records'] if row['kind'] == 'entity')
    identity['data']['aliases'] = ['EGO 1600 四目', '四目 EGO 1600']
    identity['fact_review']['record_sha256'] = record_fingerprint(identity)
    identity['access_review']['record_sha256'] = access_fingerprint(identity)
    monkeypatch.setattr(op, 'PRO_IDENTITY_ALIAS_FINGERPRINT',
                        record_fingerprint(identity), raising=False)
    monkeypatch.setattr(op, 'PRO_IDENTITY_ALIAS_REF',
                        deepcopy(identity['source_refs'][0]), raising=False)
    monkeypatch.setattr(op, 'PRO_IDENTITY_ALIAS_VALUE',
                        'EGO 1600 四目', raising=False)
    monkeypatch.setattr(op, 'PRO_IDENTITY_ALIAS_VALUE_2',
                        '四目 EGO 1600', raising=False)
    monkeypatch.setattr(op, 'PRO_IDENTITY_ALIAS_REF_2',
                        deepcopy(identity['source_refs'][0]), raising=False)
    monkeypatch.setattr(op, 'PRO_IDENTITY_ALIAS_ID', identity['id'], raising=False)
    new['records'] += added
    new['previous_release'] = previous
    new['runtime']['release'] = 'fae-runtime-2'
    new['scope']['answerable_ids'] = [row['id'] for row in new['records']]
    new['scope']['excluded_topics'] = [x for x in new['scope']['excluded_topics']
                                       if x not in {'topology', 'procedure'}]
    new['source_check'] = {
        'format_version': 'daq-dev-source-check/v1', 'graph_sha256': 'e' * 64,
        'new_record_ids': list(fingerprints), 'records': deepcopy(fingerprints),
        'scope': 'loopback_dev_internal_fae_only',
    }
    new['replay_identity'] = {
        'mode': 'loopback_dev_candidate_only', 'environment': 'development',
        'daq_code_sha256': daq_code_sha(), 'daq_code_sha256_after': daq_code_sha(),
        'code_unchanged_during_replay': True, 'graph_sha256': 'e' * 64,
        'prompt_sha256': op._prompt_sha256(),
        'candidate_prompt_sha256': hashlib.sha256((Path(__file__).resolve().parents[1] /
            'prompts/candidate_knowledge_system.md').read_bytes()).hexdigest(),
        'schema_prompt_sha256': op._schema_prompt_sha256(),
        'cases': [{'id': case, 'trace_id': f'trace-{case}', 'http_status': 200,
                   'outcome': outcome, 'coverage_status': coverage}
                  for case, outcome, coverage in (
                      ('pro_single_topology', 'resolved', 'full'),
                      ('pro_single_procedure', 'resolved', 'full'),
                      ('ego_resolution_conflict_negative', 'safe_abstained', 'partial'))],
    }
    review = {
        'environment': 'loopback_dev', 'review_method': 'independent_codex_semantic_review',
        'code_sha256': daq_code_sha(), 'identity_sha256': op.digest(new['replay_identity']),
        'all_passed': True,
        'cases': [{'case_id': case['id'], 'trace_id': case['trace_id'],
                   'outcome': case['outcome'], 'coverage_status': case['coverage_status'],
                   'http_status': 200, 'fallback_used': False, 'verdict': 'pass',
                   'source_ids': ([added[0]['id']] if case['id'] == 'pro_single_topology'
                                  else [added[1]['id']] if case['id'] == 'pro_single_procedure'
                                  else [base['records'][1]['id']])}
                  for case in new['replay_identity']['cases']],
    }
    new['bound_input_files'] = {}
    for name, value in (('source_check', new['source_check']),
                        ('replay_identity', new['replay_identity']),
                        ('semantic_review', review)):
        path = tmp_path / f'{name}.json'
        path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True) + '\n')
        new['bound_input_files'][name] = str(path)
    return root, archive, new, review, previous


def _stage_v2(root, archive, bundle, review):
    from daq_fae.knowledge.operator_release import stage_operator_pro_release
    return stage_operator_pro_release(root, bundle, archive, review,
                                      runtime_release='fae-runtime-2', upstream_sha='a' * 40)


def test_pro_v2_stages_exact_delta_and_preserves_v1_loader(tmp_path, monkeypatch):
    from daq_fae.knowledge import releases
    from daq_fae.knowledge.reviewed_view import ReviewedKnowledge
    root, archive, bundle, review, previous = _proposal(tmp_path, monkeypatch)
    rid = _stage_v2(root, archive, bundle, review)
    assert releases.read_active_release(root)['release_id'] == previous
    manifest, _ = releases._read_release(root, rid)
    assert manifest['previous_release'] == previous
    assert manifest['review']['operator_release']['mode'] == 'owner_directed_source_checked_v2'
    assert len(manifest['records']) == 4
    releases._write_active_pointer(root, rid)
    loaded = ReviewedKnowledge.load_active(root, runtime_release='fae-runtime-2',
                                           upstream_sha='a' * 40)
    assert {r['id'] for r in loaded.records_for('internal_fae')} == set(bundle['scope']['answerable_ids'])
    assert {r['kind'] for r in loaded.records_for('channel')} == set()
    assert loaded.ground_question('EGO 1600 四目不连接电脑或手机时，单机采集',
                                  role='internal_fae')[
        'entities'] == ['entity:ego-1600']
    assert loaded.ground_question('EGO 1600 四目牛奶', role='internal_fae') == {}


def test_pro_v2_rejects_unreviewed_identity_alias_change(tmp_path, monkeypatch):
    root, archive, bundle, review, _ = _proposal(tmp_path, monkeypatch)
    identity = next(row for row in bundle['records'] if row['kind'] == 'entity')
    identity['data']['aliases'] = ['EGO 1600 四目', 'EGO 1600 任意版本']
    with pytest.raises(ValueError, match='pro_delta'):
        _stage_v2(root, archive, bundle, review)


def test_pro_v2_formal_retrieval_and_same_runtime_rollback(tmp_path, monkeypatch):
    from daq_fae.domain_tools import DaqToolBox
    from daq_fae.knowledge.operator_release import (
        activate_operator_release,
        rollback_operator_release,
    )
    from daq_fae.knowledge.releases import read_active_release
    from daq_fae.knowledge.reviewed_view import ReviewedKnowledge
    root, archive, bundle, review, previous = _proposal(tmp_path, monkeypatch)
    rid = _stage_v2(root, archive, bundle, review)
    observe = lambda release: {
        'health': {'agent_id': 'ai-daq-fae-agent', 'knowledge_release': release,
                   'runtime_release': 'fae-runtime-2'},
        'trace': {'agent_id': 'ai-daq-fae-agent', 'knowledge_release': release,
                  'runtime_release': 'fae-runtime-2', 'trace_id': 'trace-v2'},
    }
    activate_operator_release(root, rid, observe=observe)
    knowledge = ReviewedKnowledge.load_active(root, runtime_release='fae-runtime-2',
                                              upstream_sha='a' * 40)
    def lookup(question, name, arguments, role='internal_fae'):
        return DaqToolBox(question=question, knowledge=knowledge, role=role,
                          requirements=[]).dispatch(name, arguments)
    positive = lookup('EGO 1600 单机独立采集', 'inspect_topology',
                      {'query': 'EGO 1600 单机独立采集'})
    assert positive.status == 'ok'
    assert [item['source_id'] for item in positive.sources] == ['topology:ego-pro-standalone']
    assert positive.sources[0]['type'] == 'daq_governed_claim'
    procedure = lookup('EGO 1600 本机 TF 卡独立录制', 'lookup_procedure',
                       {'task': '录制单机 EGO Pro 数据', 'entity': 'EGO 1600'})
    assert procedure.status == 'ok'
    assert [item['source_id'] for item in procedure.sources] == ['procedure:ego-pro-standalone']
    detailed_question = ('EGO 1600 本机 TF 卡独立录制，从断电插卡到开机、开始和停止录制，'
                         '语音提示和绿灯怎么核对？')
    requirement = {'id': 'p1', 'capability': 'lookup_procedure',
                   'entities': ['EGO 1600'],
                   'conditions': {'standalone': True, 'storage': 'tf_card',
                                  'task': 'local_recording',
                                  'steps': ['insert_card_power_off', 'power_on',
                                            'start_recording', 'stop_recording'],
                                  'indicators': ['voice_prompt', 'green_led']}}
    matched = DaqToolBox(question=detailed_question, knowledge=knowledge,
                         role='internal_fae', requirements=[requirement]).dispatch(
        'lookup_procedure', {'task': 'local_recording', 'entity': 'EGO 1600'})
    assert matched.content['matched_requirement_ids'] == ['p1']
    bad_step = deepcopy(requirement)
    bad_step['conditions']['steps'] = ['stop_recording', 'start_recording']
    unmatched = DaqToolBox(question=detailed_question, knowledge=knowledge,
                           role='internal_fae', requirements=[bad_step]).dispatch(
        'lookup_procedure', {'task': 'local_recording', 'entity': 'EGO 1600'})
    assert unmatched.content['matched_requirement_ids'] == []
    assert lookup('EGO 1600 单机独立采集', 'inspect_topology',
                  {'query': 'EGO 1600 单机独立采集'}, role='channel').status == 'not_found'
    for query in ('EGO 1600 加 WristCam 单机独立采集',
                  'EGO 1600 接 HUB 单机独立采集',
                  'EGO 1600 固件 v1.0.3 单机独立采集',
                  'EGO 1600 组合采集，单机独立采集',
                  'EGO 1600 多机同步，单机独立采集',
                  'EGO 1600 通过 USB 与 PC 通信时单机独立采集',
                  'EGO 1600 连接 USB 线到电脑单机独立采集'):
        assert lookup(query, 'inspect_topology', {'query': query}).status == 'not_found'
        assert lookup(query, 'search_knowledge', {'query': query}).status == 'not_found'
        assert lookup(query, 'inspect_topology',
                      {'query': 'EGO 1600 单机独立采集'}).status == 'not_found'
        assert lookup(query, 'lookup_procedure',
                      {'task': '录制单机 EGO Pro 数据', 'entity': 'EGO 1600'}).status == 'not_found'
    rollback_operator_release(root, observe=observe)
    assert read_active_release(root)['release_id'] == previous
    assert len(ReviewedKnowledge.load_active(root, runtime_release='fae-runtime-2',
                                              upstream_sha='a' * 40).records_for('internal_fae')) == 2


def test_pro_v2_loader_rejects_changed_code_and_manifest_scope(tmp_path, monkeypatch):
    from daq_fae.knowledge import operator_release as op
    from daq_fae.knowledge import releases
    from daq_fae.knowledge.reviewed_view import ReviewedKnowledge
    root, archive, bundle, review, _previous = _proposal(tmp_path, monkeypatch)
    rid = _stage_v2(root, archive, bundle, review)
    manifest, _ = releases._read_release(root, rid)
    changed = deepcopy(manifest)
    changed['review']['operator_release']['daq_code_sha256'] = 'f' * 64
    with pytest.raises(ValueError, match='dev_identity'):
        ReviewedKnowledge.from_manifest(rid, changed)
    changed = deepcopy(manifest)
    changed['review']['operator_release']['pro_record_fingerprints'] = {}
    with pytest.raises(ValueError, match='manifest_binding'):
        ReviewedKnowledge.from_manifest(rid, changed)
    monkeypatch.setattr(op, '_code_sha256', lambda: 'f' * 64)
    with pytest.raises(ValueError, match='dev_identity'):
        op._checked_operator(root, rid, runtime_release='fae-runtime-2', upstream_sha='a' * 40)


def test_pro_v2_rollback_refuses_uncoordinated_notes_binding(tmp_path, monkeypatch):
    from daq_fae.knowledge import operator_release as op
    from daq_fae.knowledge import releases, sdk_release
    root, archive, bundle, review, _previous = _proposal(tmp_path, monkeypatch)
    rid = _stage_v2(root, archive, bundle, review)
    releases._write_active_pointer(root, rid)
    sdk_root = tmp_path / 'sdk'
    sdk_root.mkdir()
    (sdk_root / 'active.json').write_text(json.dumps({'release_id': 'a' * 64}))
    monkeypatch.setattr(sdk_release, '_read', lambda _root, _rid: {
        'release_notes': [{'kb_id': 'one'}], 'knowledge_release': rid})
    monkeypatch.setenv('DAQ_SDK_RELEASE_ROOT', str(sdk_root))
    with pytest.raises(ValueError, match='sdk_coordinated_rollback_required'):
        op.rollback_operator_release(root, observe=lambda _: {})
    assert releases.read_active_release(root)['release_id'] == rid


@pytest.mark.parametrize('change,reason', [
    ('extra', 'pro_delta'), ('wrong_previous', 'production_baseline'),
    ('wrong_source', 'pro_source'), ('role', 'internal_scope'),
    ('replay_code', 'dev_identity'), ('candidate_prompt', 'dev_identity'),
    ('review_fail', 'dev_cases'),
    ('version_scope', 'pro_record'), ('bound_input', 'bound_inputs'),
])
def test_pro_v2_rejects_unreviewed_delta_and_identity(tmp_path, monkeypatch, change, reason):
    root, archive, bundle, review, _previous = _proposal(tmp_path, monkeypatch)
    if change == 'extra':
        bundle['records'].append(deepcopy(bundle['records'][-1]))
        bundle['records'][-1]['id'] = 'procedure:extra'
        bundle['scope']['answerable_ids'].append('procedure:extra')
    elif change == 'wrong_previous':
        bundle['previous_release'] = 'f' * 64
    elif change == 'wrong_source':
        bundle['records'][-1]['source_refs'][0]['locator']['end'] = 999
    elif change == 'role':
        bundle['records'][-1]['access_review']['view_roles'] = ['channel']
    elif change == 'replay_code':
        bundle['replay_identity']['daq_code_sha256'] = 'f' * 64
    elif change == 'candidate_prompt':
        bundle['replay_identity']['candidate_prompt_sha256'] = 'f' * 64
    elif change == 'review_fail':
        review['cases'][0]['verdict'] = 'fail'
    elif change == 'version_scope':
        bundle['records'][-1]['scope']['firmware_version'] = '1.0.3'
    elif change == 'bound_input':
        Path(bundle['bound_input_files']['source_check']).write_text('{}\n')
    with pytest.raises(ValueError, match=reason):
        _stage_v2(root, archive, bundle, review)
