from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.production_analytics.taxonomy import AnalyticsTaxonomy

_HEX_64 = re.compile(r"^[0-9a-f]{64}$")
_SESSION_KEYS = {
    "analysis_session_id",
    "taxonomy_version",
    "input_hash",
    "intent_capabilities",
    "product_families",
    "canonical_models",
    "unresolved_model_mentions",
    "application_scenarios",
    "technical_objects",
    "complexity_level",
    "resolution_status",
    "product_signals",
    "human_value_class",
    "workflow_failure_layers",
    "semantic_confidence",
    "evidence_turn_indexes",
    "internal_reason",
    "turn_annotations",
    "annotator_kind",
    "annotator_identity",
    "annotator_version",
    "annotated_at",
}
_TURN_KEYS = {
    "turn_index",
    "question_kind",
    "context_dependency",
    "runtime_failure_kind",
    "answer_quality",
    "failure_layer",
    "secondary_layers",
    "user_correction_signal",
    "followup_reason",
    "attachment_dependency",
    "semantic_confidence",
    "evidence_turn_indexes",
    "internal_reason",
}
_QUESTION_KINDS = {
    "technical_question",
    "selection_request",
    "troubleshooting_request",
    "context_followup",
    "clarification",
    "business_or_boundary",
    "other",
    "unknown",
}
_CONTEXT_DEPENDENCIES = {"standalone", "depends_on_prior_turn", "unknown"}
_RUNTIME_FAILURE_KINDS = {
    "none",
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
_ANSWER_QUALITIES = {
    "correct_and_helpful",
    "partially_correct",
    "incorrect",
    "clarification_only",
    "correct_refusal",
    "incorrect_refusal",
    "failed",
    "insufficient_review_evidence",
}
_FOLLOWUP_REASONS = {
    "none",
    "request_detail",
    "request_location",
    "user_correction",
    "unresolved_previous_turn",
    "new_topic",
    "other",
    "unknown",
}
_ATTACHMENT_DEPENDENCIES = {"none", "supplemental", "required", "unknown"}
_ANNOTATOR_KINDS = {"codex", "human_fae", "governed_model"}


class AnnotationError(ValueError):
    pass


@dataclass(frozen=True)
class SessionAnnotation:
    payload: dict[str, Any]

    @property
    def analysis_session_id(self) -> str:
        return str(self.payload["analysis_session_id"])

    @property
    def taxonomy_version(self) -> str:
        return str(self.payload["taxonomy_version"])

    @property
    def input_hash(self) -> str:
        return str(self.payload["input_hash"])


@dataclass(frozen=True)
class AnnotationBatch:
    batch_id: str
    rows: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class AnnotationBatchPlan:
    batches: tuple[AnnotationBatch, ...]
    skipped_count: int
    resume_count: int


def _exact_keys(row: Mapping[str, object], expected: set[str], label: str) -> None:
    unknown = set(row) - expected
    missing = expected - set(row)
    if unknown:
        raise AnnotationError(f"unknown key in {label}: {sorted(unknown)}")
    if missing:
        raise AnnotationError(f"missing key in {label}: {sorted(missing)}")


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise AnnotationError(f"{label} must be a non-empty string")
    return value


def _string_list(value: object, label: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise AnnotationError(f"{label} must be an array of non-empty strings")
    if len(value) != len(set(value)):
        raise AnnotationError(f"{label} contains duplicates")
    return list(value)


def _governed_list(value: object, allowed: Sequence[str], label: str) -> list[str]:
    result = _string_list(value, label)
    invalid = set(result) - set(allowed)
    if invalid:
        raise AnnotationError(f"{label} contains invalid values: {sorted(invalid)}")
    return result


def _confidence(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AnnotationError("confidence must be numeric")
    result = float(value)
    if not 0.0 <= result <= 1.0:
        raise AnnotationError("confidence must be between 0 and 1")
    return result


def _turn_indexes(value: object, valid: set[int]) -> list[int]:
    if not isinstance(value, list) or any(isinstance(item, bool) or not isinstance(item, int) for item in value):
        raise AnnotationError("evidence turn indexes must be integers")
    if len(value) != len(set(value)):
        raise AnnotationError("evidence turn indexes contain duplicates")
    if not set(value) <= valid:
        raise AnnotationError("evidence turn index is not in the Session")
    return list(value)


def _parse_turn_annotation(
    row: object, *, taxonomy: AnalyticsTaxonomy, valid_turn_indexes: set[int]
) -> dict[str, Any]:
    if not isinstance(row, Mapping):
        raise AnnotationError("turn annotation must be an object")
    _exact_keys(row, _TURN_KEYS, "turn annotation")
    turn_index = row["turn_index"]
    if isinstance(turn_index, bool) or not isinstance(turn_index, int) or turn_index not in valid_turn_indexes:
        raise AnnotationError("turn annotation index is invalid")
    question_kind = _string(row["question_kind"], "question_kind")
    if question_kind not in _QUESTION_KINDS:
        raise AnnotationError("question_kind is invalid")
    context_dependency = _string(row["context_dependency"], "context_dependency")
    if context_dependency not in _CONTEXT_DEPENDENCIES:
        raise AnnotationError("context_dependency is invalid")
    runtime_failure_kind = _string(row["runtime_failure_kind"], "runtime_failure_kind")
    if runtime_failure_kind not in _RUNTIME_FAILURE_KINDS:
        raise AnnotationError("runtime_failure_kind is invalid")
    answer_quality = _string(row["answer_quality"], "answer_quality")
    if answer_quality not in _ANSWER_QUALITIES:
        raise AnnotationError("answer_quality is invalid")
    failure_layer = row["failure_layer"]
    if failure_layer is not None and failure_layer not in taxonomy.failure_layers:
        raise AnnotationError("failure_layer is invalid")
    secondary = _governed_list(
        row["secondary_layers"], taxonomy.failure_layers, "secondary_layers"
    )
    if failure_layer in secondary:
        raise AnnotationError("primary failure_layer is duplicated in secondary_layers")
    if not isinstance(row["user_correction_signal"], bool):
        raise AnnotationError("user_correction_signal must be boolean")
    followup_reason = _string(row["followup_reason"], "followup_reason")
    if followup_reason not in _FOLLOWUP_REASONS:
        raise AnnotationError("followup_reason is invalid")
    dependency = _string(row["attachment_dependency"], "attachment_dependency")
    if dependency not in _ATTACHMENT_DEPENDENCIES:
        raise AnnotationError("attachment_dependency is invalid")
    result = dict(row)
    result["semantic_confidence"] = _confidence(row["semantic_confidence"])
    result["evidence_turn_indexes"] = _turn_indexes(
        row["evidence_turn_indexes"], valid_turn_indexes
    )
    _string(row["internal_reason"], "internal_reason")
    return result


def parse_session_annotation(
    row: Mapping[str, object],
    *,
    taxonomy: AnalyticsTaxonomy,
    valid_turn_indexes: set[int],
    known_model_ids: set[str],
) -> SessionAnnotation:
    _exact_keys(row, _SESSION_KEYS, "session annotation")
    _string(row["analysis_session_id"], "analysis_session_id")
    if row["taxonomy_version"] != taxonomy.taxonomy_version:
        raise AnnotationError("taxonomy version mismatch")
    input_hash = _string(row["input_hash"], "input_hash")
    if not _HEX_64.fullmatch(input_hash):
        raise AnnotationError("input hash must be 64 lowercase hex characters")
    _governed_list(
        row["intent_capabilities"], taxonomy.intent_capabilities, "intent_capabilities"
    )
    _string_list(row["product_families"], "product_families")
    models = _string_list(row["canonical_models"], "canonical_models")
    if not set(models) <= known_model_ids:
        raise AnnotationError("canonical model is not in the governed resolver catalog")
    _string_list(row["unresolved_model_mentions"], "unresolved_model_mentions")
    _governed_list(
        row["application_scenarios"], taxonomy.scenario_categories, "application_scenarios"
    )
    _governed_list(row["technical_objects"], taxonomy.technical_objects, "technical_objects")
    if row["complexity_level"] not in taxonomy.complexity_levels:
        raise AnnotationError("complexity_level is invalid")
    if row["resolution_status"] not in taxonomy.resolution_statuses:
        raise AnnotationError("resolution_status is invalid")
    _governed_list(row["product_signals"], taxonomy.product_signals, "product_signals")
    if row["human_value_class"] not in taxonomy.human_value_classes:
        raise AnnotationError("human_value_class is invalid")
    _governed_list(
        row["workflow_failure_layers"], taxonomy.failure_layers, "workflow_failure_layers"
    )
    _confidence(row["semantic_confidence"])
    _turn_indexes(row["evidence_turn_indexes"], valid_turn_indexes)
    _string(row["internal_reason"], "internal_reason")
    turn_rows = row["turn_annotations"]
    if not isinstance(turn_rows, list):
        raise AnnotationError("turn_annotations must be an array")
    parsed_turns = [
        _parse_turn_annotation(item, taxonomy=taxonomy, valid_turn_indexes=valid_turn_indexes)
        for item in turn_rows
    ]
    turn_indexes = [item["turn_index"] for item in parsed_turns]
    if len(turn_indexes) != len(set(turn_indexes)) or set(turn_indexes) != valid_turn_indexes:
        raise AnnotationError("turn_annotations must cover every Session turn exactly once")
    annotator_kind = _string(row["annotator_kind"], "annotator_kind")
    if annotator_kind not in _ANNOTATOR_KINDS:
        raise AnnotationError("annotator_kind is invalid")
    _string(row["annotator_identity"], "annotator_identity")
    _string(row["annotator_version"], "annotator_version")
    annotated_at = _string(row["annotated_at"], "annotated_at")
    try:
        timestamp = datetime.fromisoformat(annotated_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AnnotationError("annotated_at is invalid") from exc
    if timestamp.tzinfo is None:
        raise AnnotationError("annotated_at must be timezone-aware")
    payload = dict(row)
    payload["turn_annotations"] = parsed_turns
    return SessionAnnotation(payload)


def _annotation_input(session: Mapping[str, object], turns: Sequence[Mapping[str, object]], taxonomy_version: str) -> dict[str, Any]:
    session_keys = (
        "analysis_session_id",
        "channel",
        "started_at",
        "ended_at",
        "week_start",
        "turn_count",
        "feedback_event_count",
        "feedback_good",
        "feedback_bad",
        "feedback_affected_turn_count",
        "attachment_summary",
    )
    turn_keys = (
        "turn_index",
        "question",
        "answer",
        "question_at",
        "answer_at",
        "runtime_duration_ms",
        "computed_duration_ms",
        "data_errors",
        "actual_outcome",
        "fallback_used",
        "fallback_reason",
        "planned_capabilities",
        "capability_coverage",
        "attachment_summary",
        "feedback_good",
        "feedback_bad",
    )
    return {
        "analysis_session_id": session["analysis_session_id"],
        "taxonomy_version": taxonomy_version,
        "session": {key: session.get(key) for key in session_keys},
        "turns": [
            {key: turn.get(key) for key in turn_keys}
            for turn in sorted(turns, key=lambda item: int(item["turn_index"]))
        ],
    }


def _hash_payload(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _existing_key(row: Mapping[str, object] | SessionAnnotation) -> tuple[str, str, str]:
    if isinstance(row, SessionAnnotation):
        return row.analysis_session_id, row.taxonomy_version, row.input_hash
    return (
        str(row.get("analysis_session_id") or ""),
        str(row.get("taxonomy_version") or ""),
        str(row.get("input_hash") or ""),
    )


def build_annotation_batches(
    sessions: Sequence[Mapping[str, object]],
    turns: Sequence[Mapping[str, object]],
    taxonomy_version: str,
    batch_size: int = 25,
    existing_annotations: Sequence[Mapping[str, object] | SessionAnnotation] = (),
) -> AnnotationBatchPlan:
    if batch_size <= 0:
        raise AnnotationError("batch_size must be positive")
    existing: dict[str, tuple[str, str]] = {}
    for row in existing_annotations:
        analysis_session_id, version, input_hash = _existing_key(row)
        if not analysis_session_id or analysis_session_id in existing:
            raise AnnotationError("duplicate or invalid existing annotation")
        existing[analysis_session_id] = (version, input_hash)
    turns_by_session: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for turn in turns:
        turns_by_session[str(turn.get("analysis_session_id") or "")].append(turn)
    rows: list[dict[str, Any]] = []
    skipped = 0
    resumed = 0
    for session in sorted(sessions, key=lambda item: str(item.get("analysis_session_id") or "")):
        if session.get("population_class") != "included_production":
            continue
        analysis_session_id = _string(session.get("analysis_session_id"), "analysis_session_id")
        payload = _annotation_input(
            session, turns_by_session.get(analysis_session_id, []), taxonomy_version
        )
        input_hash = _hash_payload(payload)
        payload["input_hash"] = input_hash
        previous = existing.get(analysis_session_id)
        if previous == (taxonomy_version, input_hash):
            skipped += 1
            continue
        if previous is not None:
            resumed += 1
        rows.append(payload)
    batches = tuple(
        AnnotationBatch(f"batch-{index // batch_size + 1:03d}", tuple(rows[index : index + batch_size]))
        for index in range(0, len(rows), batch_size)
    )
    return AnnotationBatchPlan(batches, skipped, resumed)


def load_annotation_results(
    rows: Sequence[Mapping[str, object]],
    *,
    expected: Mapping[str, tuple[str, set[int]]],
    taxonomy: AnalyticsTaxonomy,
    known_model_ids: set[str],
) -> tuple[SessionAnnotation, ...]:
    seen: set[str] = set()
    annotations: list[SessionAnnotation] = []
    for row in rows:
        analysis_session_id = _string(row.get("analysis_session_id"), "analysis_session_id")
        if analysis_session_id in seen:
            raise AnnotationError("duplicate annotation result")
        seen.add(analysis_session_id)
        expected_item = expected.get(analysis_session_id)
        if expected_item is None:
            raise AnnotationError("annotation analysis_session_id is not expected")
        expected_hash, valid_turn_indexes = expected_item
        if row.get("taxonomy_version") != taxonomy.taxonomy_version:
            raise AnnotationError("taxonomy version mismatch")
        if row.get("input_hash") != expected_hash:
            raise AnnotationError("input hash mismatch")
        annotations.append(
            parse_session_annotation(
                row,
                taxonomy=taxonomy,
                valid_turn_indexes=valid_turn_indexes,
                known_model_ids=known_model_ids,
            )
        )
    return tuple(annotations)


def annotation_completion(
    *, expected_analysis_ids: set[str], completed_analysis_ids: set[str]
) -> dict[str, float | int]:
    if not completed_analysis_ids <= expected_analysis_ids:
        raise AnnotationError("completed annotation contains unexpected analysis ID")
    expected = len(expected_analysis_ids)
    completed = len(completed_analysis_ids)
    return {
        "expected": expected,
        "completed": completed,
        "missing": expected - completed,
        "completion_rate": completed / expected if expected else 1.0,
    }
