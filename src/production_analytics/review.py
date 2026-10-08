from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.production_analytics.annotation import SessionAnnotation

_HARD_REASON_ORDER = (
    "bad_feedback",
    "fallback",
    "non_resolved_outcome",
    "empty_answer",
    "truncated",
    "timeout",
    "provider_or_protocol_error",
    "other_runtime_failure",
    "attachment_failure",
    "required_attachment",
    "incorrect_refusal",
    "answer_failed",
    "low_confidence",
    "uncertain_population",
    "management_case",
    "product_backlog",
)
_HARD_REASONS = set(_HARD_REASON_ORDER)
_FAILURE_LAYERS = {
    "channel",
    "context",
    "guardrail",
    "schema",
    "planner",
    "capability_evidence",
    "coverage",
    "synthesis",
    "outcome",
    "trace_eval",
}
_RESOLUTION_STATUSES = {
    "fully_resolved",
    "partially_resolved",
    "clarification_needed",
    "correct_refusal",
    "incorrect_refusal",
    "answer_failed",
    "insufficient_review_evidence",
}
_QUALITY_JUDGMENTS = {
    "correct_and_helpful",
    "partially_correct",
    "incorrect",
    "correct_refusal",
    "incorrect_refusal",
    "failed",
    "insufficient_review_evidence",
}
_REVIEW_KEYS = {
    "analysis_session_id",
    "reviewer_kind",
    "reviewer_identity",
    "reviewed_at",
    "quality_judgment",
    "resolution_status",
    "primary_failure_layer",
    "secondary_layers",
    "annotation_agreement",
    "failure_reason",
    "business_case_approved",
    "product_signal_approved",
    "first_turn_resolved",
    "multi_turn_converged",
}


class ReviewError(ValueError):
    pass


@dataclass(frozen=True)
class ReviewSubject:
    analysis_session_id: str
    week_start: str
    primary_product_family: str
    primary_intent: str
    complexity: str
    feedback_state: str
    hard_reasons: tuple[str, ...]
    turn_count: int

    @property
    def ordinary_stratum(self) -> str:
        return "|".join(
            (
                self.week_start,
                self.primary_product_family,
                self.primary_intent,
                self.complexity,
                self.feedback_state,
            )
        )


@dataclass(frozen=True)
class ReviewQueueItem:
    analysis_session_id: str
    queue_kind: str
    reasons: tuple[str, ...]
    review_stratum: str
    turn_count: int


@dataclass(frozen=True)
class ReviewQueues:
    hard: tuple[ReviewQueueItem, ...]
    ordinary: tuple[ReviewQueueItem, ...]
    stratum_populations: dict[str, int]


@dataclass(frozen=True)
class AcceptedReview:
    payload: dict[str, Any]
    queue_kind: str
    review_stratum: str

    @property
    def analysis_session_id(self) -> str:
        return str(self.payload["analysis_session_id"])

    def to_metric_dict(self) -> dict[str, Any]:
        return {
            **self.payload,
            "queue_kind": self.queue_kind,
            "review_stratum": self.review_stratum,
        }


def _annotation_payload(value: Mapping[str, object] | SessionAnnotation) -> Mapping[str, object]:
    return value.payload if isinstance(value, SessionAnnotation) else value


def _append_reason(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


def _attachment_failure(summary: object) -> bool:
    if not isinstance(summary, Mapping):
        return False
    archive = summary.get("archive_statuses")
    thumbnail = summary.get("thumbnail_statuses")
    if isinstance(archive, Mapping) and any(
        int(archive.get(status) or 0) > 0
        for status in ("failed", "expired_unarchived", "deletion_pending")
    ):
        return True
    return isinstance(thumbnail, Mapping) and int(thumbnail.get("unavailable") or 0) > 0


def _runtime_failure_kinds(turn: Mapping[str, object]) -> set[str]:
    raw = turn.get("runtime_failure_kinds")
    if raw is None:
        return set()
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return {"other"}
    kinds: set[str] = set()
    allowed = {
        "fallback",
        "timeout",
        "provider_error",
        "protocol_error",
        "truncated",
        "empty_answer",
        "attachment_failure",
        "other",
        "unknown",
    }
    for item in raw:
        kinds.add(item if isinstance(item, str) and item in allowed else "other")
    return kinds


def derive_review_subjects(
    sessions: Sequence[Mapping[str, object]],
    turns: Sequence[Mapping[str, object]],
    annotations: Sequence[Mapping[str, object] | SessionAnnotation],
    *,
    management_case_ids: frozenset[str] = frozenset(),
    product_backlog_ids: frozenset[str] = frozenset(),
    low_confidence_threshold: float = 0.7,
) -> tuple[ReviewSubject, ...]:
    turns_by_session: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for turn in turns:
        turns_by_session[str(turn.get("analysis_session_id") or "")].append(turn)
    annotations_by_session = {
        str(_annotation_payload(item).get("analysis_session_id") or ""): _annotation_payload(item)
        for item in annotations
    }
    subjects: list[ReviewSubject] = []
    for session in sessions:
        population_class = str(session.get("population_class") or "")
        if population_class == "excluded_non_production":
            continue
        analysis_session_id = str(session.get("analysis_session_id") or "")
        if not analysis_session_id:
            raise ReviewError("review subject is missing analysis_session_id")
        annotation = annotations_by_session.get(analysis_session_id, {})
        session_turns = turns_by_session.get(analysis_session_id, [])
        reasons: list[str] = []
        if session.get("feedback_bad") is True:
            _append_reason(reasons, "bad_feedback")
        runtime_kinds = set().union(
            *(_runtime_failure_kinds(turn) for turn in session_turns)
        ) if session_turns else set()
        if "fallback" in runtime_kinds or any(
            turn.get("fallback_used") is True for turn in session_turns
        ):
            _append_reason(reasons, "fallback")
        outcomes = [str(turn.get("actual_outcome") or "") for turn in session_turns]
        if any(outcome and outcome != "resolved" for outcome in outcomes):
            _append_reason(reasons, "non_resolved_outcome")
        if "empty_answer" in runtime_kinds or any(
            not str(turn.get("answer") or "").strip() for turn in session_turns
        ):
            _append_reason(reasons, "empty_answer")
        if "truncated" in runtime_kinds:
            _append_reason(reasons, "truncated")
        if "timeout" in runtime_kinds:
            _append_reason(reasons, "timeout")
        if runtime_kinds & {"provider_error", "protocol_error"}:
            _append_reason(reasons, "provider_or_protocol_error")
        if runtime_kinds & {"other", "unknown"}:
            _append_reason(reasons, "other_runtime_failure")
        if "attachment_failure" in runtime_kinds or _attachment_failure(
            session.get("attachment_summary")
        ):
            _append_reason(reasons, "attachment_failure")
        turn_annotations = annotation.get("turn_annotations")
        if isinstance(turn_annotations, list) and any(
            isinstance(item, Mapping) and item.get("attachment_dependency") == "required"
            for item in turn_annotations
        ):
            _append_reason(reasons, "required_attachment")
        resolution_status = annotation.get("resolution_status")
        if resolution_status == "incorrect_refusal":
            _append_reason(reasons, "incorrect_refusal")
        if resolution_status == "answer_failed":
            _append_reason(reasons, "answer_failed")
        confidence = annotation.get("semantic_confidence")
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or confidence < low_confidence_threshold:
            _append_reason(reasons, "low_confidence")
        if population_class == "uncertain_population":
            _append_reason(reasons, "uncertain_population")
        if analysis_session_id in management_case_ids:
            _append_reason(reasons, "management_case")
        if analysis_session_id in product_backlog_ids:
            _append_reason(reasons, "product_backlog")
        product_families = annotation.get("product_families")
        intent_capabilities = annotation.get("intent_capabilities")
        primary_product = (
            str(product_families[0])
            if isinstance(product_families, list) and product_families
            else "unknown"
        )
        primary_intent = (
            str(intent_capabilities[0])
            if isinstance(intent_capabilities, list) and intent_capabilities
            else "unknown"
        )
        feedback_state = (
            "bad"
            if session.get("feedback_bad") is True
            else "good"
            if session.get("feedback_good") is True
            else "none"
        )
        subjects.append(
            ReviewSubject(
                analysis_session_id=analysis_session_id,
                week_start=str(session.get("week_start") or "unknown"),
                primary_product_family=primary_product,
                primary_intent=primary_intent,
                complexity=str(annotation.get("complexity_level") or "unknown"),
                feedback_state=feedback_state,
                hard_reasons=tuple(reasons),
                turn_count=int(session.get("turn_count") or len(session_turns)),
            )
        )
    return tuple(sorted(subjects, key=lambda item: item.analysis_session_id))


def _sample_rank(analysis_id: str, stratum: str, session_id: str) -> str:
    return hashlib.sha256(f"{analysis_id}:{stratum}:{session_id}".encode()).hexdigest()


def build_review_queues(
    subjects: Sequence[ReviewSubject], *, analysis_id: str
) -> ReviewQueues:
    seen: set[str] = set()
    hard: list[ReviewQueueItem] = []
    ordinary_by_stratum: dict[str, list[ReviewSubject]] = defaultdict(list)
    populations: dict[str, int] = defaultdict(int)
    for subject in subjects:
        if subject.analysis_session_id in seen:
            raise ReviewError("duplicate review subject")
        seen.add(subject.analysis_session_id)
        invalid_reasons = set(subject.hard_reasons) - _HARD_REASONS
        if invalid_reasons:
            raise ReviewError(f"unknown hard review reasons: {sorted(invalid_reasons)}")
        reasons = tuple(dict.fromkeys(subject.hard_reasons))
        if reasons:
            stratum = f"hard:{subject.ordinary_stratum}"
            populations[stratum] += 1
            hard.append(
                ReviewQueueItem(
                    subject.analysis_session_id,
                    "hard",
                    reasons,
                    stratum,
                    subject.turn_count,
                )
            )
        else:
            ordinary_by_stratum[subject.ordinary_stratum].append(subject)
            populations[subject.ordinary_stratum] += 1
    ordinary: list[ReviewQueueItem] = []
    for stratum, members in sorted(ordinary_by_stratum.items()):
        sample_size = min(len(members), max(10, math.ceil(len(members) * 0.10)))
        selected = sorted(
            members,
            key=lambda item: _sample_rank(analysis_id, stratum, item.analysis_session_id),
        )[:sample_size]
        ordinary.extend(
            ReviewQueueItem(
                item.analysis_session_id,
                "ordinary",
                (),
                stratum,
                item.turn_count,
            )
            for item in selected
        )
    hard.sort(key=lambda item: item.analysis_session_id)
    ordinary.sort(key=lambda item: item.analysis_session_id)
    return ReviewQueues(tuple(hard), tuple(ordinary), dict(sorted(populations.items())))


def _validate_review_record(row: Mapping[str, object], item: ReviewQueueItem) -> dict[str, Any]:
    unknown = set(row) - _REVIEW_KEYS
    missing = _REVIEW_KEYS - set(row)
    if unknown:
        raise ReviewError(f"unknown review key: {sorted(unknown)}")
    if missing:
        raise ReviewError(f"missing review key: {sorted(missing)}")
    reviewer_kind = row["reviewer_kind"]
    if reviewer_kind not in {"codex", "human_fae"}:
        raise ReviewError("reviewer_kind must be codex or human_fae")
    reviewer_identity = row["reviewer_identity"]
    if not isinstance(reviewer_identity, str) or not reviewer_identity:
        raise ReviewError("reviewer_identity must be non-empty")
    if "glm" in reviewer_identity.casefold():
        raise ReviewError("GLM self review is not allowed")
    reviewed_at = row["reviewed_at"]
    if not isinstance(reviewed_at, str) or not reviewed_at:
        raise ReviewError("reviewed_at must be non-empty")
    try:
        timestamp = datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReviewError("reviewed_at is invalid") from exc
    if timestamp.tzinfo is None:
        raise ReviewError("reviewed_at must be timezone-aware")
    if row["quality_judgment"] not in _QUALITY_JUDGMENTS:
        raise ReviewError("quality_judgment is invalid")
    if row["resolution_status"] not in _RESOLUTION_STATUSES:
        raise ReviewError("resolution_status is invalid")
    primary = row["primary_failure_layer"]
    if primary is not None and primary not in _FAILURE_LAYERS:
        raise ReviewError("primary failure layer is invalid")
    secondary = row["secondary_layers"]
    if not isinstance(secondary, list) or any(item not in _FAILURE_LAYERS for item in secondary):
        raise ReviewError("secondary failure layer is invalid")
    if len(secondary) != len(set(secondary)) or primary in secondary:
        raise ReviewError("failure layers contain duplicates")
    for field in (
        "annotation_agreement",
        "business_case_approved",
        "product_signal_approved",
    ):
        if not isinstance(row[field], bool):
            raise ReviewError(f"{field} must be boolean")
    if not isinstance(row["failure_reason"], str):
        raise ReviewError("failure_reason must be a string")
    if row["first_turn_resolved"] is not None and not isinstance(
        row["first_turn_resolved"], bool
    ):
        raise ReviewError("first_turn_resolved must be boolean or null")
    if row["multi_turn_converged"] is not None and not isinstance(
        row["multi_turn_converged"], bool
    ):
        raise ReviewError("multi_turn_converged must be boolean or null")
    if item.turn_count <= 1 and row["multi_turn_converged"] is not None:
        raise ReviewError("single-turn review cannot set multi_turn_converged")
    return dict(row)


def import_review_records(
    rows: Sequence[Mapping[str, object]],
    *,
    queues: ReviewQueues,
    annotation_producers: Mapping[str, str],
) -> tuple[AcceptedReview, ...]:
    items = {
        item.analysis_session_id: item for item in (*queues.hard, *queues.ordinary)
    }
    seen: set[str] = set()
    accepted: list[AcceptedReview] = []
    for row in rows:
        analysis_session_id = row.get("analysis_session_id")
        if not isinstance(analysis_session_id, str) or not analysis_session_id:
            raise ReviewError("analysis_session_id must be non-empty")
        if analysis_session_id in seen:
            raise ReviewError("duplicate final review")
        seen.add(analysis_session_id)
        item = items.get(analysis_session_id)
        if item is None:
            raise ReviewError("review Session is not in a review queue")
        payload = _validate_review_record(row, item)
        producer_identity = annotation_producers.get(analysis_session_id)
        if not isinstance(producer_identity, str) or not producer_identity.strip():
            raise ReviewError("annotation producer identity is required")
        if str(payload["reviewer_identity"]).strip().casefold() == producer_identity.strip().casefold():
            raise ReviewError("reviewer must be independent from annotation producer")
        accepted.append(AcceptedReview(payload, item.queue_kind, item.review_stratum))
    return tuple(accepted)


def review_completion(
    queues: ReviewQueues, reviews: Sequence[AcceptedReview]
) -> dict[str, int | str | None]:
    reviewed_ids = {item.analysis_session_id for item in reviews}
    hard_ids = {item.analysis_session_id for item in queues.hard}
    ordinary_ids = {item.analysis_session_id for item in queues.ordinary}
    if not reviewed_ids <= hard_ids | ordinary_ids:
        raise ReviewError("accepted review is outside current queues")
    hard_completed = len(reviewed_ids & hard_ids)
    ordinary_completed = len(reviewed_ids & ordinary_ids)
    complete = hard_completed == len(hard_ids) and ordinary_completed == len(ordinary_ids)
    return {
        "hard_expected": len(hard_ids),
        "hard_completed": hard_completed,
        "ordinary_expected": len(ordinary_ids),
        "ordinary_completed": ordinary_completed,
        "status": "complete" if complete else "blocked",
        "block_reason": None if complete else "review_incomplete",
    }
