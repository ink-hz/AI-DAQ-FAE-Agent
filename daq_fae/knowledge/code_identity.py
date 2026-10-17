"""Digest the exact DAQ code and runtime prompts used by Dev replay."""

from __future__ import annotations

import hashlib
from pathlib import Path


def daq_code_sha(root: Path | None = None) -> str:
    root = root or Path(__file__).resolve().parents[2]
    digest = hashlib.sha256()
    paths = list((root / "daq_fae").rglob("*.py"))
    paths.extend(root / "prompts" / name for name in (
        "reviewed_knowledge_system.md", "candidate_knowledge_system.md",
        "daq_schema_extraction.md"))
    for path in sorted(paths):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
