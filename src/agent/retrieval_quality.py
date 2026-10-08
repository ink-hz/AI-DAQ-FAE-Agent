"""检索质量评估器 — 工作流设计 §2.3 两段式判定。

Stage 1:纯算术粗筛(top1_sim 太低 / 信号强度不足直接 "no_strong_signal")
Stage 2:LLM 精判(看 top-3,返回 any_relevant / best_idx / reason)

D / B / C 三桶共享。替代 engine_a.py 里写死的 0.85/0.6/0.5 阈值(engine_a 在 T4 D 桶迁移时退役)。
"""
from pathlib import Path
from typing import Literal

from src.agent.llm_client import complete_json
from src.agent.schema import RetrievalHit

Decision = Literal["direct_reuse", "synthesize", "no_strong_signal", "irrelevant"]

_TEMPLATE_FILE = "retrieval_quality.md"
_SYSTEM = "你是 Orbbec FAE Agent 的检索质量评估模块。严格按要求输出 JSON。"

# Stage 1 阈值(工作流设计 §2.3.2)
_MIN_TOP1_SIM = 0.3
_MIN_SIGNAL_STRENGTH = 0.05

# 喂给 LLM 的候选个数(top-3),snippet 截断长度
_LLM_TOP_K = 3
_SNIPPET_MAX = 800


def _load_template(prompts_dir: Path) -> str:
    return (prompts_dir / _TEMPLATE_FILE).read_text(encoding="utf-8")


def _render_candidates_block(candidates: list[RetrievalHit]) -> str:
    """渲染 top-3 候选块(prompt 占位 {CANDIDATES_BLOCK} 用)。"""
    parts: list[str] = []
    for i, hit in enumerate(candidates[:_LLM_TOP_K]):
        snippet = hit.snippet[:_SNIPPET_MAX]
        parts.append(
            f"### 候选 {i} (sim={hit.score:.2f})\n"
            f"**title**: {hit.title}\n"
            f"**snippet**: {snippet}\n"
        )
    return "\n".join(parts)


def evaluate(
    *,
    candidates: list[RetrievalHit],
    user_query: str,
    provider: str,
    api_key: str,
    model_fast: str,
    prompts_dir: Path,
    base_url: str = "",
) -> Decision:
    """两段式检索质量评估。详见工作流设计 §2.3。

    返回值:
      - "no_strong_signal": Stage 1 信号弱(空 / top1 太低 / 与 mean 差距太小)
      - "irrelevant":      Stage 2 LLM 判定无相关候选(或返回 best_idx 不可用)
      - "direct_reuse":    top1 命中且其 metadata.confidence == "high"
      - "synthesize":      有相关候选但需要合成(非 top1 或 top1 非 high 置信)
    """
    # Stage 1: 纯算术粗筛
    if not candidates:
        return "no_strong_signal"
    top1_sim = candidates[0].score
    top5 = candidates[:5]
    mean_top5 = sum(h.score for h in top5) / len(top5)
    signal_strength = top1_sim - mean_top5
    if top1_sim < _MIN_TOP1_SIM or signal_strength < _MIN_SIGNAL_STRENGTH:
        return "no_strong_signal"

    # Stage 2: LLM 精判
    template = _load_template(prompts_dir)
    user = (
        template
        .replace("{USER_QUERY}", user_query)
        .replace("{CANDIDATES_BLOCK}", _render_candidates_block(candidates))
    )
    raw = complete_json(
        provider=provider,
        api_key=api_key,
        model=model_fast,
        base_url=base_url,
        system=_SYSTEM,
        user=user,
    )

    # 缺字段:KeyError → irrelevant(保守);其他异常(JSONDecodeError 等)向上传播
    try:
        any_relevant = raw["any_relevant"]
        best_idx = raw["best_idx"]
    except KeyError:
        return "irrelevant"

    if not any_relevant:
        return "irrelevant"
    if best_idx is None:
        return "irrelevant"
    # best_idx 越界(LLM 乱编)→ irrelevant
    if not isinstance(best_idx, int) or best_idx < 0 or best_idx >= len(candidates):
        return "irrelevant"

    if best_idx == 0 and candidates[0].metadata.get("confidence") == "high":
        return "direct_reuse"
    return "synthesize"
