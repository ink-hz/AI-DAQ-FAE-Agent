from src.agent.loop.answer_contract import AnswerSubmission
from src.agent.loop.tools import ToolResult

from daq_fae.domain_evidence import DaqEvidencePolicy, question_requirements
from daq_fae.domain_tools import DaqToolBox
from daq_fae.service_contract import ServiceContract


def test_service_contract_satisfies_only_service_goals():
    contract = ServiceContract.for_release(None, role='internal_fae',
                                           runtime_release='runtime-dev')
    requirements = {'requirements': [
        {'id': 'service', 'capability': 'service_identity',
         'evidence_class': 'service_contract'},
    ]}
    session = DaqEvidencePolicy(service_contract=contract).begin(requirements)
    assert session.snapshot().requirement_status == {'service': 'satisfied'}
    assert session.evaluate(AnswerSubmission(
        outcome='resolved', conclusion='我是内部数采 FAE 助手。')).action == 'allow'


def test_answer_contract_requests_rewrite_of_literal_newline_escape():
    contract = ServiceContract.for_release(None, role='internal_fae',
                                           runtime_release='runtime-dev')
    session = DaqEvidencePolicy(service_contract=contract).begin({'requirements': [
        {'id': 'service', 'capability': 'service_identity',
         'evidence_class': 'service_contract'},
    ]})
    decision = session.evaluate(AnswerSubmission(
        outcome='resolved', conclusion='步骤：\\n1. 断电插卡。\\n2. 开机。'))
    assert decision.action == 'request_evidence'
    assert decision.reason_code == 'daq_literal_escape_in_answer'
    assert session.evaluate(AnswerSubmission(
        outcome='resolved', conclusion='步骤：\n1. 断电插卡。\n2. 开机。')).action == 'allow'


def test_answer_contract_hides_source_file_names_but_keeps_output_names():
    contract = ServiceContract.for_release(None, role='internal_fae',
                                           runtime_release='runtime-dev')
    session = DaqEvidencePolicy(service_contract=contract).begin({'requirements': [
        {'id': 'service', 'capability': 'service_identity',
         'evidence_class': 'service_contract'},
    ]})
    decision = session.evaluate(AnswerSubmission(
        outcome='resolved', conclusion='包内有 EgoLowBleDemo.cpp 示例源码。'))
    assert decision.action == 'request_evidence'
    assert decision.reason_code == 'daq_source_filename_in_answer'
    assert session.evaluate(AnswerSubmission(
        outcome='resolved', conclusion='示例会导出 align_calib.yaml。')).action == 'allow'


def test_english_answer_contract_retries_chinese_body_not_only_headings():
    contract = ServiceContract.for_release(None, role='internal_fae',
                                           runtime_release='runtime-dev')
    session = DaqEvidencePolicy(service_contract=contract,
                                answer_language='en').begin({'requirements': [
        {'id': 'service', 'capability': 'service_identity',
         'evidence_class': 'service_contract'},
    ]})
    mixed = session.evaluate(AnswerSubmission(
        outcome='resolved', conclusion='I am the DAQ FAE assistant.',
        basis='当前资料只覆盖产品规格。'))
    assert mixed.action == 'request_evidence'
    assert mixed.reason_code == 'daq_answer_language_mismatch'
    assert session.evaluate(AnswerSubmission(
        outcome='resolved', conclusion='I am the DAQ FAE assistant.',
        basis='The current release covers products and specifications.')).action == 'allow'


def test_mixed_service_and_product_cannot_resolve_on_service_contract_alone():
    contract = ServiceContract.for_release(None, role='internal_fae',
                                           runtime_release='runtime-dev')
    session = DaqEvidencePolicy(service_contract=contract).begin({'requirements': [
        {'id': 'service', 'capability': 'service_identity',
         'evidence_class': 'service_contract'},
        {'id': 'spec', 'capability': 'lookup_spec',
         'evidence_class': 'governed_required'},
    ]})
    assert session.snapshot().requirement_status == {
        'service': 'satisfied', 'spec': 'unknown',
    }
    assert session.evaluate(AnswerSubmission(
        outcome='resolved', conclusion='我是数采助手，整机精度为 1 mm。')).action != 'allow'
    session.observe('lookup_spec', ToolResult(
        status='not_found', content={'matched_requirement_ids': ['spec']},
    ))
    assert session.snapshot().requirement_status['spec'] == 'missing'
    assert session.evaluate(AnswerSubmission(
        outcome='safe_abstained', basis='我是内部数采 FAE 助手。',
        missing='该整机精度缺少已发布证据。')).action == 'allow'


def test_empty_release_exposes_full_domain_tool_contract_without_camera_facts():
    toolbox = DaqToolBox().with_request_context("EG-DB 如何同步？")
    names = {item["function"]["name"] for item in toolbox.tool_schemas()}
    assert names == {
        "resolve_entity", "lookup_spec", "inspect_topology",
        "lookup_procedure", "check_software_support", "search_knowledge", "read_doc",
        "sdk_evidence", "official_links", "session_state",
        "catalog", "selection", "experience", "risk",
    }
    for name in names - {"session_state"}:
        result = toolbox.dispatch(name, {})
        assert result.status == "not_found"
        assert result.sources == []
        assert result.content["reason"] == "empty_knowledge_release"
    state = toolbox.dispatch("session_state", {})
    assert state.status == "ok"
    assert state.sources == []
    assert state.content["question"] == "EG-DB 如何同步？"
    assert toolbox.dispatch("filter_models", {}).status == "tool_error"


def test_question_requirement_is_explicit_and_not_satisfied_by_absence():
    requirements = question_requirements("EGO Pro 和 UMI 怎么配对？")
    session = DaqEvidencePolicy().begin(requirements)
    snapshot = session.snapshot()
    assert snapshot.planned_capabilities == ("search_knowledge",)
    assert snapshot.requirement_status == {"question_evidence": "unknown"}
    assert session.evaluate(AnswerSubmission(outcome="resolved", conclusion="可以配对")).action == "request_evidence"
    session.observe("search_knowledge", ToolResult(
        status="not_found", content={"reason": "empty_knowledge_release", "matches": []},
    ))
    assert session.snapshot().requirement_status == {"question_evidence": "missing"}
    assert session.evaluate(AnswerSubmission(outcome="safe_abstained", missing="缺少已审核资料")).action == "allow"
    assert session.evaluate(AnswerSubmission(outcome="resolved", conclusion="可以配对")).action == "reject"


def test_partial_answer_cannot_repeat_prior_product_fact_without_current_source():
    session = DaqEvidencePolicy().begin({'requirements': [
        {'id': 'mode', 'capability': 'lookup_spec'},
    ]})
    session.observe('lookup_spec', ToolResult(
        status='not_found', content={'matched_requirement_ids': ['mode']},
    ))
    decision = session.evaluate(AnswerSubmission(
        outcome='safe_abstained',
        basis='上一轮查到该产品在另一个分辨率下是 30 fps。',
        missing='本轮目标模式的帧率缺少已核实资料。',
    ))
    assert decision.action == 'request_evidence'
    assert decision.reason_code == 'daq_partial_basis_without_current_source'
    assert session.evaluate(AnswerSubmission(
        outcome='safe_abstained', missing='本轮目标模式的帧率缺少已核实资料。',
    )).action == 'allow'


def test_unrelated_entity_source_does_not_support_partial_sdk_basis():
    session = DaqEvidencePolicy().begin({'requirements': [
        {'id': 'support', 'capability': 'check_software_support'},
    ]})
    session.observe('resolve_entity', ToolResult(
        status='ok', content={'matches': [{'id': 'entity:ego-pro'}]},
        sources=[{'type': 'daq_governed_claim', 'verification_status': 'verified',
                  'release_id': 'kb-v1', 'source_id': 'entity:ego-pro'}],
    ))
    session.observe('check_software_support', ToolResult(
        status='not_found', content={'matched_requirement_ids': ['support']},
    ))
    decision = session.evaluate(AnswerSubmission(
        outcome='safe_abstained',
        basis='上一轮的 SDK 示例检查读写权限和支持模式位图。',
        missing='当前设备组合的同步支持没有证据。',
    ))
    assert decision.action == 'request_evidence'
    assert decision.reason_code == 'daq_partial_basis_without_current_source'


def test_source_scope_wording_is_reviewed_offline_not_rewritten_by_policy():
    session = DaqEvidencePolicy().begin({'requirements': [
        {'id': 'identity', 'capability': 'resolve_entity'},
    ]})
    session.observe('resolve_entity', ToolResult(
        status='ok', content={'matched_requirement_ids': ['identity']},
        sources=[{'type': 'daq_governed_claim', 'verification_status': 'verified',
                  'release_id': 'knowledge-v1', 'source_id': 'entity:ego-pro'}],
    ))
    decision = session.evaluate(AnswerSubmission(
        outcome='safe_abstained',
        basis='当前发布资料里找不到这个型号。',
        missing='新型号关系尚未核实。'))
    assert decision.action == 'allow'
    assert session.evaluate(AnswerSubmission(
        outcome='safe_abstained',
        basis='本轮返回的实体记录没有列出该型号作为别名。',
        missing='新型号关系尚未核实。',
    )).action == 'allow'


def test_partial_spec_values_are_semantic_review_obligations_not_runtime_words():
    session = DaqEvidencePolicy().begin({'requirements': [
        {'id': 'fov', 'capability': 'lookup_spec', 'field': 'horizontal_fov'},
    ]})
    for variant, value in [('1600x1200', 165), ('1920x1200', 149)]:
        session.observe('lookup_spec', ToolResult(
            status='ok', content={'matches': [{
                'kind': 'claim', 'status': 'verified',
                'scope': {'resolution_variant': variant},
                'data': {'field': 'horizontal_fov', 'value': value,
                         'unit': 'deg', 'conditions': {}},
            }], 'matched_requirement_ids': []},
            sources=[{'type': 'daq_governed_claim',
                      'verification_status': 'verified',
                      'release_id': 'knowledge-v1',
                      'source_id': f'claim:fov-{variant}'}],
        ))
    decision = session.evaluate(AnswerSubmission(
        outcome='safe_abstained', basis='两个变体各有视场角记录。',
        missing='未指定适用产品变体。'))
    assert decision.action == 'allow'
    assert session.evaluate(AnswerSubmission(
        outcome='safe_abstained',
        basis='1600×1200 版为 165°，1920×1200 版为 149°。',
        missing='请确认适用产品变体。',
    )).action == 'allow'


def test_partial_spec_answer_reports_known_conditioned_value():
    session = DaqEvidencePolicy().begin({'requirements': [
        {'id': 'target_mode', 'capability': 'lookup_spec',
         'field': 'rgb_max_frame_rate'},
    ]})
    session.observe('lookup_spec', ToolResult(
        status='not_found', content={'matched_requirement_ids': ['target_mode']},
    ))
    session.observe('lookup_spec', ToolResult(
        status='ok', content={'matches': [{
            'kind': 'claim', 'status': 'verified',
            'scope': {'resolution_variant': '1920x1200'},
            'data': {'field': 'rgb_max_fps', 'value': 30, 'unit': 'fps',
                     'conditions': {'resolution': '1920x1200'}},
        }], 'matched_requirement_ids': []},
        sources=[{'type': 'daq_governed_claim', 'verification_status': 'verified',
                  'release_id': 'knowledge-v1', 'source_id': 'claim:fps-1920'}],
    ))
    decision = session.evaluate(AnswerSubmission(
        outcome='safe_abstained', missing='1920×1080 下的帧率未核实。',
    ))
    assert decision.action == 'allow'
    assert session.evaluate(AnswerSubmission(
        outcome='safe_abstained',
        basis='1920×1200 变体在 1920×1200 分辨率下的标称最大帧率为 30 fps。',
        missing='1920×1080 下的帧率未核实。',
    )).action == 'allow'


def test_alternative_resolution_numeric_source_cannot_certify_target_mode_value():
    session = DaqEvidencePolicy(question=(
        'EGO 1920 版在 RGB 1920×1080 模式能达到 30 fps 吗？')).begin({
        'requirements': [{
            'id': 'target_mode', 'capability': 'lookup_spec',
            'field': 'rgb_max_frame_rate', 'entities': ['entity:ego-1920'],
            'conditions': {'resolution_variant': '1920x1200',
                           'resolution': '1920x1080'},
        }],
    })
    session.observe('lookup_spec', ToolResult(
        status='not_found', content={'matched_requirement_ids': ['target_mode']}))
    session.observe('lookup_spec', ToolResult(
        status='ok', content={'matches': [{
            'kind': 'claim', 'status': 'verified',
            'scope': {'resolution_variant': '1920x1200'},
            'data': {'entity_id': 'entity:ego-1920',
                     'field': 'rgb_max_fps', 'value': 30, 'unit': 'fps',
                     'conditions': {'resolution': '1920x1200'}},
        }], 'matched_requirement_ids': []},
        sources=[{'type': 'daq_governed_claim', 'verification_status': 'verified',
                  'release_id': 'knowledge-v1',
                  'source_id': 'claim:ego-1920-max-fps'}],
    ))
    wrong = session.evaluate(AnswerSubmission(
        outcome='safe_abstained',
        basis='1920×1080 模式最大帧率是 30 fps。',
        missing='1920×1080 的实际取流能力还未验证。'))
    assert wrong.action == 'request_evidence'
    assert wrong.reason_code == 'daq_partial_numeric_measurement_scope_mismatch'
    vague = session.evaluate(AnswerSubmission(
        outcome='safe_abstained', basis='本轮核实最大帧率为 30 fps。',
        missing='1920×1080 条件下尚未核实。'))
    assert vague.reason_code == 'daq_partial_numeric_measurement_scope_mismatch'
    for basis in (
        '1920×1080 模式最大 30fps下仍可取流。',
        '1920×1080 模式最大 30 fps下仍可取流。',
        '1920×1080 模式最大 30 帧/秒。',
    ):
        decision = session.evaluate(AnswerSubmission(
            outcome='safe_abstained', basis=basis,
            missing='1920×1080 条件下尚未核实。'))
        assert decision.reason_code == 'daq_partial_numeric_measurement_scope_mismatch'
    correct = session.evaluate(AnswerSubmission(
        outcome='safe_abstained',
        basis='1920×1200 模式的标称最大帧率是 30 fps。',
        missing='1920×1080 条件下尚未核实。'))
    assert correct.action == 'allow'
    scoped_unknown = session.evaluate(AnswerSubmission(
        outcome='safe_abstained',
        basis='1920×1200 模式的标称最大帧率是 30 fps；'
              '1920×1080 模式能否达到 30 fps 仍未知。',
        missing='1920×1080 条件下尚未核实。'))
    assert scoped_unknown.action == 'allow'
    for basis in (
        '1920×1080 没有对应帧率记录（1920×1200 标称最大帧率为 30 fps）。',
        '1920×1080 无帧率数据（1920×1200 模式最大帧率为 30 fps）。',
    ):
        assert session.evaluate(AnswerSubmission(
            outcome='safe_abstained', basis=basis,
            missing='1920×1080 条件下尚未核实。')).action == 'allow'
    for basis in (
        '1920×1080 模式最大帧率为 30 fps（待核实）。',
        '1920×1080 模式最大帧率是 30 fps，尚未核实。',
        '1920×1080 模式最大帧率是 60 fps。',
        '1920×1080 模式最大帧率是 29 fps。',
        '1920×1080 最高可达 30 fps（待核实）。',
        '1920×1080 下可达到 30 fps（待核实）。',
        '1920×1080 支持 30 fps（待核实）。',
        '1920×1080：30 fps（待核实）。',
        '1920×1080 = 30 fps（待核实）。',
        '1920×1080 暂填 30 fps（待核实）。',
        '1920×1080 不能确认，但最大帧率为 30 fps（待核实）。',
        '1920×1080 不能确认但最大帧率为 30 fps（待核实）。',
        '1920×1080 最大帧率是 30 fps，测量分辨率是 1920×1200。',
        '1920×1080 未核实，但表中填 30 fps。',
        '1920×1080 尚未核实；该栏填 30 fps。',
        '1920×1080 未核实；可以填 30 fps。',
    ):
        decision = session.evaluate(AnswerSubmission(
            outcome='safe_abstained', basis=basis,
            missing='目标模式未知。'))
        assert decision.reason_code == 'daq_partial_numeric_measurement_scope_mismatch'


def test_alternative_resolution_verified_alias_lookup_can_support_partial_basis():
    session = DaqEvidencePolicy(question=(
        'EGO 双目 1920×1200 版本在 1920×1080 模式能填 30 fps 吗？')).begin({
        'requirements': [{
            'id': 'target_mode', 'capability': 'lookup_spec',
            'field': 'rgb_max_frame_rate', 'entities': ['EGO 双目'],
            'conditions': {'resolution_variant': '1920x1200',
                           'resolution': '1920x1080'},
        }],
    })
    session.observe('lookup_spec', ToolResult(
        status='not_found', content={'matched_requirement_ids': ['target_mode']}))
    checked = ToolResult(status='ok', content={
        'query_entity': 'EGO 双目',
        'matches': [{
            'kind': 'claim', 'status': 'verified',
            'scope': {'resolution_variant': '1920x1200'},
            'data': {'entity_id': 'entity:ego-1920', 'field': 'rgb_max_fps',
                     'value': 30, 'unit': 'fps',
                     'conditions': {'resolution': '1920x1200'}},
        }], 'matched_requirement_ids': [],
    }, sources=[{'type': 'daq_governed_claim',
                 'verification_status': 'verified', 'release_id': 'knowledge-v1',
                 'source_id': 'claim:ego-1920-max-fps'}])
    session.observe('lookup_spec', checked)
    assert session.evaluate(AnswerSubmission(
        outcome='safe_abstained',
        basis='EGO 双目 1920×1200 版在 1920×1200 模式的最大帧率为 30 fps。',
        missing='1920×1080 模式帧率尚未核实。')).action == 'allow'
    assert session.evaluate(AnswerSubmission(
        outcome='safe_abstained', basis='1920×1080 最大帧率是 30 fps。',
        missing='目标模式未知。')).reason_code == 'daq_partial_numeric_measurement_scope_mismatch'
    for field in ('cautions', 'next_steps', 'pending_confirmation',
                  'unresolved_constraints'):
        decision = session.evaluate(AnswerSubmission(
            outcome='safe_abstained', missing='目标模式未知。',
            **{field: '把 1920×1080 模式最大帧率填为 30 fps。'}))
        assert decision.reason_code == 'daq_partial_numeric_measurement_scope_mismatch'


def test_partial_variant_comparison_can_state_verified_target_resolution_fps():
    session = DaqEvidencePolicy(question=(
        'RGB 1920×1200 和 60 fps 必须同时满足，EGO 双目两版有吗？')).begin({
        'requirements': [{
            'id': 'target_mode', 'capability': 'lookup_spec',
            'field': 'rgb_max_frame_rate', 'entities': ['EGO 双目'],
            'conditions': {'resolution': '1920x1200'},
        }],
    })
    session.observe('lookup_spec', ToolResult(
        status='not_found', content={'matched_requirement_ids': ['target_mode']}))
    for variant, value in (('1920x1200', 30), ('1600x1200', 60)):
        session.observe('lookup_spec', ToolResult(
            status='ok', content={'query_entity': 'EGO 双目', 'matches': [{
                'kind': 'claim', 'status': 'verified',
                'scope': {'resolution_variant': variant},
                'data': {'entity_id': f'entity:ego-{variant[:4]}',
                         'field': 'rgb_max_fps', 'value': value, 'unit': 'fps',
                         'conditions': {'resolution': variant}},
            }], 'matched_requirement_ids': []},
            sources=[{'type': 'daq_governed_claim',
                      'verification_status': 'verified',
                      'release_id': 'knowledge-v1',
                      'source_id': f'claim:ego-{variant[:4]}-max-fps'}],
        ))
    assert session.evaluate(AnswerSubmission(
        outcome='safe_abstained',
        basis='EGO 1920×1200 版在 1920×1200 模式的最大帧率为 30 fps。',
        missing='目标帧率硬条件未获证。')).action == 'allow'
    assert session.evaluate(AnswerSubmission(
        outcome='safe_abstained', missing='60 fps 硬条件未获证。')).action == 'allow'
    assert session.evaluate(AnswerSubmission(
        outcome='safe_abstained',
        basis='1920×1200 版在 1920×1200 下达不到 60 fps。',
        unresolved_constraints='RGB 1920×1200 和 60 fps 必须同时满足。',
    )).action == 'allow'
    assert session.evaluate(AnswerSubmission(
        outcome='safe_abstained',
        basis=('EGO 1920×1200 版在 1920×1200 下最大帧率为 30 fps，'
               '低于 60 fps 的要求；两版数据分别为 30 fps@1920×1200、'
               '60 fps@1600×1200，不是同一分辨率下从 30 变成 60。'),
        next_steps=('建议找在 RGB 1920×1200 下有 60 fps 已核实规格的'
                    '其他候选产品，或拿到 1920×1200@60 fps 的正式规格。'),
        missing='没有查到 RGB 1920×1200 下可达 60 fps 的规格记录。',
    )).action == 'allow'
    assert session.evaluate(AnswerSubmission(
        outcome='safe_abstained',
        basis=('EGO 1920×1200 版在 1920×1200 下的最大帧率为 30 fps。'
               '两个数值分别是 30 fps 对应 1920×1200，'
               '60 fps 对应 1600×1200。'),
        next_steps=('只在已核实 RGB 1920×1200 下最大帧率 ≥ 60 fps 的'
                    '其他候选里继续找。'),
        missing='两版都没有 RGB 1920×1200 且 60 fps 的核实记录。',
    )).action == 'allow'
    assert session.evaluate(AnswerSubmission(
        outcome='safe_abstained',
        basis=('1920×1200 版 RGB 最大帧率为 30 fps，测量分辨率是 '
               '1920×1200；1600×1200 版 RGB 最大帧率为 60 fps，'
               '测量分辨率是 1600×1200。'),
        missing='1920×1200 版最大帧率 30 fps（测量分辨率 1920×1200）。',
    )).action == 'allow'
    assert session.evaluate(AnswerSubmission(
        outcome='safe_abstained',
        basis='RGB 最大帧率为 30 fps，测量分辨率是 1920×1200。',
        missing='最大帧率 30 fps（测量分辨率 1920×1200）。',
    )).action == 'allow'
    assert session.evaluate(AnswerSubmission(
        outcome='safe_abstained',
        basis='EGO 1920×1200 版在 1920×1200 模式的最大帧率为 60 fps。',
        missing='硬条件未知。')).reason_code == (
            'daq_partial_numeric_measurement_scope_mismatch')
    for basis in (
        'EGO 1600×1200 版在 1920×1200 模式最大帧率为 30 fps。',
        'EGO 1920×1200 版在 1600×1200 模式最大帧率为 60 fps。',
    ):
        assert session.evaluate(AnswerSubmission(
            outcome='safe_abstained', basis=basis,
            missing='硬条件未知。')).reason_code == (
                'daq_partial_numeric_measurement_scope_mismatch')


def test_wrong_variant_numeric_source_cannot_certify_partial_spec_basis():
    session = DaqEvidencePolicy(question=(
        'EGO 双目 1920×1200 版本在 1920×1080 模式能填 30 fps 吗？')).begin({
        'requirements': [{
            'id': 'target_mode', 'capability': 'lookup_spec',
            'field': 'rgb_max_frame_rate', 'entities': ['entity:ego-1920'],
            'conditions': {'resolution_variant': '1920x1200',
                           'resolution': '1920x1080'},
        }],
    })
    session.observe('lookup_spec', ToolResult(
        status='ok', content={'matches': [{
            'kind': 'claim', 'status': 'verified',
            'scope': {'resolution_variant': '1600x1200'},
            'data': {'entity_id': 'entity:ego-1600', 'field': 'rgb_max_fps',
                     'value': 60, 'unit': 'fps',
                     'conditions': {'resolution': '1600x1200'}},
        }], 'matched_requirement_ids': []},
        sources=[{'type': 'daq_governed_claim', 'verification_status': 'verified',
                  'release_id': 'knowledge-v1',
                  'source_id': 'claim:ego-1600-max-fps'}],
    ))
    decision = session.evaluate(AnswerSubmission(
        outcome='safe_abstained', basis='本轮核实 60 fps。',
        missing='目标模式的帧率未核实。'))
    assert decision.action == 'request_evidence'
    assert decision.reason_code == 'daq_partial_basis_without_current_source'


def test_ambiguous_reviewed_alias_can_report_each_checked_variant_value():
    session = DaqEvidencePolicy(question='EGO 双目视场角是多少？').begin({
        'requirements': [{'id': 'fov', 'capability': 'lookup_spec',
                          'field': 'horizontal_fov', 'entities': ['EGO 双目']}],
    })
    session.observe('resolve_entity', ToolResult(
        status='ok', content={'matches': [
            {'id': 'entity:ego-1600', 'kind': 'entity', 'data': {
                'name': 'EGO 1600', 'aliases': ['EGO 双目']}},
            {'id': 'entity:ego-1920', 'kind': 'entity', 'data': {
                'name': 'EGO 1920', 'aliases': ['EGO 双目']}},
        ], 'matched_requirement_ids': []},
        sources=[{'type': 'daq_governed_claim', 'verification_status': 'verified',
                  'release_id': 'knowledge-v1', 'source_id': 'entity:ego-1600'}],
    ))
    session.observe('lookup_spec', ToolResult(
        status='ok', content={'matches': [
            {'kind': 'claim', 'status': 'verified',
             'scope': {'resolution_variant': '1600x1200'},
             'data': {'entity_id': 'entity:ego-1600', 'field': 'horizontal_fov',
                      'value': 165, 'unit': 'deg', 'conditions': {}}},
        ], 'matched_requirement_ids': []},
        sources=[{'type': 'daq_governed_claim', 'verification_status': 'verified',
                  'release_id': 'knowledge-v1', 'source_id': 'claim:fov-1600'}],
    ))
    assert session.evaluate(AnswerSubmission(
        outcome='safe_abstained', basis='1600×1200 版水平视场角为 165°。',
        missing='请确认设备变体。')).action == 'allow'


def test_combo_abstention_reports_checked_hub_capacity_premise():
    session = DaqEvidencePolicy(question=(
        '一台 EGO Pro 加两台 WristCam 接数采 HUB，4 个下游口看着够，'
        '能保证接线、供电和同步录制吗？')).begin({'requirements': [
            {'id': 'topology', 'capability': 'inspect_topology'},
        ]})
    session.observe('inspect_topology', ToolResult(
        status='not_found', content={'matched_requirement_ids': ['topology']}))
    session.observe('lookup_spec', ToolResult(
        status='ok', content={'matches': [{
            'kind': 'claim', 'status': 'verified', 'scope': {'product': 'hub'},
            'data': {'field': 'downstream_port_count', 'value': 4,
                     'unit': 'port', 'conditions': {}},
        }], 'matched_requirement_ids': []},
        sources=[{'type': 'daq_governed_claim', 'verification_status': 'verified',
                  'release_id': 'knowledge-v1', 'source_id': 'claim:hub-ports'}],
    ))
    decision = session.evaluate(AnswerSubmission(
        outcome='safe_abstained',
        cautions='“4 个下游口看着够”只是用户的初步判断。',
        missing='实际占口、供电和同步组合证据未知。',
    ))
    assert decision.action == 'allow'
    assert session.evaluate(AnswerSubmission(
        outcome='safe_abstained',
        basis='本轮核实数采 HUB 有 4 个下游口。',
        missing='实际占口、供电和同步组合证据未知。',
    )).action == 'allow'


def test_unrelated_hub_capacity_cannot_certify_daq_hub_partial_basis():
    session = DaqEvidencePolicy(question=(
        '一台 EGO Pro 加两台 WristCam 接数采 HUB，4 个下游口够吗？')).begin({
        'requirements': [{'id': 'topology', 'capability': 'inspect_topology'}],
    })
    session.observe('lookup_spec', ToolResult(
        status='ok', content={'matches': [{
            'kind': 'claim', 'status': 'verified',
            'scope': {'product': 'unrelated-hub'},
            'data': {'entity_id': 'entity:unrelated-hub',
                     'field': 'downstream_port_count', 'value': 4,
                     'unit': 'port', 'conditions': {}},
        }], 'matched_requirement_ids': []},
        sources=[{'type': 'daq_governed_claim', 'verification_status': 'verified',
                  'release_id': 'knowledge-v1',
                  'source_id': 'claim:unrelated-hub-ports'}],
    ))
    decision = session.evaluate(AnswerSubmission(
        outcome='safe_abstained', basis='数采 HUB 已核实有 4 个下游口。',
        missing='组合连接还未核实。'))
    assert decision.action == 'request_evidence'
    assert decision.reason_code == 'daq_partial_basis_without_current_source'


def test_user_named_sdk_build_variable_is_not_internal_provenance():
    session = DaqEvidencePolicy(question='TOOLCHAIN_DIR 和运行时 lib 路径怎么处理？').begin({
        'requirements': [{'id': 'sdk', 'capability': 'sdk_evidence'}],
    })
    assert session.evaluate(AnswerSubmission(
        outcome='safe_abstained',
        missing='本轮还没有查到 TOOLCHAIN_DIR 的包内设置说明。',
    )).action == 'allow'
    decision = session.evaluate(AnswerSubmission(
        outcome='safe_abstained',
        missing='本轮还没有查到 invented_internal_flag 的包内设置说明。',
    ))
    assert decision.reason_code == 'daq_internal_provenance_in_answer'


def test_evidence_requires_real_source_and_all_planned_requirements():
    requirements = {"requirements": [
        {"id": "spec", "capability": "lookup_spec", "critical": True},
        {"id": "software", "capability": "check_software_support", "critical": True},
    ]}
    session = DaqEvidencePolicy().begin(requirements)
    session.observe("lookup_spec", ToolResult(status="ok", content={"value": "120 mm"}))
    assert session.snapshot().requirement_status["spec"] == "unknown"
    session.observe("lookup_spec", ToolResult(
        status="ok", content={"value": "120 mm", "matched_requirement_ids": ["other"]},
        sources=[{"source_id": "reviewed-spec", "type": "daq_governed_claim",
                  "verification_status": "verified", "release_id": "reviewed-v1"}],
    ))
    assert session.snapshot().requirement_status["spec"] == "unknown"
    session.observe("lookup_spec", ToolResult(
        status="ok", content={"value": "120 mm", "matched_requirement_ids": ["spec"]},
        sources=[{"source_id": "user-log", "type": "user_attachment",
                  "verification_status": "verified", "release_id": "reviewed-v1"}],
    ))
    assert session.snapshot().requirement_status["spec"] == "unknown"
    session.observe("lookup_spec", ToolResult(
        status="ok", content={"value": "120 mm", "matched_requirement_ids": ["spec"]},
        sources=[{"source_id": "reviewed-spec", "type": "daq_governed_claim",
                  "verification_status": "verified", "release_id": "reviewed-v1"}],
    ))
    assert session.snapshot().requirement_status["spec"] == "satisfied"
    assert session.evaluate(AnswerSubmission(outcome="resolved", conclusion="可用")).action == "request_evidence"
    session.observe("check_software_support", ToolResult(
        status="conflict", content={"candidates": [], "matched_requirement_ids": ["software"]},
        sources=[{"source_id": "a"}],
    ))
    assert session.snapshot().requirement_status["software"] == "conflict"
    assert session.evaluate(AnswerSubmission(outcome="resolved", conclusion="可用")).action == "reject"


def test_unselected_variant_asks_for_safe_conditional_answer_before_contract_failure():
    session = DaqEvidencePolicy().begin({"requirements": [
        {"id": "spec", "capability": "lookup_spec", "critical": True},
    ]})
    session.observe("lookup_spec", ToolResult(
        status="not_found", content={"reason": "variant_selector_required",
                                     "matched_requirement_ids": ["spec"]},
    ))
    decision = session.evaluate(AnswerSubmission(
        outcome="resolved", conclusion="EGO 水平视场角为 165°或 149°"))
    assert decision.action == "request_evidence"
    assert "safe_abstained" in decision.notice


def test_field01_partial_specs_and_missing_selection_request_safe_answer():
    session = DaqEvidencePolicy().begin({'requirements': [
        {'id': 'fov', 'capability': 'lookup_spec', 'critical': True},
        {'id': 'selection', 'capability': 'selection', 'critical': True},
    ]})
    session.observe('lookup_spec', ToolResult(
        status='ok', content={'matched_requirement_ids': ['fov']},
        sources=[{'source_id': 'claim:ego-fov', 'type': 'daq_governed_claim',
                  'verification_status': 'verified', 'release_id': 'reviewed-v1'}],
    ))
    session.observe('selection', ToolResult(
        status='not_found', content={'matched_requirement_ids': ['selection']},
    ))
    decision = session.evaluate(AnswerSubmission(
        outcome='resolved', conclusion='推荐 EGO 1600 版用于整套采集。'))
    assert decision.action == 'request_evidence'
    assert 'safe_abstained' in decision.notice


def test_dev_source_checked_evidence_only_counts_in_explicit_dev_policy():
    result = ToolResult(
        status="ok", content={"matched_requirement_ids": ["spec"]},
        sources=[{"source_id": "claim:dev", "type": "daq_dev_source_checked_claim",
                  "verification_status": "source_checked", "release_id": "candidate-dev-example"}],
    )
    requirements = {"requirements": [{"id": "spec", "capability": "lookup_spec"}]}
    ordinary = DaqEvidencePolicy().begin(requirements)
    ordinary.observe("lookup_spec", result)
    assert ordinary.snapshot().requirement_status["spec"] == "unknown"
    dev = DaqEvidencePolicy(allow_dev_source_check=True).begin(requirements)
    dev.observe("lookup_spec", result)
    assert dev.snapshot().requirement_status["spec"] == "satisfied"
    assert dev.evaluate(AnswerSubmission(outcome="resolved", conclusion="1600×1200")).action == "allow"


def test_tool_error_is_protocol_failure_not_missing_knowledge():
    session = DaqEvidencePolicy().begin(question_requirements("怎么连接？"))
    session.observe("search_knowledge", ToolResult(status="tool_error", content={"error": "timeout"}))
    assert session.snapshot().requirement_status == {"question_evidence": "unknown"}
    decision = session.evaluate(AnswerSubmission(outcome="safe_abstained", missing="资料不足"))
    assert decision.action == "reject"
    assert decision.reason_code == "daq_evidence_tool_failure"


def test_out_of_goal_tool_call_does_not_poison_safe_evidence_gap_answer():
    allowed_tools = frozenset({'inspect_topology'})
    session = DaqEvidencePolicy(allowed_tools=allowed_tools).begin({'requirements': [
        {'id': 'topology', 'capability': 'inspect_topology', 'critical': True},
    ]})
    box = DaqToolBox(allowed_tools=allowed_tools)
    denied = box.dispatch('lookup_spec', {})
    assert denied.status == 'tool_error'
    assert denied.content['error'] == 'tool_outside_request_goals'
    session.observe('lookup_spec', denied)
    assert session.snapshot().requirement_status == {'topology': 'unknown'}
    assert session.evaluate(AnswerSubmission(
        outcome='safe_abstained', missing='No reviewed wiring topology is available.',
    )).action == 'allow'
    assert session.evaluate(AnswerSubmission(
        outcome='resolved', conclusion='The wiring is supported.',
    )).action != 'allow'


def test_planned_tool_scope_error_remains_a_configuration_failure():
    session = DaqEvidencePolicy(allowed_tools=frozenset({'lookup_spec'})).begin({
        'requirements': [{'id': 'spec', 'capability': 'lookup_spec'}],
    })
    session.observe('lookup_spec', ToolResult(
        status='tool_error', content={'error': 'tool_outside_request_goals'},
    ))
    decision = session.evaluate(AnswerSubmission(
        outcome='safe_abstained', missing='No specification was found.',
    ))
    assert decision.action == 'reject'
    assert decision.reason_code == 'daq_evidence_tool_failure'


def test_vision_unavailable_allows_explicit_abstention_but_no_resolution():
    session = DaqEvidencePolicy().begin(question_requirements("看一下这张图"))
    session.observe("analyze_image", ToolResult(
        status="tool_error", content={"error": "vision_unavailable"},
    ))
    assert session.evaluate(AnswerSubmission(
        outcome="safe_abstained", missing="图片视觉分析不可用",
    )).action == "allow"
    decision = session.evaluate(AnswerSubmission(outcome="resolved", conclusion="图中是 EGO"))
    assert decision.action == "reject"
    assert decision.reason_code == "daq_attachment_evidence_failed"


def test_attachment_source_is_tracked_separately_from_governed_claims():
    session = DaqEvidencePolicy().begin({"requirements": [
        {"id": "product", "capability": "lookup_spec", "evidence_class": "governed_required"},
        {"id": "user_log", "capability": "search_attachments",
         "evidence_class": "user_evidence_required", "source_ids": ["user-src"]},
    ]})
    session.observe("search_knowledge", ToolResult(
        status="not_found", content={"reason": "empty_knowledge_release"},
    ))
    assert session.snapshot().requirement_status == {
        "product": "missing", "user_log": "unknown",
    }
    session.observe("search_attachments", ToolResult(
        status="ok", content={"hits": []},
        sources=[{"type": "user_attachment", "source_id": "user-src"}],
    ))
    assert session.snapshot().requirement_status == {
        "product": "missing", "user_log": "satisfied",
    }
    assert session.evaluate(AnswerSubmission(outcome="resolved", conclusion="设备支持")).action == "reject"


def test_warn_only_attachment_cannot_become_capture_request_failure():
    requirements = {"requirements": [{
        "id": "user_log", "capability": "search_attachments",
        "evidence_class": "user_evidence_required", "source_ids": ["user-src"],
    }]}
    warning = ("device=EGO Pro firmware=v9.9.9 sdk=2.9.0\n"
               "WARN capture_start request_id=R-42 usb_transfer_timeout code=E42\n"
               "operator_note=after HUB hotplug; retry_not_performed")

    def observed(text):
        session = DaqEvidencePolicy(question="请读这份现场日志，实际写了什么？").begin(requirements)
        session.observe("search_attachments", ToolResult(
            status="ok", content={"hits": [{"source_id": "user-src", "text": text}]},
            sources=[{"type": "user_attachment", "source_id": "user-src"}],
        ))
        return session

    session = observed(warning)
    bad = session.evaluate(AnswerSubmission(
        outcome="safe_abstained", basis="这次采集启动请求失败了。",
        missing="仍不能确认兼容性。",
    ))
    assert bad.action == "request_evidence"
    assert bad.reason_code == "daq_attachment_terminal_outcome_unverified"
    assert session.evaluate(AnswerSubmission(
        outcome="safe_abstained",
        basis="不能证明组合不兼容，但这次采集启动请求失败了。",
        missing="正式组合结论仍未知。",
    )).reason_code == "daq_attachment_terminal_outcome_unverified"
    assert session.evaluate(AnswerSubmission(
        outcome="safe_abstained",
        basis="不能证明组合不兼容但这次采集启动请求失败了。",
        missing="正式组合结论仍未知。",
    )).reason_code == "daq_attachment_terminal_outcome_unverified"
    assert session.evaluate(AnswerSubmission(
        outcome="safe_abstained",
        basis="采集启动请求出现 USB 传输超时告警。",
        missing="不能确认请求是否失败，也不能确认兼容性。",
    )).action == "allow"
    assert session.evaluate(AnswerSubmission(
        outcome="safe_abstained",
        basis="日志并不能证明采集启动请求失败。",
        missing="请求最终状态未知。",
    )).action == "allow"

    unrelated_failure = observed(warning + "\nWARN capture_start failed to read callback metadata")
    assert unrelated_failure.evaluate(AnswerSubmission(
        outcome="safe_abstained", basis="这次采集启动请求失败了。",
        missing="仍不能确认兼容性。",
    )).reason_code == "daq_attachment_terminal_outcome_unverified"
    another_request = observed(warning + "\nresult capture_start request_id=R-43 status=failed")
    assert another_request.evaluate(AnswerSubmission(
        outcome="safe_abstained", basis="这次采集启动请求失败了。",
        missing="仍不能确认兼容性。",
    )).reason_code == "daq_attachment_terminal_outcome_unverified"
    assert another_request.evaluate(AnswerSubmission(
        outcome="safe_abstained", basis="R-43 的采集启动请求失败了。",
        missing="兼容性未知。",
    )).action == "allow"
    assert another_request.evaluate(AnswerSubmission(
        outcome="safe_abstained", basis="R-42 的采集启动请求失败了。",
        missing="兼容性未知。",
    )).reason_code == "daq_attachment_terminal_outcome_unverified"
    assert session.evaluate(AnswerSubmission(
        outcome="safe_abstained", basis="这次采集启动失败了。",
        missing="兼容性未知。",
    )).reason_code == "daq_attachment_terminal_outcome_unverified"
    assert session.evaluate(AnswerSubmission(
        outcome="safe_abstained", basis="不能确认兼容性并且这次采集启动请求失败了。",
        missing="兼容性未知。",
    )).reason_code == "daq_attachment_terminal_outcome_unverified"
    assert session.evaluate(AnswerSubmission(
        outcome="safe_abstained", basis="这次采集启动请求并未失败。",
        missing="兼容性未知。",
    )).action == "allow"
    assert session.evaluate(AnswerSubmission(
        outcome="safe_abstained", basis="这次采集启动请求尚未失败。",
        missing="兼容性未知。",
    )).action == "allow"
    assert session.evaluate(AnswerSubmission(
        outcome="safe_abstained", basis="这次采集启动请求失败了吗？",
        missing="兼容性未知。",
    )).action == "allow"

    callback_failure = observed(warning + "\nWARN capture_start request_id=R-42 callback event status=failed")
    assert callback_failure.evaluate(AnswerSubmission(
        outcome="safe_abstained", basis="这次采集启动请求失败了。",
        missing="兼容性未知。",
    )).reason_code == "daq_attachment_terminal_outcome_unverified"
    mislabeled_result = observed(warning + "\nWARN result capture_start request_id=R-42 status=failed")
    assert mislabeled_result.evaluate(AnswerSubmission(
        outcome="safe_abstained", basis="这次采集启动请求失败了。",
        missing="兼容性未知。",
    )).reason_code == "daq_attachment_terminal_outcome_unverified"
    chinese_warning = observed("警告：采集启动请求发生 USB 传输超时；最终结果未记录。")
    assert chinese_warning.evaluate(AnswerSubmission(
        outcome="safe_abstained", basis="这次采集启动请求失败了。",
        missing="兼容性未知。",
    )).reason_code == "daq_attachment_terminal_outcome_unverified"

    source_bound = DaqEvidencePolicy(question="请读附件 A 的日志").begin(requirements)
    source_bound.observe("search_attachments", ToolResult(
        status="ok", content={"hits": [{"source_id": "user-src", "text": warning}]},
        sources=[{"type": "user_attachment", "source_id": "user-src"}],
    ))
    source_bound.observe("read_attachment", ToolResult(
        status="ok", content={"source_id": "other-src",
                              "text": "result capture_start request_id=R-42 status=failed"},
        sources=[{"type": "user_attachment", "source_id": "other-src"}],
    ))
    assert source_bound.evaluate(AnswerSubmission(
        outcome="safe_abstained", basis="这次采集启动请求失败了。",
        missing="兼容性未知。",
    )).reason_code == "daq_attachment_terminal_outcome_unverified"

    terminal = observed(warning + "\nresult capture_start request_id=R-42 status=failed")
    assert terminal.evaluate(AnswerSubmission(
        outcome="safe_abstained", basis="这次采集启动请求失败了。",
        missing="仍不能确认兼容性。",
    )).action == "allow"
    timestamped = observed(warning + "\n2026-10-20T10:21:34+08:00 INFO result capture_start request_id=R-42 status=failed")
    assert timestamped.evaluate(AnswerSubmission(
        outcome="safe_abstained", basis="R-42 的采集启动请求失败了。",
        missing="兼容性未知。",
    )).action == "allow"

    question_bound = DaqEvidencePolicy(question="R-42 的启动请求最终结果？").begin(requirements)
    question_bound.observe("search_attachments", ToolResult(
        status="ok", content={"hits": [{"source_id": "user-src", "text": (
            "capture_start request_id=R-42 status=pending\n"
            "result capture_start request_id=R-43 status=failed")}]},
        sources=[{"type": "user_attachment", "source_id": "user-src"}],
    ))
    assert question_bound.evaluate(AnswerSubmission(
        outcome="safe_abstained", basis="这次采集启动请求失败了。",
        missing="兼容性未知。",
    )).reason_code == "daq_attachment_terminal_outcome_unverified"


def test_answer_must_keep_internal_provenance_out_of_prose():
    session = DaqEvidencePolicy().begin({"requirements": [
        {"id": "spec", "capability": "lookup_spec"},
    ]})
    session.observe("lookup_spec", ToolResult(
        status="ok", content={"matched_requirement_ids": ["spec"], "matches": [
            {"id": "claim:ego-1600-horizontal-fov", "data": {"field": "horizontal_fov", "value": 165}},
        ]}, sources=[{"source_id": "claim:ego-1600-horizontal-fov",
                     "type": "daq_governed_claim", "verification_status": "verified",
                     "release_id": "reviewed-v1", "source_refs": [
                         {"path": "规格书/EGO.pdf", "sha256": "a" * 64,
                          "locator": {"kind": "page", "page": 3}},
                     ]}],
    ))
    assert session.snapshot().requirement_status["spec"] == "satisfied"
    for basis in ("依据 claim:ego-1600-horizontal-fov", "字段 horizontal_fov",
                  "查不到 depth_accuracy", "工具返回 not_found",
                  "source_id: claim:ego-1600-horizontal-fov", "见规格书/EGO.pdf",
                  "内部 release reviewed-v1"):
        decision = session.evaluate(AnswerSubmission(
            outcome="resolved", conclusion="EGO 1600 的水平视场角为 165°", basis=basis))
        assert decision.action == "request_evidence"
        assert decision.reason_code == "daq_internal_provenance_in_answer"
    assert session.evaluate(AnswerSubmission(
        outcome="resolved", conclusion="EGO 1600 的水平视场角为 165°",
        basis="按 1600×1200 版本的已发布规格核对。",
    )).action == "allow"


def test_source_page_or_line_locator_is_not_delivered_in_answer_prose():
    session = DaqEvidencePolicy().begin(question_requirements('变更日志怎么说？'))
    for prose in ('变更日志第 8 行记载了该变更。',
                  'The change is on line 8 of the release note.',
                  '原件第 3 页有说明。'):
        decision = session.evaluate(AnswerSubmission(
            outcome='safe_abstained', basis=prose, missing='组合支持未知。'))
        assert decision.action == 'request_evidence'
        assert decision.reason_code == 'daq_internal_provenance_in_answer'
    session.observe('search_knowledge', ToolResult(
        status='ok', content={'evidence_scope': 'release_note_statement_only',
                              'matched_requirement_ids': ['question_evidence']},
        sources=[{'type': 'daq_governed_release_note',
                  'verification_status': 'verified',
                  'release_id': 'knowledge-v1', 'source_id': 'note-v1'}],
    ))
    assert session.evaluate(AnswerSubmission(
        outcome='safe_abstained', basis='变更日志记载了该变更。',
        missing='组合支持未知。')).action == 'allow'


def test_model_added_interface_must_be_in_user_question_or_reviewed_source():
    requirements = {'requirements': [
        {'id': 'sdk', 'capability': 'sdk_evidence'},
    ]}
    session = DaqEvidencePolicy(question='iOS framework 如何嵌入？',
                                allow_dev_source_check=True).begin(requirements)
    session.observe('sdk_evidence', ToolResult(
        status='ok', content={'matched_requirement_ids': ['sdk'],
                              'matches': [{'answer': '把 framework 嵌入应用。'}]},
        sources=[{'source_id': 'sdk-ios', 'type': 'daq_sdk_source_checked',
                  'verification_status': 'source_checked',
                  'release_id': 'dev-sdk'}],
    ))
    decision = session.evaluate(AnswerSubmission(
        outcome='safe_abstained', basis='本轮按 USB 连接核对支持。',
        missing='设备固件支持未知。'))
    assert decision.action == 'request_evidence'
    assert decision.reason_code == 'daq_unrequested_interface_in_answer'

    grounded = DaqEvidencePolicy(question='多设备同步怎么设置？',
                                 allow_dev_source_check=True).begin(requirements)
    grounded.observe('sdk_evidence', ToolResult(
        status='ok', content={'matched_requirement_ids': ['sdk'],
                              'matches': [{'answer': '示例通过 USB 枚举打开设备。'}]},
        sources=[{'source_id': 'sdk-sync', 'type': 'daq_sdk_source_checked',
                  'verification_status': 'source_checked',
                  'release_id': 'dev-sdk'}],
    ))
    assert grounded.evaluate(AnswerSubmission(
        outcome='resolved', conclusion='示例通过 USB 枚举打开设备。')).action == 'allow'


def test_same_evidence_interface_decision_depends_only_on_trusted_question():
    requirements = {'requirements': [{'id': 'sdk', 'capability': 'sdk_evidence'}]}
    result = ToolResult(
        status='ok',
        content={'matched_requirement_ids': ['sdk'],
                 'matches': [{'answer': '此包提供设备枚举示例。'}]},
        sources=[{'source_id': 'sdk-example', 'type': 'daq_sdk_source_checked',
                  'verification_status': 'source_checked',
                  'release_id': 'dev-sdk'}],
    )
    answer = AnswerSubmission(
        outcome='safe_abstained', basis='本轮按 USB 连接核对支持。',
        missing='设备与固件的组合支持仍未知。')
    for question, expected in (
        ('iOS framework 如何嵌入？', 'daq_unrequested_interface_in_answer'),
        ('iOS framework 通过 USB 如何嵌入？', ''),
    ):
        # Dev, authenticated chat and Task now pass this same effective question
        # to the policy. Their entrypoint wiring is asserted separately.
        decisions = []
        for _entry in ('dev', 'authenticated', 'task'):
            session = DaqEvidencePolicy(
                question=question, allow_dev_source_check=True).begin(requirements)
            session.observe('sdk_evidence', result)
            decisions.append(session.evaluate(answer))
        assert len({(item.action, item.reason_code) for item in decisions}) == 1
        assert decisions[0].reason_code == expected

    reviewed = ToolResult(
        status='ok', content={'matched_requirement_ids': ['sdk'],
                              'matches': [{'answer': '通过 USB 枚举设备。'}]},
        sources=[{'source_id': 'sdk-usb', 'type': 'daq_sdk_source_checked',
                  'verification_status': 'source_checked',
                  'release_id': 'dev-sdk'}],
    )
    session = DaqEvidencePolicy(
        question='iOS framework 如何嵌入？',
        allow_dev_source_check=True).begin(requirements)
    session.observe('sdk_evidence', reviewed)
    assert session.evaluate(answer).action == 'allow'


def test_sdk_output_filenames_are_allowed_but_source_member_paths_are_not():
    session = DaqEvidencePolicy(allow_dev_source_check=True).begin({"requirements": [
        {"id": "sdk", "capability": "sdk_evidence"},
    ]})
    session.observe("sdk_evidence", ToolResult(
        status="ok", content={"matched_requirement_ids": ["sdk"], "matches": []},
        sources=[{"type": "daq_sdk_source_checked", "verification_status": "source_checked",
                  "release_id": "dev-sdk", "source_id": "sdk-calib",
                  "source_refs": [{"member": "pkg/examples/ego_calib_yaml_export.cpp",
                                   "member_sha256": "a" * 64}]}],
    ))
    assert session.evaluate(AnswerSubmission(
        outcome="resolved", conclusion="导出 align_calib.yaml 和 imu_calib.yaml。"
    )).action == "allow"
    decision = session.evaluate(AnswerSubmission(
        outcome="resolved", conclusion="请查看 ego_calib_yaml_export.cpp。"
    ))
    assert decision.reason_code == "daq_source_filename_in_answer"


def test_public_sdk_symbols_are_allowed_in_sdk_usage_answer():
    session = DaqEvidencePolicy(allow_dev_source_check=True).begin({"requirements": [
        {"id": "sdk", "capability": "sdk_evidence"},
    ]})
    session.observe("sdk_evidence", ToolResult(
        status="ok", content={"matched_requirement_ids": ["sdk"], "matches": [
            {"symbols": ["OB_RAW_DATA_ALIGN_CALIB_YAML", "ob_capture_configure"],
             "code_tokens": ["ob_capture_config_t", "enable_mask"]},
        ]}, sources=[{"type": "daq_sdk_source_checked",
                     "verification_status": "source_checked",
                     "release_id": "dev-sdk", "source_id": "sdk-calib"}],
    ))
    assert session.evaluate(AnswerSubmission(
        outcome="resolved", conclusion="检查 OB_RAW_DATA_ALIGN_CALIB_YAML，再用 ob_capture_config_t 的 enable_mask 调用 ob_capture_configure。"
    )).action == "allow"
    assert session.evaluate(AnswerSubmission(
        outcome="resolved", conclusion="调用 invented_private_api。"
    )).reason_code == "daq_internal_provenance_in_answer"


def test_english_provenance_repair_notice_uses_selected_answer_language():
    session = DaqEvidencePolicy(answer_language='en').begin({'requirements': [
        {'id': 'identity', 'capability': 'resolve_entity'},
    ]})
    decision = session.evaluate(AnswerSubmission(
        outcome='safe_abstained', missing='No source_id was returned.',
    ))
    assert decision.reason_code == 'daq_internal_provenance_in_answer'
    assert 'Rewrite' in decision.notice
    assert not any('\u3400' <= character <= '\u9fff' for character in decision.notice)


def test_chinese_answer_contract_rewrites_an_english_body_for_chinese_question():
    session = DaqEvidencePolicy(answer_language='zh').begin({"requirements": [
        {"id": "service", "capability": "service_identity",
         "evidence_class": "clarification_required"},
    ]})
    decision = session.evaluate(AnswerSubmission(
        outcome='safe_abstained',
        missing=('I am the internal data acquisition FAE assistant and I can help '
                 'identify products, inspect specifications, explain connection '
                 'questions, and review capture workflows using the published sources.'),
    ))
    assert decision.action == 'request_evidence'
    assert decision.reason_code == 'daq_answer_language_mismatch'
    assert session.evaluate(AnswerSubmission(
        outcome='safe_abstained', missing='目前资料不足，暂时无法确认 EGO 的具体版本。',
    )).action == 'allow'
