"""澄清追问节点 — 工作流设计 §2.5.2。

B/C 桶缺关键字段时跳过 retrieval + synthesis,改由 fast model 流式出追问文本。
仅暴露流式入口 clarify_stream(orchestrator 直接 yield 给 SSE),不提供非流式版本。

Pattern 对齐 src/agent/synthesizer.py:synthesize_engine_a_stream:
  - 读 .md 模板作为 user message
  - SYSTEM_PROMPT 作为模块级常量(.md 文件里不含 system 段,避免歧义)
  - 占位符直接字符串 replace
  - missing list 用 Python list repr 序列化(如 ["scenario", "constraints"])
    — LLM 可读,且与示例输出里的 missing=[...] 标注一致
"""
from collections.abc import Iterator
from pathlib import Path

from src.agent.llm_client import complete_stream
from src.agent.schema import Bucket, RequestSchema

SYSTEM_PROMPT = (
    "你是 Orbbec FAE Agent 的澄清追问模块。"
    "基于用户的原问 + 当前桶名 + 缺失字段,自然语义化地追问最多 2 个最关键字段。"
    "语气贴近 FAE 同事,简洁不僵硬。"
)


def _load_template(prompts_dir: Path, name: str) -> str:
    return (prompts_dir / name).read_text(encoding="utf-8")


def clarify_stream(
    *,
    user_query: str,
    schema: RequestSchema,
    bucket: Bucket,
    missing: list[str],
    provider: str,
    api_key: str,
    model_fast: str,
    prompts_dir: Path,
    base_url: str = "",
) -> Iterator[str]:
    """Yield text chunks of a clarification question. See §2.5.2."""
    template = _load_template(prompts_dir, "clarification.md")
    user = (
        template
        .replace("{USER_QUERY}", user_query)
        .replace("{BUCKET}", bucket)
        .replace("{MISSING}", repr(missing))
        .replace("{CHANNEL}", schema.channel)
    )
    yield from complete_stream(
        provider=provider,
        api_key=api_key,
        base_url=base_url,
        model=model_fast,
        system=SYSTEM_PROMPT,
        user=user,
        max_tokens=256,
        temperature=0.3,
    )
