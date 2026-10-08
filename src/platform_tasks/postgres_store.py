from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from hashlib import sha256
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row

from src.platform_tasks.crypto import SealedTaskContent, TaskContentCodec
from src.platform_tasks.models import (
    TERMINAL_STATUSES,
    CancelReceipt,
    MessageReceipt,
    PlatformTask,
    PlatformTaskMessage,
    PlatformTaskSpec,
    TaskCreateResult,
    TaskEvent,
    TaskEventKind,
    TaskStoreError,
)


def _json_fingerprint(value: dict[str, object]) -> bytes:
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).digest()


def _task(row: dict) -> PlatformTask:
    return PlatformTask(
        task_id=row["task_id"],
        platform_task_id=row["platform_task_id"],
        status=row["status"],
        cancel_requested=bool(row["cancel_requested"]),
        next_event_seq=int(row["next_event_seq"]),
        next_message_seq=int(row["next_message_seq"]),
        capability_version=int(row["capability_version"]),
        deadline_at=row["deadline_at"],
        requester_internal_user_id=row["requester_internal_user_id"],
        authorized_scopes=tuple(row["authorized_scopes"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        terminal_at=row.get("terminal_at"),
        lease_owner=row.get("lease_owner"),
        lease_expires_at=row.get("lease_expires_at"),
        attempt_count=int(row.get("attempt_count") or 0),
    )


class PostgresPlatformTaskStore:
    def __init__(
        self,
        database_url: str,
        *,
        codec: TaskContentCodec,
        connect: Callable = psycopg.connect,
    ) -> None:
        if not database_url:
            raise ValueError("platform_task_database_url_missing")
        self._database_url = database_url
        self._codec = codec
        self._connect = connect

    def __repr__(self) -> str:
        return "PostgresPlatformTaskStore(database_url=<redacted>)"

    def _connection(self):
        return self._connect(
            self._database_url,
            row_factory=dict_row,
            connect_timeout=3,
        )

    def create_task(self, spec: PlatformTaskSpec, *, now: datetime) -> TaskCreateResult:
        fingerprint = spec.fingerprint()
        task_id = uuid4()
        sealed = self._codec.seal_json(
            f"platform-task:{task_id}:request", spec.protected_payload()
        )
        with self._connection() as connection, connection.cursor() as db:
            row = db.execute(
                """
                insert into platform_tasks (
                    task_id, platform_task_id, idempotency_key, request_sha256,
                    request_ciphertext, request_key_version,
                    requester_internal_user_id, capability_version,
                    authorized_scopes, deadline_at, created_at, updated_at
                ) values (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                on conflict do nothing
                returning *
                """,
                (
                    task_id,
                    spec.platform_task_id,
                    spec.idempotency_key,
                    fingerprint,
                    sealed.ciphertext,
                    sealed.key_version,
                    spec.requester_internal_user_id,
                    spec.capability_version,
                    list(spec.authorized_scopes),
                    spec.deadline_at,
                    now,
                    now,
                ),
            ).fetchone()
            if row is not None:
                return TaskCreateResult(_task(row), False)
            existing = db.execute(
                """
                select * from platform_tasks
                where platform_task_id = %s or idempotency_key = %s
                for update
                """,
                (spec.platform_task_id, spec.idempotency_key),
            ).fetchall()
            if (
                len(existing) != 1
                or existing[0]["platform_task_id"] != spec.platform_task_id
                or existing[0]["idempotency_key"] != spec.idempotency_key
                or bytes(existing[0]["request_sha256"]) != fingerprint
            ):
                raise TaskStoreError("idempotency_conflict")
            return TaskCreateResult(_task(existing[0]), True)

    def get_task(self, task_id: UUID) -> PlatformTask:
        with self._connection() as connection, connection.cursor() as db:
            row = db.execute(
                "select * from platform_tasks where task_id = %s", (task_id,)
            ).fetchone()
        if row is None:
            raise TaskStoreError("task_not_found")
        return _task(row)

    def _claim(
        self,
        *,
        worker_id: str,
        lease_seconds: int,
        now: datetime,
        expired: bool,
    ) -> PlatformTask | None:
        if not worker_id or lease_seconds <= 0:
            raise ValueError("task_lease_invalid")
        deadline_clause = "deadline_at <= %(now)s" if expired else "deadline_at > %(now)s"
        status_clause = (
            "status not in ('completed','failed','cancelled','timed_out')"
            if expired
            else "status in ('queued','running') and cancel_requested = false"
        )
        lease_clause = (
            "true"
            if expired
            else """
                lease_expires_at is null
                or lease_expires_at <= %(now)s
                or lease_owner = %(worker_id)s
            """
        )
        with self._connection() as connection, connection.cursor() as db:
            row = db.execute(
                f"""
                with candidate as (
                    select task_id from platform_tasks
                    where {status_clause}
                      and {deadline_clause}
                      and ({lease_clause})
                    order by created_at, task_id
                    for update skip locked
                    limit 1
                )
                update platform_tasks task
                   set lease_owner = %(worker_id)s,
                       lease_expires_at = %(now)s + (%(lease_seconds)s * interval '1 second'),
                       attempt_count = attempt_count + 1,
                       updated_at = %(now)s
                  from candidate
                 where task.task_id = candidate.task_id
                returning task.*
                """,
                {
                    "worker_id": worker_id,
                    "lease_seconds": lease_seconds,
                    "now": now,
                },
            ).fetchone()
        return None if row is None else _task(row)

    def claim_next_expired_task(
        self, *, worker_id: str, lease_seconds: int, now: datetime
    ) -> PlatformTask | None:
        return self._claim(
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            now=now,
            expired=True,
        )

    def claim_next_task(
        self, *, worker_id: str, lease_seconds: int, now: datetime
    ) -> PlatformTask | None:
        return self._claim(
            worker_id=worker_id,
            lease_seconds=lease_seconds,
            now=now,
            expired=False,
        )

    def renew_task_lease(
        self,
        task_id: UUID,
        *,
        worker_id: str,
        lease_seconds: int,
        now: datetime,
    ) -> PlatformTask:
        with self._connection() as connection, connection.cursor() as db:
            row = db.execute(
                """
                update platform_tasks
                   set lease_expires_at = %s + (%s * interval '1 second'),
                       updated_at = %s
                 where task_id = %s
                   and lease_owner = %s
                   and lease_expires_at > %s
                   and status not in ('completed','failed','cancelled','timed_out')
                returning *
                """,
                (now, lease_seconds, now, task_id, worker_id, now),
            ).fetchone()
        if row is None:
            raise TaskStoreError("task_lease_lost")
        return _task(row)

    def release_task_lease(self, task_id: UUID, *, worker_id: str, now: datetime) -> None:
        with self._connection() as connection, connection.cursor() as db:
            db.execute(
                """
                update platform_tasks
                   set lease_owner = null, lease_expires_at = null,
                       updated_at = greatest(updated_at, %s)
                 where task_id = %s and lease_owner = %s
                """,
                (now, task_id, worker_id),
            )

    def load_request(self, task_id: UUID) -> PlatformTaskSpec:
        with self._connection() as connection, connection.cursor() as db:
            row = db.execute(
                """
                select *, request_ciphertext, request_key_version
                from platform_tasks where task_id = %s
                """,
                (task_id,),
            ).fetchone()
        if row is None:
            raise TaskStoreError("task_not_found")
        payload = self._codec.unseal_json(
            f"platform-task:{task_id}:request",
            SealedTaskContent(bytes(row["request_ciphertext"]), int(row["request_key_version"])),
        )
        return PlatformTaskSpec(
            platform_task_id=row["platform_task_id"],
            conversation_ref=str(payload["conversation_ref"]),
            turn_ref=str(payload["turn_ref"]),
            objective=str(payload["objective"]),
            context_excerpt=tuple(str(value) for value in payload["context_excerpt"]),
            constraints=tuple(str(value) for value in payload["constraints"]),
            attachment_refs=tuple(UUID(str(value)) for value in payload["attachment_refs"]),
            expected_output=str(payload["expected_output"]),
            capability_version=int(row["capability_version"]),
            idempotency_key=str(row["idempotency_key"]),
            deadline_at=row["deadline_at"],
            requester_internal_user_id=row["requester_internal_user_id"],
            authorized_scopes=tuple(row["authorized_scopes"]),
        )

    def append_event(
        self,
        task_id: UUID,
        *,
        kind: TaskEventKind,
        payload: dict[str, object],
        now: datetime,
    ) -> TaskEvent:
        fingerprint = _json_fingerprint(payload)
        with self._connection() as connection, connection.cursor() as db:
            sealed = self._codec.seal_json(
                f"platform-task:{task_id}:event", payload
            )
            try:
                result = db.execute(
                    """
                    select * from append_platform_task_event(%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        task_id,
                        kind,
                        fingerprint,
                        sealed.ciphertext,
                        sealed.key_version,
                        now,
                    ),
                ).fetchone()
            except psycopg.errors.CheckViolation as exc:
                raise TaskStoreError("task_terminal") from exc
            actual_seq = int(result["event_seq"])
            return TaskEvent(task_id, actual_seq, kind, payload, now)

    def events_after(
        self, task_id: UUID, *, after: int, limit: int
    ) -> tuple[TaskEvent, ...]:
        if after < 0 or not 1 <= limit <= 100:
            raise ValueError("event_page_invalid")
        with self._connection() as connection, connection.cursor() as db:
            rows = db.execute(
                """
                select seq, kind, payload_ciphertext, payload_key_version, created_at
                from platform_task_events
                where task_id = %s and seq > %s
                order by seq
                limit %s
                """,
                (task_id, after, limit),
            ).fetchall()
        events = []
        for row in rows:
            seq = int(row["seq"])
            payload = self._codec.unseal_json(
                f"platform-task:{task_id}:event",
                SealedTaskContent(
                    bytes(row["payload_ciphertext"]), int(row["payload_key_version"])
                ),
            )
            events.append(TaskEvent(task_id, seq, row["kind"], payload, row["created_at"]))
        return tuple(events)

    def append_message(
        self,
        task_id: UUID,
        *,
        message_seq: int,
        content: str,
        attachment_refs: tuple[UUID, ...],
        idempotency_key: str,
        now: datetime,
    ) -> MessageReceipt:
        payload = {
            "content": content,
            "attachment_refs": [str(value) for value in attachment_refs],
        }
        fingerprint = _json_fingerprint(payload)
        with self._connection() as connection, connection.cursor() as db:
            existing = db.execute(
                """
                select message_seq, idempotency_key, content_sha256
                from platform_task_messages
                where task_id = %s and (message_seq = %s or idempotency_key = %s)
                for update
                """,
                (task_id, message_seq, idempotency_key),
            ).fetchall()
            if existing:
                if (
                    len(existing) != 1
                    or int(existing[0]["message_seq"]) != message_seq
                    or existing[0]["idempotency_key"] != idempotency_key
                    or bytes(existing[0]["content_sha256"]) != fingerprint
                ):
                    raise TaskStoreError("message_sequence_conflict")
                return MessageReceipt(task_id, message_seq, True)
            task_row = db.execute(
                """
                select status, next_message_seq, next_event_seq from platform_tasks
                where task_id = %s for update
                """,
                (task_id,),
            ).fetchone()
            if task_row is None:
                raise TaskStoreError("task_not_found")
            if task_row["status"] in TERMINAL_STATUSES:
                raise TaskStoreError("task_terminal")
            if int(task_row["next_message_seq"]) != message_seq:
                raise TaskStoreError("message_sequence_conflict")
            sealed = self._codec.seal_json(
                f"platform-task:{task_id}:message:{message_seq}", payload
            )
            db.execute(
                """
                insert into platform_task_messages (
                    task_id, message_seq, idempotency_key, content_sha256,
                    content_ciphertext, content_key_version, created_at
                ) values (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    task_id,
                    message_seq,
                    idempotency_key,
                    fingerprint,
                    sealed.ciphertext,
                    sealed.key_version,
                    now,
                ),
            )
            queued_payload: dict[str, object] = {
                "phase": "message_queued",
                "message_seq": message_seq,
            }
            queued_fingerprint = _json_fingerprint(queued_payload)
            queued = self._codec.seal_json(
                f"platform-task:{task_id}:event", queued_payload
            )
            event_seq = int(task_row["next_event_seq"])
            db.execute(
                """
                insert into platform_task_events (
                    task_id, seq, kind, payload_sha256, payload_ciphertext,
                    payload_key_version, created_at
                ) values (%s, %s, 'work_update', %s, %s, %s, %s)
                """,
                (
                    task_id,
                    event_seq,
                    queued_fingerprint,
                    queued.ciphertext,
                    queued.key_version,
                    now,
                ),
            )
            db.execute(
                """
                update platform_tasks
                set next_message_seq = %s, next_event_seq = %s, updated_at = %s
                where task_id = %s
                """,
                (message_seq + 1, event_seq + 1, now, task_id),
            )
            return MessageReceipt(task_id, message_seq, False)

    def next_pending_message(self, task_id: UUID) -> PlatformTaskMessage | None:
        with self._connection() as connection, connection.cursor() as db:
            row = db.execute(
                """
                select message_seq, content_ciphertext, content_key_version,
                       created_at, consumed_at
                from platform_task_messages
                where task_id = %s and consumed_at is null
                order by message_seq
                limit 1
                """,
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        message_seq = int(row["message_seq"])
        payload = self._codec.unseal_json(
            f"platform-task:{task_id}:message:{message_seq}",
            SealedTaskContent(
                bytes(row["content_ciphertext"]), int(row["content_key_version"])
            ),
        )
        return PlatformTaskMessage(
            task_id=task_id,
            message_seq=message_seq,
            content=str(payload["content"]),
            attachment_refs=tuple(
                UUID(str(value)) for value in payload["attachment_refs"]
            ),
            created_at=row["created_at"],
            consumed_at=row["consumed_at"],
        )

    def mark_message_consumed(
        self, task_id: UUID, *, message_seq: int, now: datetime
    ) -> bool:
        payload: dict[str, object] = {
            "phase": "message_consumed",
            "message_seq": message_seq,
        }
        fingerprint = _json_fingerprint(payload)
        sealed = self._codec.seal_json(f"platform-task:{task_id}:event", payload)
        with self._connection() as connection, connection.cursor() as db:
            task_row = db.execute(
                "select status from platform_tasks where task_id = %s for update",
                (task_id,),
            ).fetchone()
            if task_row is None:
                raise TaskStoreError("task_not_found")
            if task_row["status"] in TERMINAL_STATUSES:
                raise TaskStoreError("task_terminal")
            row = db.execute(
                """
                select consumed_at from platform_task_messages
                where task_id = %s and message_seq = %s
                for update
                """,
                (task_id, message_seq),
            ).fetchone()
            if row is None:
                raise TaskStoreError("message_not_found")
            if row["consumed_at"] is not None:
                return False
            db.execute(
                """
                update platform_task_messages set consumed_at = %s
                where task_id = %s and message_seq = %s
                """,
                (now, task_id, message_seq),
            )
            try:
                db.execute(
                    "select * from append_platform_task_event(%s, %s, %s, %s, %s, %s)",
                    (
                        task_id,
                        "work_update",
                        fingerprint,
                        sealed.ciphertext,
                        sealed.key_version,
                        now,
                    ),
                ).fetchone()
            except psycopg.errors.CheckViolation as exc:
                raise TaskStoreError("task_terminal") from exc
            return True

    def append_terminal_event_if_no_pending(
        self,
        task_id: UUID,
        *,
        kind: TaskEventKind,
        payload: dict[str, object],
        now: datetime,
    ) -> bool:
        if kind not in {"result", "failed"}:
            raise ValueError("task_terminal_event_invalid")
        fingerprint = _json_fingerprint(payload)
        sealed = self._codec.seal_json(f"platform-task:{task_id}:event", payload)
        with self._connection() as connection, connection.cursor() as db:
            task_row = db.execute(
                "select status from platform_tasks where task_id = %s for update",
                (task_id,),
            ).fetchone()
            if task_row is None:
                raise TaskStoreError("task_not_found")
            if task_row["status"] in TERMINAL_STATUSES:
                raise TaskStoreError("task_terminal")
            pending = db.execute(
                """
                select 1 from platform_task_messages
                where task_id = %s and consumed_at is null
                limit 1
                """,
                (task_id,),
            ).fetchone()
            if pending is not None:
                return False
            try:
                db.execute(
                    "select * from append_platform_task_event(%s, %s, %s, %s, %s, %s)",
                    (
                        task_id,
                        kind,
                        fingerprint,
                        sealed.ciphertext,
                        sealed.key_version,
                        now,
                    ),
                ).fetchone()
            except psycopg.errors.CheckViolation as exc:
                raise TaskStoreError("task_terminal") from exc
            return True

    def request_cancel(
        self,
        task_id: UUID,
        *,
        idempotency_key: str,
        now: datetime,
    ) -> CancelReceipt:
        with self._connection() as connection, connection.cursor() as db:
            existing = db.execute(
                """
                select cancel_request_id from platform_task_cancel_requests
                where task_id = %s and idempotency_key = %s
                """,
                (task_id, idempotency_key),
            ).fetchone()
            task_row = db.execute(
                "select * from platform_tasks where task_id = %s for update", (task_id,)
            ).fetchone()
            if task_row is None:
                raise TaskStoreError("task_not_found")
            if existing is not None:
                return CancelReceipt(
                    task_id,
                    existing["cancel_request_id"],
                    task_row["status"],
                    True,
                )
            request_row = db.execute(
                """
                insert into platform_task_cancel_requests (
                    task_id, idempotency_key, created_at
                ) values (%s, %s, %s)
                returning cancel_request_id
                """,
                (task_id, idempotency_key, now),
            ).fetchone()
            if task_row["status"] not in TERMINAL_STATUSES:
                db.execute(
                    """
                    update platform_tasks
                    set cancel_requested = true, updated_at = %s
                    where task_id = %s
                    """,
                    (now, task_id),
                )
            return CancelReceipt(
                task_id,
                request_row["cancel_request_id"],
                task_row["status"],
                False,
            )
