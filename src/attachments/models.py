"""Typed contracts shared by attachment storage, parsers, API, and Agent tools."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Literal

AttachmentKind = Literal["image", "pdf", "document", "spreadsheet", "text", "code"]
AttachmentStatus = Literal[
    "validating", "parsing", "ready", "rejected", "deleted", "expired"
]
ParseCoverage = Literal["full", "partial", "empty"]


class AttachmentError(RuntimeError):
    """Stable attachment failure that can be safely mapped at API/tool boundaries."""

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        super().__init__(message or code)


@dataclass(frozen=True)
class AttachmentLimits:
    ttl_seconds: int = 86400
    max_files: int = 5
    max_batch_bytes: int = 50 * 1024 * 1024
    image_max_bytes: int = 10 * 1024 * 1024
    image_max_pixels: int = 25_000_000
    office_max_bytes: int = 25 * 1024 * 1024
    text_max_bytes: int = 5 * 1024 * 1024
    pdf_max_pages: int = 100
    spreadsheet_max_sheets: int = 20
    spreadsheet_max_nonempty_cells: int = 100_000
    parse_timeout_seconds: int = 30
    storage_capacity_bytes: int = 5 * 1024 * 1024 * 1024
    storage_reject_ratio: float = 0.9


@dataclass(frozen=True)
class AttachmentLocator:
    page: int | None = None
    heading: str | None = None
    paragraph_range: str | None = None
    table_index: int | None = None
    sheet: str | None = None
    cell_range: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    region_label: str | None = None

    def as_dict(self) -> dict:
        return {key: value for key, value in asdict(self).items() if value is not None}

    @classmethod
    def from_dict(cls, value: dict) -> AttachmentLocator:
        return cls(**{key: item for key, item in value.items() if key in cls.__dataclass_fields__})


@dataclass(frozen=True)
class AttachmentChunk:
    chunk_id: str
    source_id: str
    locator: AttachmentLocator
    text: str
    content_sha256: str
    token_estimate: int

    def as_dict(self) -> dict:
        result = asdict(self)
        result["locator"] = self.locator.as_dict()
        return result

    @classmethod
    def from_dict(cls, value: dict) -> AttachmentChunk:
        return cls(
            chunk_id=str(value["chunk_id"]),
            source_id=str(value["source_id"]),
            locator=AttachmentLocator.from_dict(value.get("locator") or {}),
            text=str(value.get("text") or ""),
            content_sha256=str(value.get("content_sha256") or ""),
            token_estimate=int(value.get("token_estimate") or 0),
        )


@dataclass(frozen=True)
class AttachmentManifest:
    attachment_id: str
    source_id: str
    display_name: str
    kind: AttachmentKind
    media_type: str
    size_bytes: int
    sha256: str
    created_at: datetime
    expires_at: datetime
    status: AttachmentStatus
    parse_coverage: ParseCoverage
    failure_code: str | None = None
    parser_name: str = ""
    parser_version: str = ""
    bound_session_id: str | None = None
    owner_subject_id: str | None = None
    warnings: tuple[str, ...] = ()
    normalized_media_type: str | None = None
    normalized_size_bytes: int | None = None
    normalized_width: int | None = None
    normalized_height: int | None = None
    archive_protected_until: datetime | None = None
    archive_thumbnail_status: Literal[
        "not_applicable", "pending", "ready", "unavailable"
    ] = "not_applicable"
    archive_thumbnail_media_type: str | None = None
    archive_thumbnail_size_bytes: int | None = None
    archive_thumbnail_sha256: str | None = None

    def as_dict(self) -> dict:
        value = asdict(self)
        value["created_at"] = self.created_at.isoformat()
        value["expires_at"] = self.expires_at.isoformat()
        if self.archive_protected_until is not None:
            value["archive_protected_until"] = self.archive_protected_until.isoformat()
        value["warnings"] = list(self.warnings)
        for key in (
            "normalized_media_type",
            "normalized_size_bytes",
            "normalized_width",
            "normalized_height",
        ):
            if value[key] is None:
                del value[key]
        return value

    @classmethod
    def from_dict(cls, value: dict) -> AttachmentManifest:
        return cls(
            attachment_id=str(value["attachment_id"]),
            source_id=str(value["source_id"]),
            display_name=str(value["display_name"]),
            kind=value["kind"],
            media_type=str(value["media_type"]),
            size_bytes=int(value["size_bytes"]),
            sha256=str(value["sha256"]),
            created_at=datetime.fromisoformat(value["created_at"]),
            expires_at=datetime.fromisoformat(value["expires_at"]),
            status=value["status"],
            parse_coverage=value.get("parse_coverage", "empty"),
            failure_code=value.get("failure_code"),
            parser_name=str(value.get("parser_name") or ""),
            parser_version=str(value.get("parser_version") or ""),
            bound_session_id=value.get("bound_session_id"),
            owner_subject_id=(
                value.get("owner_subject_id")
                if value.get("owner_subject_id") is not None
                else value.get("owner_internal_user_id")
            ),
            warnings=tuple(str(item) for item in value.get("warnings") or ()),
            normalized_media_type=value.get("normalized_media_type"),
            normalized_size_bytes=(
                int(value["normalized_size_bytes"])
                if value.get("normalized_size_bytes") is not None
                else None
            ),
            normalized_width=(
                int(value["normalized_width"])
                if value.get("normalized_width") is not None
                else None
            ),
            normalized_height=(
                int(value["normalized_height"])
                if value.get("normalized_height") is not None
                else None
            ),
            archive_protected_until=(
                datetime.fromisoformat(value["archive_protected_until"])
                if value.get("archive_protected_until")
                else None
            ),
            archive_thumbnail_status=value.get(
                "archive_thumbnail_status", "not_applicable"
            ),
            archive_thumbnail_media_type=value.get("archive_thumbnail_media_type"),
            archive_thumbnail_size_bytes=value.get("archive_thumbnail_size_bytes"),
            archive_thumbnail_sha256=value.get("archive_thumbnail_sha256"),
        )


@dataclass(frozen=True)
class AttachmentDescriptor:
    attachment_id: str
    source_id: str
    display_name: str
    kind: AttachmentKind
    status: AttachmentStatus
    parse_coverage: ParseCoverage
    bound_session_id: str | None
    owner_subject_id: str | None = None
    normalized_media_type: str | None = None

    @classmethod
    def from_manifest(cls, manifest: AttachmentManifest) -> AttachmentDescriptor:
        return cls(
            attachment_id=manifest.attachment_id,
            source_id=manifest.source_id,
            display_name=manifest.display_name,
            kind=manifest.kind,
            status=manifest.status,
            parse_coverage=manifest.parse_coverage,
            bound_session_id=manifest.bound_session_id,
            owner_subject_id=manifest.owner_subject_id,
            normalized_media_type=manifest.normalized_media_type,
        )


@dataclass(frozen=True)
class AttachmentGCResult:
    deleted_count: int
    failed_count: int


@dataclass(frozen=True)
class AttachmentStorageStatus:
    ready: bool
    used_bytes: int
    capacity_bytes: int
