"""答复协议单一定义 (C3 起点):outcome 枚举与 answer_contract 受控词表。

一处定义、多处消费。新增协议值必须先在这里登记,禁止在 planner/synthesizer/
eval 里散落新的自由字符串。
"""
from __future__ import annotations

# ── outcome 枚举 ─────────────────────────────────────────────────────────
OUTCOME_RESOLVED = "resolved"
OUTCOME_SAFE_ABSTAINED = "safe_abstained"
OUTCOME_ESCALATE_RD = "escalate_rd"          # 需研发/产品内部确认,用户补充信息也无法解决
OUTCOME_ESCALATE_FAE = "escalate_fae"        # 需人工 FAE 介入(现场判断/实物/多轮排查未收敛)
OUTCOME_UNSAFE_SUSPECTED = "unsafe_fail_suspected"  # 仅自检层下调使用,模型不得主动声明
OUTCOME_BUDGET_EXHAUSTED = "budget_exhausted"
OUTCOME_MAX_DURATION = "max_duration"
OUTCOME_EMPTY_ANSWER = "empty_answer"
OUTCOME_TRUNCATED_ANSWER = "truncated_answer"
OUTCOME_TOOL_XML_LEAK = "tool_xml_leak"
OUTCOME_UNSTRUCTURED_FINAL = "unstructured_final"
OUTCOME_INVALID_ANSWER_CONTRACT = "invalid_answer_contract"
OUTCOME_PROVIDER_REFUSAL = "provider_refusal"
OUTCOME_PROVIDER_UNAVAILABLE = "provider_unavailable"
OUTCOME_PROVIDER_CONFIGURATION_ERROR = "provider_configuration_error"

# 模型在合成层允许主动声明的 outcome
MODEL_DECLARABLE_OUTCOMES = frozenset({
    OUTCOME_RESOLVED,
    OUTCOME_SAFE_ABSTAINED,
    OUTCOME_ESCALATE_RD,
    OUTCOME_ESCALATE_FAE,
})

# done 事件/评测中允许出现的最终 outcome
FINAL_OUTCOMES = frozenset({
    OUTCOME_RESOLVED,
    OUTCOME_SAFE_ABSTAINED,
    OUTCOME_ESCALATE_RD,
    OUTCOME_ESCALATE_FAE,
    OUTCOME_UNSAFE_SUSPECTED,
    OUTCOME_BUDGET_EXHAUSTED,
    OUTCOME_MAX_DURATION,
    OUTCOME_EMPTY_ANSWER,
    OUTCOME_TRUNCATED_ANSWER,
    OUTCOME_TOOL_XML_LEAK,
    OUTCOME_UNSTRUCTURED_FINAL,
    OUTCOME_INVALID_ANSWER_CONTRACT,
    OUTCOME_PROVIDER_REFUSAL,
    OUTCOME_PROVIDER_UNAVAILABLE,
    OUTCOME_PROVIDER_CONFIGURATION_ERROR,
})

RUNTIME_FAILURE_OUTCOMES = frozenset({
    OUTCOME_BUDGET_EXHAUSTED,
    OUTCOME_MAX_DURATION,
    OUTCOME_EMPTY_ANSWER,
    OUTCOME_TRUNCATED_ANSWER,
    OUTCOME_TOOL_XML_LEAK,
    OUTCOME_UNSTRUCTURED_FINAL,
    OUTCOME_INVALID_ANSWER_CONTRACT,
    OUTCOME_PROVIDER_UNAVAILABLE,
    OUTCOME_PROVIDER_CONFIGURATION_ERROR,
})

# escalate_rd 与 safe_abstained 的语义边界:
# - safe_abstained: 缺证据,通常可由用户补充信息/实测解决;
# - escalate_rd:   答案只能由研发/产品内部给出(未公开引脚、固件版本承诺、
#                  未收录规格的承诺、认证适用性),正文必须给"已确认部分 +
#                  需研发确认的具体问题清单"。


def is_model_declarable(outcome: str) -> bool:
    return outcome in MODEL_DECLARABLE_OUTCOMES


def is_final_outcome(outcome: str) -> bool:
    return outcome in FINAL_OUTCOMES


# ── answer_contract 受控词表 ────────────────────────────────────────────
# 现存散落于 planner/synthesizer 的契约字符串在此登记;新增契约先登记再使用。
ANSWER_CONTRACTS = frozenset({
    "allow_bounded_general_engineering_answer",
    "bounded_answer_before_clarifying",
    "use_user_provided_premise_with_boundary",
    "separate_confirmed_from_unknown",
    "no_negative_from_missing_evidence",
    "requires_product_anchor_for_model_specific_specs",
    "rank_candidates_by_constraints_and_risk",
    "escalate_when_internal_confirmation_required",
})


def is_known_contract(contract: str) -> bool:
    return contract in ANSWER_CONTRACTS
