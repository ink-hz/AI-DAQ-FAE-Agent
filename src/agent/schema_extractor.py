"""从用户问题里抽 RequestSchema。

走 prompts/schema_extraction.md 模板 → complete_json → 解析回 RequestSchema。
"""
import logging
from pathlib import Path

from pydantic import ValidationError

from src.agent.llm_client import LLMJsonError, complete_json
from src.agent.schema import RequestSchema
from src.agent.tracing import current_trace_ctx

_TEMPLATE_FILE = "schema_extraction.md"
_SYSTEM = "你是 Orbbec FAE Agent 的需求抽取模块。严格按要求输出 JSON。"

logger = logging.getLogger(__name__)


# spec_or_compat 触发短语 — 命中即认为是规格性问题(前提:产品锚点非空)。
# 关键词来自 core_007..core_010 实测的误判 case。
_SPEC_TRIGGER_PHRASES: tuple[str, ...] = (
    "是否支持", "是否兼容", "兼容性",
    "能否", "能不能",
    "工作距离", "分辨率", "帧率", "重量", "尺寸",
    "防护等级", "IP6", "IP5",
    "供电", "电源", "接口",
    "插口", "接线", "线材", "线缆", "适配器", "引脚", "pin", "Pin",
    "规格", "参数",
    "需不需要", "需要", "必须",
)

# 排错 / 故障短语 — 命中即保留 fae_experience(避免硬规则吃掉真排错)。
_TROUBLESHOOT_PHRASES: tuple[str, ...] = (
    "排查", "丢帧", "异常", "故障",
    "看不到", "崩溃", "卡顿", "不工作",
    "怎么烧", "怎么对齐", "怎么配", "如何排查",
    "报错", "错误码",
)


# 硬件接口 / SDK / 平台关键词 — 命中即认为用户提到了具体产品锚点,
# 用于 anchor 兜底:LLM 没抽到 products/technical_components 时,
# 只要原文有这些词,也算"产品锚点"。
_PRODUCT_ANCHOR_KEYWORDS: tuple[str, ...] = (
    "SDK", "PoE", "USB", "IMU", "ROS", "Jetson",
    "Python", "C#", "C++", "Java", "Rust",
    "D2C", "对齐", "profile", "Profile",
    "深度", "彩色",
    "M12", "M8", "Type-C", "Type-A", "SBU", "DC",
    "线材", "线缆", "电源插口", "适配器", "引脚", "pin", "Pin",
    "Gemini", "Femto", "Astra", "Zora", "MS500",
    "Orbbec",  # Orbbec SDK 等场景
)


def _contains_anchor_keyword(user_message: str, keyword: str) -> bool:
    return keyword in user_message or keyword.lower() in user_message.lower()


def _has_product_anchor(schema: RequestSchema, user_message: str) -> bool:
    """schema.products / technical_components 非空 OR 用户原文含硬件/SDK 关键词。"""
    if schema.products or schema.technical_components:
        return True
    return any(_contains_anchor_keyword(user_message, kw) for kw in _PRODUCT_ANCHOR_KEYWORDS)


def _detect_anchor_keywords(user_message: str) -> list[str]:
    """从原文里抽出命中的 anchor 关键词,保持首次出现顺序、去重。"""
    seen: list[str] = []
    for kw in _PRODUCT_ANCHOR_KEYWORDS:
        if _contains_anchor_keyword(user_message, kw) and kw not in seen:
            seen.append(kw)
    return seen


def _fill_spec_technical_components(
    schema: RequestSchema, user_message: str,
) -> RequestSchema:
    """spec_or_compat 但 products / technical_components 双空时,
    把原文里命中的 anchor 关键词填进 technical_components。

    防止 router(L25-26)因 products 空把 B 桶降到 D 桶 — 技术组件本身
    也是 B 桶的合法查询锚点(如 PoE 供电、SDK 接口)。
    """
    if schema.intent != "spec_or_compat":
        return schema
    if schema.products or schema.technical_components:
        return schema
    hits = _detect_anchor_keywords(user_message)
    if not hits:
        return schema
    return schema.model_copy(update={"technical_components": hits})


def _anchor_spec_or_compat(schema: RequestSchema, user_message: str) -> RequestSchema:
    """LLM 把 spec 性问题误判成 fae_experience 的硬覆盖。

    覆盖条件(全部满足):
    - LLM 返回 intent=fae_experience
    - user_message 命中任一 spec 触发短语
    - user_message 不命中任何 troubleshoot 短语
    - 存在产品锚点(LLM 抽到 products/technical_components,
      或用户原文含硬件/SDK 关键词)
    """
    if schema.intent != "fae_experience":
        return schema
    if any(p in user_message for p in _TROUBLESHOOT_PHRASES):
        return schema
    if not any(p in user_message for p in _SPEC_TRIGGER_PHRASES):
        return schema
    if not _has_product_anchor(schema, user_message):
        return schema
    # 真覆盖:暴露原 LLM 选择到 intent_alternatives,便于事后排查
    alternatives = list(schema.intent_alternatives)
    if "fae_experience" not in alternatives:
        alternatives.append("fae_experience")
    ctx = current_trace_ctx()
    if ctx is not None:
        cur = ctx.current_span()
        if cur is not None:
            cur.set_metadata(
                spec_anchor_applied=True,
                spec_anchor_from="fae_experience",
                spec_anchor_to="spec_or_compat",
            )
    # 同时把 anchor 关键词填进 technical_components,避免 router 误降级
    hits = _detect_anchor_keywords(user_message)
    technical_components = schema.technical_components or hits
    return schema.model_copy(update={
        "intent": "spec_or_compat",
        "intent_confidence": "low",
        "intent_alternatives": alternatives,
        "technical_components": technical_components,
    })


def _load_template(prompts_dir: Path) -> str:
    return (prompts_dir / _TEMPLATE_FILE).read_text(encoding="utf-8")


def extract_schema(
    *,
    user_message: str,
    history_schema: RequestSchema | None,
    provider: str,
    api_key: str,
    model: str,
    prompts_dir: Path,
    base_url: str = "",
    current_turn_image_count: int = 0,
) -> RequestSchema:
    template = _load_template(prompts_dir)
    history_json = history_schema.model_dump_json() if history_schema else "null"
    user = (
        template.replace("{HISTORY_SCHEMA}", history_json)
        .replace("{USER_MESSAGE}", user_message)
        .replace("{CURRENT_TURN_IMAGE_COUNT}", str(max(0, current_turn_image_count)))
    )
    try:
        raw = complete_json(
            provider=provider,
            api_key=api_key,
            model=model,
            base_url=base_url,
            system=_SYSTEM,
            user=user,
        )
        schema = RequestSchema.model_validate(raw)
        if current_turn_image_count <= 0:
            schema = schema.model_copy(update={
                "attachment_dependency": "unknown",
            })
        elif schema.attachment_dependency == "unknown":
            # The model omitted/returned the non-image default despite a current
            # image. Preserve the conservative boundary without scanning prose.
            schema = schema.model_copy(update={
                "attachment_dependency": "required_for_answer",
            })
        schema = _anchor_spec_or_compat(schema, user_message)
        schema = _fill_spec_technical_components(schema, user_message)
        return schema
    except (LLMJsonError, ValidationError) as exc:
        # 用户原话:"LLM 不是稳定 JSON 机器"。降级:塞 catch-all 兜底 schema,
        # confidence=low 让 router 走 fae_experience 二次降级链。
        # T4(20260713):只豁免 JSON 抖动/校验失败。传输层失败(LLMTransportError)
        # 不在此捕获——自然传播到 orchestrator 的 schema_transport_error 分支
        # 如实计 fallback。replay84 实证:网关 401 曾被归为 llm_json_invalid
        # 穿过 loop 路径 O1a 豁免,84/84 降级仅 3 条进 done.fallback_used。
        logger.warning("schema_extractor fallback: %s", exc)
        # 在当前 trace span 上打 fallback 标记
        ctx = current_trace_ctx()
        if ctx is not None:
            cur = ctx.current_span()
            if cur is not None:
                reason = "llm_json_invalid" if isinstance(exc, LLMJsonError) else "schema_validation"
                cur.set_metadata(fallback_used=True, fallback_reason=reason)
        channel = history_schema.channel if history_schema else "fae"
        return RequestSchema(
            intent="fae_experience",
            intent_confidence="low",
            channel=channel,
            attachment_dependency=(
                "required_for_answer" if current_turn_image_count > 0 else "unknown"
            ),
        )
