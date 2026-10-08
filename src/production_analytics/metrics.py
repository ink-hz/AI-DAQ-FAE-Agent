from __future__ import annotations

import hashlib
import math
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from src.production_analytics.annotation import SessionAnnotation
from src.production_analytics.contracts import PopulationClass
from src.production_analytics.population import PopulationDecision


@dataclass(frozen=True)
class Metric:
    value: Any
    numerator: int | float | None = None
    denominator: int | float | None = None
    filters: tuple[str, ...] = ()
    method: str = "exact_count"
    interval: tuple[float, float] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "filters": list(self.filters),
            "method": self.method,
            "interval": list(self.interval) if self.interval is not None else None,
        }


@dataclass(frozen=True)
class AnalyticsMetricInputs:
    sessions: Sequence[Mapping[str, object]]
    turns: Sequence[Mapping[str, object]]
    annotations: Sequence[Mapping[str, object] | SessionAnnotation] = ()
    accepted_reviews: Sequence[Mapping[str, object]] = ()
    canonical_issue_by_turn: Mapping[str, str] | None = None
    potential_conversion_session_ids: frozenset[str] = frozenset()
    review_stratum_population: Mapping[str, int] | None = None


def _public_count_distribution(counts: Mapping[str, int]) -> dict[str, int | str]:
    return {
        key: value if value >= 5 else "少于 5"
        for key, value in counts.items()
    }


def distribution_cell_metric_id(metric_id: str, label: str) -> str:
    digest = hashlib.sha256(label.encode("utf-8")).hexdigest()[:16]
    return f"{metric_id}.cell.{digest}"


def wilson_interval(
    successes: int, total: int, z: float = 1.959963984540054
) -> tuple[float, float] | None:
    if total == 0:
        return None
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    radius = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
    return max(0.0, center - radius), min(1.0, center + radius)


def _rate(
    numerator: int,
    denominator: int,
    *,
    method: str = "exact_population_rate",
    filters: tuple[str, ...] = (),
) -> Metric:
    return Metric(
        numerator / denominator if denominator else None,
        numerator=numerator,
        denominator=denominator,
        filters=filters,
        method=method,
        interval=wilson_interval(numerator, denominator),
    )


def compute_population_metrics(
    decisions: Sequence[PopulationDecision],
) -> dict[str, Metric]:
    counts = Counter(item.population_class for item in decisions)
    raw = len(decisions)
    included = counts[PopulationClass.INCLUDED]
    excluded = counts[PopulationClass.EXCLUDED]
    uncertain = counts[PopulationClass.UNCERTAIN]
    if raw != included + excluded + uncertain:
        raise ValueError("population denominator does not close")
    return {
        "population.raw_sessions": Metric(raw),
        "population.included": Metric(included, filters=("included_production",)),
        "population.excluded": Metric(excluded, filters=("excluded_non_production",)),
        "population.uncertain": Metric(uncertain, filters=("uncertain_population",)),
        "population.uncertain_rate": _rate(uncertain, raw),
    }


def _payload(annotation: Mapping[str, object] | SessionAnnotation) -> Mapping[str, object]:
    return annotation.payload if isinstance(annotation, SessionAnnotation) else annotation


def _percentile(values: Sequence[int | float], quantile: float) -> int | float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        result = ordered[lower]
    else:
        weight = position - lower
        result = ordered[lower] * (1 - weight) + ordered[upper] * weight
    stable = round(float(result), 10)
    return int(stable) if stable.is_integer() else stable


def _latency_summary(values: Sequence[int | float]) -> dict[str, int | float | None]:
    return {
        "p50": _percentile(values, 0.50),
        "p90": _percentile(values, 0.90),
        "p95": _percentile(values, 0.95),
    }


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


def _non_work_hour(value: object) -> bool:
    timestamp = _timestamp(value)
    if timestamp is None:
        return False
    local = timestamp.astimezone(ZoneInfo("Asia/Shanghai"))
    return local.weekday() >= 5 or local.hour < 9 or local.hour >= 18


def _attachment_failed(summary: object) -> bool:
    if not isinstance(summary, Mapping):
        return False
    archive = summary.get("archive_statuses")
    thumbnails = summary.get("thumbnail_statuses")
    failed_archive = False
    failed_thumbnail = False
    if isinstance(archive, Mapping):
        failed_archive = any(
            int(archive.get(state) or 0) > 0
            for state in ("failed", "expired_unarchived", "deletion_pending")
        )
    if isinstance(thumbnails, Mapping):
        failed_thumbnail = int(thumbnails.get("unavailable") or 0) > 0
    return failed_archive or failed_thumbnail


def _counter_from_annotations(
    annotations: Sequence[Mapping[str, object]], field: str
) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for annotation in annotations:
        value = annotation.get(field)
        if isinstance(value, list):
            counter.update(str(item) for item in value)
        elif isinstance(value, str) and value:
            counter[value] += 1
    return dict(sorted(counter.items()))


def compute_metrics(inputs: AnalyticsMetricInputs) -> dict[str, Metric]:
    sessions = [
        row for row in inputs.sessions if row.get("population_class") == "included_production"
    ]
    included_ids = {str(row.get("analysis_session_id") or "") for row in sessions}
    turns = [row for row in inputs.turns if str(row.get("analysis_session_id") or "") in included_ids]
    annotations = [
        dict(_payload(row))
        for row in inputs.annotations
        if str(_payload(row).get("analysis_session_id") or "") in included_ids
    ]
    annotation_by_session = {
        str(row["analysis_session_id"]): row for row in annotations
    }
    reviews = [
        row
        for row in inputs.accepted_reviews
        if str(row.get("analysis_session_id") or "") in included_ids
    ]
    review_by_session = {str(row["analysis_session_id"]): row for row in reviews}
    metrics: dict[str, Metric] = {}

    total_turns = len(turns)
    total_sessions = len(sessions)
    bad_events = sum(
        1
        for turn in turns
        for event in turn.get("feedback_events", [])
        if isinstance(event, Mapping) and event.get("rating") == "bad"
    )
    bad_turn_ids = {
        str(turn.get("analysis_turn_id") or "")
        for turn in turns
        if turn.get("feedback_bad") is True
    }
    bad_session_ids = {
        str(turn.get("analysis_session_id") or "")
        for turn in turns
        if turn.get("feedback_bad") is True
    }
    issue_map = inputs.canonical_issue_by_turn or {}
    canonical_issues = {issue_map[turn_id] for turn_id in bad_turn_ids if turn_id in issue_map}
    metrics.update(
        {
            "feedback.bad_events": Metric(bad_events),
            "feedback.bad_affected_turns": Metric(len(bad_turn_ids)),
            "feedback.bad_affected_sessions": Metric(len(bad_session_ids)),
            "feedback.canonical_issues": Metric(len(canonical_issues)),
            "feedback.unclustered_bad_turns": Metric(
                len(bad_turn_ids - set(issue_map)), method="exact_unclustered_count"
            ),
            "feedback.all_bad_turns_clustered": Metric(
                not (bad_turn_ids - set(issue_map)), method="exact_coverage_state"
            ),
            "feedback.bad_affected_turn_rate": _rate(len(bad_turn_ids), total_turns),
            "feedback.bad_affected_session_rate": _rate(
                len(bad_session_ids), total_sessions
            ),
        }
    )

    durations: list[int | float] = []
    missing_duration = 0
    invalid_duration = 0
    durations_by_complexity: dict[str, list[int | float]] = defaultdict(list)
    for turn in turns:
        duration = turn.get("runtime_duration_ms")
        if not isinstance(duration, (int, float)) or isinstance(duration, bool):
            missing_duration += 1
            continue
        if duration < 0:
            invalid_duration += 1
            continue
        durations.append(duration)
        annotation = annotation_by_session.get(str(turn.get("analysis_session_id") or ""), {})
        complexity = str(annotation.get("complexity_level") or "unknown")
        durations_by_complexity[complexity].append(duration)
    metrics["latency.overall_ms"] = Metric(
        _latency_summary(durations), denominator=len(durations), method="linear_percentile"
    )
    metrics["latency.missing_duration_turns"] = Metric(missing_duration)
    metrics["latency.invalid_duration_turns"] = Metric(invalid_duration)
    for complexity in (
        "L1_single_fact",
        "L2_composed",
        "L3_solution_or_diagnosis",
        "unknown",
    ):
        values = durations_by_complexity.get(complexity, [])
        metrics[f"latency.{complexity}_ms"] = Metric(
            _latency_summary(values),
            denominator=len(values),
            filters=(f"complexity={complexity}",),
            method="linear_percentile",
        )

    fallback_turns = sum(turn.get("fallback_used") is True for turn in turns)
    outcome_counts = Counter(str(turn.get("actual_outcome") or "unavailable") for turn in turns)
    attachment_failure_sessions = sum(
        _attachment_failed(session.get("attachment_summary")) for session in sessions
    )
    metrics["reliability.fallback_turns"] = Metric(fallback_turns)
    metrics["reliability.fallback_turn_rate"] = _rate(fallback_turns, total_turns)
    metrics["reliability.outcome_counts"] = Metric(dict(sorted(outcome_counts.items())))
    metrics["reliability.attachment_failure_sessions"] = Metric(attachment_failure_sessions)

    weekly = Counter(str(session.get("week_start") or "unavailable") for session in sessions)
    metrics["usage.weekly_sessions"] = Metric(dict(sorted(weekly.items())))
    metrics["usage.included_turns"] = Metric(total_turns)

    resolution_counts = Counter(
        str(annotation.get("resolution_status") or "unavailable") for annotation in annotations
    )
    for status, count in sorted(resolution_counts.items()):
        metrics[f"annotation.resolution_status.{status}"] = Metric(
            count,
            denominator=len(annotations),
            method="governed_annotation_not_final_quality",
        )
    reviewed_fully = sum(
        review.get("resolution_status") == "fully_resolved" for review in reviews
    )
    reviewed_judgment_counts = Counter(
        str(review.get("quality_judgment") or "unavailable") for review in reviews
    )
    reviewed_resolution_counts = Counter(
        str(review.get("resolution_status") or "unavailable") for review in reviews
    )
    annotation_agreements = sum(
        review.get("annotation_agreement") is True for review in reviews
    )
    metrics["quality.reviewed_count"] = Metric(
        len(reviews), method="accepted_review_only"
    )
    metrics["quality.reviewed_judgment_counts"] = Metric(
        dict(sorted(reviewed_judgment_counts.items())),
        denominator=len(reviews),
        method="accepted_review_only",
    )
    metrics["quality.reviewed_judgment_counts_public"] = Metric(
        _public_count_distribution(dict(sorted(reviewed_judgment_counts.items()))),
        denominator=len(reviews),
        method="accepted_review_only_small_cells_suppressed",
    )
    for label, count in reviewed_judgment_counts.items():
        metrics[
            distribution_cell_metric_id("quality.reviewed_judgment_counts", label)
        ] = Metric(count, denominator=len(reviews), method="accepted_review_count_cell")
    metrics["quality.reviewed_resolution_counts"] = Metric(
        dict(sorted(reviewed_resolution_counts.items())),
        denominator=len(reviews),
        method="accepted_review_only",
    )
    metrics["quality.reviewed_resolution_counts_public"] = Metric(
        _public_count_distribution(dict(sorted(reviewed_resolution_counts.items()))),
        denominator=len(reviews),
        method="accepted_review_only_small_cells_suppressed",
    )
    for label, count in reviewed_resolution_counts.items():
        metrics[
            distribution_cell_metric_id("quality.reviewed_resolution_counts", label)
        ] = Metric(count, denominator=len(reviews), method="accepted_review_count_cell")
    metrics["quality.reviewed_annotation_agreement_rate"] = _rate(
        annotation_agreements,
        len(reviews),
        method="accepted_review_only",
    )
    metrics["quality.reviewed_fully_resolved_rate"] = _rate(
        reviewed_fully,
        len(reviews),
        method="accepted_review_only",
        filters=("accepted_review",),
    )
    if len(review_by_session) == total_sessions and total_sessions:
        metrics["quality.all_sessions_fully_resolved_rate"] = _rate(
            reviewed_fully,
            total_sessions,
            method="full_accepted_review",
            filters=("all_included_sessions_reviewed",),
        )
    elif inputs.review_stratum_population:
        declared = dict(inputs.review_stratum_population)
        invalid_population = any(
            not isinstance(size, int) or isinstance(size, bool) or size <= 0
            for size in declared.values()
        )
        reviews_by_stratum: dict[str, list[Mapping[str, object]]] = defaultdict(list)
        for review in reviews:
            stratum = review.get("review_stratum")
            if isinstance(stratum, str) and stratum:
                reviews_by_stratum[stratum].append(review)
        missing_strata = sorted(set(declared) - set(reviews_by_stratum))
        unexpected_strata = sorted(set(reviews_by_stratum) - set(declared))
        if (
            not invalid_population
            and sum(declared.values()) == total_sessions
            and not missing_strata
            and not unexpected_strata
        ):
            estimate = 0.0
            lower = 0.0
            upper = 0.0
            for stratum, population in declared.items():
                stratum_reviews = reviews_by_stratum[stratum]
                successes = sum(
                    review.get("resolution_status") == "fully_resolved"
                    for review in stratum_reviews
                )
                weight = population / total_sessions
                estimate += weight * successes / len(stratum_reviews)
                interval = wilson_interval(successes, len(stratum_reviews))
                if interval is not None:
                    lower += weight * interval[0]
                    upper += weight * interval[1]
            metrics["quality.stratified_fully_resolved_estimate"] = Metric(
                estimate,
                numerator=reviewed_fully,
                denominator=len(reviews),
                filters=("accepted_stratified_review",),
                method="stratified_accepted_review_estimate",
                interval=(lower, upper),
            )
        else:
            metrics["quality.stratified_estimate_unavailable_strata"] = Metric(
                {
                    "invalid_population": invalid_population,
                    "population_total": sum(declared.values()),
                    "expected_total": total_sessions,
                    "missing": missing_strata,
                    "unexpected": unexpected_strata,
                },
                method="validation_result",
            )
    first_turn_values = [
        bool(review["first_turn_resolved"])
        for review in reviews
        if review.get("first_turn_resolved") is not None
    ]
    metrics["quality.reviewed_first_turn_resolution_rate"] = _rate(
        sum(first_turn_values),
        len(first_turn_values),
        method="accepted_review_only",
    )
    multi_turn_values = [
        bool(review["multi_turn_converged"])
        for review in reviews
        if review.get("multi_turn_converged") is not None
    ]
    metrics["quality.reviewed_multiturn_convergence_rate"] = _rate(
        sum(multi_turn_values),
        len(multi_turn_values),
        method="accepted_review_only",
    )

    field_metrics = {
        "product.family_counts": "product_families",
        "product.model_counts": "canonical_models",
        "product.scenario_counts": "application_scenarios",
        "product.technical_object_counts": "technical_objects",
        "product.signal_counts": "product_signals",
        "demand.intent_capability_counts": "intent_capabilities",
        "workflow.failure_layer_counts": "workflow_failure_layers",
    }
    for metric_id, field in field_metrics.items():
        counts = _counter_from_annotations(annotations, field)
        metrics[metric_id] = Metric(
            counts,
            denominator=len(annotations),
            method="governed_annotation_distribution",
        )
        metrics[f"{metric_id}_public"] = Metric(
            _public_count_distribution(counts),
            denominator=len(annotations),
            method="governed_annotation_distribution_small_cells_suppressed",
        )
        for label, count in counts.items():
            metrics[distribution_cell_metric_id(metric_id, label)] = Metric(
                count,
                denominator=len(annotations),
                method="governed_annotation_distribution_count_cell",
            )

    non_work_hours = sum(_non_work_hour(session.get("started_at")) for session in sessions)
    multiturn = sum(int(session.get("turn_count") or 0) > 1 for session in sessions)
    attachment_sessions = sum(
        int((session.get("attachment_summary") or {}).get("count") or 0) > 0
        for session in sessions
        if isinstance(session.get("attachment_summary"), Mapping)
    )
    assisted_reviewed = sum(
        annotation_by_session.get(session_id, {}).get("human_value_class") == "fae_assisted"
        and review.get("business_case_approved") is True
        for session_id, review in review_by_session.items()
    )
    metrics.update(
        {
            "value.observed_included_sessions": Metric(total_sessions),
            "value.observed_included_turns": Metric(total_turns),
            "value.observed_non_work_hour_sessions": Metric(non_work_hours),
            "value.observed_multiturn_sessions": Metric(multiturn),
            "value.observed_attachment_sessions": Metric(attachment_sessions),
            "value.assisted_reviewed_sessions": Metric(
                assisted_reviewed,
                method="accepted_review_and_annotation",
                filters=("business_case_approved",),
            ),
            "value.scenario_potential_conversion_sessions": Metric(
                len(inputs.potential_conversion_session_ids & included_ids),
                method="reviewed_scenario_population",
                filters=("potential_not_observed",),
            ),
        }
    )
    return metrics
