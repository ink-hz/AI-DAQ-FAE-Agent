"""Verified login → real DAQ PostgreSQL ownership and account isolation.

The Platform verification boundary and model are test doubles. Cookie/CSRF,
request execution, conversation repository, durable state and SQL are real.
"""
from dataclasses import replace
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import psycopg
from psycopg.rows import dict_row

from daq_fae.app import create_app
from daq_fae.authenticated_persistence import _FailClosedFeedbackWriter
from src.platform_identity.service import InMemoryAuthenticatedSessionRepository
from src.storage.authenticated_conversations import AuthenticatedConversationRepository, ConversationContentCodec
from src.storage.postgres_data_flywheel import PostgresDataFlywheelStore
from src.storage.review_center import PostgresReviewCenterStore
from tests.test_api_transport import terminal
from tests.test_authenticated_persistence import environment as persistence_environment
from tests.test_durable_state import store
from tests.test_platform_identity import FakePlatform, environment as identity_environment

pytest_plugins = ('tests.test_durable_state',)


def test_verified_login_persists_owner_and_account_switch_cannot_reassign_it(tmp_path, monkeypatch, pg_database):
    root = Path(__file__).resolve().parents[1]
    with psycopg.connect(pg_database, autocommit=True) as c:
        for migration in sorted((root / 'migrations/shared').glob('0*.sql')):
            c.execute(migration.read_text())
    first = FakePlatform().subject
    other_id = UUID('d46114bb-e01c-4f69-b67f-ef0d21f62e5e')
    second = replace(first, subject_id=other_id, internal_user_id=other_id, identity_binding_id=uuid4())

    class Accounts:
        async def exchange(self, code):
            return first if code == 'a'*43 else second

        async def validate(self, binding_id):
            return first if binding_id == first.identity_binding_id else second

    env = {**identity_environment(tmp_path), **persistence_environment(tmp_path),
           'DAQ_PLATFORM_ALLOWED_SUBJECT_IDS': f'{first.subject_id},{second.subject_id}',
           'DAQ_TRACE_LOG_PATH': str(tmp_path/'traces.jsonl')}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    codec = ConversationContentCodec.from_file(env['DAQ_AUTHENTICATED_CONTENT_KEYRING_FILE'])
    app = create_app(provider_mode='offline', platform_client=Accounts(),
        identity_repository=InMemoryAuthenticatedSessionRepository(),
        conversation_repository=AuthenticatedConversationRepository(pg_database, codec=codec),
        feedback_store=PostgresDataFlywheelStore(pg_database, _FailClosedFeedbackWriter()),
        review_store=PostgresReviewCenterStore(pg_database), durable_state=store(pg_database))
    with TestClient(app, base_url=env['DAQ_PLATFORM_PUBLIC_ORIGIN']) as client:
        assert client.get('/authenticated/conversations').status_code == 401

        def login(code):
            response = client.post('/enterprise/session', json={'code': code*43},
                                  headers={'Origin': env['DAQ_PLATFORM_PUBLIC_ORIGIN']})
            assert response.status_code == 201
            return {'Origin': env['DAQ_PLATFORM_PUBLIC_ORIGIN'],
                    'X-DAQ-Enterprise-CSRF': response.json()['csrf_token']}

        headers = login('a')
        done_a = terminal(client.post('/chat', json={'message': '设备是 EGO',
            'client_request_id': 'identity-a'}, headers=headers))
        sid = done_a['session_id']
        terminal(client.post('/chat', json={'message': '操作系统是 Android',
            'session_id': sid, 'client_request_id': 'identity-a-followup'}, headers=headers))
        headers = login('b')
        assert client.get(f'/authenticated/conversations/{sid}').status_code == 404
        assert client.post('/chat', json={'message': '设备是 EGO', 'session_id': sid,
            'client_request_id': 'foreign-session'}, headers=headers).status_code == 404
        done_b = terminal(client.post('/chat', json={'message': '设备是 EGO',
            'client_request_id': 'identity-b'}, headers=headers))
        assert done_b['session_id'] != sid
        ids_b = {item['session_id'] for item in client.get('/authenticated/conversations').json()['items']}
        assert ids_b == {done_b['session_id']}
        login('a')
        assert {item['session_id'] for item in client.get('/authenticated/conversations').json()['items']} == {sid}
        detail = client.get(f'/authenticated/conversations/{sid}').json()
        assert len(detail['turns']) == 2
        assert client.get(f"/authenticated/conversations/{done_b['session_id']}").status_code == 404

    with psycopg.connect(pg_database, row_factory=dict_row) as c:
        tables = {name: c.execute(f'select * from {name} order by id').fetchall()
                  for name in ('chat_sessions', 'chat_turns', 'turn_feedback', 'turn_reviews')}
    expected = {sid: str(first.subject_id), done_b['session_id']: str(second.subject_id)}
    assert len(tables['chat_sessions']) == 2 and len(tables['chat_turns']) == 3
    for row in tables['chat_sessions']:
        assert str(row['owner_subject_id']) == row['user_id'] == expected[row['external_session_id']]
        assert row['owner_subject_type'] == 'enterprise_member'
        assert row['metadata']['authentication_mode'] == 'platform_enterprise'
    for row in tables['chat_turns']:
        assert row['metadata']['owner_subject_id'] == expected[row['external_session_id']]
        assert row['metadata']['owner_subject_type'] == 'enterprise_member'
        assert row['metadata']['authentication_mode'] == 'platform_enterprise'
    # Optional synthetic producer snapshot for the separate Platform consumer gate.
    if target := os.environ.get('DAQ_IDENTITY_EVIDENCE_FILE'):
        Path(target).write_text(json.dumps(tables, default=str, ensure_ascii=False))
