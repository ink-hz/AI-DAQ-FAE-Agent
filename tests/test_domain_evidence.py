from src.agent.loop.answer_contract import AnswerSubmission
from src.agent.loop.tools import ToolResult

from daq_fae.domain_evidence import DaqEvidencePolicy, question_requirements
from daq_fae.domain_tools import DaqToolBox


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
                  "source_id: claim:ego-1600-horizontal-fov", "见规格书/EGO.pdf"):
        decision = session.evaluate(AnswerSubmission(
            outcome="resolved", conclusion="EGO 1600 的水平视场角为 165°", basis=basis))
        assert decision.action == "request_evidence"
        assert decision.reason_code == "daq_internal_provenance_in_answer"
    assert session.evaluate(AnswerSubmission(
        outcome="resolved", conclusion="EGO 1600 的水平视场角为 165°",
        basis="按 1600×1200 版本的已发布规格核对。",
    )).action == "allow"
