from __future__ import annotations

import base64
import json
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class TaskIdentityError(RuntimeError):
    def __init__(self, *, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.safe_message = message
        super().__init__(message)


@dataclass(frozen=True)
class VerifiedTaskIdentity:
    internal_user_id: UUID
    agent_id: str
    agent_task_id: UUID
    capability_version: int
    authorized_scopes: tuple[str, ...]
    task_deadline_at: datetime
    action_execution_deadline_at: datetime | None
    request_id: UUID
    issued_at: datetime
    expires_at: datetime
    key_id: str


def _decode_segment(value: str) -> bytes:
    if not isinstance(value, str) or not value:
        raise ValueError
    return base64.b64decode(
        value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
    )


def _utc_timestamp(value: object) -> datetime:
    if type(value) is not int:
        raise ValueError
    return datetime.fromtimestamp(value, tz=UTC)


def _utc_rfc3339(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError
    parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    if parsed.utcoffset() != timedelta(0):
        raise ValueError
    return parsed


def _read_public_key(path_value: str | Path) -> Ed25519PublicKey:
    path = Path(path_value)
    if not path.is_absolute():
        raise RuntimeError("Task public key must use an absolute path")
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RuntimeError("Task public key must be an owned regular file") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise RuntimeError("Task public key must be an owned regular file")
        if stat.S_IMODE(metadata.st_mode) not in {0o600, 0o644}:
            raise RuntimeError("Task public key must use mode 0600 or 0644")
        if metadata.st_uid != os.getuid():
            raise RuntimeError("Task public key must be owned by the service user")
        raw = b""
        while len(raw) <= 32:
            chunk = os.read(descriptor, 33 - len(raw))
            if not chunk:
                break
            raw += chunk
    finally:
        os.close(descriptor)
    if len(raw) != 32:
        raise RuntimeError("Task public key must contain 32 raw Ed25519 bytes")
    try:
        return Ed25519PublicKey.from_public_bytes(raw)
    except ValueError as exc:
        raise RuntimeError("Task public key is invalid") from exc


class TaskTokenVerifier:
    def __init__(
        self,
        keys: Mapping[str, Ed25519PublicKey],
        *,
        audience: str,
        issuer: str = "orbbec-agent-platform",
        clock_skew_seconds: int = 15,
    ) -> None:
        if (
            not keys
            or any(
                not isinstance(kid, str)
                or _IDENTIFIER.fullmatch(kid) is None
                or not isinstance(key, Ed25519PublicKey)
                for kid, key in keys.items()
            )
            or _IDENTIFIER.fullmatch(audience) is None
            or _IDENTIFIER.fullmatch(issuer) is None
            or isinstance(clock_skew_seconds, bool)
            or not 0 <= clock_skew_seconds <= 60
        ):
            raise ValueError("Task token verifier configuration invalid")
        self._keys = dict(keys)
        self._audience = audience
        self._issuer = issuer
        self._clock_skew_seconds = clock_skew_seconds

    @classmethod
    def from_files(
        cls,
        paths: Mapping[str, str | Path],
        *,
        audience: str,
        issuer: str = "orbbec-agent-platform",
        clock_skew_seconds: int = 15,
    ) -> TaskTokenVerifier:
        return cls(
            {kid: _read_public_key(path) for kid, path in paths.items()},
            audience=audience,
            issuer=issuer,
            clock_skew_seconds=clock_skew_seconds,
        )

    def verify(
        self, token: str, *, now: datetime | None = None
    ) -> VerifiedTaskIdentity:
        selected_now = datetime.now(UTC) if now is None else now
        try:
            if selected_now.tzinfo is None or selected_now.utcoffset() != timedelta(0):
                raise ValueError
            header_segment, payload_segment, signature_segment = token.split(".")
            header = json.loads(_decode_segment(header_segment))
            claims = json.loads(_decode_segment(payload_segment))
            if not isinstance(header, dict) or not isinstance(claims, dict):
                raise ValueError
            kid = header.get("kid")
            if header != {"alg": "EdDSA", "kid": kid, "typ": "JWT"}:
                raise ValueError
            if not isinstance(kid, str) or kid not in self._keys:
                raise ValueError
            self._keys[kid].verify(
                _decode_segment(signature_segment),
                f"{header_segment}.{payload_segment}".encode("ascii"),
            )
            issued_at = _utc_timestamp(claims.get("iat"))
            expires_at = _utc_timestamp(claims.get("exp"))
            if (
                claims.get("iss") != self._issuer
                or claims.get("aud") != self._audience
                or issued_at > selected_now + timedelta(seconds=self._clock_skew_seconds)
                or expires_at <= selected_now
                or expires_at <= issued_at
            ):
                raise ValueError
            internal_user_id = UUID(str(claims["internal_user_id"]))
            if claims.get("sub") != str(internal_user_id):
                raise ValueError
            agent_id = claims.get("agent_id")
            capability_version = claims.get("capability_version")
            scopes = claims.get("authorized_scopes")
            if (
                agent_id != self._audience
                or type(capability_version) is not int
                or capability_version <= 0
                or not isinstance(scopes, list)
                or not scopes
                or any(
                    not isinstance(scope, str)
                    or _IDENTIFIER.fullmatch(scope) is None
                    for scope in scopes
                )
                or tuple(sorted(set(scopes))) != tuple(scopes)
            ):
                raise ValueError
            action_deadline_raw = claims.get("action_execution_deadline_at")
            return VerifiedTaskIdentity(
                internal_user_id=internal_user_id,
                agent_id=agent_id,
                agent_task_id=UUID(str(claims["agent_task_id"])),
                capability_version=capability_version,
                authorized_scopes=tuple(scopes),
                task_deadline_at=_utc_rfc3339(claims["task_deadline_at"]),
                action_execution_deadline_at=(
                    None
                    if action_deadline_raw is None
                    else _utc_rfc3339(action_deadline_raw)
                ),
                request_id=UUID(str(claims["request_id"])),
                issued_at=issued_at,
                expires_at=expires_at,
                key_id=kid,
            )
        except (
            InvalidSignature,
            KeyError,
            TypeError,
            ValueError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ):
            raise TaskIdentityError(
                status_code=401,
                code="protocol_violation",
                message="request is not authorized",
            ) from None
