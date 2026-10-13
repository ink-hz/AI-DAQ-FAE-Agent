import json
import threading
import time

from fastapi.testclient import TestClient
import pytest

from daq_fae.app import create_app
from daq_fae.offline_adapter import OfflineAdapter
from src.agent.tracing import TraceRecorder, TraceSink


@pytest.fixture(autouse=True)
def isolated_dev_state(tmp_path, monkeypatch):
    monkeypatch.setenv('DAQ_DEV_STATE_DB', str(tmp_path / 'state.sqlite3'))


def events(response):
    result = []
    for block in response.text.strip().split('\n\n'):
        lines = block.splitlines()
        name = next((line[7:] for line in lines if line.startswith('event: ')), None)
        payload = next((json.loads(line[6:]) for line in lines if line.startswith('data: ')), None)
        result.append((name, payload))
    return result


def terminal(response):
    return next(payload for name, payload in events(response) if name == 'done')


def test_local_semantic_service_request_uses_same_service_evidence_contract():
    class ServiceAdapter:
        model = 'scripted-dev'
        tool_choice_strategy = 'submit_only_auto'

        def chat(self, messages, tools=None, required_tool=None):
            if required_tool == 'submit_daq_request_schema':
                yield {'type': 'tool_call', 'id': 'schema', 'name': required_tool,
                       'arguments': {'confidence': 'high', 'goals': [
                           {'kind': 'service', 'capability': 'service_identity'}]}}
            else:
                yield {'type': 'tool_call', 'id': 'answer', 'name': 'submit_answer',
                       'arguments': {'outcome': 'resolved',
                                     'conclusion': '我是奥比中光内部数采 FAE 助手。'}}
            yield {'type': 'stop', 'stop_reason': 'tool_use', 'usage': None}

    done = terminal(TestClient(create_app(adapter=ServiceAdapter())).post(
        '/chat', json={'message': '介绍一下你自己'}))
    assert done['outcome'] == 'resolved'
    assert done['planned_capabilities'] == ['service_identity']
    assert done['sources'] == []


def test_selected_response_language_controls_english_answer_for_chinese_question():
    class ServiceAdapter:
        model = 'scripted-dev'
        tool_choice_strategy = 'submit_only_auto'

        def chat(self, messages, tools=None, required_tool=None):
            if required_tool == 'submit_daq_request_schema':
                yield {'type': 'tool_call', 'id': 'schema', 'name': required_tool,
                       'arguments': {'confidence': 'high', 'answer_language': 'zh',
                                     'goals': [{'kind': 'service',
                                                'capability': 'service_identity'}]}}
            else:
                yield {'type': 'tool_call', 'id': 'answer', 'name': 'submit_answer',
                       'arguments': {'outcome': 'resolved',
                                     'conclusion': 'I am the Orbbec DAQ FAE assistant.'}}
            yield {'type': 'stop', 'stop_reason': 'tool_use', 'usage': None}

    done = terminal(TestClient(create_app(adapter=ServiceAdapter())).post(
        '/chat', json={'message': '介绍一下你自己', 'response_language': 'en-US'}))
    assert done['outcome'] == 'resolved'
    assert done['answer_language'] == 'en'
    assert 'I am' in done['answer']


def test_local_setup_statement_clarifies_without_starting_loop():
    class ContextAdapter:
        model = 'scripted-dev'
        tool_choice_strategy = 'submit_only_auto'

        def chat(self, messages, tools=None, required_tool=None):
            assert required_tool == 'submit_daq_request_schema'
            yield {'type': 'tool_call', 'id': 'schema', 'name': required_tool,
                   'arguments': {'confidence': 'medium', 'goals': [
                       {'kind': 'product', 'capability': 'resolve_entity',
                        'entities': ['EGO']},
                       {'kind': 'unknown', 'capability': 'clarify'},
                   ]}}

    response = TestClient(create_app(adapter=ContextAdapter())).post(
        '/chat', json={'message': '我用 EGO 双目 1600×1200 版本'})
    done = terminal(response)
    assert done['outcome'] == 'safe_abstained'
    assert done['synthesis_mode'] == 'context_clarification'
    stages = [payload['stage'] for name, payload in events(response)
              if name == 'stage']
    assert 'clarification' in stages and 'loop' not in stages


def test_local_refusal_has_no_loop_stage_and_all_running_stages_close():
    response = TestClient(create_app(provider_mode='offline')).post(
        '/chat', json={'message': '请给我客户名单'})
    stage_events = [payload for name, payload in events(response) if name == 'stage']
    assert all(item['stage'] != 'loop' for item in stage_events)
    started = {item['stage'] for item in stage_events if item.get('status') == 'running'}
    ended = {item['stage'] for item in stage_events
             if item.get('status') in {'completed', 'error'}}
    assert started <= ended


def test_local_invalid_schema_is_visible_without_entering_loop():
    class InvalidSchemaAdapter:
        model = 'scripted-dev'
        tool_choice_strategy = 'submit_only_auto'

        def chat(self, messages, tools=None, required_tool=None):
            assert required_tool == 'submit_daq_request_schema'
            yield {'type': 'text_delta', 'text': 'not a schema'}
            yield {'type': 'stop', 'stop_reason': 'end_turn', 'usage': None}

    response = TestClient(create_app(adapter=InvalidSchemaAdapter())).post(
        '/chat', json={'message': '介绍一下你的服务'})
    done = terminal(response)
    assert done['outcome'] == 'schema_error'
    assert done['fallback_used'] is True
    stages = [payload for name, payload in events(response) if name == 'stage']
    assert all(item['stage'] != 'loop' for item in stages)


def test_named_stream_and_multiturn_history_reaches_provider():
    class RecordingAdapter(OfflineAdapter):
        def __init__(self):
            self.requests = []

        def chat(self, messages, tools=None, required_tool=None):
            self.requests.append(messages)
            yield from super().chat(messages, tools, required_tool)

    adapter = RecordingAdapter()
    client = TestClient(create_app(adapter=adapter))
    first = client.post('/chat', json={'message': '设备是 EGO', 'channel': 'fae'})
    assert first.status_code == 200
    named = events(first)
    assert named[0][0] == 'session'
    sid = named[0][1]['session_id']
    assert {'stage', 'text_delta', 'sources', 'done'} <= {name for name, _ in named}
    assert next(payload for name, payload in named if name == 'text_delta')['delta']
    second = client.post('/chat', json={'message': '它的版本呢？', 'session_id': sid})
    assert second.status_code == 200
    assert any(message['content'] == '设备是 EGO' for message in adapter.requests[-2])
    history = client.get('/history', params={'session_id': sid}).json()
    assert [message['role'] for message in history['messages']] == ['user', 'assistant', 'user', 'assistant']
    assert history['messages'][-2]['content'] == '它的版本呢？'
    assert terminal(second)['session_id'] == sid


def test_first_turn_feedback_keeps_legacy_default_message_index():
    client = TestClient(create_app(provider_mode='offline'))
    sid = terminal(client.post('/chat', json={'message': '设备版本'}))['session_id']
    feedback = client.post('/feedback', json={'session_id': sid, 'rating': 'bad'})
    assert feedback.status_code == 200
    assert feedback.json()['feedback_id']


def test_unsupported_attachments_and_unknown_sessions_are_explicit():
    client = TestClient(create_app(provider_mode='offline'))
    response = client.post('/chat', json={'message': '看日志', 'attachment_ids': ['unwired']})
    assert response.status_code == 422
    assert response.json()['detail'] == 'attachment_not_found'
    assert client.post('/chat', json={'message': '继续', 'session_id': 'unknown'}).status_code == 404
    assert client.get('/history', params={'session_id': 'unknown'}).status_code == 404


def test_request_replay_is_idempotent_and_payload_conflict_rejected():
    client = TestClient(create_app(provider_mode='offline'))
    body = {'message': '设备版本', 'client_request_id': 'same-request'}
    first = client.post('/chat', json=body)
    second = client.post('/chat', json=body)
    assert terminal(first) == terminal(second)
    sid = terminal(first)['session_id']
    assert len(client.get('/history', params={'session_id': sid}).json()['messages']) == 2
    assert client.post('/chat', json={**body, 'message': '不同问题'}).status_code == 409


def test_heartbeat_while_provider_is_blocked_and_gate_released():
    class SlowAdapter(OfflineAdapter):
        def chat(self, messages, tools=None, required_tool=None):
            time.sleep(0.04)
            yield from super().chat(messages, tools, required_tool)

    app = create_app(adapter=SlowAdapter(), heartbeat_interval_seconds=0.005)
    response = TestClient(app).post('/chat', json={'message': '设备版本'})
    assert 'heartbeat' in [name for name, _ in events(response)]
    assert app.state.chat_concurrency_gate.snapshot().active == 0


def test_trace_uses_terminal_identity_and_observed_coverage():
    class CaptureSink(TraceSink):
        def __init__(self):
            self.spans = []

        def emit_span(self, span, *, is_root):
            self.spans.append((span, is_root))

    sink = CaptureSink()
    app = create_app(provider_mode='offline', trace_recorder=TraceRecorder([sink]))
    done = terminal(TestClient(app).post('/chat', json={'message': '规格'}))
    root = next(span for span, is_root in sink.spans if is_root)
    assert root.trace_id == done['trace_id']
    assert root.output_summary['outcome'] == done['outcome']
    assert done['planned_capabilities'] == ['lookup_spec']
    assert done['capability_coverage'] == {'lookup_spec': 'empty'}
    assert done['actual_capabilities'] == ['search_knowledge']
    assert done['coverage_status'] == 'empty'
    assert done['duration_ms'] >= 0


def test_local_dev_guard_rejects_non_loopback_and_exposes_no_platform_identity():
    app = create_app(provider_mode='offline')
    remote = TestClient(app, client=('192.0.2.10', 1234))
    assert remote.post('/chat', json={'message': '规格'}).status_code == 403
    health = TestClient(app).get('/health').json()
    assert health['local_dev_only'] is True
    assert health['platform_identity_enabled'] is False
    assert health['session_persistence'] == 'local_sqlite_dev'


def test_production_mode_requires_authenticated_service(monkeypatch):
    monkeypatch.setenv('DAQ_ENVIRONMENT', 'production')
    monkeypatch.setenv('DAQ_RUNTIME_RELEASE', 'daq-release-1')
    with pytest.raises(ValueError, match='daq_production_requires_authenticated_mode'):
        create_app(provider_mode='offline')


def test_concurrent_requests_have_explicit_limits_and_no_duplicate_execution():
    entered = threading.Event()
    release = threading.Event()

    class BlockingAdapter(OfflineAdapter):
        def chat(self, messages, tools=None, required_tool=None):
            entered.set()
            assert release.wait(timeout=2)
            yield from super().chat(messages, tools, required_tool)

    app = create_app(adapter=BlockingAdapter(), max_concurrent=1)
    client = TestClient(app)
    body = {'message': '规格', 'client_request_id': 'inflight'}
    result = []
    worker = threading.Thread(target=lambda: result.append(client.post('/chat', json=body)))
    worker.start()
    try:
        assert entered.wait(timeout=2)
        assert client.post('/chat', json=body).status_code == 409
        assert client.post('/chat', json={'message': '另一题'}).status_code == 429
    finally:
        release.set()
        worker.join(timeout=2)
    assert result[0].status_code == 200
    assert app.state.chat_concurrency_gate.snapshot().active == 0
    assert terminal(client.post('/chat', json=body)) == terminal(result[0])


def test_request_validation_and_provider_failure_dont_invent_evidence():
    import httpx

    class FailingAdapter(OfflineAdapter):
        def chat(self, messages, tools=None, required_tool=None):
            raise httpx.ConnectError('unavailable')
            yield

    client = TestClient(create_app(adapter=FailingAdapter()))
    assert client.post('/chat', json={'message': '甲', 'question': '乙'}).status_code == 422
    assert client.post('/chat', json={'message': '甲', 'channel': 'other'}).status_code == 422
    done = terminal(client.post('/chat', json={'message': '规格'}))
    assert done['outcome'] == 'provider_unavailable'
    assert done['actual_capabilities'] == []
    assert done['capability_coverage'] == {}
    assert done['coverage_status'] == 'unknown'
    assert done['fallback_used'] is True


@pytest.mark.parametrize('message,category', [
    ('这套设备的采购报价是多少？', 'price'),
    ('请给我客户的联系方式。', 'customer'),
    ('帮我编一个客户案例。', 'customer'),
])
def test_business_and_private_requests_use_inherited_redline_without_model(message, category):
    class NoCallAdapter:
        def chat(self, *args, **kwargs):
            raise AssertionError('redline should stop before provider')

    client = TestClient(create_app(adapter=NoCallAdapter()))
    done = terminal(client.post('/chat', json={'message': message}))
    assert done['outcome'] == 'safe_abstained'
    assert done['refusal_category'] == category
    assert done['fallback_used'] is False
    assert done['sources'] == []
    assert done['actual_capabilities'] == []
    assert done['answer']


@pytest.mark.parametrize('reason,status,expected', [
    ('read_error', None, 'provider_unavailable'),
    ('http_error', 400, 'provider_configuration_error'),
    ('http_error', 429, 'provider_rate_limited'),
    ('http_error', 500, 'provider_unavailable'),
    ('invalid_sse_json', None, 'provider_protocol_error'),
])
def test_buffered_anthropic_transport_failures_keep_their_failure_layer(reason, status, expected):
    from src.agent.anthropic_transport import AnthropicTransportError, AnthropicTransportTelemetry

    telemetry = AnthropicTransportTelemetry(
        mode='anthropic_sse_buffered', attempts=3, retry_count=2,
        retry_reasons=(reason,), first_event_ms=None, complete_message_ms=None,
        message_stop_received=False, discarded_incomplete_attempts=3,
        http_status=status,
    )

    class FailingAdapter:
        def chat(self, *args, **kwargs):
            raise AnthropicTransportError(reason, retryable=False, telemetry=telemetry)
            yield

    done = terminal(TestClient(create_app(adapter=FailingAdapter())).post(
        '/chat', json={'message': 'EGO 规格'}))
    assert done['outcome'] == expected
    assert done['fallback_used'] is True
    assert done['provider_status_code'] == status
    assert done['transport_reason'] == reason
