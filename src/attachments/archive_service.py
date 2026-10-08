"""Application service for preparing protected attachment archive handoffs."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Callable, Literal
from uuid import UUID

from src.attachments.archive_models import AttachmentTurnInput
from src.attachments.archive_repository import (
    ArchivePage,
    AttachmentArchiveError,
    AttachmentArchiveRepository,
)
from src.attachments.models import AttachmentManifest
from src.attachments.store import AttachmentStore


class AttachmentArchiveService:
    def __init__(
        self,
        store: AttachmentStore,
        *,
        enabled: bool,
        handoff_seconds: int,
        repository: AttachmentArchiveRepository | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self.enabled = enabled
        self.handoff_seconds = handoff_seconds
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _authority(self) -> AttachmentArchiveRepository:
        if self._repository is None:
            raise AttachmentArchiveError("archive_repository_unavailable")
        return self._repository

    def list_pending(self, *, limit: int, cursor: str | None) -> ArchivePage:
        return self._authority().list_pending(
            limit=limit,
            cursor=cursor,
            now=self._clock(),
        )

    def read(
        self,
        relation_id: UUID,
        variant: Literal["original", "thumbnail"],
    ) -> bytes:
        grant = self._authority().get_read_grant(
            relation_id,
            variant,
            now=self._clock(),
        )
        content = self._store.read_archive_variant(
            grant.attachment_id,
            variant,
            now=self._clock(),
        )
        if (
            len(content) != grant.size_bytes
            or hashlib.sha256(content).hexdigest() != grant.sha256
        ):
            raise AttachmentArchiveError("archive_content_mismatch")
        self._authority().confirm_readable(
            relation_id,
            variant,
            expected_sha256=grant.sha256,
            now=self._clock(),
        )
        return content

    def ack_archived(
        self,
        *,
        relation_id: UUID,
        sha256: str,
        platform_attachment_id: UUID,
        archived_at: datetime,
    ) -> None:
        self._authority().ack_archived(
            relation_id=relation_id,
            sha256=sha256,
            platform_attachment_id=platform_attachment_id,
            archived_at=archived_at,
            now=self._clock(),
        )

    def ack_failed(self, relation_id: UUID, error_code: str) -> None:
        self._authority().ack_failed(relation_id, error_code)

    def ack_deleted(self, relation_id: UUID, platform_attachment_id: UUID) -> None:
        self._authority().ack_deleted(relation_id, platform_attachment_id)

    def delete(self, attachment_id: str) -> None:
        # Disabling new archive handoffs cannot erase a prior Platform copy.
        if self._repository is not None:
            self._repository.request_deletion(attachment_id)
        elif self.enabled:
            raise AttachmentArchiveError("archive_repository_unavailable")
        self._store.delete(attachment_id)

    @property
    def has_deletion_authority(self) -> bool:
        return self._repository is not None

    def delete_for_owner(self, attachment_id: str, owner_subject_id: str) -> bool:
        """Delete an archived copy when its temporary local manifest is gone."""
        if self._repository is None:
            return False
        repository = self._repository
        if repository.owner_for_attachment(attachment_id) != owner_subject_id:
            return False
        repository.request_deletion(attachment_id)
        self._store.delete(attachment_id)
        return True

    def prepare_turn(
        self,
        attachment_ids: list[str],
        *,
        explicit_attachment_ids: list[str],
        answer_at: datetime,
    ) -> tuple[AttachmentTurnInput, ...]:
        if not self.enabled or not attachment_ids:
            return ()
        handoff_deadline = answer_at + timedelta(seconds=self.handoff_seconds)
        manifests = [self._store.get(attachment_id) for attachment_id in attachment_ids]
        archive_status = "pending"
        archive_error = ""
        try:
            manifests = self._store.protect_for_archive(
                attachment_ids,
                handoff_deadline,
            )
        except Exception:
            archive_status = "failed"
            archive_error = "local_protection_failed"
        return _relations(
            manifests,
            explicit_attachment_ids=explicit_attachment_ids,
            handoff_deadline=handoff_deadline,
            archive_status=archive_status,
            archive_error=archive_error,
        )


def _relations(
    manifests: list[AttachmentManifest],
    *,
    explicit_attachment_ids: list[str],
    handoff_deadline: datetime,
    archive_status: str,
    archive_error: str,
) -> tuple[AttachmentTurnInput, ...]:
    explicit = set(explicit_attachment_ids)
    return tuple(
        AttachmentTurnInput(
            attachment_id=manifest.attachment_id,
            source_id=manifest.source_id,
            direction="user_input",
            ordinal=ordinal,
            association_kind=(
                "explicit_current_turn"
                if manifest.attachment_id in explicit
                else "session_context"
            ),
            display_name=manifest.display_name,
            kind=manifest.kind,
            media_type=manifest.media_type,
            size_bytes=manifest.size_bytes,
            sha256=manifest.sha256,
            created_at=manifest.created_at,
            processing_expires_at=manifest.expires_at,
            handoff_deadline_at=handoff_deadline,
            thumbnail_status=manifest.archive_thumbnail_status,
            thumbnail_media_type=manifest.archive_thumbnail_media_type,
            thumbnail_size_bytes=manifest.archive_thumbnail_size_bytes,
            thumbnail_sha256=manifest.archive_thumbnail_sha256,
            initial_archive_status=archive_status,
            initial_archive_error=archive_error,
        )
        for ordinal, manifest in enumerate(manifests)
    )
