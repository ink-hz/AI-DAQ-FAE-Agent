from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path


class PrivatePathError(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _absolute(path: Path) -> Path:
    expanded = path.expanduser()
    return expanded if expanded.is_absolute() else Path.cwd() / expanded


def _has_symlink_component(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if current.is_symlink():
            return True
    return False


def _require_private_directory(path: Path) -> None:
    if not path.is_dir():
        raise PrivatePathError("private_output_not_directory")
    if path.stat().st_mode & 0o077:
        raise PrivatePathError("private_directory_permissions")


def _require_private_tree(path: Path) -> None:
    for candidate in (path, *path.rglob("*")):
        if candidate.is_symlink():
            raise PrivatePathError("private_output_symlink")
        mode = candidate.stat().st_mode
        if candidate.is_dir():
            if mode & 0o077:
                raise PrivatePathError("private_directory_permissions")
        elif candidate.is_file():
            if mode & 0o077:
                raise PrivatePathError("private_file_permissions")
        else:
            raise PrivatePathError("private_output_unsupported_type")


def prepare_private_output_directory(
    path: Path,
    *,
    private_root: Path,
    git_ignore_checker: Callable[[Path], bool],
) -> Path:
    """Create a private output directory after a fail-closed path preflight."""
    lexical_root = _absolute(private_root)
    lexical_output = _absolute(path)
    if _has_symlink_component(lexical_root) or _has_symlink_component(lexical_output):
        raise PrivatePathError("private_output_symlink")
    resolved_root = lexical_root.resolve(strict=False)
    resolved_output = lexical_output.resolve(strict=False)
    if resolved_output == resolved_root or not resolved_output.is_relative_to(resolved_root):
        raise PrivatePathError("output_outside_private_root")
    if not git_ignore_checker(resolved_output):
        raise PrivatePathError("output_not_git_ignored")

    if resolved_root.exists():
        _require_private_directory(resolved_root)
    else:
        resolved_root.mkdir(parents=True, mode=0o700)
        resolved_root.chmod(0o700)

    current = resolved_root
    for part in resolved_output.relative_to(resolved_root).parts:
        current /= part
        if current.exists():
            if current.is_symlink():
                raise PrivatePathError("private_output_symlink")
            _require_private_directory(current)
        else:
            current.mkdir(mode=0o700)
            current.chmod(0o700)
    _require_private_tree(resolved_output)
    return resolved_output


def ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def atomic_write_text(path: Path, content: str) -> None:
    ensure_private_directory(path.parent)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        temporary.chmod(0o600)
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def atomic_write_json(path: Path, payload: Mapping[str, object]) -> None:
    content = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    atomic_write_text(path, content)


def write_jsonl(path: Path, rows: Iterable[Mapping[str, object]]) -> None:
    content = "".join(
        json.dumps(row, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
        for row in rows
    )
    atomic_write_text(path, content)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
