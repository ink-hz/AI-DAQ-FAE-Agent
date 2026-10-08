from __future__ import annotations

import hashlib
import json
import stat
import subprocess
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from src.facts.resolver import load_catalog
from src.production_analytics.annotation import (
    AnnotationBatchPlan,
    annotation_completion,
    build_annotation_batches,
    load_annotation_results,
)
from src.production_analytics.canonical import (
    AnalyticsIdentityKey,
    CanonicalDataError,
    analysis_id,
    build_canonical_dataset,
)
from src.production_analytics.contracts import (
    AnalysisManifest,
    AnalysisStatus,
    PopulationClass,
)
from src.production_analytics.metrics import (
    AnalyticsMetricInputs,
    Metric,
    compute_metrics,
    compute_population_metrics,
)
from src.production_analytics.population import (
    NonProductionRegistry,
    PopulationDecision,
    PopulationEvidence,
    classify_session,
)
from src.production_analytics.private_io import (
    atomic_write_json,
    atomic_write_text,
    sha256_file,
    write_jsonl,
)
from src.production_analytics.reporting import (
    ActionCandidate,
    Claim,
    ReportBundle,
    ReportSlice,
    build_action_backlog,
    render_reports,
)
from src.production_analytics.review import (
    ReviewQueueItem,
    ReviewQueues,
    build_review_queues,
    derive_review_subjects,
    import_review_records,
    review_completion,
)
from src.production_analytics.taxonomy import load_taxonomy

_ALLOWED_TRANSITIONS = {
    AnalysisStatus.PREPARED: AnalysisStatus.SNAPSHOT_COMPLETE,
    AnalysisStatus.SNAPSHOT_COMPLETE: AnalysisStatus.CLASSIFICATION_COMPLETE,
    AnalysisStatus.CLASSIFICATION_COMPLETE: AnalysisStatus.REVIEW_COMPLETE,
    AnalysisStatus.REVIEW_COMPLETE: AnalysisStatus.REPORT_READY,
}

_REQUIRED_REPORT_ARTIFACTS = frozenset(
    {
        "claim_ledger.jsonl",
        "action_backlog.jsonl",
        "executive_summary.md",
        "full_report.md",
        "audit_appendix.md",
        "report.html",
    }
)


class AnalysisBlocked(RuntimeError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class StageResult:
    status: AnalysisStatus
    skipped: bool
    resume_count: int


def _default_git_ignore_checker(path: Path) -> bool:
    result = subprocess.run(
        ["git", "check-ignore", "-q", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


class AnalysisRunner:
    def __init__(
        self,
        analysis_dir: Path,
        *,
        private_root: Path,
        git_ignore_checker: Callable[[Path], bool] = _default_git_ignore_checker,
    ) -> None:
        self.analysis_dir = analysis_dir.resolve()
        self.private_root = private_root.resolve()
        if self.analysis_dir == self.private_root or not self.analysis_dir.is_relative_to(
            self.private_root
        ):
            raise AnalysisBlocked("analysis_dir_outside_private_root")
        if not git_ignore_checker(self.analysis_dir):
            raise AnalysisBlocked("analysis_dir_not_git_ignored")
        self._git_ignore_checker = git_ignore_checker

    @property
    def manifest_path(self) -> Path:
        return self.analysis_dir / "analysis_manifest.json"

    def _privacy_preflight(self) -> None:
        if not self.analysis_dir.exists() or self.analysis_dir.is_symlink():
            raise AnalysisBlocked("private_directory_missing_or_symlink")
        for path in (self.analysis_dir, *self.analysis_dir.rglob("*")):
            if path.is_symlink():
                raise AnalysisBlocked("private_artifact_symlink")
            mode = stat.S_IMODE(path.stat().st_mode)
            if path.is_dir() and mode & 0o077:
                raise AnalysisBlocked("private_directory_permissions")
            if path.is_file() and mode & 0o077:
                raise AnalysisBlocked("private_file_permissions")
        if not self._git_ignore_checker(self.analysis_dir):
            raise AnalysisBlocked("analysis_dir_not_git_ignored")

    def _validate_private_input(self, path: Path) -> Path:
        if path.is_symlink():
            raise AnalysisBlocked("input_outside_analysis_dir")
        resolved = path.resolve()
        if not resolved.is_relative_to(self.analysis_dir) or not resolved.is_file():
            raise AnalysisBlocked("input_outside_analysis_dir")
        if stat.S_IMODE(resolved.stat().st_mode) & 0o077:
            raise AnalysisBlocked("private_file_permissions")
        return resolved

    def load_manifest(self) -> AnalysisManifest:
        self._privacy_preflight()
        try:
            payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AnalysisBlocked("manifest_unavailable_or_invalid") from exc
        if not isinstance(payload, dict):
            raise AnalysisBlocked("manifest_unavailable_or_invalid")
        try:
            return AnalysisManifest.from_dict(payload)
        except ValueError as exc:
            raise AnalysisBlocked("manifest_contract_mismatch") from exc

    def save_manifest(self, manifest: AnalysisManifest) -> None:
        atomic_write_json(self.manifest_path, manifest.to_dict())

    def _validate_artifact_hashes(self, manifest: AnalysisManifest) -> None:
        for relative, expected in manifest.snapshot_file_hashes.items():
            path = (self.analysis_dir / relative).resolve()
            if not path.is_relative_to(self.analysis_dir) or not path.is_file():
                raise AnalysisBlocked("artifact_hash_target_invalid")
            if sha256_file(path) != expected:
                raise AnalysisBlocked("artifact_hash_mismatch")

    def status(self) -> dict[str, object]:
        manifest = self.load_manifest()
        self._validate_artifact_hashes(manifest)
        if manifest.status is AnalysisStatus.REPORT_READY:
            self._validate_report_ready_outputs(manifest)
        return {
            "analysis_id": manifest.analysis_id,
            "status": manifest.status.value,
            "block_reason": manifest.block_reason,
            "resume_count": manifest.resume_count,
            "source_table_counts": dict(manifest.source_table_counts),
            "exported_row_counts": dict(manifest.exported_row_counts),
            "population_counts": dict(manifest.population_counts),
            "snapshot_file_hashes": dict(manifest.snapshot_file_hashes),
            "stage_hashes": dict(manifest.stage_hashes),
        }

    def advance(
        self,
        *,
        expected: AnalysisStatus,
        target: AnalysisStatus,
        stage_name: str,
        input_hash: str,
    ) -> StageResult:
        manifest = self.load_manifest()
        self._validate_artifact_hashes(manifest)
        if manifest.status is not expected or _ALLOWED_TRANSITIONS.get(expected) is not target:
            raise AnalysisBlocked("invalid_stage_transition")
        hashes = dict(manifest.stage_hashes)
        previous = hashes.get(stage_name)
        if previous is not None and previous != input_hash:
            self._block_manifest(manifest, "input_hash_changed", stage_name, previous)
            raise AnalysisBlocked("input_hash_changed")
        hashes[stage_name] = input_hash
        updated = replace(
            manifest,
            status=target,
            block_reason=None,
            blocked_stage=None,
            stage_hashes=hashes,
        )
        self.save_manifest(updated)
        return StageResult(updated.status, previous == input_hash, updated.resume_count)

    def _block_manifest(
        self,
        manifest: AnalysisManifest,
        reason: str,
        stage_name: str,
        input_hash: str,
    ) -> AnalysisManifest:
        hashes = dict(manifest.stage_hashes)
        hashes.setdefault(stage_name, input_hash)
        active_status = (
            manifest.blocked_stage
            if manifest.status is AnalysisStatus.BLOCKED and manifest.blocked_stage
            else manifest.status.value
        )
        blocked = replace(
            manifest,
            status=AnalysisStatus.BLOCKED,
            block_reason=reason,
            blocked_stage=active_status,
            stage_hashes=hashes,
        )
        self.save_manifest(blocked)
        return blocked

    def block(self, reason: str, *, stage_name: str, input_hash: str) -> StageResult:
        manifest = self.load_manifest()
        if manifest.status is AnalysisStatus.REPORT_READY:
            raise AnalysisBlocked("terminal_analysis_cannot_block")
        previous = manifest.stage_hashes.get(stage_name)
        if previous is not None and previous != input_hash:
            self._block_manifest(manifest, "input_hash_changed", stage_name, previous)
            raise AnalysisBlocked("input_hash_changed")
        blocked = self._block_manifest(manifest, reason, stage_name, input_hash)
        return StageResult(blocked.status, False, blocked.resume_count)

    def resume(self, *, stage_name: str, input_hash: str) -> StageResult:
        manifest = self.load_manifest()
        self._validate_artifact_hashes(manifest)
        if manifest.status is not AnalysisStatus.BLOCKED or not manifest.blocked_stage:
            raise AnalysisBlocked("analysis_is_not_resumable")
        if manifest.stage_hashes.get(stage_name) != input_hash:
            changed = replace(manifest, block_reason="input_hash_changed")
            self.save_manifest(changed)
            raise AnalysisBlocked("input_hash_changed")
        restored = replace(
            manifest,
            status=AnalysisStatus(manifest.blocked_stage),
            block_reason=None,
            blocked_stage=None,
            resume_count=manifest.resume_count + 1,
        )
        self.save_manifest(restored)
        return StageResult(restored.status, False, restored.resume_count)

    def record_stage_input(self, stage_name: str, input_hash: str) -> StageResult:
        manifest = self.load_manifest()
        self._validate_artifact_hashes(manifest)
        hashes = dict(manifest.stage_hashes)
        previous = hashes.get(stage_name)
        if previous == input_hash:
            return StageResult(manifest.status, True, manifest.resume_count)
        if previous is not None:
            self._block_manifest(manifest, "input_hash_changed", stage_name, previous)
            raise AnalysisBlocked("input_hash_changed")
        hashes[stage_name] = input_hash
        updated = replace(manifest, stage_hashes=hashes)
        self.save_manifest(updated)
        return StageResult(updated.status, False, updated.resume_count)

    def _record_stage_outputs(self, stage_name: str, filenames: tuple[str, ...]) -> None:
        manifest = self.load_manifest()
        recorded = {
            filename: sha256_file(self.analysis_dir / filename) for filename in filenames
        }
        outputs = {
            name: dict(artifact_hashes)
            for name, artifact_hashes in manifest.stage_output_hashes.items()
        }
        previous = outputs.get(stage_name)
        if previous is not None and previous != recorded:
            raise AnalysisBlocked("stage_output_hash_changed")
        outputs[stage_name] = recorded
        self.save_manifest(replace(manifest, stage_output_hashes=outputs))

    def _metric_input_paths(self) -> list[Path]:
        optional = (
            "session_annotations.jsonl",
            "accepted_reviews.jsonl",
            "review_stratum_populations.json",
            "potential_conversion_session_ids.json",
            "canonical_issue_by_turn.json",
        )
        return [
            self.analysis_dir / "canonical_sessions.jsonl",
            self.analysis_dir / "canonical_turns.jsonl",
            *(self.analysis_dir / name for name in optional if (self.analysis_dir / name).exists()),
        ]

    def _validate_reviewed_metrics(self, manifest: AnalysisManifest) -> None:
        recorded_input = manifest.stage_hashes.get("metrics_reviewed")
        recorded_outputs = manifest.stage_output_hashes.get("metrics_reviewed")
        if recorded_input is None or set(recorded_outputs or {}) != {"metrics.json"}:
            raise AnalysisBlocked("reviewed_metrics_unavailable")
        try:
            current_input = _hash_paths(self._metric_input_paths())
        except OSError as exc:
            raise AnalysisBlocked("reviewed_metrics_input_changed") from exc
        if current_input != recorded_input:
            raise AnalysisBlocked("reviewed_metrics_input_changed")
        metrics_path = self.analysis_dir / "metrics.json"
        if (
            not metrics_path.is_file()
            or sha256_file(metrics_path) != recorded_outputs["metrics.json"]
        ):
            raise AnalysisBlocked("reviewed_metrics_output_changed")

    def _validate_report_ready_outputs(self, manifest: AnalysisManifest) -> None:
        self._validate_reviewed_metrics(manifest)
        recorded = manifest.stage_output_hashes.get("report")
        if set(recorded or {}) != _REQUIRED_REPORT_ARTIFACTS:
            raise AnalysisBlocked("report_artifacts_unavailable")
        for filename, expected_hash in recorded.items():
            path = self.analysis_dir / filename
            if not path.is_file() or sha256_file(path) != expected_hash:
                raise AnalysisBlocked("report_artifact_changed")

    def canonicalize(
        self, registry_path: Path, identity_key: AnalyticsIdentityKey
    ) -> StageResult:
        registry_path = self._validate_private_input(registry_path)
        manifest = self.load_manifest()
        self._validate_artifact_hashes(manifest)
        if manifest.status is not AnalysisStatus.SNAPSHOT_COMPLETE:
            raise AnalysisBlocked("snapshot_not_ready")
        try:
            raw = {
                name: _read_jsonl(self.analysis_dir / "raw_snapshot" / f"{name}.jsonl")
                for name in ("sessions", "turns", "feedback", "reviews", "attachments")
            }
            registry_payload = _read_json(registry_path)
            registry = NonProductionRegistry.from_dict(registry_payload)
            turns_by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for turn in raw["turns"]:
                turns_by_session[str(turn.get("external_session_id") or "")].append(turn)
            window_start = datetime.fromisoformat(manifest.window_start)
            window_end = datetime.fromisoformat(manifest.window_end)
            decisions: dict[str, PopulationDecision] = {}
            for session in raw["sessions"]:
                external_id = str(session.get("external_session_id") or "")
                bundle = {**session, "turns": turns_by_session.get(external_id, [])}
                decisions[external_id] = classify_session(
                    bundle,
                    registry=registry,
                    source_database_is_production=True,
                    window_start=window_start,
                    window_end=window_end,
                )
            dataset = build_canonical_dataset(
                sessions=raw["sessions"],
                turns=raw["turns"],
                feedback=raw["feedback"],
                reviews=raw["reviews"],
                attachments=raw["attachments"],
                population_decisions=decisions,
                identity_key=identity_key,
            )
        except (CanonicalDataError, ValueError) as exc:
            reason = str(exc) or "canonicalization_failed"
            self.block(
                reason,
                stage_name="canonicalize",
                input_hash=_hash_paths(
                    [self.analysis_dir / "raw_snapshot" / "turns.jsonl", registry_path]
                ),
            )
            raise AnalysisBlocked(reason) from exc
        write_jsonl(self.analysis_dir / "canonical_sessions.jsonl", dataset.sessions)
        write_jsonl(self.analysis_dir / "canonical_turns.jsonl", dataset.turns)
        decision_rows = []
        for external_id, decision in sorted(decisions.items()):
            decision_rows.append(
                {
                    "analysis_session_id": analysis_id(external_id, identity_key),
                    "population_class": decision.population_class.value,
                    "reasons": [reason.value for reason in decision.reasons],
                    "evidence": [
                        {"source": item.source, "value": item.value, "detail": item.detail}
                        for item in decision.evidence
                    ],
                    "original_population_class": (
                        decision.original_population_class.value
                        if decision.original_population_class
                        else None
                    ),
                    "override_applied": decision.override_applied,
                }
            )
        write_jsonl(self.analysis_dir / "population_decisions.jsonl", decision_rows)
        counts = defaultdict(int)
        for decision in decisions.values():
            counts[decision.population_class.value] += 1
        input_hash = _hash_paths(
            [
                *(self.analysis_dir / "raw_snapshot" / f"{name}.jsonl" for name in raw),
                registry_path,
            ],
            extra=identity_key.version,
        )
        hashes = dict(manifest.stage_hashes)
        hashes["canonicalize"] = input_hash
        updated = replace(
            manifest,
            status=AnalysisStatus.CLASSIFICATION_COMPLETE,
            population_counts=dict(counts),
            stage_hashes=hashes,
            block_reason=None,
            blocked_stage=None,
        )
        self.save_manifest(updated)
        return StageResult(updated.status, False, updated.resume_count)

    def build_annotation_batches(self, *, batch_size: int = 25) -> AnnotationBatchPlan:
        manifest = self.load_manifest()
        if manifest.status is not AnalysisStatus.CLASSIFICATION_COMPLETE:
            raise AnalysisBlocked("classification_not_ready")
        sessions = _read_jsonl(self.analysis_dir / "canonical_sessions.jsonl")
        turns = _read_jsonl(self.analysis_dir / "canonical_turns.jsonl")
        existing_path = self.analysis_dir / "session_annotations.jsonl"
        existing = _read_jsonl(existing_path) if existing_path.exists() else []
        plan = build_annotation_batches(
            sessions,
            turns,
            manifest.taxonomy_version,
            batch_size=batch_size,
            existing_annotations=existing,
        )
        batch_dir = self.analysis_dir / "annotation_batches"
        active_files: list[str] = []
        expected_rows: list[dict[str, Any]] = []
        for batch in plan.batches:
            filename = f"{batch.batch_id}.jsonl"
            write_jsonl(batch_dir / filename, batch.rows)
            active_files.append(filename)
            for row in batch.rows:
                expected_rows.append(
                    {
                        "analysis_session_id": row["analysis_session_id"],
                        "input_hash": row["input_hash"],
                        "turn_indexes": [item["turn_index"] for item in row["turns"]],
                    }
                )
        for row in existing:
            expected_rows.append(
                {
                    "analysis_session_id": row["analysis_session_id"],
                    "input_hash": row["input_hash"],
                    "turn_indexes": [item["turn_index"] for item in row["turn_annotations"]],
                }
            )
        expected_rows.sort(key=lambda item: str(item["analysis_session_id"]))
        write_jsonl(self.analysis_dir / "annotation_expected.jsonl", expected_rows)
        atomic_write_json(
            self.analysis_dir / "annotation_batch_index.json",
            {
                "active_files": active_files,
                "batch_size": batch_size,
                "skipped_count": plan.skipped_count,
                "resume_count": plan.resume_count,
            },
        )
        self.record_stage_input(
            "annotation_batches",
            _hash_paths(
                [
                    self.analysis_dir / "canonical_sessions.jsonl",
                    self.analysis_dir / "canonical_turns.jsonl",
                    self.analysis_dir / "annotation_expected.jsonl",
                ]
            ),
        )
        return plan

    def import_annotations(self, input_path: Path) -> tuple[dict[str, Any], ...]:
        input_path = self._validate_private_input(input_path)
        manifest = self.load_manifest()
        if manifest.status not in {
            AnalysisStatus.CLASSIFICATION_COMPLETE,
            AnalysisStatus.BLOCKED,
        }:
            raise AnalysisBlocked("classification_not_ready")
        expected_rows = _read_jsonl(self.analysis_dir / "annotation_expected.jsonl")
        expected = {
            str(row["analysis_session_id"]): (
                str(row["input_hash"]),
                {int(item) for item in row["turn_indexes"]},
            )
            for row in expected_rows
        }
        stage_hash = _hash_paths([self.analysis_dir / "annotation_expected.jsonl"])
        if manifest.status is AnalysisStatus.BLOCKED:
            if manifest.block_reason != "annotation_incomplete":
                raise AnalysisBlocked(str(manifest.block_reason or "analysis_blocked"))
            self.resume(stage_name="annotations", input_hash=stage_hash)
        incoming = _read_jsonl(input_path)
        existing_path = self.analysis_dir / "session_annotations.jsonl"
        existing = _read_jsonl(existing_path) if existing_path.exists() else []
        merged = {str(row["analysis_session_id"]): row for row in existing}
        for row in incoming:
            session_id = str(row.get("analysis_session_id") or "")
            previous = merged.get(session_id)
            if previous is not None and previous != row:
                self.block("input_hash_changed", stage_name="annotations", input_hash=stage_hash)
                raise AnalysisBlocked("input_hash_changed")
            merged[session_id] = row
        taxonomy = load_taxonomy(_repo_root() / "evals/production_analytics/taxonomy_v1.json")
        resolver = load_catalog(
            _repo_root() / "Knowledge/_facts/catalog.yaml", _repo_root() / "Knowledge"
        )
        parsed = load_annotation_results(
            list(merged.values()),
            expected=expected,
            taxonomy=taxonomy,
            known_model_ids=set(resolver.entries),
        )
        payloads = tuple(
            annotation.payload for annotation in sorted(parsed, key=lambda item: item.analysis_session_id)
        )
        write_jsonl(existing_path, payloads)
        completion = annotation_completion(
            expected_analysis_ids=set(expected),
            completed_analysis_ids={item["analysis_session_id"] for item in payloads},
        )
        if completion["missing"]:
            self.block(
                "annotation_incomplete", stage_name="annotations", input_hash=stage_hash
            )
            raise AnalysisBlocked("annotation_incomplete")
        self.record_stage_input("annotations", stage_hash)
        return payloads

    def build_review_queues(self) -> ReviewQueues:
        manifest = self.load_manifest()
        if manifest.status is not AnalysisStatus.CLASSIFICATION_COMPLETE:
            raise AnalysisBlocked("classification_not_ready")
        sessions = _read_jsonl(self.analysis_dir / "canonical_sessions.jsonl")
        turns = _read_jsonl(self.analysis_dir / "canonical_turns.jsonl")
        annotations = _read_jsonl(self.analysis_dir / "session_annotations.jsonl")
        subjects = derive_review_subjects(sessions, turns, annotations)
        queues = build_review_queues(subjects, analysis_id=manifest.analysis_id)
        write_jsonl(
            self.analysis_dir / "hard_review_queue.jsonl",
            [_queue_item_dict(item) for item in queues.hard],
        )
        write_jsonl(
            self.analysis_dir / "ordinary_sample_queue.jsonl",
            [_queue_item_dict(item) for item in queues.ordinary],
        )
        atomic_write_json(
            self.analysis_dir / "review_stratum_populations.json",
            queues.stratum_populations,
        )
        self.record_stage_input(
            "review_queues",
            _hash_paths(
                [
                    self.analysis_dir / "canonical_sessions.jsonl",
                    self.analysis_dir / "canonical_turns.jsonl",
                    self.analysis_dir / "session_annotations.jsonl",
                ]
            ),
        )
        return queues

    def _load_review_queues(self) -> ReviewQueues:
        hard = tuple(
            _queue_item_from_dict(row)
            for row in _read_jsonl(self.analysis_dir / "hard_review_queue.jsonl")
        )
        ordinary = tuple(
            _queue_item_from_dict(row)
            for row in _read_jsonl(self.analysis_dir / "ordinary_sample_queue.jsonl")
        )
        populations = _read_json(self.analysis_dir / "review_stratum_populations.json")
        return ReviewQueues(hard, ordinary, {str(key): int(value) for key, value in populations.items()})

    def import_reviews(self, input_path: Path) -> tuple[dict[str, Any], ...]:
        input_path = self._validate_private_input(input_path)
        manifest = self.load_manifest()
        if manifest.status not in {
            AnalysisStatus.CLASSIFICATION_COMPLETE,
            AnalysisStatus.BLOCKED,
        }:
            raise AnalysisBlocked("classification_not_ready")
        queues = self._load_review_queues()
        review_hash = _hash_paths(
            [
                self.analysis_dir / "hard_review_queue.jsonl",
                self.analysis_dir / "ordinary_sample_queue.jsonl",
                self.analysis_dir / "session_annotations.jsonl",
            ]
        )
        if manifest.status is AnalysisStatus.BLOCKED:
            if manifest.block_reason != "review_incomplete":
                raise AnalysisBlocked(str(manifest.block_reason or "analysis_blocked"))
            self.resume(stage_name="review", input_hash=review_hash)
        existing_path = self.analysis_dir / "review_results.jsonl"
        existing = _read_jsonl(existing_path) if existing_path.exists() else []
        merged = {str(row["analysis_session_id"]): row for row in existing}
        for row in _read_jsonl(input_path):
            session_id = str(row.get("analysis_session_id") or "")
            previous = merged.get(session_id)
            if previous is not None and previous != row:
                raise AnalysisBlocked("review_changed_after_acceptance")
            merged[session_id] = row
        annotation_producers = {
            str(row.get("analysis_session_id") or ""): str(
                row.get("annotator_identity") or ""
            )
            for row in _read_jsonl(
                self.analysis_dir / "session_annotations.jsonl"
            )
        }
        accepted = import_review_records(
            list(merged.values()),
            queues=queues,
            annotation_producers=annotation_producers,
        )
        completion = review_completion(queues, accepted)
        write_jsonl(existing_path, [item.payload for item in accepted])
        metric_rows = tuple(item.to_metric_dict() for item in accepted)
        write_jsonl(self.analysis_dir / "accepted_reviews.jsonl", metric_rows)
        if completion["status"] != "complete":
            self.block("review_incomplete", stage_name="review", input_hash=review_hash)
            raise AnalysisBlocked("review_incomplete")
        self.advance(
            expected=AnalysisStatus.CLASSIFICATION_COMPLETE,
            target=AnalysisStatus.REVIEW_COMPLETE,
            stage_name="review",
            input_hash=review_hash,
        )
        return metric_rows

    def compute_metrics(self, *, deterministic_only: bool = False) -> dict[str, Metric]:
        manifest = self.load_manifest()
        allowed = {AnalysisStatus.CLASSIFICATION_COMPLETE, AnalysisStatus.REVIEW_COMPLETE}
        if manifest.status not in allowed:
            raise AnalysisBlocked("metrics_stage_not_ready")
        if not deterministic_only and manifest.status is not AnalysisStatus.REVIEW_COMPLETE:
            raise AnalysisBlocked("review_incomplete")
        annotations_path = self.analysis_dir / "session_annotations.jsonl"
        reviews_path = self.analysis_dir / "accepted_reviews.jsonl"
        populations_path = self.analysis_dir / "review_stratum_populations.json"
        potential_path = self.analysis_dir / "potential_conversion_session_ids.json"
        issue_path = self.analysis_dir / "canonical_issue_by_turn.json"
        metric_inputs = self._metric_input_paths()
        metric_stage = "metrics_deterministic" if deterministic_only else "metrics_reviewed"
        self.record_stage_input(
            metric_stage,
            _hash_paths(metric_inputs),
        )
        sessions = _read_jsonl(self.analysis_dir / "canonical_sessions.jsonl")
        turns = _read_jsonl(self.analysis_dir / "canonical_turns.jsonl")
        annotations = _read_jsonl(annotations_path) if annotations_path.exists() else []
        reviews = _read_jsonl(reviews_path) if reviews_path.exists() else []
        strata = _read_json(populations_path) if populations_path.exists() else None
        potential_payload = _read_json_value(potential_path) if potential_path.exists() else []
        issues = _read_json(issue_path) if issue_path.exists() else {}
        computed = compute_metrics(
            AnalyticsMetricInputs(
                sessions=sessions,
                turns=turns,
                annotations=annotations,
                accepted_reviews=reviews,
                canonical_issue_by_turn={str(key): str(value) for key, value in issues.items()},
                potential_conversion_session_ids=frozenset(str(item) for item in potential_payload),
                review_stratum_population=(
                    {str(key): int(value) for key, value in strata.items()}
                    if isinstance(strata, dict)
                    else None
                ),
            )
        )
        decisions = []
        for session in sessions:
            population = session.get("population_class")
            decisions.append(
                PopulationDecision(
                    population_class=PopulationClass(str(population)),
                    reasons=(),
                    evidence=(PopulationEvidence("canonical", "population_class"),),
                )
            )
        computed.update(compute_population_metrics(decisions))
        atomic_write_json(
            self.analysis_dir / "metrics.json",
            {metric_id: metric.to_dict() for metric_id, metric in sorted(computed.items())},
        )
        self._record_stage_outputs(metric_stage, ("metrics.json",))
        return computed

    def generate_report(self, config_path: Path) -> StageResult:
        config_path = self._validate_private_input(config_path)
        manifest = self.load_manifest()
        if manifest.status is not AnalysisStatus.REVIEW_COMPLETE:
            raise AnalysisBlocked("review_incomplete")
        self._validate_reviewed_metrics(manifest)
        config = _read_json(config_path)
        metric_payload = _read_json(self.analysis_dir / "metrics.json")
        metrics = {key: _metric_from_dict(value) for key, value in metric_payload.items()}
        claims = tuple(
            self._claim_from_private_config(item) for item in config.get("claims", [])
        )
        candidates = tuple(
            _action_candidate_from_dict(item) for item in config.get("action_candidates", [])
        )
        accepted_reviews = _read_jsonl(self.analysis_dir / "accepted_reviews.jsonl")
        accepted_review_by_session = {
            str(item["analysis_session_id"]): item for item in accepted_reviews
        }
        for candidate in candidates:
            if candidate.action_type not in {"product", "official_material"}:
                continue
            if any(
                accepted_review_by_session.get(session_id, {}).get(
                    "product_signal_approved"
                )
                is not True
                for session_id in candidate.evidence_session_ids
            ):
                raise AnalysisBlocked("product_action_evidence_not_approved")
        backlog = build_action_backlog(
            candidates,
            accepted_review_session_ids=set(accepted_review_by_session),
        )
        slices = tuple(_slice_from_dict(item) for item in config.get("slices", []))
        for item in slices:
            if not item.example or not item.business_case_approved:
                continue
            if not item.evidence_session_ids or any(
                accepted_review_by_session.get(session_id, {}).get(
                    "business_case_approved"
                )
                is not True
                for session_id in item.evidence_session_ids
            ):
                raise AnalysisBlocked("management_case_evidence_not_approved")
        bundle = ReportBundle(
            analysis_id=manifest.analysis_id,
            period_label=str(config.get("period_label") or "未提供周期"),
            metrics=metrics,
            claims=claims,
            backlog=backlog,
            slices=slices,
            private_context=None,
        )
        artifacts = render_reports(bundle)
        for filename, content in artifacts.items():
            atomic_write_text(self.analysis_dir / filename, content)
        self._record_stage_outputs("report", tuple(sorted(artifacts)))
        report_hash = hashlib_for_artifacts(self.analysis_dir, tuple(sorted(artifacts)))
        return self.advance(
            expected=AnalysisStatus.REVIEW_COMPLETE,
            target=AnalysisStatus.REPORT_READY,
            stage_name="report",
            input_hash=report_hash,
        )

    def _claim_from_private_config(self, row: object) -> Claim:
        if not isinstance(row, dict):
            raise AnalysisBlocked("claim_contract_invalid")
        artifacts = row.get("evidence_artifacts")
        hashes = row.get("evidence_artifact_hashes")
        if not isinstance(artifacts, list) or not isinstance(hashes, list):
            raise AnalysisBlocked("claim_evidence_contract_invalid")
        if len(artifacts) != len(hashes):
            raise AnalysisBlocked("claim_evidence_hash_mismatch")
        actual_hashes: list[str] = []
        for artifact in artifacts:
            if not isinstance(artifact, str) or not artifact:
                raise AnalysisBlocked("claim_evidence_artifact_invalid")
            candidate = self.analysis_dir / artifact
            if candidate.is_symlink():
                raise AnalysisBlocked("claim_evidence_artifact_invalid")
            resolved = candidate.resolve()
            if not resolved.is_relative_to(self.analysis_dir) or not resolved.is_file():
                raise AnalysisBlocked("claim_evidence_artifact_invalid")
            if stat.S_IMODE(resolved.stat().st_mode) & 0o077:
                raise AnalysisBlocked("private_file_permissions")
            actual_hashes.append(sha256_file(resolved))
        if hashes != actual_hashes:
            raise AnalysisBlocked("claim_evidence_hash_mismatch")
        return _claim_from_dict(row)

def hashlib_for_artifacts(root: Path, filenames: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    for filename in filenames:
        digest.update(filename.encode("utf-8"))
        digest.update((root / filename).read_bytes())
    return digest.hexdigest()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _read_json_value(path: Path) -> Any:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AnalysisBlocked(f"invalid_json:{path.name}") from exc
    return payload


def _read_json(path: Path) -> dict[str, Any]:
    payload = _read_json_value(path)
    if not isinstance(payload, dict):
        raise AnalysisBlocked(f"invalid_json_object:{path.name}")
    return payload


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise AnalysisBlocked(f"jsonl_unavailable:{path.name}") from exc
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AnalysisBlocked(f"invalid_jsonl:{path.name}:{line_number}") from exc
        if not isinstance(row, dict):
            raise AnalysisBlocked(f"invalid_jsonl_object:{path.name}:{line_number}")
        rows.append(row)
    return rows


def _hash_paths(paths: list[Path], *, extra: str = "") -> str:
    digest = hashlib.sha256(extra.encode("utf-8"))
    for path in sorted((item.resolve() for item in paths), key=str):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _queue_item_dict(item: ReviewQueueItem) -> dict[str, Any]:
    return {
        "analysis_session_id": item.analysis_session_id,
        "queue_kind": item.queue_kind,
        "reasons": list(item.reasons),
        "review_stratum": item.review_stratum,
        "turn_count": item.turn_count,
    }


def _queue_item_from_dict(row: Mapping[str, object]) -> ReviewQueueItem:
    return ReviewQueueItem(
        analysis_session_id=str(row["analysis_session_id"]),
        queue_kind=str(row["queue_kind"]),
        reasons=tuple(str(item) for item in row.get("reasons", [])),
        review_stratum=str(row["review_stratum"]),
        turn_count=int(row["turn_count"]),
    )


def _metric_from_dict(row: object) -> Metric:
    if not isinstance(row, dict):
        raise AnalysisBlocked("metric_contract_invalid")
    interval = row.get("interval")
    return Metric(
        value=row.get("value"),
        numerator=row.get("numerator"),
        denominator=row.get("denominator"),
        filters=tuple(str(item) for item in row.get("filters", [])),
        method=str(row.get("method") or ""),
        interval=(float(interval[0]), float(interval[1])) if isinstance(interval, list) else None,
    )


def _claim_from_dict(row: object) -> Claim:
    if not isinstance(row, dict):
        raise AnalysisBlocked("claim_contract_invalid")
    return Claim(
        claim_id=str(row.get("claim_id") or ""),
        claim_text=str(row.get("claim_text") or ""),
        claim_type=str(row.get("claim_type") or ""),  # type: ignore[arg-type]
        metric_ids=tuple(str(item) for item in row.get("metric_ids", [])),
        numerator=row.get("numerator"),
        denominator=row.get("denominator"),
        filters=tuple(str(item) for item in row.get("filters", [])),
        assumptions=tuple(str(item) for item in row.get("assumptions", [])),
        evidence_artifacts=tuple(str(item) for item in row.get("evidence_artifacts", [])),
        evidence_artifact_hashes=tuple(
            str(item) for item in row.get("evidence_artifact_hashes", [])
        ),
        reviewer=str(row.get("reviewer") or ""),
    )


def _action_candidate_from_dict(row: object) -> ActionCandidate:
    if not isinstance(row, dict):
        raise AnalysisBlocked("action_contract_invalid")
    return ActionCandidate(
        action_id=str(row.get("action_id") or ""),
        action_type=str(row.get("action_type") or ""),
        title=str(row.get("title") or ""),
        population_count=int(row.get("population_count") or 0),
        affected_products=tuple(str(item) for item in row.get("affected_products", [])),
        evidence_session_ids=tuple(str(item) for item in row.get("evidence_session_ids", [])),
        primary_failure_layer=str(row.get("primary_failure_layer") or ""),
        recommended_action=str(row.get("recommended_action") or ""),
        expected_value=str(row.get("expected_value") or ""),
        confidence=float(row.get("confidence") or 0),
        owner_role=str(row.get("owner_role") or ""),
        severity=int(row.get("severity") or 0),
        recurrence=int(row.get("recurrence") or 0),
        customer_blocking=int(row.get("customer_blocking") or 0),
        fix_cost=int(row.get("fix_cost") or 0),
        verifiability=int(row.get("verifiability") or 0),
    )


def _slice_from_dict(row: object) -> ReportSlice:
    if not isinstance(row, dict):
        raise AnalysisBlocked("slice_contract_invalid")
    return ReportSlice(
        label=str(row.get("label") or ""),
        session_count=int(row.get("session_count") or 0),
        description=str(row.get("description") or ""),
        example=str(row["example"]) if row.get("example") is not None else None,
        business_case_approved=bool(row.get("business_case_approved")),
        evidence_session_ids=tuple(
            str(item) for item in row.get("evidence_session_ids", [])
        ),
    )
