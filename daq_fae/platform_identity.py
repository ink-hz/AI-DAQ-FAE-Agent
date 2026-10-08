"""Explicit DAQ Platform identity assembly for an authorized internal pilot.

This module does not register a Platform card or enable an application by itself.
The caller must opt in before ASGI startup and provide DAQ-scoped configuration.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.platform_identity.client import PlatformIdentityClient
from src.platform_identity.models import PlatformIdentityError
from src.platform_identity.routes import register_platform_identity_routes
from src.platform_identity.service import (
    AuthenticatedSessionService,
    PostgresAuthenticatedSessionRepository,
    SessionTokenKeyring,
)

AGENT_ID = "ai-daq-fae-agent"
SESSION_COOKIE = "__Host-daq_enterprise_session"
CSRF_HEADER = "X-DAQ-Enterprise-CSRF"
_PROTECTED_ROOTS = (
    "/chat", "/history", "/attachments", "/feedback", "/authenticated", "/review",
    "/enterprise/session",
)


@dataclass(frozen=True)
class DaqIdentityConfig:
    database_url: str
    base_url: str
    public_origin: str
    session_keyring_file: Path
    allowed_subject_ids: frozenset[UUID]

    @classmethod
    def from_environment(cls, environ: Mapping[str, str]) -> DaqIdentityConfig | None:
        enabled = environ.get("DAQ_PLATFORM_IDENTITY_ENABLED", "false")
        if enabled not in {"true", "false"}:
            raise ValueError("daq_platform_identity_enabled_invalid")
        if enabled == "false":
            return None
        keys = (
            "DAQ_DATABASE_URL", "DAQ_PLATFORM_IDENTITY_BASE_URL", "DAQ_PLATFORM_PUBLIC_ORIGIN",
            "DAQ_PLATFORM_SESSION_KEYRING_FILE", "DAQ_PLATFORM_ALLOWED_SUBJECT_IDS",
        )
        if any(not environ.get(key, "").strip() for key in keys):
            raise ValueError("daq_platform_identity_configuration_missing")
        path = Path(environ[keys[3]])
        if not path.is_absolute():
            raise ValueError("daq_platform_identity_keyring_invalid")
        try:
            allowed = frozenset(UUID(value.strip()) for value in environ[keys[4]].split(","))
        except ValueError:
            raise ValueError("daq_platform_identity_allowlist_invalid") from None
        return cls(environ[keys[0]], environ[keys[1]], environ[keys[2]], path, allowed)


class _InternalPilotClient:
    def __init__(self, client, allowed_subject_ids: frozenset[UUID]):
        self._client = client
        self._allowed = allowed_subject_ids

    def _authorize(self, subject):
        if subject.agent_id != AGENT_ID or not subject.active:
            raise PlatformIdentityError("identity_binding_invalid", status_code=401)
        if subject.subject_type != "enterprise_member" or subject.subject_id not in self._allowed:
            raise PlatformIdentityError("daq_subject_not_authorized", status_code=403)
        return subject

    async def exchange(self, code):
        return self._authorize(await self._client.exchange(code))

    async def validate(self, binding_id):
        return self._authorize(await self._client.validate(binding_id))


def configure_platform_identity(
    app: FastAPI, *, environ: Mapping[str, str] | None = None,
    repository=None, platform_client=None,
) -> AuthenticatedSessionService | None:
    """Configure trusted DAQ sessions; missing enabled configuration fails startup.

    The pilot allowlist grants entry only. Fine-grained knowledge entitlements
    must still be applied before retrieval; this is not a document access policy.
    Injected ports are for isolated contract tests, never browser-controlled.
    """
    config = DaqIdentityConfig.from_environment(os.environ if environ is None else environ)
    if config is None:
        return None
    token_keyring = SessionTokenKeyring.from_file(config.session_keyring_file)
    owns_client = platform_client is None
    client = platform_client if platform_client is not None else PlatformIdentityClient(
        config.base_url, agent_id=AGENT_ID,
    )
    service = AuthenticatedSessionService(
        repository=repository if repository is not None else PostgresAuthenticatedSessionRepository(config.database_url),
        platform_client=_InternalPilotClient(client, config.allowed_subject_ids),
        token_keyring=token_keyring, agent_id=AGENT_ID,
        validation_cache_seconds=60, idle_ttl_seconds=8 * 60 * 60,
        absolute_ttl_seconds=24 * 60 * 60,
        partner_subject_ownership_ready=False,
    )

    # Registered before the shared boundary so Starlette runs identity parsing
    # first. This guard then sees only the trusted subject projection.
    @app.middleware("http")
    async def require_internal_daq_identity(request: Request, call_next):
        path = request.url.path
        protected = any(path == root or path.startswith(root + "/") for root in _PROTECTED_ROOTS)
        exchange = path == "/enterprise/session" and request.method == "POST"
        if protected and not exchange:
            subject = getattr(request.state, "platform_identity", None)
            if subject is None:
                return JSONResponse({"error": {"code": "enterprise_session_required"}}, status_code=401)
            if (
                subject.agent_id != AGENT_ID or subject.subject_type != "enterprise_member"
                or subject.subject_id not in config.allowed_subject_ids
            ):
                return JSONResponse({"error": {"code": "daq_subject_not_authorized"}}, status_code=403)
        return await call_next(request)

    register_platform_identity_routes(
        app, service=service, public_origin=config.public_origin,
        cookie_name=SESSION_COOKIE, csrf_header=CSRF_HEADER,
    )
    app.state.authenticated_session_service = service
    app.state.enterprise_session_service = service
    app.state.daq_identity_config = config
    if owns_client:
        existing_lifespan = app.router.lifespan_context

        @asynccontextmanager
        async def identity_lifespan(application):
            try:
                async with existing_lifespan(application):
                    yield
            finally:
                await client.aclose()

        app.router.lifespan_context = identity_lifespan
    return service
