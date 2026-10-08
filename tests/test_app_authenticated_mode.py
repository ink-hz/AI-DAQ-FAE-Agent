"""Authenticated DAQ assembly must fail before exposing anonymous Dev routes."""

import pytest
from fastapi.testclient import TestClient

from daq_fae.app import create_app
from src.platform_identity.service import InMemoryAuthenticatedSessionRepository
from tests.test_authenticated_persistence import Conversations, Feedback
from tests.test_authenticated_persistence import environment as persistence_environment
from tests.test_platform_identity import CODE, FakePlatform, environment as identity_environment


def test_enabled_platform_identity_requires_dedicated_persistence(monkeypatch, tmp_path):
    monkeypatch.setenv("DAQ_PLATFORM_IDENTITY_ENABLED", "true")
    monkeypatch.delenv("DAQ_DATABASE_URL", raising=False)
    monkeypatch.delenv("DAQ_AUTHENTICATED_CONTENT_KEYRING_FILE", raising=False)
    with pytest.raises(ValueError, match="daq_authenticated_persistence_configuration_missing"):
        create_app(provider_mode="offline", state_db_path=tmp_path / "dev.sqlite3")


def test_authenticated_assembly_exposes_no_anonymous_state_or_chat(tmp_path, monkeypatch):
    env = {**identity_environment(tmp_path), **persistence_environment(tmp_path)}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    local_db = tmp_path / 'anonymous.sqlite3'
    app = create_app(provider_mode='offline', state_db_path=local_db)
    assert app.state.local_state is None
    assert not local_db.exists()
    client = TestClient(app, base_url=env['DAQ_PLATFORM_PUBLIC_ORIGIN'])
    health = client.get('/health').json()
    assert health['platform_identity_enabled'] is True
    assert health['session_persistence'] == 'postgres_daq_authenticated'
    assert client.post('/chat', json={'message': 'EGO 规格', 'client_request_id': 'one'}).status_code == 401


def test_authenticated_app_exchanges_daq_identity_with_scoped_cookie(tmp_path, monkeypatch):
    env = {**identity_environment(tmp_path), **persistence_environment(tmp_path)}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    app = create_app(
        provider_mode='offline', platform_client=FakePlatform(),
        identity_repository=InMemoryAuthenticatedSessionRepository(),
    )
    client = TestClient(app, base_url=env['DAQ_PLATFORM_PUBLIC_ORIGIN'])
    response = client.post('/enterprise/session', json={'code': CODE},
                           headers={'Origin': env['DAQ_PLATFORM_PUBLIC_ORIGIN']})
    assert response.status_code == 201
    assert '__Host-daq_enterprise_session' in client.cookies
    assert '__Host-fae_enterprise_session' not in client.cookies
    assert client.get('/enterprise/session').status_code == 200


def test_authenticated_history_and_feedback_use_owner_scoped_repository(tmp_path, monkeypatch):
    env = {**identity_environment(tmp_path), **persistence_environment(tmp_path)}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    conversations, feedback = Conversations(), Feedback()
    platform = FakePlatform()
    app = create_app(
        provider_mode='offline', platform_client=platform,
        identity_repository=InMemoryAuthenticatedSessionRepository(),
        conversation_repository=conversations, feedback_store=feedback, review_store=object(),
    )
    session = app.state.daq_authenticated_persistence.create_session(platform.subject)
    session.append_message('user', 'EGO 版本')
    session.append_message('assistant', '请提供版本')
    conversations.sessions[session.session_id] = session
    client = TestClient(app, base_url=env['DAQ_PLATFORM_PUBLIC_ORIGIN'])
    launch = client.post('/enterprise/session', json={'code': CODE},
                         headers={'Origin': env['DAQ_PLATFORM_PUBLIC_ORIGIN']})
    assert launch.status_code == 201
    response = client.get('/history', params={'session_id': session.session_id})
    assert response.status_code == 200
    assert response.json()['messages'][-1]['content'] == '请提供版本'
    result = client.post('/feedback', json={'session_id': session.session_id,
                                             'message_index': 1, 'rating': 'bad'},
                         headers={'Origin': env['DAQ_PLATFORM_PUBLIC_ORIGIN'],
                                  'X-DAQ-Enterprise-CSRF': launch.json()['csrf_token']})
    assert result.status_code == 200 and result.json()['feedback_id'] == 'feedback-1'
    assert feedback.records[-1].metadata['owner_subject_id'] == str(platform.subject.subject_id)


def test_authenticated_navigation_and_review_are_scoped(tmp_path, monkeypatch):
    env = {**identity_environment(tmp_path), **persistence_environment(tmp_path),
           'DAQ_PLATFORM_REVIEWER_SUBJECT_IDS': ''}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    conversations, platform = Conversations(), FakePlatform()
    app = create_app(
        provider_mode='offline', platform_client=platform,
        identity_repository=InMemoryAuthenticatedSessionRepository(),
        conversation_repository=conversations, feedback_store=Feedback(), review_store=object(),
    )
    session = app.state.daq_authenticated_persistence.create_session(platform.subject)
    conversations.sessions[session.session_id] = session
    client = TestClient(app, base_url=env['DAQ_PLATFORM_PUBLIC_ORIGIN'])
    client.post('/enterprise/session', json={'code': CODE},
                headers={'Origin': env['DAQ_PLATFORM_PUBLIC_ORIGIN']})
    assert client.get('/authenticated/conversations').json()['items'] == []
    detail = client.get(f'/authenticated/conversations/{session.session_id}')
    assert detail.status_code == 200 and detail.json()['session_id'] == session.session_id
    assert client.get('/review/sessions').status_code == 403


def test_authenticated_browser_uses_daq_entry_and_root_api(tmp_path, monkeypatch):
    env = {**identity_environment(tmp_path), **persistence_environment(tmp_path)}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    dist = tmp_path / 'dist'
    dist.mkdir()
    (dist / 'index.html').write_text('<html><head></head><body>DAQ workspace</body></html>')
    (dist / 'assets').mkdir()
    app = create_app(provider_mode='offline', webui_dist=dist)
    client = TestClient(app, base_url=env['DAQ_PLATFORM_PUBLIC_ORIGIN'])
    html = client.get('/daq/').text
    assert 'DAQ workspace' in html
    assert 'content="/daq"' in html
    assert 'name="fae-api-base" content=""' in html
    assert client.get('/daq/conversations/example').status_code == 200
    assert client.get('/app/').status_code == 404
