"""capability_stub — CapabilityRiskCompliance 真实现。

CapabilityRiskCompliance is a real implementation that reuses
src.agent.guardrails.inject_mandatory_warnings to produce Fact items.
No LLM is called.
"""
from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter

from src.agent.capabilities import register
from src.agent.evidence import Evidence, Fact
from src.agent.guardrails import inject_mandatory_warnings
from src.agent.planner import Capability


# ──────────────────────────────────────────────────────────────────────────────
# CapabilityRiskCompliance — REAL implementation
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class CapabilityRiskCompliance:
    name: Capability = "risk_compliance"

    def run(
        self,
        query: str,
        *,
        schema,
        channel: str = "fae",
        product_catalog: dict | None = None,
        **kwargs,
    ) -> Evidence:
        t0 = perf_counter()
        try:
            warnings = inject_mandatory_warnings(schema) if schema else []
        except Exception as exc:
            return Evidence(
                source_module="risk_compliance",
                facts=[],
                confidence="low",
                coverage="empty",
                notes=[f"guardrails error: {exc}"],
                latency_ms=int((perf_counter() - t0) * 1000),
                error=str(exc),
            )
        facts = [
            Fact(
                statement=w,
                source_ref="src/agent/guardrails.py:inject_mandatory_warnings",
                confidence=1.0,
            )
            for w in warnings
        ]
        coverage = "full" if facts else "empty"
        return Evidence(
            source_module="risk_compliance",
            facts=facts,
            confidence="high" if facts else "low",
            coverage=coverage,
            notes=[] if facts else ["未触发任何强制风险提示"],
            latency_ms=int((perf_counter() - t0) * 1000),
        )


# ──────────────────────────────────────────────────────────────────────────────
# Auto-register at module import
# ──────────────────────────────────────────────────────────────────────────────

register(CapabilityRiskCompliance())
