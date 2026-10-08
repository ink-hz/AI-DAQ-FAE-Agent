"""capability_spec — 从 Knowledge/<model>/{index,product,hardware}.md 抽规格类 Fact。

不调 LLM。Markdown ## 段切片 → Fact(statement = "段标题: 段首若干字")。
Evidence Synthesizer 拿到 Facts 后再决定怎么合成回答。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from src.agent.capabilities import register
from src.agent.engine_b import engine_b_search
from src.agent.evidence import Evidence, Fact
from src.agent.planner import Capability
from src.agent.product_entities import resolve_product_entities
from src.agent.schema import RequestSchema

from src.data.product_loader import _split_by_h2

# 每条 Fact 截到中等长度,避免关键规格表行被 200 字截断。
_FACT_MAX_CHARS = 800


@dataclass
class CapabilitySpec:

    name: Capability = "spec"

    def run(
        self,
        query: str,
        *,
        schema,                    # src.agent.schema.RequestSchema
        channel: str = "fae",
        product_catalog: dict[str, dict] | None = None,
        fact_store=None,
        model_resolver=None,
        **kwargs,
    ) -> Evidence:
        t0 = perf_counter()
        catalog = product_catalog or {}
        product_names = schema.products if schema else []
        notes: list[str] = []
        inferred_from_components = False

        resolution = resolve_product_entities(
            product_names, catalog, resolver=model_resolver
        )
        resolved_models = list(resolution.resolved)

        if not resolved_models and schema and schema.technical_components and catalog:
            candidate_schema = _candidate_schema_from_components(schema, query)
            candidate_result = engine_b_search(
                schema=candidate_schema,
                product_catalog=catalog,
            )
            for hit in candidate_result.hits:
                model = str((hit.metadata or {}).get("model") or hit.title or "").strip()
                if model in catalog and model not in resolved_models:
                    resolved_models.append(model)
            if resolved_models:
                inferred_from_components = True
                notes.append("缺产品锚点,已按技术组件候选产品取证")

        if not resolved_models:
            return Evidence(
                source_module="spec",
                facts=[],
                confidence="low",
                coverage="empty",
                notes=notes + ["缺产品锚点(schema.products 为空或无法解析到 catalog)"],
                latency_ms=int((perf_counter() - t0) * 1000),
            )

        facts: list[Fact] = []
        matrix_counts: dict[str, int] = {}
        for model in resolved_models:
            matrix_facts, matrix_notes = _matrix_facts(model, fact_store)
            if matrix_facts:
                matrix_counts[model] = len(matrix_facts)
                facts.extend(matrix_facts)
            notes.extend(matrix_notes)
            model_dir = Path(catalog[model]["dir"])
            for md_name in ("index.md", "product.md", "hardware.md", "software.md"):
                md_path = model_dir / md_name
                if not md_path.exists():
                    notes.append(f"{model}/{md_name} 不存在")
                    continue
                try:
                    facts.extend(self._extract_facts(md_path, model, md_name))
                except Exception as exc:
                    notes.append(f"{model}/{md_name} 抽取失败: {exc}")

        coverage = "full" if facts else "empty"
        if facts and (resolution.unresolved or inferred_from_components):
            coverage = "partial"

        metadata: dict[str, object] = {}
        if matrix_counts:
            metadata["fact_matrix"] = matrix_counts

        return Evidence(
            source_module="spec",
            facts=facts,
            confidence="high" if facts else "low",
            coverage=coverage,
            notes=notes,
            latency_ms=int((perf_counter() - t0) * 1000),
            metadata=metadata,
        )

    def _extract_facts(self, md_path: Path, model: str, md_name: str) -> list[Fact]:
        """Markdown ## 段切片 → Fact list,每段首 _FACT_MAX_CHARS 字。

        source_ref 用 `Knowledge/<model>/<md_name>:<line>` 形式,行号 = 段标题行。
        """
        text = md_path.read_text(encoding="utf-8")
        sections = _split_by_h2(text)
        if not sections:
            return []

        # 算每段标题在原文里的行号
        lines = text.splitlines()
        title_to_line: dict[str, int] = {}
        for i, line in enumerate(lines, 1):
            if line.startswith("## "):
                title = line[3:].strip()
                title_to_line.setdefault(title, i)

        facts: list[Fact] = []
        for title, content in sections:
            line_no = title_to_line.get(title, 1)
            statement = content[:_FACT_MAX_CHARS].strip()
            facts.append(Fact(
                statement=statement,
                source_ref=f"Knowledge/{model}/{md_name}:{line_no}",
                confidence=1.0,
            ))
        return facts


def _matrix_facts(model: str, fact_store) -> tuple[list[Fact], list[str]]:
    """事实矩阵行 → 前置 Fact。conflict 行不作为证据,显式记 notes。

    statement 内容优先级:文本行的 value 是 FAE 归一化后的真值(可能带核验
    补充,如"被动双目/无投射器"),优先于文档原文;range 行保留原文口径
    (评测 token 与用户语言一致);value 与原文不同时并列展示防信息丢失。
    """
    if fact_store is None:
        return [], []
    rows = fact_store.rows(model)
    facts: list[Fact] = []
    notes: list[str] = []
    for fid in sorted(rows):
        row = rows[fid]
        if row.status == "conflict":
            notes.append(f"{model}: 矩阵字段 {fid} 来源冲突未裁决,未作为证据")
            continue
        fdef = fact_store.fields.get(fid)
        name = fdef.name_zh if fdef else fid
        value_text = "" if row.value is None else str(row.value)
        if (
            not isinstance(row.value, str)
            or not value_text.strip()
            or value_text == row.raw_value
        ):
            content = row.raw_value or value_text
        elif row.raw_value and row.raw_value not in value_text:
            content = f"{value_text}(文档原文: {row.raw_value})"
        else:
            content = value_text
        facts.append(Fact(
            statement=f"{name}: {content}",
            source_ref=f"Knowledge/{model}/facts.yaml#{fid}",
            confidence=1.0 if row.status == "fae_verified" else 0.9,
        ))
    if facts:
        notes.append(f"{model}: 事实矩阵命中 {len(facts)} 行(查表优先)")
    return facts, notes


def _candidate_schema_from_components(schema, query: str) -> RequestSchema:
    """Build a product-candidate schema when components are clearer than products."""
    return RequestSchema(
        intent=schema.intent,
        intent_confidence=schema.intent_confidence,
        intent_alternatives=list(schema.intent_alternatives),
        scenario=list(schema.scenario) or [query],
        constraints=list(schema.constraints),
        products=[],
        platforms=list(schema.platforms),
        technical_components=list(schema.technical_components),
        channel=schema.channel,
        missing_for_bucket=list(schema.missing_for_bucket),
        is_topic_switch=schema.is_topic_switch,
    )


# 模块 import 时自动注册
register(CapabilitySpec())
