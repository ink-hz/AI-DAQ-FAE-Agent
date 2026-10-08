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
