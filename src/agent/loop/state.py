"""Loop 会话显式状态模型(设计:Loop会话状态模型设计_20260708.md)。

核心纪律:
- 状态更新**全部由代码从循环的确定性产物提取**,不新增 LLM 调用;
- 不自动推断"排除"(语义判断写错状态比没有状态更糟);
- 主题切换软重置(归档到 previous_topic,可经 session_state 工具读档);
- 上限丢弃(型号 ≤8、结论 ≤5)是显式设计,不是截断副作用。
"""
from __future__ import annotations

from dataclasses import dataclass, field

_ACTIVE_MODELS_CAP = 8
_CONCLUSIONS_CAP = 5
_SUMMARY_CAP = 200
_TOPIC_CAP = 60
# 只有这些工具的 model_id 入参算"查证过该型号"
_MODEL_BEARING_TOOLS = ("fact_lookup", "read_doc")
_OK_STATUSES = ("ok",)


@dataclass
class LoopSessionState:
    active_models: list[str] = field(default_factory=list)   # 最近在前
    constraints: list[str] = field(default_factory=list)
    conclusions: list[dict] = field(default_factory=list)    # {turn, models, outcome, summary}
    topic: str = ""
    previous_topic: dict | None = None                        # 主题切换归档


def archive_active_topic(state: LoopSessionState | None) -> LoopSessionState:
    """Archive active Loop memory before a new topic reaches the planner."""

    if state is None:
        return LoopSessionState()
    has_active_topic = bool(
        state.topic
        or state.active_models
        or state.constraints
        or state.conclusions
    )
    if not has_active_topic:
        return LoopSessionState(previous_topic=state.previous_topic)
    return LoopSessionState(previous_topic={
        "topic": state.topic,
        "active_models": list(state.active_models),
        "constraints": list(state.constraints),
    })


def update_from_turn(state: LoopSessionState | None, *, question: str,
                     answer: str, outcome: str, tool_calls: list[dict],
                     canonicalize, constraints: list[str],
                     is_followup: bool, is_topic_switch: bool,
                     turn: int) -> LoopSessionState:
    """本轮 done 产物 → 新状态。canonicalize(model_id)->canonical|None 由调用方注入。"""
    state = state or LoopSessionState()
    if is_topic_switch:
        state = archive_active_topic(state)

    turn_models: list[str] = []
    for call in tool_calls or []:
        if call.get("tool") not in _MODEL_BEARING_TOOLS:
            continue
        if call.get("status") not in _OK_STATUSES:
            continue
        canonical = canonicalize(str((call.get("input") or {}).get("model_id") or ""))
        if canonical and canonical not in turn_models:
            turn_models.append(canonical)

    merged = turn_models + [m for m in state.active_models if m not in turn_models]
    state.active_models = merged[:_ACTIVE_MODELS_CAP]

    state.conclusions = ([{
        "turn": turn,
        "models": turn_models,
        "outcome": outcome,
        "summary": (answer or "")[:_SUMMARY_CAP],
    }] + state.conclusions)[:_CONCLUSIONS_CAP]

    if constraints:
        seen = set(state.constraints)
        state.constraints = state.constraints + [
            c for c in constraints if c and c not in seen]

    if not is_followup and question:
        state.topic = question[:_TOPIC_CAP]
    return state


def render_state_note(state: LoopSessionState | None) -> str:
    """注入 system prompt 的状态块;空状态返回空串(不占上下文)。"""
    if state is None:
        return ""
    lines: list[str] = []
    if state.active_models:
        lines.append("- 讨论中型号: " + ", ".join(state.active_models))
    if state.constraints:
        lines.append("- 已确认约束: " + " / ".join(state.constraints))
    if state.conclusions:
        last = state.conclusions[0]
        lines.append(f"- 上轮结论({last['outcome']}): {last['summary']}")
    return "\n".join(lines)
