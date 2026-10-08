"""Typed, path-free contracts for attachment archive handoff."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from src.attachments.models import AttachmentKind

ArchiveStatus = Literal[
    "pending",
    "failed",
    "archived",
    "deletion_pending",
    "expired_unarchived",
    "deleted",
]
AssociationKind = Literal["explicit_current_turn", "session_context"]
ArchiveDirection = Literal["user_input", "agent_output"]
ThumbnailStatus = Literal["not_applicable", "pending", "ready", "unavailable"]
ArchiveVariantName = Literal["original", "thumbnail"]
ArchiveAckStatus = Literal["archived", "failed", "deleted"]

_HASH = re.compile(r"^[0-9a-f]{64}$")
_DIRECTIONS = {"user_input", "agent_output"}
_ASSOCIATIONS = {"explicit_current_turn", "session_context"}
_KINDS = {"image", "pdf", "document", "spreadsheet", "text", "code"}
_THUMBNAIL_STATES = {"not_applicable", "pending", "ready", "unavailable"}
_INITIAL_ARCHIVE_STATES = {"pending", "failed"}


def _aware(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


def _rfc3339(value: datetime) -> str:
    _aware(value, "datetime")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _uuid(value: str, field: str) -> str:
    try:
        return str(UUID(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{field} must be a UUID") from exc


@dataclass(frozen=True)
class ArchiveVariant:
    name: ArchiveVariantName
    media_type: str
    size_bytes: int
    sha256: str

    def __post_init__(self) -> None:
        if self.name not in {"original", "thumbnail"}:
            raise ValueError("variant name is invalid")
        if not self.media_type:
            raise ValueError("variant media_type is required")
        if self.size_bytes < 0:
            raise ValueError("variant size_bytes must be non-negative")
        if not _HASH.fullmatch(self.sha256):
            raise ValueError("variant sha256 must be lowercase hex")

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "media_type": self.media_type,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class AttachmentTurnInput:
    attachment_id: str
    source_id: str
    direction: ArchiveDirection
    ordinal: int
    association_kind: AssociationKind
    display_name: str
    kind: AttachmentKind
    media_type: str
    size_bytes: int
    sha256: str
    created_at: datetime
    processing_expires_at: datetime
    handoff_deadline_at: datetime
    thumbnail_status: ThumbnailStatus
    thumbnail_media_type: str | None = None
    thumbnail_size_bytes: int | None = None
    thumbnail_sha256: str | None = None
    initial_archive_status: Literal["pending", "failed"] = "pending"
    initial_archive_error: str = ""

    def __post_init__(self) -> None:
        if not self.attachment_id:
            raise ValueError("attachment_id is required")
        if not self.source_id:
            raise ValueError("source_id is required")
        if self.direction not in _DIRECTIONS:
            raise ValueError("direction is invalid")
        if self.ordinal < 0:
            raise ValueError("ordinal must be non-negative")
        if self.association_kind not in _ASSOCIATIONS:
            raise ValueError("association_kind is invalid")
        if self.kind not in _KINDS:
            raise ValueError("kind is invalid")
        if not self.display_name or not self.media_type:
            raise ValueError("display_name and media_type are required")
        if self.size_bytes < 0:
            raise ValueError("size_bytes must be non-negative")
        if not _HASH.fullmatch(self.sha256):
            raise ValueError("sha256 must be 64 lowercase hex characters")
        for field in ("created_at", "processing_expires_at", "handoff_deadline_at"):
            _aware(getattr(self, field), field)
        if self.processing_expires_at < self.created_at:
            raise ValueError("processing expiry precedes creation")
        if self.handoff_deadline_at < self.processing_expires_at:
            raise ValueError("handoff deadline precedes processing expiry")
        if self.thumbnail_status not in _THUMBNAIL_STATES:
            raise ValueError("thumbnail_status is invalid")
        if self.thumbnail_status == "ready":
            if self.thumbnail_media_type != "image/webp":
                raise ValueError("ready thumbnail must use image/webp")
            if self.thumbnail_size_bytes is None or self.thumbnail_size_bytes < 0:
                raise ValueError("ready thumbnail size is required")
            if self.thumbnail_sha256 is None or not _HASH.fullmatch(
                self.thumbnail_sha256
            ):
                raise ValueError("ready thumbnail sha256 is invalid")
        elif any(
            value is not None
            for value in (
                self.thumbnail_media_type,
                self.thumbnail_size_bytes,
                self.thumbnail_sha256,
            )
        ):
            raise ValueError("non-ready thumbnail metadata must be empty")
        if self.initial_archive_status not in _INITIAL_ARCHIVE_STATES:
            raise ValueError("initial_archive_status is invalid")
        if self.initial_archive_status == "pending" and self.initial_archive_error:
            raise ValueError("pending archive cannot have an initial error")


@dataclass(frozen=True)
class ArchiveManifest:
    relation_id: str
    native_turn_id: str
    external_session_id: str
    trace_id: str
    direction: ArchiveDirection
    ordinal: int
    association_kind: AssociationKind
    display_name: str
    kind: AttachmentKind
    media_type: str
    size_bytes: int
    sha256: str
    created_at: datetime
    processing_expires_at: datetime
    handoff_deadline_at: datetime
    variants: tuple[ArchiveVariant, ...]
    agent_id: str = "ai-fae-agent"

    @classmethod
    def from_relation(
        cls,
        *,
        relation_id: str,
        native_turn_id: str,
        external_session_id: str,
        trace_id: str,
        relation: AttachmentTurnInput,
        agent_id: str = "ai-fae-agent",
    ) -> ArchiveManifest:
        variants = [ArchiveVariant(
            name="original",
            media_type=relation.media_type,
            size_bytes=relation.size_bytes,
            sha256=relation.sha256,
        )]
        if relation.thumbnail_status == "ready":
            variants.append(ArchiveVariant(
                name="thumbnail",
                media_type=relation.thumbnail_media_type or "",
                size_bytes=relation.thumbnail_size_bytes or 0,
                sha256=relation.thumbnail_sha256 or "",
            ))
        return cls(
            relation_id=_uuid(relation_id, "relation_id"),
            native_turn_id=_uuid(native_turn_id, "native_turn_id"),
            external_session_id=external_session_id,
            trace_id=trace_id,
            direction=relation.direction,
            ordinal=relation.ordinal,
            association_kind=relation.association_kind,
            display_name=relation.display_name,
            kind=relation.kind,
            media_type=relation.media_type,
            size_bytes=relation.size_bytes,
            sha256=relation.sha256,
            created_at=relation.created_at,
            processing_expires_at=relation.processing_expires_at,
            handoff_deadline_at=relation.handoff_deadline_at,
            variants=tuple(variants),
            agent_id=agent_id,
        )

    def as_dict(self) -> dict:
        return {
            "schema_version": "fae-attachment-archive/v1",
            "agent_id": self.agent_id,
            "operation": "archive",
            "relation_id": self.relation_id,
            "native_turn_id": self.native_turn_id,
            "external_session_id": self.external_session_id,
            "trace_id": self.trace_id,
            "direction": self.direction,
            "ordinal": self.ordinal,
            "association_kind": self.association_kind,
            "display_name": self.display_name,
            "kind": self.kind,
            "media_type": self.media_type,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "created_at": _rfc3339(self.created_at),
            "processing_expires_at": _rfc3339(self.processing_expires_at),
            "handoff_deadline_at": _rfc3339(self.handoff_deadline_at),
            "variants": [item.as_dict() for item in self.variants],
        }


@dataclass(frozen=True)
class ArchiveDeleteManifest:
    relation_id: str
    native_turn_id: str
    platform_attachment_id: str
    requested_at: datetime
    agent_id: str = "ai-fae-agent"

    def __post_init__(self) -> None:
        _uuid(self.relation_id, "relation_id")
        _uuid(self.native_turn_id, "native_turn_id")
        _uuid(self.platform_attachment_id, "platform_attachment_id")
        _aware(self.requested_at, "requested_at")

    def as_dict(self) -> dict:
        return {
            "schema_version": "fae-attachment-archive/v1",
            "agent_id": self.agent_id,
            "operation": "delete",
            "relation_id": self.relation_id,
            "native_turn_id": self.native_turn_id,
            "platform_attachment_id": self.platform_attachment_id,
            "requested_at": _rfc3339(self.requested_at),
        }


@dataclass(frozen=True)
class ArchiveAck:
    relation_id: str
    status: ArchiveAckStatus
    sha256: str | None = None
    platform_attachment_id: str | None = None
    archived_at: datetime | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        _uuid(self.relation_id, "relation_id")
        if self.status not in {"archived", "failed", "deleted"}:
            raise ValueError("ack status is invalid")
        present = {
            field
            for field, value in {
                "sha256": self.sha256,
                "platform_attachment_id": self.platform_attachment_id,
                "archived_at": self.archived_at,
                "error_code": self.error_code,
            }.items()
            if value is not None
        }
        required = {
            "archived": {"sha256", "platform_attachment_id", "archived_at"},
            "failed": {"error_code"},
            "deleted": {"platform_attachment_id"},
        }[self.status]
        if present != required:
            raise ValueError(f"{self.status} ack shape is invalid")
        if self.sha256 is not None and not _HASH.fullmatch(self.sha256):
            raise ValueError("ack sha256 is invalid")
        if self.platform_attachment_id is not None:
            _uuid(self.platform_attachment_id, "platform_attachment_id")
        if self.archived_at is not None:
            _aware(self.archived_at, "archived_at")
        if self.error_code is not None and not self.error_code:
            raise ValueError("ack error_code is required")
