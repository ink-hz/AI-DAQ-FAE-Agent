"""Real DAQ Postgres turn and attachment archive lifecycle in an isolated DB."""
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
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
        old_attachment_id = f'old-{uuid4()}'
        with psycopg.connect(pg_database) as connection:
            old_session = f'old-{uuid4()}'
            old_session_id = connection.execute(
                "insert into chat_sessions (external_session_id,channel,metadata) "
                "values (%s,'fae','{\"agent_id\":\"ai-fae-agent\"}'::jsonb) returning id",
                (old_session,),
            ).fetchone()[0]
            old_turn_id = connection.execute(
                "insert into chat_turns (session_id,external_session_id,turn_index,trace_id,channel,question,answer) "
                "values (%s,%s,0,'old-trace','fae','old question','old answer') returning id",
                (old_session_id, old_session),
            ).fetchone()[0]
            old_relation_id = connection.execute(
                """insert into chat_turn_attachments (
                    turn_id,external_session_id,trace_id,attachment_id,source_id,direction,ordinal,
                    association_kind,display_name,kind,media_type,size_bytes,sha256,created_at,
                    processing_expires_at,handoff_deadline_at,archive_status,thumbnail_status)
                    select %s,%s,'old-trace',%s,'old-source',direction,ordinal,association_kind,
                           display_name,kind,media_type,size_bytes,sha256,created_at,
                           processing_expires_at,handoff_deadline_at,archive_status,thumbnail_status
                    from chat_turn_attachments where id=%s returning id""",
                (old_turn_id, old_session, old_attachment_id, relation_id),
            ).fetchone()[0]
        assert all(item.relation_id != str(old_relation_id)
                   for item in archive.list_pending(limit=10, cursor=None).items)
        camera_page = AttachmentArchiveRepository(pg_database).list_pending(
            limit=10, cursor=None, now=datetime.now(UTC))
        assert any(item.relation_id == str(old_relation_id) for item in camera_page.items)
        assert all(item.relation_id != str(relation_id) for item in camera_page.items)
        from src.attachments.archive_repository import AttachmentArchiveError
        with pytest.raises(AttachmentArchiveError, match='archive_relation_not_found'):
            archive.read(old_relation_id, 'original')
        assert archive.read(relation_id, 'original') == b'status=ready'
        platform_attachment_id = uuid4()
        archive.ack_archived(relation_id=relation_id, sha256=manifest.sha256,
                             platform_attachment_id=platform_attachment_id, archived_at=datetime.now(UTC))
        assert client.get(f'/authenticated/conversations/{sid}').json()['attachments'][0]['status'] == 'archived'
        archive.enabled = False  # rollback disables new handoffs, not erasure of old copies
        app.state.attachment_store.delete(aid)
        assert client.delete(f'/attachments/{aid}', headers=headers).status_code == 204
        pending_delete = next(item for item in archive.list_pending(limit=10, cursor=None).items
                              if item.relation_id == manifest.relation_id)
        assert pending_delete.as_dict()['agent_id'] == 'ai-daq-fae-agent'
        assert pending_delete.as_dict()['operation'] == 'delete'
        archive.ack_deleted(relation_id, platform_attachment_id)
        assert client.get(f'/authenticated/conversations/{sid}').json()['attachments'][0]['status'] == 'deleted'


@pytest.mark.parametrize('zero_delete_acked', [False, True])
def test_archive_ack_after_delete_preserves_late_platform_copy(pg_database, zero_delete_acked):
    from src.attachments.archive_repository import AttachmentArchiveRepository

    migrations = Path(__file__).resolve().parents[1] / 'migrations' / 'shared'
    now = datetime.now(UTC)
    aid = f'race-{uuid4()}'
    session_ref = f'daq:{uuid4()}'
    with psycopg.connect(pg_database) as connection:
        for migration in sorted(migrations.glob('0*.sql')):
            connection.execute(migration.read_text())
        session_id = connection.execute(
            "insert into chat_sessions (external_session_id,channel,metadata) "
            "values (%s,'fae','{\"agent_id\":\"ai-daq-fae-agent\"}'::jsonb) returning id",
            (session_ref,),
        ).fetchone()[0]
        turn_id = connection.execute(
            "insert into chat_turns (session_id,external_session_id,turn_index,trace_id,"
            "channel,question,answer,metadata) values "
            "(%s,%s,0,'race-trace','fae','q','a',"
            "'{\"agent_id\":\"ai-daq-fae-agent\"}'::jsonb) returning id",
            (session_id, session_ref),
        ).fetchone()[0]
        relation_id = connection.execute(
            """insert into chat_turn_attachments (
                turn_id,external_session_id,trace_id,attachment_id,source_id,direction,ordinal,
                association_kind,display_name,kind,media_type,size_bytes,sha256,created_at,
                processing_expires_at,handoff_deadline_at,archive_status,thumbnail_status)
                values (%s,'daq:race','race-trace',%s,'src','user_input',0,
                'explicit_current_turn','log.txt','text','text/plain',1,%s,%s,%s,%s,
                'pending','not_applicable') returning id""",
            (turn_id, aid, 'a' * 64, now, now + timedelta(minutes=1),
             now + timedelta(minutes=2)),
        ).fetchone()[0]

    repo = AttachmentArchiveRepository(pg_database, agent_id='ai-daq-fae-agent')
    repo.request_deletion(aid)
    if zero_delete_acked:
        repo.ack_deleted(relation_id, UUID(int=0))
    platform_id = uuid4()
    repo.ack_archived(relation_id=relation_id, sha256='a' * 64,
                      platform_attachment_id=platform_id, archived_at=now, now=now)
    delete = next(item for item in repo.list_pending(limit=10, cursor=None, now=now).items
                  if item.relation_id == str(relation_id))
    assert delete.as_dict()['operation'] == 'delete'
    assert delete.platform_attachment_id == str(platform_id)
    repo.ack_deleted(relation_id, platform_id)
    assert repo.all_relations_released(aid)
