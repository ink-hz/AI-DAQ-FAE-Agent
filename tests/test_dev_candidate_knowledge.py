import hashlib
import json
from pathlib import Path

import pytest

from daq_fae.domain_evidence import DaqEvidencePolicy, question_requirements
from daq_fae.domain_tools import DaqToolBox
from daq_fae.knowledge.dev_candidate import DevCandidateKnowledge
from daq_fae.knowledge.records import record_fingerprint
from daq_fae.request_schema import DaqRequestSchema
from daq_fae.task_context import prepare_turn
from src.agent.loop.answer_contract import AnswerSubmission


def _write(path, value):
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode()
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _fixture(tmp_path):
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    bound = tmp_path / "bound.json"
    archive = tmp_path / "archive"
    archive.mkdir()
    source = {"path": "guide.md", "sha256": "a" * 64, "size": 1, "kind": "text"}
    ref = {"path": source["path"], "sha256": source["sha256"],
           "locator": {"kind": "lines", "start": 1, "end": 1}}
    body = "EGO 单机试录步骤。"
    section = {"section_id": "system:ego:recording", "title": "EGO 单机录制",
               "scope": {"product": "ego"}, "source_refs": [ref],
               "dependency_claim_ids": [], "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
               "review_status": "candidate", "proposed_view_roles": ["internal_fae"]}
    snapshot = {"archive_manifest_sha256": "b" * 64, "sources": [source],
                "chunks": [{"source_path": source["path"],
                            "source_sha256": source["sha256"], "locator": ref["locator"]}]}
    bundle = {"records": [], "sections": [section], "snapshot": snapshot}
    bundle_sha = _write(graph_dir / "dependency-bundle.json", bundle)
    bound_sha = _write(bound, {"sections": [section], "bodies": {section["section_id"]: body}})
    _write(archive / "manifest.json", {"files": [source]})
    _write(graph_dir / "summary.json", {
        "output_sha256": {"dependency-bundle.json": bundle_sha},
        "input_sha256": {"bound_section_index": bound_sha}})
    return graph_dir, bound, archive, snapshot


def test_real_candidate_sections_are_searchable_but_not_governed(monkeypatch, tmp_path):
    graph_dir, bound, archive, snapshot = _fixture(tmp_path)
    monkeypatch.setattr("daq_fae.knowledge.dev_candidate.verify_archive",
                        lambda *_: {"manifest_sha256": snapshot["archive_manifest_sha256"],
                                    "sources": snapshot["sources"]})
    knowledge = DevCandidateKnowledge.load(graph_dir, bound, archive)
    assert knowledge.release_id.startswith("candidate-dev-")
    assert knowledge.records_for("internal_fae") == []
    assert knowledge.sections_for("channel") == []
    result = DaqToolBox(knowledge=knowledge, role="internal_fae").dispatch(
        "search_knowledge", {"query": "EGO 单机录制"})
    assert result.status == "ok"
    assert result.content["matches"][0]["status"] == "candidate"
    assert result.sources[0]["verification_status"] == "candidate"
    assert result.content["matched_requirement_ids"] == []
    session = DaqEvidencePolicy().begin(question_requirements("EGO 单机怎么录制？"))
    session.observe("search_knowledge", result)
    assert session.snapshot().requirement_status == {"question_evidence": "unknown"}
    assert session.evaluate(AnswerSubmission(outcome="resolved", conclusion="按候选步骤录制")).action != "allow"


def test_candidate_input_hash_and_body_tampering_fail(monkeypatch, tmp_path):
    graph_dir, bound, archive, snapshot = _fixture(tmp_path)
    monkeypatch.setattr("daq_fae.knowledge.dev_candidate.verify_archive",
                        lambda *_: {"manifest_sha256": snapshot["archive_manifest_sha256"],
                                    "sources": snapshot["sources"]})
    data = json.loads(bound.read_text())
    data["bodies"]["system:ego:recording"] = "tampered"
    _write(bound, data)
    with pytest.raises(ValueError, match="hash"):
        DevCandidateKnowledge.load(graph_dir, bound, archive)


def test_source_check_reuses_unchanged_records_across_graph_updates(monkeypatch, tmp_path):
    graph_dir, bound, archive, snapshot = _fixture(tmp_path)
    monkeypatch.setattr("daq_fae.knowledge.dev_candidate.verify_archive",
                        lambda *_: {"manifest_sha256": snapshot["archive_manifest_sha256"]})
    bundle = json.loads((graph_dir / "dependency-bundle.json").read_text())
    entity = {"id": "entity:ego", "kind": "entity", "status": "candidate",
              "scope": {"product": "ego"},
              "data": {"name": "EGO", "entity_type": "device"},
              "source_refs": bundle["sections"][0]["source_refs"]}
    bundle["records"] = [entity]
    graph_sha = _write(graph_dir / "dependency-bundle.json", bundle)
    _write(graph_dir / "summary.json", {
        "output_sha256": {"dependency-bundle.json": graph_sha},
        "input_sha256": {"bound_section_index": hashlib.sha256(bound.read_bytes()).hexdigest()}})
    checked = {**entity, "status": "verified"}
    ledger = tmp_path / "source-check.json"
    _write(ledger, {"format_version": "daq-dev-source-check/v1",
                    "graph_sha256": graph_sha, "reviewer": "Codex",
                    "reviewed_at": "2026-10-12",
                    "scope": "loopback_dev_internal_fae_only",
                    "records": {"entity:ego": record_fingerprint(checked)}})
    knowledge = DevCandidateKnowledge.load(graph_dir, bound, archive, ledger)
    assert knowledge.manifest["source_checked_count"] == 1
    assert knowledge.records_for("internal_fae")[0]["id"] == "entity:ego"
    assert knowledge.records_for("channel") == []
    original_release = knowledge.release_id
    bundle["records"].append({**entity, "id": "entity:unrelated",
                              "data": {"name": "Unrelated", "entity_type": "device"}})
    updated_sha = _write(graph_dir / "dependency-bundle.json", bundle)
    _write(graph_dir / "summary.json", {
        "output_sha256": {"dependency-bundle.json": updated_sha},
        "input_sha256": {"bound_section_index": hashlib.sha256(bound.read_bytes()).hexdigest()}})
    updated = DevCandidateKnowledge.load(graph_dir, bound, archive, ledger)
    assert updated.manifest["source_checked_count"] == 1
    assert updated.release_id != original_release
    bundle["records"][0]["data"]["name"] = "Changed EGO"
    changed_sha = _write(graph_dir / "dependency-bundle.json", bundle)
    _write(graph_dir / "summary.json", {
        "output_sha256": {"dependency-bundle.json": changed_sha},
        "input_sha256": {"bound_section_index": hashlib.sha256(bound.read_bytes()).hexdigest()}})
    with pytest.raises(ValueError, match="fingerprint"):
        DevCandidateKnowledge.load(graph_dir, bound, archive, ledger)
    broken = json.loads(ledger.read_text())
    broken["records"]["entity:ego"] = "0" * 64
    _write(ledger, broken)
    with pytest.raises(ValueError, match="fingerprint"):
        DevCandidateKnowledge.load(graph_dir, bound, archive, ledger)


def test_source_checked_natural_variant_question_can_satisfy_dev_ledger():
    ref = {"path": "guide.md", "sha256": "a" * 64,
           "locator": {"kind": "lines", "start": 1, "end": 1}}
    entity = {"id": "entity:ego-1600", "kind": "entity", "status": "verified",
              "scope": {"product": "ego", "required_selectors": ["resolution_variant"],
                        "resolution_variant": "1600x1200"},
              "data": {"name": "EGO", "entity_type": "variant"}, "source_refs": [ref]}
    claim = {"id": "claim:ego-1600-resolution", "kind": "claim", "status": "verified",
             "scope": entity["scope"], "source_refs": [ref],
             "data": {"entity_id": entity["id"], "field": "rgb_resolution",
                      "value": "1600x1200", "unit": "px", "conditions": {}}}
    knowledge = DevCandidateKnowledge("candidate-dev-" + "a" * 64, [], [entity, claim],
                                      {"rgb分辨率": "rgb_resolution"})
    question = "EGO 双目 1600×1200 变体的 RGB 分辨率是多少？"
    plan = prepare_turn(question, grounded_targets=knowledge.ground_question(question))
    toolbox = DaqToolBox(question=question, knowledge=knowledge, role="internal_fae",
                         requirements=plan.requirements)
    session = DaqEvidencePolicy(allow_dev_source_check=True).begin(plan.evidence_requirements())
    identity = toolbox.dispatch("resolve_entity", {"text": "EGO 双目 1600×1200 变体"})
    fact = toolbox.dispatch("lookup_spec", {"entity": "EGO 双目", "field": "RGB 分辨率",
                                            "conditions": "{'variant': '双目 1600×1200'}"})
    assert identity.status == "ok"
    assert fact.status == "ok"
    assert fact.content["query_scope_normalized"] is True
    assert fact.content["matches"][0]["status"] == "source_checked"
    for name, result in [("resolve_entity", identity), ("lookup_spec", fact)]:
        session.observe(name, result)
    assert set(session.snapshot().requirement_status.values()) == {"satisfied"}
    assert session.evaluate(AnswerSubmission(outcome="resolved", conclusion="1600×1200")).action == "allow"


def test_candidate_preview_keeps_loose_public_key_discovery():
    ref = {'path': 'hub.md', 'sha256': 'a' * 64,
           'locator': {'kind': 'lines', 'start': 1, 'end': 1}}
    entity = {'id': 'entity:hub', 'kind': 'entity', 'status': 'verified',
              'scope': {'product': 'hub'},
              'data': {'name': '数采 HUB', 'entity_type': 'hub'},
              'source_refs': [ref]}
    knowledge = DevCandidateKnowledge('candidate-dev-' + 'a' * 64, [], [entity], {})
    result = DaqToolBox(knowledge=knowledge, role='internal_fae').dispatch(
        'resolve_entity', {'text': 'HUB 同步中枢'})
    assert result.status == 'ok'
    assert result.content['matches'][0]['id'] == 'entity:hub'


def test_source_checked_standalone_tools_accept_natural_pro_queries_without_combo_leak():
    ref = {'path': 'pro.md', 'sha256': 'a' * 64,
           'locator': {'kind': 'lines', 'start': 54, 'end': 90}}
    entity = {'id': 'entity:ego-pro', 'kind': 'entity', 'status': 'verified',
              'scope': {'product': 'ego-pro'},
              'data': {'name': 'EGO Pro', 'entity_type': 'device'}, 'source_refs': [ref]}
    topology = {'id': 'topology:ego-pro-standalone', 'kind': 'topology',
                'status': 'verified', 'scope': {'product': 'ego-pro', 'mode': 'standalone'},
                'data': {'members': [entity['id']], 'roles': {entity['id']: 'recorder'},
                         'connections': [], 'power': {entity['id']: 'internal battery'},
                         'platform': 'standalone', 'sync_target': 'local streams',
                         'storage': 'TF card',
                         'evidence_conditions': {
                             'host_connection': 'none', 'standalone': True,
                             'topology_aspects': ['power', 'connection', 'storage']},
                         'retrieval_aliases': ['单机独立采集', '不连接电脑或手机']},
                'source_refs': [ref]}
    procedure = {'id': 'procedure:ego-pro-standalone', 'kind': 'procedure',
                 'status': 'verified', 'scope': topology['scope'],
                 'data': {'task': '录制单机 EGO Pro 数据', 'topology_id': topology['id'],
                          'prerequisites': ['断电插卡'], 'steps': ['开机', '录制', '停止'],
                          'checks': ['绿灯常亮'], 'failure_branches': [],
                          'evidence_conditions': {
                              'recording_mode': 'local_tf_card',
                              'standalone': True, 'storage': 'tf_card',
                              'steps': ['insert_card_power_off', 'power_on',
                                        'start_recording', 'stop_recording'],
                              'indicators': ['voice_prompt', 'green_led']},
                          'retrieval_aliases': ['本机 TF 卡独立录制'],
                          'task_intents': ['recording', 'capture', 'local_recording']},
                 'source_refs': [ref]}
    knowledge = DevCandidateKnowledge('candidate-dev-' + 'a' * 64, [],
                                      [entity, topology, procedure], {},
                                      candidate_entities=[
                                          {'id': 'entity:hub', 'kind': 'entity',
                                           'scope': {'product': 'hub'},
                                           'data': {'name': '数采 HUB', 'aliases': []}},
                                          {'id': 'entity:wristcam', 'kind': 'entity',
                                           'scope': {'product': 'wristcam'},
                                           'data': {'name': 'WristCam', 'aliases': []}},
                                      ])
    box = DaqToolBox(knowledge=knowledge, role='internal_fae')
    topology_query = 'EGO Pro 四目 standalone 不连接电脑或手机 单机独立采集 供电 连接 存储拓扑'
    topology_result = box.dispatch('inspect_topology', {'query': topology_query})
    assert topology_result.status == 'ok'
    assert [row['id'] for row in topology_result.content['matches']] == [topology['id']]
    procedure_result = box.dispatch('lookup_procedure', {
        'task': '本机 TF 卡独立录制（断电插卡、开机、开始/停止录制、语音与绿灯状态核对）',
        'entity': 'EGO Pro',
    })
    assert procedure_result.status == 'ok'
    assert [row['id'] for row in procedure_result.content['matches']] == [procedure['id']]
    trace_input = {
        'task': '本机 TF 卡独立录制：断电插卡、开机、开始录制、停止录制，语音与绿灯状态核对',
        'entity': 'EGO Pro 四目',
    }
    trace_result = box.dispatch('lookup_procedure', trace_input)
    assert [row['id'] for row in trace_result.content['matches']] == [procedure['id']]
    assert box.dispatch('lookup_procedure', {
        **trace_input, 'entity': 'EGO Pro 四目与 HUB',
    }).status == 'not_found'
    assert box.dispatch('lookup_procedure', {
        **trace_input, 'task': trace_input['task'] + '，另接 WristCam',
    }).status == 'not_found'
    assert box.dispatch('lookup_procedure', {
        **trace_input, 'task': trace_input['task'] + '，固件 v1.1.6',
    }).status == 'not_found'
    assert box.dispatch('inspect_topology', {
        'query': 'EGO Pro 与左右 WristCam 经 HUB 组合采集',
    }).status == 'not_found'
    assert box.dispatch('inspect_topology', {
        'query': 'EGO Pro 与 HUB 不连接电脑或手机的单机独立采集',
    }).status == 'not_found'
    assert box.dispatch('inspect_topology', {
        'query': 'EGO Pro 与 WristCam 不连接电脑或手机的单机独立采集',
    }).status == 'not_found'
    assert box.dispatch('inspect_topology', {
        'query': 'EGO Pro 连接电脑做单机独立采集',
    }).status == 'not_found'
    assert box.dispatch('lookup_procedure', {
        'task': '升级 EGO Pro 固件', 'entity': 'EGO Pro',
    }).status == 'not_found'
    assert box.dispatch('inspect_topology', {
        'query': 'EGO Pro standalone 固件 v1.1.6 的单机独立采集拓扑',
    }).status == 'not_found'
    assert box.dispatch('inspect_topology', {
        'query': 'EGO Pro 1600×1200 单机独立采集拓扑',
    }).status == 'not_found'
    assert DaqToolBox(knowledge=knowledge, role='channel').dispatch(
        'inspect_topology', {'query': topology_query}).status == 'not_found'
    topology_schema = DaqRequestSchema.from_candidate(topology_query, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'inspect_topology',
            'entities': ['EGO Pro'],
            'conditions': topology['data']['evidence_conditions'],
        }],
    })
    topology_plan = prepare_turn(topology_query, request_schema=topology_schema,
                                 grounded_targets=knowledge.ground_question(topology_query))
    assert 'task' not in topology_plan.requirements[0]['conditions']
    scoped = DaqToolBox(question=topology_query, knowledge=knowledge,
                        role='internal_fae', requirements=topology_plan.requirements)
    scoped_result = scoped.dispatch('inspect_topology', {'query': topology_query})
    assert scoped_result.content['matched_requirement_ids'] == [
        item['id'] for item in topology_plan.requirements]
    topology_session = DaqEvidencePolicy(allow_dev_source_check=True).begin(
        topology_plan.evidence_requirements())
    topology_session.observe('inspect_topology', scoped_result)
    assert set(topology_session.snapshot().requirement_status.values()) == {'satisfied'}
    trace_question = ('一台 EGO Pro 不连接电脑或手机时，单机独立采集的'
                      '供电、连接与存储拓扑是什么？')
    trace_schema = DaqRequestSchema.from_candidate(trace_question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'inspect_topology',
            'entities': ['EGO Pro'], 'conditions': {
                'usage_context': 'standalone_no_host',
                'power': True, 'connection': True, 'storage': True},
        }],
    })
    trace_plan = prepare_turn(trace_question, request_schema=trace_schema,
                              grounded_targets=knowledge.ground_question(trace_question))
    trace_result = DaqToolBox(
        question=trace_question, knowledge=knowledge, role='internal_fae',
        requirements=trace_plan.requirements).dispatch(
            'inspect_topology', {'query': trace_question})
    assert trace_result.content['matched_requirement_ids'] == [
        item['id'] for item in trace_plan.requirements]
    trace_session = DaqEvidencePolicy(allow_dev_source_check=True).begin(
        trace_plan.evidence_requirements())
    trace_session.observe('inspect_topology', trace_result)
    assert set(trace_session.snapshot().requirement_status.values()) == {'satisfied'}
    without_scope = {**topology, 'data': {key: value for key, value in
                                        topology['data'].items()
                                        if key != 'evidence_conditions'}}
    unbound = DevCandidateKnowledge('candidate-dev-' + 'b' * 64, [],
                                    [entity, without_scope, procedure], {})
    unbound_result = DaqToolBox(question=topology_query, knowledge=unbound,
                                role='internal_fae',
                                requirements=topology_plan.requirements).dispatch(
        'inspect_topology', {'query': topology_query})
    assert unbound_result.status == 'ok'
    assert unbound_result.content['matched_requirement_ids'] == []


def test_candidate_combo_requires_each_positive_member_and_vetoes_negation():
    ref = {'path': 'combo.md', 'sha256': 'a' * 64,
           'locator': {'kind': 'lines', 'start': 1, 'end': 3}}
    pro = {'id': 'entity:pro', 'kind': 'entity', 'status': 'verified',
           'scope': {'product': 'ego-pro'},
           'data': {'name': 'EGO Pro', 'entity_type': 'device'},
           'source_refs': [ref]}
    wrist = {'id': 'entity:wrist', 'kind': 'entity', 'status': 'verified',
             'scope': {'product': 'wristcam'},
             'data': {'name': 'WristCam', 'entity_type': 'device'},
             'source_refs': [ref]}
    combo = {'id': 'topology:pro-wrist', 'kind': 'topology', 'status': 'verified',
             'scope': {'product': 'pro-wrist', 'mode': 'combination'},
             'data': {'members': [pro['id'], wrist['id']],
                      'roles': {pro['id']: 'recorder', wrist['id']: 'camera'},
                      'connections': [], 'power': {}, 'platform': 'standalone',
                      'sync_target': 'local streams', 'storage': 'TF card',
                      'retrieval_aliases': ['组合采集']},
             'source_refs': [ref]}
    knowledge = DevCandidateKnowledge('candidate-dev-' + 'a' * 64, [],
                                      [pro, wrist, combo], {})
    box = DaqToolBox(knowledge=knowledge, role='internal_fae')
    assert box.dispatch('inspect_topology', {
        'query': 'EGO Pro 与 WristCam 组合采集'}).status == 'ok'
    assert box.dispatch('inspect_topology', {
        'query': 'EGO Pro 组合采集'}).status == 'not_found'
    assert box.dispatch('inspect_topology', {
        'query': 'EGO Pro 组合采集，不接 WristCam'}).status == 'not_found'


def test_candidate_scope_versions_are_exact_and_bound_to_semantic_field():
    from daq_fae.domain_tools import _candidate_query_scope_safe
    row = {'scope': {'product': 'ego-pro', 'firmware_version': '1.0.30',
                     'resolution_variant': '1600x1200'}}
    assert _candidate_query_scope_safe(row, 'EGO Pro 固件 v1.0.30 1600×1200')
    assert not _candidate_query_scope_safe(row, 'EGO Pro 固件 v1.0.3 1600×1200')
    assert not _candidate_query_scope_safe(row, 'EGO Pro Viewer v1.0.30 1600×1200')
    assert not _candidate_query_scope_safe(row, 'EGO Pro 固件 v1.0.30 1600×1300')


def test_source_checked_natural_retrieval_uses_reviewed_aliases_for_other_devices():
    ref = {'path': 'umi.md', 'sha256': 'b' * 64,
           'locator': {'kind': 'lines', 'start': 1, 'end': 8}}
    entity = {'id': 'entity:umi-finger', 'kind': 'entity', 'status': 'verified',
              'scope': {'product': 'umi-finger'},
              'data': {'name': 'UMI Finger', 'entity_type': 'device'},
              'source_refs': [ref]}
    topology = {'id': 'topology:umi-finger-local', 'kind': 'topology',
                'status': 'verified', 'scope': {'product': 'umi-finger', 'mode': 'local'},
                'data': {'members': [entity['id']], 'roles': {entity['id']: 'recorder'},
                         'connections': [], 'power': {}, 'platform': 'local',
                         'sync_target': 'none', 'storage': 'local',
                         'retrieval_aliases': ['本机离线采集']},
                'source_refs': [ref]}
    procedure = {'id': 'procedure:umi-finger-local', 'kind': 'procedure',
                 'status': 'verified', 'scope': topology['scope'],
                 'data': {'task': 'UMI Finger 本机录制',
                          'topology_id': topology['id'], 'prerequisites': [],
                          'steps': ['准备', '录制'], 'checks': ['检查'],
                          'failure_branches': [],
                          'retrieval_aliases': ['本机离线录制'],
                          'task_intents': ['recording']},
                 'source_refs': [ref]}
    knowledge = DevCandidateKnowledge('candidate-dev-' + 'a' * 64, [],
                                      [entity, topology, procedure], {})
    box = DaqToolBox(knowledge=knowledge, role='internal_fae')
    assert box.dispatch('inspect_topology', {
        'query': 'UMI Finger 的本机离线采集：供电、存储拓扑',
    }).status == 'ok'
    assert box.dispatch('lookup_procedure', {
        'entity': 'UMI Finger', 'task': '本机离线录制时怎么准备和检查？',
    }).status == 'ok'


def test_source_checked_procedure_covers_recording_intent_in_candidate_preview():
    ref = {'path': 'pro.md', 'sha256': 'a' * 64,
           'locator': {'kind': 'lines', 'start': 54, 'end': 90}}
    entity = {'id': 'entity:ego-pro', 'kind': 'entity', 'status': 'verified',
              'scope': {'product': 'ego-pro'},
              'data': {'name': 'EGO Pro', 'entity_type': 'device'}, 'source_refs': [ref]}
    topology = {'id': 'topology:ego-pro-standalone', 'kind': 'topology',
                'status': 'verified', 'scope': {'product': 'ego-pro', 'mode': 'standalone'},
                'data': {'members': [entity['id']], 'roles': {entity['id']: 'recorder'},
                         'connections': [], 'power': {}, 'platform': 'standalone',
                         'sync_target': 'local streams', 'storage': 'TF card'},
                'source_refs': [ref]}
    procedure = {'id': 'procedure:ego-pro-standalone', 'kind': 'procedure',
                 'status': 'verified', 'scope': topology['scope'],
                 'data': {'task': '录制单机 EGO Pro 数据', 'topology_id': topology['id'],
                          'prerequisites': ['断电插卡'], 'steps': ['开机', '录制', '停止'],
                          'checks': ['绿灯常亮'], 'failure_branches': [],
                          'evidence_conditions': {
                              'recording_mode': 'local_tf_card',
                              'standalone': True, 'storage': 'tf_card',
                              'steps': ['insert_card_power_off', 'power_on',
                                        'start_recording', 'stop_recording'],
                              'indicators': ['voice_prompt', 'green_led']},
                          'task_intents': ['recording', 'capture', 'local_recording']},
                 'source_refs': [ref]}
    knowledge = DevCandidateKnowledge('candidate-dev-' + 'a' * 64, [],
                                      [entity, topology, procedure], {})
    question = '一台 EGO Pro 用本机 TF 卡独立录制，怎么操作？'
    plan = prepare_turn(question, grounded_targets=knowledge.ground_question(question))
    requirement_ids = {row['id'] for row in plan.requirements
                       if row['capability'] == 'lookup_procedure'}
    assert requirement_ids
    result = DaqToolBox(question=question, requirements=plan.requirements,
                        knowledge=knowledge, role='internal_fae').dispatch(
        'lookup_procedure', {'task': '录制单机 EGO Pro 数据', 'entity': 'EGO Pro'})
    assert result.status == 'ok'
    assert set(result.content['matched_requirement_ids']) == requirement_ids
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'lookup_procedure',
            'entities': ['EGO Pro'],
            'conditions': {'recording_mode': 'local_tf_card',
                           'task': ['recording']},
        }],
    })
    scoped_plan = prepare_turn(question, request_schema=schema,
                               grounded_targets=knowledge.ground_question(question))
    scoped_result = DaqToolBox(question=question, requirements=scoped_plan.requirements,
                               knowledge=knowledge, role='internal_fae').dispatch(
        'lookup_procedure', {'task': '录制单机 EGO Pro 数据', 'entity': 'EGO Pro'})
    assert scoped_result.content['matched_requirement_ids'] == [
        item['id'] for item in scoped_plan.requirements]
    detailed_question = ('一台 EGO Pro 用本机 TF 卡独立录制：从断电插卡、开机到开始和停止录制，'
                         '应按什么顺序操作？各步的语音和绿灯状态如何核对？')
    detailed_schema = DaqRequestSchema.from_candidate(detailed_question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'lookup_procedure',
            'entities': ['EGO Pro'], 'conditions': {
                'indicators': ['voice_prompt', 'green_led'],
                'steps': ['insert_card_power_off', 'power_on',
                          'start_recording', 'stop_recording'],
                'storage': 'tf_card', 'task': 'local_recording',
                'standalone': True,
            },
        }],
    })
    detailed_plan = prepare_turn(detailed_question, request_schema=detailed_schema,
                                 grounded_targets=knowledge.ground_question(detailed_question))
    detailed_box = DaqToolBox(question=detailed_question, knowledge=knowledge,
                              role='internal_fae', requirements=detailed_plan.requirements)
    natural = detailed_box.dispatch('lookup_procedure', {
        'task': 'local_recording', 'entity': 'EGO Pro'})
    assert natural.status == 'ok'
    assert natural.content['matched_requirement_ids'] == [
        item['id'] for item in detailed_plan.requirements]
    red_question = 'EGO Pro 单机 TF 卡录制时，红色 LED 灯态表示什么？'
    red_schema = DaqRequestSchema.from_candidate(red_question, {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'lookup_procedure',
            'entities': ['EGO Pro'], 'conditions': {'indicators': ['led']},
        }],
    })
    assert red_schema.goals[0].condition_map()['indicators'] == ['red_led']
    red_plan = prepare_turn(red_question, request_schema=red_schema,
                            grounded_targets=knowledge.ground_question(red_question))
    red_result = DaqToolBox(question=red_question, knowledge=knowledge,
                            role='internal_fae', requirements=red_plan.requirements
                            ).dispatch('lookup_procedure', {
                                'task': '录制单机 EGO Pro 数据', 'entity': 'EGO Pro'})
    assert red_result.content['matched_requirement_ids'] == []
    procedure_session = DaqEvidencePolicy(allow_dev_source_check=True).begin(
        detailed_plan.evidence_requirements())
    procedure_session.observe('lookup_procedure', natural)
    assert set(procedure_session.snapshot().requirement_status.values()) == {'satisfied'}
    missing = {**procedure, 'data': {**procedure['data']}}
    missing['data'].pop('evidence_conditions')
    unbound = DevCandidateKnowledge('candidate-dev-' + 'c' * 64, [],
                                    [entity, topology, missing], {})
    unbound_result = DaqToolBox(question=detailed_question, knowledge=unbound,
                                role='internal_fae',
                                requirements=detailed_plan.requirements).dispatch(
        'lookup_procedure', {'task': 'local_recording', 'entity': 'EGO Pro'})
    assert unbound_result.status == 'ok'
    assert unbound_result.content['matched_requirement_ids'] == []
    wrong_order = [{**detailed_plan.requirements[0],
                    'conditions': {**detailed_plan.requirements[0]['conditions'],
                                   'steps': ['stop_recording', 'start_recording']}}]
    order_result = DaqToolBox(question=detailed_question, knowledge=knowledge,
                              role='internal_fae', requirements=wrong_order).dispatch(
        'lookup_procedure', {'task': 'local_recording', 'entity': 'EGO Pro'})
    assert order_result.status == 'ok'
    assert order_result.content['matched_requirement_ids'] == []
    no_green = {**procedure, 'data': {**procedure['data'],
                                     'evidence_conditions': {
                                         **procedure['data']['evidence_conditions'],
                                         'indicators': ['voice_prompt']}}}
    no_green_knowledge = DevCandidateKnowledge('candidate-dev-' + 'd' * 64, [],
                                               [entity, topology, no_green], {})
    no_green_result = DaqToolBox(question=detailed_question,
                                 knowledge=no_green_knowledge,
                                 role='internal_fae',
                                 requirements=detailed_plan.requirements).dispatch(
        'lookup_procedure', {'task': 'local_recording', 'entity': 'EGO Pro'})
    assert no_green_result.status == 'ok'
    assert no_green_result.content['matched_requirement_ids'] == []


def test_candidate_preview_rejects_authenticated_mode_and_active_pointer(monkeypatch, tmp_path):
    from daq_fae.app import create_app

    monkeypatch.setenv("DAQ_DEV_CANDIDATE_MODE", "true")
    monkeypatch.setenv("DAQ_PLATFORM_IDENTITY_ENABLED", "true")
    monkeypatch.setenv("DAQ_DATABASE_URL", "postgresql://unused/unused")
    monkeypatch.setenv("DAQ_AUTHENTICATED_CONTENT_KEYRING_FILE", str(tmp_path / "unused"))
    with pytest.raises(ValueError, match="requires_local_mode"):
        create_app(provider_mode="offline")

    monkeypatch.setenv("DAQ_PLATFORM_IDENTITY_ENABLED", "false")
    monkeypatch.setenv("DAQ_DEV_CANDIDATE_GRAPH_DIR", "unused")
    monkeypatch.setenv("DAQ_DEV_CANDIDATE_BOUND_INDEX", "unused")
    monkeypatch.setenv("DAQ_DEV_CANDIDATE_ARCHIVE", "unused")
    releases = tmp_path / "releases"
    releases.mkdir()
    (releases / "active.json").write_text("{}")
    with pytest.raises(ValueError, match="cannot_shadow_active_release"):
        create_app(provider_mode="offline", knowledge_release_root=releases)


def test_generic_variant_spec_reports_checked_scopes_without_answering():
    ref = {"path": "guide.md", "sha256": "a" * 64,
           "locator": {"kind": "lines", "start": 1, "end": 1}}
    rows = []
    for variant, value in (("1600x1200", 165), ("1920x1200", 149)):
        entity_id = "entity:ego-" + variant[:4]
        scope = {"product": "ego", "required_selectors": ["resolution_variant"],
                 "resolution_variant": variant}
        rows.extend([
            {"id": entity_id, "kind": "entity", "status": "verified",
             "scope": scope, "data": {"name": "EGO", "entity_type": "variant"},
             "source_refs": [ref]},
            {"id": "claim:ego-" + variant[:4] + "-horizontal-fov", "kind": "claim",
             "status": "verified", "scope": scope, "source_refs": [ref],
             "data": {"entity_id": entity_id, "field": "horizontal_fov",
                      "value": value, "unit": "deg", "conditions": {}}},
        ])
    knowledge = DevCandidateKnowledge("candidate-dev-" + "a" * 64, [], rows,
                                      {"水平视场角": "horizontal_fov"})
    question = "EGO 双目的水平视场角是多少？"
    plan = prepare_turn(question, grounded_targets=knowledge.ground_question(question))
    toolbox = DaqToolBox(question=question, knowledge=knowledge, role="internal_fae",
                         requirements=plan.requirements)
    result = toolbox.dispatch("lookup_spec", {"entity": "EGO 双目", "field": "horizontal_fov"})
    assert result.status == "not_found"
    assert result.content["reason"] == "variant_selector_required"
    assert result.content["source_checked_variant_scopes"] == {
        "resolution_variant": ["1600x1200", "1920x1200"]}
    assert result.content["matches"] == []
    assert result.sources == []


def test_generic_ego_resolution_questions_do_not_select_a_variant():
    ref = {'path': 'guide.md', 'sha256': 'a' * 64,
           'locator': {'kind': 'lines', 'start': 1, 'end': 1}}
    rows = []
    for variant in ('1600x1200', '1920x1200'):
        entity_id = 'entity:ego-' + variant[:4]
        scope = {'product': 'ego', 'required_selectors': ['resolution_variant'],
                 'resolution_variant': variant}
        rows.extend([
            {'id': entity_id, 'kind': 'entity', 'status': 'verified',
             'scope': scope, 'data': {'name': 'EGO', 'entity_type': 'variant'},
             'source_refs': [ref]},
            {'id': 'claim:' + entity_id + ':rgb_resolution', 'kind': 'claim',
             'status': 'verified', 'scope': scope,
             'data': {'entity_id': entity_id, 'field': 'rgb_resolution',
                      'value': variant, 'unit': 'px', 'conditions': {}},
             'source_refs': [ref]},
        ])
    knowledge = DevCandidateKnowledge('candidate-dev-' + 'a' * 64, [], rows,
                                      {'rgb分辨率': 'rgb_resolution'})
    unknown = DaqToolBox(question='泛称 EGO 能录 1600×1300 吗？',
                         knowledge=knowledge, role='internal_fae').dispatch(
        'lookup_spec', {'entity': 'EGO', 'field': 'rgb_resolution'})
    assert unknown.status == 'not_found'
    assert unknown.content['reason'] == 'variant_selector_required'
    assert unknown.content['matches'] == []
    only = DaqToolBox(question='泛称 EGO 是否只支持 1600×1200？',
                      knowledge=knowledge, role='internal_fae').dispatch(
        'lookup_spec', {'entity': 'EGO', 'field': 'rgb_resolution'})
    assert only.status == 'ok'
    assert [row['scope']['resolution_variant']
            for row in only.content['matches']] == ['1600x1200']
    assert only.content['variant_scopes_exhaustive'] is False


def test_source_checked_mode_conditions_require_explicit_user_language():
    question = '一台 EGO Pro 不连接电脑或手机，单机采集的供电、连接与存储拓扑是什么？'
    conditions = {'host_connection': 'none', 'standalone': True,
                  'topology_aspects': ['power', 'connection', 'storage']}
    candidate = {'confidence': 'high', 'goals': [{
        'kind': 'product', 'capability': 'inspect_topology',
        'entities': ['EGO Pro'], 'conditions': conditions,
    }]}
    schema = DaqRequestSchema.from_candidate(question, candidate)
    assert schema.goals[0].unmapped_condition_keys == ()
    ungrounded = DaqRequestSchema.from_candidate('EGO Pro 拓扑是什么？', candidate)
    assert set(ungrounded.goals[0].unmapped_condition_keys) == set(conditions)


def test_source_checked_recording_mode_condition_requires_explicit_tf_card():
    candidate = {'confidence': 'high', 'goals': [{
        'kind': 'product', 'capability': 'lookup_procedure',
        'entities': ['EGO Pro'],
        'conditions': {'recording_mode': 'local_tf_card', 'task': ['recording']},
    }]}
    question = '一台 EGO Pro 用本机 TF 卡独立录制时怎么操作？'
    schema = DaqRequestSchema.from_candidate(question, candidate)
    assert schema.goals[0].unmapped_condition_keys == ()
    ungrounded = DaqRequestSchema.from_candidate('EGO Pro 怎么操作？', candidate)
    # The model-inferred local TF mode is removed before retrieval; the
    # unsupported recording task remains visible as an ungrounded condition.
    assert 'recording_mode' not in ungrounded.goals[0].condition_map()
    assert set(ungrounded.goals[0].unmapped_condition_keys) == {'task'}


def test_model_topology_condition_aliases_preserve_explicit_scope():
    candidate = {'confidence': 'high', 'goals': [{
        'kind': 'product', 'capability': 'inspect_topology',
        'entities': ['EGO Pro'], 'conditions': {
            'host': 'none', 'connection_mode': 'standalone',
            'aspects': ['power_supply', 'connectivity', 'storage'],
            'task': 'standalone_capture',
        },
    }]}
    question = '一台 EGO Pro 不连接电脑或手机时，单机独立采集的供电、连接与存储拓扑是什么？'
    goal = DaqRequestSchema.from_candidate(question, candidate).goals[0]
    assert goal.unmapped_condition_keys == ()
    assert goal.condition_map() == {
        'host_connection': 'none', 'standalone': True,
        'topology_aspects': ['power', 'connection', 'storage'],
        'task': 'standalone_capture',
    }
    ungrounded = DaqRequestSchema.from_candidate(
        'EGO Pro 拓扑是什么？', candidate).goals[0]
    assert ungrounded.unmapped_condition_keys


def test_model_usage_context_alias_requires_explicit_standalone_and_no_host():
    candidate = {'confidence': 'high', 'goals': [{
        'kind': 'product', 'capability': 'inspect_topology',
        'entities': ['EGO Pro'],
        'conditions': {'usage_context': 'standalone_no_host',
                       'power': True, 'connection': True, 'storage': True},
    }]}
    question = '一台 EGO Pro 不连接电脑或手机时，单机独立采集的供电、连接与存储拓扑是什么？'
    goal = DaqRequestSchema.from_candidate(question, candidate).goals[0]
    assert goal.unmapped_condition_keys == ()
    assert goal.condition_map() == {
        'standalone': True, 'host_connection': 'none',
        'topology_aspects': ['power', 'connection', 'storage'],
    }
    no_host_unasked = DaqRequestSchema.from_candidate(
        'EGO Pro 单机采集的供电、连接与存储拓扑是什么？', candidate).goals[0]
    assert 'host_connection' in no_host_unasked.unmapped_condition_keys
    unasked_aspects = DaqRequestSchema.from_candidate(
        'EGO Pro 不连接电脑或手机时，单机采集拓扑是什么？', candidate).goals[0]
    assert 'topology_aspects' in unasked_aspects.unmapped_condition_keys
    unknown_value = {**candidate, 'goals': [{
        **candidate['goals'][0],
        'conditions': {'usage_context': 'standalone_with_hub'},
    }]}
    assert DaqRequestSchema.from_candidate(question, unknown_value).goals[0].unmapped_condition_keys
    extra_device = {**candidate, 'goals': [{
        **candidate['goals'][0], 'entities': ['EGO Pro', 'HUB'],
    }]}
    with pytest.raises(ValueError, match='entity_not_grounded'):
        DaqRequestSchema.from_candidate(question, extra_device)


def test_model_procedure_conditions_keep_step_order_and_indicators():
    candidate = {'confidence': 'high', 'goals': [{
        'kind': 'product', 'capability': 'lookup_procedure',
        'entities': ['EGO Pro'], 'conditions': {
            'indicators': ['voice_prompt', 'green_led'],
            'steps': ['insert_card_power_off', 'power_on',
                      'start_recording', 'stop_recording'],
            'storage': 'tf_card', 'task': 'local_recording',
            'standalone': True,
        },
    }]}
    question = ('一台 EGO Pro 用本机 TF 卡独立录制：从断电插卡、开机到开始和停止录制，'
                '应按什么顺序操作？各步的语音和绿灯状态如何核对？')
    goal = DaqRequestSchema.from_candidate(question, candidate).goals[0]
    assert goal.unmapped_condition_keys == ()
    assert goal.condition_map()['steps'] == [
        'insert_card_power_off', 'power_on', 'start_recording', 'stop_recording']
    ungrounded = DaqRequestSchema.from_candidate(
        'EGO Pro 怎么操作？', candidate).goals[0]
    assert ungrounded.unmapped_condition_keys


def test_model_step_value_alias_is_exact_and_does_not_add_unasked_steps():
    question = ('一台 EGO Pro 用本机 TF 卡独立录制：从断电插卡、开机到开始和停止录制，'
                '应按什么顺序操作？各步的语音和绿灯状态如何核对？')
    def goal(steps):
        return DaqRequestSchema.from_candidate(question, {
            'confidence': 'high', 'goals': [{
                'kind': 'product', 'capability': 'lookup_procedure',
                'entities': ['EGO Pro'], 'conditions': {'steps': steps},
            }],
        }).goals[0]
    aliased = goal(['power_off_insert_card', 'power_on',
                    'start_recording', 'stop_recording'])
    assert aliased.unmapped_condition_keys == ()
    assert aliased.condition_map()['steps'][0] == 'insert_card_power_off'
    assert goal(['attach_hub', 'power_on']).unmapped_condition_keys == ('steps',)
    assert goal(['stop_recording', 'start_recording']).condition_map()['steps'] == [
        'stop_recording', 'start_recording']
    unasked = DaqRequestSchema.from_candidate('EGO Pro 怎样录制？', {
        'confidence': 'high', 'goals': [{
            'kind': 'product', 'capability': 'lookup_procedure',
            'entities': ['EGO Pro'],
            'conditions': {'steps': ['power_off_insert_card']},
        }],
    }).goals[0]
    assert unasked.unmapped_condition_keys == ('steps',)


def test_explicit_single_port_scope_reaches_source_checked_power_rating():
    ref = {"path": "hub.pdf", "sha256": "a" * 64,
           "locator": {"kind": "page", "page": 4}}
    entity = {"id": "entity:hub", "kind": "entity", "status": "verified",
              "scope": {"product": "hub"},
              "data": {"name": "HUB", "entity_type": "hub"},
              "source_refs": [ref]}
    claim = {"id": "claim:hub-power", "kind": "claim", "status": "verified",
             "scope": {"product": "hub"}, "source_refs": [ref],
             "data": {"entity_id": "entity:hub", "field": "downstream_port_power_output",
                      "value": "5V/2A", "unit": "electrical_rating",
                      "conditions": {"per_port": True}}}
    knowledge = DevCandidateKnowledge("candidate-dev-" + "a" * 64,
                                      [], [entity, claim], {})
    question = "数采 HUB 单个下游端口的供电输出是多少？"
    plan = prepare_turn(question, grounded_targets=knowledge.ground_question(question))
    toolbox = DaqToolBox(question=question, knowledge=knowledge, role="internal_fae",
                         requirements=plan.requirements)
    result = toolbox.dispatch("lookup_spec", {
        "entity": "HUB", "field": "downstream_port_power_output"})
    assert result.status == "ok"
    assert result.content["matches"][0]["data"]["value"] == "5V/2A"
    assert result.content["matched_requirement_ids"] == [
        item["id"] for item in plan.requirements if item["capability"] == "lookup_spec"]
    generic_question = "数采 HUB 下游端口的供电输出是多少？"
    generic_plan = prepare_turn(generic_question,
                                grounded_targets=knowledge.ground_question(generic_question))
    generic_toolbox = DaqToolBox(question=generic_question, knowledge=knowledge,
                                 role="internal_fae", requirements=generic_plan.requirements)
    assert generic_toolbox.dispatch("lookup_spec", {
        "entity": "HUB", "field": "downstream_port_power_output"}).status == "not_found"
    assert toolbox.dispatch("lookup_spec", {
        "entity": "HUB", "field": "downstream_port_power_output",
        "conditions": {"per_port": False}}).status == "not_found"

    for aggregate_question in (
        "HUB 单个端口标称 5V/2A，四个下游端口同时供电输出是多少？",
        "HUB 单个下游端口标称 5V/2A，四口同时供电输出是多少？",
    ):
        aggregate_plan = prepare_turn(aggregate_question,
                                      grounded_targets=knowledge.ground_question(aggregate_question))
        aggregate_result = DaqToolBox(
            question=aggregate_question, knowledge=knowledge, role="internal_fae",
            requirements=aggregate_plan.requirements).dispatch("lookup_spec", {
                "entity": "HUB", "field": "downstream_port_power_output",
                "conditions": {"per_port": True}})
        assert aggregate_result.status == "ok"
        assert aggregate_result.content["matched_requirement_ids"] == []
        session = DaqEvidencePolicy(allow_dev_source_check=True).begin(
            aggregate_plan.evidence_requirements())
        session.observe("lookup_spec", aggregate_result)
        assert set(session.snapshot().requirement_status.values()) == {"unknown"}


def test_rgb_question_cannot_be_satisfied_by_mono_resolution():
    ref = {"path": "umi.pdf", "sha256": "a" * 64,
           "locator": {"kind": "page", "page": 3}}
    entity = {"id": "entity:umi-finger", "kind": "entity", "status": "verified",
              "scope": {"product": "umi-finger"},
              "data": {"name": "UMI Finger", "entity_type": "device"},
              "source_refs": [ref]}
    mono = {"id": "claim:umi-mono", "kind": "claim", "status": "verified",
            "scope": {"product": "umi-finger"}, "source_refs": [ref],
            "data": {"entity_id": "entity:umi-finger", "field": "stereo_mono_resolution",
                     "value": "1280x960", "unit": "px", "conditions": {}}}
    knowledge = DevCandidateKnowledge("candidate-dev-" + "a" * 64, [], [entity, mono], {})
    question = "UMI Finger 的 RGB 分辨率是多少？"
    plan = prepare_turn(question, grounded_targets=knowledge.ground_question(question))
    result = DaqToolBox(question=question, knowledge=knowledge, role="internal_fae",
                        requirements=plan.requirements).dispatch("lookup_spec", {
                            "entity": "UMI Finger", "field": "stereo_mono_resolution"})
    assert result.status == "ok"
    assert result.content["matched_requirement_ids"] == []


def test_frame_rate_intent_matches_fps_claim_and_model_field_alias():
    ref = {"path": "ego.pdf", "sha256": "a" * 64,
           "locator": {"kind": "page", "page": 3}}
    scope = {"product": "ego", "required_selectors": ["resolution_variant"],
             "resolution_variant": "1600x1200"}
    entity = {"id": "entity:ego-1600", "kind": "entity", "status": "verified",
              "scope": scope, "data": {"name": "EGO", "entity_type": "variant"},
              "source_refs": [ref]}
    claim = {"id": "claim:ego-fps", "kind": "claim", "status": "verified",
             "scope": scope, "source_refs": [ref],
             "data": {"entity_id": "entity:ego-1600", "field": "rgb_max_fps",
                      "value": 60, "unit": "fps",
                      "conditions": {"resolution": "1600x1200"}}}
    knowledge = DevCandidateKnowledge("candidate-dev-" + "a" * 64,
                                      [], [entity, claim],
                                      {"rgb_max_frame_rate": "rgb_max_fps"})
    question = "EGO 1600×1200 版本在 1600×1200 分辨率下的 RGB 最高帧率是多少？"
    plan = prepare_turn(question, grounded_targets=knowledge.ground_question(question))
    result = DaqToolBox(question=question, knowledge=knowledge, role="internal_fae",
                        requirements=plan.requirements).dispatch("lookup_spec", {
                            "entity": "EGO", "field": "rgb_max_frame_rate",
                            "conditions": {"resolution_variant": "1600x1200",
                                           "resolution": "1600x1200"}})
    assert result.status == "ok"
    assert result.content["matches"][0]["data"]["value"] == 60
    assert result.content["matched_requirement_ids"] == [
        item["id"] for item in plan.requirements if item["capability"] == "lookup_spec"]


def test_scoped_resolution_alias_does_not_cross_ego_variants():
    ref = {"path": "ego.pdf", "sha256": "a" * 64,
           "locator": {"kind": "page", "page": 4}}
    rows = []
    for suffix, field, value in (
        ("1600", "rgb_resolution", "1600x1200"),
        ("1920", "rgb_resolution_options", ["1920x1200", "1920x1080"]),
    ):
        scope = {"product": "ego", "required_selectors": ["resolution_variant"],
                 "resolution_variant": suffix + "x1200"}
        rows.extend([
            {"id": "entity:ego-" + suffix, "kind": "entity", "status": "verified",
             "scope": scope, "data": {"name": "EGO", "entity_type": "variant"},
             "source_refs": [ref]},
            {"id": "claim:ego-" + suffix + "-resolution", "kind": "claim",
             "status": "verified", "scope": scope, "source_refs": [ref],
             "data": {"entity_id": "entity:ego-" + suffix, "field": field,
                      "value": value, "unit": "px", "conditions": {}}},
        ])
    knowledge = DevCandidateKnowledge(
        "candidate-dev-" + "a" * 64, [], rows,
        {"rgb_resolution": "rgb_resolution"},
        scoped_field_aliases={"entity:ego-1920": {
            "rgb_resolution": "rgb_resolution_options"}},
    )
    for suffix, expected in (
        ("1600", "1600x1200"),
        ("1920", ["1920x1200", "1920x1080"]),
    ):
        question = f"EGO 双目 {suffix}×1200 版本的 RGB 分辨率是多少？"
        plan = prepare_turn(question, grounded_targets=knowledge.ground_question(question))
        result = DaqToolBox(question=question, knowledge=knowledge,
                            role="internal_fae", requirements=plan.requirements).dispatch(
                                "lookup_spec", {"entity": "EGO", "field": "rgb_resolution"})
        assert result.status == "ok"
        assert result.content["matches"][0]["data"]["value"] == expected
    generic = DaqToolBox(question="EGO 双目的 RGB 分辨率是多少？",
                         knowledge=knowledge, role="internal_fae").dispatch(
                             "lookup_spec", {"entity": "EGO", "field": "rgb_resolution"})
    assert generic.status == "not_found"
    assert generic.content["source_checked_variant_scopes"] == {
        "resolution_variant": ["1600x1200", "1920x1200"]}


def test_resolution_measurement_condition_corrects_model_variant_key_only_when_equal():
    ref = {"path": "ego-pro.pdf", "sha256": "a" * 64,
           "locator": {"kind": "page", "page": 4}}
    entity = {"id": "entity:ego-pro", "kind": "entity", "status": "verified",
              "scope": {"product": "ego-pro"},
              "data": {"name": "EGO Pro", "entity_type": "device"},
              "source_refs": [ref]}
    claim = {"id": "claim:ego-pro-fps", "kind": "claim", "status": "verified",
             "scope": {"product": "ego-pro"}, "source_refs": [ref],
             "data": {"entity_id": "entity:ego-pro", "field": "rgb_max_fps",
                      "value": 60, "unit": "fps",
                      "conditions": {"resolution": "1600x1200"}}}
    knowledge = DevCandidateKnowledge("candidate-dev-" + "a" * 64,
                                      [], [entity, claim],
                                      {"rgb_max_frame_rate": "rgb_max_fps"})
    question = "EGO Pro 在 1600×1200 分辨率下的 RGB 最高帧率是多少？"
    plan = prepare_turn(question, grounded_targets=knowledge.ground_question(question))
    toolbox = DaqToolBox(question=question, knowledge=knowledge,
                         role="internal_fae", requirements=plan.requirements)
    result = toolbox.dispatch("lookup_spec", {
        "entity": "EGO Pro", "field": "rgb_max_frame_rate",
        "conditions": {"resolution_variant": "1600x1200"}})
    assert result.status == "ok"
    assert result.content["matches"][0]["data"]["value"] == 60
    assert result.content["matched_requirement_ids"] == [
        item["id"] for item in plan.requirements if item["capability"] == "lookup_spec"]
    assert toolbox.dispatch("lookup_spec", {
        "entity": "EGO Pro", "field": "rgb_max_frame_rate",
        "conditions": {"resolution_variant": "1920x1200"}}).status == "not_found"


def test_exact_longer_product_name_wins_over_shorter_variant_name():
    ref = {"path": "ego.pdf", "sha256": "a" * 64,
           "locator": {"kind": "page", "page": 4}}
    rows = [
        {"id": "entity:ego-1600", "kind": "entity", "status": "verified",
         "scope": {"product": "ego", "required_selectors": ["resolution_variant"],
                   "resolution_variant": "1600x1200"},
         "data": {"name": "EGO", "entity_type": "variant"}, "source_refs": [ref]},
        {"id": "entity:ego-pro", "kind": "entity", "status": "verified",
         "scope": {"product": "ego-pro"},
         "data": {"name": "EGO Pro", "entity_type": "device"}, "source_refs": [ref]},
    ]
    knowledge = DevCandidateKnowledge("candidate-dev-" + "a" * 64, [], rows, {})
    assert knowledge.ground_question(
        "EGO Pro 在 1600×1200 分辨率下的 RGB 帧率是多少？") == {
            "entities": ["entity:ego-pro"], "conditions": {}}
    assert knowledge.ground_question(
        "EGO Pro 和 EGO 双目 1600×1200 的帧率分别是多少？") == {}
    other = {"id": "entity:umi-vision-module", "kind": "entity",
             "status": "verified", "scope": {"product": "umi-vision-module"},
             "data": {"name": "UMI 视觉模块", "entity_type": "component"},
             "source_refs": [ref]}
    finger = {"id": "entity:umi-finger", "kind": "entity",
              "status": "verified", "scope": {"product": "umi-finger"},
              "data": {"name": "UMI Finger", "entity_type": "device"},
              "source_refs": [ref]}
    umi = DevCandidateKnowledge("candidate-dev-" + "a" * 64, [], [other, finger], {})
    assert umi.ground_question("比较 UMI Finger 和 UMI 视觉模块的 Mono 分辨率") == {}


def test_grounded_entity_accepts_its_unique_id_suffix_as_model_tool_argument():
    ref = {"path": "umi.pdf", "sha256": "a" * 64,
           "locator": {"kind": "page", "page": 4}}
    entity = {"id": "entity:umi-vision-module", "kind": "entity",
              "status": "verified", "scope": {"product": "umi-vision-module"},
              "data": {"name": "UMI 视觉模块", "entity_type": "component"},
              "source_refs": [ref]}
    claim = {"id": "claim:umi-mono-resolution", "kind": "claim",
             "status": "verified", "scope": {"product": "umi-vision-module"},
             "source_refs": [ref],
             "data": {"entity_id": "entity:umi-vision-module",
                      "field": "stereo_mono_resolution", "value": "1280x960",
                      "unit": "px", "conditions": {}}}
    knowledge = DevCandidateKnowledge("candidate-dev-" + "a" * 64,
                                      [], [entity, claim], {})
    question = "UMI 视觉模块的双目 Mono 分辨率是多少？"
    plan = prepare_turn(question, grounded_targets=knowledge.ground_question(question))
    toolbox = DaqToolBox(question=question, knowledge=knowledge,
                         role="internal_fae", requirements=plan.requirements)
    result = toolbox.dispatch("lookup_spec", {
        "entity": "umi-vision-module", "field": "stereo_mono_resolution"})
    assert result.status == "ok"
    assert result.content["matched_requirement_ids"] == [
        item["id"] for item in plan.requirements if item["capability"] == "lookup_spec"]
    assert toolbox.dispatch("lookup_spec", {
        "entity": "umi-finger", "field": "stereo_mono_resolution"}).status == "not_found"


@pytest.mark.parametrize("field", ["mono_stereo_baseline", "mono_baseline",
                                     "stereo_baseline", "baseline"])
def test_source_checked_mono_baseline_accepts_model_field_variants(field):
    ref = {"path": "guide.md", "sha256": "a" * 64,
           "locator": {"kind": "lines", "start": 1, "end": 1}}
    entity = {"id": "entity:umi-finger", "kind": "entity", "status": "verified",
              "scope": {"product": "umi-finger"},
              "data": {"name": "UMI Finger", "entity_type": "device"},
              "source_refs": [ref]}
    claim = {"id": "claim:umi-finger-mono-baseline", "kind": "claim",
             "status": "verified", "scope": entity["scope"], "source_refs": [ref],
             "data": {"entity_id": entity["id"], "field": "stereo_mono_baseline",
                      "value": 50, "unit": "mm", "conditions": {}}}
    aliases = {alias: "stereo_mono_baseline" for alias in
               ("mono_stereo_baseline", "mono_baseline", "stereo_baseline", "baseline")}
    knowledge = DevCandidateKnowledge("candidate-dev-" + "a" * 64, [],
                                      [entity, claim], aliases)
    question = "UMI Finger 的双目 Mono 基线是多少？"
    plan = prepare_turn(question, grounded_targets=knowledge.ground_question(question))
    result = DaqToolBox(question=question, knowledge=knowledge, role="internal_fae",
                        requirements=plan.requirements).dispatch(
                            "lookup_spec", {"entity": "UMI Finger", "field": field})
    assert result.status == "ok"
    assert result.content["matches"][0]["data"]["value"] == 50
    assert result.content["matched_requirement_ids"]


def test_dev_source_check_ledger_declares_mono_baseline_model_aliases():
    ledger = json.loads((Path(__file__).resolve().parents[1] /
                         "review_recipes/dev_source_checked_v1.json").read_text())
    aliases = set(ledger["field_aliases"]["stereo_mono_baseline"])
    assert {"mono_stereo_baseline", "mono_baseline", "stereo_baseline",
            "baseline"} <= aliases
