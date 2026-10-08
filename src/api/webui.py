"""Serve the built AI FAE WebUI when the Vite dist directory is present."""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

_BLOCKED_SPA_PREFIXES = ("api/", "assets/", "enterprise/", "attachments/", "health")


def default_webui_dist() -> Path:
    return Path(__file__).resolve().parents[2] / "webui" / "dist"


def _workspace_index(index_html: str, browser_base: str, api_base: str) -> str:
    tags = (
        f'<base href="{browser_base}/">'
        f'<meta name="fae-browser-base" content="{browser_base}">'
        f'<meta name="fae-api-base" content="{api_base}">'
    )
    return index_html.replace("<head>", f"<head>{tags}", 1)


def _safe_spa_path(path: str) -> bool:
    normalized = path.lstrip("/")
    return not any(
        normalized == prefix.rstrip("/") or normalized.startswith(prefix)
        for prefix in _BLOCKED_SPA_PREFIXES
    )


def mount_webui(app: FastAPI, dist_dir: Path | None = None) -> bool:
    """Mount the production WebUI under /app.

    Returns False when the build output is absent so API-only deployments keep
    working without requiring frontend assets.
    """
    dist = dist_dir or default_webui_dist()
    index = dist / "index.html"
    assets = dist / "assets"
    if not index.exists():
        return False
    index_html = index.read_text(encoding="utf-8")
    public_index = _workspace_index(index_html, "/app", "")
    internal_index = _workspace_index(index_html, "/fae", "/fae/api")

    if assets.exists():
        app.mount(
            "/app/assets",
            StaticFiles(directory=str(assets)),
            name="ai-fae-webui-assets",
        )
        app.mount(
            "/fae/assets",
            StaticFiles(directory=str(assets)),
            name="ai-fae-webui-internal-assets",
        )

    @app.get("/app", include_in_schema=False)
    @app.get("/app/", include_in_schema=False)
    def webui_index():
        return HTMLResponse(public_index)

    @app.get("/fae", include_in_schema=False)
    @app.get("/fae/", include_in_schema=False)
    def internal_webui_index():
        return HTMLResponse(internal_index)

    @app.get("/app/{path:path}", include_in_schema=False)
    def webui_fallback(path: str):
        if not _safe_spa_path(path):
            raise HTTPException(status_code=404)
        candidate = dist / path
        if candidate.is_file() and candidate.resolve().is_relative_to(dist.resolve()):
            return FileResponse(candidate)
        return HTMLResponse(public_index)

    @app.get("/fae/{path:path}", include_in_schema=False)
    def internal_webui_fallback(path: str):
        if not _safe_spa_path(path):
            raise HTTPException(status_code=404)
        candidate = dist / path
        if candidate.is_file() and candidate.resolve().is_relative_to(dist.resolve()):
            return FileResponse(candidate)
        return HTMLResponse(internal_index)

    return True
