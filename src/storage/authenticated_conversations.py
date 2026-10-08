"""Durable ownership and restoration for authenticated FAE conversations.

Conversation turns keep living in `chat_turns`: this module adds the trusted
owner projection on `chat_sessions` plus a sealed session-state checkpoint, so an
authenticated subject can resume a conversation on another device after the
in-memory `SessionStore` is gone. Public/anonymous conversations stay ownerless
and are excluded from every read path here.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import secrets
import stat
from collections.abc import Mapping
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import UUID

import psycopg
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from psycopg.rows import dict_row

from src.agent.session import Session, validate_owner_binding
from src.attachments.archive_models import AttachmentTurnInput
from src.storage.data_flywheel import ChatTurnRecord
from src.storage.postgres_data_flywheel import PostgresDataFlywheelStore

_MAX_KEYRING_BYTES = 64 * 1024
_NONCE_BYTES = 12
_MIN_SEALED_BYTES = 28
_MAX_PAGE_LIMIT = 50
_DEFAULT_PAGE_LIMIT = 30


class ConversationStoreError(RuntimeError):
    """Authenticated conversation storage failed. Never downgraded or masked."""


class ConversationNotFound(ConversationStoreError):
    """No conversation is readable by this subject.

    A conversation owned by another subject and a conversation that never
    existed raise this same error with the same message, so a cross-subject
    probe cannot distinguish them.
    """


class ConversationOwnerConflict(ConversationStoreError):
    """A stored conversation is already owned by a different subject."""


def _canonical_json(payload: dict) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _state_digest(payload: dict) -> bytes:
    """Digest of the canonical checkpoint bytes, used to detect row tampering."""
    return hashlib.sha256(_canonical_json(payload)).digest()


@dataclass(frozen=True)
class SealedConversationState:
    ciphertext: bytes = field(repr=False)
    key_version: int
    state_sha256: bytes

    def __repr__(self) -> str:
        return (
            "SealedConversationState(ciphertext=<redacted>, "
            f"key_version={self.key_version!r})"
        )


class ConversationContentCodec:
    """AES-GCM codec for sealed conversation state.

    The key material is distinct from task-content and browser-session keys, and
    the AAD binds every ciphertext to its own session id and key version, so a
    checkpoint cannot be replayed into another conversation.
    """

    def __init__(self, *, active_key_version: int, keys: Mapping[int, bytes]) -> None:
        if (
            isinstance(active_key_version, bool)
            or not isinstance(active_key_version, int)
            or active_key_version <= 0
            or active_key_version not in keys
            or any(isinstance(version, bool) or version <= 0 for version in keys)
            or any(not isinstance(key, bytes) or len(key) != 32 for key in keys.values())
        ):
            raise ConversationStoreError("conversation_content_keyring_invalid")
        self._active_key_version = active_key_version
        self._keys = dict(keys)

    @classmethod
    def from_file(cls, path_value: str | Path) -> ConversationContentCodec:
        path = Path(path_value)
        if not path.is_absolute():
            raise ConversationStoreError("conversation_content_keyring_unavailable")
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags)
            try:
                metadata = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or stat.S_IMODE(metadata.st_mode) != 0o600
                    or metadata.st_uid != os.getuid()
                    or metadata.st_size > _MAX_KEYRING_BYTES
                ):
                    raise ValueError
                raw = b""
                while len(raw) <= _MAX_KEYRING_BYTES:
                    chunk = os.read(descriptor, _MAX_KEYRING_BYTES + 1 - len(raw))
                    if not chunk:
                        break
                    raw += chunk
            finally:
                os.close(descriptor)
            document = json.loads(raw)
            if not isinstance(document, dict) or set(document) != {
                "active_version",
                "keys",
            }:
                raise ValueError
            encoded_keys = document["keys"]
            if not isinstance(encoded_keys, dict) or not encoded_keys:
                raise ValueError
            keys = {
                int(version): base64.b64decode(value, validate=True)
                for version, value in encoded_keys.items()
            }
            return cls(active_key_version=document["active_version"], keys=keys)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            raise ConversationStoreError(
                "conversation_content_keyring_unavailable"
            ) from None

    @property
    def active_key_version(self) -> int:
        return self._active_key_version

    def __repr__(self) -> str:
        return "ConversationContentCodec(keys=<redacted>)"

    @staticmethod
    def _aad(external_session_id: str, version: int) -> bytes:
        if (
            not isinstance(external_session_id, str)
            or not external_session_id
            or "\0" in external_session_id
        ):
            raise ValueError
        return f"fae-conversation:{external_session_id}:v{version}".encode("utf-8")

    def seal_state(
        self, external_session_id: str, payload: dict
    ) -> SealedConversationState:
        try:
            if not isinstance(payload, dict):
                raise ValueError
            version = self._active_key_version
            plaintext = _canonical_json(payload)
            nonce = secrets.token_bytes(_NONCE_BYTES)
            ciphertext = nonce + AESGCM(self._keys[version]).encrypt(
                nonce, plaintext, self._aad(external_session_id, version)
            )
            return SealedConversationState(
                ciphertext=ciphertext,
                key_version=version,
                state_sha256=hashlib.sha256(plaintext).digest(),
            )
        except (KeyError, TypeError, ValueError, UnicodeError):
            raise ConversationStoreError("conversation_content_encrypt_failed") from None

    def unseal_state(
        self, external_session_id: str, sealed: SealedConversationState
    ) -> dict:
        try:
            if len(sealed.ciphertext) < _MIN_SEALED_BYTES:
                raise ValueError
            key = self._keys[sealed.key_version]
            plaintext = AESGCM(key).decrypt(
                sealed.ciphertext[:_NONCE_BYTES],
                sealed.ciphertext[_NONCE_BYTES:],
                self._aad(external_session_id, sealed.key_version),
            )
            payload = json.loads(plaintext.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError
            return payload
        except (
            AttributeError,
            InvalidTag,
            KeyError,
            TypeError,
            ValueError,
            UnicodeError,
            json.JSONDecodeError,
        ):
            raise ConversationStoreError("conversation_content_decrypt_failed") from None


@dataclass(frozen=True)
class ConversationSummary:
    """Listing projection. It carries no conversation content."""

    external_session_id: str
    channel: str
    title: str | None
    message_count: int
    created_at: datetime
    last_active_at: datetime


@dataclass(frozen=True)
class ConversationPage:
    items: tuple[ConversationSummary, ...]
    next_cursor: str | None


@dataclass(frozen=True)
class ConversationAttachment:
    """Detail projection for one attachment of an owned conversation.

    It carries the turn linkage and the descriptive fields a client needs to
    render history, and deliberately no bearer reference: the `attachment_id`
    that grants access to bytes stays in the database, and no filesystem or
    object path is projected at all. `source_id` is the non-bearer citation
    reference already used in answers.
    """

    turn_index: int
    source_id: str
    display_name: str
    kind: str
    media_type: str
    size_bytes: int
    direction: str
    ordinal: int
    association_kind: str
    status: str
    created_at: datetime


def _encode_cursor(last_active_at: datetime, row_id: UUID) -> str:
    """Opaque base64url cursor over the index key `(last_active_at, id)`.

    The internal row id is used, never the external session id, so the cursor
    discloses nothing about another subject's conversations.
    """
    raw = _canonical_json(
        {"last_active_at": last_active_at.isoformat(), "id": str(row_id)}
    )
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> tuple[str, str]:
    try:
        if not isinstance(cursor, str) or not cursor:
            raise ValueError
        padding = "=" * (-len(cursor) % 4)
        document = json.loads(base64.urlsafe_b64decode(cursor + padding))
        if not isinstance(document, dict) or set(document) != {
            "last_active_at",
            "id",
        }:
            raise ValueError
        last_active_at = document["last_active_at"]
        row_id = document["id"]
        if not isinstance(last_active_at, str) or not isinstance(row_id, str):
            raise ValueError
        datetime.fromisoformat(last_active_at)
        return last_active_at, str(UUID(row_id))
    except (
        binascii.Error,
        TypeError,
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ):
        raise ValueError("conversation_cursor_invalid") from None


class AuthenticatedConversationRepository:
    """Owner-scoped durable storage for authenticated FAE conversations."""

    def __init__(self, database_url: str, *, codec: ConversationContentCodec) -> None:
        self._database_url = database_url
        self._codec = codec

    def _connect(self):
        return psycopg.connect(self._database_url, row_factory=dict_row)

    @staticmethod
    def _owner(session: Session) -> tuple[str, str]:
        owner_subject_id = session.owner_subject_id
        owner_subject_type = session.owner_subject_type
        if owner_subject_id is None or owner_subject_type is None:
            # A public/anonymous conversation has no trusted owner and is never
            # persisted as authenticated.
            raise ValueError("conversation_owner_required")
        try:
            return str(UUID(owner_subject_id)), owner_subject_type
        except (AttributeError, TypeError, ValueError):
            raise ValueError("conversation_owner_invalid") from None

    def save_turn_and_checkpoint(
        self,
        session: Session,
        *,
        turn: ChatTurnRecord | None = None,
        attachment_relations: tuple[AttachmentTurnInput, ...] = (),
        connection=None,
    ) -> str | None:
        """Persist owner projection, turn and sealed checkpoint in one transaction.

        Either all three become visible or none of them do, so an authenticated
        conversation can never be listed without the state needed to restore it.

        Returns the id of the turn written by this call, or `None` when the call
        only refreshed the checkpoint. This path owns the turn write, so it is
        also the only place that can report the turn id a client needs in order
        to attach feedback to the answer it just received.
        """
        # The durable store is the last gate before an owner projection becomes
        # authoritative, so the trusted binding is re-validated here even though
        # `SessionStore.create` and `Session.from_checkpoint` also validate it.
        # This runs before any transaction is opened and before any state is
        # sealed, so a malformed identity writes nothing and encrypts nothing.
        validate_owner_binding(
            authentication_mode=session.authentication_mode,
            internal_user_id=session.internal_user_id,
            owner_subject_id=session.owner_subject_id,
        )
        owner_subject_id, owner_subject_type = self._owner(session)
        if turn is not None and turn.external_session_id != session.session_id:
            raise ValueError("conversation_turn_session_mismatch")
        payload = session.to_checkpoint()
        sealed = self._codec.seal_state(session.session_id, payload)
        turn_id: str | None = None
        with (nullcontext(connection) if connection is not None else self._connect()) as active_connection:
            with active_connection.cursor() as cur:
                self._write_owner_projection(
                    cur,
                    session,
                    owner_subject_id=owner_subject_id,
                    owner_subject_type=owner_subject_type,
                )
                if turn is not None:
                    # The one and only turn-write path: no second source of truth.
                    turn_id = PostgresDataFlywheelStore._record_chat_turn_locked(
                        cur, turn, attachment_relations
                    )
                self._write_checkpoint(
                    cur,
                    session,
                    owner_subject_id=owner_subject_id,
                    sealed=sealed,
                    message_count=len(session.messages),
                )
        return turn_id

    def _write_owner_projection(
        self,
        cur,
        session: Session,
        *,
        owner_subject_id: str,
        owner_subject_type: str,
    ) -> None:
        cur.execute(
            """
            insert into chat_sessions (
                external_session_id, channel, user_id, owner_subject_id,
                owner_subject_type, created_at, last_active_at, metadata
            )
            values (%s, %s, %s, %s, %s, to_timestamp(%s), to_timestamp(%s), %s::jsonb)
            on conflict (external_session_id)
            do update set
                last_active_at = greatest(
                    chat_sessions.last_active_at, excluded.last_active_at
                ),
                metadata = chat_sessions.metadata || excluded.metadata
            where chat_sessions.owner_subject_id = excluded.owner_subject_id
              and chat_sessions.owner_subject_type = excluded.owner_subject_type
              and chat_sessions.user_id is not distinct from excluded.user_id
              and coalesce(
                  chat_sessions.metadata->>'authentication_mode', 'public_customer'
              ) = coalesce(
                  excluded.metadata->>'authentication_mode', 'public_customer'
              )
            returning id
            """,
            (
                session.session_id,
                session.channel,
                session.internal_user_id,
                owner_subject_id,
                owner_subject_type,
                session.created_at,
                session.last_active,
                json.dumps(
                    {"authentication_mode": session.authentication_mode},
                    ensure_ascii=False,
                ),
            ),
        )
        if cur.fetchone() is None:
            # The row exists with a different owner, identity or mode. Ownership
            # is immutable, so this write is rejected instead of merged.
            raise ConversationOwnerConflict("conversation_owner_conflict")

    def _write_checkpoint(
        self,
        cur,
        session: Session,
        *,
        owner_subject_id: str,
        sealed: SealedConversationState,
        message_count: int,
    ) -> None:
        cur.execute(
            """
            insert into chat_session_checkpoints (
                external_session_id, owner_subject_id, state_ciphertext,
                state_key_version, state_sha256, message_count, updated_at
            )
            values (%s, %s, %s, %s, %s, %s, clock_timestamp())
            on conflict (external_session_id)
            do update set
                state_ciphertext = excluded.state_ciphertext,
                state_key_version = excluded.state_key_version,
                state_sha256 = excluded.state_sha256,
                message_count = excluded.message_count,
                updated_at = excluded.updated_at
            where chat_session_checkpoints.owner_subject_id
                  = excluded.owner_subject_id
            returning external_session_id
            """,
            (
                session.session_id,
                owner_subject_id,
                sealed.ciphertext,
                sealed.key_version,
                sealed.state_sha256,
                message_count,
            ),
        )
        if cur.fetchone() is None:
            raise ConversationOwnerConflict("conversation_owner_conflict")

    def load_for_subject(self, external_session_id: str, subject_id: UUID) -> Session:
        """Restore a conversation for its owner, or fail as not found.

        Ownership is compared in SQL, so a cross-subject read never reaches the
        decryption step and is indistinguishable from a missing conversation.
        """
        owner = self._subject_parameter(subject_id)
        with self._connect() as connection:
            row = connection.execute(
                """
                select
                    checkpoint.state_ciphertext,
                    checkpoint.state_key_version,
                    checkpoint.state_sha256,
                    checkpoint.message_count,
                    session.created_at,
                    session.last_active_at
                from chat_session_checkpoints checkpoint
                join chat_sessions session
                  on session.external_session_id = checkpoint.external_session_id
                where checkpoint.external_session_id = %s
                  and checkpoint.owner_subject_id = %s
                  and session.owner_subject_id = %s
                """,
                (external_session_id, owner, owner),
            ).fetchone()
        if row is None:
            raise ConversationNotFound("conversation_not_found")
        sealed = SealedConversationState(
            ciphertext=bytes(row["state_ciphertext"]),
            key_version=row["state_key_version"],
            state_sha256=bytes(row["state_sha256"]),
        )
        payload = self._codec.unseal_state(external_session_id, sealed)
        if (
            _state_digest(payload) != sealed.state_sha256
            or row["message_count"] != len(payload.get("messages") or [])
        ):
            raise ConversationStoreError("conversation_checkpoint_tampered")
        try:
            session = Session.from_checkpoint(
                payload,
                created_at=row["created_at"].timestamp(),
                last_active=row["last_active_at"].timestamp(),
            )
        except ValueError as exc:
            raise ConversationStoreError(str(exc)) from exc
        if session.session_id != external_session_id or session.owner_subject_id != owner:
            # The sealed envelope disagrees with the row it was stored on.
            raise ConversationStoreError("conversation_checkpoint_tampered")
        return session

    def list_for_subject(
        self,
        subject_id: UUID,
        *,
        limit: int = _DEFAULT_PAGE_LIMIT,
        cursor: str | None = None,
    ) -> ConversationPage:
        owner = self._subject_parameter(subject_id)
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError("conversation_page_limit_invalid")
        if limit < 1 or limit > _MAX_PAGE_LIMIT:
            raise ValueError("conversation_page_limit_invalid")
        cursor_last_active: str | None = None
        cursor_id: str | None = None
        if cursor is not None:
            cursor_last_active, cursor_id = _decode_cursor(cursor)
        with self._connect() as connection:
            rows = connection.execute(
                """
                select
                    session.id,
                    session.external_session_id,
                    session.channel,
                    session.created_at,
                    session.last_active_at,
                    checkpoint.message_count,
                    first_turn.question as title
                from chat_sessions session
                join chat_session_checkpoints checkpoint
                  on checkpoint.external_session_id = session.external_session_id
                left join chat_turns first_turn
                  on first_turn.external_session_id = session.external_session_id
                  and first_turn.turn_index = 0
                where session.owner_subject_id = %s
                  and checkpoint.owner_subject_id = %s
                  and (
                      %s::timestamptz is null
                      or (session.last_active_at, session.id)
                         < (%s::timestamptz, %s::uuid)
                  )
                order by session.last_active_at desc, session.id desc
                limit %s
                """,
                (owner, owner, cursor_last_active, cursor_last_active, cursor_id, limit),
            ).fetchall()
        items = tuple(
            ConversationSummary(
                external_session_id=row["external_session_id"],
                channel=row["channel"],
                title=(
                    row["title"][:120] if isinstance(row["title"], str) else None
                ),
                message_count=row["message_count"],
                created_at=row["created_at"],
                last_active_at=row["last_active_at"],
            )
            for row in rows
        )
        next_cursor = (
            _encode_cursor(rows[-1]["last_active_at"], rows[-1]["id"])
            if len(rows) == limit
            else None
        )
        return ConversationPage(items=items, next_cursor=next_cursor)

    def list_attachments_for_subject(
        self, external_session_id: str, subject_id: UUID
    ) -> tuple[ConversationAttachment, ...]:
        """Project the attachments of one owned conversation, in turn order.

        Ownership is enforced by the same join as every other authenticated read:
        `chat_turn_attachments -> chat_turns -> chat_sessions`, filtered on
        `chat_sessions.owner_subject_id`. A foreign subject therefore reads no
        rows at all, rather than reading rows and being filtered afterwards.
        """
        owner = self._subject_parameter(subject_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                select
                    turn.turn_index,
                    relation.source_id,
                    relation.display_name,
                    relation.kind,
                    relation.media_type,
                    relation.size_bytes,
                    relation.direction,
                    relation.ordinal,
                    relation.association_kind,
                    relation.archive_status,
                    relation.created_at
                from chat_turn_attachments relation
                join chat_turns turn on turn.id = relation.turn_id
                join chat_sessions session on session.id = turn.session_id
                where session.external_session_id = %s
                  and session.owner_subject_id = %s
                order by turn.turn_index, relation.direction, relation.ordinal
                """,
                (external_session_id, owner),
            ).fetchall()
        return tuple(
            ConversationAttachment(
                turn_index=row["turn_index"],
                source_id=row["source_id"],
                display_name=row["display_name"],
                kind=row["kind"],
                media_type=row["media_type"],
                size_bytes=row["size_bytes"],
                direction=row["direction"],
                ordinal=row["ordinal"],
                association_kind=row["association_kind"],
                status=row["archive_status"],
                created_at=row["created_at"],
            )
            for row in rows
        )

    @staticmethod
    def _subject_parameter(subject_id: UUID) -> str:
        try:
            return str(UUID(str(subject_id)))
        except (AttributeError, TypeError, ValueError):
            raise ValueError("conversation_subject_invalid") from None


__all__ = [
    "AuthenticatedConversationRepository",
    "ConversationAttachment",
    "ConversationContentCodec",
    "ConversationNotFound",
    "ConversationOwnerConflict",
    "ConversationPage",
    "ConversationStoreError",
    "ConversationSummary",
    "SealedConversationState",
]
