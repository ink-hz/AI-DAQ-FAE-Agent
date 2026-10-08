"""内部叙述离线观察器。

这些规则只用于离线展示质量审计,不参与 runtime 控制流,不重写答案,也不改变
outcome。自然语言形态无法靠有限词表完备识别；命中表示 presentation warning,
未命中也不代表语义质量已经通过。最终交付质量由 Codex/FAE 独立复审。

覆盖三类"客户不该看见"的内部痕迹:
- self_check/label 散文:自查/结构自检/去标记/判停纪律(F6 修复回合内部术语);
- 推理独白:面向内部的第一人称思考前言("我应如实说明""用户问的是…")——
  prod-909781a2 实证泄露形态,旧 scorer 词表未覆盖;
- 内部术语:工具名(resolve_model 等)、会话槽位——模型把系统/schema 暴露给它的
  内部标识复述进面向客户正文(02ce95b 实证:工具名 27 条、会话槽位 16 条)。

误报纪律:pattern 保持高精度——推理独白锚定明确的元叙述形态,不用宽泛的
"首先/让我"。不要为了追逐新词面扩大规则并重新接入在线门禁。
**字段名/SDK 符号刻意不用宽泛 lower_snake 启发式**:20260714 离线全量审计
(637 答案)显示,该启发式把 `wait_for_frames`/`get_depth_frame` 等**合法 SDK
代码标识**当泄露(SDK 编程答案本就该出现),误报率 46.6%。字段 ID 泄露只在
调用方显式传入 live field 列表时按**精确本名**判定,绝不用启发式撞 SDK 符号。
"""
from __future__ import annotations

import re

# ── self_check / label / 判停 散文(自 check_protocol_clean 收敛而来)──────────
# "自查[:：]"仅在前面不是"先"时命中——"先自查:"是给用户的排查建议(正常 FAE 用语)。
_SELF_CHECK_NARRATION = re.compile(
    r"标签自查|结构自检|自查(?:结论|结果|过程|回合|修复)"
    r"|(?<!先)自查\s*[:：]"
    r"|(?:去掉|撤掉|撤销)标记|予以维持|按\s*resolved|修复过程"
    r"|标记.{0,6}重新输出|重新输出.{0,4}[:：]"
    r"|判停(?:条件|纪律|协议)|符合判停|触发判停"
)

# ── 推理独白:面向内部的第一人称思考前言(prod-909781a2)──────────────────────
# 高精度:客户面前 FAE 说"您"不说"用户";答案直接给结论,不叙述"我应/我需要说明"。
_REASONING_PREAMBLE = re.compile(
    r"我应(?:该)?如实"
    r"|用户(?:问的是|想(?:要)?知道的是|想了解的是|的(?:真实|核心)?(?:问题|需求|意图)是)"
    r"|(?:需要|应|我先|我来|先)向用户(?:说明|解释|澄清|指出|确认这)"
    r"|我(?:先|来|先来)(?:梳理|理清|分析|拆解)一下(?:这|该|用户)"
)

# ── 内部术语:工具名 + 会话槽位(固定表)────────────────────────────────────
_TOOL_NAMES = (
    "resolve_model", "fact_lookup", "search_knowledge", "read_doc",
    "sdk_evidence", "session_state", "list_models", "search_qa",
    "sampling_geometry",
)
_TOOL_NAME_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:" + "|".join(_TOOL_NAMES) + r")(?![A-Za-z0-9_])")
_SLOT_TERM_RE = re.compile(r"会话槽位|(?<![认插])槽位")

# 精确字段本名匹配器:仅在 field_ids 传入时,按 live 字段 ID 逐一比对(词边界),
# 不用 lower_snake 启发式——后者会撞 SDK 代码符号(合法内容),见模块 docstring。
_WORD_BOUND = r"(?<![A-Za-z0-9_]){}(?![A-Za-z0-9_])"


def find_internal_narration(text: str, field_ids: list[str] | None = None) -> list[str]:
    """返回正文中命中的内部叙述/术语片段;空列表即干净。

    field_ids 传入时,额外把**确属 live 字段 ID** 的本名(含下划线者)计为泄露;
    不传时只查叙述/判停/工具名/会话槽位(高精度,零 SDK 符号误伤)。
    """
    if not text:
        return []
    hits: list[str] = []
    hits += _SELF_CHECK_NARRATION.findall(text)
    hits += _REASONING_PREAMBLE.findall(text)
    hits += _TOOL_NAME_RE.findall(text)
    hits += _SLOT_TERM_RE.findall(text)
    if field_ids:
        for fid in field_ids:
            if "_" in fid and re.search(_WORD_BOUND.format(re.escape(fid)), text):
                hits.append(fid)
    # 去重保序
    seen: set[str] = set()
    uniq: list[str] = []
    for h in hits:
        if h not in seen:
            seen.add(h)
            uniq.append(h)
    return uniq
