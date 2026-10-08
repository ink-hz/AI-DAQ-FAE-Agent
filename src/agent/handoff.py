"""转接上下文打包 (D4/问答难点 3.8:escalation 是产品功能,转接要带上下文)。

代码侧从会话状态聚合,不靠模型复述——接手的研发/FAE 直接拿到已收集的
场景、约束、候选与排除项,不用让用户从头再说一遍。
"""
from __future__ import annotations

from src.agent.protocol import OUTCOME_ESCALATE_FAE, OUTCOME_ESCALATE_RD

_ESCALATION_TARGETS = {
    OUTCOME_ESCALATE_RD: "rd",
    OUTCOME_ESCALATE_FAE: "fae",
}


def is_escalation_outcome(outcome: str | None) -> bool:
    return outcome in _ESCALATION_TARGETS


def build_handoff_package(state, outcome: str, user_message: str) -> dict:
    """从 ConsultationState 聚合转接包;state 为 None 时给最小包。"""
    target = _ESCALATION_TARGETS.get(outcome, "unknown")
    if state is None:
        return {
            "target": target,
            "question": user_message,
            "scenario": [],
            "constraints": [],
            "products": [],
            "candidate_models": [],
            "excluded_models": [],
            "pending_questions": [],
        }
    return {
        "target": target,
        "question": user_message,
        "scenario": list(getattr(state, "scenario", []) or []),
        "constraints": list(getattr(state, "constraints", []) or []),
        "products": list(getattr(state, "products", []) or []),
        "platforms": list(getattr(state, "platforms", []) or []),
        "technical_components": list(getattr(state, "technical_components", []) or []),
        "candidate_models": list(getattr(state, "candidate_models", []) or []),
        "excluded_models": list(getattr(state, "excluded_models", []) or []),
        "pending_questions": list(getattr(state, "pending_questions", []) or []),
    }
