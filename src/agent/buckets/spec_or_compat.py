"""B 桶 — spec_or_compat。工作流设计 §4。

DAG:missing_products_check → model_resolve → doc_load → spec_synth_stream

handle() 返回 SpecOrCompatResult:
  - decision: "answered" | "unknown_model" | "missing_products"
  - hits: list[RetrievalHit]  每个 hit 一型号,snippet 是 index/hw/sw 三段带行号块
  - unresolved: list[str] — 仅 decision="unknown_model" 时填,未匹配到 catalog 的型号名
  - chunks: Iterator[str] | None — lazy iterator,仅 decision="answered" 时非空

为什么 missing_products / unknown_model 不在桶里直接答:
  - missing_products → orchestrator T9 路由到 T3 clarification 节点(LLM 出追问文本)
  - unknown_model    → orchestrator T9 套总设 §6.4 "知识库未收录 X" 固定文案
  - 这两路都不需要 LLM 合成,所以 chunks=None

DocLoad 策略(§4.3):
  - spec / compat 都加载 index + hardware + software 全部三个(LLM 看哪段需要哪段)
  - hardware / software 截到 ~2500 字(§4.5)
"""
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from src.agent.engine_b import _read_hw_summary, _read_index_summary, engine_b_search
from src.agent.product_entities import resolve_product_entities, resolve_single_model
from src.agent.schema import RequestSchema, RetrievalHit
from src.agent.synthesizer import spec_synth_stream

# §4.5 hardware/software 截断阈值
_DOC_MAX_CHARS = 2500
# index.md 摘要长度(沿用 engine_b._read_index_summary 默认值 1500)
_INDEX_MAX_CHARS = 1500

Decision = Literal["answered", "unknown_model", "missing_products"]


@dataclass
class SpecOrCompatResult:
    """B 桶产出。

    answered:        hits 非空,chunks 是 spec_synth_stream 的 lazy iterator
    unknown_model:   unresolved 非空,hits/chunks 空 — caller 套固定文案
    missing_products: 全空 — caller 路由到 clarification 节点
    """
    decision: Decision
    hits: list[RetrievalHit] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    chunks: Iterator[str] | None = None


# ---------------- internal helpers ----------------


def _resolve_model(name: str, catalog: dict[str, dict]) -> str | None:
    """把用户写的型号名匹配到 catalog 的标准 key(如 Gemini_335L)。

    沿用 engine_b._normalize_model_name 的思路(空格 → 下划线),再扩展为大小写无关的子串匹配:
      1. 精确(规范化后) — `Gemini 335L` → `Gemini_335L`
      2. 大小写无关精确
      3. 大小写无关子串(用户写 `335L` 命中 `Gemini_335L`;用户写 `Gemini_335L` 命中精确)

    多个 catalog 项同时命中子串 → 返回 None(歧义,视为未解析,避免乱猜)。
    """
    return resolve_single_model(name, catalog)


def _add_line_numbers(text: str) -> str:
    """每行前面加 `N: ` 行号,供 prompt 里的 `[file:N]` 溯源使用。"""
    lines = text.splitlines()
    return "\n".join(f"{i + 1}: {line}" for i, line in enumerate(lines))


def _read_sw_summary(model_dir: Path, max_chars: int = _DOC_MAX_CHARS) -> str:
    """engine_b 没有 software.md 的读取器,这里补上 — 形态与 _read_hw_summary 一致。"""
    sw = model_dir / "software.md"
    if not sw.exists():
        return ""
    return sw.read_text(encoding="utf-8")[:max_chars]


def _build_doc_snippet(model_dir: Path) -> str:
    """构造 RetrievalHit.snippet — 三段带行号的 markdown 块,供 spec_synth prompt 用。"""
    index_text = _read_index_summary(model_dir, max_chars=_INDEX_MAX_CHARS)
    hw_text = _read_hw_summary(model_dir, max_chars=_DOC_MAX_CHARS)
    sw_text = _read_sw_summary(model_dir)
    return (
        f"**index.md**:\n{_add_line_numbers(index_text) if index_text else '(无 index.md)'}\n\n"
        f"**hardware.md**:\n{_add_line_numbers(hw_text) if hw_text else '(无 hardware.md)'}\n\n"
        f"**software.md**:\n{_add_line_numbers(sw_text) if sw_text else '(无 software.md)'}"
    )


# ---------------- public handle ----------------


def handle(
    *,
    schema: RequestSchema,
    user_query: str,
    product_catalog: dict[str, dict],
    provider: str,
    api_key: str,
    model_main: str,
    prompts_dir: Path,
    base_url: str = "",
    consultation_context: str = "",
) -> SpecOrCompatResult:
    """B 桶入口。步骤见 module docstring。

    Args:
        schema: RequestSchema;schema.products 必填,空 → missing_products
        product_catalog: load_product_catalog() 返回的 dict {model: {"dir": str, ...}}
        api_key: 主合成模型 key
        model_main: 主合成模型名(spec_synth_stream 用)
    """
    # 1. missing_products check — schema.products 空 → 优先看是否有可取证的技术组件。
    #    PoE/GMSL/同步/SDK 这类问题即使没有指定型号,也应先给通用原则和候选方向,
    #    不要只反问"哪款型号"。
    if not schema.products:
        if schema.technical_components:
            candidate_schema = RequestSchema(
                intent=schema.intent,
                intent_confidence=schema.intent_confidence,
                intent_alternatives=list(schema.intent_alternatives),
                scenario=list(schema.scenario) or [user_query],
                constraints=list(schema.constraints),
                products=[],
                platforms=list(schema.platforms),
                technical_components=list(schema.technical_components),
                channel=schema.channel,
                missing_for_bucket=list(schema.missing_for_bucket),
                is_topic_switch=schema.is_topic_switch,
            )
            candidate_result = engine_b_search(
                schema=candidate_schema,
                product_catalog=product_catalog,
            )
            if candidate_result.hits:
                chunks = spec_synth_stream(
                    user_query=user_query,
                    schema=schema,
                    hits=candidate_result.hits,
                    provider=provider,
                    api_key=api_key,
                    model=model_main,
                    prompts_dir=prompts_dir,
                    base_url=base_url,
                    consultation_context=consultation_context,
                )
                return SpecOrCompatResult(
                    decision="answered",
                    hits=candidate_result.hits,
                    chunks=chunks,
                )
        return SpecOrCompatResult(decision="missing_products")

    # 2. model_resolve — 全部解析才算 ok;有任一未解析 → 整体 unknown_model(strict)
    resolution = resolve_product_entities(schema.products, product_catalog)
    resolved = resolution.resolved
    unresolved = resolution.unresolved
    if unresolved:
        return SpecOrCompatResult(decision="unknown_model", unresolved=unresolved)

    # 3. doc_load — 每型号一 RetrievalHit,snippet 是 index/hw/sw 三段带行号块
    hits: list[RetrievalHit] = []
    for key in resolved:
        info = product_catalog[key]
        model_dir = Path(info["dir"])
        snippet = _build_doc_snippet(model_dir)
        hits.append(RetrievalHit(
            source="product",
            kb_id=None,
            title=key,
            snippet=snippet,
            score=1.0,  # B 桶无向量检索,固定 1.0
            metadata={"model": key, "dir": str(model_dir)},
        ))

    # 4. spec_synth_stream — 返回 lazy iterator,handle 不 consume
    chunks = spec_synth_stream(
        user_query=user_query,
        schema=schema,
        hits=hits,
        provider=provider,
        api_key=api_key,
        model=model_main,
        prompts_dir=prompts_dir,
        base_url=base_url,
        consultation_context=consultation_context,
    )
    return SpecOrCompatResult(decision="answered", hits=hits, chunks=chunks)
