"""字段覆盖候选的可重放人工裁决。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

import yaml

from src.facts.extract import DocumentedFieldCandidate
from src.facts.store import FactStore

OUTCOMES = {
    "fact_added",
    "conflict_recorded",
    "not_applicable",
    "audit_false_positive",
}


class FieldAdjudicationError(ValueError):
    pass


@dataclass(frozen=True)
class FieldAdjudicationDecision:
    model_id: str
    field_id: str
    source_file: str
    section: str
    line: int
    source_row: str
    outcome: str
    reason: str


class FieldAdjudicationManifest:
    def __init__(
        self,
        *,
        path: Path,
        captured_at: date,
        baseline_count: int,
        decisions: dict[tuple[str, str], FieldAdjudicationDecision],
    ):
        self.path = path
        self.captured_at = captured_at
        self.baseline_count = baseline_count
        self.decisions = dict(decisions)

    def get(self, model_id: str, field_id: str) -> FieldAdjudicationDecision:
        return self.decisions[(model_id, field_id)]

    def unadjudicated(
        self,
        findings: Iterable[dict[str, str]],
    ) -> tuple[dict[str, str], ...]:
        return tuple(
            finding
            for finding in findings
            if (finding["model"], finding["field"]) not in self.decisions
        )


def _parse_date(value: object, field: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise FieldAdjudicationError(f"{field}: invalid date {value!r}") from exc


def _candidate_key(candidate: DocumentedFieldCandidate) -> tuple[str, str, str, str, int, str]:
    return (
        candidate.model_id,
        candidate.field_id,
        candidate.source_file,
        candidate.section,
        candidate.line,
        candidate.source_row,
    )


def load_field_adjudications(
    path: Path,
    *,
    candidates: Iterable[DocumentedFieldCandidate],
    store: FactStore,
) -> FieldAdjudicationManifest:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError as exc:
        raise FieldAdjudicationError(f"field adjudications are missing: {path}") from exc
    if data.get("version") != 1:
        raise FieldAdjudicationError(f"{path}: unsupported version")
    baseline = data.get("baseline") or {}
    if not isinstance(baseline, dict):
        raise FieldAdjudicationError(f"{path}: baseline must be a mapping")
    captured_at = _parse_date(baseline.get("captured_at"), "baseline.captured_at")
    try:
        baseline_count = int(baseline.get("documented_without_fact"))
    except (TypeError, ValueError) as exc:
        raise FieldAdjudicationError(
            "baseline.documented_without_fact must be an integer"
        ) from exc
    if baseline_count < 0:
        raise FieldAdjudicationError(
            "baseline.documented_without_fact must not be negative"
        )

    candidates_by_key = {
        _candidate_key(candidate): candidate for candidate in candidates
    }
    candidate_keys = set(candidates_by_key)
    decisions: dict[tuple[str, str], FieldAdjudicationDecision] = {}
    rows = data.get("decisions") or []
    if not isinstance(rows, list):
        raise FieldAdjudicationError(f"{path}: decisions must be a list")
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise FieldAdjudicationError(f"decisions[{index}] must be a mapping")
        model_id = str(raw.get("model") or "")
        field_id = str(raw.get("field") or "")
        key = (model_id, field_id)
        if not all(key):
            raise FieldAdjudicationError(f"decisions[{index}]: model and field required")
        if key in decisions:
            raise FieldAdjudicationError(
                f"duplicate adjudication decision: {model_id}/{field_id}"
            )
        outcome = str(raw.get("outcome") or "")
        if outcome not in OUTCOMES:
            raise FieldAdjudicationError(
                f"{model_id}/{field_id}: unknown outcome {outcome!r}"
            )
        reason = str(raw.get("reason") or "").strip()
        if not reason:
            raise FieldAdjudicationError(f"{model_id}/{field_id}: reason is required")
        source = raw.get("source") or {}
        if not isinstance(source, dict):
            raise FieldAdjudicationError(
                f"{model_id}/{field_id}: source must be a mapping"
            )
        source_file = str(source.get("file") or "")
        section = str(source.get("section") or "")
        source_row = str(source.get("source_row") or "")
        try:
            line = int(source.get("line"))
        except (TypeError, ValueError) as exc:
            raise FieldAdjudicationError(
                f"{model_id}/{field_id}: source line must be an integer"
            ) from exc
        source_key = (model_id, field_id, source_file, section, line, source_row)
        if source_key not in candidate_keys:
            raise FieldAdjudicationError(
                f"{model_id}/{field_id}: decision does not match a source candidate"
            )

        result = store.get_spec(model_id, field_id)
        if outcome == "fact_added" and (
            result.status != "found" or result.row.status == "conflict"
        ):
            raise FieldAdjudicationError(
                f"{model_id}/{field_id}: fact_added requires a non-conflict fact"
            )
        if outcome == "conflict_recorded" and (
            result.status != "found" or result.row.status != "conflict"
        ):
            raise FieldAdjudicationError(
                f"{model_id}/{field_id}: conflict_recorded requires a conflict fact"
            )
        if outcome in {"fact_added", "conflict_recorded"}:
            fact_source = result.row.source
            if (
                str(fact_source.get("file") or "") != source_file
                or str(fact_source.get("section") or "") != section
                or (
                    outcome == "fact_added"
                    and result.row.raw_value
                    != candidates_by_key[source_key].raw_value
                )
            ):
                raise FieldAdjudicationError(
                    f"{model_id}/{field_id}: fact source/raw_value does not match "
                    "the adjudicated candidate"
                )
        decisions[key] = FieldAdjudicationDecision(
            model_id=model_id,
            field_id=field_id,
            source_file=source_file,
            section=section,
            line=line,
            source_row=source_row,
            outcome=outcome,
            reason=reason,
        )
    return FieldAdjudicationManifest(
        path=path,
        captured_at=captured_at,
        baseline_count=baseline_count,
        decisions=decisions,
    )
