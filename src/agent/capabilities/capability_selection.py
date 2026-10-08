"""Selection capability — 硬约束过滤优先的选型取证。

流程(2026-07-06 W2):产品规则/QA 出种子候选 → 事实矩阵硬约束过滤[代码]
(距离/IP/接口/停产,missing != negative) → 合规候选附矩阵硬指标,剔除项
显式给理由 → 权衡排序留给合成层模型。
"""
from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter

from src.agent.capabilities import register
from src.agent.engine_b import engine_b_search
from src.agent.evidence import Evidence, Fact
from src.agent.planner import Capability
from src.agent.qa_search import qa_search
from src.agent.schema import RequestSchema
from src.facts.selection_filter import hard_filter_candidates

_TOP_K = 8
_FACT_MAX_CHARS = 320
_SELECTION_DOC_TYPES = {"selection"}
_BLOCKED_ANSWER_TYPES = {"排查型"}


def _is_selection_hit(hit) -> bool:
    metadata = hit.metadata or {}
    doc_type = str(metadata.get("doc_type") or "")
    answer_type = str(metadata.get("answer_type") or "")
    if answer_type in _BLOCKED_ANSWER_TYPES:
        return False
    return doc_type in _SELECTION_DOC_TYPES


def _qa_fact(hit) -> Fact:
    title = (hit.title or "").strip()
    snippet = (hit.snippet or "").strip()
    statement = f"{title}: {snippet}" if title else snippet
    return Fact(
        statement=statement[:_FACT_MAX_CHARS].strip(),
        source_ref=hit.kb_id or title or "qa",
        confidence=float(hit.score),
    )


def _product_fact(hit) -> Fact:
    metadata = hit.metadata or {}
    model_key = str(metadata.get("model") or hit.title or "").strip()
    display_model = model_key.replace("_", " ")
    tech_path = str(metadata.get("tech_path") or "").strip()
    snippet = (hit.snippet or "").strip().replace("\n", " ")
    if tech_path:
        statement = f"候选型号: {display_model}; 匹配方向: {tech_path}; {snippet}"
    else:
        statement = f"候选型号: {display_model}; {snippet}"
    return Fact(
        statement=statement[:_FACT_MAX_CHARS].strip(),
        source_ref=f"Knowledge/{model_key}/index.md:1",
        confidence=float(hit.score),
    )


@dataclass
class CapabilitySelection:
    name: Capability = "selection"

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
        fact_store=None,
        **kwargs,
    ) -> Evidence:
        t0 = perf_counter()
        facts: list[Fact] = []
        notes: list[str] = []
        qa_enabled = kwargs.get("qa_enabled", True) is not False

        qa_error: str | None = None
        if not qa_enabled:
            notes.append("QA KB 已关闭,跳过 QA 选型检索")
        elif qa_collection is not None and api_key and embed_model:
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
                facts.extend(_qa_fact(hit) for hit in hits if _is_selection_hit(hit))
                if used_broad_search:
                    notes.append("精确 QA 选型检索无命中,已使用宽检索")
                if hits and not facts:
                    notes.append("QA 命中存在,但未命中 selection 记录")
            except Exception as exc:
                qa_error = str(exc)
                notes.append(f"selection QA 检索失败: {exc}")
        else:
            notes.append("缺 QA collection 或 embedding 配置,跳过 QA 选型检索")

        if facts:
            return Evidence(
                source_module="selection",
                facts=facts,
                confidence="high",
                coverage="full",
                notes=notes,
                latency_ms=int((perf_counter() - t0) * 1000),
                error=qa_error,
            )

        candidate_pairs = _product_candidate_pairs(schema, product_catalog or {}, query=query)
        product_facts, filter_meta = _apply_hard_filter(
            candidate_pairs, schema=schema, fact_store=fact_store, notes=notes,
        )
        if product_facts:
            if candidate_pairs:
                notes.append("未命中 QA 选型记录,使用产品规则候选")
            metadata: dict[str, object] = {}
            if filter_meta is not None:
                metadata["hard_filter"] = filter_meta
            return Evidence(
                source_module="selection",
                facts=product_facts,
                confidence="low",
                coverage="partial",
                notes=notes,
                latency_ms=int((perf_counter() - t0) * 1000),
                error=qa_error,
                metadata=metadata,
            )

        if schema is None or (not schema.scenario and not schema.constraints and not schema.products):
            notes.append("缺少选型场景、约束或候选产品,需要追问")
        else:
            notes.append("未命中 QA 选型记录,产品规则也没有候选")
        return Evidence(
            source_module="selection",
            facts=[],
            confidence="low",
            coverage="empty",
            notes=notes,
            latency_ms=int((perf_counter() - t0) * 1000),
            error=qa_error,
        )


def _product_candidate_pairs(
    schema, product_catalog: dict, *, query: str = ""
) -> list[tuple[str, Fact]]:
    """产品规则候选 → [(model_key, Fact)];model_key 可能为空串(无法解析)。"""
    if schema is None or not product_catalog:
        return []
    candidate_schema = schema
    if not candidate_schema.scenario and query.strip():
        candidate_schema = RequestSchema(
            intent=schema.intent,
            intent_confidence=schema.intent_confidence,
            intent_alternatives=list(schema.intent_alternatives),
            products=list(schema.products),
            scenario=[query.strip()],
            constraints=list(schema.constraints),
            platforms=list(schema.platforms),
            channel=schema.channel,
            technical_components=list(schema.technical_components),
            missing_for_bucket=list(schema.missing_for_bucket),
            is_topic_switch=schema.is_topic_switch,
        )
    if not candidate_schema.scenario:
        return []
    result = engine_b_search(schema=candidate_schema, product_catalog=product_catalog)
    pairs: list[tuple[str, Fact]] = []
    for hit in result.hits:
        model_key = str((hit.metadata or {}).get("model") or hit.title or "").strip()
        pairs.append((model_key, _product_fact(hit)))
    return pairs


def _matrix_candidate_fact(fact_store, model: str) -> Fact:
    bits: list[str] = []
    for fid, label in (
        ("depth_range_max", "深度范围"), ("ip_rating", "防护"),
        ("data_interface", "接口"), ("poe_standard", "PoE"),
    ):
        r = fact_store.get_spec(model, fid)
        if r.status == "found":
            bits.append(f"{label} {r.row.raw_value[:50]}")
    display = model.replace("_", " ")
    detail = "; ".join(bits) if bits else "矩阵暂无硬指标行"
    return Fact(
        statement=f"候选型号: {display}; 硬指标: {detail}"[:_FACT_MAX_CHARS],
        source_ref=f"Knowledge/{model}/facts.yaml",
        confidence=1.0,
    )


def _apply_hard_filter(
    candidate_pairs: list[tuple[str, Fact]],
    *,
    schema,
    fact_store,
    notes: list[str],
) -> tuple[list[Fact], dict | None]:
    """硬约束过滤:合规候选附矩阵硬指标,剔除项显式给理由。

    fact_store 缺失时保持原行为(直接返回候选 Fact),不做隐藏降级——
    接线状态在 Evidence.metadata 有无 hard_filter 上可见。
    """
    if fact_store is None or schema is None:
        return [f for _, f in candidate_pairs], None

    constraints = [str(c) for c in (schema.constraints or [])]
    seed_models = [m for m, _ in candidate_pairs if m]
    result = hard_filter_candidates(seed_models, constraints, fact_store)
    notes.extend(result.notes)

    kept_models = set(result.candidates) | set(result.unfilterable)
    facts: list[Fact] = []
    seen_models: set[str] = set()
    for model, fact in candidate_pairs:
        if model and model not in kept_models:
            continue
        facts.append(fact)
        if model:
            seen_models.add(model)
    # 枚举路径:种子候选为空时,矩阵过滤出的候选补充为事实
    for model in result.candidates:
        if model not in seen_models:
            facts.append(_matrix_candidate_fact(fact_store, model))
            seen_models.add(model)
    for model, reason in result.excluded:
        facts.append(Fact(
            statement=f"已按硬约束剔除: {model.replace('_', ' ')}; 理由: {reason}"[:_FACT_MAX_CHARS],
            source_ref=f"Knowledge/{model}/facts.yaml#hard_filter",
            confidence=1.0,
        ))
    for model in result.unfilterable:
        notes.append(f"{model}: 关键约束缺矩阵证据,未能代码判定,需模型权衡并标注不确定性")

    meta = {
        "candidates": list(result.candidates),
        "excluded": [[m, r] for m, r in result.excluded],
        "unfilterable": list(result.unfilterable),
        "constraints": constraints,
    }
    return facts, meta


def _broad_schema(schema) -> RequestSchema:
    return RequestSchema(
        intent=schema.intent,
        intent_confidence=schema.intent_confidence,
        intent_alternatives=list(schema.intent_alternatives),
        channel=schema.channel,
    )


register(CapabilitySelection())
