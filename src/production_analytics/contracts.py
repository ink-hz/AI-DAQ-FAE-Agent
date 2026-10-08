from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping


class AnalysisStatus(str, Enum):
    PREPARED = "prepared"
    SNAPSHOT_COMPLETE = "snapshot_complete"
    CLASSIFICATION_COMPLETE = "classification_complete"
    REVIEW_COMPLETE = "review_complete"
    REPORT_READY = "report_ready"
    BLOCKED = "blocked"


class PopulationClass(str, Enum):
    INCLUDED = "included_production"
    EXCLUDED = "excluded_non_production"
    UNCERTAIN = "uncertain_population"


class ExclusionReason(str, Enum):
    DEV_ENVIRONMENT = "dev_environment"
    REGISTERED_EVAL_RUN = "registered_eval_run"
    REGISTERED_PROBE = "registered_probe"
    REGISTERED_SMOKE = "registered_smoke"
    KNOWN_TEST_IDENTITY = "known_test_identity"
    KNOWN_TEST_SESSION = "known_test_session"
    SYNTHETIC_PAYLOAD = "synthetic_payload"
    DUPLICATE_INGESTION = "duplicate_ingestion"
    OUTSIDE_TIME_WINDOW = "outside_time_window"


@dataclass(frozen=True)
class AnalysisManifest:
    analysis_id: str
    schema_version: str
    query_version: str
    taxonomy_version: str
    window_start: str
    window_end: str
    timezone: str
    snapshot_started_at: str | None
    snapshot_completed_at: str | None
    source_table_counts: Mapping[str, int]
    exported_row_counts: Mapping[str, int]
    population_counts: Mapping[str, int]
    source_build_identity_if_available: str | None
    snapshot_file_hashes: Mapping[str, str]
    reviewer_policy_version: str
    status: AnalysisStatus
    block_reason: str | None
    resume_count: int
    stage_hashes: Mapping[str, str]
    requested_window_start: str | None = None
    window_start_adjusted: bool = False
    blocked_stage: str | None = None
    stage_output_hashes: Mapping[str, Mapping[str, str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "analysis_id": self.analysis_id,
            "schema_version": self.schema_version,
            "query_version": self.query_version,
            "taxonomy_version": self.taxonomy_version,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "timezone": self.timezone,
            "snapshot_started_at": self.snapshot_started_at,
            "snapshot_completed_at": self.snapshot_completed_at,
            "source_table_counts": dict(self.source_table_counts),
            "exported_row_counts": dict(self.exported_row_counts),
            "population_counts": dict(self.population_counts),
            "source_build_identity_if_available": self.source_build_identity_if_available,
            "snapshot_file_hashes": dict(self.snapshot_file_hashes),
            "reviewer_policy_version": self.reviewer_policy_version,
            "status": self.status.value,
            "block_reason": self.block_reason,
            "resume_count": self.resume_count,
            "stage_hashes": dict(self.stage_hashes),
            "requested_window_start": self.requested_window_start,
            "window_start_adjusted": self.window_start_adjusted,
            "blocked_stage": self.blocked_stage,
            "stage_output_hashes": {
                stage_name: dict(artifact_hashes)
                for stage_name, artifact_hashes in self.stage_output_hashes.items()
            },
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> AnalysisManifest:
        values = dict(payload)
        try:
            values["status"] = AnalysisStatus(str(values["status"]))
        except (KeyError, ValueError) as exc:
            raise ValueError("analysis manifest status is invalid") from exc
        values.setdefault("requested_window_start", None)
        values.setdefault("window_start_adjusted", False)
        values.setdefault("blocked_stage", None)
        values.setdefault("stage_output_hashes", {})
        try:
            return cls(**values)  # type: ignore[arg-type]
        except TypeError as exc:
            raise ValueError("analysis manifest contract mismatch") from exc
