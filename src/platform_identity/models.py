from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

SubjectType = Literal["enterprise_member", "partner_operator"]
AuthenticationMode = Literal["platform_enterprise", "platform_partner"]

_AUTHENTICATION_MODES: dict[str, AuthenticationMode] = {
    "enterprise_member": "platform_enterprise",
    "partner_operator": "platform_partner",
}
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
_DISPLAY_NAME_LIMIT = 64


def authentication_mode_for(subject_type: str) -> AuthenticationMode:
    mode = _AUTHENTICATION_MODES.get(subject_type)
    if mode is None:
        raise PlatformIdentityError("identity_binding_invalid", status_code=401)
    return mode


def safe_display_name(value: object) -> str | None:
    """Normalize a Platform-managed name for rendering, or drop it entirely."""
    if not isinstance(value, str):
        return None
    cleaned = _CONTROL_CHARACTERS.sub("", value).strip()
    return cleaned[:_DISPLAY_NAME_LIMIT].strip() or None


class PlatformIdentityError(RuntimeError):
    def __init__(self, code: str, *, status_code: int = 401) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class PlatformSubject:
    subject_id: UUID
    subject_type: SubjectType
    identity_binding_id: UUID
    agent_id: str
    active: bool
    internal_user_id: UUID | None = None
    display_name: str | None = None
    partner_display_name: str | None = None


@dataclass(frozen=True)
class AuthenticatedAccountProjection:
    """The only identity shape a browser may see: no ids, no tokens, no binding."""

    authentication_mode: AuthenticationMode
    display_name: str | None = None
    partner_display_name: str | None = None

    @classmethod
    def from_subject(cls, subject: PlatformSubject) -> AuthenticatedAccountProjection:
        mode = authentication_mode_for(subject.subject_type)
        return cls(
            authentication_mode=mode,
            display_name=safe_display_name(subject.display_name),
            partner_display_name=(
                safe_display_name(subject.partner_display_name)
                if mode == "platform_partner"
                else None
            ),
        )

    @classmethod
    def unnamed(cls, subject_type: str) -> AuthenticatedAccountProjection:
        """Platform names are briefly unreadable; never invent or cache them."""
        return cls(authentication_mode=authentication_mode_for(subject_type))


@dataclass
class AuthenticatedSessionRecord:
    session_id: UUID
    session_token_hash: bytes
    session_token_key_version: int
    csrf_token_hash: bytes
    csrf_token_key_version: int
    owner_subject_id: UUID
    owner_subject_type: SubjectType
    internal_user_id: UUID | None
    identity_binding_id: UUID
    agent_id: str
    created_at: datetime
    last_seen_at: datetime
    last_validated_at: datetime
    idle_expires_at: datetime
    absolute_expires_at: datetime
    revoked_at: datetime | None = None

    def subject(self) -> PlatformSubject:
        return PlatformSubject(
            subject_id=self.owner_subject_id,
            subject_type=self.owner_subject_type,
            internal_user_id=self.internal_user_id,
            identity_binding_id=self.identity_binding_id,
            agent_id=self.agent_id,
            active=self.revoked_at is None,
        )


@dataclass(frozen=True)
class IssuedAuthenticatedSession:
    session_id: UUID
    session_token: str
    csrf_token: str
    subject: PlatformSubject


# One-release compatibility aliases. New callers use the generic names above.
EnterpriseSessionRecord = AuthenticatedSessionRecord
IssuedEnterpriseSession = IssuedAuthenticatedSession
