"""Request-local DAQ evidence ledger for the shared Loop policy hook."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re

from src.agent.loop.answer_contract import AnswerSubmission
from src.agent.loop.evidence_policy import EvidenceDecision, EvidenceSnapshot
from src.agent.loop.tools import ToolResult

from daq_fae.service_contract import ServiceContract


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
    evidence_class: str
    source_ids: tuple[str, ...]


class DaqEvidencePolicy:
    def __init__(self, *, allow_dev_source_check: bool = False,
                 service_contract: ServiceContract | None = None,
                 answer_language: str = 'zh',
                 allowed_chinese_terms: tuple[str, ...] = ()):
        if answer_language not in {'zh', 'en'}:
            raise ValueError('daq_answer_language_invalid')
        self.allow_dev_source_check = allow_dev_source_check
        self.service_contract = service_contract
        self.answer_language = answer_language
        self.allowed_chinese_terms = allowed_chinese_terms

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
            evidence_class = entry.get("evidence_class", "governed_required")
            source_ids = entry.get("source_ids", [])
            if (evidence_class not in {"governed_required", "user_evidence_required",
                                       "service_contract", "clarification_required"}
                    or not isinstance(source_ids, list)
                    or any(not isinstance(value, str) or not value for value in source_ids)
                    or (evidence_class == "user_evidence_required" and not source_ids)):
                raise ValueError("daq_requirement_evidence_class_invalid")
            ids.add(requirement_id)
            parsed.append(_Requirement(
                requirement_id, capability, entry.get("critical", True) is True,
                evidence_class, tuple(dict.fromkeys(source_ids)),
            ))
        if not parsed:
            raise ValueError("daq_requirements_empty")
        return DaqEvidenceSession(tuple(parsed), allow_dev_source_check=self.allow_dev_source_check,
                                  service_contract=self.service_contract,
                                  answer_language=self.answer_language,
                                  allowed_chinese_terms=self.allowed_chinese_terms)


class DaqEvidenceSession:
    def __init__(self, requirements: tuple[_Requirement, ...], *,
                 allow_dev_source_check: bool = False,
                 service_contract: ServiceContract | None = None,
                 answer_language: str = 'zh',
                 allowed_chinese_terms: tuple[str, ...] = ()):
        self.requirements = requirements
        self.status = {
            item.id: ('satisfied' if item.evidence_class == 'service_contract'
                      and service_contract is not None
                      and service_contract.supports(item.capability) else 'unknown')
            for item in requirements
        }
        self.actual: list[str] = []
        self.tool_failed = False
        self.attachment_failed = False
        self.variant_selector_missing = False
        self._observed_user_sources = {item.id: set() for item in requirements}
        self._internal_provenance: set[str] = set()
        self.allow_dev_source_check = allow_dev_source_check
        self.answer_language = answer_language
        self.allowed_chinese_terms = allowed_chinese_terms

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
        if tool_name == 'lookup_spec' and result.status == 'not_found' and \
                content.get('reason') == 'variant_selector_required':
            self.variant_selector_missing = True
        if result.status == "ok":
            for match in content.get("matches", []):
                if isinstance(match, dict):
                    for value in (match.get("id"), (match.get("data") or {}).get("field")):
                        if isinstance(value, str) and value:
                            self._internal_provenance.add(value.casefold())
            for source in result.sources:
                if not isinstance(source, dict):
                    continue
                source_id = source.get("source_id")
                if isinstance(source_id, str) and source_id:
                    self._internal_provenance.add(source_id.casefold())
                for ref in source.get("source_refs", []):
                    if isinstance(ref, dict) and isinstance(ref.get("path"), str):
                        self._internal_provenance.add(ref["path"].casefold())
        matched = content.get("matched_requirement_ids", [])
        matched_ids = set(matched) if isinstance(matched, list) and all(
            isinstance(value, str) for value in matched
        ) else set()
        empty_release = content.get("reason") == "empty_knowledge_release"
        for item in self.requirements:
            if item.evidence_class in {'service_contract', 'clarification_required'}:
                continue
            if empty_release and result.status == "not_found" and item.evidence_class == "governed_required":
                if self.status[item.id] != "satisfied":
                    self.status[item.id] = "missing"
                continue
            if item.capability != tool_name:
                continue
            if item.evidence_class == "user_evidence_required":
                if result.status == "ok":
                    observed = {
                        source.get("source_id") for source in result.sources
                        if isinstance(source, dict) and source.get("type") == "user_attachment"
                    }
                    self._observed_user_sources[item.id].update(
                        observed.intersection(item.source_ids)
                    )
                    if set(item.source_ids) <= self._observed_user_sources[item.id]:
                        self.status[item.id] = "satisfied"
                elif result.status == "not_found":
                    self.status[item.id] = "missing"
                continue
            if item.id not in matched_ids and not (empty_release and result.status == "not_found"):
                continue
            previous = self.status[item.id]
            if result.status == "conflict":
                self.status[item.id] = "conflict"
            elif previous == "conflict":
                continue
            elif result.status == "ok" and _has_governed_source(
                    result, allow_dev_source_check=self.allow_dev_source_check):
                self.status[item.id] = "satisfied"
            elif result.status == "not_found" and previous != "satisfied":
                self.status[item.id] = "missing"

    def evaluate(self, submission: AnswerSubmission) -> EvidenceDecision:
        fields = (submission.conclusion, submission.basis, submission.cautions,
                  submission.next_steps, submission.missing,
                  submission.pending_confirmation, submission.handoff_reason,
                  submission.unresolved_constraints)
        visible_text = '\n'.join(value for value in fields if value)
        if self.answer_language == 'en':
            for term in self.allowed_chinese_terms:
                if isinstance(term, str) and term:
                    visible_text = visible_text.replace(term, '')
            if re.search(r'[\u3400-\u9fff]', visible_text):
                return EvidenceDecision(
                    'request_evidence', 'daq_answer_language_mismatch',
                    'Rewrite every answer field in English, including the conclusion, '
                    'basis, cautions, missing evidence and next steps. Preserve the '
                    'same facts, evidence limits, outcome and product names.',
                )
        elif (len(re.findall(r'\b[A-Za-z]{2,}\b', visible_text)) >= 20
              and len(re.findall(r'[\u3400-\u9fff]', visible_text)) < 8):
            return EvidenceDecision(
                'request_evidence', 'daq_answer_language_mismatch',
                '请把结论、依据、注意、缺口和下一步都改用中文陈述；保留相同事实、'
                '证据边界和终态，产品名、单位及 SDK 名称可保留原文。',
            )
        prose = "\n".join(value for value in (
            submission.conclusion, submission.basis, submission.cautions,
            submission.next_steps, submission.missing, submission.pending_confirmation,
            submission.handoff_reason, submission.unresolved_constraints,
        ) if value).casefold()
        if (re.search(r'\b[a-z]+(?:_[a-z]+)+\b', prose)
                or re.search(r'\b(?:claim|entity|topology|procedure|software|link):[a-z0-9_.:-]+', prose)
                or any(token in prose for token in self._internal_provenance)):
            return EvidenceDecision(
                "request_evidence", "daq_internal_provenance_in_answer",
                "请重新提交终稿：正文只用自然中文说明结论、适用条件和必要缺口；"
                "删除内部记录 ID、英文内部字段代码和原始文件路径。"
                "来源已保留在结构化 sources，不必重复取证。",
            )
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
            if self.variant_selector_missing and all(
                    item.capability == 'lookup_spec' and self.status[item.id] == 'missing'
                    for item in outstanding):
                return EvidenceDecision(
                    'request_evidence', 'daq_variant_selector_needed',
                    '用户尚未指定产品变体；即使逐个查到已核对的条件数值，也不能提交 '
                    'resolved。请按变体分别说明数值和适用范围，要求用户选择，并改用 '
                    'safe_abstained。',
                )
            return EvidenceDecision("reject", "daq_evidence_not_satisfied")
        return EvidenceDecision(
            "request_evidence", "daq_evidence_needed",
            "请调用相应数采取证工具核对关键需求；缺少已发布且按来源核对的资料时改用 safe_abstained。",
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


def _has_governed_source(result: ToolResult, *, allow_dev_source_check: bool = False) -> bool:
    return any(
        isinstance(source, dict)
        and ((source.get("type") == "daq_governed_claim"
              and source.get("verification_status") == "verified")
             or (allow_dev_source_check and source.get("type") == "daq_dev_source_checked_claim"
                 and source.get("verification_status") == "source_checked"))
        and isinstance(source.get("release_id"), str)
        and bool(source["release_id"].strip())
        and isinstance(source.get("source_id"), str)
        and bool(source["source_id"].strip())
        for source in result.sources
    )
