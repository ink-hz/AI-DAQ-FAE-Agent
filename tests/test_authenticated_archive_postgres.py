"""Real DAQ Postgres turn and attachment archive lifecycle in an isolated DB."""
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
from fastapi.testclient import TestClient

from daq_fae.app import create_app
from src.platform_identity.service import InMemoryAuthenticatedSessionRepository
from src.storage.authenticated_conversations import AuthenticatedConversationRepository, ConversationContentCodec
from src.storage.postgres_data_flywheel import PostgresDataFlywheelStore
from src.storage.review_center import PostgresReviewCenterStore
from tests.test_api_transport import terminal
from tests.test_authenticated_persistence import environment as persistence_environment
from tests.test_durable_state import store as durable_store
from tests.test_platform_identity import CODE, FakePlatform, environment as identity_environment

pytest_plugins = ('tests.test_durable_state',)


def test_authenticated_archive_has_daq_manifest_and_owner_history(tmp_path, monkeypatch, pg_database):
    migration_dir = Path(__file__).resolve().parents[1] / 'migrations' / 'shared'
    with psycopg.connect(pg_database, autocommit=True) as connection:
        for migration in sorted(migration_dir.glob('0*.sql')):
            connection.execute(migration.read_text())
    durable = durable_store(pg_database)
    env = {**identity_environment(tmp_path), **persistence_environment(tmp_path),
           'DAQ_ATTACHMENT_ARCHIVE_ENABLED': 'true',
           'DAQ_TRACE_LOG_PATH': str(tmp_path / 'traces.jsonl')}
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    from src.attachments.archive_repository import AttachmentArchiveRepository
    from daq_fae.authenticated_persistence import _FailClosedFeedbackWriter
    archive_repo = AttachmentArchiveRepository(pg_database, agent_id='ai-daq-fae-agent')
    codec = ConversationContentCodec.from_file(env['DAQ_AUTHENTICATED_CONTENT_KEYRING_FILE'])
    app = create_app(
        provider_mode='offline', platform_client=FakePlatform(),
        identity_repository=InMemoryAuthenticatedSessionRepository(),
        conversation_repository=AuthenticatedConversationRepository(pg_database, codec=codec),
        feedback_store=PostgresDataFlywheelStore(pg_database, _FailClosedFeedbackWriter()),
        review_store=PostgresReviewCenterStore(pg_database), durable_state=durable,
        archive_repository=archive_repo, attachment_dir=tmp_path / 'attachments',
    )
    with TestClient(app, base_url=env['DAQ_PLATFORM_PUBLIC_ORIGIN']) as client:
        assert client.get('/health').json()['attachment_archive']['ready'] is True
        launch = client.post('/enterprise/session', json={'code': CODE},
                             headers={'Origin': env['DAQ_PLATFORM_PUBLIC_ORIGIN']})
        assert launch.status_code == 201
        headers = {'Origin': env['DAQ_PLATFORM_PUBLIC_ORIGIN'],
                   'X-DAQ-Enterprise-CSRF': launch.json()['csrf_token']}
        upload = client.post('/attachments', files=[('files', ('log.txt', b'status=ready', 'text/plain'))],
                             headers=headers)
        assert upload.status_code == 201
        aid = upload.json()['results'][0]['attachment']['attachment_id']
        answer = client.post('/chat', json={'message': '看这份日志', 'attachment_ids': [aid],
                                            'client_request_id': f'archive-{uuid4()}'}, headers=headers)
        done = terminal(answer)
        assert done['outcome'] == 'safe_abstained'
        sid = done['session_id']
        detail = client.get(f'/authenticated/conversations/{sid}').json()
        assert detail['attachments'][0]['source_id']
        assert detail['attachments'][0]['status'] == 'pending'

        archive = app.state.attachment_archive_service
        page = archive.list_pending(limit=10, cursor=None)
        manifest = next(item for item in page.items if item.external_session_id == sid)
        assert manifest.as_dict()['agent_id'] == 'ai-daq-fae-agent'
        relation_id = UUID(manifest.relation_id)
        assert archive.read(relation_id, 'original') == b'status=ready'
        platform_attachment_id = uuid4()
        archive.ack_archived(relation_id=relation_id, sha256=manifest.sha256,
                             platform_attachment_id=platform_attachment_id, archived_at=datetime.now(UTC))
        assert client.get(f'/authenticated/conversations/{sid}').json()['attachments'][0]['status'] == 'archived'
        assert client.delete(f'/attachments/{aid}', headers=headers).status_code == 204
        pending_delete = next(item for item in archive.list_pending(limit=10, cursor=None).items
                              if item.relation_id == manifest.relation_id)
        assert pending_delete.as_dict()['agent_id'] == 'ai-daq-fae-agent'
        assert pending_delete.as_dict()['operation'] == 'delete'
        archive.ack_deleted(relation_id, platform_attachment_id)
        assert client.get(f'/authenticated/conversations/{sid}').json()['attachments'][0]['status'] == 'deleted'
