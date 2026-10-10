"""Deterministic candidate extraction from a hash-verified, off-repo archive."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat

import pymupdf as fitz

from scripts.archive_candidate_snapshot import verify_archive


_TEXT_KINDS = {".md": "markdown", ".txt": "text", ".pdf": "pdf"}
_HEADING = re.compile(r"^#{1,6}\s+")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_verified(path: Path, entry: dict, *, capture: bool) -> bytes | None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    digest = hashlib.sha256()
    size = 0
    parts = [] if capture else None
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError(f"source changed during import: {entry['path']}")
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(block)
            digest.update(block)
            if parts is not None:
                parts.append(block)
    if size != entry["size"] or digest.hexdigest() != entry["sha256"]:
        raise ValueError(f"source changed during import: {entry['path']}")
    return b"".join(parts) if parts is not None else None


def _decode(data: bytes) -> tuple[str, str]:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ValueError("text file is neither UTF-8 nor GB18030")


def _line_sections(content: str, *, markdown: bool) -> list[tuple[dict, str]]:
    lines = content.splitlines()
    if not lines:
        return []
    starts = [i for i, line in enumerate(lines) if markdown and _HEADING.match(line)]
    if not starts or starts[0] != 0:
        starts.insert(0, 0)
    sections = []
    for index, start in enumerate(starts):
        stop = starts[index + 1] if index + 1 < len(starts) else len(lines)
        while stop > start and not lines[stop - 1].strip():
            stop -= 1
        text = "\n".join(lines[start:stop]).strip()
        if text:
            sections.append(({"kind": "lines", "start": start + 1, "end": stop}, text))
    return sections


def _chunk(path: str, source_sha256: str, locator: dict, text: str) -> dict:
    basis = json.dumps([path, source_sha256, locator, _sha(text.encode("utf-8"))],
                       ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "chunk_id": _sha(basis.encode("utf-8")),
        "source_path": path,
        "source_sha256": source_sha256,
        "locator": locator,
        "text_sha256": _sha(text.encode("utf-8")),
        "text": text,
    }


def import_archive(archive: Path, manifest_sha256: str) -> dict:
    """Import verified raw files as *candidates*, never as approved facts."""
    verified = verify_archive(archive, manifest_sha256)
    manifest = json.loads((archive / "manifest.json").read_text(encoding="utf-8"))
    sources: list[dict] = []
    chunks: list[dict] = []
    for entry in sorted(manifest["files"], key=lambda row: row["path"]):
        path = entry["path"]
        kind = _TEXT_KINDS.get(Path(path).suffix.casefold(), "asset")
        source = {
            "path": path, "sha256": entry["sha256"], "size": entry["size"],
            "kind": kind, "modified_at_utc": entry["modified_at_utc"],
            "extraction_status": "metadata_only" if kind == "asset" else "extracted",
        }
        payload = _read_verified(archive / "files" / path, entry, capture=kind != "asset")
        if kind in {"markdown", "text"}:
            try:
                content, encoding = _decode(payload)
                source["encoding"] = encoding
                for locator, text in _line_sections(content, markdown=kind == "markdown"):
                    chunks.append(_chunk(path, entry["sha256"], locator, text))
                if not any(c["source_path"] == path for c in chunks):
                    source["extraction_status"] = "empty_text"
            except ValueError:
                source["extraction_status"] = "decode_failed"
        elif kind == "pdf":
            try:
                with fitz.open(stream=payload, filetype="pdf") as pdf:
                    missing_text_pages = []
                    for page_number, page in enumerate(pdf, start=1):
                        text = page.get_text(sort=True).strip()
                        if text:
                            chunks.append(_chunk(path, entry["sha256"],
                                                 {"kind": "page", "page": page_number}, text))
                        else:
                            missing_text_pages.append(page_number)
                    if missing_text_pages:
                        source["needs_ocr_pages"] = missing_text_pages
                    if len(missing_text_pages) == len(pdf):
                        source["extraction_status"] = "needs_ocr"
                    elif missing_text_pages:
                        source["extraction_status"] = "partial_text"
            except (RuntimeError, ValueError):
                source["extraction_status"] = "pdf_unreadable"
        sources.append(source)
    return {
        "format_version": 1,
        "extractor_version": "1",
        "archive_manifest_sha256": verified["manifest_sha256"],
        "source_date": manifest["source_date"],
        "sources": sources,
        "chunks": chunks,
    }


def compare_snapshots(previous: dict, current: dict) -> dict[str, list[str]]:
    before = {row["path"]: row["sha256"] for row in previous["sources"]}
    after = {row["path"]: row["sha256"] for row in current["sources"]}
    return {
        "added": sorted(after.keys() - before.keys()),
        "changed": sorted(path for path in before.keys() & after.keys()
                          if before[path] != after[path]),
        "removed": sorted(before.keys() - after.keys()),
        "unchanged": sorted(path for path in before.keys() & after.keys()
                            if before[path] == after[path]),
    }
