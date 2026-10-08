"""An authenticated browser turn must use DAQ Postgres state, never Dev SQLite."""

from fastapi.testclient import TestClient
import time

from daq_fae.app import create_app
from daq_fae.offline_adapter import OfflineAdapter
from src.platform_identity.service import InMemoryAuthenticatedSessionRepository
from tests.test_api_transport import terminal
from tests.test_authenticated_persistence import Conversations, Feedback
from tests.test_authenticated_persistence import environment as persistence_environment
from tests.test_durable_state import store
from tests.test_platform_identity import CODE, FakePlatform, environment as identity_environment

pytest_plugins = ('tests.test_durable_state',)


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
