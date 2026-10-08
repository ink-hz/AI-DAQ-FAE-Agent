"""Serve the DAQ workspace from a reviewed build in local Dev."""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

_BLOCKED = ("assets/", "api/", "enterprise/", "attachments/", "health", "review/")


def mount_local_webui(app: FastAPI, *, dist: Path) -> bool:
    index = dist / "index.html"
    if not index.is_file():
        return False
    html = index.read_text(encoding="utf-8").replace(
        "<head>",
        '<head><base href="/app/">'
        '<meta name="fae-browser-base" content="/app">'
        '<meta name="fae-api-base" content="">',
        1,
    )
    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/app/assets", StaticFiles(directory=str(assets)), name="daq-assets")

    @app.get("/app", include_in_schema=False)
    @app.get("/app/", include_in_schema=False)
    def index_page():
        return HTMLResponse(html)

    @app.get("/app/{path:path}", include_in_schema=False)
    def fallback(path: str):
        clean = path.lstrip("/")
        if not clean or any(clean == value.rstrip("/") or clean.startswith(value) for value in _BLOCKED):
            raise HTTPException(404)
        candidate = dist / clean
        if candidate.is_file() and candidate.resolve().is_relative_to(dist.resolve()):
            return FileResponse(candidate)
        return HTMLResponse(html)

    return True
