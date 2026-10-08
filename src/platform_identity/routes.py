from __future__ import annotations

import re
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from src.platform_identity.models import (
    AuthenticatedAccountProjection,
    PlatformIdentityError,
)
from src.platform_identity.service import AuthenticatedSessionService

ENTERPRISE_SESSION_COOKIE = "__Host-fae_enterprise_session"
ENTERPRISE_CSRF_HEADER = "X-FAE-Enterprise-CSRF"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_APPROVED_BROWSER_ORIGIN_HOSTS = {
    "fae.orbbec.com.cn",
    "agent.orbbec.com.cn",
}
_PRODUCTION_PARTNER_AUTH_START_URL = (
    "https://agent.orbbec.com.cn/partner-auth/start"
)


class ExchangeLaunchRequest(BaseModel):
    code: str = Field(min_length=32, max_length=256)


def register_platform_identity_routes(
    app: FastAPI,
    *,
    service: AuthenticatedSessionService,
    public_origin: str,
    additional_browser_origins: tuple[str, ...] = (),
    partner_auth_start_url: str | None = None,
    cookie_name: str = ENTERPRISE_SESSION_COOKIE,
    csrf_header: str = ENTERPRISE_CSRF_HEADER,
) -> None:
    if (
        not isinstance(cookie_name, str)
        or re.fullmatch(r"__Host-[A-Za-z0-9_-]+", cookie_name) is None
        or not isinstance(csrf_header, str)
        or re.fullmatch(r"[A-Za-z0-9!#$%&'*+.^_`|~-]+", csrf_header) is None
    ):
        raise ValueError("enterprise_browser_scope_invalid")

    def scoped_error(code: str, status_code: int, *, clear_cookie: bool = False):
        return _error(code, status_code, clear_cookie=clear_cookie, cookie_name=cookie_name)

    canonical_origin = _canonical_browser_origin(
        public_origin,
        error_code="enterprise_public_origin_invalid",
    )
    allowed_browser_origins = [canonical_origin]
    for origin in additional_browser_origins:
        additional = _canonical_browser_origin(
            origin,
            error_code="enterprise_browser_origin_invalid",
        )
        if additional in allowed_browser_origins:
            raise ValueError("enterprise_browser_origin_invalid")
        allowed_browser_origins.append(additional)
    allowed_browser_origin_set = frozenset(allowed_browser_origins)
    if partner_auth_start_url == "":
        partner_auth_start_url = None
    if (
        partner_auth_start_url is not None
        and partner_auth_start_url != _PRODUCTION_PARTNER_AUTH_START_URL
    ):
        raise ValueError("platform_partner_auth_start_url_invalid")

    @app.middleware("http")
    async def platform_identity_boundary(request: Request, call_next):
        token = request.cookies.get(cookie_name)
        request.state.platform_identity = None
        request.state.enterprise_identity = None
        is_exchange = request.url.path == "/enterprise/session" and request.method == "POST"
        if request.method not in _SAFE_METHODS and (token or is_exchange):
            if request.headers.get("origin") not in allowed_browser_origin_set:
                return scoped_error("enterprise_origin_invalid", 403)
        if token and not is_exchange:
            try:
                subject = await service.authenticate(token)
                request.state.platform_identity = subject
                request.state.enterprise_identity = subject
            except PlatformIdentityError as exc:
                return scoped_error(exc.code, exc.status_code, clear_cookie=exc.status_code == 401)
            if request.method not in _SAFE_METHODS:
                csrf = request.headers.get(csrf_header, "")
                if not service.verify_csrf(token, csrf):
                    return scoped_error("enterprise_csrf_invalid", 403)
        return await call_next(request)

    @app.get("/identity/capabilities")
    async def identity_capabilities(request: Request):
        # Only the boolean: the start URL itself is never exposed to a browser.
        return JSONResponse(
            {
                "partner_login_available": (
                    partner_auth_start_url is not None
                    and request.url.hostname != "agent.orbbec.com.cn"
                )
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/partner/login")
    async def partner_login(request: Request):
        if (
            partner_auth_start_url is None
            or request.url.hostname == "agent.orbbec.com.cn"
        ):
            return scoped_error("partner_login_unavailable", 404)
        return RedirectResponse(
            partner_auth_start_url,
            status_code=302,
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/enterprise/session", status_code=201)
    async def exchange_session(body: ExchangeLaunchRequest):
        try:
            issued = await service.exchange_launch(body.code)
        except PlatformIdentityError as exc:
            return scoped_error(exc.code, exc.status_code)
        response = JSONResponse(
            _session_body(
                AuthenticatedAccountProjection.from_subject(issued.subject),
                issued.csrf_token,
            ),
            status_code=201,
            headers={"Cache-Control": "no-store"},
        )
        response.set_cookie(
            cookie_name,
            issued.session_token,
            secure=True,
            httponly=True,
            samesite="lax",
            path="/",
            max_age=24 * 60 * 60,
        )
        return response

    @app.get("/enterprise/session")
    async def read_session(request: Request):
        token = request.cookies.get(cookie_name)
        if request.state.platform_identity is None or not token:
            return scoped_error("enterprise_session_required", 401)
        try:
            csrf_token = await service.restore_csrf(token)
            account = await service.describe_account(token)
        except PlatformIdentityError as exc:
            return scoped_error(exc.code, exc.status_code, clear_cookie=exc.status_code == 401)
        return JSONResponse(
            _session_body(account, csrf_token),
            headers={"Cache-Control": "no-store"},
        )

    @app.delete("/enterprise/session", status_code=204)
    async def delete_session(request: Request):
        token = request.cookies.get(cookie_name)
        if request.state.platform_identity is None or not token:
            return scoped_error("enterprise_session_required", 401)
        service.revoke(token)
        response = Response(status_code=204)
        response.delete_cookie(
            cookie_name,
            secure=True,
            httponly=True,
            samesite="lax",
            path="/",
        )
        response.headers["Cache-Control"] = "no-store"
        return response


def _canonical_browser_origin(raw: str, *, error_code: str) -> str:
    parsed = urlsplit(raw)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or parsed.hostname != parsed.hostname.lower()
        or parsed.netloc != parsed.hostname
        or parsed.hostname not in _APPROVED_BROWSER_ORIGIN_HOSTS
    ):
        raise ValueError(error_code)
    return f"{parsed.scheme}://{parsed.hostname}"


def _session_body(
    account: AuthenticatedAccountProjection, csrf_token: str
) -> dict[str, object]:
    return {
        "authenticated": True,
        "authentication_mode": account.authentication_mode,
        "display_name": account.display_name,
        "partner_display_name": account.partner_display_name,
        "csrf_token": csrf_token,
    }


def _error(
    code: str, status_code: int, *, clear_cookie: bool = False,
    cookie_name: str = ENTERPRISE_SESSION_COOKIE,
) -> JSONResponse:
    response = JSONResponse(
        {"error": {"code": code, "message": "platform identity request rejected"}},
        status_code=status_code,
        headers={"Cache-Control": "no-store"},
    )
    if clear_cookie:
        response.delete_cookie(
            cookie_name,
            secure=True,
            httponly=True,
            samesite="lax",
            path="/",
        )
    return response
