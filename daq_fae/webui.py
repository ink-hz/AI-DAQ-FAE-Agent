"""Serve the DAQ workspace from a reviewed build in local Dev."""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

_BLOCKED = ("assets/", "api/", "enterprise/", "attachments/", "health", "review/")


def mount_local_webui(app: FastAPI, *, dist: Path) -> bool:
    return _mount_webui(app, dist=dist, prefix="/app")


def mount_authenticated_webui(app: FastAPI, *, dist: Path) -> bool:
    return _mount_webui(app, dist=dist, prefix="/daq")


def _mount_webui(app: FastAPI, *, dist: Path, prefix: str) -> bool:
    index = dist / "index.html"
    if not index.is_file():
        return False
    html = index.read_text(encoding="utf-8").replace(
        "<head>",
        f'<head><base href="{prefix}/">'
        f'<meta name="fae-browser-base" content="{prefix}">'
        '<meta name="fae-api-base" content="">',
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
