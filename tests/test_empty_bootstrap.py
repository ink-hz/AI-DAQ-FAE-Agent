import json

from fastapi.testclient import TestClient
import httpx
import pytest


def _events(response):
    result = []
    for block in response.text.strip().split("\n\n"):
        lines = block.splitlines()
        name = next((line.removeprefix("event: ") for line in lines if line.startswith("event: ")), None)
        payload = next((json.loads(line.removeprefix("data: ")) for line in lines if line.startswith("data: ")), None)
        if isinstance(payload, dict):
            result.append({**payload, "type": name})
    return result


def test_health_identifies_independent_empty_dev_service():
    from daq_fae.app import create_app

    response = TestClient(create_app(provider_mode="offline")).get("/health")

    assert response.status_code == 200
    assert response.json()["agent_id"] == "ai-daq-fae-agent"
    assert response.json()["environment"] == "development"
    assert response.json()["knowledge_release"] == "empty-dev-v0"
    assert response.json()["provider_mode"] == "offline"


def test_empty_knowledge_request_uses_loop_and_explicitly_abstains():
    from daq_fae.app import create_app

    response = TestClient(create_app(provider_mode="offline")).post(
        "/chat", json={"question": "EG-DB 的深度精度是多少？"}
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _events(response)
    done = next(event for event in events if event["type"] == "done")
    assert done["outcome"] == "safe_abstained"
    assert done["sources"] == []
    assert done["planned_capabilities"] == ["lookup_spec"]
    assert done["coverage_status"] == "empty"
    assert done["capability_coverage"] == {"lookup_spec": "empty"}
    assert set(done["evidence_policy"]["requirement_status"].values()) == {"missing"}
    assert done["tool_calls"][0]["status"] == "not_found"
    assert done["fallback_used"] is True
    assert done["fallback_reason"] == "empty_release_synthesis_template"
    assert done["synthesis_mode"] == "deterministic_empty_release"
    assert done["trace_id"]
    assert "正式规格" in done["answer"]
    assert "精度是" not in done["answer"]


class UngroundedAdapter:
    model = "test-model"
    tool_choice_strategy = "submit_only_auto"

    def chat(self, messages, tools, required_tool=None):
        yield {
            "type": "tool_call",
            "id": "unguarded-answer",
            "name": "submit_answer",
            "arguments": {"outcome": "resolved", "conclusion": "深度精度是 1 mm。"},
        }
        yield {"type": "stop", "stop_reason": "tool_use", "usage": None}


def test_ungrounded_model_conclusion_is_blocked_and_visible():
    from daq_fae.app import create_app

    response = TestClient(create_app(adapter=UngroundedAdapter())).post(
        "/chat", json={"question": "EG-DB 的深度精度是多少？"}
    )

    done = next(event for event in _events(response) if event["type"] == "done")
    assert done["outcome"] == "invalid_answer_contract"
    assert done["fallback_used"] is True
    assert done["fallback_reason"] == "evidence_policy:daq_evidence_needed"
    assert done["sources"] == []
    assert "1 mm" not in response.text


def test_ungrounded_escalation_cannot_deliver_a_claim_either():
    from daq_fae.app import create_app

    class EscalatingAdapter:
        model = "test-model"
        tool_choice_strategy = "submit_only_auto"

        def chat(self, messages, tools, required_tool=None):
            yield {
                "type": "tool_call",
                "id": "unguarded-escalation",
                "name": "submit_answer",
                "arguments": {
                    "outcome": "escalate_rd",
                    "conclusion": "深度精度是 1 mm。",
                    "pending_confirmation": "请研发确认。",
                },
            }
            yield {"type": "stop", "stop_reason": "tool_use", "usage": None}

    response = TestClient(create_app(adapter=EscalatingAdapter())).post(
        "/chat", json={"question": "EG-DB 的深度精度是多少？"}
    )

    done = next(event for event in _events(response) if event["type"] == "done")
    assert done["outcome"] == "invalid_answer_contract"
    assert done["fallback_used"] is True
    assert "1 mm" not in response.text


def test_bootstrap_rejects_unreviewed_knowledge_files(tmp_path):
    from daq_fae.app import create_app

    (tmp_path / "camera-spec.md").write_text("not reviewed", encoding="utf-8")
    with pytest.raises(ValueError, match="empty knowledge"):
        create_app(provider_mode="offline", knowledge_dir=tmp_path)


def test_default_bootstrap_keeps_private_candidate_drafts_unloaded():
    from daq_fae.app import create_app

    response = TestClient(create_app(provider_mode="offline")).get("/health")
    assert response.json()["knowledge_release"] == "empty-dev-v0"


def test_custom_knowledge_directory_cannot_hide_unreviewed_drafts(tmp_path):
    from daq_fae.app import create_app

    drafts = tmp_path / "drafts"
    drafts.mkdir()
    (drafts / "rogue.md").write_text("unreviewed", encoding="utf-8")
    with pytest.raises(ValueError, match="empty knowledge"):
        create_app(provider_mode="offline", knowledge_dir=tmp_path)


@pytest.mark.parametrize(
    ("status_code", "outcome"),
    [(400, "provider_configuration_error"), (503, "provider_unavailable")],
)
def test_gateway_errors_keep_their_failure_class(status_code, outcome):
    from daq_fae.app import create_app

    class FailingAdapter:
        model = "test-model"
        tool_choice_strategy = "submit_only_auto"

        def chat(self, messages, tools, required_tool=None):
            request = httpx.Request("POST", "https://example.invalid/v1/messages")
            response = httpx.Response(status_code, request=request)
            raise httpx.HTTPStatusError("gateway error", request=request, response=response)
            yield  # Keep this a generator like the provider adapter.

    response = TestClient(create_app(adapter=FailingAdapter())).post(
        "/chat", json={"question": "EG-DB 的深度精度是多少？"}
    )

    done = next(event for event in _events(response) if event["type"] == "done")
    assert done["outcome"] == outcome
    assert done["provider_status_code"] == status_code
    assert done["fallback_used"] is True
