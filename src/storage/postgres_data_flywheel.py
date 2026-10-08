from __future__ import annotations

import json
import logging
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from src.agent.session import OWNER_SUBJECT_TYPES
from src.attachments.archive_models import AttachmentTurnInput
from src.storage.data_flywheel import (
    ChatTurnRecord,
    DataFlywheelFallbackWriter,
    FeedbackRecord,
    TurnResolutionUnavailable,
)

logger = logging.getLogger(__name__)


def _session_metadata_projection(record: ChatTurnRecord) -> str:
    """Only the authentication mode is projected onto the session row.

    An unknown mode projects nothing, so a forged metadata value can never
    turn a public conversation into an authenticated one.
    """
    mode = record.metadata.get("authentication_mode")
    projected = {"authentication_mode": mode} if mode in OWNER_SUBJECT_TYPES else {}
    return json.dumps(projected, ensure_ascii=False)


def _owner_columns_from_metadata(record: ChatTurnRecord) -> tuple[str | None, str | None]:
    """Owner projection for a directly-written turn (no repository transaction).

    No claimed owner yields `(None, None)`: that is a public/anonymous turn, or
    a rolling-deploy enterprise turn whose owner migration 010's
    `chat_sessions_derive_owner` trigger derives from the row's own trusted
    `user_id`. A partner turn has no such trigger branch, so its owner columns
    can only come from here.

    A claimed owner that is malformed raises instead of degrading to
    `(None, None)`: `chat_sessions_owner_shape` accepts an ownerless row, so
    returning nothing here would silently write an authenticated turn as a
    public one. These two columns are what every later ownership check reads,
    so the owner id must be a UUID and the owner type must match the
    authentication mode the same row projects.
    """
    owner_subject_id = record.metadata.get("owner_subject_id")
    owner_subject_type = record.metadata.get("owner_subject_type")
    if owner_subject_id is None and owner_subject_type is None:
        return None, None
    if not isinstance(owner_subject_id, str) or not owner_subject_id:
        raise ValueError("chat_turn_owner_projection_invalid")
    try:
        owner_subject_id = str(UUID(owner_subject_id))
    except (AttributeError, TypeError, ValueError):
        raise ValueError("chat_turn_owner_projection_invalid") from None
    if OWNER_SUBJECT_TYPES.get(record.metadata.get("authentication_mode")) != (
        owner_subject_type
    ):
        raise ValueError("chat_turn_owner_projection_invalid")
    return owner_subject_id, owner_subject_type


class PostgresDataFlywheelStore:
    def __init__(self, database_url: str, fallback_writer: DataFlywheelFallbackWriter) -> None:
        self._database_url = database_url
        self._fallback_writer = fallback_writer

    def _connect(self):
        return psycopg.connect(self._database_url, row_factory=dict_row)

    def record_chat_turn(
        self,
        record: ChatTurnRecord,
        *,
        attachment_relations: tuple[AttachmentTurnInput, ...] = (),
    ) -> str | None:
        """Record one anonymous or authenticated turn in its own transaction."""
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    return self._record_chat_turn_locked(
                        cur, record, attachment_relations
                    )
        except Exception as exc:
            logger.warning(
                "data_flywheel_write_failed record_type=chat_turn exception_type=%s",
                type(exc).__name__,
            )
            self._fallback_writer.write(
                "chat_turn",
                record,
                error="data_flywheel_write_failed",
                attachment_relations=attachment_relations,
            )
            return None

    @staticmethod
    def _record_chat_turn_locked(
        cur,
        record: ChatTurnRecord,
        attachment_relations: tuple[AttachmentTurnInput, ...] = (),
    ) -> str:
        """Write one turn inside a caller-owned transaction.

        This is the single conversation-turn write path. An authenticated caller
        composes it with the owner projection and the sealed checkpoint in one
        transaction instead of creating a second system of record. It raises on
        failure: the caller owns rollback and fallback policy.
        """
        owner_subject_id, owner_subject_type = _owner_columns_from_metadata(record)
        cur.execute(
            """
            insert into chat_sessions (
                external_session_id, channel, user_id, external_user_id,
                owner_subject_id, owner_subject_type, last_active_at, metadata
            )
            values (%s, %s, %s, %s, %s, %s, now(), %s::jsonb)
            on conflict (external_session_id)
            do update set
                last_active_at = excluded.last_active_at,
                metadata = chat_sessions.metadata || excluded.metadata
            where chat_sessions.user_id is not distinct from excluded.user_id
              and chat_sessions.external_user_id is not distinct from excluded.external_user_id
              and coalesce(
                  chat_sessions.metadata->>'authentication_mode',
                  'public_customer'
              ) = coalesce(
                  excluded.metadata->>'authentication_mode',
                  'public_customer'
              )
            returning id
            """,
            (
                record.external_session_id,
                record.channel,
                record.user_id,
                record.external_user_id,
                owner_subject_id,
                owner_subject_type,
                _session_metadata_projection(record),
            ),
        )
        session_row = cur.fetchone()
        if session_row is None:
            raise RuntimeError("chat_session_identity_conflict")
        session_id = session_row["id"]
        cur.execute(
            """
            insert into chat_turns (
                session_id, external_session_id, turn_index, trace_id, channel,
                question, answer, sources, stages, done, planned_capabilities,
                capability_coverage, fallback_used, fallback_reason, outcome,
                duration_ms, question_at, answer_at, metadata
            )
            values (
                %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb,
                %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s, %s, %s::jsonb
            )
            on conflict (external_session_id, turn_index)
            do update set
                trace_id = excluded.trace_id,
                answer = excluded.answer,
                sources = excluded.sources,
                stages = excluded.stages,
                done = excluded.done,
                planned_capabilities = excluded.planned_capabilities,
                capability_coverage = excluded.capability_coverage,
                fallback_used = excluded.fallback_used,
                fallback_reason = excluded.fallback_reason,
                outcome = excluded.outcome,
                duration_ms = excluded.duration_ms,
                question_at = coalesce(excluded.question_at, chat_turns.question_at),
                answer_at = coalesce(excluded.answer_at, chat_turns.answer_at),
                metadata = excluded.metadata
            returning id
            """,
            (
                session_id,
                record.external_session_id,
                record.turn_index,
                record.trace_id,
                record.channel,
                record.question,
                record.answer,
                json.dumps(record.sources, ensure_ascii=False),
                json.dumps(record.stages, ensure_ascii=False),
                json.dumps(record.done, ensure_ascii=False),
                json.dumps(record.planned_capabilities, ensure_ascii=False),
                json.dumps(record.capability_coverage, ensure_ascii=False),
                record.fallback_used,
                record.fallback_reason,
                record.outcome,
                record.duration_ms,
                record.question_at,
                record.answer_at,
                json.dumps(record.metadata, ensure_ascii=False),
            ),
        )
        turn_id = cur.fetchone()["id"]
        cur.execute(
            """
            select attachment_id, direction, ordinal, sha256, archive_status
            from chat_turn_attachments
            where turn_id = %s
            for update
            """,
            (turn_id,),
        )
        existing = {
            str(row["attachment_id"]): row
            for row in (cur.fetchall() or [])
        }
        desired = {item.attachment_id: item for item in attachment_relations}
        immutable_states = {
            "archived", "deletion_pending", "expired_unarchived", "deleted",
        }
        for attachment_id, row in existing.items():
            if row["archive_status"] not in immutable_states:
                continue
            item = desired.get(attachment_id)
            if item is None or (
                row["direction"] != item.direction
                or int(row["ordinal"]) != item.ordinal
                or row["sha256"] != item.sha256
            ):
                raise RuntimeError("attachment_relation_immutable_conflict")
        stale_attachment_ids = [
            attachment_id
            for attachment_id, row in existing.items()
            if attachment_id not in desired
            and row["archive_status"] in {"pending", "failed"}
        ]
        for attachment_id in stale_attachment_ids:
            cur.execute(
                """
                delete from chat_turn_attachments
                where turn_id = %s and attachment_id = %s
                  and archive_status in ('pending', 'failed')
                """,
                (turn_id, attachment_id),
            )
        for item in attachment_relations:
            row = existing.get(item.attachment_id)
            if row and row["archive_status"] in immutable_states:
                continue
            cur.execute(
                """
                insert into chat_turn_attachments (
                    turn_id, external_session_id, trace_id, attachment_id,
                    source_id, direction, ordinal, association_kind,
                    display_name, kind, media_type, size_bytes, sha256,
                    created_at, processing_expires_at, handoff_deadline_at,
                    archive_status, last_archive_error, thumbnail_status,
                    thumbnail_media_type, thumbnail_size_bytes, thumbnail_sha256
                ) values (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                on conflict (turn_id, attachment_id) do update set
                    trace_id = excluded.trace_id,
                    direction = excluded.direction,
                    ordinal = excluded.ordinal,
                    association_kind = excluded.association_kind,
                    display_name = excluded.display_name,
                    media_type = excluded.media_type,
                    updated_at = now()
                """,
                (
                    turn_id, record.external_session_id, record.trace_id,
                    item.attachment_id, item.source_id, item.direction,
                    item.ordinal, item.association_kind, item.display_name,
                    item.kind, item.media_type, item.size_bytes, item.sha256,
                    item.created_at, item.processing_expires_at,
                    item.handoff_deadline_at, item.initial_archive_status,
                    item.initial_archive_error, item.thumbnail_status,
                    item.thumbnail_media_type, item.thumbnail_size_bytes,
                    item.thumbnail_sha256,
                ),
            )
        return str(turn_id)

    def record_feedback(self, record: FeedbackRecord) -> str | None:
        # The target turn is resolved by the caller, which is the only layer that
        # knows who is asking. Re-resolving here without an ownership scope would
        # re-open the boundary the caller just enforced, so a record that arrives
        # without a turn id is written unlinked rather than silently retargeted.
        turn_id = record.turn_id
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        insert into turn_feedback (
                            turn_id, external_session_id, trace_id, rating, reason_code,
                            comment, channel, user_id, external_user_id, metadata
                        )
                        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                        returning id
                        """,
                        (
                            turn_id,
                            record.external_session_id,
                            record.trace_id,
                            record.rating,
                            record.reason_code,
                            record.comment,
                            record.channel,
                            record.user_id,
                            record.external_user_id,
                            json.dumps(record.metadata, ensure_ascii=False),
                        ),
                    )
                    return str(cur.fetchone()["id"])
        except Exception as exc:
            logger.warning(
                "data_flywheel_write_failed record_type=feedback exception_type=%s",
                type(exc).__name__,
            )
            self._fallback_writer.write(
                "feedback", record, error="data_flywheel_write_failed"
            )
            return None

    def resolve_turn_id(
        self,
        *,
        external_session_id: str,
        message_index: int | None,
        trace_id: str | None = None,
        candidate_turn_id: str | None = None,
        owner_subject_id: str | None = None,
        internal_user_id: str | None = None,
        require_unowned: bool = False,
    ) -> str | None:
        """Resolve a feedback target inside an explicit ownership scope.

        The scope is mandatory and mutually exclusive: either the turn belongs to
        `owner_subject_id`, or it belongs to no subject at all
        (`require_unowned`, the public/anonymous case). Every candidate -- turn
        id, trace id and `(session, turn_index)` alike -- is matched by SQL joined
        to `chat_sessions`, so a caller-supplied identifier can never be accepted
        as authorization on its own, and an anonymous caller can never name an
        authenticated subject's turn.

        Returns `None` only when the query ran and matched nothing. An
        infrastructure failure raises `TurnResolutionUnavailable`: reporting it
        as "no such turn" would let a database outage read as a denial for every
        caller, owner and public alike.
        """
        if (owner_subject_id is None) is not require_unowned:
            raise ValueError("turn_resolution_owner_scope_required")
        conditions = ["turn.external_session_id = %s"]
        params: tuple[object, ...] = (external_session_id,)
        if owner_subject_id is not None:
            conditions.append("session.owner_subject_id = %s::uuid")
            params += (owner_subject_id,)
            if internal_user_id is not None:
                # Enterprise turns carry the internal user id as well. Ownership
                # is decided by the subject id above; this is defense in depth,
                # never the authorization key.
                conditions.append("session.user_id = %s")
                conditions.append(
                    "session.metadata->>'authentication_mode' = 'platform_enterprise'"
                )
                params += (internal_user_id,)
        else:
            conditions.append("session.owner_subject_id is null")
        if candidate_turn_id:
            conditions.append("turn.id::text = %s")
            params += (candidate_turn_id,)
        elif trace_id:
            conditions.append("turn.trace_id = %s")
            params += (trace_id,)
        elif message_index is not None:
            conditions.append("turn.turn_index = %s")
            params += (max(message_index // 2, 0),)
        else:
            return None
        query = (
            "select turn.id from chat_turns turn "
            "join chat_sessions session on session.id = turn.session_id "
            "where " + " and ".join(conditions) + " "
            "order by turn.created_at desc limit 1"
        )
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(query, params)
                    row = cur.fetchone()
                    return str(row["id"]) if row else None
        except Exception as exc:
            # Only the exception type is logged: the message can carry the DSN
            # (credentials included) or a caller-supplied identifier.
            logger.warning(
                "data_flywheel_turn_resolution_unavailable exception_type=%s",
                type(exc).__name__,
            )
            raise TurnResolutionUnavailable("turn_resolution_unavailable") from exc
