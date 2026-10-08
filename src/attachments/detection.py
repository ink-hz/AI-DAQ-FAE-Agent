"""Allowlist-based attachment type detection using extension, MIME, and signatures."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from src.attachments.models import AttachmentError, AttachmentKind

DetectionStage = Literal["extension", "signature", "mime"]

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_CODE_EXTENSIONS = {
    "c", "cc", "cpp", "h", "hpp", "java", "js", "jsx", "json", "py", "rs",
    "sh", "ts", "tsx", "yaml", "yml",
}
_CODE_MIME_TYPES = {
    "application/json",
    "application/x-sh",
    "application/yaml",
    "text/javascript",
    "text/plain",
    "text/x-c",
    "text/x-c++",
    "text/x-java-source",
    "text/x-python",
    "text/x-rust",
    "text/x-shellscript",
    "text/yaml",
}


@dataclass(frozen=True)
class DetectedUpload:
    extension: str
    kind: AttachmentKind
    media_type: str


@dataclass(frozen=True)
class UploadInspection:
    extension: str
    media_type: str
    signature_category: str


class UploadDetectionError(AttachmentError):
    def __init__(self, code: str, *, stage: DetectionStage):
        self.stage = stage
        super().__init__(code)


def _signature_category(head: bytes) -> str:
    if not head:
        return "empty"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if len(head) >= 12 and head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "webp"
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"PK\x03\x04"):
        return "zip"
    return "unknown"


def inspect_upload(filename: str, declared_type: str, head: bytes) -> UploadInspection:
    return UploadInspection(
        extension=Path((filename or "").replace("\\", "/")).suffix.lower().lstrip("."),
        media_type=(declared_type or "application/octet-stream")
        .split(";", 1)[0].strip().lower(),
        signature_category=_signature_category(head),
    )


def detect_upload(filename: str, declared_type: str, head: bytes) -> DetectedUpload:
    inspection = inspect_upload(filename, declared_type, head)
    extension = inspection.extension
    media_type = inspection.media_type

    expected: set[str]
    kind: AttachmentKind
    valid_signature = True
    if extension in {"jpg", "jpeg"}:
        kind, expected = "image", {"image/jpeg"}
        valid_signature = head.startswith(b"\xff\xd8\xff")
    elif extension == "png":
        kind, expected = "image", {"image/png"}
        valid_signature = head.startswith(b"\x89PNG\r\n\x1a\n")
    elif extension == "webp":
        kind, expected = "image", {"image/webp"}
        valid_signature = len(head) >= 12 and head.startswith(b"RIFF") and head[8:12] == b"WEBP"
    elif extension == "pdf":
        kind, expected = "pdf", {"application/pdf"}
        valid_signature = head.startswith(b"%PDF-")
    elif extension == "docx":
        kind, expected = "document", {_DOCX_MIME, "application/zip"}
        valid_signature = head.startswith(b"PK\x03\x04")
    elif extension == "xlsx":
        kind, expected = "spreadsheet", {_XLSX_MIME, "application/zip"}
        valid_signature = head.startswith(b"PK\x03\x04")
    elif extension == "csv":
        kind, expected = "spreadsheet", {"text/csv", "text/plain", "application/csv"}
        valid_signature = b"\x00" not in head
    elif extension in {"txt", "md", "markdown", "log"}:
        kind, expected = "text", {"text/plain", "text/markdown", "text/x-log"}
        valid_signature = b"\x00" not in head
    elif extension in _CODE_EXTENSIONS:
        kind, expected = "code", _CODE_MIME_TYPES
        valid_signature = b"\x00" not in head
    else:
        raise UploadDetectionError("unsupported_attachment_type", stage="extension")

    if not valid_signature:
        raise UploadDetectionError("unsupported_attachment_type", stage="signature")
    if media_type not in expected:
        raise UploadDetectionError("attachment_mime_mismatch", stage="mime")
    return DetectedUpload(extension=extension, kind=kind, media_type=media_type)
