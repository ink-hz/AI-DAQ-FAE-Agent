from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from hashlib import sha256
from threading import RLock
from uuid import UUID, uuid4

from src.platform_tasks.crypto import SealedTaskContent, TaskContentCodec
from src.platform_tasks.models import (
    EVENT_STATUS,
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

__all__ = ["InMemoryPlatformTaskStore", "TaskStoreError"]


def _payload_fingerprint(value: dict[str, object]) -> bytes:
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).digest()


@dataclass
class _StoredMessage:
    fingerprint: bytes
    content: SealedTaskContent
    idempotency_key: str
    created_at: datetime
    consumed_at: datetime | None = None


@dataclass
class _StoredTask:
    task: PlatformTask
    spec: PlatformTaskSpec
    request_fingerprint: bytes
    request_content: SealedTaskContent
    events: list[tuple[TaskEvent, SealedTaskContent]]
    messages: dict[int, _StoredMessage]
    message_keys: dict[str, int]
    cancel_keys: dict[str, UUID]


class InMemoryPlatformTaskStore:
    """Thread-safe semantic twin of the PostgreSQL authority for unit tests/dev."""

    def __init__(self, *, codec: TaskContentCodec) -> None:
        self._codec = codec
        self._lock = RLock()
        self._tasks: dict[UUID, _StoredTask] = {}
        self._by_platform_task: dict[UUID, UUID] = {}
        self._by_idempotency: dict[str, UUID] = {}

    def __len__(self) -> int:
        with self._lock:
            return len(self._tasks)

    def create_task(self, spec: PlatformTaskSpec, *, now: datetime) -> TaskCreateResult:
        fingerprint = spec.fingerprint()
        with self._lock:
            existing_id = self._by_idempotency.get(spec.idempotency_key)
            platform_existing_id = self._by_platform_task.get(spec.platform_task_id)
            if existing_id is not None or platform_existing_id is not None:
                if existing_id != platform_existing_id:
                    raise TaskStoreError("idempotency_conflict")
                stored = self._tasks[existing_id]
                if stored.request_fingerprint != fingerprint:
                    raise TaskStoreError("idempotency_conflict")
                return TaskCreateResult(stored.task, True)
            task_id = uuid4()
            task = PlatformTask(
                task_id=task_id,
                platform_task_id=spec.platform_task_id,
                status="queued",
                cancel_requested=False,
                next_event_seq=1,
                next_message_seq=1,
                capability_version=spec.capability_version,
                deadline_at=spec.deadline_at,
                requester_internal_user_id=spec.requester_internal_user_id,
                authorized_scopes=spec.authorized_scopes,
                created_at=now,
                updated_at=now,
            )
            sealed = self._codec.seal_json(
                f"platform-task:{task_id}:request", spec.protected_payload()
            )
            self._tasks[task_id] = _StoredTask(
                task=task,
                spec=spec,
                request_fingerprint=fingerprint,
                request_content=sealed,
                events=[],
                messages={},
                message_keys={},
                cancel_keys={},
            )
            self._by_idempotency[spec.idempotency_key] = task_id
            self._by_platform_task[spec.platform_task_id] = task_id
            return TaskCreateResult(task, False)

    def get_task(self, task_id: UUID) -> PlatformTask:
        with self._lock:
            return self._stored(task_id).task

    def claim_next_expired_task(
        self, *, worker_id: str, lease_seconds: int, now: datetime
    ) -> PlatformTask | None:
        with self._lock:
            candidates = [
                stored
                for stored in self._tasks.values()
                if not stored.task.terminal
                and stored.task.deadline_at <= now
            ]
            return self._claim(candidates, worker_id, lease_seconds, now)

    def claim_next_task(
        self, *, worker_id: str, lease_seconds: int, now: datetime
    ) -> PlatformTask | None:
        with self._lock:
            candidates = [
                stored
                for stored in self._tasks.values()
                if stored.task.status in {"queued", "running"}
                and not stored.task.cancel_requested
                and stored.task.deadline_at > now
                and (
                    stored.task.lease_expires_at is None
                    or stored.task.lease_expires_at <= now
                    or stored.task.lease_owner == worker_id
                )
            ]
            return self._claim(candidates, worker_id, lease_seconds, now)

    def _claim(
        self,
        candidates: list[_StoredTask],
        worker_id: str,
        lease_seconds: int,
        now: datetime,
    ) -> PlatformTask | None:
        if not worker_id or lease_seconds <= 0:
            raise ValueError("task_lease_invalid")
        if not candidates:
            return None
        stored = min(candidates, key=lambda value: (value.task.created_at, value.task.task_id))
        stored.task = replace(
            stored.task,
            lease_owner=worker_id,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
            attempt_count=stored.task.attempt_count + 1,
            updated_at=now,
        )
        return stored.task

    def renew_task_lease(
        self,
        task_id: UUID,
        *,
        worker_id: str,
        lease_seconds: int,
        now: datetime,
    ) -> PlatformTask:
        with self._lock:
            stored = self._stored(task_id)
            if (
                stored.task.terminal
                or stored.task.lease_owner != worker_id
                or stored.task.lease_expires_at is None
                or stored.task.lease_expires_at <= now
            ):
                raise TaskStoreError("task_lease_lost")
            stored.task = replace(
                stored.task,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                updated_at=now,
            )
            return stored.task

    def release_task_lease(self, task_id: UUID, *, worker_id: str, now: datetime) -> None:
        with self._lock:
            stored = self._stored(task_id)
            if stored.task.lease_owner != worker_id:
                return
            stored.task = replace(
                stored.task,
                lease_owner=None,
                lease_expires_at=None,
                updated_at=max(now, stored.task.updated_at),
            )

    def load_request(self, task_id: UUID) -> PlatformTaskSpec:
        with self._lock:
            stored = self._stored(task_id)
            self._codec.unseal_json(
                f"platform-task:{task_id}:request", stored.request_content
            )
            return stored.spec

    def append_event(
        self,
        task_id: UUID,
        *,
        kind: TaskEventKind,
        payload: dict[str, object],
        now: datetime,
    ) -> TaskEvent:
        with self._lock:
            stored = self._stored(task_id)
            return self._append_event_locked(stored, kind=kind, payload=payload, now=now)

    def _append_event_locked(
        self,
        stored: _StoredTask,
        *,
        kind: TaskEventKind,
        payload: dict[str, object],
        now: datetime,
        project_status: bool = True,
    ) -> TaskEvent:
        if stored.task.status in TERMINAL_STATUSES:
            raise TaskStoreError("task_terminal")
        task_id = stored.task.task_id
        seq = stored.task.next_event_seq
        event = TaskEvent(
            task_id=task_id,
            seq=seq,
            kind=kind,
            payload=payload,
            created_at=now,
        )
        sealed = self._codec.seal_json(f"platform-task:{task_id}:event", payload)
        status = (
            EVENT_STATUS.get(kind, stored.task.status)
            if project_status
            else stored.task.status
        )
        terminal_at = now if status in TERMINAL_STATUSES else None
        stored.events.append((event, sealed))
        stored.task = replace(
            stored.task,
            status=status,
            next_event_seq=seq + 1,
            updated_at=now,
            terminal_at=terminal_at,
            lease_owner=(None if terminal_at is not None else stored.task.lease_owner),
            lease_expires_at=(
                None if terminal_at is not None else stored.task.lease_expires_at
            ),
        )
        return event

    def events_after(self, task_id: UUID, *, after: int, limit: int) -> tuple[TaskEvent, ...]:
        if after < 0 or not 1 <= limit <= 100:
            raise ValueError("event_page_invalid")
        with self._lock:
            stored = self._stored(task_id)
            result: list[TaskEvent] = []
            for event, sealed in stored.events:
                if event.seq <= after:
                    continue
                payload = self._codec.unseal_json(
                    f"platform-task:{task_id}:event", sealed
                )
                result.append(replace(event, payload=payload))
                if len(result) == limit:
                    break
            return tuple(result)

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
        if message_seq <= 0 or not content or not idempotency_key:
            raise ValueError("message_invalid")
        payload = {
            "content": content,
            "attachment_refs": [str(value) for value in attachment_refs],
        }
        fingerprint = _payload_fingerprint(payload)
        with self._lock:
            stored = self._stored(task_id)
            if stored.task.status in TERMINAL_STATUSES:
                raise TaskStoreError("task_terminal")
            existing_seq = stored.message_keys.get(idempotency_key)
            existing = stored.messages.get(message_seq)
            if existing_seq is not None or existing is not None:
                if (
                    existing_seq != message_seq
                    or existing is None
                    or existing.fingerprint != fingerprint
                    or existing.idempotency_key != idempotency_key
                ):
                    raise TaskStoreError("message_sequence_conflict")
                return MessageReceipt(task_id, message_seq, True)
            if message_seq != stored.task.next_message_seq:
                raise TaskStoreError("message_sequence_conflict")
            sealed = self._codec.seal_json(
                f"platform-task:{task_id}:message:{message_seq}", payload
            )
            stored.messages[message_seq] = _StoredMessage(
                fingerprint=fingerprint,
                content=sealed,
                idempotency_key=idempotency_key,
                created_at=now,
            )
            stored.message_keys[idempotency_key] = message_seq
            stored.task = replace(
                stored.task,
                next_message_seq=message_seq + 1,
                updated_at=now,
            )
            self._append_event_locked(
                stored,
                kind="work_update",
                payload={"phase": "message_queued", "message_seq": message_seq},
                now=now,
                project_status=False,
            )
            return MessageReceipt(task_id, message_seq, False)

    def next_pending_message(self, task_id: UUID) -> PlatformTaskMessage | None:
        with self._lock:
            stored = self._stored(task_id)
            for message_seq, message in sorted(stored.messages.items()):
                if message.consumed_at is not None:
                    continue
                payload = self._codec.unseal_json(
                    f"platform-task:{task_id}:message:{message_seq}", message.content
                )
                return PlatformTaskMessage(
                    task_id=task_id,
                    message_seq=message_seq,
                    content=str(payload["content"]),
                    attachment_refs=tuple(
                        UUID(str(value)) for value in payload["attachment_refs"]
                    ),
                    created_at=message.created_at,
                )
            return None

    def mark_message_consumed(
        self, task_id: UUID, *, message_seq: int, now: datetime
    ) -> bool:
        with self._lock:
            stored = self._stored(task_id)
            if stored.task.terminal:
                raise TaskStoreError("task_terminal")
            message = stored.messages.get(message_seq)
            if message is None:
                raise TaskStoreError("message_not_found")
            if message.consumed_at is not None:
                return False
            message.consumed_at = now
            self._append_event_locked(
                stored,
                kind="work_update",
                payload={"phase": "message_consumed", "message_seq": message_seq},
                now=now,
            )
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
        with self._lock:
            stored = self._stored(task_id)
            if any(message.consumed_at is None for message in stored.messages.values()):
                return False
            self._append_event_locked(stored, kind=kind, payload=payload, now=now)
            return True

    def request_cancel(
        self,
        task_id: UUID,
        *,
        idempotency_key: str,
        now: datetime,
    ) -> CancelReceipt:
        if not idempotency_key:
            raise ValueError("cancel_idempotency_key_invalid")
        with self._lock:
            stored = self._stored(task_id)
            existing = stored.cancel_keys.get(idempotency_key)
            if existing is not None:
                return CancelReceipt(task_id, existing, stored.task.status, True)
            request_id = uuid4()
            stored.cancel_keys[idempotency_key] = request_id
            if stored.task.status not in TERMINAL_STATUSES:
                stored.task = replace(
                    stored.task,
                    cancel_requested=True,
                    updated_at=now,
                )
            return CancelReceipt(task_id, request_id, stored.task.status, False)

    def _stored(self, task_id: UUID) -> _StoredTask:
        try:
            return self._tasks[task_id]
        except KeyError:
            raise TaskStoreError("task_not_found") from None
