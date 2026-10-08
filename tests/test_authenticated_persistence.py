"""Owner/Agent persistence contracts with deterministic storage ports."""
import base64
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import FastAPI

from src.agent.session import SessionStore
from src.platform_identity.models import PlatformIdentityError, PlatformSubject
from src.storage.authenticated_conversations import ConversationNotFound, ConversationPage, ConversationStoreError, ConversationSummary
from src.storage.data_flywheel import ChatTurnRecord, TurnResolutionUnavailable

OWNER = UUID('b78b3205-2c86-424a-a217-775382c208bd')
OTHER = UUID('d46114bb-e01c-4f69-b67f-ef0d21f62e5e')
SUBJECT = PlatformSubject(subject_id=OWNER, subject_type='enterprise_member', internal_user_id=OWNER,
                          identity_binding_id=UUID('6dbedcf8-5263-493f-91f5-6324be037d7c'),
                          agent_id='ai-daq-fae-agent', active=True)


class Conversations:
    def __init__(self):
        self.sessions = {}
        self.writes = []
        self.list_result = None

    def load_for_subject(self, sid, owner):
        session = self.sessions.get(sid)
        if session is None or session.owner_subject_id != str(owner):
            raise ConversationNotFound('conversation_not_found')
        return session

    def save_turn_and_checkpoint(self, session, *, turn, attachment_relations=()):
        self.writes.append((session, turn, attachment_relations))
        self.sessions[session.session_id] = session
        return 'turn-1'

    def list_for_subject(self, owner, **kwargs):
        return self.list_result or ConversationPage(items=(), next_cursor=None)

    def list_attachments_for_subject(self, sid, owner):
        return ()


class Feedback:
    def __init__(self):
        self.calls = []
        self.records = []
        self.target = 'turn-1'
        self.error = None
        self.result = 'feedback-1'

    def resolve_turn_id(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return self.target

    def record_feedback(self, record):
        self.records.append(record)
        return self.result


def environment(tmp_path):
    keyring = tmp_path / 'daq-content-keys.json'
    keyring.write_text(json.dumps({'active_version': 1, 'keys': {'1': base64.b64encode(b'c' * 32).decode()}}))
    keyring.chmod(0o600)
    return {'DAQ_DATABASE_URL': 'postgresql://daq_user@localhost/daq_database',
            'DAQ_AUTHENTICATED_CONTENT_KEYRING_FILE': str(keyring),
            'DAQ_PLATFORM_REVIEWER_SUBJECT_IDS': str(OWNER)}


def assembly(tmp_path):
    from daq_fae.authenticated_persistence import configure_authenticated_persistence
    repo, feedback, review = Conversations(), Feedback(), object()
    app = FastAPI()
    adapter = configure_authenticated_persistence(
        app, environ=environment(tmp_path), runtime_release='runtime-1', knowledge_release='knowledge-1',
        conversation_repository=repo, feedback_store=feedback, review_store=review,
    )
    return adapter, repo, feedback, review, app


@pytest.mark.parametrize('missing', ['DAQ_DATABASE_URL', 'DAQ_AUTHENTICATED_CONTENT_KEYRING_FILE'])
def test_missing_persistence_config_fails_closed(tmp_path, missing):
    from daq_fae.authenticated_persistence import configure_authenticated_persistence
    env = environment(tmp_path)
    env.pop(missing)
    with pytest.raises(ValueError, match='daq_authenticated_persistence_configuration_missing'):
        configure_authenticated_persistence(FastAPI(), environ=env,
                                             runtime_release='runtime-1', knowledge_release='knowledge-1')


@pytest.mark.parametrize('database', ['postgresql://localhost/daq', 'sqlite:///daq.db',
                                     'postgresql://fae_user@localhost/fae_database',
                                     'postgresql://fae_user@localhost:5432/another_database',
                                     'postgresql://daq_user@localhost/%66ae_database',
                                     'postgresql://daq_user@localhost/another_database?user=fae_user'])
def test_database_user_and_domain_must_be_dedicated(tmp_path, database):
    from daq_fae.authenticated_persistence import configure_authenticated_persistence
    env = {**environment(tmp_path), 'DAQ_DATABASE_URL': database,
           'DATABASE_URL': 'postgresql://fae_user@localhost/fae_database'}
    with pytest.raises(ValueError, match='daq_database_identity_invalid'):
        configure_authenticated_persistence(FastAPI(), environ=env,
                                             runtime_release='runtime-1', knowledge_release='knowledge-1')


def test_content_keyring_cannot_reuse_browser_keyring(tmp_path):
    from daq_fae.authenticated_persistence import configure_authenticated_persistence
    env = environment(tmp_path)
    env['DAQ_PLATFORM_SESSION_KEYRING_FILE'] = env['DAQ_AUTHENTICATED_CONTENT_KEYRING_FILE']
    with pytest.raises(ValueError, match='daq_content_keyring_conflict'):
        configure_authenticated_persistence(FastAPI(), environ=env,
                                             runtime_release='runtime-1', knowledge_release='knowledge-1')


def test_owned_session_turn_and_checkpoint_are_written_once_with_release_metadata(tmp_path):
    adapter, repo, feedback, _, app = assembly(tmp_path)
    store = SessionStore(ttl_seconds=3600)
    session = adapter.create_session(SUBJECT, store=store)
    assert session.session_id.startswith('daq:')
    assert store.get(session.session_id) is session
    session.append_message('user', 'EGO 版本')
    session.append_message('assistant', '请提供版本')
    turn = ChatTurnRecord(external_session_id=session.session_id, channel='fae', question='EGO 版本',
                          answer='请提供版本', trace_id='trace-1', turn_index=0,
                          done={'outcome': 'safe_abstained'}, planned_capabilities=['spec'],
                          capability_coverage={'spec': 'missing'})
    assert adapter.save_turn(SUBJECT, session, turn=turn) == 'turn-1'
    assert len(repo.writes) == 1 and not feedback.records
    persisted = repo.writes[0][1]
    assert persisted.metadata['agent_id'] == 'ai-daq-fae-agent'
    assert persisted.metadata['runtime_release'] == 'runtime-1'
    assert persisted.metadata['knowledge_release'] == 'knowledge-1'
    assert persisted.metadata['owner_subject_id'] == str(OWNER)
    assert persisted.user_id == str(OWNER)
    assert app.state.authenticated_conversation_repository is repo
    assert app.state.daq_authenticated_persistence is adapter
    restored = adapter.load_session(SUBJECT, session.session_id)
    assert restored.messages == session.messages
    assert adapter.history(SUBJECT, session.session_id)['messages'][-1]['content'] == '请提供版本'


@pytest.mark.parametrize('subject', [replace(SUBJECT, agent_id='ai-fae-agent'), replace(SUBJECT, active=False),
                                   replace(SUBJECT, subject_type='partner_operator', internal_user_id=None)])
def test_foreign_and_inactive_subjects_are_rejected(tmp_path, subject):
    adapter, _, _, _, _ = assembly(tmp_path)
    with pytest.raises(PlatformIdentityError):
        adapter.create_session(subject)


def test_owner_and_foreign_agent_session_cannot_be_loaded_or_written(tmp_path):
    adapter, repo, _, _, _ = assembly(tmp_path)
    session = adapter.create_session(SUBJECT)
    repo.sessions[session.session_id] = session
    other = replace(SUBJECT, subject_id=OTHER, internal_user_id=OTHER)
    with pytest.raises(ConversationNotFound):
        adapter.load_session(other, session.session_id)
    with pytest.raises(ConversationNotFound):
        adapter.load_session(SUBJECT, 'old-fae-session')
    turn = ChatTurnRecord(external_session_id=session.session_id, channel='fae', question='q', answer='a',
                          trace_id='trace-1', turn_index=0, metadata={'agent_id': 'ai-fae-agent'})
    with pytest.raises(ConversationStoreError, match='daq_turn_identity_mismatch'):
        adapter.save_turn(SUBJECT, session, turn=turn)
    assert not repo.writes


def test_feedback_resolution_is_scoped_and_versioned(tmp_path):
    adapter, repo, feedback, _, _ = assembly(tmp_path)
    session = adapter.create_session(SUBJECT)
    repo.sessions[session.session_id] = session
    assert adapter.record_feedback(SUBJECT, session_id=session.session_id, message_index=1,
                                   rating='bad', comment='需补证', turn_id='turn-1', trace_id='trace-1') == 'feedback-1'
    assert feedback.calls[-1]['owner_subject_id'] == str(OWNER)
    assert feedback.calls[-1]['require_unowned'] is False
    assert feedback.calls[0]['candidate_turn_id'] == 'turn-1'
    assert feedback.records[-1].metadata['runtime_release'] == 'runtime-1'
    assert feedback.records[-1].turn_id == 'turn-1'


def test_feedback_missing_target_outage_and_failed_write_never_downgrade(tmp_path):
    adapter, repo, feedback, _, _ = assembly(tmp_path)
    session = adapter.create_session(SUBJECT)
    repo.sessions[session.session_id] = session
    args = {'session_id': session.session_id, 'message_index': 1, 'rating': 'bad'}
    feedback.target = None
    with pytest.raises(ConversationNotFound):
        adapter.record_feedback(SUBJECT, **args)
    assert not feedback.records
    feedback.error = TurnResolutionUnavailable('db down')
    with pytest.raises(ConversationStoreError, match='daq_feedback_storage_unavailable'):
        adapter.record_feedback(SUBJECT, **args)
    assert not feedback.records
    feedback.error, feedback.target, feedback.result = None, 'turn-1', None
    with pytest.raises(ConversationStoreError, match='daq_feedback_storage_unavailable'):
        adapter.record_feedback(SUBJECT, **args)


def test_review_requires_separate_reviewer_allowlist(tmp_path):
    adapter, _, _, review, _ = assembly(tmp_path)
    assert adapter.review_for(SUBJECT) is review
    other = replace(SUBJECT, subject_id=OTHER, internal_user_id=OTHER)
    with pytest.raises(PlatformIdentityError, match='daq_review_not_authorized'):
        adapter.review_for(other)


def test_list_fails_closed_if_store_returns_foreign_agent_namespace(tmp_path):
    adapter, repo, _, _, _ = assembly(tmp_path)
    now = datetime.now(UTC)
    repo.list_result = ConversationPage(items=(ConversationSummary(
        external_session_id='old-fae-session', channel='fae', title='private', message_count=2,
        created_at=now, last_active_at=now),), next_cursor=None)
    with pytest.raises(ConversationStoreError, match='daq_conversation_agent_mismatch'):
        adapter.list_conversations(SUBJECT)



def test_attachment_content_bearers_and_credential_urls_are_not_persisted(tmp_path):
    adapter, repo, _, _, _ = assembly(tmp_path)
    session = adapter.create_session(SUBJECT)
    turn = ChatTurnRecord(
        external_session_id=session.session_id, channel='fae', question='q', answer='a',
        trace_id='trace-1', turn_index=0,
        sources=[{'type': 'user_attachment', 'source_id': 'user_attachment:1',
                  'attachment_id': 'bearer-secret', 'content': 'raw-secret', 'path': '/tmp/raw-secret',
                  'url': 'https://storage.test/private?token=token-secret'},
                 {'type': 'knowledge', 'source_id': 'official:1', 'path': 'manual.md'}],
        done={'capability_coverage': {'spec': 'missing'},
              'vision': {'authorization': 'auth-secret', 'content': 'raw-secret'}},
    )
    adapter.save_turn(SUBJECT, session, turn=turn)
    persisted = repo.writes[0][1]
    value = repr(persisted)
    for secret in ('bearer-secret', 'raw-secret', 'token-secret', 'auth-secret'):
        assert secret not in value
    assert persisted.sources[-1]['path'] == 'manual.md'
    assert persisted.done['capability_coverage'] == {'spec': 'missing'}


def test_configured_feedback_store_never_writes_plaintext_fallback_on_outage(tmp_path, monkeypatch):
    from daq_fae.authenticated_persistence import configure_authenticated_persistence
    from src.storage.data_flywheel import FeedbackRecord
    adapter = configure_authenticated_persistence(
        FastAPI(), environ=environment(tmp_path), runtime_release='runtime-1', knowledge_release='knowledge-1',
    )

    def unavailable():
        raise RuntimeError('credentials-must-not-leak')

    monkeypatch.setattr(adapter.feedback, '_connect', unavailable)
    record = FeedbackRecord(external_session_id='daq:1', trace_id='trace', rating='bad',
                            reason_code=None, comment='private', channel='fae')
    with pytest.raises(ConversationStoreError, match='daq_feedback_storage_unavailable'):
        adapter.feedback.record_feedback(record)
    assert sorted(path.name for path in tmp_path.iterdir()) == ['daq-content-keys.json']


def test_distinct_keyring_files_must_not_reuse_browser_key_material(tmp_path):
    from daq_fae.authenticated_persistence import configure_authenticated_persistence
    env = environment(tmp_path)
    session_path = tmp_path / 'daq-session-keys.json'
    session_path.write_text(Path(env['DAQ_AUTHENTICATED_CONTENT_KEYRING_FILE']).read_text())
    session_path.chmod(0o600)
    env['DAQ_PLATFORM_SESSION_KEYRING_FILE'] = str(session_path)
    with pytest.raises(ValueError, match='daq_content_keyring_conflict'):
        configure_authenticated_persistence(FastAPI(), environ=env,
                                             runtime_release='runtime-1', knowledge_release='knowledge-1')


def test_detail_restores_messages_and_owner_checked_non_bearer_attachment_projection(tmp_path):
    adapter, repo, _, _, _ = assembly(tmp_path)
    session = adapter.create_session(SUBJECT)
    session.append_message('user', '日志')
    repo.sessions[session.session_id] = session
    result = adapter.conversation_detail(SUBJECT, session.session_id)
    assert result['messages'] == [{'role': 'user', 'content': '日志'}]
    assert result['attachments'] == []
    with pytest.raises(ConversationNotFound):
        adapter.conversation_detail(replace(SUBJECT, subject_id=OTHER, internal_user_id=OTHER), session.session_id)


def test_attachment_metadata_alone_triggers_content_redaction(tmp_path):
    adapter, repo, _, _, _ = assembly(tmp_path)
    session = adapter.create_session(SUBJECT)
    turn = ChatTurnRecord(external_session_id=session.session_id, channel='fae', question='q', answer='a',
                          trace_id='trace-1', turn_index=0, metadata={'contains_attachment': True},
                          done={'vision': {'content': 'raw-secret'}})
    adapter.save_turn(SUBJECT, session, turn=turn)
    assert 'raw-secret' not in repr(repo.writes[0][1])
