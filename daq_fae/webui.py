"""Serve the DAQ workspace from a reviewed build in local Dev."""

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from daq_fae.platform_identity import SESSION_COOKIE

_BLOCKED = ("assets/", "api/", "enterprise/", "attachments/", "health", "review/")


def mount_local_webui(app: FastAPI, *, dist: Path) -> bool:
    return _mount_webui(app, dist=dist, prefix="/app", api_base="")


def mount_authenticated_webui(app: FastAPI, *, dist: Path) -> bool:
    mounted = _mount_webui(app, dist=dist, prefix="/daq", api_base="/daq/api")
    if mounted:
        @app.middleware("http")
        async def recover_stale_browser_session(request: Request, call_next):
            response = await call_next(request)
            browser_page = (request.url.path in {"/daq", "/daq/"}
                            or request.url.path.startswith("/daq/conversations/"))
            if (request.method == "GET" and browser_page
                    and request.cookies.get(SESSION_COOKIE)
                    and response.status_code == 401):
                redirect = RedirectResponse(
                    request.url.path, status_code=303,
                    headers={"Cache-Control": "no-store"},
                )
                redirect.delete_cookie(
                    SESSION_COOKIE, secure=True, httponly=True,
                    samesite="lax", path="/",
                )
                return redirect
            return response
    return mounted


def _mount_webui(app: FastAPI, *, dist: Path, prefix: str, api_base: str) -> bool:
    index = dist / "index.html"
    if not index.is_file():
        return False
    html = index.read_text(encoding="utf-8").replace(
        "<head>",
        f'<head><base href="{prefix}/">'
        f'<meta name="fae-browser-base" content="{prefix}">'
        f'<meta name="fae-api-base" content="{api_base}">',
        1,
    )
    assets = dist / "assets"
    if assets.is_dir():
        app.mount(f"{prefix}/assets", StaticFiles(directory=str(assets)), name="daq-assets")

    @app.get(prefix, include_in_schema=False)
    @app.get(prefix + "/", include_in_schema=False)
    def index_page():
        return HTMLResponse(html)

    @app.get(prefix + "/{path:path}", include_in_schema=False)
    def fallback(path: str):
        clean = path.lstrip("/")
        if not clean or any(clean == value.rstrip("/") or clean.startswith(value) for value in _BLOCKED):
            raise HTTPException(404)
        candidate = dist / clean
        if candidate.is_file() and candidate.resolve().is_relative_to(dist.resolve()):
            return FileResponse(candidate)
        return HTMLResponse(html)

    return True
