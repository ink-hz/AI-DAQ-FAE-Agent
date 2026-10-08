from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from src.attachments.archive_models import AttachmentTurnInput


@dataclass(frozen=True)
class ChatTurnRecord:
    external_session_id: str
    channel: str
    question: str
    answer: str
    trace_id: str
    turn_index: int
    user_id: str | None = None
    external_user_id: str | None = None
    sources: list[dict] = field(default_factory=list)
    stages: list[dict] = field(default_factory=list)
    done: dict = field(default_factory=dict)
    planned_capabilities: list[str] = field(default_factory=list)
    capability_coverage: dict = field(default_factory=dict)
    fallback_used: bool = False
    fallback_reason: str | None = None
    outcome: str | None = None
    duration_ms: int | None = None
    question_at: datetime | None = None
    answer_at: datetime | None = None
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class FeedbackRecord:
    external_session_id: str
    trace_id: str
    rating: str
    reason_code: str | None
    comment: str
    channel: str
    turn_id: str | None = None
    message_index: int | None = None
    user_id: str | None = None
    external_user_id: str | None = None
    metadata: dict = field(default_factory=dict)


class TurnResolutionUnavailable(RuntimeError):
    """The store could not decide whether a feedback target exists.

    Deliberately distinct from a resolution that returns `None`. `None` means
    the store looked and there is no such turn inside the caller's ownership
    scope, so a named target is genuinely absent or belongs to somebody else --
    an answer the caller may safely turn into a 404. This exception means the
    lookup never ran (no store is configured, or the database is unreachable),
    so nothing has been proven about the target and no ownership check has
    happened. Overloading `None` for both conflated an outage with a denial.

    Callers must branch on the ownership scope: an owned request has no safe
    answer and must fail closed, while the ownerless public path may accept the
    feedback unlinked -- never linked to a target it could not verify.
    """


class DataFlywheelStore(Protocol):
    def record_chat_turn(
        self,
        record: ChatTurnRecord,
        *,
        attachment_relations: tuple[AttachmentTurnInput, ...] = (),
    ) -> str | None:
        ...

    def record_feedback(self, record: FeedbackRecord) -> str | None:
        ...

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

        The scope is mandatory and mutually exclusive for every implementation:
        either the turn belongs to `owner_subject_id`, or it belongs to no
        subject at all (`require_unowned`). Callers pass it unconditionally, so
        an implementation that omits the parameters turns an ordinary feedback
        submission into a `TypeError`.

        Returns the turn id, or `None` when the lookup ran and no turn matched
        inside that scope. Raises `TurnResolutionUnavailable` when the lookup
        could not run at all -- the two are not interchangeable, see that
        exception's docstring.
        """
        ...


class DisabledDataFlywheelStore:
    def record_chat_turn(
        self,
        record: ChatTurnRecord,
        *,
        attachment_relations: tuple[AttachmentTurnInput, ...] = (),
    ) -> str | None:
        return None

    def record_feedback(self, record: FeedbackRecord) -> str | None:
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
        # No store means no lookup, so nothing can be said about the target --
        # least of all "it does not exist", which would let a database-less
        # deployment answer every feedback submission with a 404. The ownership
        # arguments are accepted, not ignored by omission.
        raise TurnResolutionUnavailable("turn_resolution_unavailable")


class DataFlywheelFallbackWriter:
    def __init__(self, path: Path) -> None:
        self._path = path

    def write(
        self,
        record_type: str,
        payload: ChatTurnRecord | FeedbackRecord,
        *,
        error: str,
        attachment_relations: tuple[AttachmentTurnInput, ...] = (),
    ) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "record_type": record_type,
            "error": error,
            "payload": asdict(payload),
        }
        if attachment_relations:
            record["attachment_relations"] = [
                asdict(item) for item in attachment_relations
            ]
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
