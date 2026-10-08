"""DAQ-owned authenticated conversation/feedback persistence ports.

No route, model, knowledge or archive publication is enabled by this module.
"""
from __future__ import annotations

import os
import time
from collections.abc import Mapping
from dataclasses import asdict, replace
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit
from uuid import UUID, uuid4

from src.agent.session import Session
from src.api.routes import (
    _redact_attachment_done,
    _redact_attachment_sources_for_persistence,
    _strip_attachment_bearers,
)
from src.platform_identity.models import PlatformIdentityError
from src.platform_identity.service import SessionTokenKeyring
from src.platform_tasks.crypto import TaskContentCodec
from src.storage.authenticated_conversations import (
    AuthenticatedConversationRepository,
    ConversationContentCodec,
    ConversationNotFound,
    ConversationStoreError,
)
from src.storage.data_flywheel import FeedbackRecord, TurnResolutionUnavailable
from src.storage.postgres_data_flywheel import PostgresDataFlywheelStore
from src.storage.review_center import PostgresReviewCenterStore

from daq_fae.platform_identity import AGENT_ID

_SESSION_PREFIX = "daq:"


_SECRET_FIELDS = {"authorization", "api_key", "auth_token", "access_token", "password", "secret"}


def _redact_credentials(value):
    if isinstance(value, dict):
        return {key: _redact_credentials(item) for key, item in value.items()
                if key.lower() not in _SECRET_FIELDS}
    if isinstance(value, list):
        return [_redact_credentials(item) for item in value]
    if isinstance(value, str) and value.startswith(("https://", "http://")):
        try:
            url = urlsplit(value)
            parameters = {key.lower() for key, _ in parse_qsl(url.query)}
            sensitive = {"token", "key", "api_key", "access_token", "auth_token", "signature", "credential", "password"}
            if (url.username or url.password or parameters & sensitive
                    or any(key.startswith(("x-amz-", "x-goog-")) for key in parameters)
                    or "platform_launch=" in url.fragment or "partner_launch=" in url.fragment):
                return "<credential URL redacted>"
        except ValueError:
            return "<invalid URL redacted>"
    return value


def _assert_subject(subject):
    if (
        subject is None or subject.agent_id != AGENT_ID or not subject.active
        or subject.subject_type != "enterprise_member"
        or not isinstance(subject.subject_id, UUID)
        or subject.internal_user_id != subject.subject_id
    ):
        raise PlatformIdentityError("identity_binding_invalid", status_code=401)


def _assert_session_id(session_id):
    if not isinstance(session_id, str) or not session_id.startswith(_SESSION_PREFIX):
        raise ConversationNotFound("conversation_not_found")
    try:
        UUID(session_id[len(_SESSION_PREFIX):])
    except ValueError:
        raise ConversationNotFound("conversation_not_found") from None


def _assert_owner(subject, session):
    _assert_session_id(session.session_id)
    if (
        session.owner_subject_id != str(subject.subject_id)
        or session.authentication_mode != "platform_enterprise"
        or session.internal_user_id != str(subject.internal_user_id)
    ):
        raise ConversationNotFound("conversation_not_found")


class _FailClosedFeedbackWriter:
    def write(self, *args, **kwargs):
        # Shared PG feedback catches outages and calls this port. Authenticated
        # DAQ must not create a plaintext fallback journal or report success.
        raise ConversationStoreError("daq_feedback_storage_unavailable")


class DaqAuthenticatedPersistence:
    def __init__(self, *, conversation_repository, feedback_store, review_store,
                 runtime_release, knowledge_release, reviewer_subject_ids):
        self.conversations = conversation_repository
        self.feedback = feedback_store
        self._review = review_store
        self._reviewers = reviewer_subject_ids
        self._versions = {"agent_id": AGENT_ID, "runtime_release": runtime_release,
                          "knowledge_release": knowledge_release}

    def create_session(self, subject, *, store=None):
        _assert_subject(subject)
        now = time.time()
        session = Session(
            session_id=f"{_SESSION_PREFIX}{uuid4()}", channel="fae", created_at=now, last_active=now,
            authentication_mode="platform_enterprise", internal_user_id=str(subject.internal_user_id),
            owner_subject_id=str(subject.subject_id),
        )
        return store.adopt(session) if store is not None else session

    def load_session(self, subject, session_id, *, store=None):
        _assert_subject(subject)
        _assert_session_id(session_id)
        try:
            session = self.conversations.load_for_subject(session_id, subject.subject_id)
        except ConversationNotFound:
            raise
        except Exception:
            raise ConversationStoreError("daq_conversation_storage_unavailable") from None
        _assert_owner(subject, session)
        if store is not None:
            session = store.adopt(session)
            _assert_owner(subject, session)
        return session

    def history(self, subject, session_id):
        session = self.load_session(subject, session_id)
        return {"session_id": session.session_id, "channel": session.channel,
                "messages": list(session.messages),
                "current_schema": session.current_schema.model_dump() if session.current_schema else None}

    def conversation_detail(self, subject, session_id):
        result = self.history(subject, session_id)
        try:
            attachments = self.conversations.list_attachments_for_subject(session_id, subject.subject_id)
        except ConversationNotFound:
            raise
        except Exception:
            raise ConversationStoreError("daq_conversation_storage_unavailable") from None
        result["attachments"] = [{**asdict(item), "created_at": item.created_at.isoformat()}
                                 for item in attachments]
        return result

    def list_conversations(self, subject, *, cursor=None, limit=30):
        _assert_subject(subject)
        page = self.conversations.list_for_subject(subject.subject_id, cursor=cursor, limit=limit)
        for item in page.items:
            try:
                _assert_session_id(item.external_session_id)
            except ConversationNotFound:
                raise ConversationStoreError("daq_conversation_agent_mismatch") from None
        return {"items": [{"session_id": item.external_session_id, "title": item.title,
                           "channel": item.channel, "created_at": item.created_at.isoformat(),
                           "last_active_at": item.last_active_at.isoformat()} for item in page.items],
                "next_cursor": page.next_cursor}

    def save_turn(self, subject, session, *, turn, attachment_relations=()):
        _assert_subject(subject)
        _assert_owner(subject, session)
        trusted = {**self._versions, "authentication_mode": "platform_enterprise",
                   "owner_subject_id": str(subject.subject_id), "owner_subject_type": "enterprise_member"}
        if (
            turn.external_session_id != session.session_id or turn.channel != session.channel
            or turn.user_id not in {None, str(subject.internal_user_id)}
            or any(key in turn.metadata and turn.metadata[key] != value for key, value in trusted.items())
            or any(key in turn.done and turn.done[key] != value for key, value in self._versions.items())
        ):
            raise ConversationStoreError("daq_turn_identity_mismatch")
        contains_attachment = (bool(session.visible_attachments())
                               or bool(turn.metadata.get("contains_attachment"))
                               or bool(turn.done.get("active_attachment_count"))) or any(
            source.get("type") in {"attachment", "user_attachment"} for source in turn.sources
        )
        sources = [_redact_attachment_done(source)
                   if source.get("type") in {"attachment", "user_attachment"}
                   else _strip_attachment_bearers(source) for source in turn.sources]
        redact = _redact_attachment_done if contains_attachment else _strip_attachment_bearers
        record = replace(
            turn, user_id=str(subject.internal_user_id),
            metadata={**_redact_credentials(redact(turn.metadata)), **trusted},
            sources=_redact_credentials(_redact_attachment_sources_for_persistence(sources)),
            stages=_redact_credentials(redact(turn.stages)),
            done={**_redact_credentials(redact(turn.done)), **self._versions},
        )
        try:
            turn_id = self.conversations.save_turn_and_checkpoint(
                session, turn=record, attachment_relations=attachment_relations,
            )
        except Exception:
            raise ConversationStoreError("daq_conversation_storage_unavailable") from None
        if not turn_id:
            raise ConversationStoreError("daq_conversation_storage_unavailable")
        return turn_id

    def record_feedback(self, subject, *, session_id, message_index, rating, comment="",
                        turn_id=None, trace_id=None, reason_code=None):
        if (rating not in {"good", "bad"} or isinstance(message_index, bool)
                or not isinstance(message_index, int) or message_index < 0):
            raise ValueError("daq_feedback_invalid")
        self.load_session(subject, session_id)
        scope = {"external_session_id": session_id, "message_index": message_index,
                 "owner_subject_id": str(subject.subject_id),
                 "internal_user_id": str(subject.internal_user_id), "require_unowned": False}
        try:
            target = self.feedback.resolve_turn_id(**scope, candidate_turn_id=turn_id, trace_id=trace_id)
            if target is not None and turn_id and trace_id:
                traced = self.feedback.resolve_turn_id(**scope, trace_id=trace_id)
                if traced != target:
                    target = None
        except TurnResolutionUnavailable:
            raise ConversationStoreError("daq_feedback_storage_unavailable") from None
        if target is None:
            raise ConversationNotFound("feedback_target_not_found")
        record = FeedbackRecord(
            external_session_id=session_id, trace_id=trace_id or "", rating=rating,
            reason_code=reason_code, comment=comment, channel="fae", turn_id=target,
            message_index=message_index, user_id=str(subject.internal_user_id),
            metadata={**self._versions, "authentication_mode": "platform_enterprise",
                      "owner_subject_id": str(subject.subject_id), "owner_subject_type": "enterprise_member"},
        )
        try:
            feedback_id = self.feedback.record_feedback(record)
        except Exception:
            raise ConversationStoreError("daq_feedback_storage_unavailable") from None
        if not feedback_id:
            raise ConversationStoreError("daq_feedback_storage_unavailable")
        return feedback_id

    def review_for(self, subject):
        _assert_subject(subject)
        if subject.subject_id not in self._reviewers:
            raise PlatformIdentityError("daq_review_not_authorized", status_code=403)
        return self._review


def _validate_database(database_url, old_database_url):
    parsed = urlsplit(database_url)
    if (parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname
            or not parsed.username or parsed.path in {"", "/"}
            or {key.lower() for key, _ in parse_qsl(parsed.query)} & {"user", "dbname", "database", "host", "hostaddr", "port"}):
        raise ValueError("daq_database_identity_invalid")
    if old_database_url:
        old = urlsplit(old_database_url)
        if ((parsed.hostname, parsed.port or 5432) == (old.hostname, old.port or 5432)
                and (unquote(parsed.username) == unquote(old.username or "")
                     or unquote(parsed.path) == unquote(old.path))):
            raise ValueError("daq_database_identity_invalid")


def configure_authenticated_persistence(
    app, *, runtime_release: str, knowledge_release: str, environ: Mapping[str, str] | None = None,
    conversation_repository=None, feedback_store=None, review_store=None,
):
    """Explicit assembly; never downgrades an authenticated turn to local storage."""
    env = os.environ if environ is None else environ
    database_url = env.get("DAQ_DATABASE_URL", "")
    keyring_file = env.get("DAQ_AUTHENTICATED_CONTENT_KEYRING_FILE", "")
    if not database_url or not keyring_file:
        raise ValueError("daq_authenticated_persistence_configuration_missing")
    if not all(isinstance(value, str) and value.strip() for value in (runtime_release, knowledge_release)):
        raise ValueError("daq_persistence_release_missing")
    _validate_database(database_url, env.get("DATABASE_URL"))
    content_path = Path(keyring_file)
    for key in ("DAQ_PLATFORM_SESSION_KEYRING_FILE", "DAQ_PLATFORM_TASK_CONTENT_KEYRING_FILE"):
        if env.get(key) and content_path.resolve() == Path(env[key]).resolve():
            raise ValueError("daq_content_keyring_conflict")
    codec = ConversationContentCodec.from_file(content_path)
    for key, keyring_type in (
        ("DAQ_PLATFORM_SESSION_KEYRING_FILE", SessionTokenKeyring),
        ("DAQ_PLATFORM_TASK_CONTENT_KEYRING_FILE", TaskContentCodec),
    ):
        if env.get(key):
            other_keyring = keyring_type.from_file(Path(env[key]))
            # Fixed upstream keyring classes expose their validated key map.
            # Compare actual material as well as paths; copied files are not
            # independent keys merely because their filenames differ.
            if set(codec._keys.values()) & set(other_keyring._keys.values()):
                raise ValueError("daq_content_keyring_conflict")
    try:
        reviewers = frozenset(UUID(item.strip()) for item in env.get("DAQ_PLATFORM_REVIEWER_SUBJECT_IDS", "").split(",") if item.strip())
    except ValueError:
        raise ValueError("daq_reviewer_configuration_invalid") from None
    repository = conversation_repository if conversation_repository is not None else AuthenticatedConversationRepository(database_url, codec=codec)
    feedback = feedback_store if feedback_store is not None else PostgresDataFlywheelStore(database_url, _FailClosedFeedbackWriter())
    review = review_store if review_store is not None else PostgresReviewCenterStore(database_url)
    adapter = DaqAuthenticatedPersistence(
        conversation_repository=repository, feedback_store=feedback, review_store=review,
        runtime_release=runtime_release, knowledge_release=knowledge_release, reviewer_subject_ids=reviewers,
    )
    app.state.daq_authenticated_persistence = adapter
    app.state.authenticated_conversation_repository = repository
    return adapter
