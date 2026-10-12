"""An authenticated browser turn must use DAQ Postgres state, never Dev SQLite."""

from fastapi.testclient import TestClient
import time

from daq_fae.app import create_app
from daq_fae.offline_adapter import OfflineAdapter
from src.platform_identity.service import InMemoryAuthenticatedSessionRepository
from src.agent.tracing import JsonlTraceSink, TraceRecorder, current_trace_ctx
from tests.test_api_transport import terminal
from tests.test_authenticated_persistence import Conversations, Feedback
from tests.test_authenticated_persistence import environment as persistence_environment
from tests.test_durable_state import store
from tests.test_platform_identity import CODE, FakePlatform, environment as identity_environment

pytest_plugins = ('tests.test_durable_state',)


def test_authenticated_stage_lifecycle_is_closed_and_persisted(
    tmp_path, monkeypatch, pg_database,
):
    env = {**identity_environment(tmp_path), **persistence_environment(tmp_path)}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    conversations = Conversations()
    trace_path = tmp_path / 'authenticated-traces.jsonl'
    app = create_app(
        provider_mode='offline', platform_client=FakePlatform(),
        identity_repository=InMemoryAuthenticatedSessionRepository(),
        conversation_repository=conversations, feedback_store=Feedback(), review_store=object(),
        durable_state=store(pg_database),
        trace_recorder=TraceRecorder([JsonlTraceSink(trace_path)]),
    )
    client = TestClient(app, base_url=env['DAQ_PLATFORM_PUBLIC_ORIGIN'])
    launch = client.post('/enterprise/session', json={'code': CODE},
                         headers={'Origin': env['DAQ_PLATFORM_PUBLIC_ORIGIN']})
    headers = {'Origin': env['DAQ_PLATFORM_PUBLIC_ORIGIN'],
               'X-DAQ-Enterprise-CSRF': launch.json()['csrf_token']}
    response = client.post('/chat', json={
        'message': '设备是 EGO', 'client_request_id': 'stage-lifecycle',
    }, headers=headers)
    done = terminal(response)
    import json
    stages = [json.loads(line[6:]) for line in response.text.splitlines()
              if line.startswith('data: ')]
    stages = [item for item in stages if 'stage' in item and 'status' in item]
    started = {item['stage'] for item in stages if item['status'] == 'running'}
    ended = {item['stage'] for item in stages if item['status'] in {'completed', 'error'}}
    assert started <= ended
    assert {item['stage'] for item in conversations.writes[0][1].stages} == \
        {item['stage'] for item in stages}
    assert all(item.get('trace_id') == done['trace_id'] for item in stages)
    tool_stages = [item for item in stages if item['stage'] == 'tool_call']
    assert all(isinstance(item.get('tool'), str) and item['tool'] for item in tool_stages)
    roots = [json.loads(line) for line in trace_path.read_text().splitlines()
             if json.loads(line)['type'] == 'root']
    assert roots[-1]['trace_id'] == done['trace_id']
    assert {item['stage'] for item in roots[-1]['metadata']['stages']} == \
        {item['stage'] for item in stages}


def test_authenticated_service_question_uses_service_contract_without_product_search(
    tmp_path, monkeypatch, pg_database,
):
    env = {**identity_environment(tmp_path), **persistence_environment(tmp_path)}
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    class ServiceAdapter:
        model = 'scripted-dev'
        tool_choice_strategy = 'submit_only_auto'

        def __init__(self):
            self.calls = []

        def chat(self, messages, tools=None, required_tool=None):
            assert current_trace_ctx() is not None
            self.calls.append(required_tool)
            if required_tool == 'submit_daq_request_schema':
                yield {'type': 'tool_call', 'id': 'schema', 'name': required_tool,
                       'arguments': {'confidence': 'high', 'goals': [
                           {'kind': 'service', 'capability': 'service_identity'},
                       ]}}
            else:
                yield {'type': 'tool_call', 'id': 'answer', 'name': 'submit_answer',
                       'arguments': {'outcome': 'resolved',
                                     'conclusion': '我是奥比中光内部数采 FAE 助手。'}}
            yield {'type': 'stop', 'stop_reason': 'tool_use', 'usage': None}

    adapter = ServiceAdapter()
    conversations = Conversations()
    app = create_app(
        adapter=adapter, platform_client=FakePlatform(),
        identity_repository=InMemoryAuthenticatedSessionRepository(),
        conversation_repository=conversations, feedback_store=Feedback(), review_store=object(),
        durable_state=store(pg_database),
    )
    client = TestClient(app, base_url=env['DAQ_PLATFORM_PUBLIC_ORIGIN'])
    launch = client.post('/enterprise/session', json={'code': CODE},
                         headers={'Origin': env['DAQ_PLATFORM_PUBLIC_ORIGIN']})
    headers = {'Origin': env['DAQ_PLATFORM_PUBLIC_ORIGIN'],
               'X-DAQ-Enterprise-CSRF': launch.json()['csrf_token']}
    done = terminal(client.post('/chat', json={
        'message': '介绍一下你自己', 'client_request_id': 'service-introduction',
    }, headers=headers))
    assert done['outcome'] == 'resolved'
    assert done['planned_capabilities'] == ['service_identity']
    assert done['sources'] == []
    assert '当前无法下结论' not in done['answer']
    assert adapter.calls.count('submit_daq_request_schema') == 1
    assert conversations.writes[0][1].stages


def test_authenticated_setup_statement_clarifies_and_persists_without_loop(
    tmp_path, monkeypatch, pg_database,
):
    env = {**identity_environment(tmp_path), **persistence_environment(tmp_path)}
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    class ContextAdapter:
        model = 'scripted-dev'
        tool_choice_strategy = 'submit_only_auto'

        def __init__(self):
            self.calls = 0

        def chat(self, messages, tools=None, required_tool=None):
            self.calls += 1
            assert required_tool == 'submit_daq_request_schema'
            yield {'type': 'tool_call', 'id': 'schema', 'name': required_tool,
                   'arguments': {'confidence': 'medium', 'goals': [
                       {'kind': 'product', 'capability': 'resolve_entity',
                        'entities': ['EGO']},
                       {'kind': 'unknown', 'capability': 'clarify'},
                   ]}}

    adapter = ContextAdapter()
    conversations = Conversations()
    platform = FakePlatform()
    app = create_app(
        adapter=adapter, platform_client=platform,
        identity_repository=InMemoryAuthenticatedSessionRepository(),
        conversation_repository=conversations, feedback_store=Feedback(), review_store=object(),
        durable_state=store(pg_database),
    )
    client = TestClient(app, base_url=env['DAQ_PLATFORM_PUBLIC_ORIGIN'])
    launch = client.post('/enterprise/session', json={'code': CODE},
                         headers={'Origin': env['DAQ_PLATFORM_PUBLIC_ORIGIN']})
    headers = {'Origin': env['DAQ_PLATFORM_PUBLIC_ORIGIN'],
               'X-DAQ-Enterprise-CSRF': launch.json()['csrf_token']}
    response = client.post('/chat', json={
        'message': '我用 EGO 双目 1600×1200 版本',
        'client_request_id': 'setup-without-question',
    }, headers=headers)
    done = terminal(response)
    assert adapter.calls == 1
    assert done['outcome'] == 'safe_abstained'
    assert done['synthesis_mode'] == 'context_clarification'
    assert done['sources'] == []
    assert '"stage": "loop"' not in response.text
    assert any(stage['stage'] == 'clarification'
               for stage in conversations.writes[0][1].stages)
    checkpoint = app.state.daq_durable_state.load_context(
        platform.subject, done['session_id'])
    assert checkpoint.state.task_context['setup_statement']['value'] == \
        '我用 EGO 双目 1600×1200 版本'


def test_schema_provider_400_is_configuration_error_before_loop(
    tmp_path, monkeypatch, pg_database,
):
    import httpx
    env = {**identity_environment(tmp_path), **persistence_environment(tmp_path)}
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    class BadSchemaAdapter:
        model = 'scripted-dev'
        tool_choice_strategy = 'submit_only_auto'

        def chat(self, messages, tools=None, required_tool=None):
            assert required_tool == 'submit_daq_request_schema'
            request = httpx.Request('POST', 'https://gateway.invalid/messages')
            response = httpx.Response(400, request=request)
            raise httpx.HTTPStatusError('bad request', request=request, response=response)
            yield  # pragma: no cover

    app = create_app(
        adapter=BadSchemaAdapter(), platform_client=FakePlatform(),
        identity_repository=InMemoryAuthenticatedSessionRepository(),
        conversation_repository=Conversations(), feedback_store=Feedback(), review_store=object(),
        durable_state=store(pg_database),
    )
    client = TestClient(app, base_url=env['DAQ_PLATFORM_PUBLIC_ORIGIN'])
    launch = client.post('/enterprise/session', json={'code': CODE},
                         headers={'Origin': env['DAQ_PLATFORM_PUBLIC_ORIGIN']})
    headers = {'Origin': env['DAQ_PLATFORM_PUBLIC_ORIGIN'],
               'X-DAQ-Enterprise-CSRF': launch.json()['csrf_token']}
    response = client.post('/chat', json={
        'message': '介绍你的服务', 'client_request_id': 'schema-provider-400',
    }, headers=headers)
    done = terminal(response)
    assert done['outcome'] == 'provider_configuration_error'
    assert done['fallback_used'] is True
    assert '"stage": "loop", "status": "running"' not in response.text


def test_authenticated_chat_persists_owner_turn_and_replays_without_model_rerun(
    tmp_path, monkeypatch, pg_database,
):
    env = {**identity_environment(tmp_path), **persistence_environment(tmp_path)}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    conversations = Conversations()
    platform = FakePlatform()
    app = create_app(
        provider_mode='offline', platform_client=platform,
        identity_repository=InMemoryAuthenticatedSessionRepository(),
        conversation_repository=conversations, feedback_store=Feedback(), review_store=object(),
        durable_state=store(pg_database), state_db_path=tmp_path / 'anonymous.sqlite3',
    )
    client = TestClient(app, base_url=env['DAQ_PLATFORM_PUBLIC_ORIGIN'])
    launch = client.post('/enterprise/session', json={'code': CODE},
                         headers={'Origin': env['DAQ_PLATFORM_PUBLIC_ORIGIN']})
    assert launch.status_code == 201
    headers = {'Origin': env['DAQ_PLATFORM_PUBLIC_ORIGIN'],
               'X-DAQ-Enterprise-CSRF': launch.json()['csrf_token']}
    body = {'message': '设备是 EGO', 'client_request_id': 'owner-first'}
    first = client.post('/chat', json=body, headers=headers)
    assert first.status_code == 200
    done = terminal(first)
    assert done['session_id'].startswith('daq:')
    assert done['agent_id'] == 'ai-daq-fae-agent'
    assert done['outcome'] == 'safe_abstained'
    assert len(conversations.writes) == 1
    assert conversations.writes[0][1].metadata['owner_subject_id'] == str(platform.subject.subject_id)
    assert terminal(client.post('/chat', json=body, headers=headers)) == done
    assert len(conversations.writes) == 1
    checkpoint = app.state.daq_durable_state.load_context(platform.subject, done['session_id'])
    assert checkpoint.state.task_context['values']['equipment']['value'] == ['EGO']
    second = client.post('/chat', json={'message': '它的版本呢？', 'session_id': done['session_id'],
                                        'client_request_id': 'owner-second'}, headers=headers)
    assert second.status_code == 200
    assert terminal(second)['session_id'] == done['session_id']
    assert len(conversations.writes) == 2
    assert not (tmp_path / 'anonymous.sqlite3').exists()


def test_unexpected_authenticated_execution_error_interrupts_reservation(
    tmp_path, monkeypatch, pg_database,
):
    env = {**identity_environment(tmp_path), **persistence_environment(tmp_path)}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    app = create_app(
        provider_mode='offline', platform_client=FakePlatform(),
        identity_repository=InMemoryAuthenticatedSessionRepository(),
        conversation_repository=Conversations(), feedback_store=Feedback(), review_store=object(),
        durable_state=store(pg_database),
    )
    client = TestClient(app, base_url=env['DAQ_PLATFORM_PUBLIC_ORIGIN'])
    launch = client.post('/enterprise/session', json={'code': CODE},
                         headers={'Origin': env['DAQ_PLATFORM_PUBLIC_ORIGIN']})
    headers = {'Origin': env['DAQ_PLATFORM_PUBLIC_ORIGIN'],
               'X-DAQ-Enterprise-CSRF': launch.json()['csrf_token']}

    def fail_toolbox(*args, **kwargs):
        raise RuntimeError('injected_failure')

    monkeypatch.setattr('daq_fae.authenticated_chat.DaqToolBox', fail_toolbox)
    body = {'message': 'EGO 规格', 'client_request_id': 'fails-open-check'}
    first = client.post('/chat', json=body, headers=headers)
    assert first.status_code == 200
    assert 'event: done' not in first.text
    assert 'authenticated_execution_failed' in first.text
    assert client.post('/chat', json=body, headers=headers).status_code == 409


def test_authenticated_attachment_upload_binds_only_to_owner_session(
    tmp_path, monkeypatch, pg_database,
):
    env = {**identity_environment(tmp_path), **persistence_environment(tmp_path)}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    platform = FakePlatform()
    app = create_app(
        provider_mode='offline', platform_client=platform,
        identity_repository=InMemoryAuthenticatedSessionRepository(),
        conversation_repository=Conversations(), feedback_store=Feedback(), review_store=object(),
        durable_state=store(pg_database), attachment_dir=tmp_path / 'attachments',
    )
    client = TestClient(app, base_url=env['DAQ_PLATFORM_PUBLIC_ORIGIN'])
    launch = client.post('/enterprise/session', json={'code': CODE},
                         headers={'Origin': env['DAQ_PLATFORM_PUBLIC_ORIGIN']})
    headers = {'Origin': env['DAQ_PLATFORM_PUBLIC_ORIGIN'],
               'X-DAQ-Enterprise-CSRF': launch.json()['csrf_token']}
    upload = client.post('/attachments', files=[('files', ('log.txt', b'status=ready', 'text/plain'))],
                         headers=headers)
    assert upload.status_code == 201
    aid = upload.json()['results'][0]['attachment']['attachment_id']
    assert app.state.attachment_store.get(aid).owner_subject_id == str(platform.subject.subject_id)
    done = terminal(client.post('/chat', json={'message': '看这份日志',
                                               'attachment_ids': [aid],
                                               'client_request_id': 'attachment-owner'}, headers=headers))
    assert done['session_id'].startswith('daq:')
    assert app.state.attachment_store.get(aid).bound_session_id == done['session_id']
    assert client.get(f'/attachments/{aid}').status_code == 200


def test_blocked_provider_keeps_authenticated_execution_lease_alive(tmp_path, monkeypatch, pg_database):
    env = {**identity_environment(tmp_path), **persistence_environment(tmp_path)}
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    class SlowAdapter(OfflineAdapter):
        def chat(self, messages, tools=None, required_tool=None):
            time.sleep(0.05)
            yield from super().chat(messages, tools, required_tool)

    app = create_app(
        adapter=SlowAdapter(), platform_client=FakePlatform(),
        identity_repository=InMemoryAuthenticatedSessionRepository(),
        conversation_repository=Conversations(), feedback_store=Feedback(), review_store=object(),
        durable_state=store(pg_database), heartbeat_interval_seconds=0.005,
        request_lease_renew_interval_seconds=0.01,
    )
    renewals = []
    original = app.state.daq_durable_state.renew

    def renew(*args, **kwargs):
        renewals.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(app.state.daq_durable_state, 'renew', renew)
    client = TestClient(app, base_url=env['DAQ_PLATFORM_PUBLIC_ORIGIN'])
    launch = client.post('/enterprise/session', json={'code': CODE},
                         headers={'Origin': env['DAQ_PLATFORM_PUBLIC_ORIGIN']})
    headers = {'Origin': env['DAQ_PLATFORM_PUBLIC_ORIGIN'],
               'X-DAQ-Enterprise-CSRF': launch.json()['csrf_token']}
    response = client.post('/chat', json={'message': '设备版本', 'client_request_id': 'slow-owner'},
                           headers=headers)
    assert response.status_code == 200
    assert renewals
    assert terminal(response)['outcome'] == 'safe_abstained'


def test_progress_stream_renews_even_without_heartbeat(tmp_path, monkeypatch, pg_database):
    env = {**identity_environment(tmp_path), **persistence_environment(tmp_path)}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    app = create_app(
        provider_mode='offline', platform_client=FakePlatform(),
        identity_repository=InMemoryAuthenticatedSessionRepository(),
        conversation_repository=Conversations(), feedback_store=Feedback(), review_store=object(),
        durable_state=store(pg_database), heartbeat_interval_seconds=0.1,
        request_lease_renew_interval_seconds=0.01,
    )
    renewals = []
    original = app.state.daq_durable_state.renew

    def renew(*args, **kwargs):
        renewals.append(True)
        return original(*args, **kwargs)

    def chatty_runtime(*args, **kwargs):
        for index in range(12):
            time.sleep(0.005)
            yield {'type': 'tool_call', 'name': 'search_knowledge', 'index': index}
        yield {'type': 'done', 'answer': '资料不足', 'outcome': 'safe_abstained',
               'sources': [], 'capability_coverage': {'lookup_spec': 'empty'}}

    monkeypatch.setattr(app.state.daq_durable_state, 'renew', renew)
    monkeypatch.setattr('daq_fae.authenticated_chat.LoopRuntime.run', chatty_runtime)
    client = TestClient(app, base_url=env['DAQ_PLATFORM_PUBLIC_ORIGIN'])
    launch = client.post('/enterprise/session', json={'code': CODE},
                         headers={'Origin': env['DAQ_PLATFORM_PUBLIC_ORIGIN']})
    headers = {'Origin': env['DAQ_PLATFORM_PUBLIC_ORIGIN'],
               'X-DAQ-Enterprise-CSRF': launch.json()['csrf_token']}
    response = client.post('/chat', json={'message': 'EGO 规格', 'client_request_id': 'chatty-lease'},
                           headers=headers)
    assert terminal(response)['outcome'] == 'safe_abstained'
    assert renewals


def test_execution_ownership_is_checked_before_turn_persistence(tmp_path, monkeypatch, pg_database):
    env = {**identity_environment(tmp_path), **persistence_environment(tmp_path)}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    conversations = Conversations()
    app = create_app(
        provider_mode='offline', platform_client=FakePlatform(),
        identity_repository=InMemoryAuthenticatedSessionRepository(),
        conversation_repository=conversations, feedback_store=Feedback(), review_store=object(),
        durable_state=store(pg_database),
    )

    def expired(*args, **kwargs):
        from daq_fae.durable_state import RequestInterrupted
        raise RequestInterrupted('execution_lease_expired')

    monkeypatch.setattr(app.state.daq_durable_state, 'renew', expired)
    client = TestClient(app, base_url=env['DAQ_PLATFORM_PUBLIC_ORIGIN'])
    launch = client.post('/enterprise/session', json={'code': CODE},
                         headers={'Origin': env['DAQ_PLATFORM_PUBLIC_ORIGIN']})
    headers = {'Origin': env['DAQ_PLATFORM_PUBLIC_ORIGIN'],
               'X-DAQ-Enterprise-CSRF': launch.json()['csrf_token']}
    response = client.post('/chat', json={'message': 'EGO 规格', 'client_request_id': 'expired-before-save'},
                           headers=headers)
    assert 'event: done' not in response.text
    assert not conversations.writes


def test_authenticated_turn_records_attachment_archive_relations(tmp_path, monkeypatch, pg_database):
    env = {**identity_environment(tmp_path), **persistence_environment(tmp_path)}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    conversations = Conversations()
    app = create_app(
        provider_mode='offline', platform_client=FakePlatform(),
        identity_repository=InMemoryAuthenticatedSessionRepository(),
        conversation_repository=conversations, feedback_store=Feedback(), review_store=object(),
        durable_state=store(pg_database), attachment_dir=tmp_path / 'attachments',
    )

    class ArchiveService:
        def __init__(self):
            self.calls = []

        def prepare_turn(self, attachment_ids, *, explicit_attachment_ids, answer_at):
            assert not model_started, 'archive protection must precede model execution'
            self.calls.append((attachment_ids, explicit_attachment_ids, answer_at))
            return ('archive-relation',)

    model_started = False
    original_run = __import__('daq_fae.authenticated_chat', fromlist=['LoopRuntime']).LoopRuntime.run

    def checked_run(*args, **kwargs):
        nonlocal model_started
        model_started = True
        yield from original_run(*args, **kwargs)

    monkeypatch.setattr('daq_fae.authenticated_chat.LoopRuntime.run', checked_run)
    archive = ArchiveService()
    app.state.attachment_archive_service = archive
    client = TestClient(app, base_url=env['DAQ_PLATFORM_PUBLIC_ORIGIN'])
    launch = client.post('/enterprise/session', json={'code': CODE},
                         headers={'Origin': env['DAQ_PLATFORM_PUBLIC_ORIGIN']})
    headers = {'Origin': env['DAQ_PLATFORM_PUBLIC_ORIGIN'],
               'X-DAQ-Enterprise-CSRF': launch.json()['csrf_token']}
    upload = client.post('/attachments', files=[('files', ('log.txt', b'status=ready', 'text/plain'))],
                         headers=headers)
    aid = upload.json()['results'][0]['attachment']['attachment_id']
    response = client.post('/chat', json={'message': '看这份日志', 'attachment_ids': [aid],
                                          'client_request_id': 'archive-turn'}, headers=headers)
    assert terminal(response)['outcome'] == 'safe_abstained'
    assert archive.calls[0][0] == [aid]
    assert archive.calls[0][1] == [aid]
    assert conversations.writes[0][2] == ('archive-relation',)
