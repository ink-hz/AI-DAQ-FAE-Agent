"""DAQ identity assembly contracts; no Platform or database network calls."""
import base64
import json
from dataclasses import replace
from uuid import UUID

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from src.platform_identity.models import PlatformSubject
from src.platform_identity.service import InMemoryAuthenticatedSessionRepository

SUBJECT = UUID('b78b3205-2c86-424a-a217-775382c208bd')
BINDING = UUID('6dbedcf8-5263-493f-91f5-6324be037d7c')
ORIGIN = 'https://agent.orbbec.com.cn'
COOKIE = '__Host-daq_enterprise_session'
HEADER = 'X-DAQ-Enterprise-CSRF'
CODE = 'l' * 43


class FakePlatform:
    def __init__(self):
        self.subject = PlatformSubject(
            subject_id=SUBJECT, subject_type='enterprise_member', internal_user_id=SUBJECT,
            identity_binding_id=BINDING, agent_id='ai-daq-fae-agent', active=True,
            display_name='Internal FAE',
        )

    async def exchange(self, code):
        return self.subject

    async def validate(self, binding_id):
        return self.subject


def environment(tmp_path):
    keyring = tmp_path / 'daq-session-keys.json'
    keyring.write_text(json.dumps({'active_version': 1, 'keys': {'1': base64.b64encode(b'd' * 32).decode()}}))
    keyring.chmod(0o600)
    return {
        'DAQ_PLATFORM_IDENTITY_ENABLED': 'true',
        'DAQ_DATABASE_URL': 'postgresql://daq.test/daq',
        'DAQ_PLATFORM_IDENTITY_BASE_URL': 'http://127.0.0.1:8080',
        'DAQ_PLATFORM_PUBLIC_ORIGIN': ORIGIN,
        'DAQ_PLATFORM_SESSION_KEYRING_FILE': str(keyring),
        'DAQ_PLATFORM_ALLOWED_SUBJECT_IDS': str(SUBJECT),
    }


def application(tmp_path):
    from daq_fae.platform_identity import configure_platform_identity
    app = FastAPI()
    platform = FakePlatform()
    repository = InMemoryAuthenticatedSessionRepository()
    configure_platform_identity(app, environ=environment(tmp_path), repository=repository,
                                platform_client=platform)

    @app.post('/chat')
    def chat(request: Request):
        return {'agent_id': request.state.platform_identity.agent_id}

    @app.get('/history')
    def history():
        return {'ok': True}

    return TestClient(app, base_url=ORIGIN), platform, repository, app


@pytest.mark.parametrize('missing', [
    'DAQ_DATABASE_URL', 'DAQ_PLATFORM_IDENTITY_BASE_URL', 'DAQ_PLATFORM_PUBLIC_ORIGIN',
    'DAQ_PLATFORM_SESSION_KEYRING_FILE', 'DAQ_PLATFORM_ALLOWED_SUBJECT_IDS',
])
def test_enabled_identity_missing_config_fails_closed(tmp_path, missing):
    from daq_fae.platform_identity import configure_platform_identity
    settings = environment(tmp_path)
    settings.pop(missing)
    with pytest.raises(ValueError, match='daq_platform_identity_configuration_missing'):
        configure_platform_identity(FastAPI(), environ=settings)


def test_identity_disabled_is_explicit_and_registers_no_platform_routes():
    from daq_fae.platform_identity import configure_platform_identity
    app = FastAPI()
    assert configure_platform_identity(app, environ={}) is None
    assert not any(getattr(route, 'path', '') == '/enterprise/session' for route in app.routes)


def test_daq_launch_session_and_csrf_are_scoped_and_missing_identity_denied(tmp_path):
    client, _, _, _ = application(tmp_path)
    assert client.post('/chat').status_code == 401
    assert client.get('/history').status_code == 401
    assert client.post('/enterprise/session', json={'code': CODE}).status_code == 403
    launch = client.post('/enterprise/session', json={'code': CODE}, headers={'Origin': ORIGIN})
    assert launch.status_code == 201
    assert COOKIE in client.cookies
    assert '__Host-fae_enterprise_session' not in client.cookies
    assert 'HttpOnly' in launch.headers['set-cookie'] and 'Secure' in launch.headers['set-cookie']
    csrf = launch.json()['csrf_token']
    assert client.get('/enterprise/session').status_code == 200
    assert client.post('/chat', headers={'Origin': ORIGIN, 'X-FAE-Enterprise-CSRF': csrf}).status_code == 403
    assert client.post('/chat', headers={'Origin': ORIGIN, HEADER: csrf}).json()['agent_id'] == 'ai-daq-fae-agent'
    assert client.delete('/enterprise/session', headers={'Origin': ORIGIN, HEADER: csrf}).status_code == 204
    assert client.get('/history').status_code == 401


@pytest.mark.parametrize('updates', [
    {'agent_id': 'ai-fae-agent'}, {'active': False},
    {'subject_id': UUID('d46114bb-e01c-4f69-b67f-ef0d21f62e5e'),
     'internal_user_id': UUID('d46114bb-e01c-4f69-b67f-ef0d21f62e5e')},
    {'subject_type': 'partner_operator', 'internal_user_id': None},
])
def test_foreign_or_unapproved_launch_cannot_issue_session(tmp_path, updates):
    client, platform, repository, _ = application(tmp_path)
    platform.subject = replace(platform.subject, **updates)
    response = client.post('/enterprise/session', json={'code': CODE}, headers={'Origin': ORIGIN})
    assert response.status_code in {401, 403}
    assert not repository._records
    assert COOKIE not in client.cookies


def test_foreign_cached_session_rejected_and_fae_cookie_not_accepted(tmp_path):
    client, _, repository, _ = application(tmp_path)
    client.cookies.set('__Host-fae_enterprise_session', 'foreign-token')
    assert client.get('/enterprise/session').status_code == 401
    client.post('/enterprise/session', json={'code': CODE}, headers={'Origin': ORIGIN})
    record = next(iter(repository._records.values()))
    record.agent_id = 'ai-fae-agent'
    assert client.get('/history').status_code == 401
    assert client.cookies.get('__Host-fae_enterprise_session') == 'foreign-token'


def test_production_assembly_passes_trusted_agent_to_platform_client(tmp_path, monkeypatch):
    from daq_fae import platform_identity
    seen = {}

    class Client(FakePlatform):
        def __init__(self, base_url, *, agent_id):
            super().__init__()
            seen.update(base_url=base_url, agent_id=agent_id)

        async def aclose(self):
            pass

    monkeypatch.setattr(platform_identity, 'PlatformIdentityClient', Client)
    platform_identity.configure_platform_identity(
        FastAPI(), environ=environment(tmp_path), repository=InMemoryAuthenticatedSessionRepository(),
    )
    assert seen == {'base_url': 'http://127.0.0.1:8080', 'agent_id': 'ai-daq-fae-agent'}


@pytest.mark.parametrize('settings', [
    {'DAQ_PLATFORM_IDENTITY_ENABLED': 'yes'},
    {'DAQ_PLATFORM_ALLOWED_SUBJECT_IDS': ''},
    {'DAQ_PLATFORM_ALLOWED_SUBJECT_IDS': 'not-a-uuid'},
    {'DAQ_PLATFORM_SESSION_KEYRING_FILE': 'relative/keys.json'},
])
def test_invalid_enabled_configuration_rejected_before_routes(tmp_path, settings):
    from daq_fae.platform_identity import configure_platform_identity
    env = {**environment(tmp_path), **settings}
    app = FastAPI()
    with pytest.raises(ValueError):
        configure_platform_identity(app, environ=env, platform_client=FakePlatform())
    assert not any(getattr(route, 'path', '') == '/enterprise/session' for route in app.routes)


def test_identity_closes_owned_client_with_existing_application_lifespan(tmp_path, monkeypatch):
    from contextlib import asynccontextmanager
    from daq_fae import platform_identity
    lifecycle = []

    class Client(FakePlatform):
        def __init__(self, *args, **kwargs):
            super().__init__()

        async def aclose(self):
            lifecycle.append('identity_closed')

    @asynccontextmanager
    async def existing_lifespan(app):
        lifecycle.append('app_started')
        yield
        lifecycle.append('app_stopped')

    app = FastAPI(lifespan=existing_lifespan)
    monkeypatch.setattr(platform_identity, 'PlatformIdentityClient', Client)
    platform_identity.configure_platform_identity(
        app, environ=environment(tmp_path), repository=InMemoryAuthenticatedSessionRepository(),
    )
    with TestClient(app, base_url=ORIGIN):
        pass
    assert lifecycle == ['app_started', 'app_stopped', 'identity_closed']
