from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal
from uuid import UUID

TaskStatus = Literal[
    "queued",
    "running",
    "waiting_input",
    "waiting_confirmation",
    "completed",
    "failed",
    "cancelled",
    "timed_out",
]
TaskEventKind = Literal[
    "thinking_summary",
    "message",
    "work_update",
    "artifact",
    "input_required",
    "action_required",
    "finding",
    "result",
    "failed",
    "timeout",
    "cancelled",
]
TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "timed_out"})
EVENT_STATUS: dict[str, TaskStatus] = {
    "work_update": "running",
    "input_required": "waiting_input",
    "action_required": "waiting_confirmation",
    "result": "completed",
    "failed": "failed",
    "timeout": "timed_out",
    "cancelled": "cancelled",
}


class TaskStoreError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _require_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError("task_timestamp_must_be_utc")
    return value


def _require_nonempty(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ValueError(f"{field_name}_invalid")
    return value


@dataclass(frozen=True)
class PlatformTaskSpec:
    platform_task_id: UUID
    conversation_ref: str
    turn_ref: str
    objective: str = field(repr=False)
    context_excerpt: tuple[str, ...] = field(repr=False)
    constraints: tuple[str, ...] = field(repr=False)
    attachment_refs: tuple[UUID, ...]
    expected_output: str = field(repr=False)
    capability_version: int
    idempotency_key: str
    deadline_at: datetime
    requester_internal_user_id: UUID
    authorized_scopes: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in (
            "conversation_ref",
            "turn_ref",
            "objective",
            "expected_output",
            "idempotency_key",
        ):
            _require_nonempty(getattr(self, name), name)
        if isinstance(self.capability_version, bool) or self.capability_version <= 0:
            raise ValueError("capability_version_invalid")
        _require_utc(self.deadline_at)
        if not self.authorized_scopes or tuple(sorted(set(self.authorized_scopes))) != self.authorized_scopes:
            raise ValueError("authorized_scopes_invalid")
        for value in (*self.context_excerpt, *self.constraints, *self.authorized_scopes):
            _require_nonempty(value, "task_sequence_value")

    def protected_payload(self) -> dict[str, object]:
        return {
            "conversation_ref": self.conversation_ref,
            "turn_ref": self.turn_ref,
            "objective": self.objective,
            "context_excerpt": list(self.context_excerpt),
            "constraints": list(self.constraints),
            "attachment_refs": [str(value) for value in self.attachment_refs],
            "expected_output": self.expected_output,
        }

    def fingerprint(self) -> bytes:
        document = {
            "platform_task_id": str(self.platform_task_id),
            "capability_version": self.capability_version,
            "idempotency_key": self.idempotency_key,
            "deadline_at": self.deadline_at.isoformat(),
            "requester_internal_user_id": str(self.requester_internal_user_id),
            "authorized_scopes": list(self.authorized_scopes),
            **self.protected_payload(),
        }
        encoded = json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return sha256(encoded).digest()


@dataclass(frozen=True)
class PlatformTask:
    task_id: UUID
    platform_task_id: UUID
    status: TaskStatus
    cancel_requested: bool
    next_event_seq: int
    next_message_seq: int
    capability_version: int
    deadline_at: datetime
    requester_internal_user_id: UUID
    authorized_scopes: tuple[str, ...]
    created_at: datetime
    updated_at: datetime
    terminal_at: datetime | None = None
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    attempt_count: int = 0

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES


@dataclass(frozen=True)
class TaskCreateResult:
    task: PlatformTask
    duplicate: bool


@dataclass(frozen=True)
class TaskEvent:
    task_id: UUID
    seq: int
    kind: TaskEventKind
    payload: dict[str, object] = field(repr=False)
    created_at: datetime


@dataclass(frozen=True)
class MessageReceipt:
    task_id: UUID
    message_seq: int
    duplicate: bool


@dataclass(frozen=True)
class PlatformTaskMessage:
    task_id: UUID
    message_seq: int
    content: str = field(repr=False)
    attachment_refs: tuple[UUID, ...]
    created_at: datetime
    consumed_at: datetime | None = None


@dataclass(frozen=True)
class CancelReceipt:
    task_id: UUID
    cancel_request_id: UUID
    status: TaskStatus
    duplicate: bool
