from __future__ import annotations

from dataclasses import dataclass, field

from src.agent.orchestrator import StreamEvent
from src.agent.protocol import RUNTIME_FAILURE_OUTCOMES
from src.platform_tasks.models import TaskEventKind

ProjectedEvent = tuple[TaskEventKind, dict[str, object]]

CANONICAL_EVENT_KINDS = frozenset({
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
})


def validate_canonical_event_kind(kind: str) -> TaskEventKind:
    if kind not in CANONICAL_EVENT_KINDS:
        raise ValueError("event_kind_not_canonical")
    return kind  # type: ignore[return-value]


def normalize_legacy_task_event(
    kind: str,
    payload: dict[str, object],
) -> tuple[ProjectedEvent, ...]:
    """Normalize an FAE-internal event before it crosses the HTTP v1 boundary."""
    normalized = dict(payload)
    if kind == "accepted":
        return ()
    if kind == "started":
        return (("work_update", {"phase": "started", **normalized}),)
    if kind in {"progress", "message_queued", "message_consumed", "cancel_pending"}:
        if kind != "progress":
            normalized.setdefault("phase", kind)
        return (("work_update", normalized),)
    if kind == "sources":
        return (("artifact", {"artifact_type": "sources", **normalized}),)
    if kind in {"done", "succeeded"}:
        return (("result", normalized),)
    if kind == "timed_out":
        return (("timeout", normalized),)
    return ((validate_canonical_event_kind(kind), normalized),)


@dataclass
class FaeTaskEventProjector:
    """Stateful projection of the real public FAE stream into Task Contract v1."""

    _message_parts: list[str] = field(default_factory=list)

    def project(self, event: StreamEvent) -> tuple[ProjectedEvent, ...]:
        if event.kind == "stage":
            if not isinstance(event.data, dict):
                raise ValueError("fae_stage_payload_invalid")
            payload: dict[str, object] = {
                "phase": str(event.data.get("stage") or "fae"),
                "status": str(event.data.get("status") or "running"),
                "summary": str(event.data.get("message") or "FAE 正在执行"),
            }
            for key in ("agent", "metadata"):
                if key in event.data:
                    payload[key] = event.data[key]
            return (("work_update", payload),)

        if event.kind == "text_delta":
            if not isinstance(event.data, dict):
                raise ValueError("fae_text_delta_invalid")
            delta = event.data.get("delta")
            if not isinstance(delta, str):
                raise ValueError("fae_text_delta_invalid")
            if delta:
                self._message_parts.append(delta)
            return ()

        if event.kind == "sources":
            if not isinstance(event.data, list):
                raise ValueError("fae_sources_invalid")
            if not event.data:
                return ()
            return (("artifact", {
                "artifact_type": "sources",
                "items": list(event.data),
            }),)

        if event.kind == "done":
            if not isinstance(event.data, dict):
                raise ValueError("fae_done_invalid")
            answer = "".join(self._message_parts)
            projected: list[ProjectedEvent] = []
            if answer:
                projected.append(("message", {"content": answer}))
            outcome = str(event.data.get("outcome") or "resolved")
            common: dict[str, object] = {
                "answer_markdown": answer,
                "outcome": outcome,
                "fallback_used": bool(event.data.get("fallback_used")),
            }
            if event.data.get("fallback_reason") is not None:
                common["fallback_reason"] = str(event.data["fallback_reason"])
            if event.data.get("loop") is not None:
                common["execution"] = event.data["loop"]
            fallback_reason = str(event.data.get("fallback_reason") or "")
            if outcome in RUNTIME_FAILURE_OUTCOMES or fallback_reason in {
                *RUNTIME_FAILURE_OUTCOMES,
                "loop_runtime_error",
            }:
                common["reason_code"] = str(
                    event.data.get("fallback_reason") or outcome
                )
                projected.append(("failed", common))
            else:
                projected.append(("result", common))
            return tuple(projected)

        raise ValueError("fae_stream_event_not_supported")
