from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from src.platform_identity.models import (
    PlatformIdentityError,
    PlatformSubject,
    safe_display_name,
)

CONTRACT_VERSION = "orbbec-fae-identity/v1"
# The frozen `orbbec-fae-identity/v1` message shapes. They are exact sets, not
# minimums: an unknown field means the Platform is speaking a contract this
# release does not understand, and identity is the last place to guess.
_EXCHANGE_FIELDS = frozenset(
    {
        "contract_version",
        "subject_id",
        "subject_type",
        "internal_user_id",
        "identity_binding_id",
        "agent_id",
        "display_name",
        "partner_display_name",
    }
)
_VALIDATE_FIELDS = _EXCHANGE_FIELDS | {"active"}


class PlatformIdentityClient:
    """Backchannel client pinned to a local or private Platform loopback proxy."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        parsed = urlsplit(base_url)
        hostname = parsed.hostname or ""
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None
        if (
            parsed.scheme != "http"
            or not (
                (address is not None and address.is_loopback)
                or hostname == "172.31.0.3"
            )
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
        ):
            raise ValueError("platform_identity_base_url_invalid")
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            transport=transport,
            trust_env=False,
            headers={
                "X-Real-IP": "127.0.0.1",
                "X-Forwarded-Proto": "http",
                "Accept": "application/json",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def exchange(self, code: str) -> PlatformSubject:
        return await self._post_subject(
            "/api/v1/internal/agent-launch/exchange",
            {"code": code},
            expected_fields=_EXCHANGE_FIELDS,
        )

    async def validate(self, binding_id: UUID) -> PlatformSubject:
        return await self._post_subject(
            f"/api/v1/internal/agent-bindings/{binding_id}/validate",
            {"agent_id": "ai-fae-agent"},
            expected_fields=_VALIDATE_FIELDS,
        )

    async def _post_subject(
        self,
        path: str,
        body: dict[str, str],
        *,
        expected_fields: frozenset[str],
    ) -> PlatformSubject:
        active_required = "active" in expected_fields
        try:
            response = await self._client.post(path, json=body)
        except httpx.HTTPError:
            raise PlatformIdentityError(
                "platform_identity_unavailable", status_code=503
            ) from None
        if response.status_code == 503:
            raise PlatformIdentityError(
                "platform_identity_unavailable", status_code=503
            )
        if response.status_code not in (200, 201):
            raise PlatformIdentityError("identity_binding_invalid", status_code=401)
        try:
            payload = response.json()
            # The shape is checked before anything is read out of it, so a
            # validation response can never be mistaken for an exchange
            # response and no extra identity material is silently accepted.
            if not isinstance(payload, dict) or set(payload) != set(expected_fields):
                raise ValueError
            if payload["contract_version"] != CONTRACT_VERSION:
                raise ValueError
            subject_type = payload["subject_type"]
            if subject_type not in {"enterprise_member", "partner_operator"}:
                raise ValueError
            subject_id = UUID(str(payload["subject_id"]))
            internal_user_value = payload["internal_user_id"]
            internal_user_id = (
                UUID(str(internal_user_value))
                if internal_user_value is not None
                else None
            )
            display_name = payload["display_name"]
            partner_display_name = payload["partner_display_name"]
            for name in (display_name, partner_display_name):
                # A conforming name is already bounded and single-line; anything
                # else is a Platform that stopped projecting its names.
                if name is not None and (
                    not isinstance(name, str) or safe_display_name(name) != name
                ):
                    raise ValueError
            if (
                subject_type == "enterprise_member"
                and (
                    internal_user_id is None
                    or subject_id != internal_user_id
                    or partner_display_name is not None
                )
                or subject_type == "partner_operator"
                and internal_user_id is not None
            ):
                raise ValueError
            subject = PlatformSubject(
                subject_id=subject_id,
                subject_type=subject_type,
                internal_user_id=internal_user_id,
                identity_binding_id=UUID(str(payload["identity_binding_id"])),
                agent_id=str(payload["agent_id"]),
                active=(payload["active"] is True) if active_required else True,
                display_name=display_name,
                partner_display_name=partner_display_name,
            )
        except (KeyError, TypeError, ValueError):
            raise PlatformIdentityError(
                "platform_identity_protocol_error", status_code=503
            ) from None
        if subject.agent_id != "ai-fae-agent" or not subject.active:
            raise PlatformIdentityError("identity_binding_invalid", status_code=401)
        return subject
