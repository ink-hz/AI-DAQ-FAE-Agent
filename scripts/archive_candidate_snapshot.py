"""Freeze an unreviewed DAQ source snapshot outside Git; never publish it as knowledge."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import sys
import tempfile


CHUNK_SIZE = 1024 * 1024


def _digest(path: Path) -> tuple[int, str]:
    size = 0
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(CHUNK_SIZE):
            size += len(block)
            digest.update(block)
    return size, digest.hexdigest()


def _entries(root: Path, *, enforce_modes: bool = False) -> list[tuple[str, Path]]:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("tree must be a real directory")
    files = []

    def walk(directory: Path) -> None:
        if enforce_modes and stat.S_IMODE(directory.stat(follow_symlinks=False).st_mode) != 0o500:
            raise ValueError("directory mode mismatch")
        with os.scandir(directory) as scanner:
            for entry in scanner:
                path = Path(entry.path)
                if entry.is_symlink():
                    raise ValueError("tree contains a symlink")
                if entry.is_dir(follow_symlinks=False):
                    walk(path)
                elif entry.is_file(follow_symlinks=False):
                    if enforce_modes and stat.S_IMODE(entry.stat(follow_symlinks=False).st_mode) != 0o400:
                        raise ValueError("file mode mismatch")
                    files.append((path.relative_to(root).as_posix(), path))
                else:
                    raise ValueError("tree contains a non-regular entry")

    walk(root)
    return sorted(files)


def _safe_relative(name: str) -> Path:
    posix = PurePosixPath(name)
    if not name or posix.is_absolute() or any(part in {"", ".", ".."} for part in name.split("/")):
        raise ValueError("manifest has an unsafe relative path")
    return Path(*posix.parts)


def verify_archive(destination: Path, expected_manifest_sha256: str | None = None) -> dict:
    if destination.is_symlink() or not destination.is_dir():
        raise ValueError("archive must be a real directory")
    if stat.S_IMODE(destination.stat(follow_symlinks=False).st_mode) != 0o500:
        raise ValueError("archive mode mismatch")
    manifest_path = destination / "manifest.json"
    files_root = destination / "files"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError("archive manifest missing or linked")
    if files_root.is_symlink() or not files_root.is_dir():
        raise ValueError("archive files directory missing or linked")
    if stat.S_IMODE(manifest_path.stat(follow_symlinks=False).st_mode) != 0o400:
        raise ValueError("manifest mode mismatch")
    if {path.name for path in destination.iterdir()} != {"manifest.json", "files"}:
        raise ValueError("unexpected archive root entry")
    manifest_bytes = manifest_path.read_bytes()
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    if expected_manifest_sha256 is not None and manifest_sha256 != expected_manifest_sha256:
        raise ValueError("manifest hash mismatch")
    manifest = json.loads(manifest_bytes)
    if manifest.get("format_version") != 1 or not isinstance(manifest.get("files"), list):
        raise ValueError("unsupported archive manifest")
    date.fromisoformat(manifest["source_date"])
    actual = {name for name, _ in _entries(files_root, enforce_modes=True)}
    listed: set[str] = set()
    total = 0
    for entry in manifest["files"]:
        name = entry["path"]
        relative = _safe_relative(name)
        if name in listed:
            raise ValueError("duplicate manifest path")
        listed.add(name)
        path = files_root / relative
        if path.is_symlink():
            raise ValueError("archive contains a symlink")
        if not path.is_file():
            raise ValueError("archive file missing")
        size, digest = _digest(path)
        if size != entry["size"]:
            raise ValueError("size mismatch")
        if digest != entry["sha256"]:
            raise ValueError("hash mismatch")
        total += size
    if actual != listed:
        raise ValueError("unexpected or missing archive file")
    return {
        "files": len(listed),
        "bytes": total,
        "manifest_sha256": manifest_sha256,
    }


def create_archive(source: Path, destination: Path, source_date: str) -> dict:
    date.fromisoformat(source_date)
    if destination.exists() or destination.is_symlink():
        raise ValueError("destination already exists")
    if not destination.parent.is_dir():
        raise ValueError("destination parent missing")
    if source.is_symlink() or not source.is_dir():
        raise ValueError("source must be a real directory without symlink")
    if destination.parent.resolve().is_relative_to(source.resolve()):
        raise ValueError("destination cannot be inside source")
    stage = Path(tempfile.mkdtemp(prefix=".daq-archive-", dir=destination.parent))
    try:
        files_root = stage / "files"
        files_root.mkdir(mode=0o700)
        records = []

        def copy_tree(directory_fd: int, parts: tuple[str, ...]) -> None:
            with os.scandir(directory_fd) as scanner:
                names = sorted(entry.name for entry in scanner)
            for name in names:
                before_name = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                if stat.S_ISLNK(before_name.st_mode):
                    raise ValueError("source contains a symlink")
                if stat.S_ISDIR(before_name.st_mode):
                    child_fd = os.open(
                        name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=directory_fd,
                    )
                    try:
                        copy_tree(child_fd, (*parts, name))
                    finally:
                        os.close(child_fd)
                    continue
                if not stat.S_ISREG(before_name.st_mode):
                    raise ValueError("source contains a non-regular entry")
                file_fd = os.open(
                    name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                    dir_fd=directory_fd,
                )
                try:
                    before = os.fstat(file_fd)
                    if not stat.S_ISREG(before.st_mode) or before.st_ino != before_name.st_ino:
                        raise ValueError("source changed during archive")
                    target = files_root.joinpath(*parts, name)
                    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    digest = hashlib.sha256()
                    size = 0
                    with target.open("xb") as writer:
                        while block := os.read(file_fd, CHUNK_SIZE):
                            writer.write(block)
                            digest.update(block)
                            size += len(block)
                    after_fd = os.fstat(file_fd)
                    after_name = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                    identity = lambda item: (item.st_ino, item.st_size, item.st_mtime_ns)
                    if identity(before) != identity(after_fd) or identity(before) != identity(after_name) or size != before.st_size:
                        raise ValueError("source changed during archive")
                    records.append({
                        "path": PurePosixPath(*parts, name).as_posix(),
                        "size": size,
                        "sha256": digest.hexdigest(),
                        "modified_at_utc": datetime.fromtimestamp(
                            before.st_mtime, tz=timezone.utc,
                        ).isoformat(),
                    })
                finally:
                    os.close(file_fd)

        root_fd = os.open(source, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            copy_tree(root_fd, ())
        finally:
            os.close(root_fd)
        manifest = {
            "format_version": 1,
            "source_date": source_date,
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
            "files": records,
        }
        (stage / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
            encoding="utf-8",
        )
        for path in sorted(stage.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            os.chmod(path, 0o500 if path.is_dir() else 0o400)
        os.chmod(stage, 0o500)
        summary = verify_archive(stage)
        if destination.exists() or destination.is_symlink():
            raise ValueError("destination already exists")
        stage.rename(destination)
        return summary
    except Exception:
        if stage.exists():
            for path in stage.rglob("*"):
                if path.is_dir():
                    os.chmod(path, 0o700)
            os.chmod(stage, 0o700)
            shutil.rmtree(stage)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    create = subcommands.add_parser("create")
    create.add_argument("source", type=Path)
    create.add_argument("destination", type=Path)
    create.add_argument("--source-date", required=True)
    verify = subcommands.add_parser("verify")
    verify.add_argument("destination", type=Path)
    verify.add_argument("--expected-manifest-sha256", required=True)
    args = parser.parse_args()
    try:
        summary = (
            create_archive(args.source, args.destination, args.source_date)
            if args.command == "create" else verify_archive(args.destination, args.expected_manifest_sha256)
        )
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"archive error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
