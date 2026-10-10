"""Immutable local DAQ knowledge snapshots and an explicit atomic active pointer."""

from __future__ import annotations

from collections import Counter
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile

from daq_fae.knowledge.records import validate_records
from daq_fae.knowledge.section_release import (compile_sections, section_fingerprint,
                                               section_indices)


_RELEASE_ID = re.compile(r"^[0-9a-f]{64}$")


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _review_ok(review: object) -> bool:
    if not isinstance(review, dict):
        return False
    if not all(isinstance(review.get(key), str) and review[key].strip()
               for key in ("reviewer", "reviewed_at", "dev_batch")):
        return False
    try:
        date.fromisoformat(review["reviewed_at"])
    except ValueError:
        return False
    return True


def _prepare_root(root: Path) -> Path:
    if root.is_symlink():
        raise ValueError("release root must not be a symlink")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    releases = root / "releases"
    if releases.is_symlink():
        raise ValueError("releases directory must not be a symlink")
    releases.mkdir(mode=0o700, exist_ok=True)
    return releases


def _read_release(root: Path, release_id: str) -> tuple[dict, str]:
    if not _RELEASE_ID.fullmatch(release_id):
        raise ValueError("invalid release ID")
    directory = root / "releases" / release_id
    manifest_path = directory / "manifest.json"
    if directory.is_symlink() or not directory.is_dir() or manifest_path.is_symlink() \
            or not manifest_path.is_file():
        raise ValueError("release missing or linked")
    data = manifest_path.read_bytes()
    if _digest(data) != release_id:
        raise ValueError("release manifest digest mismatch")
    manifest = json.loads(data)
    from daq_fae.knowledge.reviewed_view import ReviewedKnowledge
    if not isinstance(manifest, dict) or type(manifest.get("format_version")) is not int \
            or manifest["format_version"] not in {1, 2}:
        raise ValueError("knowledge release format invalid")
    if manifest["format_version"] == 1 and "sections" in manifest:
        raise ValueError("knowledge release format v1 is records only")
    if manifest["format_version"] == 2:
        ReviewedKnowledge.from_manifest(release_id, manifest)
    return manifest, release_id


def publish_release(root: Path, snapshot: dict, records: list[dict],
                    previous_release: str | None, review: dict, *,
                    sections: list[dict] | None = None, bodies: dict[str, str] | None = None) -> str:
    """Stage a reviewed, content-addressed release; never activate implicitly."""
    if not _review_ok(review):
        raise ValueError("release review missing")
    if not isinstance(snapshot.get("archive_manifest_sha256"), str) or not \
            _RELEASE_ID.fullmatch(snapshot["archive_manifest_sha256"]):
        raise ValueError("archive manifest identity missing")
    normalized, findings = validate_records(records, snapshot)
    if findings:
        raise ValueError("record validation failed: " +
                         ", ".join(sorted({finding["code"] for finding in findings})))
    if previous_release is not None:
        _read_release(root, previous_release)
    manifest = {
        "format_version": 1,
        "archive_manifest_sha256": snapshot["archive_manifest_sha256"],
        "source_date": snapshot.get("source_date"),
        "sources": [{key: source[key] for key in ("path", "sha256", "size", "kind")}
                    for source in snapshot["sources"]],
        "source_count": len(snapshot["sources"]),
        "record_count": len(normalized),
        "status_counts": dict(sorted(Counter(row["status"] for row in normalized).items())),
        "answerable_count": sum(1 for row in normalized if row["answerable"]),
        "records": sorted(normalized, key=lambda row: row["id"]),
        "previous_release": previous_release,
        "review": review,
    }
    if sections is not None:
        compiled = compile_sections(sections, bodies, normalized, snapshot)
        manifest.update(format_version=2, sections=compiled,
                        runtime_contract="daq-reviewed-sections-v2",
                        source_locations=sorted([
                            {k: chunk[k] for k in ("source_path", "source_sha256", "locator")}
                            for chunk in snapshot.get("chunks", [])], key=lambda c: _json_bytes(c)))
        manifest.update(section_indices(compiled, normalized))
    elif bodies is not None:
        raise ValueError("section bodies require sections")
    # Apply runtime URL/role/schema gates before creating a staged artifact.
    from daq_fae.knowledge.reviewed_view import ReviewedKnowledge
    if sections is not None:
        ReviewedKnowledge.from_manifest("0" * 64, manifest)
    data = _json_bytes(manifest)
    release_id = _digest(data)
    releases = _prepare_root(root)
    destination = releases / release_id
    if destination.exists() or destination.is_symlink():
        existing, _ = _read_release(root, release_id)
        if _json_bytes(existing) != data:
            raise ValueError("release ID collision")
        return release_id
    stage = Path(tempfile.mkdtemp(prefix=".release-", dir=releases))
    try:
        manifest_path = stage / "manifest.json"
        descriptor = os.open(manifest_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        stage.chmod(0o500)
        os.replace(stage, destination)
    finally:
        if stage.exists():
            stage.chmod(0o700)
            for path in stage.iterdir():
                path.chmod(0o600)
                path.unlink()
            stage.rmdir()
    return release_id


def activate_release(root: Path, release_id: str) -> None:
    """Atomically switch the local active pointer, including for rollback."""
    _manifest, checked_id = _read_release(root, release_id)
    releases = _prepare_root(root)
    pointer = root / "active.json"
    if pointer.is_symlink():
        raise ValueError("active pointer must not be a symlink")
    payload = _json_bytes({"release_id": checked_id})
    descriptor, name = tempfile.mkstemp(prefix=".active-", dir=root)
    try:
        os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, pointer)
        directory_fd = os.open(root, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_active_release(root: Path) -> dict | None:
    pointer = root / "active.json"
    if pointer.is_symlink():
        raise ValueError("active pointer must not be a symlink")
    if not pointer.exists():
        return None
    payload = json.loads(pointer.read_text(encoding="utf-8"))
    release_id = payload.get("release_id")
    if not isinstance(release_id, str):
        raise ValueError("active pointer invalid")
    manifest, _ = _read_release(root, release_id)
    return {"release_id": release_id, "manifest": manifest}
