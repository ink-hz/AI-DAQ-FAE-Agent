"""Request-local DAQ evidence ledger for the shared Loop policy hook."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from src.agent.loop.answer_contract import AnswerSubmission
from src.agent.loop.evidence_policy import EvidenceDecision, EvidenceSnapshot
from src.agent.loop.tools import ToolResult


def question_requirements(_question: str) -> dict[str, object]:
    """Represent the universal governed-evidence need before a domain planner exists.

    The empty release has no entity/schema facts to plan narrower claims. The
    requirement stays explicit so an empty plan cannot be mistaken for coverage.
    """
    return {"requirements": [{
        "id": "question_evidence", "capability": "search_knowledge", "critical": True,
    }]}


@dataclass(frozen=True)
class _Requirement:
    id: str
    capability: str
    critical: bool


class DaqEvidencePolicy:
    def begin(self, requirements: Mapping[str, object]) -> "DaqEvidenceSession":
        raw = requirements.get("requirements", [])
        if not isinstance(raw, list):
            raise ValueError("daq_requirements_invalid")
        parsed: list[_Requirement] = []
        ids: set[str] = set()
        for entry in raw:
            if not isinstance(entry, dict):
                raise ValueError("daq_requirement_invalid")
            requirement_id = entry.get("id")
            capability = entry.get("capability")
            if (not isinstance(requirement_id, str) or not requirement_id.strip()
                    or not isinstance(capability, str) or not capability.strip()
                    or requirement_id in ids):
                raise ValueError("daq_requirement_invalid")
            ids.add(requirement_id)
            parsed.append(_Requirement(requirement_id, capability, entry.get("critical", True) is True))
        if not parsed:
            raise ValueError("daq_requirements_empty")
        return DaqEvidenceSession(tuple(parsed))


class DaqEvidenceSession:
    def __init__(self, requirements: tuple[_Requirement, ...]):
        self.requirements = requirements
        self.status = {item.id: "unknown" for item in requirements}
        self.actual: list[str] = []
        self.tool_failed = False
        self.attachment_failed = False

    def observe(self, tool_name: str, result: ToolResult) -> None:
        if tool_name not in self.actual:
            self.actual.append(tool_name)
        if result.status == "tool_error":
            code = result.content.get("error") if isinstance(result.content, dict) else None
            if tool_name == "analyze_image" and code in {
                "vision_unavailable", "vision_timeout", "vision_http_error",
                "vision_transport_error", "vision_invalid_response",
                "vision_output_truncated", "vision_runtime_error",
            }:
                self.attachment_failed = True
            else:
                self.tool_failed = True
            return
        content = result.content if isinstance(result.content, dict) else {}
        matched = content.get("matched_requirement_ids", [])
        matched_ids = set(matched) if isinstance(matched, list) and all(
            isinstance(value, str) for value in matched
        ) else set()
        empty_release = content.get("reason") == "empty_knowledge_release"
        for item in self.requirements:
            if item.capability != tool_name:
                continue
            if item.id not in matched_ids and not (empty_release and result.status == "not_found"):
                continue
            previous = self.status[item.id]
            if result.status == "conflict":
                self.status[item.id] = "conflict"
            elif previous == "conflict":
                continue
            elif result.status == "ok" and _has_governed_source(result):
                self.status[item.id] = "satisfied"
            elif result.status == "not_found" and previous != "satisfied":
                self.status[item.id] = "missing"

    def evaluate(self, submission: AnswerSubmission) -> EvidenceDecision:
        if self.tool_failed:
            return EvidenceDecision("reject", "daq_evidence_tool_failure")
        if self.attachment_failed and submission.outcome == "resolved":
            return EvidenceDecision("reject", "daq_attachment_evidence_failed")
        if submission.outcome != "resolved":
            if submission.conclusion and not any(
                value == "satisfied" for value in self.status.values()
            ):
                return EvidenceDecision("reject", "daq_unverified_partial_conclusion")
            return EvidenceDecision("allow")
        outstanding = [
            item for item in self.requirements
            if item.critical and self.status[item.id] != "satisfied"
        ]
        if not outstanding:
            return EvidenceDecision("allow")
        if any(self.status[item.id] in {"missing", "conflict"} for item in outstanding):
            return EvidenceDecision("reject", "daq_evidence_not_satisfied")
        return EvidenceDecision(
            "request_evidence", "daq_evidence_needed",
            "请调用相应数采取证工具核对关键需求；缺少已审核资料时改用 safe_abstained。",
        )

    def snapshot(self) -> EvidenceSnapshot:
        planned = tuple(dict.fromkeys(item.capability for item in self.requirements))
        coverage: dict[str, str] = {}
        for capability in planned:
            statuses = [
                self.status[item.id] for item in self.requirements
                if item.capability == capability
            ]
            coverage[capability] = (
                "full" if all(status == "satisfied" for status in statuses)
                else "partial" if "satisfied" in statuses
                else "empty" if "missing" in statuses
                else "unknown"
            )
        return EvidenceSnapshot(
            planned_capabilities=planned,
            actual_capabilities=tuple(self.actual),
            capability_coverage=coverage,
            requirement_status=self.status,
        )


def _has_governed_source(result: ToolResult) -> bool:
    return any(
        isinstance(source, dict)
        and source.get("type") == "daq_governed_claim"
        and source.get("verification_status") == "verified"
        and isinstance(source.get("release_id"), str)
        and bool(source["release_id"].strip())
        and isinstance(source.get("source_id"), str)
        and bool(source["source_id"].strip())
        for source in result.sources
    )
