import json

from fastapi.testclient import TestClient

from daq_fae.app import create_app


def _done(response):
    assert response.status_code == 200
    for block in response.text.split("\n\n"):
        if block.startswith("event: done\n"):
            return json.loads(next(line[6:] for line in block.splitlines() if line.startswith("data: ")))
    raise AssertionError("no terminal event")


def test_local_dev_session_and_feedback_survive_restart(tmp_path):
    database = tmp_path / "daq-state.sqlite3"
    first = TestClient(create_app(provider_mode="offline", state_db_path=database))
    initial = _done(first.post("/chat", json={
        "message": "EGO 如何连接？", "client_request_id": "stable-request",
    }))
    assert initial["turn_id"]
    second = TestClient(create_app(provider_mode="offline", state_db_path=database))
    history = second.get("/history", params={"session_id": initial["session_id"]})
    assert history.status_code == 200
    assert len(history.json()["messages"]) == 2
    replay = _done(second.post("/chat", json={
        "message": "EGO 如何连接？", "client_request_id": "stable-request",
    }))
    assert replay == initial
    followup = _done(second.post("/chat", json={
        "session_id": initial["session_id"], "message": "那 UMI 呢？",
    }))
    assert followup["session_id"] == initial["session_id"]
    assert followup["turn_id"] != initial["turn_id"]
    feedback = second.post("/feedback", json={
        "session_id": initial["session_id"], "message_index": 0,
        "rating": "bad", "comment": "缺资料", "turn_id": initial["turn_id"],
        "trace_id": initial["trace_id"],
    })
    assert feedback.status_code == 200
    assert feedback.json()["feedback_id"]
    assert second.post("/feedback", json={
        "session_id": initial["session_id"], "message_index": 0,
        "rating": "bad", "turn_id": followup["turn_id"],
    }).status_code == 404


def test_local_request_id_conflict_survives_restart(tmp_path):
    database = tmp_path / "daq-state.sqlite3"
    first = TestClient(create_app(provider_mode="offline", state_db_path=database))
    _done(first.post("/chat", json={"message": "问题 A", "client_request_id": "same"}))
    second = TestClient(create_app(provider_mode="offline", state_db_path=database))
    assert second.post("/chat", json={
        "message": "问题 B", "client_request_id": "same",
    }).status_code == 409


def test_daq_context_survives_restart_and_topic_switch(tmp_path):
    from daq_fae.offline_adapter import OfflineAdapter

    class CaptureAdapter(OfflineAdapter):
        def __init__(self):
            self.system_prompts = []

        def chat(self, messages, tools=None, required_tool=None):
            self.system_prompts.append(messages[0]["content"])
            yield from super().chat(messages, tools, required_tool)

    database = tmp_path / "state.sqlite3"
    first = TestClient(create_app(provider_mode="offline", state_db_path=database))
    initial = _done(first.post("/chat", json={
        "message": "设备是 EGO；SDK 版本为 2.0；录制报错",
    }))
    assert {"resolve_entity", "check_software_support", "lookup_procedure"} <= set(
        initial["planned_capabilities"]
    )
    adapter = CaptureAdapter()
    second = TestClient(create_app(adapter=adapter, state_db_path=database))
    followup = _done(second.post("/chat", json={
        "session_id": initial["session_id"], "message": "还是不行，下一步？",
    }))
    assert "EGO" in adapter.system_prompts[0]
    assert "sdk_version" in adapter.system_prompts[0]
    assert "2.0" in adapter.system_prompts[0]
    _done(second.post("/chat", json={
        "session_id": initial["session_id"], "message": "换个场景：另一台设备的规格",
    }))
    assert "EGO" not in adapter.system_prompts[-1]
    assert followup["evidence_policy"]["requirement_status"]
