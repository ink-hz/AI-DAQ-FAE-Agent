from __future__ import annotations

import hashlib
import hmac
import re
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from src.agent.protocol import FINAL_OUTCOMES
from src.production_analytics.population import PopulationDecision

_KEY_VERSION = re.compile(r"^[A-Za-z0-9._-]+$")
_KINDS = {"image", "pdf", "document", "spreadsheet", "text", "code"}
_ARCHIVE_STATES = {
    "pending",
    "failed",
    "archived",
    "deletion_pending",
    "expired_unarchived",
    "deleted",
}
_THUMBNAIL_STATES = {"not_applicable", "pending", "ready", "unavailable"}
_ASSOCIATIONS = {"explicit_current_turn", "session_context"}
_DIRECTIONS = {"user_input", "agent_output"}
_SOURCE_TYPES = {"loop", "composition", "product", "user_attachment"}
_CONFIDENCE_LAYERS = {"hard_fact", "doc", "code", "session", "user_provided"}
_STAGE_NAMES = {
    "queue",
    "session_context",
    "guardrail",
    "schema_extract",
    "planner",
    "capability",
    "composition",
    "loop",
    "loop_tool",
    "reasoning_plan",
}
_STAGE_STATUSES = {"started", "completed", "failed", "error", "skipped"}
_PROTOCOL_STATUSES = {
    "complete",
    "incomplete",
    "structural_error",
    "failed",
    "error",
    "unknown",
}
_RUNTIME_FAILURE_ORDER = (
    "fallback",
    "timeout",
    "provider_error",
    "protocol_error",
    "truncated",
    "empty_answer",
    "attachment_failure",
    "other",
    "unknown",
)
_RUNTIME_FAILURES = set(_RUNTIME_FAILURE_ORDER)
_RUNTIME_FAILURE_ALIASES = {
    "fallback": "fallback",
    "timeout": "timeout",
    "max_duration": "timeout",
    "provider_error": "provider_error",
    "provider_unavailable": "provider_error",
    "provider_configuration_error": "provider_error",
    "provider_model_not_found": "provider_error",
    "provider_tool_choice_unsupported": "provider_error",
    "protocol_error": "protocol_error",
    "tool_xml_leak": "protocol_error",
    "unstructured_final": "protocol_error",
    "invalid_answer_contract": "protocol_error",
    "truncated": "truncated",
    "truncated_answer": "truncated",
    "empty_answer": "empty_answer",
    "attachment_failure": "attachment_failure",
    "budget_exhausted": "other",
    "other": "other",
    "unknown": "unknown",
}


class CanonicalDataError(ValueError):
    pass


@dataclass(frozen=True)
class AnalyticsIdentityKey:
    version: str
    key_bytes: bytes

    def __post_init__(self) -> None:
        if not _KEY_VERSION.fullmatch(self.version):
            raise ValueError("identity key version is invalid")
        if len(self.key_bytes) < 32:
            raise ValueError("identity key must contain at least 32 bytes")


@dataclass(frozen=True)
class CanonicalDataset:
    sessions: tuple[dict[str, Any], ...]
    turns: tuple[dict[str, Any], ...]


def analysis_id(source_id: str, key: AnalyticsIdentityKey) -> str:
    digest = hmac.new(key.key_bytes, source_id.encode("utf-8"), hashlib.sha256)
    return f"{key.version}:{digest.hexdigest()}"


def _governed(value: object, allowed: set[str]) -> str:
    return str(value) if isinstance(value, str) and value in allowed else "unknown"


def _media_type_group(media_type: object, kind: str) -> str:
    normalized = str(media_type).lower() if isinstance(media_type, str) else ""
    if normalized.startswith("image/"):
        return "image"
    if normalized == "application/pdf" or kind == "pdf":
        return "pdf"
    if kind == "spreadsheet" or "spreadsheet" in normalized or "excel" in normalized:
        return "spreadsheet"
    if normalized.startswith("text/") or kind == "text":
        return "text"
    if kind == "code":
        return "code"
    if kind == "document":
        return "document"
    return "other"


def _size_bucket(value: object) -> str:
    try:
        size = int(value)
    except (TypeError, ValueError):
        return "unknown"
    if size < 0:
        return "invalid"
    if size == 0:
        return "empty"
    if size < 1024 * 1024:
        return "under_1_mib"
    if size < 10 * 1024 * 1024:
        return "1_to_10_mib"
    if size < 25 * 1024 * 1024:
        return "10_to_25_mib"
    return "25_mib_or_more"


def _mapping_items(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _safe_count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))


def _runtime_failure_kind(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    return _RUNTIME_FAILURE_ALIASES.get(value, "other")


def _ordered_runtime_failures(values: set[str]) -> list[str]:
    return [item for item in _RUNTIME_FAILURE_ORDER if item in values]


def project_sources(raw: object) -> dict[str, object]:
    rows = _mapping_items(raw)
    types = Counter(_governed(row.get("type"), _SOURCE_TYPES) for row in rows)
    confidence_layers = Counter(
        _governed(row.get("confidence_layer"), _CONFIDENCE_LAYERS) for row in rows
    )
    return {
        "count": len(rows),
        "types": dict(sorted(types.items())),
        "confidence_layers": dict(sorted(confidence_layers.items())),
    }


def _stage_runtime_failures(row: Mapping[str, object]) -> set[str]:
    failures: set[str] = set()
    metadata = row.get("metadata")
    raw_kind: object = None
    if isinstance(metadata, Mapping):
        for key in ("runtime_failure_kind", "failure_kind", "error_kind"):
            if metadata.get(key) not in (None, ""):
                raw_kind = metadata.get(key)
                break
    kind = _runtime_failure_kind(raw_kind)
    if kind is not None:
        failures.add(kind)
    failed = row.get("status") in {"failed", "error"}
    transport = metadata.get("provider_transport") if isinstance(metadata, Mapping) else None
    if failed and isinstance(transport, Mapping):
        failures.add("provider_error")
    transport_projection = _transport_projection_from_mapping(transport)
    if _transport_has_protocol_failure(transport_projection):
        failures.add("protocol_error")
    if failed and not failures:
        failures.add("unknown")
    return failures


def project_stages(raw: object) -> dict[str, object]:
    rows = _mapping_items(raw)
    stages = Counter(_governed(row.get("stage"), _STAGE_NAMES) for row in rows)
    statuses = Counter(_governed(row.get("status"), _STAGE_STATUSES) for row in rows)
    failures = Counter(
        kind for row in rows for kind in _stage_runtime_failures(row)
    )
    return {
        "count": len(rows),
        "stages": dict(sorted(stages.items())),
        "statuses": dict(sorted(statuses.items())),
        "runtime_failure_kinds": dict(sorted(failures.items())),
    }


def _provider_transport(raw_done: object) -> Mapping[str, object]:
    if not isinstance(raw_done, Mapping):
        return {}
    loop = raw_done.get("loop")
    if isinstance(loop, Mapping) and isinstance(loop.get("provider_transport"), Mapping):
        return loop["provider_transport"]  # type: ignore[return-value]
    transport = raw_done.get("provider_transport")
    return transport if isinstance(transport, Mapping) else {}


def _transport_projection_from_mapping(raw: object) -> dict[str, object]:
    transport = raw if isinstance(raw, Mapping) else {}
    rounds = _mapping_items(transport.get("rounds"))
    statuses = Counter(
        _governed(status, _PROTOCOL_STATUSES)
        for row in rounds
        if (status := row.get("provider_protocol_status")) not in (None, "")
    )
    aggregate_error_kinds = transport.get("protocol_error_kinds")
    if isinstance(aggregate_error_kinds, Sequence) and not isinstance(
        aggregate_error_kinds, (str, bytes)
    ):
        error_count = len(aggregate_error_kinds)
    else:
        error_count = sum(
            isinstance(row.get("protocol_error_kind"), str)
            and bool(row.get("protocol_error_kind"))
            for row in rounds
        )
    return {
        "attempts": _safe_count(transport.get("attempts")),
        "retry_count": _safe_count(transport.get("retry_count")),
        "discarded_incomplete_attempts": _safe_count(
            transport.get("discarded_incomplete_attempts")
        ),
        "protocol_statuses": dict(sorted(statuses.items())),
        "protocol_error_kinds": {"present": error_count} if error_count else {},
    }


def _transport_projection(raw_done: object) -> dict[str, object]:
    return _transport_projection_from_mapping(_provider_transport(raw_done))


def _transport_has_protocol_failure(transport: Mapping[str, object]) -> bool:
    if int(transport["discarded_incomplete_attempts"]):
        return True
    if transport["protocol_error_kinds"]:
        return True
    statuses = transport["protocol_statuses"]
    return isinstance(statuses, Mapping) and any(
        status != "complete" and int(count) > 0 for status, count in statuses.items()
    )


def derive_runtime_failure_kinds(
    turn: Mapping[str, object],
    *,
    attachment_summary: Mapping[str, object],
) -> list[str]:
    failures: set[str] = set()
    if turn.get("fallback_used") is True:
        failures.add("fallback")
    if not str(turn.get("answer") or "").strip():
        failures.add("empty_answer")

    outcome = turn.get("outcome")
    if isinstance(outcome, str) and outcome:
        mapped = _runtime_failure_kind(outcome)
        if outcome not in FINAL_OUTCOMES:
            failures.add(mapped or "other")
        elif mapped in _RUNTIME_FAILURES and mapped not in {"other", "unknown"}:
            failures.add(mapped)
        elif outcome == "budget_exhausted":
            failures.add("other")

    for row in _mapping_items(turn.get("stages")):
        failures.update(_stage_runtime_failures(row))

    done = turn.get("done")
    if isinstance(done, Mapping):
        if done.get("fallback_used") is True:
            failures.add("fallback")
        done_outcome = done.get("outcome")
        if isinstance(done_outcome, str) and done_outcome:
            mapped = _runtime_failure_kind(done_outcome)
            if done_outcome not in FINAL_OUTCOMES:
                failures.add(mapped or "other")
            elif mapped in _RUNTIME_FAILURES and mapped not in {"other", "unknown"}:
                failures.add(mapped)
            elif done_outcome == "budget_exhausted":
                failures.add("other")
        if done.get("attachment_fallback_used") is True:
            failures.add("attachment_failure")
        for key in ("attachment_failures", "image_failures", "failed_attachment_source_ids"):
            value = done.get(key)
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and value:
                failures.add("attachment_failure")
        loop = done.get("loop")
        if isinstance(loop, Mapping) and _safe_count(loop.get("truncation_rounds")):
            failures.add("truncated")

    transport = _transport_projection(done)
    if _transport_has_protocol_failure(transport):
        failures.add("protocol_error")

    archive = attachment_summary.get("archive_statuses")
    thumbnail = attachment_summary.get("thumbnail_statuses")
    if isinstance(archive, Mapping) and any(
        int(archive.get(status) or 0) > 0
        for status in ("failed", "expired_unarchived", "deletion_pending")
    ):
        failures.add("attachment_failure")
    if isinstance(thumbnail, Mapping) and int(thumbnail.get("unavailable") or 0) > 0:
        failures.add("attachment_failure")
    return _ordered_runtime_failures(failures)


def project_done(
    raw: object,
    *,
    actual_outcome: object,
    fallback_used: bool,
    runtime_failure_kinds: Sequence[str],
) -> dict[str, object]:
    done = raw if isinstance(raw, Mapping) else {}
    loop = done.get("loop") if isinstance(done.get("loop"), Mapping) else {}
    outcome = done.get("outcome", actual_outcome)
    return {
        "outcome": _governed(outcome, set(FINAL_OUTCOMES)),
        "fallback_used": bool(done.get("fallback_used")) or fallback_used,
        "runtime_failure_kinds": list(runtime_failure_kinds),
        "truncation_rounds": _safe_count(loop.get("truncation_rounds")),
        "provider_transport": _transport_projection(done),
    }


def project_attachment(raw: Mapping[str, object]) -> dict[str, str]:
    kind = _governed(raw.get("kind"), _KINDS)
    return {
        "kind": kind,
        "media_type_group": _media_type_group(raw.get("media_type"), kind),
        "size_bucket": _size_bucket(raw.get("size_bytes")),
        "archive_status": _governed(raw.get("archive_status"), _ARCHIVE_STATES),
        "thumbnail_status": _governed(raw.get("thumbnail_status"), _THUMBNAIL_STATES),
        "association_kind": _governed(raw.get("association_kind"), _ASSOCIATIONS),
        "direction": _governed(raw.get("direction"), _DIRECTIONS),
    }


def _string(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise CanonicalDataError(f"missing_{field}")
    return value


def _timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else None
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else None
    return None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _turn_time(turn: Mapping[str, object]) -> datetime | None:
    return _timestamp(turn.get("question_at")) or _timestamp(turn.get("created_at"))


def _week_start(value: datetime | None) -> str | None:
    if value is None:
        return None
    local = value.astimezone(ZoneInfo("Asia/Shanghai"))
    monday = (local - timedelta(days=local.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return monday.isoformat()


def _deduplicate_turns(turns: Sequence[Mapping[str, object]]) -> list[Mapping[str, object]]:
    by_key: dict[tuple[str, int], Mapping[str, object]] = {}
    for turn in turns:
        external_session_id = _string(
            turn.get("external_session_id"), field="turn_external_session_id"
        )
        try:
            turn_index = int(turn.get("turn_index"))
        except (TypeError, ValueError) as exc:
            raise CanonicalDataError("invalid_turn_index") from exc
        key = (external_session_id, turn_index)
        previous = by_key.get(key)
        if previous is not None and dict(previous) != dict(turn):
            raise CanonicalDataError("conflicting_duplicate_turn")
        by_key[key] = turn
    return [by_key[key] for key in sorted(by_key)]


def _validate_relations(
    sessions: Sequence[Mapping[str, object]],
    turns: Sequence[Mapping[str, object]],
    feedback: Sequence[Mapping[str, object]],
    reviews: Sequence[Mapping[str, object]],
    attachments: Sequence[Mapping[str, object]],
) -> tuple[dict[str, Mapping[str, object]], dict[str, Mapping[str, object]]]:
    sessions_by_id: dict[str, Mapping[str, object]] = {}
    sessions_by_external: dict[str, Mapping[str, object]] = {}
    for row in sessions:
        source_id = _string(row.get("id"), field="session_id")
        external_id = _string(row.get("external_session_id"), field="external_session_id")
        if source_id in sessions_by_id or external_id in sessions_by_external:
            raise CanonicalDataError("duplicate_session")
        sessions_by_id[source_id] = row
        sessions_by_external[external_id] = row

    turns_by_id: dict[str, Mapping[str, object]] = {}
    for row in turns:
        source_id = _string(row.get("id"), field="turn_id")
        session_id = row.get("session_id")
        external_id = _string(
            row.get("external_session_id"), field="turn_external_session_id"
        )
        session = sessions_by_external.get(external_id)
        if session is None or (
            session_id is not None and str(session_id) not in sessions_by_id
        ):
            raise CanonicalDataError("orphan_turns")
        if session_id is not None and str(session.get("id")) != str(session_id):
            raise CanonicalDataError("orphan_turns")
        previous = turns_by_id.get(source_id)
        if previous is not None and dict(previous) != dict(row):
            raise CanonicalDataError("conflicting_turn_id")
        turns_by_id[source_id] = row

    for name, rows in (
        ("feedback", feedback),
        ("reviews", reviews),
        ("attachments", attachments),
    ):
        for row in rows:
            if str(row.get("turn_id") or "") not in turns_by_id:
                raise CanonicalDataError(f"orphan_{name}")
    return sessions_by_external, turns_by_id


def _feedback_projection(row: Mapping[str, object]) -> dict[str, object]:
    return {
        "rating": row.get("rating"),
        "reason_code": row.get("reason_code"),
        "comment": row.get("comment") or "",
        "created_at": row.get("created_at"),
    }


def _review_projection(row: Mapping[str, object]) -> dict[str, object]:
    return {
        "priority": row.get("priority"),
        "review_status": row.get("review_status"),
        "failure_layer": row.get("failure_layer"),
        "failure_reason": row.get("failure_reason") or "",
        "expected_answer_notes": row.get("expected_answer_notes") or "",
        "corrected_answer": row.get("corrected_answer") or "",
        "reviewer": row.get("reviewer"),
        "should_add_to_eval": bool(row.get("should_add_to_eval")),
        "should_update_knowledge": bool(row.get("should_update_knowledge")),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _metadata_attachment_evidence(metadata: object) -> tuple[int, Counter[str]]:
    if not isinstance(metadata, Mapping):
        return 0, Counter()
    raw_count = metadata.get("attachment_count")
    count = (
        int(raw_count)
        if isinstance(raw_count, int) and not isinstance(raw_count, bool) and raw_count >= 0
        else 0
    )
    raw_kinds = metadata.get("attachment_kinds")
    kinds: Counter[str] = Counter()
    if isinstance(raw_kinds, Sequence) and not isinstance(raw_kinds, (str, bytes)):
        kinds.update(_governed(item, _KINDS) for item in raw_kinds)
    if metadata.get("contains_attachment") is True:
        count = max(count, 1)
    return max(count, sum(kinds.values())), kinds


def _attachment_summary(
    items: Sequence[dict[str, str]], metadata: object = None
) -> dict[str, object]:
    archive_kinds = Counter(item["kind"] for item in items)
    metadata_count, metadata_kinds = _metadata_attachment_evidence(metadata)
    kinds = archive_kinds | metadata_kinds
    sources = []
    if items:
        sources.append("archive_records")
    if metadata_count:
        sources.append("turn_metadata")
    return {
        "count": max(len(items), metadata_count),
        "archive_record_count": len(items),
        "kinds": dict(sorted(kinds.items())),
        "archive_statuses": dict(
            sorted(Counter(item["archive_status"] for item in items).items())
        ),
        "thumbnail_statuses": dict(
            sorted(Counter(item["thumbnail_status"] for item in items).items())
        ),
        "evidence_sources": sources,
    }


def _merge_attachment_summaries(
    summaries: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    kinds: Counter[str] = Counter()
    archive_statuses: Counter[str] = Counter()
    thumbnail_statuses: Counter[str] = Counter()
    evidence_sources: set[str] = set()
    count = 0
    archive_record_count = 0
    for summary in summaries:
        count += int(summary.get("count") or 0)
        archive_record_count += int(summary.get("archive_record_count") or 0)
        for target, key in (
            (kinds, "kinds"),
            (archive_statuses, "archive_statuses"),
            (thumbnail_statuses, "thumbnail_statuses"),
        ):
            values = summary.get(key)
            if isinstance(values, Mapping):
                target.update({str(name): int(value) for name, value in values.items()})
        raw_sources = summary.get("evidence_sources")
        if isinstance(raw_sources, Sequence) and not isinstance(raw_sources, (str, bytes)):
            evidence_sources.update(str(item) for item in raw_sources)
    return {
        "count": count,
        "archive_record_count": archive_record_count,
        "kinds": dict(sorted(kinds.items())),
        "archive_statuses": dict(sorted(archive_statuses.items())),
        "thumbnail_statuses": dict(sorted(thumbnail_statuses.items())),
        "evidence_sources": [
            source
            for source in ("archive_records", "turn_metadata")
            if source in evidence_sources
        ],
    }


def build_canonical_dataset(
    *,
    sessions: Sequence[Mapping[str, object]],
    turns: Sequence[Mapping[str, object]],
    feedback: Sequence[Mapping[str, object]],
    reviews: Sequence[Mapping[str, object]],
    attachments: Sequence[Mapping[str, object]],
    population_decisions: Mapping[str, PopulationDecision],
    identity_key: AnalyticsIdentityKey,
) -> CanonicalDataset:
    deduplicated_turns = _deduplicate_turns(turns)
    sessions_by_external, _ = _validate_relations(
        sessions, deduplicated_turns, feedback, reviews, attachments
    )
    if set(sessions_by_external) != set(population_decisions):
        raise CanonicalDataError("population_decision_mismatch")

    feedback_by_turn: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    reviews_by_turn: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    attachments_by_turn: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in feedback:
        feedback_by_turn[str(row["turn_id"])].append(row)
    for row in reviews:
        reviews_by_turn[str(row["turn_id"])].append(row)
    for row in attachments:
        attachments_by_turn[str(row["turn_id"])].append(row)

    canonical_turns: list[dict[str, Any]] = []
    turns_by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for turn in deduplicated_turns:
        source_turn_id = _string(turn.get("id"), field="turn_id")
        external_session_id = _string(
            turn.get("external_session_id"), field="turn_external_session_id"
        )
        source_trace_id = _string(turn.get("trace_id"), field="trace_id")
        question_at = _turn_time(turn)
        answer_at = _timestamp(turn.get("answer_at"))
        computed_duration_ms: int | None = None
        data_errors: list[str] = []
        if question_at is not None and answer_at is not None:
            computed_duration_ms = int((answer_at - question_at).total_seconds() * 1000)
            if computed_duration_ms < 0:
                data_errors.append("negative_duration")
        runtime_duration = turn.get("duration_ms")
        if isinstance(runtime_duration, (int, float)) and runtime_duration < 0:
            if "negative_duration" not in data_errors:
                data_errors.append("negative_duration")
        feedback_events = [
            _feedback_projection(row) for row in feedback_by_turn.get(source_turn_id, [])
        ]
        attachment_items = [
            project_attachment(row) for row in attachments_by_turn.get(source_turn_id, [])
        ]
        attachment_summary = _attachment_summary(attachment_items, turn.get("metadata"))
        runtime_failure_kinds = derive_runtime_failure_kinds(
            turn, attachment_summary=attachment_summary
        )
        fallback_used = bool(turn.get("fallback_used"))
        record: dict[str, Any] = {
            "analysis_session_id": analysis_id(external_session_id, identity_key),
            "analysis_turn_id": analysis_id(source_turn_id, identity_key),
            "analysis_trace_id": analysis_id(source_trace_id, identity_key),
            "turn_index": int(turn["turn_index"]),
            "channel": turn.get("channel"),
            "question": turn.get("question") or "",
            "answer": turn.get("answer") or "",
            "question_at": _iso(question_at),
            "answer_at": _iso(answer_at),
            "runtime_duration_ms": runtime_duration,
            "computed_duration_ms": computed_duration_ms,
            "data_errors": data_errors,
            "actual_outcome": turn.get("outcome"),
            "fallback_used": fallback_used,
            "fallback_reason": turn.get("fallback_reason"),
            "runtime_failure_kinds": runtime_failure_kinds,
            "sources": project_sources(turn.get("sources")),
            "stages": project_stages(turn.get("stages")),
            "done": project_done(
                turn.get("done"),
                actual_outcome=turn.get("outcome"),
                fallback_used=fallback_used,
                runtime_failure_kinds=runtime_failure_kinds,
            ),
            "planned_capabilities": turn.get("planned_capabilities") or [],
            "capability_coverage": turn.get("capability_coverage") or {},
            "feedback_events": feedback_events,
            "feedback_good": any(item.get("rating") == "good" for item in feedback_events),
            "feedback_bad": any(item.get("rating") == "bad" for item in feedback_events),
            "reviews": [
                _review_projection(row) for row in reviews_by_turn.get(source_turn_id, [])
            ],
            "attachments": attachment_items,
            "attachment_summary": attachment_summary,
        }
        canonical_turns.append(record)
        turns_by_session[external_session_id].append(record)

    canonical_sessions: list[dict[str, Any]] = []
    for external_session_id in sorted(sessions_by_external):
        source_session = sessions_by_external[external_session_id]
        decision = population_decisions[external_session_id]
        session_turns = sorted(
            turns_by_session.get(external_session_id, []), key=lambda item: item["turn_index"]
        )
        turn_starts = [_timestamp(item.get("question_at")) for item in session_turns]
        turn_ends = [
            _timestamp(item.get("answer_at")) or _timestamp(item.get("question_at"))
            for item in session_turns
        ]
        known_starts = [item for item in turn_starts if item is not None]
        known_ends = [item for item in turn_ends if item is not None]
        started_at = min(known_starts) if known_starts else _timestamp(source_session.get("created_at"))
        ended_at = max(known_ends) if known_ends else _timestamp(source_session.get("last_active_at"))
        feedback_event_count = sum(len(item["feedback_events"]) for item in session_turns)
        canonical_sessions.append(
            {
                "analysis_session_id": analysis_id(external_session_id, identity_key),
                "channel": source_session.get("channel"),
                "started_at": _iso(started_at),
                "ended_at": _iso(ended_at),
                "week_start": _week_start(started_at),
                "turn_count": len(session_turns),
                "feedback_event_count": feedback_event_count,
                "feedback_good": any(item["feedback_good"] for item in session_turns),
                "feedback_bad": any(item["feedback_bad"] for item in session_turns),
                "feedback_affected_turn_count": sum(
                    bool(item["feedback_good"] or item["feedback_bad"]) for item in session_turns
                ),
                "population_class": decision.population_class.value,
                "exclusion_reasons": [reason.value for reason in decision.reasons],
                "population_override_applied": decision.override_applied,
                "attachment_summary": _merge_attachment_summaries(
                    [item["attachment_summary"] for item in session_turns]
                ),
            }
        )

    canonical_turns.sort(key=lambda item: (item["analysis_session_id"], item["turn_index"]))
    return CanonicalDataset(tuple(canonical_sessions), tuple(canonical_turns))
