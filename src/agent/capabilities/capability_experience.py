"""Experience capability — reusable non-troubleshooting FAE QA evidence."""
from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter

from src.agent.capabilities import register
from src.agent.evidence import Evidence, Fact
from src.agent.planner import Capability
from src.agent.qa_search import qa_search
from src.agent.schema import RequestSchema

_TOP_K = 8
_FACT_MAX_CHARS = 280
_EXPERIENCE_ANSWER_TYPES = {"终结型", "条件型", "操作型"}
_TROUBLESHOOT_ANSWER_TYPES = {"排查型"}
_TROUBLESHOOT_DOC_TYPES = {"troubleshooting"}


def _is_experience_hit(hit) -> bool:
    metadata = hit.metadata or {}
    answer_type = str(metadata.get("answer_type") or "")
    doc_type = str(metadata.get("doc_type") or "")
    if answer_type in _TROUBLESHOOT_ANSWER_TYPES:
        return False
    if doc_type in _TROUBLESHOOT_DOC_TYPES:
        return False
    if answer_type in _EXPERIENCE_ANSWER_TYPES:
        return True
    return bool(doc_type)


def _fact_from_hit(hit) -> Fact:
    title = (hit.title or "").strip()
    snippet = (hit.snippet or "").strip()
    statement = f"{title}: {snippet}" if title else snippet
    statement = statement[:_FACT_MAX_CHARS].strip()
    source_ref = hit.kb_id or title or "qa"
    return Fact(statement=statement, source_ref=source_ref, confidence=float(hit.score))


@dataclass
class CapabilityExperience:
    name: Capability = "experience"

    def run(
        self,
        query: str,
        *,
        schema,
        channel: str = "fae",
        product_catalog: dict | None = None,
        qa_collection=None,
        api_key: str = "",
        embed_model: str = "",
        **kwargs,
    ) -> Evidence:
        t0 = perf_counter()
        if kwargs.get("qa_enabled", True) is False:
            return Evidence(
                source_module="experience",
                facts=[],
                confidence="low",
                coverage="empty",
                notes=["QA KB 已关闭,在线 experience 检索已跳过"],
                latency_ms=int((perf_counter() - t0) * 1000),
            )
        if qa_collection is None:
            return Evidence(
                source_module="experience",
                facts=[],
                confidence="low",
                coverage="empty",
                notes=["缺 QA collection,无法执行 experience 检索"],
                latency_ms=int((perf_counter() - t0) * 1000),
            )
        if not api_key or not embed_model:
            return Evidence(
                source_module="experience",
                facts=[],
                confidence="low",
                coverage="empty",
                notes=["缺 embedding 配置,无法执行 experience 检索"],
                latency_ms=int((perf_counter() - t0) * 1000),
            )

        try:
            hits = qa_search(
                schema=schema,
                user_query=query,
                collection=qa_collection,
                api_key=api_key,
                embed_model=embed_model,
                top_k=_TOP_K,
            )
            used_broad_search = False
            if not hits and schema is not None:
                hits = qa_search(
                    schema=_broad_schema(schema),
                    user_query=query,
                    collection=qa_collection,
                    api_key=api_key,
                    embed_model=embed_model,
                    top_k=_TOP_K,
                )
                used_broad_search = bool(hits)
        except Exception as exc:
            return Evidence(
                source_module="experience",
                facts=[],
                confidence="low",
                coverage="empty",
                notes=[f"experience 检索失败: {exc}"],
                latency_ms=int((perf_counter() - t0) * 1000),
                error=str(exc),
            )

        facts = [_fact_from_hit(hit) for hit in hits if _is_experience_hit(hit)]
        notes: list[str] = []
        if used_broad_search:
            notes.append("精确 QA 检索无命中,已使用宽检索")
        if not facts:
            if hits:
                notes.append("QA 命中存在,但未命中普通经验 answer_type/doc_type")
            else:
                notes.append("QA 未命中普通经验记录")

        return Evidence(
            source_module="experience",
            facts=facts,
            confidence="high" if facts else "low",
            coverage="full" if facts else "empty",
            notes=notes,
            latency_ms=int((perf_counter() - t0) * 1000),
        )


def _broad_schema(schema) -> RequestSchema:
    return RequestSchema(
        intent=schema.intent,
        intent_confidence=schema.intent_confidence,
        intent_alternatives=list(schema.intent_alternatives),
        channel=schema.channel,
    )


register(CapabilityExperience())
