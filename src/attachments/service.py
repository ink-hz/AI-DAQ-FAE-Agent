"""Synchronous-ingest orchestration for multipart attachment batches."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from src.attachments.archive_thumbnail import render_archive_thumbnail
from src.attachments.detection import UploadDetectionError, detect_upload, inspect_upload
from src.attachments.models import AttachmentError, AttachmentLimits, AttachmentManifest
from src.attachments.parsers import parse_attachment
from src.attachments.store import AttachmentStore

logger = logging.getLogger(__name__)


class AsyncUpload(Protocol):
    filename: str | None
    content_type: str | None

    async def read(self, size: int = -1) -> bytes: ...


@dataclass(frozen=True)
class AttachmentBatchResult:
    request_id: str
    results: tuple[dict, ...]

    @property
    def all_ok(self) -> bool:
        return bool(self.results) and all(item.get("ok") is True for item in self.results)

    @property
    def payload(self) -> dict:
        return {"request_id": self.request_id, "results": list(self.results)}


class AttachmentService:
    READ_CHUNK_BYTES = 1024 * 1024

    def __init__(self, store: AttachmentStore, limits: AttachmentLimits):
        self.store = store
        self.limits = limits

    @staticmethod
    def new_request_id() -> str:
        return f"upload-{secrets.token_hex(12)}"

    async def ingest_batch(
        self,
        files: list[AsyncUpload],
        *,
        request_id: str | None = None,
        owner_subject_id: str | None = None,
    ) -> AttachmentBatchResult:
        request_id = request_id or self.new_request_id()
        if not files or len(files) > self.limits.max_files:
            raise AttachmentError("attachment_file_count_exceeded")

        with tempfile.TemporaryDirectory(
            prefix=".batch-", dir=self.store.root,
        ) as batch_dir_text:
            batch_dir = Path(batch_dir_text)
            staged: list[tuple[AsyncUpload, Path]] = []
            total = 0
            for index, upload in enumerate(files):
                path = batch_dir / f"{index}.bin"
                with path.open("wb") as handle:
                    os.chmod(path, 0o600)
                    while True:
                        chunk = await upload.read(self.READ_CHUNK_BYTES)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > self.limits.max_batch_bytes:
                            raise AttachmentError("attachment_batch_too_large")
                        handle.write(chunk)
                staged.append((upload, path))

            results = []
            for index, (upload, path) in enumerate(staged):
                try:
                    result = await self._ingest_one(
                        upload,
                        path,
                        owner_subject_id=owner_subject_id,
                    )
                    results.append({"index": index, "ok": True, "attachment": result})
                except UploadDetectionError as exc:
                    _log_detection_rejection(
                        request_id=request_id,
                        index=index,
                        upload=upload,
                        path=path,
                        error=exc,
                    )
                    results.append({
                        "index": index,
                        "ok": False,
                        "error": {"code": exc.code, "message": _public_message(exc.code)},
                    })
                except AttachmentError as exc:
                    results.append({
                        "index": index,
                        "ok": False,
                        "error": {"code": exc.code, "message": _public_message(exc.code)},
                    })
        return AttachmentBatchResult(request_id=request_id, results=tuple(results))

    async def _ingest_one(
        self,
        upload: AsyncUpload,
        path: Path,
        *,
        owner_subject_id: str | None = None,
    ) -> dict:
        filename = upload.filename or "attachment"
        media_type = upload.content_type or "application/octet-stream"
        with path.open("rb") as handle:
            head = handle.read(4096)
        detected = detect_upload(filename, media_type, head)
        parsed = await asyncio.to_thread(parse_attachment, path, detected, self.limits)
        archive_thumbnail = b""
        archive_thumbnail_unavailable = False
        if detected.kind == "image":
            try:
                archive_thumbnail = await asyncio.to_thread(
                    render_archive_thumbnail,
                    path,
                    max_pixels=self.limits.image_max_pixels,
                )
            except AttachmentError:
                archive_thumbnail_unavailable = True
        pending: AttachmentManifest | None = None
        try:
            pending = self.store.create_pending_from_path(
                filename,
                media_type,
                path,
                kind=detected.kind,
                owner_subject_id=owner_subject_id,
            )
            chunks = [replace(chunk, source_id=pending.source_id) for chunk in parsed.chunks]
            ready = self.store.commit_ready(
                pending.attachment_id,
                chunks=chunks,
                parse_coverage=parsed.parse_coverage,
                parser_name=parsed.parser_name,
                parser_version=parsed.parser_version,
                warnings=parsed.warnings,
                normalized_image=parsed.normalized_image,
                normalized_media_type=parsed.normalized_media_type,
                normalized_width=parsed.normalized_width,
                normalized_height=parsed.normalized_height,
                archive_thumbnail=archive_thumbnail,
                archive_thumbnail_unavailable=archive_thumbnail_unavailable,
            )
        except Exception:
            if pending is not None:
                self.store.delete(pending.attachment_id)
            raise
        return _public_manifest(ready)


def _log_detection_rejection(
    *,
    request_id: str,
    index: int,
    upload: AsyncUpload,
    path: Path,
    error: UploadDetectionError,
) -> None:
    with path.open("rb") as handle:
        inspection = inspect_upload(
            upload.filename or "",
            upload.content_type or "",
            handle.read(4096),
        )
    diagnostic = {
        "request_id": request_id,
        "batch_index": index,
        "failure_code": error.code,
        "detection_stage": error.stage,
        "extension": inspection.extension,
        "declared_mime": inspection.media_type,
        "staged_byte_count": path.stat().st_size,
        "signature_category": inspection.signature_category,
    }
    logger.warning(
        "attachment_detection_rejected %s",
        json.dumps(diagnostic, ensure_ascii=True, separators=(",", ":"), sort_keys=True),
    )


def _public_manifest(manifest: AttachmentManifest) -> dict:
    return {
        "attachment_id": manifest.attachment_id,
        "source_id": manifest.source_id,
        "display_name": manifest.display_name,
        "kind": manifest.kind,
        "media_type": manifest.media_type,
        "size_bytes": manifest.size_bytes,
        "status": manifest.status,
        "parse_coverage": manifest.parse_coverage,
        "expires_at": manifest.expires_at.isoformat(),
        "warnings": list(manifest.warnings),
    }


def _public_message(code: str) -> str:
    return {
        "unsupported_attachment_type": "不支持该附件格式",
        "attachment_mime_mismatch": "文件内容与声明类型不一致",
        "attachment_limit_exceeded": "附件超过解析或内容上限",
        "attachment_parse_timeout": "附件解析超时",
        "attachment_parse_failed": "附件损坏或无法解析",
        "attachment_capacity_exceeded": "临时附件容量不足",
    }.get(code, "附件处理失败")
