from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.request import urlopen

from .batch_manifest import FeedbackClosureBatch, load_batch_manifest


RELEASE_STATUSES = frozenset({
    "prepared",
    "failed_before_switch",
    "succeeded",
    "rolled_back",
    "rollback_failed",
})
HANDOFF_STATES = frozenset({
    "prepared",
    "pending",
    "acknowledged",
    "blocked",
    "terminal_failed",
})


class OutboxSecurityError(RuntimeError):
    pass


class OutboxStateError(RuntimeError):
    pass


@dataclass(frozen=True)
class BatchArtifact:
    manifest: FeedbackClosureBatch
    path: Path
    sha256: str


@dataclass(frozen=True)
class ReleaseDescriptor:
    name: str
    git_sha: str
    branch: str
    manifest_path: Path


@dataclass(frozen=True)
class OutboxRecord:
    path: Path
    payload: dict[str, Any]


def default_outbox_dir(*, home: Path | None = None) -> Path:
    base = home if home is not None else Path.home()
    return (
        base
        / "Library/Application Support/OrbbecAI-Agent-Platform"
        / "feedback-closure-outbox"
    )


def configured_outbox_dir() -> Path:
    configured = os.getenv("AI_FAE_FEEDBACK_CLOSURE_OUTBOX_DIR")
    return Path(configured).expanduser() if configured else default_outbox_dir()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _reject_symlink_components(path: Path) -> None:
    current = path
    while True:
        if current.is_symlink():
            raise OutboxSecurityError(f"outbox path contains symlink: {current}")
        if current.parent == current:
            return
        current = current.parent


def _git_worktree_paths(repo_root: Path) -> list[Path]:
    if not repo_root.exists():
        return [repo_root.resolve()]
    result = subprocess.run(
        ["git", "-C", str(repo_root), "worktree", "list", "--porcelain"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return [repo_root.resolve()]
    return [
        Path(line.removeprefix("worktree ")).resolve()
        for line in result.stdout.splitlines()
        if line.startswith("worktree ")
    ]


def _secure_outbox_dir(outbox_dir: Path, repo_root: Path | None = None) -> Path:
    raw = Path(outbox_dir).expanduser()
    if not raw.is_absolute():
        raise OutboxSecurityError("outbox path must be absolute")
    _reject_symlink_components(raw)
    resolved = raw.resolve(strict=False)
    if repo_root is not None:
        for worktree in _git_worktree_paths(Path(repo_root)):
            if _is_within(resolved, worktree):
                raise OutboxSecurityError("outbox path must be outside every Git worktree")
    if resolved.exists() and not resolved.is_dir():
        raise OutboxSecurityError("outbox path is not a directory")
    resolved.mkdir(parents=True, mode=0o700, exist_ok=True)
    os.chmod(resolved, 0o700)
    if stat.S_IMODE(resolved.stat().st_mode) != 0o700:
        raise OutboxSecurityError("outbox directory must have mode 0700")
    return resolved


def _atomic_write(path: Path, payload: Mapping[str, Any]) -> None:
    data = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    temp = path.parent / f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        os.chmod(path, 0o600)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temp.exists():
            temp.unlink()


def _idempotency_key(batch_id: str, release_name: str, git_sha: str) -> str:
    raw = f"{batch_id}\0{release_name}\0{git_sha}".encode("utf-8")
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _record_path(outbox: Path, idempotency_key: str) -> Path:
    prefix, separator, digest = idempotency_key.partition(":")
    if prefix != "sha256" or not separator or len(digest) != 64 or any(
        char not in "0123456789abcdef" for char in digest
    ):
        raise OutboxStateError("invalid idempotency key")
    return outbox / f"{digest}.json"


def _read_record(path: Path) -> OutboxRecord:
    if path.is_symlink():
        raise OutboxSecurityError("outbox record must not be a symlink")
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise OutboxSecurityError("outbox record must have mode 0600")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise OutboxStateError(f"cannot read outbox record: {error}") from error
    if not isinstance(payload, dict):
        raise OutboxStateError("outbox record must be an object")
    return OutboxRecord(path=path, payload=payload)


def prepare_handoff(
    outbox_dir: Path,
    *,
    batch: BatchArtifact,
    release: ReleaseDescriptor,
    repo_root: Path | None = None,
) -> OutboxRecord:
    outbox = _secure_outbox_dir(outbox_dir, repo_root)
    key = _idempotency_key(
        batch.manifest.batch_id,
        release.name,
        release.git_sha,
    )
    path = _record_path(outbox, key)
    expected_identity = {
        "batch_id": batch.manifest.batch_id,
        "batch_path": str(batch.path),
        "batch_sha256": batch.sha256,
        "release_name": release.name,
        "release_git_sha": release.git_sha,
        "release_branch": release.branch,
    }
    if path.exists() or path.is_symlink():
        current = _read_record(path)
        actual_identity = {
            "batch_id": current.payload.get("batch", {}).get("id"),
            "batch_path": current.payload.get("batch", {}).get("path"),
            "batch_sha256": current.payload.get("batch", {}).get("sha256"),
            "release_name": current.payload.get("release", {}).get("name"),
            "release_git_sha": current.payload.get("release", {}).get("git_sha"),
            "release_branch": current.payload.get("release", {}).get("branch"),
        }
        if actual_identity != expected_identity:
            raise OutboxStateError("idempotency key collision or record mismatch")
        return current

    now = _utc_now()
    payload = {
        "schema_version": 1,
        "idempotency_key": key,
        "batch": {
            "id": batch.manifest.batch_id,
            "path": str(batch.path),
            "sha256": batch.sha256,
        },
        "release": {
            "name": release.name,
            "git_sha": release.git_sha,
            "branch": release.branch,
            "transaction_status": "prepared",
            "manifest_path": str(release.manifest_path),
            "manifest_sha256": None,
        },
        "handoff": {
            "state": "prepared",
            "attempt_count": 0,
            "last_error": None,
            "acknowledged_at": None,
            "result": None,
        },
        "created_at": now,
        "updated_at": now,
    }
    _atomic_write(path, payload)
    return OutboxRecord(path=path, payload=payload)


def _update_record(
    outbox_dir: Path,
    idempotency_key: str,
    update: Callable[[dict[str, Any]], None],
) -> OutboxRecord:
    outbox = _secure_outbox_dir(outbox_dir)
    path = _record_path(outbox, idempotency_key)
    if not path.exists():
        raise OutboxStateError("outbox record not found")
    record = _read_record(path)
    update(record.payload)
    record.payload["updated_at"] = _utc_now()
    _atomic_write(path, record.payload)
    return OutboxRecord(path=path, payload=record.payload)


def finalize_handoff(
    outbox_dir: Path,
    idempotency_key: str,
    *,
    release_status: str,
    release_manifest: Path,
) -> OutboxRecord:
    if release_status not in RELEASE_STATUSES - {"prepared"}:
        raise OutboxStateError("invalid final release status")
    manifest_path = Path(release_manifest).resolve()
    try:
        manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise OutboxStateError(f"cannot read release manifest: {error}") from error
    if not isinstance(manifest_payload, dict) or manifest_payload.get("status") != release_status:
        raise OutboxStateError("release manifest status does not match final status")
    manifest_sha = _sha256(manifest_path)

    def update(payload: dict[str, Any]) -> None:
        current = payload["handoff"]["state"]
        if current == "pending" and (
            payload["release"].get("transaction_status") == release_status
            and payload["release"].get("manifest_path") == str(manifest_path)
            and payload["release"].get("manifest_sha256") == manifest_sha
        ):
            return
        if current != "prepared":
            raise OutboxStateError(
                f"invalid handoff state transition: {current} -> finalize"
            )
        payload["release"].update({
            "transaction_status": release_status,
            "manifest_path": str(manifest_path),
            "manifest_sha256": manifest_sha,
        })
        payload["handoff"]["state"] = (
            "pending" if release_status == "succeeded" else "blocked"
        )

    return _update_record(outbox_dir, idempotency_key, update)


def acknowledge_handoff(
    outbox_dir: Path,
    idempotency_key: str,
    *,
    result: Mapping[str, Any],
) -> OutboxRecord:
    def update(payload: dict[str, Any]) -> None:
        current = payload["handoff"]["state"]
        if current == "acknowledged" and payload["handoff"].get("result") == dict(result):
            return
        if current != "pending":
            raise OutboxStateError(
                f"invalid handoff state transition: {current} -> acknowledged"
            )
        payload["handoff"].update({
            "state": "acknowledged",
            "acknowledged_at": _utc_now(),
            "result": dict(result),
            "last_error": None,
        })

    return _update_record(outbox_dir, idempotency_key, update)


def list_handoffs(
    outbox_dir: Path,
    *,
    states: set[str] | None = None,
) -> list[OutboxRecord]:
    if states and not states <= HANDOFF_STATES:
        raise OutboxStateError("unknown handoff state")
    outbox = _secure_outbox_dir(outbox_dir)
    records = [_read_record(path) for path in sorted(outbox.glob("*.json"))]
    if states:
        records = [
            record
            for record in records
            if record.payload.get("handoff", {}).get("state") in states
        ]
    return records


def _fetch_json(url: str) -> Any:
    with urlopen(url, timeout=10) as response:
        return json.load(response)


def _git_is_ancestor(repo_root: Path, ancestor: str, descendant: str) -> bool:
    return subprocess.run(
        [
            "git",
            "-C",
            str(repo_root),
            "merge-base",
            "--is-ancestor",
            ancestor,
            descendant,
        ],
        check=False,
        capture_output=True,
    ).returncode == 0


def release_contains_batch(
    repo_root: Path,
    batch: FeedbackClosureBatch,
    release_git_sha: str,
) -> bool:
    return _git_is_ancestor(
        Path(repo_root).resolve(),
        batch.remediation_commit,
        release_git_sha,
    )


def load_batch_artifact(path: Path, repo_root: Path) -> BatchArtifact:
    root = Path(repo_root).resolve()
    manifest_path = Path(path).resolve()
    manifest = load_batch_manifest(manifest_path, repo_root=root)
    stored_path: Path
    try:
        stored_path = manifest_path.relative_to(root)
    except ValueError:
        stored_path = manifest_path
    return BatchArtifact(
        manifest=manifest,
        path=stored_path,
        sha256=_sha256(manifest_path),
    )


def backfill_succeeded(
    outbox_dir: Path,
    *,
    batch: BatchArtifact,
    release_manifest: Path,
    repo_root: Path,
    production_health: str,
    fetch_json: Callable[[str], Any] = _fetch_json,
) -> OutboxRecord:
    root = Path(repo_root).resolve()
    manifest_path = Path(release_manifest).resolve()
    canonical_manifests = root / "dist/release/manifests"
    if not _is_within(manifest_path, canonical_manifests):
        raise OutboxSecurityError("release manifest must be in canonical dist/release")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise OutboxStateError(f"cannot read release manifest: {error}") from error
    if not isinstance(manifest, dict) or manifest.get("status") != "succeeded":
        raise OutboxStateError("backfill requires a succeeded release manifest")
    release_name = manifest.get("release_name")
    release_sha = manifest.get("git_sha")
    release_branch = manifest.get("git_branch")
    if not all(isinstance(value, str) and value for value in (
        release_name,
        release_sha,
        release_branch,
    )):
        raise OutboxStateError("release manifest identity is incomplete")
    if not _git_is_ancestor(
        root,
        batch.manifest.remediation_commit,
        release_sha,
    ):
        raise OutboxStateError("remediation commit is not a release ancestor")

    package_name = Path(str(manifest.get("package_path", ""))).name
    if not package_name:
        raise OutboxStateError("release manifest package name is missing")
    canonical_package = root / "dist/release" / package_name
    expected_package_sha = manifest.get("package_sha256")
    if (
        not canonical_package.is_file()
        or not isinstance(expected_package_sha, str)
        or _sha256(canonical_package) != expected_package_sha
    ):
        raise OutboxStateError("canonical release package SHA-256 mismatch")

    health = fetch_json(production_health)
    build = health.get("build") if isinstance(health, Mapping) else None
    if not isinstance(build, Mapping) or not (
        build.get("available") is True
        and build.get("git_sha") == release_sha
        and build.get("release_name") == release_name
    ):
        raise OutboxStateError("production build identity does not match release")

    descriptor = ReleaseDescriptor(
        name=release_name,
        git_sha=release_sha,
        branch=release_branch,
        manifest_path=manifest_path,
    )
    prepared = prepare_handoff(
        outbox_dir,
        batch=batch,
        release=descriptor,
        repo_root=root,
    )
    return finalize_handoff(
        outbox_dir,
        prepared.payload["idempotency_key"],
        release_status="succeeded",
        release_manifest=manifest_path,
    )
