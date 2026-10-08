from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row

from src.platform_identity.models import (
    DEFAULT_AGENT_ID,
    AuthenticatedAccountProjection,
    AuthenticatedSessionRecord,
    IssuedAuthenticatedSession,
    PlatformIdentityError,
    PlatformSubject,
    validate_agent_id,
)

_LAUNCH_CODE = re.compile(r"[A-Za-z0-9_-]{32,256}\Z")

# The physical session table keeps its v8 name. Migration 009 is additive, so
# renaming the table would let migration 008 recreate an empty second table on
# the next full replay and fork live session state.
_SESSION_TABLE = "fae_enterprise_sessions"
_OWNER_COLUMNS = ("owner_subject_id", "owner_subject_type")

# Read path for rolling deploys: new code may run before migration 009 exists.
# Owner columns are reached through to_jsonb so the statement never statically
# references a column that is absent on the v8 schema.
_COMPAT_SESSION_COLUMNS = """
                sessions.session_id,
                sessions.session_token_hash,
                sessions.session_token_key_version,
                sessions.csrf_token_hash,
                sessions.csrf_token_key_version,
                coalesce(
                    (to_jsonb(sessions) ->> 'owner_subject_id')::uuid,
                    sessions.internal_user_id
                ) as owner_subject_id,
                coalesce(
                    to_jsonb(sessions) ->> 'owner_subject_type',
                    'enterprise_member'
                ) as owner_subject_type,
                sessions.internal_user_id,
                sessions.identity_binding_id,
                sessions.agent_id,
                sessions.created_at,
                sessions.last_seen_at,
                sessions.last_validated_at,
                sessions.idle_expires_at,
                sessions.absolute_expires_at,
                sessions.revoked_at
"""


def _storage_upgrade_required() -> PlatformIdentityError:
    return PlatformIdentityError(
        "platform_identity_storage_upgrade_required", status_code=503
    )


def _valid_subject_shape(subject: PlatformSubject, *, agent_id: str) -> bool:
    if subject.agent_id != agent_id or not subject.active:
        return False
    if subject.subject_type == "enterprise_member":
        return (
            subject.internal_user_id is not None
            and subject.subject_id == subject.internal_user_id
        )
    return (
        subject.subject_type == "partner_operator"
        and subject.internal_user_id is None
    )


def _subject_matches_record(
    subject: PlatformSubject, record: AuthenticatedSessionRecord, *, agent_id: str
) -> bool:
    """One comparison for every path that re-reads a Platform subject."""
    return (
        subject.subject_id == record.owner_subject_id
        and subject.subject_type == record.owner_subject_type
        and subject.internal_user_id == record.internal_user_id
        and subject.identity_binding_id == record.identity_binding_id
        and subject.agent_id == record.agent_id
        and _valid_subject_shape(subject, agent_id=agent_id)
    )


class PlatformIdentityClientProtocol(Protocol):
    async def exchange(self, code: str) -> PlatformSubject: ...

    async def validate(self, binding_id: UUID) -> PlatformSubject: ...


class AuthenticatedSessionRepository(Protocol):
    def create(self, record: AuthenticatedSessionRecord) -> None: ...

    def find_by_token_hash(
        self, token_hashes: Mapping[int, bytes]
    ) -> AuthenticatedSessionRecord | None: ...

    def update_activity(
        self,
        session_id: UUID,
        *,
        now: datetime,
        idle_expires_at: datetime,
        validated_at: datetime | None = None,
    ) -> None: ...

    def rotate_csrf(
        self,
        session_id: UUID,
        *,
        csrf_hash: bytes,
        key_version: int,
    ) -> None: ...

    def revoke(self, session_id: UUID, *, now: datetime) -> None: ...


class SessionTokenKeyring:
    def __init__(self, *, active_key_version: int, keys: Mapping[int, bytes]) -> None:
        if (
            isinstance(active_key_version, bool)
            or active_key_version <= 0
            or active_key_version not in keys
            or any(isinstance(version, bool) or version <= 0 for version in keys)
            or any(not isinstance(key, bytes) or len(key) != 32 for key in keys.values())
        ):
            raise ValueError("enterprise_session_keyring_invalid")
        self.active_key_version = active_key_version
        self._keys = dict(keys)

    @classmethod
    def from_file(cls, path_value: str | Path) -> SessionTokenKeyring:
        path = Path(path_value)
        if not path.is_absolute():
            raise ValueError("enterprise_session_keyring_unavailable")
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
                    or metadata.st_size > 64 * 1024
                ):
                    raise ValueError
                raw = os.read(descriptor, 64 * 1024 + 1)
            finally:
                os.close(descriptor)
            document = json.loads(raw)
            if not isinstance(document, dict) or set(document) != {"active_version", "keys"}:
                raise ValueError
            encoded = document["keys"]
            if not isinstance(encoded, dict) or not encoded:
                raise ValueError
            keys = {
                int(version): base64.b64decode(value, validate=True)
                for version, value in encoded.items()
            }
            return cls(active_key_version=document["active_version"], keys=keys)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            raise ValueError("enterprise_session_keyring_unavailable") from None

    def digest(self, purpose: str, token: str, *, key_version: int | None = None) -> tuple[int, bytes]:
        if not token or "\0" in token or purpose not in {"session", "csrf"}:
            raise ValueError("enterprise_session_token_invalid")
        version = key_version or self.active_key_version
        key = self._keys.get(version)
        if key is None:
            raise ValueError("enterprise_session_key_version_unknown")
        value = hmac.new(
            key,
            f"orbbec-fae-enterprise:{purpose}:v{version}:{token}".encode(),
            hashlib.sha256,
        ).digest()
        return version, value

    def candidate_digests(self, purpose: str, token: str) -> dict[int, bytes]:
        return {version: self.digest(purpose, token, key_version=version)[1] for version in self._keys}

    def derive_csrf_token(self, session_token: str, *, key_version: int) -> str:
        if not session_token or "\0" in session_token:
            raise ValueError("enterprise_session_token_invalid")
        key = self._keys.get(key_version)
        if key is None:
            raise ValueError("enterprise_session_key_version_unknown")
        derived = hmac.new(
            key,
            f"orbbec-fae-enterprise:csrf-token:v{key_version}:{session_token}".encode(),
            hashlib.sha256,
        ).digest()
        return base64.urlsafe_b64encode(derived).rstrip(b"=").decode("ascii")


class InMemoryAuthenticatedSessionRepository:
    def __init__(self) -> None:
        self._records: dict[UUID, AuthenticatedSessionRecord] = {}

    def create(self, record: AuthenticatedSessionRecord) -> None:
        self._records[record.session_id] = record

    def by_session_id(self, session_id: UUID) -> AuthenticatedSessionRecord | None:
        return self._records.get(session_id)

    def find_by_token_hash(
        self, token_hashes: Mapping[int, bytes]
    ) -> AuthenticatedSessionRecord | None:
        for record in self._records.values():
            expected = token_hashes.get(record.session_token_key_version)
            if expected is not None and hmac.compare_digest(record.session_token_hash, expected):
                return record
        return None

    def update_activity(
        self,
        session_id: UUID,
        *,
        now: datetime,
        idle_expires_at: datetime,
        validated_at: datetime | None = None,
    ) -> None:
        record = self._records[session_id]
        record.last_seen_at = now
        record.idle_expires_at = idle_expires_at
        if validated_at is not None:
            record.last_validated_at = validated_at

    def rotate_csrf(self, session_id: UUID, *, csrf_hash: bytes, key_version: int) -> None:
        record = self._records[session_id]
        record.csrf_token_hash = csrf_hash
        record.csrf_token_key_version = key_version

    def revoke(self, session_id: UUID, *, now: datetime) -> None:
        self._records[session_id].revoked_at = now


class PostgresAuthenticatedSessionRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def _connect(self):
        return psycopg.connect(self._database_url, row_factory=dict_row)

    @staticmethod
    def _record(row: dict) -> AuthenticatedSessionRecord:
        return AuthenticatedSessionRecord(**row)

    @staticmethod
    def _require_owner_columns(cursor) -> None:
        """Fail closed when the generic owner columns are not deployed yet."""
        cursor.execute(
            """
            select count(*) as owner_columns
            from information_schema.columns
            where table_schema = current_schema()
              and table_name = %s
              and column_name = any(%s)
            """,
            (_SESSION_TABLE, list(_OWNER_COLUMNS)),
        )
        row = cursor.fetchone()
        if row is None or row["owner_columns"] != len(_OWNER_COLUMNS):
            raise _storage_upgrade_required()

    def create(self, record: AuthenticatedSessionRecord) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            self._require_owner_columns(cursor)
            try:
                cursor.execute(
                    f"""
                    insert into {_SESSION_TABLE} (
                        session_id, session_token_hash, session_token_key_version,
                        csrf_token_hash, csrf_token_key_version, owner_subject_id,
                        owner_subject_type, internal_user_id, identity_binding_id,
                        agent_id, created_at, last_seen_at, last_validated_at,
                        idle_expires_at, absolute_expires_at, revoked_at
                    ) values (
                        %(session_id)s, %(session_token_hash)s, %(session_token_key_version)s,
                        %(csrf_token_hash)s, %(csrf_token_key_version)s, %(owner_subject_id)s,
                        %(owner_subject_type)s, %(internal_user_id)s, %(identity_binding_id)s,
                        %(agent_id)s, %(created_at)s, %(last_seen_at)s, %(last_validated_at)s,
                        %(idle_expires_at)s, %(absolute_expires_at)s, %(revoked_at)s
                    )
                    """,
                    record.__dict__,
                )
            except (psycopg.errors.UndefinedColumn, psycopg.errors.UndefinedTable):
                # Migration 009 landed between the shape check and the insert.
                raise _storage_upgrade_required() from None

    def find_by_token_hash(
        self, token_hashes: Mapping[int, bytes]
    ) -> AuthenticatedSessionRecord | None:
        clauses = []
        params: list[object] = []
        for version, digest in sorted(token_hashes.items()):
            clauses.append(
                "(sessions.session_token_key_version = %s"
                " and sessions.session_token_hash = %s)"
            )
            params.extend((version, digest))
        if not clauses:
            return None
        with self._connect() as connection, connection.cursor() as cursor:
            try:
                cursor.execute(
                    f"select {_COMPAT_SESSION_COLUMNS} from {_SESSION_TABLE} as sessions"
                    " where " + " or ".join(clauses),
                    params,
                )
            except psycopg.errors.UndefinedTable:
                raise _storage_upgrade_required() from None
            row = cursor.fetchone()
            return self._record(row) if row else None

    def update_activity(
        self,
        session_id: UUID,
        *,
        now: datetime,
        idle_expires_at: datetime,
        validated_at: datetime | None = None,
    ) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                update {_SESSION_TABLE}
                set last_seen_at = %s,
                    idle_expires_at = least(absolute_expires_at, %s),
                    last_validated_at = coalesce(%s, last_validated_at)
                where session_id = %s and revoked_at is null
                """,
                (now, idle_expires_at, validated_at, session_id),
            )

    def rotate_csrf(self, session_id: UUID, *, csrf_hash: bytes, key_version: int) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                update {_SESSION_TABLE}
                set csrf_token_hash = %s, csrf_token_key_version = %s
                where session_id = %s and revoked_at is null
                """,
                (csrf_hash, key_version, session_id),
            )

    def revoke(self, session_id: UUID, *, now: datetime) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"""
                update {_SESSION_TABLE}
                set revoked_at = coalesce(revoked_at, %s)
                where session_id = %s
                """,
                (now, session_id),
            )


class AuthenticatedSessionService:
    def __init__(
        self,
        *,
        repository: AuthenticatedSessionRepository,
        platform_client: PlatformIdentityClientProtocol,
        token_keyring: SessionTokenKeyring,
        validation_cache_seconds: int,
        idle_ttl_seconds: int,
        absolute_ttl_seconds: int,
        now: Callable[[], datetime] | None = None,
        partner_subject_ownership_ready: bool = False,
        agent_id: str = DEFAULT_AGENT_ID,
    ) -> None:
        self._agent_id = validate_agent_id(agent_id)
        if not 0 <= validation_cache_seconds <= 60:
            raise ValueError("enterprise_validation_cache_invalid")
        if idle_ttl_seconds <= 0 or absolute_ttl_seconds < idle_ttl_seconds:
            raise ValueError("enterprise_session_ttl_invalid")
        if not isinstance(partner_subject_ownership_ready, bool):
            raise ValueError("partner_subject_ownership_ready_invalid")
        self._repository = repository
        self._platform_client = platform_client
        self._tokens = token_keyring
        self._validation_cache = timedelta(seconds=validation_cache_seconds)
        self._idle_ttl = timedelta(seconds=idle_ttl_seconds)
        self._absolute_ttl = timedelta(seconds=absolute_ttl_seconds)
        self._now = now or (lambda: datetime.now(UTC))
        # Generic subject ownership for chat/session data lands in Tasks 7/8.
        # Until then FAE must not own a partner session. This constructor seam is
        # the single flip point and is deliberately not environment-configurable.
        self.partner_subject_ownership_ready = partner_subject_ownership_ready

    async def exchange_launch(self, code: str) -> IssuedAuthenticatedSession:
        if not isinstance(code, str) or _LAUNCH_CODE.fullmatch(code) is None:
            raise PlatformIdentityError("launch_code_invalid", status_code=401)
        subject = await self._platform_client.exchange(code)
        if not _valid_subject_shape(subject, agent_id=self._agent_id):
            raise PlatformIdentityError("identity_binding_invalid", status_code=401)
        self._require_subject_ownership(subject.subject_type)
        now = self._now()
        session_token = secrets.token_urlsafe(32)
        session_version, session_hash = self._tokens.digest("session", session_token)
        csrf_token = self._tokens.derive_csrf_token(
            session_token, key_version=session_version
        )
        csrf_version, csrf_hash = self._tokens.digest("csrf", csrf_token)
        record = AuthenticatedSessionRecord(
            session_id=uuid4(),
            session_token_hash=session_hash,
            session_token_key_version=session_version,
            csrf_token_hash=csrf_hash,
            csrf_token_key_version=csrf_version,
            owner_subject_id=subject.subject_id,
            owner_subject_type=subject.subject_type,
            internal_user_id=subject.internal_user_id,
            identity_binding_id=subject.identity_binding_id,
            agent_id=subject.agent_id,
            created_at=now,
            last_seen_at=now,
            last_validated_at=now,
            idle_expires_at=now + self._idle_ttl,
            absolute_expires_at=now + self._absolute_ttl,
        )
        self._repository.create(record)
        return IssuedAuthenticatedSession(
            session_id=record.session_id,
            session_token=session_token,
            csrf_token=csrf_token,
            subject=subject,
        )

    async def authenticate(self, session_token: str) -> PlatformSubject:
        record = self._resolve(session_token)
        self._require_subject_ownership(record.owner_subject_type)
        now = self._now()
        if (
            record.revoked_at is not None
            or record.idle_expires_at <= now
            or record.absolute_expires_at <= now
        ):
            if record.revoked_at is None:
                self._repository.revoke(record.session_id, now=now)
            raise PlatformIdentityError("enterprise_session_invalid", status_code=401)
        validated_at: datetime | None = None
        if now - record.last_validated_at >= self._validation_cache:
            try:
                subject = await self._platform_client.validate(record.identity_binding_id)
            except PlatformIdentityError as exc:
                if exc.status_code != 503:
                    self._repository.revoke(record.session_id, now=now)
                raise
            if not _subject_matches_record(subject, record, agent_id=self._agent_id):
                self._repository.revoke(record.session_id, now=now)
                raise PlatformIdentityError("identity_binding_invalid", status_code=401)
            validated_at = now
        self._repository.update_activity(
            record.session_id,
            now=now,
            idle_expires_at=min(record.absolute_expires_at, now + self._idle_ttl),
            validated_at=validated_at,
        )
        return record.subject()

    async def describe_account(
        self, session_token: str
    ) -> AuthenticatedAccountProjection:
        """Read the trusted display projection for an established session.

        The stored record deliberately keeps no display names, so the account
        block is projected from a fresh Platform read instead of from anything
        the browser sent or from the launch payload.
        """
        record = self._resolve(session_token)
        await self.authenticate(session_token)
        try:
            subject = await self._platform_client.validate(record.identity_binding_id)
        except PlatformIdentityError as exc:
            if exc.status_code == 503:
                return AuthenticatedAccountProjection.unnamed(
                    record.owner_subject_type
                )
            self._repository.revoke(record.session_id, now=self._now())
            raise
        if not _subject_matches_record(subject, record, agent_id=self._agent_id):
            self._repository.revoke(record.session_id, now=self._now())
            raise PlatformIdentityError("identity_binding_invalid", status_code=401)
        return AuthenticatedAccountProjection.from_subject(subject)

    async def restore_csrf(self, session_token: str) -> str:
        record = self._resolve(session_token)
        await self.authenticate(session_token)
        try:
            csrf_token = self._tokens.derive_csrf_token(
                session_token, key_version=record.csrf_token_key_version
            )
            _, digest = self._tokens.digest(
                "csrf", csrf_token, key_version=record.csrf_token_key_version
            )
        except ValueError:
            raise PlatformIdentityError(
                "enterprise_session_invalid", status_code=401
            ) from None
        if not hmac.compare_digest(record.csrf_token_hash, digest):
            self._repository.revoke(record.session_id, now=self._now())
            raise PlatformIdentityError("enterprise_session_invalid", status_code=401)
        return csrf_token

    def verify_csrf(self, session_token: str, csrf_token: str) -> bool:
        if not csrf_token:
            return False
        record = self._resolve(session_token)
        try:
            _, candidate = self._tokens.digest(
                "csrf", csrf_token, key_version=record.csrf_token_key_version
            )
        except ValueError:
            return False
        return hmac.compare_digest(record.csrf_token_hash, candidate)

    def revoke(self, session_token: str) -> None:
        record = self._resolve(session_token)
        self._repository.revoke(record.session_id, now=self._now())

    def _resolve(self, session_token: str) -> AuthenticatedSessionRecord:
        if not session_token:
            raise PlatformIdentityError("enterprise_session_invalid", status_code=401)
        record = self._repository.find_by_token_hash(
            self._tokens.candidate_digests("session", session_token)
        )
        if record is None or record.agent_id != self._agent_id:
            raise PlatformIdentityError("enterprise_session_invalid", status_code=401)
        return record

    def _require_subject_ownership(self, subject_type: str) -> None:
        """Reject partner subjects until the Tasks 7/8 wiring seam is flipped."""
        if subject_type == "partner_operator" and not self.partner_subject_ownership_ready:
            raise PlatformIdentityError(
                "partner_subject_ownership_not_ready", status_code=403
            )


# One-release compatibility aliases. Token/keyring behavior intentionally remains
# enterprise-named because changing derivation inputs would invalidate live cookies.
EnterpriseSessionRepository = AuthenticatedSessionRepository
InMemoryEnterpriseSessionRepository = InMemoryAuthenticatedSessionRepository
PostgresEnterpriseSessionRepository = PostgresAuthenticatedSessionRepository
EnterpriseSessionService = AuthenticatedSessionService
