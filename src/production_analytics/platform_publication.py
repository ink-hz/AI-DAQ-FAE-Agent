from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import jcs
from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from typing_extensions import Annotated

from src.production_analytics.canonical import AnalyticsIdentityKey, analysis_id

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = ROOT / "contracts" / "platform"
SCHEMA_PATH = CONTRACT_ROOT / "fae_analysis_report_v1.schema.json"
EXPECTED_SCHEMA_DIGEST_PATH = CONTRACT_ROOT / "fae_analysis_report_v1.sha256"
REPORT_ARTIFACTS = (
    "metrics.json",
    "claim_ledger.jsonl",
    "action_backlog.jsonl",
    "executive_summary.md",
    "full_report.md",
    "audit_appendix.md",
    "report.html",
)
DIMENSION_METRICS: dict[str, tuple[str, ...]] = {
    "usage": (
        "value.observed_included_sessions", "value.observed_included_turns",
        "value.observed_multiturn_sessions", "value.observed_attachment_sessions",
        "value.observed_non_work_hour_sessions", "product.family_counts_public",
        "demand.intent_capability_counts_public",
    ),
    "business_value": (
        "value.assisted_reviewed_sessions",
        "value.scenario_potential_conversion_sessions",
    ),
    "answer_effectiveness": (
        "quality.reviewed_count", "quality.reviewed_fully_resolved_rate",
        "quality.reviewed_first_turn_resolution_rate",
        "quality.reviewed_multiturn_convergence_rate", "feedback.bad_affected_sessions",
        "feedback.bad_affected_turns", "reliability.fallback_turn_rate",
        "latency.overall_ms",
    ),
    "insights_improvement": (
        "feedback.canonical_issues", "product.signal_counts_public",
        "product.scenario_counts_public", "workflow.failure_layer_counts_public",
    ),
}
METRIC_LABELS = {
    "value.observed_included_sessions": "累计服务会话",
    "value.observed_included_turns": "累计回答轮次",
    "value.observed_multiturn_sessions": "多轮会话",
    "value.observed_attachment_sessions": "图片或附件会话",
    "value.observed_non_work_hour_sessions": "非工作时段会话",
    "product.family_counts_public": "高频产品族",
    "demand.intent_capability_counts_public": "高频能力需求",
    "value.assisted_reviewed_sessions": "已形成 FAE 辅助价值的会话",
    "value.scenario_potential_conversion_sessions": "潜在可转化会话",
    "quality.reviewed_count": "独立复审会话",
    "quality.reviewed_fully_resolved_rate": "完全解决率",
    "quality.reviewed_first_turn_resolution_rate": "首轮解决率",
    "quality.reviewed_multiturn_convergence_rate": "多轮收敛率",
    "feedback.bad_affected_sessions": "负反馈影响会话",
    "feedback.bad_affected_turns": "负反馈影响回答",
    "reliability.fallback_turn_rate": "Fallback 回答占比",
    "latency.overall_ms": "回答时延分位数",
    "feedback.canonical_issues": "受治理根因家族",
    "product.signal_counts_public": "产品与资料信号",
    "product.scenario_counts_public": "业务场景分布",
    "workflow.failure_layer_counts_public": "问题层级分布",
}


class PublicationBlocked(RuntimeError):
    pass


class PublicationMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    report_id: Annotated[
        str,
        StringConstraints(pattern=r"^fae-(weekly|topic)-[a-z0-9][a-z0-9-]{2,63}$"),
    ]
    report_version: int = Field(ge=1)
    report_type: Literal["weekly", "topic"]
    title: str = Field(min_length=1, max_length=160)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PublicationBlocked(f"artifact_invalid:{path.name}") from exc
    if not isinstance(value, dict):
        raise PublicationBlocked(f"artifact_invalid:{path.name}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text("utf-8").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            if not isinstance(item, dict):
                raise ValueError
            rows.append(item)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise PublicationBlocked(f"artifact_invalid:{path.name}") from exc
    return rows


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _safe_analysis_dir(path: Path) -> Path:
    if path.is_symlink() or not path.is_dir():
        raise PublicationBlocked("analysis_directory_invalid")
    resolved = path.resolve()
    for child in resolved.rglob("*"):
        if child.is_symlink():
            raise PublicationBlocked("private_artifact_symlink")
    return resolved


class PlatformReportPublisher:
    def __init__(
        self,
        *,
        identity_key: AnalyticsIdentityKey | None,
        recover_historical_v5_order: bool = False,
    ) -> None:
        self._identity_key = identity_key
        self._recover_historical_v5_order = recover_historical_v5_order

    def publish(
        self,
        analysis_dir: Path,
        output_path: Path,
        metadata: PublicationMetadata,
    ) -> Path:
        root = _safe_analysis_dir(analysis_dir)
        output = output_path.absolute()
        if output.is_symlink() or output.parent.resolve() != root:
            raise PublicationBlocked("output_outside_analysis_directory")
        manifest = _read_json(root / "analysis_manifest.json")
        if manifest.get("status") != "report_ready":
            raise PublicationBlocked("analysis_not_report_ready")
        self._verify_artifacts(root, manifest)
        metrics_source = _read_json(root / "metrics.json")
        metrics = self._publish_metrics(metrics_source)
        report_config = _read_json(root / "report_config.json")
        canonical_sessions = self._reverse_session_index(root, manifest)
        recommendations, findings = self._publish_actions(
            root, report_config, canonical_sessions
        )
        claims = _read_jsonl(root / "claim_ledger.jsonl")
        artifact_digests = {name: _sha(root / name) for name in REPORT_ARTIFACTS}
        generated_at = self._generated_at(root, manifest)
        window_end = datetime.fromisoformat(str(manifest["window_end"]))
        source_cutoff = datetime.fromisoformat(str(manifest["snapshot_completed_at"]))
        effective_period_end = min(window_end, source_cutoff).isoformat()
        source_counts = manifest.get("exported_row_counts") or {}
        reviewed = int(metrics_source["quality.reviewed_count"]["value"])
        document = {
            "schema_name": "fae.analysis-report",
            "schema_version": "1.0.0",
            "report_id": metadata.report_id,
            "report_version": metadata.report_version,
            "report_type": metadata.report_type,
            "status": "ready",
            "title": metadata.title,
            "period": {
                "start_at": str(manifest["window_start"]),
                "end_at": effective_period_end,
            },
            "data_cutoff_at": str(manifest["snapshot_completed_at"]),
            "generated_at": generated_at,
            "analysis_version": str(manifest["analysis_id"]),
            "source": {
                "agent_id": "ai-fae-agent",
                "source_kind": "fae",
                "environment": "production",
                "source_snapshot_at": str(manifest["snapshot_completed_at"]),
                "session_count": int(source_counts.get("sessions", 0)),
                "turn_count": int(source_counts.get("turns", 0)),
                "feedback_event_count": int(source_counts.get("feedback", 0)),
                "reviewed_session_count": reviewed,
            },
            "summary": {
                "headline": self._claim_text(claims, "observed-scale", metadata.title),
                "overview": self._claim_text(
                    claims,
                    "assisted-and-conversion-value",
                    "已经实现的辅助价值与潜在转化价值分别统计，不把潜力写成成绩。",
                ),
                "top_finding_ids": [item["finding_id"] for item in findings[:5]],
                "top_recommendation_ids": [
                    item["recommendation_id"] for item in recommendations[:5]
                ],
            },
            "metrics": metrics,
            "findings": findings,
            "recommendations": recommendations,
            "cases": [],
            "artifact_digests": artifact_digests,
            "failure": None,
        }
        self._validate_contract(document)
        rendered = jcs.canonicalize(document)
        self._write_private_atomic(output, rendered)
        return output

    def _verify_artifacts(self, root: Path, manifest: dict[str, Any]) -> None:
        outputs = manifest.get("stage_output_hashes") or {}
        report_hashes = outputs.get("report") or {}
        metric_hash = (outputs.get("metrics_reviewed") or {}).get("metrics.json")
        expected = {name for name in REPORT_ARTIFACTS if name != "metrics.json"}
        if set(report_hashes) != expected or not isinstance(metric_hash, str):
            raise PublicationBlocked("report_artifacts_unavailable")
        expected_hashes = {**report_hashes, "metrics.json": metric_hash}
        for name, digest in expected_hashes.items():
            path = root / name
            if not path.is_file() or path.is_symlink() or _sha(path) != digest:
                raise PublicationBlocked("report_artifact_changed")

    def _publish_metrics(self, source: dict[str, Any]) -> list[dict[str, Any]]:
        published: list[dict[str, Any]] = []
        for dimension, ids in DIMENSION_METRICS.items():
            for metric_id in ids:
                raw = source.get(metric_id)
                if not isinstance(raw, dict):
                    raise PublicationBlocked(f"required_metric_missing:{metric_id}")
                value = raw.get("value")
                numerator = raw.get("numerator")
                denominator = raw.get("denominator")
                if metric_id == "latency.overall_ms":
                    unit = "milliseconds_distribution"
                elif isinstance(value, dict):
                    unit = "distribution"
                elif numerator is not None and denominator is not None:
                    unit = "ratio"
                else:
                    unit = "count"
                filters = [str(item)[:160] for item in raw.get("filters") or []]
                filters.append(f"method={str(raw.get('method') or 'unknown')[:120]}")
                published.append(
                    {
                        "metric_id": metric_id,
                        "dimension": dimension,
                        "label": METRIC_LABELS[metric_id],
                        "value": value,
                        "unit": unit,
                        "numerator": numerator,
                        "denominator": denominator,
                        "filters": filters,
                        "assumptions": [],
                        "evidence_artifact_refs": ["metrics.json", "audit_appendix.md"],
                    }
                )
        return published

    def _reverse_session_index(
        self, root: Path, manifest: dict[str, Any]
    ) -> dict[str, str]:
        if self._identity_key is None:
            return self._recover_v5_session_index(root, manifest)
        index: dict[str, str] = {}
        for row in _read_jsonl(root / "raw_snapshot" / "sessions.jsonl"):
            source_id = row.get("external_session_id")
            canonical_id = row.get("id")
            if not isinstance(source_id, str) or not isinstance(canonical_id, str):
                raise PublicationBlocked("source_session_identity_invalid")
            private_id = analysis_id(source_id, self._identity_key)
            if private_id in index:
                raise PublicationBlocked("duplicate_analysis_session_id")
            index[private_id] = f"fae:{canonical_id}"
        return index

    def _recover_v5_session_index(
        self, root: Path, manifest: dict[str, Any]
    ) -> dict[str, str]:
        if (
            not self._recover_historical_v5_order
            or manifest.get("analysis_id") != "fae-production-through-20260831-v5"
        ):
            raise PublicationBlocked("identity_key_required")
        raw_sessions = sorted(
            _read_jsonl(root / "raw_snapshot" / "sessions.jsonl"),
            key=lambda row: str(row.get("external_session_id") or ""),
        )
        canonical_sessions = _read_jsonl(root / "canonical_sessions.jsonl")
        if len(raw_sessions) != len(canonical_sessions) or not raw_sessions:
            raise PublicationBlocked("historical_session_order_mismatch")
        turns_by_external: dict[str, int] = {}
        for turn in _read_jsonl(root / "raw_snapshot" / "turns.jsonl"):
            external = str(turn.get("external_session_id") or "")
            turns_by_external[external] = turns_by_external.get(external, 0) + 1
        index: dict[str, str] = {}
        for raw, canonical in zip(raw_sessions, canonical_sessions, strict=True):
            external = str(raw.get("external_session_id") or "")
            source_id = str(raw.get("id") or "")
            private_id = str(canonical.get("analysis_session_id") or "")
            if (
                not external
                or not source_id
                or not private_id.startswith("v1-20260831:")
                or canonical.get("channel") != raw.get("channel")
                or int(canonical.get("turn_count") or 0) != turns_by_external.get(external, 0)
                or private_id in index
            ):
                raise PublicationBlocked("historical_session_order_mismatch")
            index[private_id] = f"fae:{source_id}"
        return index

    def _publish_actions(
        self,
        root: Path,
        report_config: dict[str, Any],
        canonical_sessions: dict[str, str],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        accepted = {
            str(row.get("action_id")): row
            for row in _read_jsonl(root / "action_backlog.jsonl")
        }
        configured = {
            str(row.get("action_id")): row
            for row in report_config.get("action_candidates") or []
            if isinstance(row, dict)
        }
        recommendations: list[dict[str, Any]] = []
        findings: list[dict[str, Any]] = []
        for action_id, action in sorted(
            accepted.items(), key=lambda item: (str(item[1].get("priority")), item[0])
        ):
            private = configured.get(action_id)
            if private is None:
                raise PublicationBlocked("accepted_action_source_missing")
            evidence = []
            for private_id in private.get("evidence_session_ids") or []:
                canonical = canonical_sessions.get(str(private_id))
                if canonical is None:
                    raise PublicationBlocked("unknown_analysis_session_id")
                evidence.append(
                    {"kind": "session", "canonical_key": canonical, "label": f"证据 Session {len(evidence) + 1:02d}"}
                )
                if len(evidence) == 5:
                    break
            if not evidence:
                raise PublicationBlocked("accepted_action_evidence_missing")
            recommendation_id = f"recommendation-{action_id}"
            finding_id = f"finding-{action_id}"
            priority = str(action.get("priority") or "P2").lower()
            if priority not in {"p0", "p1", "p2", "p3"}:
                priority = "p2"
            recommendation = {
                "recommendation_id": recommendation_id,
                "dimension": "insights_improvement",
                "priority": priority,
                "title": str(action.get("title") or action_id)[:160],
                "rationale": str(action.get("expected_value") or "受治理分析建议推进该行动。")[:2000],
                "proposed_action": str(action.get("recommended_action") or "按问题治理闭环推进。")[:2000],
                "owner_role": str(action.get("owner_role") or "FAE owner")[:80],
                "finding_ids": [finding_id],
                "success_metric_ids": ["product.signal_counts_public"],
            }
            severity = {"p0": "high", "p1": "medium", "p2": "low", "p3": "opportunity"}[priority]
            finding = {
                "finding_id": finding_id,
                "dimension": "insights_improvement",
                "severity": severity,
                "title": str(action.get("title") or action_id)[:160],
                "description": str(action.get("expected_value") or "受治理分析发现可改进事项。")[:2000],
                "root_cause_hypothesis": f"问题主要集中在 {str(action.get('primary_failure_layer') or 'unknown')[:120]} 层。",
                "impact_scope": f"受治理样本覆盖 {int(action.get('population_count') or 0)} 个 Session。",
                "metric_ids": ["product.signal_counts_public", "workflow.failure_layer_counts_public"],
                "evidence_refs": evidence,
                "recommendation_ids": [recommendation_id],
                "linked_issue_ids": [],
            }
            recommendations.append(recommendation)
            findings.append(finding)
        return recommendations, findings

    def _validate_contract(self, document: dict[str, Any]) -> None:
        schema_bytes = SCHEMA_PATH.read_bytes()
        expected = EXPECTED_SCHEMA_DIGEST_PATH.read_text("ascii").strip()
        if hashlib.sha256(schema_bytes).hexdigest() != expected:
            raise PublicationBlocked("pinned_contract_digest_mismatch")
        schema = json.loads(schema_bytes)
        errors = sorted(Draft202012Validator(schema).iter_errors(document), key=lambda item: list(item.path))
        if errors:
            raise PublicationBlocked("platform_report_contract_invalid")

    @staticmethod
    def _claim_text(claims: list[dict[str, Any]], claim_id: str, fallback: str) -> str:
        for claim in claims:
            if claim.get("claim_id") == claim_id and isinstance(claim.get("claim_text"), str):
                return str(claim["claim_text"])[:2000]
        return fallback

    @staticmethod
    def _generated_at(root: Path, manifest: dict[str, Any]) -> str:
        reviews = root / "accepted_reviews.jsonl"
        latest: datetime | None = None
        if reviews.is_file():
            for row in _read_jsonl(reviews):
                value = row.get("reviewed_at")
                if isinstance(value, str):
                    candidate = datetime.fromisoformat(value)
                    latest = candidate if latest is None or candidate > latest else latest
        if latest is None:
            latest = datetime.fromtimestamp(
                max((root / name).stat().st_mtime for name in REPORT_ARTIFACTS), tz=UTC
            )
        cutoff = datetime.fromisoformat(str(manifest["snapshot_completed_at"]))
        if latest < cutoff:
            latest = cutoff
        return latest.isoformat()

    @staticmethod
    def _write_private_atomic(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
            with os.fdopen(fd, "wb", closefd=True) as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
