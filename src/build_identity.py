"""Immutable runtime build identity loaded from the release artifact.

The release artifact is authoritative when present.  Development may opt in
with an explicit, complete environment pair; the loader never shells out to
Git or invents a partial identity.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping


_FULL_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class BuildIdentity:
    release_name: str
    git_sha: str
    source: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


def _validated(
    release_name: object,
    git_sha: object,
    source: str,
) -> BuildIdentity | None:
    name = str(release_name or "").strip()
    sha = str(git_sha or "").strip().lower()
    if not name or not _FULL_GIT_SHA.fullmatch(sha):
        return None
    return BuildIdentity(release_name=name, git_sha=sha, source=source)


def load_build_identity(
    root: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> BuildIdentity | None:
    """Load an artifact identity or an explicitly configured dev identity."""
    release_root = root or Path(__file__).resolve().parents[1]
    path = release_root / "build-info.json"
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None
        if not isinstance(payload, dict):
            return None
        return _validated(
            payload.get("release_name"),
            payload.get("git_sha"),
            "artifact",
        )

    env = os.environ if environ is None else environ
    return _validated(
        env.get("AI_FAE_RELEASE_NAME"),
        env.get("AI_FAE_BUILD_GIT_SHA"),
        "environment",
    )
