from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.production_analytics.contracts import ExclusionReason, PopulationClass


class PopulationRegistryError(ValueError):
    pass


@dataclass(frozen=True)
class ReviewedOverride:
    target_kind: str
    target_id: str
    population_class: PopulationClass
    reviewer: str
    reason: str
    created_at: str
    source_evidence: str


@dataclass(frozen=True)
class NonProductionRegistry:
    eval_trace_ids: frozenset[str]
    probe_trace_ids: frozenset[str]
    smoke_trace_ids: frozenset[str]
    test_session_ids: frozenset[str]
    test_user_ids: frozenset[str]
    synthetic_request_ids: frozenset[str]
    conflicting_ids: frozenset[str]
    reviewed_overrides: tuple[ReviewedOverride, ...]

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> NonProductionRegistry:
        expected = {
            "eval_trace_ids",
            "probe_trace_ids",
            "smoke_trace_ids",
            "test_session_ids",
            "test_user_ids",
            "synthetic_request_ids",
            "conflicting_ids",
            "reviewed_overrides",
        }
        unknown = set(payload) - expected
        missing = expected - set(payload)
        if unknown:
            raise PopulationRegistryError(f"unknown registry keys: {sorted(unknown)}")
        if missing:
            raise PopulationRegistryError(f"missing registry keys: {sorted(missing)}")

        def string_set(key: str) -> frozenset[str]:
            raw = payload[key]
            if not isinstance(raw, list) or any(
                not isinstance(item, str) or not item for item in raw
            ):
                raise PopulationRegistryError(f"{key} must be an array of non-empty strings")
            return frozenset(raw)

        raw_overrides = payload["reviewed_overrides"]
        if not isinstance(raw_overrides, list):
            raise PopulationRegistryError("reviewed_overrides must be an array")
        overrides = tuple(_parse_override(item) for item in raw_overrides)
        keys = [(item.target_kind, item.target_id) for item in overrides]
        if len(keys) != len(set(keys)):
            raise PopulationRegistryError("duplicate reviewed override target")
        return cls(
            eval_trace_ids=string_set("eval_trace_ids"),
            probe_trace_ids=string_set("probe_trace_ids"),
            smoke_trace_ids=string_set("smoke_trace_ids"),
            test_session_ids=string_set("test_session_ids"),
            test_user_ids=string_set("test_user_ids"),
            synthetic_request_ids=string_set("synthetic_request_ids"),
            conflicting_ids=string_set("conflicting_ids"),
            reviewed_overrides=overrides,
        )


@dataclass(frozen=True)
class PopulationEvidence:
    source: str
    value: str
    detail: str | None = None


@dataclass(frozen=True)
class PopulationDecision:
    population_class: PopulationClass
    reasons: tuple[ExclusionReason, ...]
    evidence: tuple[PopulationEvidence, ...]
    original_population_class: PopulationClass | None = None
    override_applied: bool = False


def _parse_override(raw: object) -> ReviewedOverride:
    expected = {
        "target_kind",
        "target_id",
        "population_class",
        "reviewer",
        "reason",
        "created_at",
        "source_evidence",
    }
    if not isinstance(raw, Mapping) or set(raw) != expected:
        raise PopulationRegistryError("reviewed override has invalid keys")
    values = {key: raw[key] for key in expected}
    if any(not isinstance(value, str) or not value for value in values.values()):
        raise PopulationRegistryError("reviewed override values must be non-empty strings")
    if values["target_kind"] not in {"session_id", "user_id", "trace_id", "request_id"}:
        raise PopulationRegistryError("reviewed override target_kind is invalid")
    try:
        population_class = PopulationClass(values["population_class"])
    except ValueError as exc:
        raise PopulationRegistryError("reviewed override population_class is invalid") from exc
    if population_class is PopulationClass.EXCLUDED:
        try:
            ExclusionReason(values["reason"])
        except ValueError as exc:
            raise PopulationRegistryError(
                "excluded override reason must be a governed exclusion reason"
            ) from exc
    try:
        created_at = datetime.fromisoformat(values["created_at"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise PopulationRegistryError("reviewed override created_at is invalid") from exc
    if created_at.tzinfo is None:
        raise PopulationRegistryError("reviewed override created_at must be timezone-aware")
    return ReviewedOverride(
        target_kind=values["target_kind"],
        target_id=values["target_id"],
        population_class=population_class,
        reviewer=values["reviewer"],
        reason=values["reason"],
        created_at=values["created_at"],
        source_evidence=values["source_evidence"],
    )


def _strings(values: object, key: str) -> tuple[str, ...]:
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return ()
    result: list[str] = []
    for value in values:
        if isinstance(value, Mapping) and isinstance(value.get(key), str) and value[key]:
            result.append(str(value[key]))
    return tuple(result)


def _metadata_values(session: Mapping[str, object], *keys: str) -> tuple[str, ...]:
    values: list[str] = []
    candidates: list[object] = [session.get("metadata")]
    turns = session.get("turns")
    if isinstance(turns, Sequence) and not isinstance(turns, (str, bytes)):
        candidates.extend(turn.get("metadata") for turn in turns if isinstance(turn, Mapping))
    for metadata in candidates:
        if not isinstance(metadata, Mapping):
            continue
        for key in keys:
            if isinstance(metadata.get(key), str) and metadata[key]:
                values.append(str(metadata[key]))
    return tuple(dict.fromkeys(values))


def _direct_values(record: Mapping[str, object], *keys: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            str(record[key])
            for key in keys
            if isinstance(record.get(key), str) and record[key]
        )
    )


def _session_identifiers(session: Mapping[str, object]) -> dict[str, tuple[str, ...]]:
    turns = session.get("turns")
    turn_rows = (
        turns
        if isinstance(turns, Sequence) and not isinstance(turns, (str, bytes))
        else ()
    )
    return {
        "session_id": _direct_values(session, "external_session_id", "session_id"),
        "user_id": tuple(
            dict.fromkeys(
                (
                    *_direct_values(session, "user_id", "external_user_id"),
                    *_metadata_values(session, "user_id", "external_user_id"),
                )
            )
        ),
        "trace_id": _strings(turns, "trace_id"),
        "request_id": tuple(
            dict.fromkeys(
                (
                    *_direct_values(session, "request_id", "client_request_id"),
                    *_strings(turn_rows, "request_id"),
                    *_strings(turn_rows, "client_request_id"),
                    *_metadata_values(session, "request_id", "client_request_id"),
                )
            )
        ),
    }


def _parse_time(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else None
    return None


def _outside_window(
    session: Mapping[str, object], window_start: datetime | None, window_end: datetime | None
) -> bool:
    if window_start is None or window_end is None:
        return False
    turns = session.get("turns")
    if not isinstance(turns, Sequence) or isinstance(turns, (str, bytes)) or not turns:
        return False
    timestamps = [
        _parse_time(turn.get("question_at") or turn.get("created_at"))
        for turn in turns
        if isinstance(turn, Mapping)
    ]
    known = [item for item in timestamps if item is not None]
    return bool(known) and all(item < window_start or item >= window_end for item in known)


def _dedupe_reasons(reasons: list[ExclusionReason]) -> tuple[ExclusionReason, ...]:
    return tuple(dict.fromkeys(reasons))


def _automatic_decision(
    session: Mapping[str, object],
    *,
    registry: NonProductionRegistry,
    source_database_is_production: bool,
    window_start: datetime | None,
    window_end: datetime | None,
) -> PopulationDecision:
    identifiers = _session_identifiers(session)
    conflicting = [
        (kind, value)
        for kind, values in identifiers.items()
        for value in values
        if value in registry.conflicting_ids
    ]
    if conflicting:
        kind, value = conflicting[0]
        reason = (
            ExclusionReason.KNOWN_TEST_IDENTITY
            if kind == "user_id"
            else ExclusionReason.KNOWN_TEST_SESSION
        )
        return PopulationDecision(
            population_class=PopulationClass.UNCERTAIN,
            reasons=(reason,),
            evidence=(PopulationEvidence("registry.conflicting_ids", value, kind),),
        )

    if _outside_window(session, window_start, window_end):
        return PopulationDecision(
            population_class=PopulationClass.EXCLUDED,
            reasons=(ExclusionReason.OUTSIDE_TIME_WINDOW,),
            evidence=(PopulationEvidence("turn_timestamps", "outside_time_window"),),
        )

    reasons: list[ExclusionReason] = []
    evidence: list[PopulationEvidence] = []
    metadata = session.get("metadata")
    environment = metadata.get("environment") if isinstance(metadata, Mapping) else None
    if isinstance(environment, str) and environment.lower() in {"dev", "development", "staging"}:
        reasons.append(ExclusionReason.DEV_ENVIRONMENT)
        evidence.append(PopulationEvidence("session.metadata.environment", environment))

    match_specs = (
        (
            "trace_id",
            registry.eval_trace_ids,
            ExclusionReason.REGISTERED_EVAL_RUN,
            "registry.eval_trace_ids",
        ),
        (
            "trace_id",
            registry.probe_trace_ids,
            ExclusionReason.REGISTERED_PROBE,
            "registry.probe_trace_ids",
        ),
        (
            "trace_id",
            registry.smoke_trace_ids,
            ExclusionReason.REGISTERED_SMOKE,
            "registry.smoke_trace_ids",
        ),
        (
            "session_id",
            registry.test_session_ids,
            ExclusionReason.KNOWN_TEST_SESSION,
            "registry.test_session_ids",
        ),
        (
            "user_id",
            registry.test_user_ids,
            ExclusionReason.KNOWN_TEST_IDENTITY,
            "registry.test_user_ids",
        ),
        (
            "request_id",
            registry.synthetic_request_ids,
            ExclusionReason.SYNTHETIC_PAYLOAD,
            "registry.synthetic_request_ids",
        ),
    )
    for kind, governed, reason, source in match_specs:
        for value in identifiers[kind]:
            if value in governed:
                reasons.append(reason)
                evidence.append(PopulationEvidence(source, value))

    if isinstance(metadata, Mapping) and metadata.get("duplicate_ingestion_verified") is True:
        reasons.append(ExclusionReason.DUPLICATE_INGESTION)
        evidence.append(PopulationEvidence("session.metadata", "duplicate_ingestion_verified"))

    if reasons:
        return PopulationDecision(
            population_class=PopulationClass.EXCLUDED,
            reasons=_dedupe_reasons(reasons),
            evidence=tuple(evidence),
        )
    if source_database_is_production:
        return PopulationDecision(
            population_class=PopulationClass.INCLUDED,
            reasons=(),
            evidence=(PopulationEvidence("source_database", "production_database_snapshot"),),
        )
    return PopulationDecision(
        population_class=PopulationClass.UNCERTAIN,
        reasons=(),
        evidence=(PopulationEvidence("source_database", "production_source_not_proven"),),
    )


def _matching_overrides(
    identifiers: Mapping[str, tuple[str, ...]], overrides: tuple[ReviewedOverride, ...]
) -> tuple[ReviewedOverride, ...]:
    return tuple(
        override
        for override in overrides
        if override.target_id in identifiers.get(override.target_kind, ())
    )


def classify_session(
    session: Mapping[str, object],
    *,
    registry: NonProductionRegistry,
    source_database_is_production: bool = True,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> PopulationDecision:
    automatic = _automatic_decision(
        session,
        registry=registry,
        source_database_is_production=source_database_is_production,
        window_start=window_start,
        window_end=window_end,
    )
    matching = _matching_overrides(_session_identifiers(session), registry.reviewed_overrides)
    if not matching:
        return automatic
    if len(matching) != 1:
        raise PopulationRegistryError("multiple reviewed overrides match one session")
    override = matching[0]
    reasons: tuple[ExclusionReason, ...] = ()
    if override.population_class is PopulationClass.EXCLUDED:
        reasons = (ExclusionReason(override.reason),)
    return PopulationDecision(
        population_class=override.population_class,
        reasons=reasons,
        evidence=automatic.evidence
        + (
            PopulationEvidence(
                "reviewed_override",
                override.target_id,
                f"{override.reviewer}:{override.reason}:{override.source_evidence}",
            ),
        ),
        original_population_class=automatic.population_class,
        override_applied=True,
    )


def population_summary(decisions: Sequence[PopulationDecision]) -> dict[str, Any]:
    counts = Counter(decision.population_class for decision in decisions)
    reason_counts = Counter(reason.value for decision in decisions for reason in decision.reasons)
    raw = len(decisions)
    included = counts[PopulationClass.INCLUDED]
    excluded = counts[PopulationClass.EXCLUDED]
    uncertain = counts[PopulationClass.UNCERTAIN]
    return {
        "raw_sessions": raw,
        "included": included,
        "excluded": excluded,
        "uncertain": uncertain,
        "uncertain_rate": uncertain / raw if raw else 0.0,
        "reason_counts": dict(sorted(reason_counts.items())),
        "sensitivity": {
            "included_if_all_uncertain": included + uncertain,
            "excluded_if_all_uncertain": excluded + uncertain,
        },
    }
