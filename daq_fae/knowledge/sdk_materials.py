"""Offline SDK archive inventory; package presence never proves compatibility."""

from __future__ import annotations

import re

_SHA = re.compile(r"[0-9a-f]{64}\Z")
_DATE = re.compile(r"(?<!\d)20\d{6}(?!\d)")
_PLATFORMS = {"android", "arm64", "ios", "linux", "macos", "windows"}


def _kind(path: str) -> str:
    lower = path.casefold()
    if path.endswith(".DS_Store"):
        return "metadata"
    if lower.endswith(".url"):
        return "pointer"
    if lower.endswith(("changelog.txt", "changelist.txt")):
        return "change_log"
    if path.startswith("SDK/"):
        return "sdk_package"
    if path.startswith("Tool/"):
        return "viewer_package"
    if path.startswith("firmware/"):
        return "firmware_or_sdk_package"
    return "other"


def build_inventory(files: list[dict], *, source_checked_packages: set[str]) -> list[dict]:
    """Derive exact-byte intake rows from a previously verified archive manifest."""
    first_hash: dict[str, str] = {}
    seen_paths: set[str] = set()
    result = []
    for item in files:
        path, digest, size = item.get("path"), item.get("sha256"), item.get("size")
        if (not isinstance(path, str) or not path or path.startswith("/")
                or ".." in path.split("/") or path in seen_paths
                or not isinstance(digest, str) or not _SHA.fullmatch(digest)
                or type(size) is not int or size < 0):
            raise ValueError("SDK material manifest row invalid")
        seen_paths.add(path)
        kind = _kind(path)
        if path in source_checked_packages and kind != "sdk_package":
            raise ValueError("SDK source-checked package is not an SDK package")
        scope = ("api_member_only" if path in source_checked_packages else
                 "release_note_candidate" if kind == "change_log" else
                 "unverified_pointer" if kind == "pointer" else
                 "non_source" if kind == "metadata" else "asset_identity_only")
        parts = path.split("/")
        platform = next((part.casefold() for part in parts if part.casefold() in _PLATFORMS), None)
        date = _DATE.search(path)
        duplicate = first_hash.setdefault(digest, path)
        result.append({
            "path": path, "sha256": digest, "size": size, "kind": kind,
            "platform_hint": platform, "drop_hint": date.group() if date else None,
            "duplicate_of": duplicate if duplicate != path else None,
            "use_scope": scope, "device_support": "unknown",
            "download_authority": "unverified",
        })
    if source_checked_packages - seen_paths:
        raise ValueError("SDK source-checked package absent from archive manifest")
    return result


def search_inventory(inventory: list[dict], text: str) -> list[dict]:
    """Local reviewer lookup of artifact identity only; no answer coverage result."""
    if not isinstance(text, str) or not text.strip() or len(text) > 200:
        return []
    needle = text.casefold()
    return [row for row in inventory if needle in row["path"].casefold()]
