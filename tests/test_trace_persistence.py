import json

from fastapi.testclient import TestClient

from daq_fae.app import create_app
from tests.test_api_transport import terminal


def test_daq_trace_is_persisted_under_its_own_path(tmp_path, monkeypatch):
    path = tmp_path / 'daq' / 'traces.jsonl'
    monkeypatch.setenv('DAQ_TRACE_LOG_PATH', str(path))
    monkeypatch.setenv('DAQ_DEV_STATE_DB', str(tmp_path / 'state.sqlite3'))
    app = create_app(provider_mode='offline')
    done = terminal(TestClient(app).post('/chat', json={'message': 'EGO 的规格'}))
    assert app.state.trace_recorder is not None
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert any(row['trace_id'] == done['trace_id'] and row['node'] == 'daq_chat_request' for row in rows)
    assert 'EGO 的规格' not in path.read_text()
    assert TestClient(app).get('/health').json()['trace_persistence'] == 'daq_jsonl_dev'
