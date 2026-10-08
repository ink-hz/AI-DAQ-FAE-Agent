"""PostgreSQL authority for attachment archive queue and acknowledgements."""
from __future__ import annotations

import base64
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from src.attachments.archive_models import (
    ArchiveDeleteManifest,
    ArchiveManifest,
    AttachmentTurnInput,
)

_FAILURE_CODES = {
    "content_missing",
    "checksum_mismatch",
    "size_mismatch",
    "unsupported_media_type",
    "platform_store_unavailable",
    "platform_commit_failed",
    "local_protection_failed",
}


class AttachmentArchiveError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ArchivePage:
    items: tuple[ArchiveManifest | ArchiveDeleteManifest, ...]
    next_cursor: str | None


@dataclass(frozen=True)
class ArchiveReadGrant:
    relation_id: UUID
    attachment_id: str
    variant: Literal["original", "thumbnail"]
    media_type: str
    size_bytes: int
    sha256: str
    handoff_deadline_at: datetime


def _cursor_encode(created_at: datetime, relation_id: UUID) -> str:
    raw = json.dumps(
        [created_at.isoformat(), str(relation_id)], separators=(",", ":")
    ).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _cursor_decode(value: str) -> tuple[datetime, UUID]:
    try:
        padded = value + "=" * (-len(value) % 4)
        created_at, relation_id = json.loads(
            base64.urlsafe_b64decode(padded).decode()
        )
        return datetime.fromisoformat(created_at), UUID(relation_id)
    except Exception as exc:
        raise ValueError("archive_cursor_invalid") from exc


def _relation(row: dict) -> AttachmentTurnInput:
    return AttachmentTurnInput(
        attachment_id=str(row["attachment_id"]),
        source_id=str(row["source_id"]),
        direction=row["direction"],
        ordinal=int(row["ordinal"]),
        association_kind=row["association_kind"],
        display_name=str(row["display_name"]),
        kind=row["kind"],
        media_type=str(row["media_type"]),
        size_bytes=int(row["size_bytes"]),
        sha256=str(row["sha256"]),
        created_at=row["created_at"],
        processing_expires_at=row["processing_expires_at"],
        handoff_deadline_at=row["handoff_deadline_at"],
        thumbnail_status=row["thumbnail_status"],
        thumbnail_media_type=row.get("thumbnail_media_type"),
        thumbnail_size_bytes=row.get("thumbnail_size_bytes"),
        thumbnail_sha256=row.get("thumbnail_sha256"),
        initial_archive_status=(
            row["archive_status"]
            if row["archive_status"] in {"pending", "failed"}
            else "pending"
        ),
        initial_archive_error=(
            str(row.get("last_archive_error") or "")
            if row["archive_status"] == "failed"
            else ""
        ),
    )


class AttachmentArchiveRepository:
    def __init__(
        self,
        database_url: str,
        *,
        connect: Callable = psycopg.connect,
        agent_id: str = "ai-fae-agent",
    ) -> None:
        self._database_url = database_url
        self._connect = connect
        self._agent_id = agent_id

    def _agent_scope(self) -> tuple[str, tuple[str, ...]]:
        # Legacy camera rows predate agent_id metadata. Both readers must
        # exclude rows belonging to another agent if a database is miswired.
        if self._agent_id == "ai-fae-agent":
            return ("exists (select 1 from chat_turns scoped_turn "
                    "where scoped_turn.id = chat_turn_attachments.turn_id "
                    "and coalesce(scoped_turn.metadata->>'agent_id', 'ai-fae-agent') "
                    "= 'ai-fae-agent')", ())
        return ("exists (select 1 from chat_turns scoped_turn "
                "where scoped_turn.id = chat_turn_attachments.turn_id "
                "and scoped_turn.metadata->>'agent_id' = %s)", (self._agent_id,))

    def _connection(self):
        return self._connect(
            self._database_url,
            row_factory=dict_row,
            connect_timeout=3,
        )

    def list_pending(
        self,
        *,
        limit: int,
        cursor: str | None,
        now: datetime,
    ) -> ArchivePage:
        if not 1 <= limit <= 100:
            raise ValueError("archive_limit_invalid")
        cursor_value = _cursor_decode(cursor) if cursor else None
        params: list[object] = [now]
        scope, scope_params = self._agent_scope()
        scope_clause = f"and {scope}" if scope else ""
        params.extend(scope_params)
        after = ""
        if cursor_value:
            after = "and (created_at, id) > (%s, %s)"
            params.extend(cursor_value)
        params.append(limit)
        with self._connection() as connection, connection.cursor() as db:
            rows = db.execute(
                f"""
                select * from chat_turn_attachments
                where (
                    (
                        archive_status in ('pending', 'failed')
                        and handoff_deadline_at > %s
                    ) or archive_status = 'deletion_pending'
                )
                  {scope_clause}
                  {after}
                order by created_at, id
                limit %s
                """,
                tuple(params),
            ).fetchall()
        items = tuple(_manifest(row, agent_id=self._agent_id) for row in rows)
        next_cursor = (
            _cursor_encode(rows[-1]["created_at"], rows[-1]["id"])
            if rows
            else None
        )
        return ArchivePage(items=items, next_cursor=next_cursor)

    def health(self) -> dict[str, int]:
        scope, scope_params = self._agent_scope()
        scope_clause = f"where {scope}" if scope else ""
        with self._connection() as connection, connection.cursor() as db:
            row = db.execute(
                f"""
                select
                    count(*) filter (where archive_status = 'pending') as pending,
                    count(*) filter (where archive_status = 'failed') as failed,
                    coalesce(extract(epoch from (
                        now() - min(created_at) filter (
                            where archive_status in ('pending', 'failed')
                        )
                    ))::bigint, 0) as oldest_pending_seconds,
                    count(*) filter (
                        where archive_status = 'expired_unarchived'
                    ) as expired_unarchived_total
                from chat_turn_attachments
                {scope_clause}
                """,
                scope_params,
            ).fetchone()
        return {
            "pending": int(row["pending"]),
            "failed": int(row["failed"]),
            "oldest_pending_seconds": max(0, int(row["oldest_pending_seconds"])),
            "expired_unarchived_total": int(row["expired_unarchived_total"]),
        }

    def get_read_grant(
        self,
        relation_id: UUID,
        variant: Literal["original", "thumbnail"],
        *,
        now: datetime,
    ) -> ArchiveReadGrant:
        row = self._locked(relation_id)
        if row["archive_status"] not in {"pending", "failed"}:
            raise AttachmentArchiveError("archive_relation_not_readable")
        if row["handoff_deadline_at"] <= now:
            raise AttachmentArchiveError("archive_handoff_expired")
        if variant == "original":
            media_type = row["media_type"]
            size_bytes = row["size_bytes"]
            sha256 = row["sha256"]
        elif variant == "thumbnail" and row["thumbnail_status"] == "ready":
            media_type = row["thumbnail_media_type"]
            size_bytes = row["thumbnail_size_bytes"]
            sha256 = row["thumbnail_sha256"]
        else:
            raise AttachmentArchiveError("archive_thumbnail_unavailable")
        return ArchiveReadGrant(
            relation_id=relation_id,
            attachment_id=str(row["attachment_id"]),
            variant=variant,
            media_type=str(media_type),
            size_bytes=int(size_bytes),
            sha256=str(sha256),
            handoff_deadline_at=row["handoff_deadline_at"],
        )

    def confirm_readable(
        self,
        relation_id: UUID,
        variant: Literal["original", "thumbnail"],
        *,
        expected_sha256: str,
        now: datetime,
    ) -> None:
        grant = self.get_read_grant(relation_id, variant, now=now)
        if grant.sha256 != expected_sha256:
            raise AttachmentArchiveError("archive_content_mismatch")

    def ack_archived(
        self,
        *,
        relation_id: UUID,
        sha256: str,
        platform_attachment_id: UUID,
        archived_at: datetime,
        now: datetime,
    ) -> None:
        with self._connection() as connection, connection.cursor() as db:
            row = self._locked(relation_id, cursor=db)
            if platform_attachment_id.int == 0:
                raise AttachmentArchiveError("archive_ack_conflict")
            if row["archive_status"] == "archived":
                if (
                    str(row["sha256"]) == sha256
                    and row["platform_attachment_id"] == platform_attachment_id
                    and row["archived_at"] == archived_at
                ):
                    return
                raise AttachmentArchiveError("archive_ack_conflict")
            deletion_requested = row["archive_status"] in {"deletion_pending", "deleted"}
            if deletion_requested:
                existing = row["platform_attachment_id"] or UUID(int=0)
                if existing == platform_attachment_id:
                    if row["sha256"] == sha256 and row["archived_at"] == archived_at:
                        return
                    raise AttachmentArchiveError("archive_ack_conflict")
                if existing.int != 0 or row["archived_at"] is not None:
                    raise AttachmentArchiveError("archive_ack_conflict")
            elif row["archive_status"] not in {"pending", "failed"}:
                raise AttachmentArchiveError("archive_ack_conflict")
            if row["sha256"] != sha256:
                raise AttachmentArchiveError("archive_checksum_mismatch")
            if archived_at < row["created_at"] or archived_at > now + timedelta(minutes=5):
                raise AttachmentArchiveError("archive_ack_time_invalid")
            db.execute(
                """
                update chat_turn_attachments
                set archive_status = %s, platform_attachment_id = %s,
                    archived_at = %s, last_archive_error = '', updated_at = now()
                where id = %s
                """,
                ("deletion_pending" if deletion_requested else "archived",
                 platform_attachment_id, archived_at, relation_id),
            )

    def ack_failed(self, relation_id: UUID, error_code: str) -> None:
        if error_code not in _FAILURE_CODES:
            raise ValueError("archive_failure_code_invalid")
        with self._connection() as connection, connection.cursor() as db:
            row = self._locked(relation_id, cursor=db)
            if row["archive_status"] not in {"pending", "failed"}:
                raise AttachmentArchiveError("archive_ack_conflict")
            db.execute(
                """
                update chat_turn_attachments
                set archive_status = 'failed',
                    archive_attempt_count = archive_attempt_count + 1,
                    last_archive_error = %s, updated_at = now()
                where id = %s
                """,
                (error_code, relation_id),
            )

    def ack_deleted(self, relation_id: UUID, platform_attachment_id: UUID) -> None:
        with self._connection() as connection, connection.cursor() as db:
            row = self._locked(relation_id, cursor=db)
            if row["archive_status"] == "deleted":
                existing = row["platform_attachment_id"] or UUID(int=0)
                if existing == platform_attachment_id:
                    return
                raise AttachmentArchiveError("archive_delete_conflict")
            if row["archive_status"] != "deletion_pending":
                raise AttachmentArchiveError("archive_delete_conflict")
            expected = row["platform_attachment_id"] or UUID(int=0)
            if expected != platform_attachment_id:
                raise AttachmentArchiveError("archive_delete_conflict")
            db.execute(
                """
                update chat_turn_attachments
                set archive_status = 'deleted', updated_at = now()
                where id = %s
                """,
                (relation_id,),
            )

    def request_deletion(self, attachment_id: str) -> None:
        scope, scope_params = self._agent_scope()
        scope_clause = f"and {scope}" if scope else ""
        with self._connection() as connection, connection.cursor() as db:
            db.execute(
                f"""
                update chat_turn_attachments
                set archive_status = 'deletion_pending',
                    platform_attachment_id = coalesce(
                        platform_attachment_id,
                        '00000000-0000-0000-0000-000000000000'::uuid
                    ),
                    updated_at = now()
                where attachment_id = %s
                  and archive_status in ('pending', 'failed', 'archived')
                  {scope_clause}
                """,
                (attachment_id, *scope_params),
            )

    def owner_for_attachment(self, attachment_id: str) -> str | None:
        """Find the immutable conversation owner after local bytes have expired."""
        scope, scope_params = self._agent_scope()
        scope_clause = f"and {scope}" if scope else ""
        with self._connection() as connection, connection.cursor() as db:
            rows = db.execute(
                f"""
                select distinct session.owner_subject_id
                from chat_turn_attachments
                join chat_turns turn_row on turn_row.id = chat_turn_attachments.turn_id
                join chat_sessions session on session.id = turn_row.session_id
                where chat_turn_attachments.attachment_id = %s
                  {scope_clause}
                """,
                (attachment_id, *scope_params),
            ).fetchall()
        if len(rows) > 1:
            raise AttachmentArchiveError("archive_attachment_owner_conflict")
        if not rows or rows[0]["owner_subject_id"] is None:
            return None
        return str(rows[0]["owner_subject_id"])

    def all_relations_released(self, attachment_id: str) -> bool:
        scope, scope_params = self._agent_scope()
        scope_clause = f"and {scope}" if scope else ""
        with self._connection() as connection, connection.cursor() as db:
            row = db.execute(
                f"""
                select count(*) as total,
                       count(*) filter (
                           where archive_status in (
                               'pending', 'failed', 'deletion_pending'
                           )
                       ) as active
                from chat_turn_attachments
                where attachment_id = %s
                  {scope_clause}
                """,
                (attachment_id, *scope_params),
            ).fetchone()
        return bool(row) and int(row["total"]) > 0 and int(row["active"]) == 0

    def expire_unarchived(self, attachment_id: str, now: datetime) -> None:
        scope, scope_params = self._agent_scope()
        scope_clause = f"and {scope}" if scope else ""
        with self._connection() as connection, connection.cursor() as db:
            db.execute(
                f"""
                update chat_turn_attachments
                set archive_status = 'expired_unarchived', updated_at = %s
                where attachment_id = %s
                  and archive_status in ('pending', 'failed')
                  and handoff_deadline_at <= %s
                  {scope_clause}
                """,
                (now, attachment_id, now, *scope_params),
            )

    def _locked(self, relation_id: UUID, *, cursor=None) -> dict:
        scope, scope_params = self._agent_scope()
        scope_clause = f"and {scope}" if scope else ""
        if cursor is None:
            with self._connection() as connection, connection.cursor() as db:
                row = db.execute(
                    f"select * from chat_turn_attachments where id = %s {scope_clause} for update",
                    (relation_id, *scope_params),
                ).fetchone()
        else:
            row = cursor.execute(
                f"select * from chat_turn_attachments where id = %s {scope_clause} for update",
                (relation_id, *scope_params),
            ).fetchone()
        if row is None:
            raise AttachmentArchiveError("archive_relation_not_found")
        return row


def _manifest(row: dict, *, agent_id: str = "ai-fae-agent") -> ArchiveManifest | ArchiveDeleteManifest:
    if row["archive_status"] == "deletion_pending":
        platform_attachment_id = row.get("platform_attachment_id") or UUID(int=0)
        return ArchiveDeleteManifest(
            relation_id=str(row["id"]),
            native_turn_id=str(row["turn_id"]),
            platform_attachment_id=str(platform_attachment_id),
            requested_at=row["updated_at"],
            agent_id=agent_id,
        )
    return ArchiveManifest.from_relation(
        relation_id=str(row["id"]),
        native_turn_id=str(row["turn_id"]),
        external_session_id=str(row["external_session_id"]),
        trace_id=str(row["trace_id"]),
        relation=_relation(row),
        agent_id=agent_id,
    )
