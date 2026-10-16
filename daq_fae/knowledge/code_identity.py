"""Digest the exact DAQ Python tree used by Dev replay and SDK loading."""

from __future__ import annotations

import hashlib
from pathlib import Path


def daq_code_sha(root: Path | None = None) -> str:
    root = root or Path(__file__).resolve().parents[2]
    digest = hashlib.sha256()
    for path in sorted((root / "daq_fae").rglob("*.py")):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
