"""Evidence 数据模型 — Phase B 组合架构的数据骨架。

Capability runners 产 Evidence;Evidence Synthesizer 消费 Evidence 拼出回答。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from src.agent.planner import Capability


@dataclass(frozen=True)
class Fact:
    """单条事实 — capability 从知识库抽到的最小可溯源单元。"""
    statement: str          # 段标题 + 首段,≤200 字
    source_ref: str         # "Knowledge/<model>/<file>.md:<line>" 或 "guardrails.py:<rule_name>"
    confidence: float       # 无 retrieval 时取 1.0


@dataclass
class Evidence:
    """单个 capability 跑完 retrieval 后的取证结果。"""
    source_module: Capability
    facts: list[Fact] = field(default_factory=list)
    confidence: Literal["high", "low"] = "low"
    coverage: Literal["full", "partial", "empty"] = "empty"
    notes: list[str] = field(default_factory=list)
    latency_ms: int = 0
    error: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)
