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
