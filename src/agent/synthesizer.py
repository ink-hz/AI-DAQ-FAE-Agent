"""主推理 LLM 合成。

每桶一个 stream 合成入口:
  experience_synth_stream — D 桶(经验复用,replaces synthesize_engine_a)
  selection_synth_stream  — C 桶(选型推荐,replaces synthesize_engine_b)
  spec_synth_stream       — B 桶(规格 / 兼容查询)
  overview_synth_stream   — A 桶(产品矩阵概览)

T9 起 synthesize_engine_a / _stream / synthesize_engine_b / _stream 全部退役 — 不再有
"主路 + 兜底" 关系,改由 5 桶各自的 handle() 调用对应 *_synth_stream。
保留 synthesize_engine_b(非 stream)给老评测脚本兜底,直到 T10 评测重写。
"""
from collections.abc import Iterator
from pathlib import Path
from typing import Literal

from src.agent.answer_quality import with_fae_answer_quality
from src.agent.llm_client import complete, complete_stream
from src.agent.schema import RequestSchema, RetrievalHit


def _load_template(prompts_dir: Path, name: str) -> str:
    return (prompts_dir / name).read_text(encoding="utf-8")


def _format_qa_candidate(hit: RetrievalHit) -> str:
    md = hit.metadata
    return (
        f"### {hit.kb_id} (sim={hit.score:.2f}, confidence={md.get('confidence', '?')}, "
        f"answer_type={md.get('answer_type', '?')})\n"
        f"**title**: {hit.title}\n"
        f"**standard_answer**:\n{hit.snippet}\n"
    )


def _format_product_candidate(hit: RetrievalHit) -> str:
    md = hit.metadata
    return (
        f"### {hit.title}  (tech_path={md.get('tech_path', '?')})\n"
        f"**index.md 摘要**:\n{hit.snippet}\n"
    )


def _format_spec_candidate(hit: RetrievalHit) -> str:
    """B 桶候选 — snippet 由 spec_or_compat.handle() 拼好(含 index/hw/sw 带行号块),
    这里只加 `### <model>` 头。"""
    return f"### {hit.title}\n{hit.snippet}\n"


def _format_catalog_grouped(grouped: dict[str, list[dict]]) -> str:
    """A 桶 — 把 {tech_path → [{name, summary, dir}]} 渲染成纯文本块。

    每个 tech_path 起一个 `分组: <tech_path>` 标题,每个型号一行
    `- <name>: <summary>`。正文不嵌 Knowledge 路径;证据由 sources 事件承载。

    不在此处截断 / 不在此处做 "还有 N 款"语句 — 让 LLM 按 prompt 规则自己处理。
    grouped 空 → 输出 `(无候选)`(理论不会发生,catalog 固定 54 条;防御性)。
    """
    if not grouped:
        return "(无候选)"
    parts: list[str] = []
    for tech_path, models in grouped.items():
        lines = [f"分组: {tech_path}"]
        for m in models:
            lines.append(f"- {m['name']}:{m['summary']}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def _format_tech_paths(paths: list[dict]) -> str:
    if not paths:
        return "(无)"
    lines = []
    for p in paths:
        lines.append(
            f"- {p['tech_path']}:{p['reason']}  推荐型号:{', '.join(p['preferred_models'])}"
        )
    return "\n".join(lines)


def synthesize_engine_b(
    *,
    user_query: str,
    schema: RequestSchema,
    hits: list[RetrievalHit],
    tech_paths: list[dict],
    provider: str,
    api_key: str,
    model: str,
    prompts_dir: Path,
    base_url: str = "",
    consultation_context: str = "",
) -> str:
    """C 桶非流式 — 仅给老评测脚本兜底,T10 评测重写后可删。"""
    template = _load_template(prompts_dir, "engine_b_synthesize.md")
    candidates = "\n\n".join(_format_product_candidate(h) for h in hits)
    user = (
        template
        .replace("{USER_QUERY}", user_query)
        .replace("{SCHEMA_JSON}", schema.model_dump_json())
        .replace("{CANDIDATES_BLOCK}", candidates)
        .replace("{TECH_PATHS_BLOCK}", _format_tech_paths(tech_paths))
        .replace("{CHANNEL}", schema.channel)
    )
    user = with_fae_answer_quality(user, consultation_context)
    return complete(
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        model=model,
        system="你是 Orbbec FAE Agent 的选型推理模块。严格按规则输出。",
        user=user,
        max_tokens=4096,
        temperature=0.2,
    )


def synthesize_engine_b_stream(
    *,
    user_query: str,
    schema: RequestSchema,
    hits: list[RetrievalHit],
    tech_paths: list[dict],
    provider: str,
    api_key: str,
    model: str,
    prompts_dir: Path,
    base_url: str = "",
    consultation_context: str = "",
) -> Iterator[str]:
    """C 桶流式 — 仅给老评测脚本兜底,T10 评测重写后可删。"""
    template = _load_template(prompts_dir, "engine_b_synthesize.md")
    candidates = "\n\n".join(_format_product_candidate(h) for h in hits)
    user = (
        template
        .replace("{USER_QUERY}", user_query)
        .replace("{SCHEMA_JSON}", schema.model_dump_json())
        .replace("{CANDIDATES_BLOCK}", candidates)
        .replace("{TECH_PATHS_BLOCK}", _format_tech_paths(tech_paths))
        .replace("{CHANNEL}", schema.channel)
    )
    user = with_fae_answer_quality(user, consultation_context)
    yield from complete_stream(
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        model=model,
        system="你是 Orbbec FAE Agent 的选型推理模块。严格按规则输出。",
        user=user,
        max_tokens=4096,
        temperature=0.2,
    )


# ---------------- C 桶 selection_synth(T7 新增) ----------------

_SELECTION_SYSTEM = (
    "你是 Orbbec FAE Agent 的选型推理模块。在给定候选范围内推荐 1-3 款,"
    "理由必须基于产品文档。不要在正文输出 Knowledge 路径、行号或 FAE 编号标记;"
    "溯源由系统通过 sources 事件单独展示。"
)


def selection_synth_stream(
    *,
    user_query: str,
    schema: RequestSchema,
    hits: list[RetrievalHit],
    matched_paths: list[dict],
    provider: str,
    api_key: str,
    model: str,
    prompts_dir: Path,
    base_url: str = "",
    consultation_context: str = "",
) -> Iterator[str]:
    """C 桶合成 — 工作流设计 §5.5。

    hits 由 buckets.selection.handle() 透传自 engine_b_search:每个 RetrievalHit 的 snippet 是
    index.md 摘要,metadata 含 tech_path。本函数:
      1. 用 _format_product_candidate 渲染 CANDIDATES_BLOCK(`### <model>(tech_path=X)` + 摘要)
      2. matched_paths 渲染 TECH_PATHS_BLOCK
      3. 填 USER_QUERY / SCHEMA_JSON / CHANNEL
      4. 走主模型流式 — 系统 prompt 是 _SELECTION_SYSTEM(限定在给定范围内推荐)
    """
    template = _load_template(prompts_dir, "selection_synth.md")
    candidates = "\n\n".join(_format_product_candidate(h) for h in hits)
    user = (
        template
        .replace("{USER_QUERY}", user_query)
        .replace("{SCHEMA_JSON}", schema.model_dump_json())
        .replace("{CANDIDATES_BLOCK}", candidates)
        .replace("{TECH_PATHS_BLOCK}", _format_tech_paths(matched_paths))
        .replace("{CHANNEL}", schema.channel)
    )
    user = with_fae_answer_quality(user, consultation_context)
    yield from complete_stream(
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        model=model,
        system=_SELECTION_SYSTEM,
        user=user,
        max_tokens=4096,
        temperature=0.2,
    )


def selection_context_synth_stream(
    *,
    user_query: str,
    schema: RequestSchema,
    provider: str,
    api_key: str,
    model: str,
    prompts_dir: Path,
    base_url: str = "",
    consultation_context: str = "",
) -> Iterator[str]:
    """C 桶上下文合成 — 有咨询上下文但规则候选为空时使用。

    这不是硬编码兜底。它把历史咨询状态和本轮追问交给 LLM 综合回答,
    但明确禁止在没有候选证据时编具体型号参数。
    """
    user = (
        "## 本轮用户问题\n"
        f"{user_query}\n\n"
        "## 当前需求 schema\n"
        f"{schema.model_dump_json()}\n\n"
        "## 任务\n"
        "- 基于当前咨询上下文和本轮问题,给 FAE 式继续推进的回答。\n"
        "- 如果缺少候选型号证据,不要编具体型号或参数;可以给判断、假设、风险和验证路径。\n"
        "- 必须体现本轮新增约束,例如精度、距离、平台、环境或排除项;不要复读上一轮。\n"
        "- 追问最多 2 个,且必须服务于下一步选型或验证。\n"
    )
    user = with_fae_answer_quality(user, consultation_context)
    yield from complete_stream(
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        model=model,
        system=(
            "你是 Orbbec FAE Agent 的多轮选型咨询模块。"
            "你要综合历史上下文和本轮新问题回答,没有证据时不要编型号参数。"
        ),
        user=user,
        max_tokens=2048,
        temperature=0.2,
    )


# ---------------- D 桶 experience_synth(T4 新增,替代 synthesize_engine_a) ----------------

_EXPERIENCE_SYSTEM = (
    "你是 Orbbec FAE Agent 的经验复用模块。严格基于给定 QA 候选回答,"
    "可补充 product_catalog 的事实校验。正文只输出自然语言,不要在正文输出 "
    "Knowledge 路径、行号或 FAE 编号标记;溯源由系统通过 sources 事件单独展示。"
)


def experience_synth_stream(
    *,
    user_query: str,
    schema: RequestSchema,
    hits: list[RetrievalHit],
    mode: Literal["reuse", "synth"],
    provider: str,
    api_key: str,
    model: str,
    prompts_dir: Path,
    base_url: str = "",
    consultation_context: str = "",
) -> Iterator[str]:
    """D 桶合成 — 工作流设计 §6.5。

    mode=reuse:严格复用 top-1 standard_answer,只换称呼适配 channel
    mode=synth:综合 top-3 改写,保留 standard_answer 的事实
    """
    template = _load_template(prompts_dir, "experience_synth.md")
    candidates = "\n\n".join(_format_qa_candidate(h) for h in hits)
    user = (
        template
        .replace("{USER_QUERY}", user_query)
        .replace("{SCHEMA_JSON}", schema.model_dump_json())
        .replace("{CANDIDATES_BLOCK}", candidates)
        .replace("{MODE}", mode)
        .replace("{CHANNEL}", schema.channel)
    )
    user = with_fae_answer_quality(user, consultation_context)
    yield from complete_stream(
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        model=model,
        system=_EXPERIENCE_SYSTEM,
        user=user,
        max_tokens=4096,
        temperature=0.2,
    )


def experience_context_synth_stream(
    *,
    user_query: str,
    schema: RequestSchema,
    provider: str,
    api_key: str,
    model: str,
    prompts_dir: Path,
    base_url: str = "",
    consultation_context: str = "",
) -> Iterator[str]:
    """D 桶上下文合成 — FAE 经验库未命中但仍有咨询上下文时使用。"""
    user = (
        "## 本轮用户问题\n"
        f"{user_query}\n\n"
        "## 当前需求 schema\n"
        f"{schema.model_dump_json()}\n\n"
        "## 任务\n"
        "- 基于当前咨询上下文和本轮问题,给 FAE 式排查/判断回答。\n"
        "- 没有命中可复用经验时,不要伪造案例编号或确定结论;可以给排查假设、验证步骤和需要补充的证据。\n"
        "- 必须回应本轮新增信息,不要复读上一轮。\n"
        "- 追问最多 2 个,且必须服务于下一步排查或验证。\n"
    )
    user = with_fae_answer_quality(user, consultation_context)
    yield from complete_stream(
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        model=model,
        system=(
            "你是 Orbbec FAE Agent 的现场问题分析模块。"
            "你要综合历史上下文和本轮新问题回答,没有经验证据时不要编案例。"
        ),
        user=user,
        max_tokens=2048,
        temperature=0.2,
    )


# ---------------- B 桶 spec_synth(T5 新增) ----------------

_SPEC_SYSTEM = (
    "你是 Orbbec FAE Agent 的规格查询模块。严格基于给定文档回答,"
    "正文只输出自然语言,不要在正文输出 Knowledge 路径、行号或 FAE 编号标记;"
    "溯源由系统通过 sources 事件单独展示。"
    "无依据时明说'该字段未在公开规格书中明确',不臆测。"
)


def spec_synth_stream(
    *,
    user_query: str,
    schema: RequestSchema,
    hits: list[RetrievalHit],
    provider: str,
    api_key: str,
    model: str,
    prompts_dir: Path,
    base_url: str = "",
    consultation_context: str = "",
) -> Iterator[str]:
    """B 桶合成 — 工作流设计 §4.5。

    hits 由 buckets.spec_or_compat.handle() 构造:每个 RetrievalHit 的 snippet 已经是
    带行号的 index/hardware/software 三段结构化块。本函数只负责
      1. 用 `### <model_name>` 把 snippet 包起来作为 MODEL_DOCS_BLOCK
      2. 把 USER_QUERY / SCHEMA_JSON / CHANNEL 占位也填好
      3. 走主模型流式 — 系统 prompt 是 _SPEC_SYSTEM(强制带行号溯源 + 不臆测)
    """
    template = _load_template(prompts_dir, "spec_synth.md")
    docs_block = "\n\n".join(_format_spec_candidate(h) for h in hits)
    user = (
        template
        .replace("{USER_QUERY}", user_query)
        .replace("{SCHEMA_JSON}", schema.model_dump_json())
        .replace("{MODEL_DOCS_BLOCK}", docs_block)
        .replace("{CHANNEL}", schema.channel)
    )
    user = with_fae_answer_quality(user, consultation_context)
    yield from complete_stream(
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        model=model,
        system=_SPEC_SYSTEM,
        user=user,
        max_tokens=4096,
        temperature=0.2,
    )


# ---------------- A 桶 overview_synth(T6 新增) ----------------

_OVERVIEW_SYSTEM = (
    "你是 Orbbec FAE Agent 的产品矩阵合成模块。基于给定的分组目录,"
    "产出简明的「按技术路径分组 + 场景指引」概览。"
)


def overview_synth_stream(
    *,
    user_query: str,
    schema: RequestSchema,
    grouped: dict[str, list[dict]],
    used_full_catalog: bool,
    provider: str,
    api_key: str,
    model: str,
    prompts_dir: Path,
    base_url: str = "",
    other_count: int = 0,
    consultation_context: str = "",
) -> Iterator[str]:
    """A 桶合成 — 工作流设计 §3.5。

    grouped 由 buckets.catalog_overview.handle() 构造:
      {tech_path → [{"name": str, "summary": str, "dir": str}]}

    used_full_catalog=True 表示用户给的类别词不在白名单(或落到 §3.7 兜底),
    LLM 在 prompt 中据此先输出一句 "该类别不在 Orbbec 产品范围,以下是全量目录"。

    other_count > 0 表示 catalog 中有 N 款型号未被 tech_path_rules 覆盖
    (Astra 家族、DaBai 家族等冷门 / 历史型号),不会出现在 grouped 中但
    应在 LLM 回答末尾用一行注脚提示用户。T6 follow-up 引入。

    本函数只负责:
      1. 用 _format_catalog_grouped 把 grouped 渲染成 markdown 块作为 CATALOG_GROUPED_BLOCK
      2. 把 USER_QUERY / SCHEMA_JSON / CHANNEL / USED_FULL_CATALOG / OTHER_COUNT 占位填好
      3. 走主模型流式 — 系统 prompt 是 _OVERVIEW_SYSTEM
    """
    template = _load_template(prompts_dir, "overview_synth.md")
    grouped_block = _format_catalog_grouped(grouped)
    total_count = sum(len(models) for models in grouped.values()) + other_count
    user = (
        template
        .replace("{USER_QUERY}", user_query)
        .replace("{SCHEMA_JSON}", schema.model_dump_json())
        .replace("{CATALOG_GROUPED_BLOCK}", grouped_block)
        .replace("{USED_FULL_CATALOG}", "true" if used_full_catalog else "false")
        .replace("{OTHER_COUNT}", str(other_count))
        .replace("{TOTAL_COUNT}", str(total_count))
        .replace("{CHANNEL}", schema.channel)
    )
    user = with_fae_answer_quality(user, consultation_context)
    yield from complete_stream(
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        model=model,
        system=_OVERVIEW_SYSTEM,
        user=user,
        max_tokens=4096,
        temperature=0.2,
    )
