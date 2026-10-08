from __future__ import annotations

import html
import json
import math
import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from src.production_analytics.metrics import Metric

_ACTION_TYPES = {
    "agent_architecture",
    "knowledge",
    "official_material",
    "product",
    "operations",
    "evaluation",
}
_EMAIL = re.compile(r"\b[^\s@]+@[^\s@]+\.[^\s@]+\b")
_RANGE = re.compile(r"(?:\d[\d,.]*\s*[–—~-]\s*\d[\d,.]*)|(?:从.+(?:至|到).+)")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_ANALYSIS_ID = re.compile(
    r"(?<![A-Za-z0-9._-])[A-Za-z0-9._-]+:[0-9a-fA-F]{64}"
)


class ReportBlocked(ValueError):
    pass


@dataclass(frozen=True)
class Claim:
    claim_id: str
    claim_text: str
    claim_type: Literal["observed", "assisted", "scenario"]
    metric_ids: tuple[str, ...]
    numerator: int | float | None
    denominator: int | float | None
    filters: tuple[str, ...]
    assumptions: tuple[str, ...]
    evidence_artifacts: tuple[str, ...]
    evidence_artifact_hashes: tuple[str, ...]
    reviewer: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "claim_text": self.claim_text,
            "claim_type": self.claim_type,
            "metric_ids": list(self.metric_ids),
            "numerator": self.numerator,
            "denominator": self.denominator,
            "filters": list(self.filters),
            "assumptions": list(self.assumptions),
            "evidence_artifacts": list(self.evidence_artifacts),
            "evidence_artifact_hashes": list(self.evidence_artifact_hashes),
            "reviewer": self.reviewer,
        }


@dataclass(frozen=True)
class ActionCandidate:
    action_id: str
    action_type: str
    title: str
    population_count: int
    affected_products: tuple[str, ...]
    evidence_session_ids: tuple[str, ...]
    primary_failure_layer: str
    recommended_action: str
    expected_value: str
    confidence: float
    owner_role: str
    severity: int
    recurrence: int
    customer_blocking: int
    fix_cost: int
    verifiability: int


@dataclass(frozen=True)
class ActionBacklogItem:
    action_id: str
    action_type: str
    title: str
    population_count: int
    affected_products: tuple[str, ...]
    evidence_session_ids: tuple[str, ...]
    primary_failure_layer: str
    recommended_action: str
    expected_value: str
    confidence: float
    owner_role: str
    priority: str
    priority_score: float
    priority_components: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        population_count = self.population_count if self.population_count >= 5 else None
        return {
            "action_id": self.action_id,
            "action_type": self.action_type,
            "title": self.title,
            "population_count": population_count,
            "population_count_display": _population_count_display(self.population_count),
            "affected_products": list(self.affected_products),
            "primary_failure_layer": self.primary_failure_layer,
            "recommended_action": self.recommended_action,
            "expected_value": self.expected_value,
            "confidence": self.confidence,
            "owner_role": self.owner_role,
            "priority": self.priority,
            "priority_score": self.priority_score,
            "priority_components": dict(self.priority_components),
        }


@dataclass(frozen=True)
class ReportSlice:
    label: str
    session_count: int
    description: str
    example: str | None
    business_case_approved: bool
    evidence_session_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReportBundle:
    analysis_id: str
    period_label: str
    metrics: dict[str, Metric]
    claims: tuple[Claim, ...]
    backlog: tuple[ActionBacklogItem, ...]
    slices: tuple[ReportSlice, ...]
    private_context: dict[str, object] | None = None


def _has_metric_range(metric: Metric) -> bool:
    if not isinstance(metric.value, dict):
        return False
    numeric = [
        value
        for value in metric.value.values()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    return len(numeric) >= 2 and min(numeric) != max(numeric)


def _contains_private_analysis_id(values: Collection[str]) -> bool:
    return any(_ANALYSIS_ID.search(value) for value in values)


def _small_counts(value: object) -> set[int]:
    if isinstance(value, bool):
        return set()
    if isinstance(value, int):
        return {value} if 0 <= value < 5 else set()
    if isinstance(value, Mapping):
        return set().union(*(_small_counts(item) for item in value.values())) if value else set()
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return set().union(*(_small_counts(item) for item in value)) if value else set()
    return set()


def validate_claim(claim: Claim, *, metrics: dict[str, Metric]) -> None:
    if not claim.claim_id or not claim.claim_text:
        raise ReportBlocked("claim_invalid")
    if claim.claim_type not in {"observed", "assisted", "scenario"}:
        raise ReportBlocked("claim_type_invalid")
    if not claim.metric_ids or len(claim.metric_ids) != len(set(claim.metric_ids)):
        raise ReportBlocked("claim_metric_ids_invalid")
    missing = [metric_id for metric_id in claim.metric_ids if metric_id not in metrics]
    if missing:
        raise ReportBlocked(f"claim_unsubstantiated:{','.join(missing)}")
    referenced = [metrics[metric_id] for metric_id in claim.metric_ids]
    if any("small_cells_suppressed" in metric.method for metric in referenced):
        raise ReportBlocked("claim_public_distribution_forbidden")
    if any(metric.method.endswith("_count_cell") for metric in referenced):
        raise ReportBlocked("claim_distribution_cell_forbidden")
    scenario_reference = any(
        metric_id.startswith("value.scenario.")
        or metric_id.startswith("value.scenario_")
        or "scenario" in metric.method
        for metric_id, metric in zip(claim.metric_ids, referenced, strict=True)
    )
    if claim.claim_type == "observed" and scenario_reference:
        raise ReportBlocked("observed_claim_references_scenario")
    if claim.claim_type == "scenario":
        if not claim.assumptions or not any(_has_metric_range(metric) for metric in referenced):
            raise ReportBlocked("scenario_claim_requires_range")
        if not _RANGE.search(claim.claim_text):
            raise ReportBlocked("scenario_claim_requires_range_in_text")
    if not claim.evidence_artifacts or not claim.reviewer:
        raise ReportBlocked("claim_review_evidence_missing")
    if len(claim.evidence_artifacts) != len(claim.evidence_artifact_hashes):
        raise ReportBlocked("claim_evidence_hashes_mismatch")
    if any(not _SHA256.fullmatch(digest) for digest in claim.evidence_artifact_hashes):
        raise ReportBlocked("claim_evidence_hash_invalid")
    public_text = (
        claim.claim_id,
        claim.claim_text,
        *claim.filters,
        *claim.assumptions,
        *claim.evidence_artifacts,
        claim.reviewer,
    )
    if _contains_private_analysis_id(public_text):
        raise ReportBlocked("claim_contains_private_identifier")
    if _EMAIL.search(claim.claim_text):
        raise ReportBlocked("claim_contains_sensitive_identity")
    unsupported_value_language = (
        ("小时" in claim.claim_text or "金额" in claim.claim_text or "roi" in claim.claim_text.casefold())
        and not any(
            any(token in metric_id.casefold() for token in ("time_saved", "cost", "roi"))
            for metric_id in claim.metric_ids
        )
    )
    if unsupported_value_language:
        raise ReportBlocked("unsupported_time_or_money_claim")
    if len(referenced) > 1 and (claim.numerator is not None or claim.denominator is not None):
        raise ReportBlocked("claim_multi_metric_counts_forbidden")
    if _small_counts(claim.numerator) or _small_counts(claim.denominator):
        raise ReportBlocked("claim_small_population_unsuppressed")
    small_counts = set().union(
        *(
            _small_counts(metric.value)
            | _small_counts(metric.numerator)
            | _small_counts(metric.denominator)
            for metric in referenced
        )
    )
    if small_counts:
        raise ReportBlocked("claim_small_population_metric_forbidden")
    if len(referenced) == 1:
        metric = referenced[0]
        if claim.denominator is not None and metric.denominator != claim.denominator:
            raise ReportBlocked("claim_denominator_mismatch")
        expected_numerator = metric.numerator if metric.numerator is not None else metric.value
        if claim.numerator is not None and expected_numerator != claim.numerator:
            raise ReportBlocked("claim_numerator_mismatch")


def _score_component(value: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5:
        raise ReportBlocked(f"action_{field}_invalid")
    return value


def build_action_backlog(
    candidates: list[ActionCandidate] | tuple[ActionCandidate, ...],
    *,
    accepted_review_session_ids: Collection[str],
) -> tuple[ActionBacklogItem, ...]:
    accepted_ids = frozenset(accepted_review_session_ids)
    if any(not isinstance(session_id, str) or not session_id for session_id in accepted_ids):
        raise ReportBlocked("accepted_review_session_ids_invalid")
    seen: set[str] = set()
    items: list[ActionBacklogItem] = []
    for candidate in candidates:
        if not candidate.action_id or candidate.action_id in seen:
            raise ReportBlocked("action_id_invalid_or_duplicate")
        seen.add(candidate.action_id)
        if candidate.action_type not in _ACTION_TYPES:
            raise ReportBlocked("action_type_invalid")
        if candidate.population_count < 0:
            raise ReportBlocked("action_population_count_invalid")
        if candidate.population_count == 0 or not candidate.evidence_session_ids:
            raise ReportBlocked("action_evidence_required")
        if (
            len(candidate.evidence_session_ids) != len(set(candidate.evidence_session_ids))
            or candidate.population_count != len(candidate.evidence_session_ids)
        ):
            raise ReportBlocked("action_population_evidence_mismatch")
        if not set(candidate.evidence_session_ids) <= accepted_ids:
            raise ReportBlocked("action_evidence_not_accepted")
        public_text = (
            candidate.action_id,
            candidate.title,
            *candidate.affected_products,
            candidate.primary_failure_layer,
            candidate.recommended_action,
            candidate.expected_value,
            candidate.owner_role,
        )
        if _contains_private_analysis_id(public_text) or any(
            session_id in text
            for session_id in candidate.evidence_session_ids
            for text in public_text
        ):
            raise ReportBlocked("action_contains_private_identifier")
        if not 0 <= candidate.confidence <= 1:
            raise ReportBlocked("action_confidence_invalid")
        affected_score = min(5, max(1, math.ceil(math.log2(candidate.population_count + 1))))
        severity = _score_component(candidate.severity, "severity")
        recurrence = _score_component(candidate.recurrence, "recurrence")
        blocking = _score_component(candidate.customer_blocking, "customer_blocking")
        fix_cost = _score_component(candidate.fix_cost, "fix_cost")
        verifiability = _score_component(candidate.verifiability, "verifiability")
        priority_score = round(
            affected_score * 0.10
            + severity * 0.25
            + recurrence * 0.15
            + blocking * 0.25
            + (6 - fix_cost) * 0.10
            + verifiability * 0.15,
            3,
        )
        priority = (
            "P0"
            if priority_score >= 4.25
            else "P1"
            if priority_score >= 3.5
            else "P2"
            if priority_score >= 2.5
            else "P3"
        )
        items.append(
            ActionBacklogItem(
                action_id=candidate.action_id,
                action_type=candidate.action_type,
                title=candidate.title,
                population_count=candidate.population_count,
                affected_products=candidate.affected_products,
                evidence_session_ids=candidate.evidence_session_ids,
                primary_failure_layer=candidate.primary_failure_layer,
                recommended_action=candidate.recommended_action,
                expected_value=candidate.expected_value,
                confidence=candidate.confidence,
                owner_role=candidate.owner_role,
                priority=priority,
                priority_score=priority_score,
                priority_components={
                    "affected_sessions": affected_score,
                    "severity": severity,
                    "recurrence": recurrence,
                    "customer_blocking": blocking,
                    "fix_cost": fix_cost,
                    "verifiability": verifiability,
                },
            )
        )
    return tuple(sorted(items, key=lambda item: (-item.priority_score, item.action_id)))


def _jsonl(rows: list[dict[str, Any]]) -> str:
    return "".join(
        json.dumps(row, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
        for row in rows
    )


def _population_count_display(population_count: int) -> str:
    return "少于 5" if population_count < 5 else str(population_count)


def _slice_count(item: ReportSlice) -> str:
    return _population_count_display(item.session_count)


def _slice_markdown(item: ReportSlice) -> str:
    line = f"- {item.label} | {_slice_count(item)} | {item.description}"
    if item.example and item.business_case_approved:
        line += f" | 代表案例：{item.example}"
    return line


def _public_metric_value(value: object) -> object:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and 0 <= value < 5:
        return "少于 5"
    if isinstance(value, Mapping):
        return {str(key): _public_metric_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_public_metric_value(item) for item in value]
    return value


def _metric_text(metric: Metric) -> str:
    return json.dumps(_public_metric_value(metric.value), ensure_ascii=False, sort_keys=True)


def _audit_count_text(value: int | float | None) -> str:
    if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 5:
        return "少于 5"
    return str(value)


def _render_executive(bundle: ReportBundle) -> str:
    observed = [claim for claim in bundle.claims if claim.claim_type != "scenario"]
    scenarios = [claim for claim in bundle.claims if claim.claim_type == "scenario"]
    lines = [
        "# AI FAE Agent 生产应用分析",
        "",
        f"分析周期：{bundle.period_label}",
        "",
        "## 核心结论",
        "",
    ]
    lines.extend(f"- {claim.claim_text}" for claim in observed)
    if scenarios:
        lines.extend(("", "## 下一阶段潜力", ""))
        lines.extend(f"- {claim.claim_text}" for claim in scenarios)
    lines.extend(
        (
            "",
            "## 口径说明",
            "",
            "正文数字均来自同一 metrics 产物并在 Claim Ledger 中闭合；潜在价值与已实现价值分开呈现。",
            "",
        )
    )
    return "\n".join(lines)


def _render_full(bundle: ReportBundle) -> str:
    lines = [
        "# AI FAE Agent 全量生产会话分析",
        "",
        f"分析周期：{bundle.period_label}",
        "",
        "## 经复核的关键结论",
        "",
    ]
    lines.extend(f"- [{claim.claim_type}] {claim.claim_text}" for claim in bundle.claims)
    lines.extend(("", "## 需求与场景切片", ""))
    lines.extend(_slice_markdown(item) for item in bundle.slices)
    lines.extend(("", "## 行动清单", ""))
    if bundle.backlog:
        lines.extend(
            f"- {item.priority} · {item.title}：{item.recommended_action}"
            f"（证据范围：{_population_count_display(item.population_count)} 个 Session）"
            for item in bundle.backlog
        )
    else:
        lines.append("- 当前没有已审批的行动项。")
    lines.append("")
    return "\n".join(lines)


def _render_audit(bundle: ReportBundle) -> str:
    lines = [
        "# AI FAE Agent 分析审计附录",
        "",
        f"analysis_id={bundle.analysis_id}",
        "",
        "## Metrics",
        "",
    ]
    for metric_id, metric in sorted(bundle.metrics.items()):
        interval = (
            json.dumps(list(metric.interval), ensure_ascii=False)
            if metric.interval is not None
            else "null"
        )
        lines.append(
            f"- {metric_id}: value={_metric_text(metric)}; "
            f"numerator={_audit_count_text(metric.numerator)}; "
            f"denominator={_audit_count_text(metric.denominator)}; "
            f"method={metric.method}; interval={interval}"
        )
    lines.extend(("", "## Claims", ""))
    lines.extend(
        f"- {claim.claim_id}: metrics={','.join(claim.metric_ids)}; reviewer={claim.reviewer}"
        for claim in bundle.claims
    )
    lines.append("")
    return "\n".join(lines)


def _render_html(bundle: ReportBundle) -> str:
    claim_items = "".join(
        f"<li><strong>{html.escape(claim.claim_type)}</strong> "
        f"{html.escape(claim.claim_text)}</li>"
        for claim in bundle.claims
    )
    slice_rows = "".join(
        "<tr>"
        f"<td>{html.escape(item.label)}</td>"
        f"<td>{html.escape(_slice_count(item))}</td>"
        f"<td>{html.escape(item.description)}</td>"
        f"<td>{html.escape(item.example or '') if item.business_case_approved else ''}</td>"
        "</tr>"
        for item in bundle.slices
    )
    action_rows = "".join(
        "<tr>"
        f"<td><span class=\"priority {html.escape(item.priority.casefold())}\">"
        f"{html.escape(item.priority)}</span></td>"
        f"<td><strong>{html.escape(item.title)}</strong><br>"
        f"<span class=\"muted\">{html.escape(item.expected_value)}</span></td>"
        f"<td>{html.escape(item.recommended_action)}</td>"
        f"<td>{html.escape(_population_count_display(item.population_count))} "
        "个证据 Session</td>"
        "</tr>"
        for item in bundle.backlog
    )
    action_section = (
        "<h2>优先行动</h2><table><thead><tr>"
        "<th>优先级</th><th>问题</th><th>建议动作</th><th>证据范围</th>"
        f"</tr></thead><tbody>{action_rows}</tbody></table>"
        if action_rows
        else "<h2>优先行动</h2><p>当前没有已审批的行动项。</p>"
    )
    return (
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<title>AI FAE Agent 生产应用分析</title>"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<style>body{font-family:system-ui,-apple-system,sans-serif;margin:0 auto;"
        "padding:3rem 2rem;max-width:1120px;color:#172033;line-height:1.65;background:#fff}"
        "h1{font-size:2rem;margin:0 0 .35rem}h2{margin:2.5rem 0 1rem;font-size:1.3rem}"
        "ul{padding-left:1.25rem}li{margin:.65rem 0}"
        "table{border-collapse:separate;border-spacing:0;width:100%;font-size:.94rem;"
        "border:1px solid #dfe4ea;border-radius:10px;overflow:hidden}"
        "th,td{border-bottom:1px solid #e7ebef;padding:.75rem;vertical-align:top}"
        "tr:last-child td{border-bottom:0}th{background:#f5f7fa;text-align:left;white-space:nowrap}"
        ".priority{display:inline-block;padding:.12rem .45rem;border-radius:999px;font-weight:700}"
        ".p0{background:#fee2e2;color:#991b1b}.p1{background:#ffedd5;color:#9a3412}"
        ".p2{background:#fef3c7;color:#92400e}.p3{background:#e5e7eb;color:#374151}"
        ".muted{color:#657084}footer{margin-top:2rem;color:#657084;font-size:.9rem}"
        "@media(max-width:720px){body{padding:1.5rem 1rem}table{display:block;overflow-x:auto}}"
        "</style></head><body>"
        "<h1>AI FAE Agent 生产应用分析</h1>"
        f"<p class=\"muted\">分析周期：{html.escape(bundle.period_label)}</p>"
        f"<h2>核心结论</h2><ul>{claim_items}</ul>"
        "<h2>需求与场景切片</h2><table><thead><tr>"
        "<th>切片</th><th>Session</th><th>说明</th><th>审批案例</th>"
        f"</tr></thead><tbody>{slice_rows}</tbody></table>"
        f"{action_section}"
        "<footer>潜在价值与已实现价值分开呈现；详细分母、区间和方法见审计附录。"
        "本页不展示原始会话、客户身份或内部证据标识。</footer>"
        "</body></html>\n"
    )


def render_reports(bundle: ReportBundle) -> dict[str, str]:
    population_metric_ids = {
        "population.raw_sessions",
        "population.included",
        "population.excluded",
        "population.uncertain",
    }
    if not population_metric_ids <= set(bundle.metrics):
        raise ReportBlocked("audit_population_metrics_missing")
    population_values = {
        metric_id: bundle.metrics[metric_id].value for metric_id in population_metric_ids
    }
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float))
        for value in population_values.values()
    ) or population_values["population.raw_sessions"] != sum(
        population_values[metric_id]
        for metric_id in (
            "population.included",
            "population.excluded",
            "population.uncertain",
        )
    ):
        raise ReportBlocked("audit_population_denominator_not_closed")
    if _contains_private_analysis_id((bundle.period_label,)):
        raise ReportBlocked("period_contains_private_identifier")
    for item in bundle.slices:
        public_text = (item.label, item.description, item.example or "")
        if _contains_private_analysis_id(public_text) or any(
            session_id in text
            for session_id in item.evidence_session_ids
            for text in public_text
        ):
            raise ReportBlocked("slice_contains_private_identifier")
    claim_ids: set[str] = set()
    for claim in bundle.claims:
        if claim.claim_id in claim_ids:
            raise ReportBlocked("duplicate_claim_id")
        claim_ids.add(claim.claim_id)
        validate_claim(claim, metrics=bundle.metrics)
    return {
        "claim_ledger.jsonl": _jsonl([claim.to_dict() for claim in bundle.claims]),
        "action_backlog.jsonl": _jsonl([item.to_dict() for item in bundle.backlog]),
        "executive_summary.md": _render_executive(bundle),
        "full_report.md": _render_full(bundle),
        "audit_appendix.md": _render_audit(bundle),
        "report.html": _render_html(bundle),
    }
