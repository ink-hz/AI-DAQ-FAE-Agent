"""Loop terminal answer contract.

``submit_answer`` is a runtime control-plane tool, not an evidence tool.  The
model submits typed answer fields once; the runtime validates and renders them
deterministically.  Free assistant text is retained only in the internal
transcript and is never delivered to the user.
"""
from __future__ import annotations

from dataclasses import dataclass

from src.agent.protocol import (
    OUTCOME_ESCALATE_FAE,
    OUTCOME_ESCALATE_RD,
    OUTCOME_RESOLVED,
    OUTCOME_SAFE_ABSTAINED,
)

SUBMIT_ANSWER_TOOL = "submit_answer"

_TEXT_FIELDS = (
    "conclusion",
    "basis",
    "cautions",
    "next_steps",
    "missing",
    "pending_confirmation",
    "handoff_reason",
    "unresolved_constraints",
)
_SELECTION_EVIDENCE_STATUSES = frozenset({
    "verified_fit", "engineering_candidate",
})
_ALLOWED_FIELDS = frozenset((
    "outcome", "selection_evidence_status", *_TEXT_FIELDS,
))


def submit_answer_tool_schema(*, include_delivery_units: bool = False) -> dict:
    """Return the OpenAI-format terminal tool schema.

    Gateways do not enforce JSON Schema consistently, so this schema is only
    model guidance. Bedrock-backed Anthropic gateways reject top-level
    ``oneOf``/``anyOf``/``allOf``, so outcome branches stay in descriptions and
    ``validate_submission`` remains the authority.
    """
    def text(description: str) -> dict:
        return {"type": "string", "minLength": 1, "description": description}

    schema = {
        "type": "function",
        "function": {
            "name": SUBMIT_ANSWER_TOOL,
            "description": (
                "提交最终用户答案并结束本轮。它是终止工具,必须单独调用;"
                "不能与取证工具混用。调用前的助手自由文本不会交付给用户。"
            ),
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "outcome": {
                        "type": "string",
                        "enum": [
                            OUTCOME_RESOLVED,
                            OUTCOME_SAFE_ABSTAINED,
                            OUTCOME_ESCALATE_RD,
                            OUTCOME_ESCALATE_FAE,
                        ],
                        "description": (
                            "resolved 需 conclusion;safe_abstained 需 missing;"
                            "escalate_rd 需 pending_confirmation;"
                            "escalate_fae 需 handoff_reason。"
                        ),
                    },
                    "conclusion": text(
                        "resolved 必填;其他 outcome 仅填写已确认部分;"
                        "safe_abstained 禁止。"),
                    "basis": text("可选,支撑结论的证据或工程依据。"),
                    "cautions": text("可选,限制条件、风险或适用边界。"),
                    "next_steps": text("可选,验证动作或最多两个澄清问题。"),
                    "missing": text(
                        "safe_abstained 必填,说明缺少什么证据;resolved 禁止。"),
                    "pending_confirmation": text(
                        "escalate_rd 必填,列出需研发或产品确认的事项。"),
                    "handoff_reason": text(
                        "escalate_fae 必填,说明需人工 FAE 介入的原因。"),
                    "selection_evidence_status": {
                        "type": "string",
                        "enum": sorted(_SELECTION_EVIDENCE_STATUSES),
                        "description": (
                            "开放式选型可选。verified_fit 表示事实矩阵存在已核验"
                            "可用候选；engineering_candidate 表示没有已证实完整"
                            "匹配，只交付工程评估候选，且必须填写"
                            " unresolved_constraints。"
                        ),
                    },
                    "unresolved_constraints": text(
                        "engineering_candidate 必填；逐条列出尚未由公开证据"
                        "闭合、必须样机或现场验证的硬约束。"),
                },
                "required": ["outcome"],
            },
        },
    }
    if include_delivery_units:
        schema['function']['parameters']['properties']['delivery_units'] = {
            'type': 'array', 'maxItems': 16,
            'description': ('DAQ 已核对且实际交付的子目标。每项正文会直接呈现给用户；'
                            '只填写本轮工具返回、已匹配该 requirement_id 的 source_id。'),
            'items': {'type': 'object', 'additionalProperties': False,
                      'properties': {
                          'requirement_id': {'type': 'string'},
                          'source_ids': {'type': 'array', 'items': {'type': 'string'}},
                          'text': {'type': 'string'},
                      }, 'required': ['requirement_id', 'source_ids', 'text']},
        }
    return schema


@dataclass(frozen=True)
class AnswerDeliveryUnit:
    requirement_id: str
    source_ids: tuple[str, ...]
    text: str


@dataclass(frozen=True)
class AnswerSubmission:
    outcome: str
    conclusion: str = ""
    basis: str = ""
    cautions: str = ""
    next_steps: str = ""
    missing: str = ""
    pending_confirmation: str = ""
    handoff_reason: str = ""
    selection_evidence_status: str = ""
    unresolved_constraints: str = ""
    delivery_units: tuple[AnswerDeliveryUnit, ...] = ()


def validate_submission(arguments: object, *, allow_delivery_units: bool = False
                        ) -> tuple[AnswerSubmission | None, list[str]]:
    """Validate a terminal submission without trusting provider enforcement."""
    if not isinstance(arguments, dict):
        return None, ["arguments_not_object"]

    errors: list[str] = []
    unknown = sorted(set(arguments) - _ALLOWED_FIELDS
                     - ({'delivery_units'} if allow_delivery_units else set()))
    if unknown:
        errors.append("unknown_fields:" + ",".join(unknown))

    outcome = arguments.get("outcome")
    allowed_outcomes = {
        OUTCOME_RESOLVED,
        OUTCOME_SAFE_ABSTAINED,
        OUTCOME_ESCALATE_RD,
        OUTCOME_ESCALATE_FAE,
    }
    if outcome not in allowed_outcomes:
        errors.append("invalid_outcome")

    selection_evidence_status = arguments.get("selection_evidence_status", "")
    if selection_evidence_status is None:
        selection_evidence_status = ""
    if not isinstance(selection_evidence_status, str):
        errors.append("non_string:selection_evidence_status")
        selection_evidence_status = ""
    else:
        selection_evidence_status = selection_evidence_status.strip()
    if (
        selection_evidence_status
        and selection_evidence_status not in _SELECTION_EVIDENCE_STATUSES
    ):
        errors.append("invalid_selection_evidence_status")

    values: dict[str, str] = {}
    for field in _TEXT_FIELDS:
        raw = arguments.get(field, "")
        if raw is None:
            raw = ""
        if not isinstance(raw, str):
            errors.append(f"non_string:{field}")
            values[field] = ""
            continue
        values[field] = raw.strip()
        if field in arguments and not values[field]:
            errors.append(f"empty_field:{field}")

    delivery_units: list[AnswerDeliveryUnit] = []
    if allow_delivery_units and 'delivery_units' in arguments:
        raw_units = arguments['delivery_units']
        if not isinstance(raw_units, list) or len(raw_units) > 16:
            errors.append('delivery_units_invalid')
        else:
            for raw in raw_units:
                if (not isinstance(raw, dict)
                        or set(raw) != {'requirement_id', 'source_ids', 'text'}
                        or not isinstance(raw['requirement_id'], str)
                        or not 0 < len(raw['requirement_id']) <= 128
                        or not isinstance(raw['source_ids'], list)
                        or not 0 < len(raw['source_ids']) <= 8
                        or not all(isinstance(item, str) and 0 < len(item) <= 256
                                   for item in raw['source_ids'])
                        or not isinstance(raw['text'], str)
                        or not 0 < len(raw['text'].strip()) <= 2000):
                    errors.append('delivery_unit_invalid')
                    continue
                delivery_units.append(AnswerDeliveryUnit(
                    raw['requirement_id'], tuple(dict.fromkeys(raw['source_ids'])),
                    raw['text'].strip()))

    if outcome == OUTCOME_RESOLVED:
        _require(values, "conclusion", errors)
        _forbid(values, ("missing", "pending_confirmation", "handoff_reason"), errors)
    elif outcome == OUTCOME_SAFE_ABSTAINED:
        _require(values, "missing", errors)
        _forbid(values, ("conclusion", "pending_confirmation", "handoff_reason"), errors)
    elif outcome == OUTCOME_ESCALATE_RD:
        _require(values, "pending_confirmation", errors)
        _forbid(values, ("handoff_reason",), errors)
    elif outcome == OUTCOME_ESCALATE_FAE:
        _require(values, "handoff_reason", errors)
        _forbid(values, ("pending_confirmation",), errors)

    if selection_evidence_status == "engineering_candidate":
        _require(values, "unresolved_constraints", errors)
    elif values.get("unresolved_constraints"):
        errors.append("unresolved_constraints_requires_engineering_candidate")

    if errors:
        return None, errors
    return AnswerSubmission(
        outcome=str(outcome),
        selection_evidence_status=selection_evidence_status,
        delivery_units=tuple(delivery_units),
        **values,
    ), []


def _require(values: dict[str, str], field: str, errors: list[str]) -> None:
    if not values.get(field):
        errors.append(f"missing_required:{field}")


def _forbid(values: dict[str, str], fields: tuple[str, ...], errors: list[str]) -> None:
    for field in fields:
        if values.get(field):
            errors.append(f"forbidden_field:{field}")


def render_submission(submission: AnswerSubmission) -> str:
    """Render validated fields without semantic rewriting."""
    sections: list[tuple[str, str]] = []
    delivered = '\n'.join(unit.text for unit in submission.delivery_units)
    outcome = submission.outcome
    if outcome == OUTCOME_RESOLVED:
        sections.append(("结论", submission.conclusion))
        if submission.selection_evidence_status == "engineering_candidate":
            sections.append((
                "候选性质", "工程评估候选（非已验证满足）",
            ))
            sections.append((
                "未闭合约束", submission.unresolved_constraints,
            ))
        _append(sections, "依据", submission.basis)
        _append(sections, "核实结果", delivered)
        _append(sections, "注意", submission.cautions)
        _append(sections, "验证与下一步", submission.next_steps)
    elif outcome == OUTCOME_SAFE_ABSTAINED:
        sections.append(("当前无法下结论", submission.missing))
        _append(sections, "已确认信息", '\n'.join(
            part for part in (submission.basis, delivered) if part))
        _append(sections, "注意", submission.cautions)
        _append(sections, "建议", submission.next_steps)
    elif outcome == OUTCOME_ESCALATE_RD:
        _append(sections, "已确认部分", submission.conclusion)
        _append(sections, "依据", submission.basis)
        _append(sections, "核实结果", delivered)
        _append(sections, "注意", submission.cautions)
        sections.append(("需研发确认", submission.pending_confirmation))
        _append(sections, "验证与下一步", submission.next_steps)
    else:
        _append(sections, "已确认部分", submission.conclusion)
        _append(sections, "依据", submission.basis)
        _append(sections, "核实结果", delivered)
        _append(sections, "注意", submission.cautions)
        sections.append(("建议转人工 FAE", submission.handoff_reason))
        _append(sections, "下一步", submission.next_steps)
    return "\n\n".join(f"**{title}**\n\n{body}" for title, body in sections)


def _append(sections: list[tuple[str, str]], title: str, value: str) -> None:
    if value:
        sections.append((title, value))
