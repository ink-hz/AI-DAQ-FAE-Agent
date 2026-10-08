from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from src.platform_identity.models import DEFAULT_AGENT_ID, validate_agent_id
from src.platform_tasks.identity import (
    TaskIdentityError,
    TaskTokenVerifier,
    VerifiedTaskIdentity,
)
from src.platform_tasks.models import (
    CancelReceipt,
    MessageReceipt,
    PlatformTask,
    PlatformTaskSpec,
    TaskCreateResult,
    TaskEvent,
    TaskStoreError,
)

CONTRACT_VERSION = "orbbec-http-task/v1"


class _TaskStore(Protocol):
    def create_task(
        self, spec: PlatformTaskSpec, *, now: datetime
    ) -> TaskCreateResult: ...

    def get_task(self, task_id: UUID) -> PlatformTask: ...

    def events_after(
        self, task_id: UUID, *, after: int, limit: int
    ) -> tuple[TaskEvent, ...]: ...

    def append_message(
        self,
        task_id: UUID,
        *,
        message_seq: int,
        content: str,
        attachment_refs: tuple[UUID, ...],
        idempotency_key: str,
        now: datetime,
    ) -> MessageReceipt: ...

    def request_cancel(
        self, task_id: UUID, *, idempotency_key: str, now: datetime
    ) -> CancelReceipt: ...


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be UTC")
    return value


def _sorted_unique(values: tuple[str, ...]) -> tuple[str, ...]:
    if not values or tuple(sorted(set(values))) != values:
        raise ValueError("values must be non-empty, unique, and sorted")
    return values


class _CreateTaskRequest(_StrictModel):
    contract_version: Literal["orbbec-http-task/v1"]
    platform_task_id: UUID
    conversation_ref: str = Field(min_length=1)
    turn_ref: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    context_excerpt: tuple[str, ...]
    constraints: tuple[str, ...]
    attachment_refs: tuple[UUID, ...]
    expected_output: str = Field(min_length=1)
    capability_version: int = Field(gt=0)
    idempotency_key: str = Field(min_length=1)
    deadline_at: datetime
    authorized_scopes: tuple[str, ...]

    _deadline_utc = field_validator("deadline_at")(_utc)
    _scopes_sorted = field_validator("authorized_scopes")(_sorted_unique)


class _MessageRequest(_StrictModel):
    contract_version: Literal["orbbec-http-task/v1"]
    message_seq: int = Field(gt=0)
    content: str = Field(min_length=1)
    attachment_refs: tuple[UUID, ...]
    idempotency_key: str = Field(min_length=1)


class _CancelRequest(_StrictModel):
    contract_version: Literal["orbbec-http-task/v1"]
    idempotency_key: str = Field(min_length=1)


@dataclass(frozen=True)
class PlatformTaskCapabilities:
    agent_id: str
    capability_version: int
    supports_actions: bool
    max_duration_seconds: int
    supported_scopes: tuple[str, ...]
    supported_event_kinds: tuple[str, ...]

    @classmethod
    def fae_v1(
        cls, *, capability_version: int = 2, agent_id: str = DEFAULT_AGENT_ID
    ) -> PlatformTaskCapabilities:
        return cls(
            agent_id=validate_agent_id(agent_id),
            capability_version=capability_version,
            supports_actions=False,
            max_duration_seconds=600,
            supported_scopes=("fae.answer",),
            supported_event_kinds=tuple(
                sorted(
                    {
                        "thinking_summary",
                        "message",
                        "work_update",
                        "artifact",
                        "input_required",
                        "finding",
                        "result",
                        "failed",
                        "timeout",
                        "cancelled",
                    }
                )
            ),
        )


def _error(status_code: int, code: str, message: str, **details: object) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "contract_version": CONTRACT_VERSION,
            "error": {"code": code, "message": message, "details": details},
        },
    )


def _bearer(request: Request) -> str:
    value = request.headers.get("authorization", "")
    if not value.startswith("Bearer ") or len(value) <= len("Bearer "):
        raise TaskIdentityError(
            status_code=401,
            code="protocol_violation",
            message="request is not authorized",
        )
    return value[len("Bearer ") :]


def _identity(
    request: Request,
    verifier: TaskTokenVerifier,
    *,
    required_scope: str,
) -> VerifiedTaskIdentity:
    identity = verifier.verify(_bearer(request))
    if required_scope not in identity.authorized_scopes:
        raise TaskIdentityError(
            status_code=403,
            code="scope_denied",
            message="task scope is not authorized",
        )
    return identity


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _task_response(task: PlatformTask) -> dict[str, object]:
    return {
        "contract_version": CONTRACT_VERSION,
        "downstream_task_id": str(task.task_id),
        "platform_task_id": str(task.platform_task_id),
        "status": task.status,
        "cancel_requested": task.cancel_requested,
        "next_event_seq": task.next_event_seq,
        "terminal": task.terminal,
        "created_at": _timestamp(task.created_at),
        "updated_at": _timestamp(task.updated_at),
    }


def register_platform_task_routes(
    app: FastAPI,
    *,
    store: _TaskStore,
    verifier: TaskTokenVerifier,
    capabilities: PlatformTaskCapabilities,
) -> None:
    prefix = "/internal/platform/v1"
    required_scope = capabilities.supported_scopes[0]

    def authorize_task(request: Request, task_id: UUID) -> PlatformTask:
        identity = _identity(request, verifier, required_scope=required_scope)
        task = store.get_task(task_id)
        if (
            identity.agent_id != capabilities.agent_id
            or identity.agent_task_id != task.platform_task_id
            or identity.capability_version != task.capability_version
            or identity.task_deadline_at != task.deadline_at
            or identity.internal_user_id != task.requester_internal_user_id
            or identity.authorized_scopes != task.authorized_scopes
        ):
            raise TaskIdentityError(
                status_code=403,
                code="protocol_violation",
                message="request is not authorized",
            )
        return task

    def task_error(exc: TaskStoreError) -> JSONResponse:
        if exc.code == "task_not_found":
            return _error(404, exc.code, "task was not found")
        if exc.code == "task_terminal":
            return _error(409, exc.code, "task is terminal")
        if exc.code == "message_sequence_conflict":
            return _error(409, exc.code, "message sequence conflicts")
        return _error(503, "upstream_unavailable", "task store is unavailable")

    @app.get(f"{prefix}/capabilities")
    def platform_task_capabilities(request: Request):
        try:
            _identity(request, verifier, required_scope=required_scope)
        except TaskIdentityError as exc:
            return _error(exc.status_code, exc.code, exc.safe_message)
        return {
            "contract_version": CONTRACT_VERSION,
            "agent_id": capabilities.agent_id,
            "capability_version": capabilities.capability_version,
            "supports_actions": capabilities.supports_actions,
            "max_duration_seconds": capabilities.max_duration_seconds,
            "supported_scopes": list(capabilities.supported_scopes),
            "supported_event_kinds": list(capabilities.supported_event_kinds),
        }

    @app.get(f"{prefix}/health")
    def platform_task_health(request: Request):
        try:
            _identity(request, verifier, required_scope=required_scope)
        except TaskIdentityError as exc:
            return _error(exc.status_code, exc.code, exc.safe_message)
        return {
            "contract_version": CONTRACT_VERSION,
            "status": "healthy",
            "capability_version": capabilities.capability_version,
        }

    @app.post(f"{prefix}/tasks")
    async def create_platform_task(request: Request):
        try:
            identity = _identity(request, verifier, required_scope=required_scope)
            body = _CreateTaskRequest.model_validate_json(await request.body())
        except TaskIdentityError as exc:
            return _error(exc.status_code, exc.code, exc.safe_message)
        except (ValidationError, ValueError):
            return _error(422, "protocol_violation", "task request is invalid")

        if body.capability_version != capabilities.capability_version:
            return _error(
                409,
                "capability_changed",
                "task capability version changed",
                current_capability_version=capabilities.capability_version,
                must_refresh_capabilities=True,
            )
        if (
            identity.agent_id != capabilities.agent_id
            or identity.agent_task_id != body.platform_task_id
            or identity.capability_version != capabilities.capability_version
            or identity.task_deadline_at != body.deadline_at
        ):
            return _error(403, "protocol_violation", "request is not authorized")
        if body.authorized_scopes != identity.authorized_scopes:
            return _error(403, "scope_denied", "task scope is not authorized")
        now = datetime.now(UTC)
        if body.deadline_at <= now:
            return _error(409, "deadline_expired", "task deadline has expired")
        try:
            result = store.create_task(
                PlatformTaskSpec(
                    platform_task_id=body.platform_task_id,
                    conversation_ref=body.conversation_ref,
                    turn_ref=body.turn_ref,
                    objective=body.objective,
                    context_excerpt=body.context_excerpt,
                    constraints=body.constraints,
                    attachment_refs=body.attachment_refs,
                    expected_output=body.expected_output,
                    capability_version=body.capability_version,
                    idempotency_key=body.idempotency_key,
                    deadline_at=body.deadline_at,
                    requester_internal_user_id=identity.internal_user_id,
                    authorized_scopes=identity.authorized_scopes,
                ),
                now=now,
            )
        except TaskStoreError as exc:
            if exc.code == "idempotency_conflict":
                return _error(409, exc.code, "task idempotency key conflicts")
            return _error(503, "upstream_unavailable", "task store is unavailable")
        return JSONResponse(
            status_code=202,
            content={
                "contract_version": CONTRACT_VERSION,
                "downstream_task_id": str(result.task.task_id),
                "status": "queued",
                "next_event_seq": 1,
                "duplicate": result.duplicate,
            },
        )

    @app.get(f"{prefix}/tasks/{{task_id}}")
    def get_platform_task(task_id: UUID, request: Request):
        try:
            task = authorize_task(request, task_id)
        except TaskIdentityError as exc:
            return _error(exc.status_code, exc.code, exc.safe_message)
        except TaskStoreError as exc:
            return task_error(exc)
        return _task_response(task)

    @app.get(f"{prefix}/tasks/{{task_id}}/events")
    def get_platform_task_events(task_id: UUID, request: Request):
        try:
            task = authorize_task(request, task_id)
            raw_after = request.query_params.get("after", "0")
            raw_limit = request.query_params.get("limit", "100")
            raw_wait = request.query_params.get("wait_seconds", "0")
            after = int(raw_after)
            limit = int(raw_limit)
            wait_seconds = float(raw_wait)
            if after < 0 or not 1 <= limit <= 100 or wait_seconds != 0:
                raise ValueError
            events = store.events_after(task_id, after=after, limit=limit)
        except TaskIdentityError as exc:
            return _error(exc.status_code, exc.code, exc.safe_message)
        except TaskStoreError as exc:
            return task_error(exc)
        except (TypeError, ValueError):
            return _error(422, "protocol_violation", "event cursor is invalid")
        next_after = events[-1].seq if events else after
        return {
            "contract_version": CONTRACT_VERSION,
            "downstream_task_id": str(task_id),
            "events": [
                {
                    "seq": event.seq,
                    "kind": event.kind,
                    "created_at": _timestamp(event.created_at),
                    "payload": event.payload,
                }
                for event in events
            ],
            "next_after": next_after,
            "terminal": task.terminal and next_after >= task.next_event_seq - 1,
        }

    @app.post(f"{prefix}/tasks/{{task_id}}/messages")
    async def append_platform_task_message(task_id: UUID, request: Request):
        try:
            authorize_task(request, task_id)
            body = _MessageRequest.model_validate_json(await request.body())
            receipt = store.append_message(
                task_id,
                message_seq=body.message_seq,
                content=body.content,
                attachment_refs=body.attachment_refs,
                idempotency_key=body.idempotency_key,
                now=datetime.now(UTC),
            )
        except TaskIdentityError as exc:
            return _error(exc.status_code, exc.code, exc.safe_message)
        except TaskStoreError as exc:
            return task_error(exc)
        except (ValidationError, ValueError):
            return _error(422, "protocol_violation", "message request is invalid")
        return JSONResponse(
            status_code=202,
            content={
                "contract_version": CONTRACT_VERSION,
                "downstream_task_id": str(receipt.task_id),
                "message_seq": receipt.message_seq,
                "status": "accepted",
                "duplicate": receipt.duplicate,
            },
        )

    @app.post(f"{prefix}/tasks/{{task_id}}/cancel")
    async def cancel_platform_task(task_id: UUID, request: Request):
        try:
            authorize_task(request, task_id)
            body = _CancelRequest.model_validate_json(await request.body())
            receipt = store.request_cancel(
                task_id,
                idempotency_key=body.idempotency_key,
                now=datetime.now(UTC),
            )
        except TaskIdentityError as exc:
            return _error(exc.status_code, exc.code, exc.safe_message)
        except TaskStoreError as exc:
            return task_error(exc)
        except (ValidationError, ValueError):
            return _error(422, "protocol_violation", "cancel request is invalid")
        status = receipt.status if receipt.status in {
            "completed", "failed", "cancelled", "timed_out"
        } else "cancel_requested"
        return JSONResponse(
            status_code=(200 if status != "cancel_requested" else 202),
            content={
                "contract_version": CONTRACT_VERSION,
                "downstream_task_id": str(receipt.task_id),
                "cancel_request_id": str(receipt.cancel_request_id),
                "status": status,
                "duplicate": receipt.duplicate,
            },
        )
