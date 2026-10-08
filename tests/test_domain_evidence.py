from src.agent.loop.answer_contract import AnswerSubmission
from src.agent.loop.tools import ToolResult

from daq_fae.domain_evidence import DaqEvidencePolicy, question_requirements
from daq_fae.domain_tools import DaqToolBox


def test_empty_release_exposes_full_domain_tool_contract_without_camera_facts():
    toolbox = DaqToolBox().with_request_context("EG-DB 如何同步？")
    names = {item["function"]["name"] for item in toolbox.tool_schemas()}
    assert names == {
        "resolve_entity", "lookup_spec", "inspect_topology",
        "lookup_procedure", "check_software_support", "search_knowledge",
        "sdk_evidence", "official_links", "session_state",
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
    session.observe("search_knowledge", ToolResult(status="not_found", content={"matches": []}))
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
        status="ok", content={"value": "120 mm"},
        sources=[{"source_id": "reviewed-spec", "type": "governed_claim"}],
    ))
    assert session.snapshot().requirement_status["spec"] == "satisfied"
    assert session.evaluate(AnswerSubmission(outcome="resolved", conclusion="可用")).action == "request_evidence"
    session.observe("check_software_support", ToolResult(
        status="conflict", content={"candidates": []}, sources=[{"source_id": "a"}],
    ))
    assert session.snapshot().requirement_status["software"] == "conflict"
    assert session.evaluate(AnswerSubmission(outcome="resolved", conclusion="可用")).action == "reject"


def test_tool_error_is_protocol_failure_not_missing_knowledge():
    session = DaqEvidencePolicy().begin(question_requirements("怎么连接？"))
    session.observe("search_knowledge", ToolResult(status="tool_error", content={"error": "timeout"}))
    assert session.snapshot().requirement_status == {"question_evidence": "unknown"}
    decision = session.evaluate(AnswerSubmission(outcome="safe_abstained", missing="资料不足"))
    assert decision.action == "reject"
    assert decision.reason_code == "daq_evidence_tool_failure"


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
