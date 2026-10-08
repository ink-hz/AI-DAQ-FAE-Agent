"""The visible turn and replay terminal must commit in one transaction."""
from pathlib import Path

import psycopg
from fastapi.testclient import TestClient

from daq_fae.app import create_app
from daq_fae.durable_state import DurableStateError
from src.platform_identity.service import InMemoryAuthenticatedSessionRepository
from src.storage.authenticated_conversations import AuthenticatedConversationRepository, ConversationContentCodec
from src.storage.postgres_data_flywheel import PostgresDataFlywheelStore
from src.storage.review_center import PostgresReviewCenterStore
from tests.test_authenticated_persistence import environment as persistence_environment
from tests.test_durable_state import store as durable_store
from tests.test_platform_identity import CODE, FakePlatform, environment as identity_environment

pytest_plugins = ('tests.test_durable_state',)


def test_context_finish_failure_rolls_back_owner_turn(tmp_path, monkeypatch, pg_database):
    root = Path(__file__).resolve().parents[1]
    with psycopg.connect(pg_database, autocommit=True) as connection:
        for path in sorted((root / 'migrations/shared').glob('0*.sql')):
            connection.execute(path.read_text())
    durable = durable_store(pg_database)
    env = {**identity_environment(tmp_path), **persistence_environment(tmp_path)}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    from daq_fae.authenticated_persistence import _FailClosedFeedbackWriter
    codec = ConversationContentCodec.from_file(env['DAQ_AUTHENTICATED_CONTENT_KEYRING_FILE'])
    app = create_app(
        provider_mode='offline', platform_client=FakePlatform(),
        identity_repository=InMemoryAuthenticatedSessionRepository(),
        conversation_repository=AuthenticatedConversationRepository(pg_database, codec=codec),
        feedback_store=PostgresDataFlywheelStore(pg_database, _FailClosedFeedbackWriter()),
        review_store=PostgresReviewCenterStore(pg_database), durable_state=durable,
    )

    def fail_context(*args, **kwargs):
        raise DurableStateError('injected_context_write_failure')

    monkeypatch.setattr(durable, '_save_context', fail_context)
    with TestClient(app, base_url=env['DAQ_PLATFORM_PUBLIC_ORIGIN']) as client:
        launch = client.post('/enterprise/session', json={'code': CODE},
                             headers={'Origin': env['DAQ_PLATFORM_PUBLIC_ORIGIN']})
        headers = {'Origin': env['DAQ_PLATFORM_PUBLIC_ORIGIN'],
                   'X-DAQ-Enterprise-CSRF': launch.json()['csrf_token']}
        response = client.post('/chat', json={'message': '设备是 EGO',
                                             'client_request_id': 'atomic-failure'}, headers=headers)
        assert 'event: done' not in response.text
        with psycopg.connect(pg_database) as connection:
            assert connection.execute('select count(*) from chat_turns').fetchone()[0] == 0
            assert connection.execute('select count(*) from chat_sessions').fetchone()[0] == 0
