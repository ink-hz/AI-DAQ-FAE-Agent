"""Private filesystem store for short-lived attachment assets."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import tempfile
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Literal, Protocol

from src.attachments.models import (
    AttachmentChunk,
    AttachmentError,
    AttachmentGCResult,
    AttachmentKind,
    AttachmentLimits,
    AttachmentManifest,
    AttachmentStorageStatus,
)

_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{40,64}$")


class AttachmentArchiveLifecyclePort(Protocol):
    def all_relations_released(self, attachment_id: str) -> bool: ...

    def expire_unarchived(self, attachment_id: str, now: datetime) -> None: ...


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_display_name(filename: str) -> str:
    leaf = (filename or "attachment").replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join(char for char in leaf if char != "\x00" and ord(char) >= 32).strip()
    return cleaned[:255] or "attachment"


class AttachmentStore:
    """Atomic local store; callers never supply a filesystem path."""

    def __init__(
        self,
        root: Path,
        limits: AttachmentLimits,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ):
        self.root = Path(root)
        self.limits = limits
        self._clock = clock
        self._lock = threading.RLock()
        self._terminal_ids: dict[str, str] = {}
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        self._cleanup_interrupted()

    def create_pending(
        self,
        filename: str,
        media_type: str,
        content: bytes,
        *,
        kind: AttachmentKind,
        owner_subject_id: str | None = None,
    ) -> AttachmentManifest:
        if self._used_bytes() + len(content) >= (
            self.limits.storage_capacity_bytes * self.limits.storage_reject_ratio
        ):
            raise AttachmentError("attachment_capacity_exceeded")

        attachment_id = secrets.token_urlsafe(32)
        source_id = f"att-src-{secrets.token_hex(6)}"
        now = self._clock()
        manifest = AttachmentManifest(
            attachment_id=attachment_id,
            source_id=source_id,
            display_name=_safe_display_name(filename),
            kind=kind,
            media_type=media_type,
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            created_at=now,
            expires_at=now + timedelta(seconds=self.limits.ttl_seconds),
            status="parsing",
            parse_coverage="empty",
            owner_subject_id=owner_subject_id,
        )
        partial_dir = self.root / f".partial-{attachment_id}"
        final_dir = self.root / attachment_id
        partial_dir.mkdir(mode=0o700)
        try:
            self._atomic_write_bytes(partial_dir / "original.bin", content)
            self._atomic_write_json(partial_dir / "manifest.json", manifest.as_dict())
            os.replace(partial_dir, final_dir)
            os.chmod(final_dir, 0o700)
        except Exception:
            shutil.rmtree(partial_dir, ignore_errors=True)
            shutil.rmtree(final_dir, ignore_errors=True)
            raise
        return manifest

    def create_pending_from_path(
        self,
        filename: str,
        media_type: str,
        source_path: Path,
        *,
        kind: AttachmentKind,
        owner_subject_id: str | None = None,
    ) -> AttachmentManifest:
        size = source_path.stat().st_size
        if self._used_bytes() + size >= (
            self.limits.storage_capacity_bytes * self.limits.storage_reject_ratio
        ):
            raise AttachmentError("attachment_capacity_exceeded")
        attachment_id = secrets.token_urlsafe(32)
        source_id = f"att-src-{secrets.token_hex(6)}"
        now = self._clock()
        partial_dir = self.root / f".partial-{attachment_id}"
        final_dir = self.root / attachment_id
        partial_dir.mkdir(mode=0o700)
        try:
            digest = self._atomic_copy(source_path, partial_dir / "original.bin")
            manifest = AttachmentManifest(
                attachment_id=attachment_id,
                source_id=source_id,
                display_name=_safe_display_name(filename),
                kind=kind,
                media_type=media_type,
                size_bytes=size,
                sha256=digest,
                created_at=now,
                expires_at=now + timedelta(seconds=self.limits.ttl_seconds),
                status="parsing",
                parse_coverage="empty",
                owner_subject_id=owner_subject_id,
            )
            self._atomic_write_json(partial_dir / "manifest.json", manifest.as_dict())
            os.replace(partial_dir, final_dir)
            os.chmod(final_dir, 0o700)
        except Exception:
            shutil.rmtree(partial_dir, ignore_errors=True)
            shutil.rmtree(final_dir, ignore_errors=True)
            raise
        return manifest

    def commit_ready(
        self,
        attachment_id: str,
        *,
        chunks: Iterable[AttachmentChunk],
        parse_coverage: str = "full",
        parser_name: str = "",
        parser_version: str = "",
        warnings: Iterable[str] = (),
        normalized_image: bytes = b"",
        normalized_media_type: str = "",
        normalized_width: int = 0,
        normalized_height: int = 0,
        archive_thumbnail: bytes = b"",
        archive_thumbnail_unavailable: bool = False,
    ) -> AttachmentManifest:
        if normalized_image and (
            normalized_media_type not in {"image/png", "image/jpeg"}
            or normalized_width <= 0
            or normalized_height <= 0
        ):
            raise AttachmentError("attachment_image_metadata_invalid")
        manifest = self._read_manifest(attachment_id)
        chunk_values = [chunk.as_dict() for chunk in chunks]
        self._atomic_write_json(self._attachment_dir(attachment_id) / "chunks.json", chunk_values)
        if normalized_image:
            self._atomic_write_bytes(
                self._attachment_dir(attachment_id) / "normalized-image.bin",
                normalized_image,
            )
        thumbnail_status = "not_applicable"
        thumbnail_media_type = None
        thumbnail_size_bytes = None
        thumbnail_sha256 = None
        if archive_thumbnail:
            self._atomic_write_bytes(
                self._attachment_dir(attachment_id) / "archive-thumbnail.webp",
                archive_thumbnail,
            )
            thumbnail_status = "ready"
            thumbnail_media_type = "image/webp"
            thumbnail_size_bytes = len(archive_thumbnail)
            thumbnail_sha256 = hashlib.sha256(archive_thumbnail).hexdigest()
        elif archive_thumbnail_unavailable:
            thumbnail_status = "unavailable"
        updated = replace(
            manifest,
            status="ready",
            parse_coverage=parse_coverage,
            parser_name=parser_name,
            parser_version=parser_version,
            warnings=tuple(warnings),
            normalized_media_type=normalized_media_type or None,
            normalized_size_bytes=len(normalized_image) if normalized_image else None,
            normalized_width=normalized_width or None,
            normalized_height=normalized_height or None,
            archive_thumbnail_status=thumbnail_status,
            archive_thumbnail_media_type=thumbnail_media_type,
            archive_thumbnail_size_bytes=thumbnail_size_bytes,
            archive_thumbnail_sha256=thumbnail_sha256,
        )
        self._write_manifest(updated)
        return updated

    def get(self, attachment_id: str) -> AttachmentManifest:
        manifest = self._read_manifest(attachment_id)
        if manifest.expires_at <= self._clock():
            raise AttachmentError("attachment_expired")
        return manifest

    def get_for_deletion(self, attachment_id: str) -> AttachmentManifest:
        """Return owner metadata even after processing TTL, for authorized erasure."""
        return self._read_manifest(attachment_id)

    def read_bytes(self, attachment_id: str) -> bytes:
        self.get(attachment_id)
        return (self._attachment_dir(attachment_id) / "original.bin").read_bytes()

    def read_chunks(self, attachment_id: str) -> list[AttachmentChunk]:
        self.get(attachment_id)
        path = self._attachment_dir(attachment_id) / "chunks.json"
        if not path.is_file():
            return []
        try:
            values = json.loads(path.read_text(encoding="utf-8"))
            return [AttachmentChunk.from_dict(item) for item in values]
        except Exception as exc:
            raise AttachmentError("attachment_chunks_invalid") from exc

    def read_normalized_image(self, attachment_id: str) -> bytes:
        manifest = self.get(attachment_id)
        if manifest.kind != "image":
            raise AttachmentError("attachment_not_image")
        path = self._attachment_dir(attachment_id) / "normalized-image.bin"
        if not path.is_file():
            raise AttachmentError("attachment_image_unavailable")
        return path.read_bytes()

    def bind(
        self,
        attachment_id: str,
        session_id: str,
        *,
        owner_subject_id: str | None = None,
    ) -> AttachmentManifest:
        return self.bind_many(
            [attachment_id],
            session_id,
            owner_subject_id=owner_subject_id,
        )[0]

    def bind_many(
        self,
        attachment_ids: Iterable[str],
        session_id: str,
        *,
        owner_subject_id: str | None = None,
    ) -> list[AttachmentManifest]:
        ids = list(dict.fromkeys(attachment_ids))
        with self._lock:
            manifests = [self.get(attachment_id) for attachment_id in ids]
            for manifest in manifests:
                if manifest.owner_subject_id != owner_subject_id:
                    raise AttachmentError("attachment_owner_mismatch")
                if manifest.status != "ready":
                    raise AttachmentError("attachment_not_ready")
                if manifest.bound_session_id not in (None, session_id):
                    raise AttachmentError("attachment_session_mismatch")
            updated = [
                manifest if manifest.bound_session_id == session_id
                else replace(manifest, bound_session_id=session_id)
                for manifest in manifests
            ]
            written = 0
            try:
                for manifest in updated:
                    self._write_manifest(manifest)
                    written += 1
            except Exception:
                for original in manifests[:written]:
                    self._write_manifest(original)
                raise
            return updated

    def protect_for_archive(
        self,
        attachment_ids: Iterable[str],
        until: datetime,
    ) -> list[AttachmentManifest]:
        ids = list(dict.fromkeys(attachment_ids))
        with self._lock:
            manifests = [self.get(attachment_id) for attachment_id in ids]
            updated = [
                replace(
                    manifest,
                    archive_protected_until=max(
                        filter(
                            None,
                            (manifest.archive_protected_until, until),
                        )
                    ),
                )
                for manifest in manifests
            ]
            written = 0
            try:
                for manifest in updated:
                    self._write_manifest(manifest)
                    written += 1
            except Exception:
                for original in manifests[:written]:
                    self._write_manifest(original)
                raise
            return updated

    def read_archive_variant(
        self,
        attachment_id: str,
        variant: Literal["original", "thumbnail"],
        *,
        now: datetime,
    ) -> bytes:
        manifest = self._read_manifest(attachment_id)
        if (
            manifest.archive_protected_until is None
            or manifest.archive_protected_until <= now
        ):
            raise AttachmentError("archive_handoff_expired")
        if variant == "original":
            filename = "original.bin"
            expected_size = manifest.size_bytes
            expected_hash = manifest.sha256
        elif variant == "thumbnail":
            if manifest.archive_thumbnail_status != "ready":
                raise AttachmentError("archive_thumbnail_unavailable")
            filename = "archive-thumbnail.webp"
            expected_size = manifest.archive_thumbnail_size_bytes
            expected_hash = manifest.archive_thumbnail_sha256
        else:
            raise AttachmentError("archive_variant_invalid")
        attachment_dir = self._attachment_dir(attachment_id)
        path = attachment_dir / filename
        if attachment_dir.is_symlink() or not path.is_file() or path.is_symlink():
            raise AttachmentError("archive_content_missing")
        content = path.read_bytes()
        if len(content) != expected_size or hashlib.sha256(content).hexdigest() != expected_hash:
            raise AttachmentError("archive_content_mismatch")
        return content

    def delete(self, attachment_id: str) -> None:
        try:
            target = self._attachment_dir(attachment_id)
        except AttachmentError:
            return
        with self._lock:
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
                self._terminal_ids[attachment_id] = "attachment_deleted"

    def gc_expired(
        self,
        lifecycle: AttachmentArchiveLifecyclePort | None = None,
    ) -> AttachmentGCResult:
        deleted = 0
        failed = 0
        for path in self.root.iterdir():
            if not path.is_dir() or path.name.startswith("."):
                continue
            try:
                manifest = self._read_manifest(path.name)
                now = self._clock()
                if manifest.expires_at > now:
                    continue
                protected_until = manifest.archive_protected_until
                if protected_until is not None:
                    if lifecycle is None:
                        failed += 1
                        continue
                    if protected_until > now:
                        if not lifecycle.all_relations_released(path.name):
                            continue
                    else:
                        lifecycle.expire_unarchived(path.name, now)
                shutil.rmtree(path)
                self._terminal_ids[path.name] = "attachment_expired"
                deleted += 1
            except Exception:
                failed += 1
        return AttachmentGCResult(deleted_count=deleted, failed_count=failed)

    def storage_status(self) -> AttachmentStorageStatus:
        return AttachmentStorageStatus(
            ready=self.root.is_dir() and os.access(self.root, os.W_OK),
            used_bytes=self._used_bytes(),
            capacity_bytes=self.limits.storage_capacity_bytes,
        )

    def _attachment_dir(self, attachment_id: str) -> Path:
        if not _ID_PATTERN.fullmatch(attachment_id or ""):
            raise AttachmentError("attachment_not_found")
        return self.root / attachment_id

    def _read_manifest(self, attachment_id: str) -> AttachmentManifest:
        terminal = self._terminal_ids.get(attachment_id)
        if terminal:
            raise AttachmentError(terminal)
        path = self._attachment_dir(attachment_id) / "manifest.json"
        if not path.is_file():
            raise AttachmentError("attachment_not_found")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return AttachmentManifest.from_dict(value)
        except AttachmentError:
            raise
        except Exception as exc:
            raise AttachmentError("attachment_manifest_invalid") from exc

    def _write_manifest(self, manifest: AttachmentManifest) -> None:
        self._atomic_write_json(
            self._attachment_dir(manifest.attachment_id) / "manifest.json",
            manifest.as_dict(),
        )

    def _atomic_write_json(self, path: Path, value: object) -> None:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._atomic_write_bytes(path, encoded)

    def _atomic_write_bytes(self, path: Path, content: bytes) -> None:
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}-", suffix=".tmp", dir=path.parent)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            os.chmod(path, 0o600)
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            Path(temporary).unlink(missing_ok=True)
            raise

    def _atomic_copy(self, source_path: Path, destination: Path) -> str:
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{destination.name}-", suffix=".tmp", dir=destination.parent,
        )
        digest = hashlib.sha256()
        try:
            os.fchmod(descriptor, 0o600)
            with source_path.open("rb") as source, os.fdopen(descriptor, "wb") as target:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, destination)
            os.chmod(destination, 0o600)
            return digest.hexdigest()
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            Path(temporary).unlink(missing_ok=True)
            raise

    def _cleanup_interrupted(self) -> None:
        for pattern in (".partial-*", ".batch-*"):
            for path in self.root.glob(pattern):
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)

    def _used_bytes(self) -> int:
        total = 0
        for path in self.root.rglob("*"):
            if path.is_file():
                relative_parts = path.relative_to(self.root).parts
                if any(part.startswith(".") for part in relative_parts):
                    continue
                try:
                    total += path.stat().st_size
                except FileNotFoundError:
                    continue
        return total


class AttachmentGCWorker:
    """Idempotent lifecycle worker; production cadence defaults to one minute."""

    def __init__(
        self,
        store: AttachmentStore,
        interval_seconds: float = 60.0,
        *,
        lifecycle: AttachmentArchiveLifecyclePort | None = None,
    ):
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self.store = store
        self.interval_seconds = interval_seconds
        self.lifecycle = lifecycle
        self.last_result = AttachmentGCResult(deleted_count=0, failed_count=0)
        self.deleted_total = 0
        self.failed_total = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="attachment-gc",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=min(self.interval_seconds + 1, 5))
        self._thread = None

    def _run(self) -> None:
        while not self._stop.is_set():
            self.last_result = self.store.gc_expired(self.lifecycle)
            self.deleted_total += self.last_result.deleted_count
            self.failed_total += self.last_result.failed_count
            self._stop.wait(self.interval_seconds)
