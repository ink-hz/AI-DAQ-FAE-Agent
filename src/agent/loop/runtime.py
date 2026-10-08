"""循环运行时 (M2):模型驱动工具循环,model-agnostic。

设计约束(升级设计 §5.2):
- 预算/止损不隐藏:超预算转为"基于已有证据的边界化答案",
  done 事件 outcome=budget_exhausted,评测按失败语义处理;
- sources 只由代码从本轮工具调用记录聚合,模型不复述来源;
- outcome 与终稿字段只接受 ``submit_answer`` 终止工具提交;
- 助手自由文本只进内部 transcript,永不直接交付;
- 模型/预算/超时全部是配置(LoopConfig),换模型=改配置。
"""
from __future__ import annotations

import copy
import json
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from src.agent.loop.answer_contract import (
    SUBMIT_ANSWER_TOOL,
    render_submission,
    submit_answer_tool_schema,
    validate_submission,
)
from src.agent.loop.evidence_policy import (
    EvidenceDecision,
    EvidencePolicy,
    EvidenceSession,
    EvidenceSnapshot,
)
from src.agent.loop.link_delivery import (
    missing_link_deliveries,
    verified_urls_by_link_type,
)
from src.agent.loop.tools import ToolBox, ToolResult
from src.agent.official_links import extract_literal_https_urls
from src.agent.protocol import (
    OUTCOME_BUDGET_EXHAUSTED,
    OUTCOME_EMPTY_ANSWER,
    OUTCOME_INVALID_ANSWER_CONTRACT,
    OUTCOME_MAX_DURATION,
    OUTCOME_PROVIDER_REFUSAL,
    OUTCOME_PROVIDER_UNAVAILABLE,
    OUTCOME_RESOLVED,
    OUTCOME_SAFE_ABSTAINED,
    OUTCOME_TOOL_XML_LEAK,
    OUTCOME_TRUNCATED_ANSWER,
    OUTCOME_UNSTRUCTURED_FINAL,
    RUNTIME_FAILURE_OUTCOMES,
)

_DEFAULT_PROMPT_PATH = Path(__file__).resolve().parents[3] / "prompts" / "loop_system.md"
# F6(20260713):答案缓冲——不实时流草稿,只在确定性交付检查完成后一次性
# emit 终稿。submit_answer 后自由文本永不交付,缓冲继续保护截断和 raw
# tool XML 等运行时不变量。
_BUDGET_NOTICE = (
    "取证工具预算或时限已用尽。现在只能单独调用 submit_answer 提交终稿;"
    "基于已获得证据说明已确认内容和缺口,不得再调用取证工具或编造。")
_EVIDENCE_WRAPUP_NOTICE = (
    "已进入本轮取证收尾阶段。请基于已获得的证据单独调用 submit_answer；"
    "证据足够时直接回答，必要事实仍缺失时明确说明缺口并安全弃答，"
    "不要继续扩展检索或推断未核验的事实。")
_ANSWER_CONTRACT_NOTICE = (
    "必须通过 submit_answer 工具提交最终答案,且该工具必须单独调用。"
    "助手自由文本不会交付给用户。请按 outcome 对应字段重新完整提交一次。")
# Q0(20260708):流完成性——断流(incomplete)或输出上限(length/max_tokens)
# 的终稿轮要求完整重提工具调用,仍不完整则显式 truncated_answer 失败。
_INCOMPLETE_STOPS = {
    "incomplete",
    "length",
    "max_tokens",
    "model_context_window_exceeded",
}
_MAX_TRUNCATION_ROUNDS = 2
_MAX_EVIDENCE_RETRY_ROUNDS = 2
_MAX_URL_EVIDENCE_RETRY_ROUNDS = 1
_MAX_ATTACHMENT_EVIDENCE_RETRY_ROUNDS = 2
_SYSTEM_ATTACHMENT_FAILURE_CODES = frozenset({
    "attachment_chunks_invalid",
    "attachment_image_unavailable",
    "vision_http_error",
    "vision_invalid_response",
    "vision_output_truncated",
    "vision_runtime_error",
    "vision_timeout",
    "vision_transport_error",
    "vision_unavailable",
})
_TRUNCATION_NOTICE = (
    "上一轮响应被截断,不能接受半截文本或半截工具参数。请重新完整、单独调用"
    " submit_answer;不要续写旧 JSON,不要输出自由文本终稿。")
# F6:工具调用 XML 泄进正文(Opus xlsx-135/214:把 <invoke>/<parameter> 当文本吐出)。
# 终稿检出即重答一次;仍含则剥离后显式 tool_xml_leak 失败,不静默穿 resolved。
_TOOL_XML_RE = re.compile(r"<\s*/?\s*(?:antml:)?(?:invoke|parameter)\b")
_TOOL_XML_NOTICE = (
    "submit_answer 字段中含原始工具协议标签。"
    "请重新完整调用 submit_answer,字段中不得包含任何工具调用标签或协议标记。")
_UNVERIFIED_URL_NOTICE = (
    "submit_answer 中包含未由本轮 official_links 或 sdk_evidence 返回的 URL。"
    "请先开放取证并按命名仓库、官方资产或入口的本名调用 official_links；只允许原样"
    "使用本轮工具返回的已核验 URL。若工具仍未返回，就删除这些 URL 后再提交。"
    "包括 git clone 命令也不得自行追加 .git、路径、参数或域名。未核验 URL: {urls}"
)
_MISSING_CONSTRAINT_EVIDENCE_NOTICE = (
    "结构化需求中存在显式备选关系，但本轮还没有用 filter_models 的同 ID "
    "any_of 组完成确定性核对。请先补做这些关系组的筛选取证，再重新提交终稿；"
    "不得只在正文中自行推理 OR。开放式选型还必须在这同一次调用中使用 "
    "models=null 扫全目录。不能只单独补缺失组，必须把所有关系组在新的同一次"
    "调用中完整重提；assessment_required 必须用 requires_assessment 并复用其"
    "稳定分支 id，同时满足 profile_minimums，"
    "不能拆分 profile 下限。全部关系组: {all_ids}；当前缺失关系组: {ids}"
)
_ENGINEERING_CANDIDATE_NOTICE = (
    "全目录 filter_models 已完成，且约束结构有效，但没有 verified_full_fit 或 "
    "conditional_fit 候选。不要重复筛选，也不得把 evidence_gap 写成满足。"
    "如果已有独立场景/规格证据可给出样机方向，可提交 resolved，并设置 "
    "selection_evidence_status='engineering_candidate'，同时在 "
    "unresolved_constraints 逐条写明必须实测或补证的硬约束；Runtime 会把"
    "候选性质固定渲染为‘非已验证满足’。否则提交 safe_abstained。"
)
_MISSING_LINK_DELIVERY_NOTICE = (
    "本轮结构化需求要求在 resolved 终稿中交付已核验链接，但正文仍缺少这些类别: "
    "{categories}。请先调用 official_links，为最终推荐型号取得 model-scoped "
    "product_page，并取得 sdk_repo 或 official_docs 集成入口；然后把工具原样返回的 "
    "HTTPS URL 写入 submit_answer 正文。不得猜测、拼接或只写链接名称。"
)
_MISSING_DEVICE_SUPPORT_EVIDENCE_NOTICE = (
    "本轮问题命中型号级穷举软件支持名册，但尚未取得结构化设备支持证据。"
    "请调用 sdk_evidence；若用户明确要求链接，也可用带 canonical model_id 的 "
    "official_links。必须以返回的 roster_listed / surface_not_listed / matched 状态"
    "处理本地旧正向资料，不能仅凭 Markdown 行提交 resolved。"
)
_MISSING_SERIES_FACT_EVIDENCE_NOTICE = (
    "本轮 schema 命中了受治理产品系列，但还没有系列级事实证据。请先调用 "
    "series_fact_lookup(series=<series_id>, field=<所问事实字段>)。不得从一个成员的"
    "事实外推全系列；not_found 表示资料缺失而非不支持，conflict 必须并列说明。"
)
_MISSING_SERIES_MEMBERSHIP_EVIDENCE_NOTICE = (
    "本轮 schema 要求受治理系列成员枚举，但还没有成员表证据。请先调用 "
    "resolve_model(text=<系列名称>)，并原样使用其 series_id 与完整 model_ids。"
    "不得用任意字段事实、目录前缀或手工列表替代受治理成员集合。"
)
_MISSING_IMAGE_EVIDENCE_NOTICE = (
    "本轮用户新附加了图片，但这些 source_id 尚未成功调用 analyze_image：{ids}。"
    "OCR/search_attachments 只能提供文字线索，不能替代视觉证据。请先逐张调用 "
    "analyze_image，再重新提交终稿；不得仅凭 OCR 或通用知识声称已看图。"
)
_FAILED_IMAGE_EVIDENCE_NOTICE = (
    "本轮图片视觉分析已明确失败：{ids}。不得提交 resolved 或声称已看清图片；"
    "请用 safe_abstained 如实说明视觉分析失败和需要用户补充的材料。"
)
_FAILURE_ANSWERS = {
    OUTCOME_EMPTY_ANSWER: "本次未生成有效终稿,请重试或转人工 FAE。",
    OUTCOME_TRUNCATED_ANSWER: "本次终稿传输连续截断,请重试或转人工 FAE。",
    OUTCOME_TOOL_XML_LEAK: "本次终稿包含无效的工具协议内容,未向用户交付原文。请重试。",
    OUTCOME_UNSTRUCTURED_FINAL: "本次模型未按结构化终稿协议提交答案,请重试或转人工 FAE。",
    OUTCOME_INVALID_ANSWER_CONTRACT: "本次结构化终稿字段无效,请重试或转人工 FAE。",
    OUTCOME_BUDGET_EXHAUSTED: "本次取证达到工具调用预算,未能形成有效终稿。请重试或转人工 FAE。",
    OUTCOME_MAX_DURATION: "本次取证达到时限,未能形成有效终稿。请重试或转人工 FAE。",
    OUTCOME_PROVIDER_REFUSAL: "模型服务拒绝处理本次请求，本次未执行工具或交付部分内容。请重试或转人工 FAE。",
    OUTCOME_PROVIDER_UNAVAILABLE: "模型服务暂时不可用，请稍后重试。",
}
# P2(20260708):同一文件 read_doc 次数阈值,超过后在工具结果附收敛提示,
# 对抗"答案不在库里但模型地毯式翻书"(434 爆预算 13 条的公共形态)。
_READ_DOC_HINT_THRESHOLD = 4
_READ_DOC_HINT = (
    "\n[收敛提示] 你已对该文件调用 read_doc {n} 次。若目标信息仍未出现,"
    "说明它很可能不在本文件/本库中——停止翻书,基于已有证据如实作答,"
    "缺失就说明缺失(missing ≠ negative),不要继续地毯式阅读。")
# C2-a(20260709):同参重复调用去重反馈——434 爆预算 trace 审计发现重复
# 全部是 read_doc 同参重读(这批案子约 12% 调用浪费);确定性去重,
# 结论喂回循环一回合,不加语义规则。
_DUPLICATE_CALL_NOTICE = (
    "[重复调用] 本次调用与第 {n} 次工具调用的名称与参数完全相同,已跳过执行"
    "——结果在上文第 {n} 次调用处。请基于已有证据推进:换检索面/换字段/换型号,"
    "或直接给出结论;若证据确实不足,明说边界并按判停协议收束。")


def _strip_tool_xml(text: str) -> str:
    """剥离工具调用 XML:从首个 <invoke>/<parameter> 标签起截断,保留前面的正文。"""
    m = _TOOL_XML_RE.search(text)
    return text[: m.start()].rstrip() if m else text


def _answer_urls(text: str) -> set[str]:
    return set(extract_literal_https_urls(text))


def _verified_urls_from_payload(value: object) -> set[str]:
    urls: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"url", "repo_url"} and str(item).startswith("https://"):
                urls.add(str(item))
            else:
                urls.update(_verified_urls_from_payload(item))
    elif isinstance(value, list):
        for item in value:
            urls.update(_verified_urls_from_payload(item))
    return urls


def _constraint_evidence_status(
    tool_log: list[dict], required_relations: list[dict], *,
    require_full_catalog_scan: bool,
) -> tuple[list[str], bool, bool]:
    """Return missing relations, verified-fit evidence, and gap-only scan state."""
    def has_usable_candidates(entry: dict) -> bool:
        evidence = entry.get("constraint_evidence")
        return bool(
            isinstance(evidence, dict)
            and evidence.get("unparsed_count") == 0
            and int(evidence.get("usable_candidate_count") or 0) > 0
        )

    def is_valid_gap_only_full_catalog(entry: dict) -> bool:
        evidence = entry.get("constraint_evidence")
        return bool(
            isinstance(evidence, dict)
            and (entry.get("input") or {}).get("models") is None
            and evidence.get("unparsed_count") == 0
            and int(evidence.get("usable_candidate_count") or 0) == 0
        )

    required_by_id = {
        str(relation.get("id") or "").strip(): relation
        for relation in required_relations
        if relation.get("relation") == "any_of"
        and str(relation.get("id") or "").strip()
    }
    required_ids = list(required_by_id)
    if not required_ids:
        if not require_full_catalog_scan:
            return [], True, False
        gap_only_scan = False
        for entry in tool_log:
            if entry.get("tool") != "filter_models" or entry.get("status") != "ok":
                continue
            if (
                (entry.get("input") or {}).get("models") is None
                and has_usable_candidates(entry)
            ):
                return [], True, False
            gap_only_scan = gap_only_scan or is_valid_gap_only_full_catalog(entry)
        return [], False, gap_only_scan

    required = set(required_ids)
    best_evidenced: set[str] = set()
    gap_only_scan = False
    for entry in tool_log:
        if entry.get("tool") != "filter_models" or entry.get("status") != "ok":
            continue
        call_input = entry.get("input") or {}
        constraints = call_input.get("constraints") or []
        evidenced: set[str] = set()
        for constraint in constraints:
            if not isinstance(constraint, dict):
                continue
            children = constraint.get("any_of")
            if isinstance(children, list) and len(children) >= 2:
                group_id = str(constraint.get("id") or "").strip()
                relation = required_by_id.get(group_id)
                if relation is None:
                    continue
                alternatives = relation.get("alternatives") or []
                if len(children) < len(alternatives):
                    continue
                assessment_ids = {
                    str(item.get("id") or "").strip()
                    for item in relation.get("assessment_required") or []
                    if isinstance(item, dict) and str(item.get("id") or "").strip()
                }
                legacy_assessment_text = {
                    str(item).strip()
                    for item in relation.get("assessment_required") or []
                    if isinstance(item, str) and str(item).strip()
                }
                assessment_id_evidence = {
                    str(child.get("id") or "").strip()
                    for child in children
                    if isinstance(child, dict)
                    and child.get("op") == "requires_assessment"
                    and str(child.get("id") or "").strip()
                }
                assessment_text_evidence = {
                    str(child.get("requirement") or "").strip()
                    for child in children
                    if isinstance(child, dict)
                    and child.get("op") == "requires_assessment"
                    and str(child.get("requirement") or "").strip()
                }
                profile_minimums = relation.get("profile_minimums") or []
                profiles_evidenced = all(
                    _profile_minimum_evidenced(children, requirement)
                    for requirement in profile_minimums
                    if isinstance(requirement, dict)
                )
                if (
                    assessment_ids.issubset(assessment_id_evidence)
                    and legacy_assessment_text.issubset(assessment_text_evidence)
                    and profiles_evidenced
                ):
                    evidenced.add(group_id)
        if len(evidenced & required) > len(best_evidenced & required):
            best_evidenced = evidenced
        if required.issubset(evidenced):
            gap_only_scan = gap_only_scan or (
                is_valid_gap_only_full_catalog(entry)
            )
            if (
                not require_full_catalog_scan
                or (
                    call_input.get("models") is None
                    and has_usable_candidates(entry)
                )
            ):
                return [], True, False
    return (
        [group_id for group_id in required_ids if group_id not in best_evidenced],
        False,
        gap_only_scan,
    )


def _image_evidence_status(
    tool_log: list[dict], required_source_ids: list[str],
) -> tuple[list[str], list[str], dict[str, str]]:
    """Return successful, missing, and failed required image source IDs."""
    required = list(dict.fromkeys(required_source_ids))
    analyzed: set[str] = set()
    failures: dict[str, str] = {}
    for entry in tool_log:
        if entry.get("tool") != "analyze_image":
            continue
        source_id = str((entry.get("input") or {}).get("source_id") or "")
        if source_id not in required:
            continue
        if entry.get("status") == "ok":
            analyzed.add(source_id)
            failures.pop(source_id, None)
        elif entry.get("status") == "tool_error":
            # Preserve the causal provider failure. A later model retry only
            # produces vision_call_limit_exceeded and must not overwrite it.
            failures.setdefault(
                source_id, str(entry.get("error_code") or "tool_error")
            )
    analyzed_ids = [source_id for source_id in required if source_id in analyzed]
    missing_ids = [source_id for source_id in required if source_id not in analyzed]
    return analyzed_ids, missing_ids, failures


def _missing_series_evidence(
    tool_log: list[dict], requirements: list[dict],
) -> list[str]:
    def canonical_fact_key(item: dict) -> tuple[str, str] | None:
        if (
            item.get("tool") != "series_fact_lookup"
            or item.get("status") not in {"ok", "not_found", "conflict"}
        ):
            return None
        series_id = str(item.get("series_id") or "")
        output_field = str(item.get("field") or "")
        input_field = str((item.get("input") or {}).get("field") or "")
        if not series_id or not output_field or input_field != output_field:
            return None
        return series_id, output_field

    fact_evidence = {
        key for item in tool_log if (key := canonical_fact_key(item)) is not None
    }
    series_with_any_fact = {
        series_id for series_id, _field_id in fact_evidence
    }
    missing: list[str] = []
    for requirement in requirements:
        series_id = requirement["series_id"]
        if requirement["evidence_kind"] == "membership":
            expected_models = {
                str(model_id) for model_id in requirement.get("model_ids", [])
                if str(model_id)
            }
            membership_complete = False
            for item in tool_log:
                if str(item.get("series_id") or "") != series_id:
                    continue
                tool = item.get("tool")
                if tool == "resolve_model":
                    eligible = (
                        item.get("status") == "ok"
                        and item.get("entity_kind") == "series"
                    )
                    member_key = "model_ids"
                elif tool == "series_fact_lookup":
                    fact_key = canonical_fact_key(item)
                    eligible = (
                        fact_key is not None
                        and fact_key[0] == series_id
                    )
                    member_key = "governed_members"
                else:
                    continue
                actual_models = {
                    str(model_id) for model_id in item.get(member_key, [])
                    if str(model_id)
                }
                if eligible and expected_models and actual_models == expected_models:
                    membership_complete = True
                    break
            if not membership_complete:
                missing.append(series_id)
            continue
        field_ids = requirement["field_ids"]
        if not field_ids:
            if series_id not in series_with_any_fact:
                missing.append(series_id)
            continue
        missing.extend(
            f"{series_id}:{field_id}"
            for field_id in field_ids
            if (series_id, field_id) not in fact_evidence
        )
    return missing


def _missing_series_evidence_notices(
    requirements: list[dict], missing: list[str],
) -> list[str]:
    missing_set = set(missing)
    notices: list[str] = []
    if any(
        item.get("evidence_kind") == "membership"
        and str(item.get("series_id") or "") in missing_set
        for item in requirements
    ):
        notices.append(_MISSING_SERIES_MEMBERSHIP_EVIDENCE_NOTICE)
    if any(
        item.get("evidence_kind") != "membership"
        and (
            str(item.get("series_id") or "") in missing_set
            or any(
                f"{item.get('series_id')}:{field_id}" in missing_set
                for field_id in item.get("field_ids", [])
            )
        )
        for item in requirements
    ):
        notices.append(_MISSING_SERIES_FACT_EVIDENCE_NOTICE)
    return notices


def _profile_minimum_evidenced(
    children: list[dict], requirement: dict,
) -> bool:
    expected_field = {
        "depth": "depth_resolution_fps",
        "rgb": "rgb_resolution_fps",
    }.get(str(requirement.get("stream") or ""))
    if expected_field is None:
        return False
    minimum_keys = ("min_pixels", "min_width", "min_height", "min_fps")
    for child in children:
        if not isinstance(child, dict):
            continue
        if child.get("field") != expected_field:
            continue
        if child.get("op") != "meets_profile_minimum":
            continue
        value = child.get("value") or {}
        if not isinstance(value, dict):
            continue
        meets = True
        for key in minimum_keys:
            required_value = requirement.get(key)
            if required_value is None:
                continue
            actual_value = value.get(key)
            if not isinstance(actual_value, (int, float)) \
                    or actual_value < required_value:
                meets = False
                break
        required_format = str(requirement.get("format") or "").strip().casefold()
        actual_format = str(value.get("format") or "").strip().casefold()
        if required_format and actual_format != required_format:
            meets = False
        if meets:
            return True
    return False


def aggregate_provider_transport(rounds: list[dict]) -> dict | None:
    """Summarize provider metadata without inventing it for other adapters."""
    if not rounds:
        return None
    return {
        "mode": "anthropic_sse_buffered",
        "rounds": rounds,
        "attempts": sum(int(item.get("attempts") or 0) for item in rounds),
        "retry_count": sum(int(item.get("retry_count") or 0) for item in rounds),
        "discarded_incomplete_attempts": sum(
            int(item.get("discarded_incomplete_attempts") or 0)
            for item in rounds
        ),
        "ignored_unknown_event_type_count": sum(
            int(item.get("ignored_unknown_event_type_count") or 0)
            for item in rounds
        ),
        "protocol_error_kinds": list(dict.fromkeys(
            str(item["protocol_error_kind"])
            for item in rounds
            if item.get("protocol_error_kind")
        )),
    }


def aggregate_provider_model_echo(
    rounds: list[dict],
    *,
    llm_turns: int,
    configured_model: str,
) -> dict:
    """Describe provider model evidence without using request configuration."""
    models = [
        str(item["model"]).strip()
        for item in rounds
        if isinstance(item.get("model"), str) and str(item["model"]).strip()
    ]
    sources = [str(item.get("source") or "unavailable") for item in rounds]
    unique_models = set(models)
    conflict = len(unique_models) > 1 or "conflict" in sources
    complete = (
        llm_turns > 0
        and len(rounds) == llm_turns
        and len(models) == llm_turns
    )
    consistent = not conflict and len(unique_models) <= 1
    failure_reason = ""
    if conflict:
        failure_reason = "model_echo_conflict"
    elif not complete:
        failure_reason = "model_echo_unavailable"
    return {
        "configured_model": configured_model,
        "models": models,
        "sources": sources,
        "echo_turns": len(models),
        "llm_turns": llm_turns,
        "complete": complete,
        "consistent": consistent,
        "failure_reason": failure_reason,
    }


@dataclass
class LoopConfig:
    # 24:上线验收与部署统一口径。深层选型/SDK 题的有效取证不能被旧 16 次
    # 默认提前截断；时限仍保持 300s，避免把实验用 600s 当成用户等待目标。
    max_tool_calls: int = 24
    max_duration_s: float = 300.0
    tool_result_char_cap: int = 6000
    # 上下文裁剪:默认关闭。20260707 sentinel 实证"永远裁最旧"对证据密集型
    # 任务有害(模型重新取证,23/31→17/31);机制保留,仅显式配置时启用,
    # 正确用法是大上下文场景的兜底而非常规策略。
    tool_keep_full_last: int | None = None
    tool_trim_char_cap: int = 400


class LoopRuntime:
    def __init__(self, adapter, toolbox: ToolBox, config: LoopConfig | None = None,
                 system_prompt_path: Path | None = None, *,
                 evidence_policy: EvidencePolicy | None = None):
        self.adapter = adapter
        self.toolbox = toolbox
        self.config = config or LoopConfig()
        self.system_prompt_path = system_prompt_path or _DEFAULT_PROMPT_PATH
        self.evidence_policy = evidence_policy

    def _system_prompt(self, entity_note: str) -> str:
        text = self.system_prompt_path.read_text(encoding="utf-8")
        if entity_note:
            text += f"\n\n## 会话上下文(代码生成,勿在答案中复述)\n{entity_note}"
        return text

    def run(self, question: str, entity_note: str = "",
            history: list[dict] | None = None,
            required_constraint_relations: list[dict] | None = None,
            require_full_catalog_scan: bool = False,
            required_link_deliveries: list[dict] | None = None,
            required_attachment_source_ids: list[str] | None = None,
            required_image_source_ids: list[str] | None = None,
            attachment_dependency: str = "unknown",
            required_series_evidence: list[dict] | None = None,
            continuation_guard: Callable[[], None] | None = None,
            *, evidence_requirements: Mapping[str, object] | None = None,
            ) -> Iterator[dict]:
        cfg = self.config
        if attachment_dependency not in {
            "required_for_answer", "supplemental", "unknown",
        }:
            attachment_dependency = "unknown"
        required_series_evidence = [
            {
                "series_id": str(item.get("series_id") or ""),
                "evidence_kind": (
                    "membership"
                    if item.get("evidence_kind") == "membership"
                    else "fact"
                ),
                "field_ids": sorted({
                    str(value)
                    for value in item.get("field_ids", [])
                    if str(value)
                }),
                "model_ids": [str(value) for value in item.get("model_ids", [])],
            }
            for item in (required_series_evidence or [])
            if str(item.get("series_id") or "")
        ]
        required_image_source_ids = list(dict.fromkeys(
            required_image_source_ids or []
        ))
        # ToolBox 保留本轮原问,防止模型缩短 sdk_evidence query 时丢掉
        # wrapper/language 约束。浅拷贝不改变工具表或共享知识资产。
        self.toolbox = self.toolbox.with_request_context(question)
        device_support_matrix = getattr(self.toolbox, "device_support", None)
        device_support_query = (
            device_support_matrix.match_query(
                question,
                resolver=self.toolbox.resolver,
            )
            if device_support_matrix is not None
            else None
        )
        required_device_support_states = sorted({
            str(match.status)
            for match in getattr(device_support_query, "matches", ())
            if getattr(device_support_query, "surface", "")
            and str(match.status) in {
                "matched", "roster_listed", "surface_not_listed",
            }
        })
        required_device_support_pairs = {
            (str(model_id), str(match.status))
            for match in getattr(device_support_query, "matches", ())
            if getattr(device_support_query, "surface", "")
            and str(match.status) in {
                "matched", "roster_listed", "surface_not_listed",
            }
            for model_id in getattr(match, "model_ids", ())
        }
        device_support_evidence_required = bool(required_device_support_pairs)
        messages = [
            {"role": "system", "content": self._system_prompt(entity_note)},
            *({"role": m["role"], "content": m["content"]}
              for m in (history or [])),
            {"role": "user", "content": question},
        ]
        evidence_schemas = self.toolbox.tool_schemas()
        submit_schema = submit_answer_tool_schema()
        schemas = [*evidence_schemas, submit_schema]
        tool_log: list[dict] = []
        vision_tool_available = any(
            (schema.get("function") or {}).get("name") == "analyze_image"
            for schema in evidence_schemas
        )
        if required_image_source_ids and not vision_tool_available:
            tool_log.extend({
                "tool": "analyze_image",
                "input": {"source_id": source_id},
                "status": "tool_error",
                "error_code": "vision_unavailable",
                "duration_ms": 0,
            } for source_id in required_image_source_ids)
        sources: list[dict] = []
        seen_sources: set[tuple] = set()
        verified_urls: set[str] = set()
        verified_urls_by_type: dict[str, set[str]] = {}
        provenance_parts: list[str] = []
        t0 = time.time()
        forced_stop: str | None = None
        # 给终稿预留一次模型回合，避免模型持续扩展检索直到预算硬失败。
        # 小预算保留原有行为，避免首轮尚未取证就触发收尾。
        evidence_wrapup_threshold = (
            cfg.max_tool_calls - max(1, cfg.max_tool_calls // 4)
            if cfg.max_tool_calls >= 8 else None
        )
        evidence_wrapup_used_at_request: int | None = None
        read_doc_counts: dict[tuple, int] = {}
        dup_index: dict[tuple[str, str], tuple[int, int]] = {}
        truncation_rounds = 0
        contract_retry_rounds = 0
        evidence_retry_rounds = 0
        url_evidence_retry_rounds = 0
        attachment_evidence_retry_rounds = 0
        contract_error: str | None = None
        missing_constraint_relations: list[str] = []
        full_catalog_constraint_evidence = not bool(
            required_constraint_relations)
        gap_only_full_catalog_evidence = False
        engineering_candidate_disclosure_complete = False
        missing_link_delivery_categories = missing_link_deliveries(
            "", required_link_deliveries or [], verified_urls_by_type)
        link_delivery_complete = not missing_link_delivery_categories
        device_support_evidence_complete = not device_support_evidence_required
        missing_series_evidence = [
            item["series_id"] for item in required_series_evidence
        ]
        series_evidence_complete = not missing_series_evidence
        unverified_urls: set[str] = set()
        submitted_outcome: str | None = None
        tool_choice_strategy = str(
            getattr(self.adapter, "tool_choice_strategy", "forced")
        )
        tool_choice_was_forced = False
        provider_refusal_category: str | None = None
        provider_models: list[str] = []
        actual_model: str | None = None
        thinking_block_count = 0
        final_answer = ""
        final_outcome = OUTCOME_RESOLVED
        # T3(20260713):token usage 跨回合累计。网关不回传 usage 的回合不计入
        # usage_turns——评测按缺失口径处理,不许拿工具调用数冒充 token 成本。
        usage_totals = {"input_tokens": 0, "output_tokens": 0}
        usage_field_turns = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }
        usage_turns = 0
        llm_turns = 0
        provider_transport_rounds: list[dict] = []
        provider_model_rounds: list[dict] = []

        policy_session: EvidenceSession | None = None
        policy_snapshot: EvidenceSnapshot | None = None
        policy_failure: str | None = None
        policy_reason: str | None = None
        policy_retry_rounds = 0

        def refresh_policy_snapshot() -> None:
            nonlocal policy_snapshot, policy_failure
            try:
                assert policy_session is not None
                snapshot = policy_session.snapshot()
                if not isinstance(snapshot, EvidenceSnapshot):
                    raise TypeError("evidence_policy_snapshot_invalid")
                policy_snapshot = snapshot
            except Exception:
                # Policy errors are protocol failures, not evidence gaps. Never
                # include exception text: it can contain private evidence.
                policy_failure = "snapshot_failed"

        def observe_policy(tool_name: str, result: ToolResult) -> None:
            nonlocal policy_failure
            try:
                assert policy_session is not None
                # A domain policy cannot mutate the model-visible result or sources.
                policy_session.observe(tool_name, copy.deepcopy(result))
            except Exception:
                policy_failure = "observe_failed"
                return
            refresh_policy_snapshot()

        if self.evidence_policy is not None:
            try:
                policy_session = self.evidence_policy.begin(
                    copy.deepcopy(dict(evidence_requirements or {})))
                if not all(callable(getattr(policy_session, name, None))
                           for name in ("observe", "evaluate", "snapshot")):
                    raise TypeError("evidence_policy_session_invalid")
            except Exception:
                policy_failure = "begin_failed"
            if policy_failure is None:
                refresh_policy_snapshot()

        while True:
            if policy_failure is not None:
                final_outcome = forced_stop or OUTCOME_INVALID_ANSWER_CONTRACT
                contract_error = "evidence_policy:" + policy_failure
                final_answer = _FAILURE_ANSWERS[final_outcome]
                break
            if continuation_guard is not None:
                continuation_guard()
            self._trim_old_tool_results(messages)

            # F6:答案文本全程缓冲,完成确定性交付检查后一次性 emit 终稿。
            # 工具调用进度仍实时(前端不白屏)。
            text_parts: list[str] = []
            calls: list[dict] = []
            turn_stop = ""
            turn_provider_blocks: list[dict] | None = None
            turn_stop_details: dict | None = None
            force_submit = contract_retry_rounds > 0
            if force_submit and tool_choice_strategy == "forced":
                tool_choice_was_forced = True
            available_schemas = (
                [submit_schema]
                if (force_submit or forced_stop is not None
                    or evidence_wrapup_used_at_request is not None)
                else schemas
            )
            required_tool = SUBMIT_ANSWER_TOOL if force_submit else None
            if continuation_guard is not None:
                continuation_guard()
            for ev in self.adapter.chat(
                    messages, available_schemas, required_tool=required_tool):
                if ev["type"] == "text_delta":
                    text_parts.append(ev["text"])
                elif ev["type"] == "tool_call":
                    calls.append(ev)
                elif ev["type"] == "stop":
                    turn_stop = ev.get("stop_reason") or ""
                    raw_provider_blocks = ev.get("_provider_blocks")
                    if (
                        isinstance(raw_provider_blocks, (list, tuple))
                        and all(isinstance(block, dict) for block in raw_provider_blocks)
                    ):
                        turn_provider_blocks = copy.deepcopy(
                            list(raw_provider_blocks)
                        )
                    response_model = ev.get("response_model")
                    if (
                        isinstance(response_model, str)
                        and response_model
                    ):
                        actual_model = response_model
                        if response_model not in provider_models:
                            provider_models.append(response_model)
                    raw_thinking_count = ev.get("thinking_block_count")
                    if isinstance(raw_thinking_count, int) and not isinstance(
                        raw_thinking_count, bool,
                    ):
                        thinking_block_count += raw_thinking_count
                    if isinstance(ev.get("stop_details"), dict):
                        turn_stop_details = ev["stop_details"]
                    provider_model_rounds.append({
                        "model": ev.get("provider_model"),
                        "source": (
                            ev.get("provider_model_source") or "unavailable"
                        ),
                    })
                    transport = ev.get("transport")
                    if isinstance(transport, dict):
                        provider_transport_rounds.append(dict(transport))
                    turn_usage = ev.get("usage")
                    if turn_usage:
                        for usage_key in (
                            "input_tokens",
                            "output_tokens",
                            "cache_creation_input_tokens",
                            "cache_read_input_tokens",
                        ):
                            usage_value = turn_usage.get(usage_key)
                            if isinstance(usage_value, int) and not isinstance(
                                usage_value, bool,
                            ):
                                usage_field_turns[usage_key] += 1
                                usage_totals[usage_key] = (
                                    usage_totals.get(usage_key, 0) + usage_value
                                )
                        output_details = turn_usage.get("output_tokens_details")
                        if isinstance(output_details, dict):
                            thinking_tokens = output_details.get("thinking_tokens")
                            if isinstance(thinking_tokens, int) and not isinstance(
                                thinking_tokens, bool,
                            ):
                                usage_totals["thinking_output_tokens"] = (
                                    usage_totals.get("thinking_output_tokens", 0)
                                    + thinking_tokens
                                )
                        usage_turns += 1
            llm_turns += 1
            text = "".join(text_parts)

            # Provider refusal is terminal. Discard partial text/tool blocks before
            # any truncation, tool dispatch, or answer-contract handling.
            if turn_stop == "refusal":
                if isinstance(turn_stop_details, dict):
                    category = turn_stop_details.get("category")
                    if isinstance(category, str):
                        provider_refusal_category = category
                final_outcome = OUTCOME_PROVIDER_REFUSAL
                final_answer = _FAILURE_ANSWERS[final_outcome]
                break

            # 截断判定必须早于工具分发。半截 JSON 即使被 adapter 勉强解析成
            # tool_call 也不能执行或提交,必须要求完整重提。
            if turn_stop in _INCOMPLETE_STOPS:
                if truncation_rounds < _MAX_TRUNCATION_ROUNDS:
                    truncation_rounds += 1
                    messages.append(_assistant_message(
                        text or "(截断输出)",
                        calls,
                        provider_blocks=turn_provider_blocks,
                    ))
                    messages.append({"role": "user", "content": _TRUNCATION_NOTICE})
                    continue
                final_outcome = OUTCOME_TRUNCATED_ANSWER
                contract_error = "truncated_submission"
                final_answer = _FAILURE_ANSWERS[final_outcome]
                break

            submit_calls = [c for c in calls if c.get("name") == SUBMIT_ANSWER_TOOL]
            if submit_calls:
                if len(calls) != 1 or len(submit_calls) != 1:
                    error = "submit_answer_must_be_exclusive"
                    if self._retry_answer_contract(messages, text, calls, error,
                                                   contract_retry_rounds,
                                                   provider_blocks=turn_provider_blocks):
                        contract_retry_rounds += 1
                        continue
                    final_outcome = OUTCOME_INVALID_ANSWER_CONTRACT
                    contract_error = error
                    final_answer = _FAILURE_ANSWERS[final_outcome]
                    break

                submission, errors = validate_submission(submit_calls[0].get("arguments"))
                if errors:
                    error = ";".join(errors)
                    if self._retry_answer_contract(messages, text, calls, error,
                                                   contract_retry_rounds,
                                                   provider_blocks=turn_provider_blocks):
                        contract_retry_rounds += 1
                        continue
                    final_outcome = OUTCOME_INVALID_ANSWER_CONTRACT
                    contract_error = error
                    final_answer = _FAILURE_ANSWERS[final_outcome]
                    break

                assert submission is not None
                rendered = render_submission(submission)
                if _TOOL_XML_RE.search(rendered):
                    if self._retry_answer_contract(
                            messages, text, calls, "tool_xml_leak",
                            contract_retry_rounds, notice=_TOOL_XML_NOTICE,
                            provider_blocks=turn_provider_blocks):
                        contract_retry_rounds += 1
                        continue
                    final_outcome = OUTCOME_TOOL_XML_LEAK
                    contract_error = "tool_xml_leak"
                    final_answer = _FAILURE_ANSWERS[final_outcome]
                    break

                (
                    analyzed_image_source_ids,
                    missing_image_source_ids,
                    image_failures,
                ) = _image_evidence_status(
                    tool_log, required_image_source_ids,
                )
                system_failed_images = {
                    source_id
                    for source_id, error_code in image_failures.items()
                    if error_code in _SYSTEM_ATTACHMENT_FAILURE_CODES
                }
                may_abstain_after_vision_failure = (
                    bool(missing_image_source_ids)
                    and set(missing_image_source_ids).issubset(system_failed_images)
                    and submission.outcome != OUTCOME_RESOLVED
                )
                may_resolve_after_supplemental_vision_failure = (
                    attachment_dependency == "supplemental"
                    and bool(missing_image_source_ids)
                    and bool(image_failures)
                    and set(missing_image_source_ids).issubset(system_failed_images)
                    and submission.outcome == OUTCOME_RESOLVED
                )
                if (
                    missing_image_source_ids
                    and not may_abstain_after_vision_failure
                    and not may_resolve_after_supplemental_vision_failure
                    and forced_stop is None
                ):
                    error = (
                        "missing_required_image_analysis:"
                        + ",".join(missing_image_source_ids)
                    )
                    if (
                        attachment_evidence_retry_rounds
                        < _MAX_ATTACHMENT_EVIDENCE_RETRY_ROUNDS
                    ):
                        attachment_evidence_retry_rounds += 1
                        messages.append(_assistant_message(
                            text,
                            calls,
                            provider_blocks=turn_provider_blocks,
                        ))
                        messages.append({
                            "role": "tool",
                            "tool_call_id": submit_calls[0].get("id", ""),
                            "content": json.dumps({
                                "status": "invalid",
                                "error": error,
                            }, ensure_ascii=False),
                        })
                        notice = (
                            _FAILED_IMAGE_EVIDENCE_NOTICE
                            if set(missing_image_source_ids).issubset(
                                system_failed_images
                            )
                            else _MISSING_IMAGE_EVIDENCE_NOTICE
                        )
                        messages.append({
                            "role": "user",
                            "content": notice.format(
                                ids=", ".join(missing_image_source_ids)
                            ),
                        })
                        continue
                    final_outcome = OUTCOME_INVALID_ANSWER_CONTRACT
                    contract_error = error
                    final_answer = (
                        "本次未完成所附图片的视觉分析，无法交付可靠结论。"
                        "请重试或转人工 FAE。"
                    )
                    break

                missing_link_delivery_categories = missing_link_deliveries(
                    rendered,
                    required_link_deliveries or [],
                    verified_urls_by_type,
                )
                link_delivery_complete = not missing_link_delivery_categories

                invalid_urls = _answer_urls(rendered) - verified_urls
                if invalid_urls:
                    unverified_urls.update(invalid_urls)
                    needs_link_evidence = (
                        submission.outcome == OUTCOME_RESOLVED
                        and bool(missing_link_delivery_categories)
                        and forced_stop is None
                    )
                    if not needs_link_evidence:
                        if (
                            forced_stop is None
                            and url_evidence_retry_rounds
                            < _MAX_URL_EVIDENCE_RETRY_ROUNDS
                        ):
                            url_evidence_retry_rounds += 1
                            messages.append(_assistant_message(
                                text,
                                calls,
                                provider_blocks=turn_provider_blocks,
                            ))
                            messages.append({
                                "role": "tool",
                                "tool_call_id": submit_calls[0].get("id", ""),
                                "content": json.dumps({
                                    "status": "invalid",
                                    "error": "unverified_url",
                                    "urls": sorted(invalid_urls),
                                }, ensure_ascii=False),
                            })
                            messages.append({
                                "role": "user",
                                "content": _UNVERIFIED_URL_NOTICE.format(
                                    urls=", ".join(sorted(invalid_urls))
                                ),
                            })
                            continue
                        final_outcome = OUTCOME_INVALID_ANSWER_CONTRACT
                        contract_error = "unverified_url"
                        final_answer = _FAILURE_ANSWERS[final_outcome]
                        break

                (
                    missing_constraint_relations,
                    full_catalog_constraint_evidence,
                    gap_only_full_catalog_evidence,
                ) = _constraint_evidence_status(
                    tool_log, required_constraint_relations or [],
                    require_full_catalog_scan=require_full_catalog_scan,
                )
                engineering_candidate_disclosure_complete = bool(
                    gap_only_full_catalog_evidence
                    and not missing_constraint_relations
                    and submission.selection_evidence_status
                    == "engineering_candidate"
                    and submission.unresolved_constraints
                )
                constraint_incomplete = bool(
                    missing_constraint_relations
                    or (
                        not full_catalog_constraint_evidence
                        and not engineering_candidate_disclosure_complete
                    )
                )
                device_support_evidence_complete = (
                    not device_support_evidence_required
                    or required_device_support_pairs.issubset({
                        (str(model_id), str(match.get("status") or ""))
                        for item in tool_log
                        if item.get("tool") in {"sdk_evidence", "official_links"}
                        and item.get("status") in {"ok", "not_found"}
                        for match in item.get("device_support_matches", [])
                        if isinstance(match, dict)
                        for model_id in match.get("model_ids", [])
                    })
                )
                device_support_incomplete = not device_support_evidence_complete
                missing_series_evidence = _missing_series_evidence(
                    tool_log, required_series_evidence,
                )
                series_evidence_complete = not missing_series_evidence
                series_evidence_incomplete = not series_evidence_complete
                evidence_incomplete = (
                    submission.outcome == OUTCOME_RESOLVED
                    and (
                        constraint_incomplete
                        or bool(missing_link_delivery_categories)
                        or device_support_incomplete
                        or series_evidence_incomplete
                    )
                )
                if evidence_incomplete and forced_stop is None:
                    if series_evidence_incomplete:
                        error = (
                            "missing_series_evidence:"
                            + ",".join(missing_series_evidence)
                        )
                    elif device_support_incomplete:
                        error_parts = [
                            "device_support=" + ",".join(
                                required_device_support_states
                            )
                        ]
                        if constraint_incomplete:
                            error_parts.append(
                                "constraints=" + (
                                    ",".join(missing_constraint_relations)
                                    or "full_catalog_scan"
                                )
                            )
                        if missing_link_delivery_categories:
                            error_parts.append(
                                "links=" + ",".join(
                                    missing_link_delivery_categories
                                )
                            )
                        error = "missing_required_evidence:" + ";".join(
                            error_parts)
                    elif missing_link_delivery_categories:
                        error_parts = [
                            "links=" + ",".join(
                                missing_link_delivery_categories)
                        ]
                        if constraint_incomplete:
                            error_parts.append(
                                "constraints=" + (
                                    ",".join(missing_constraint_relations)
                                    or "full_catalog_scan"
                                )
                            )
                        error = "missing_required_evidence:" + ";".join(
                            error_parts)
                    else:
                        if missing_constraint_relations:
                            error = (
                                "missing_constraint_evidence:"
                                + ",".join(missing_constraint_relations)
                            )
                        elif gap_only_full_catalog_evidence:
                            error = "missing_engineering_candidate_disclosure"
                        else:
                            error = "missing_full_catalog_constraint_evidence"
                    if evidence_retry_rounds < _MAX_EVIDENCE_RETRY_ROUNDS:
                        evidence_retry_rounds += 1
                        messages.append(_assistant_message(
                            text,
                            calls,
                            provider_blocks=turn_provider_blocks,
                        ))
                        messages.append({
                            "role": "tool",
                            "tool_call_id": submit_calls[0].get("id", ""),
                            "content": json.dumps({
                                "status": "invalid",
                                "error": error,
                            }, ensure_ascii=False),
                        })
                        evidence_notices: list[str] = []
                        if constraint_incomplete:
                            if (
                                gap_only_full_catalog_evidence
                                and not missing_constraint_relations
                            ):
                                evidence_notices.append(
                                    _ENGINEERING_CANDIDATE_NOTICE)
                            else:
                                evidence_notices.append(
                                    _MISSING_CONSTRAINT_EVIDENCE_NOTICE.format(
                                    all_ids=", ".join(
                                        str(relation.get("id") or "")
                                        for relation in required_constraint_relations or []
                                        if str(relation.get("id") or "")),
                                    ids=(", ".join(missing_constraint_relations)
                                         or "无（关系组齐全，但未在全目录调用中共同核对）"))
                                )
                        if missing_link_delivery_categories:
                            evidence_notices.append(
                                _MISSING_LINK_DELIVERY_NOTICE.format(
                                    categories=", ".join(
                                        missing_link_delivery_categories))
                            )
                        if device_support_incomplete:
                            evidence_notices.append(
                                _MISSING_DEVICE_SUPPORT_EVIDENCE_NOTICE
                            )
                        if series_evidence_incomplete:
                            evidence_notices.extend(
                                _missing_series_evidence_notices(
                                    required_series_evidence,
                                    missing_series_evidence,
                                )
                            )
                        messages.append({
                            "role": "user",
                            "content": "\n".join(evidence_notices),
                        })
                        continue
                    final_outcome = OUTCOME_INVALID_ANSWER_CONTRACT
                    contract_error = error
                    final_answer = _FAILURE_ANSWERS[final_outcome]
                    break

                if policy_session is not None:
                    try:
                        decision = policy_session.evaluate(submission)
                        if not isinstance(decision, EvidenceDecision):
                            raise TypeError("evidence_policy_decision_invalid")
                    except Exception:
                        policy_failure = "evaluate_failed"
                        continue
                    refresh_policy_snapshot()
                    if policy_failure is not None:
                        continue
                    policy_reason = decision.reason_code or None
                    if decision.action != "allow":
                        error = "evidence_policy:" + decision.reason_code
                        if (
                            decision.action == "request_evidence"
                            and forced_stop is None
                            and policy_retry_rounds < _MAX_EVIDENCE_RETRY_ROUNDS
                        ):
                            policy_retry_rounds += 1
                            messages.append(_assistant_message(
                                text, calls, provider_blocks=turn_provider_blocks))
                            messages.append({
                                "role": "tool",
                                "tool_call_id": submit_calls[0].get("id", ""),
                                "content": json.dumps({"status": "invalid", "error": error}),
                            })
                            messages.append({"role": "user", "content": decision.notice})
                            continue
                        policy_failure = decision.reason_code
                        contract_error = error
                        final_outcome = forced_stop or OUTCOME_INVALID_ANSWER_CONTRACT
                        final_answer = _FAILURE_ANSWERS[final_outcome]
                        break

                submitted_outcome = submission.outcome
                final_answer = rendered
                final_outcome = forced_stop or submission.outcome
                break

            if calls:
                if forced_stop is not None:
                    error = "evidence_tool_after_forced_stop"
                    if self._retry_answer_contract(messages, text, calls, error,
                                                   contract_retry_rounds,
                                                   provider_blocks=turn_provider_blocks):
                        contract_retry_rounds += 1
                        continue
                    final_outcome = forced_stop
                    contract_error = error
                    final_answer = _FAILURE_ANSWERS[final_outcome]
                    break

                messages.append(_assistant_message(
                    text,
                    calls,
                    provider_blocks=turn_provider_blocks,
                ))
                for call in calls:
                    if continuation_guard is not None:
                        continuation_guard()
                    yield from self._dispatch(call, messages, tool_log,
                                              sources, seen_sources,
                                              verified_urls,
                                              verified_urls_by_type,
                                              provenance_parts,
                                              read_doc_counts, dup_index,
                                              observe_policy if policy_session is not None else None)
                    if policy_failure is not None:
                        break
                if len(tool_log) >= cfg.max_tool_calls:
                    forced_stop = OUTCOME_BUDGET_EXHAUSTED
                    messages.append({"role": "user", "content": _BUDGET_NOTICE})
                elif (time.time() - t0) >= cfg.max_duration_s:
                    forced_stop = OUTCOME_MAX_DURATION
                    messages.append({"role": "user", "content": _BUDGET_NOTICE})
                elif (
                    evidence_wrapup_threshold is not None
                    and evidence_wrapup_used_at_request is None
                    and len(tool_log) >= evidence_wrapup_threshold
                    and not (
                        required_constraint_relations
                        or require_full_catalog_scan
                        or required_link_deliveries
                        or device_support_evidence_required
                        or required_series_evidence
                        or required_image_source_ids
                        or (policy_snapshot is not None
                            and policy_snapshot.has_outstanding_requirements)
                    )
                ):
                    evidence_wrapup_used_at_request = len(tool_log)
                    messages.append({
                        "role": "user", "content": _EVIDENCE_WRAPUP_NOTICE,
                    })
                continue

            # 无 submit_answer:自由文本只保留在 transcript,反馈一次后显式失败。
            if self._retry_answer_contract(
                    messages, text, calls, "missing_submit_answer",
                    contract_retry_rounds,
                    provider_blocks=turn_provider_blocks):
                contract_retry_rounds += 1
                continue
            contract_error = "missing_submit_answer"
            if forced_stop is not None:
                final_outcome = forced_stop
            else:
                final_outcome = (OUTCOME_UNSTRUCTURED_FINAL if text.strip()
                                 else OUTCOME_EMPTY_ANSWER)
            final_answer = _FAILURE_ANSWERS[final_outcome]
            break

        required_attachment_source_ids = (
            self.toolbox.attachment_source_ids
            if required_attachment_source_ids is None
            else list(dict.fromkeys(required_attachment_source_ids))
        )
        (
            analyzed_image_source_ids,
            missing_image_source_ids,
            image_failures,
        ) = _image_evidence_status(tool_log, required_image_source_ids)
        used_attachment_source_ids: list[str] = []
        failed_attachment_source_ids: list[str] = []
        attachment_failures: list[dict] = []
        attachment_fallback_reason: str | None = None
        attachment_coverage: str | None = None
        attachment_tool_calls = [
            item for item in tool_log
            if item.get("tool") in {
                "search_attachments", "read_attachment", "analyze_image",
            }
        ]
        coverage_attachment_source_ids = list(required_attachment_source_ids)
        if not coverage_attachment_source_ids and attachment_tool_calls:
            coverage_attachment_source_ids = self.toolbox.attachment_source_ids
        if coverage_attachment_source_ids:
            used_set = {
                str(source.get("source_id"))
                for source in sources
                if source.get("type") == "user_attachment" and source.get("source_id")
            }
            used_set = (
                used_set - set(required_image_source_ids)
            ) | set(analyzed_image_source_ids)
            used_attachment_source_ids = [
                source_id
                for source_id in coverage_attachment_source_ids
                if source_id in used_set
            ]
            failed_attachment_source_ids = (
                [
                    source_id
                    for source_id in coverage_attachment_source_ids
                    if source_id not in used_set
                ]
                if any(item.get("status") == "tool_error" for item in attachment_tool_calls)
                else []
            )
            attachment_failures = [
                {
                    key: item[key]
                    for key in (
                        "tool", "status", "error_code", "retry_count",
                        "provider_stop_reason", "vision_diagnostics",
                    )
                    if key in item
                }
                for item in attachment_tool_calls if item.get("status") == "tool_error"
            ]
            attachment_fallback_reason = next((
                str(item["error_code"])
                for item in attachment_failures
                if item.get("error_code") in _SYSTEM_ATTACHMENT_FAILURE_CODES
            ), None)
            attachment_coverage = (
                "full"
                if len(used_attachment_source_ids) == len(coverage_attachment_source_ids)
                else "partial" if used_attachment_source_ids else "empty"
            )
            if (
                attachment_coverage == "empty"
                and attachment_tool_calls
                and final_outcome == OUTCOME_RESOLVED
                and not (
                    attachment_dependency == "supplemental"
                    and bool(required_image_source_ids)
                    and set(missing_image_source_ids).issubset(
                        set(image_failures)
                    )
                )
            ):
                final_outcome = OUTCOME_SAFE_ABSTAINED
                final_answer = (
                    "当前附件中没有检索到足以支撑该结论的内容，不能据此作出确定判断。"
                    "请补充包含相关信息的页面、日志片段或更清晰图片。"
                )

        if continuation_guard is not None:
            continuation_guard()
        if final_answer:
            yield {"type": "text_delta", "text": final_answer}
        done = {
            "type": "done",
            "answer": final_answer,
            "outcome": final_outcome,
            "sources": sources,
            "tool_calls": tool_log,
            "stop_reason": (
                "refusal"
                if final_outcome == OUTCOME_PROVIDER_REFUSAL
                else forced_stop
                or (
                    final_outcome
                    if final_outcome in RUNTIME_FAILURE_OUTCOMES
                    else "end_turn"
                )
            ),
            "budget": {"used": len(tool_log), "max": cfg.max_tool_calls},
            "evidence_wrapup": {
                "requested": evidence_wrapup_used_at_request is not None,
                "threshold": evidence_wrapup_threshold,
                "used_at_request": evidence_wrapup_used_at_request,
            },
            "duration_s": round(time.time() - t0, 1),
            # 工具返回全文供 evidence warning 和独立复审对账;不进用户正文。
            "provenance": "\n".join(provenance_parts)[: self._PROVENANCE_CHAR_CAP],
            # R3(20260708):续写回合可观测性
            "truncation_rounds": truncation_rounds,
            # C2-a(20260709):同参重复调用去重可观测性
            "duplicate_calls": sum(
                1 for e in tool_log if e["status"] == "duplicate"),
            # T3(20260713):token 成本可观测性。usage_turns < llm_turns 说明
            # 网关部分回合未回传 usage,totals 是下界而非全额。
            "usage": {
                **usage_totals,
                "usage_turns": usage_turns,
                "llm_turns": llm_turns,
                "usage_field_turns": usage_field_turns,
            },
            "actual_model": actual_model,
            "provider_models": provider_models,
            "thinking_block_count": thinking_block_count,
            "provider_refusal_category": provider_refusal_category,
            "answer_contract": {
                "structured": submitted_outcome is not None,
                "retry_rounds": contract_retry_rounds,
                "evidence_retry_rounds": evidence_retry_rounds,
                "url_evidence_retry_rounds": url_evidence_retry_rounds,
                "attachment_evidence_retry_rounds": (
                    attachment_evidence_retry_rounds),
                "missing_image_source_ids": missing_image_source_ids,
                "image_evidence_complete": not missing_image_source_ids,
                "attachment_dependency": attachment_dependency,
                "validation_error": contract_error,
                "missing_constraint_relations": missing_constraint_relations,
                "full_catalog_constraint_evidence": (
                    full_catalog_constraint_evidence),
                "gap_only_full_catalog_evidence": (
                    gap_only_full_catalog_evidence),
                "engineering_candidate_disclosure_complete": (
                    engineering_candidate_disclosure_complete),
                "required_link_deliveries": list(
                    required_link_deliveries or []),
                "missing_link_deliveries": missing_link_delivery_categories,
                "link_delivery_complete": link_delivery_complete,
                "device_support_evidence_required": (
                    device_support_evidence_required),
                "device_support_evidence_complete": (
                    device_support_evidence_complete),
                "required_device_support_states": (
                    required_device_support_states),
                "required_device_support_models": sorted({
                    model_id for model_id, _status in required_device_support_pairs
                }),
                "required_series_evidence": required_series_evidence,
                "missing_series_evidence": missing_series_evidence,
                "series_evidence_complete": series_evidence_complete,
                "unverified_urls": sorted(unverified_urls),
                "submitted_outcome": submitted_outcome,
                "tool_choice_forced": tool_choice_was_forced,
                "tool_choice_strategy": tool_choice_strategy,
            },
            "vision_invoked": any(
                item.get("tool") == "analyze_image" and item.get("status") == "ok"
                for item in tool_log
            ),
            "attachment_evidence_retry_rounds": attachment_evidence_retry_rounds,
            "attachment_dependency": attachment_dependency,
        }
        if required_image_source_ids:
            image_coverage = (
                "full"
                if len(analyzed_image_source_ids) == len(required_image_source_ids)
                else "partial" if analyzed_image_source_ids else "empty"
            )
            done.update({
                "image_coverage": image_coverage,
                "required_image_source_ids": required_image_source_ids,
                "analyzed_image_source_ids": analyzed_image_source_ids,
                "missing_image_source_ids": missing_image_source_ids,
                "image_failures": [
                    {"source_id": source_id, "error_code": image_failures[source_id]}
                    for source_id in required_image_source_ids
                    if source_id in image_failures
                ],
            })
        if coverage_attachment_source_ids:
            done.update({
                "planned_capabilities": ["user_attachment"],
                "capability_coverage": {"user_attachment": attachment_coverage},
                "attachment_coverage": attachment_coverage,
                "required_attachment_source_ids": required_attachment_source_ids,
                "coverage_attachment_source_ids": coverage_attachment_source_ids,
                "used_attachment_source_ids": used_attachment_source_ids,
                "failed_attachment_source_ids": failed_attachment_source_ids,
                "attachment_failures": attachment_failures,
                "attachment_fallback_used": attachment_fallback_reason is not None,
                "attachment_fallback_reason": attachment_fallback_reason,
            })
            if done["vision_invoked"] and self.toolbox.attachment_vision_model:
                done["vision_model"] = self.toolbox.attachment_vision_model
        if self.evidence_policy is not None:
            done["evidence_policy"] = {
                "retry_rounds": policy_retry_rounds,
                "reason_code": policy_reason,
                "failure_reason": policy_failure,
                "requirement_status": (
                    dict(policy_snapshot.requirement_status) if policy_snapshot else {}),
            }
            if policy_snapshot is not None:
                done["planned_capabilities"] = list(dict.fromkeys([
                    *policy_snapshot.planned_capabilities,
                    *done.get("planned_capabilities", []),
                ]))
                done["actual_capabilities"] = list(dict.fromkeys([
                    *policy_snapshot.actual_capabilities,
                    *(["user_attachment"] if used_attachment_source_ids else []),
                ]))
                done["capability_coverage"] = {
                    **policy_snapshot.capability_coverage,
                    **done.get("capability_coverage", {}),
                }
        provider_transport = aggregate_provider_transport(provider_transport_rounds)
        if provider_transport is not None:
            done["provider_transport"] = provider_transport
        provider_model_echo = aggregate_provider_model_echo(
            provider_model_rounds,
            llm_turns=llm_turns,
            configured_model=str(getattr(self.adapter, "model", "")),
        )
        done["configured_model"] = provider_model_echo["configured_model"]
        done["actual_provider_model"] = (
            provider_model_echo["models"][0]
            if provider_model_echo["complete"]
            and provider_model_echo["consistent"]
            else None
        )
        done["provider_model_echo"] = provider_model_echo
        if continuation_guard is not None:
            continuation_guard()
        yield done
        return

    _PROVENANCE_CHAR_CAP = 60_000
    _TRIM_MARK = "...[已截断,如需全文请重新调用]"

    @staticmethod
    def _retry_answer_contract(messages: list[dict], text: str, calls: list[dict],
                               error: str, retry_rounds: int, *,
                               notice: str = _ANSWER_CONTRACT_NOTICE,
                               provider_blocks: list[dict] | None = None) -> bool:
        if retry_rounds >= 1:
            return False
        messages.append(_assistant_message(
            text or "(空输出)",
            calls,
            provider_blocks=provider_blocks,
        ))
        if calls:
            payload = json.dumps({"status": "invalid", "error": error},
                                 ensure_ascii=False)
            for call in calls:
                messages.append({"role": "tool", "tool_call_id": call.get("id", ""),
                                 "content": payload})
        messages.append({"role": "user", "content": f"{notice}\n错误:{error}"})
        return True

    def _trim_old_tool_results(self, messages: list) -> None:
        """最近 N 条工具结果保留全文,更早的截断;默认关闭(见 LoopConfig)。"""
        cfg = self.config
        if cfg.tool_keep_full_last is None:
            return
        tool_idx = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
        for i in tool_idx[: max(0, len(tool_idx) - cfg.tool_keep_full_last)]:
            content = messages[i].get("content") or ""
            if len(content) > cfg.tool_trim_char_cap + len(self._TRIM_MARK) \
                    and not content.endswith(self._TRIM_MARK):
                messages[i]["content"] = (
                    content[: cfg.tool_trim_char_cap] + self._TRIM_MARK)

    def _dispatch(self, call: dict, messages: list, tool_log: list,
                  sources: list, seen: set,
                  verified_urls: set[str],
                  verified_urls_by_type: dict[str, set[str]],
                  provenance_parts: list[str],
                  read_doc_counts: dict | None = None,
                  dup_index: dict | None = None,
                  evidence_observer: Callable[[str, ToolResult], None] | None = None,
                  ) -> Iterator[dict]:
        # C2-a 去重反馈:同名同参且原结果仍完整在上下文 → 不执行,回注指引;
        # 原结果已被裁剪(带 _TRIM_MARK)则视为合法重读,放行执行。
        canon = (call["name"],
                 json.dumps(call["arguments"], ensure_ascii=False,
                            sort_keys=True))
        if dup_index is not None:
            prior = dup_index.get(canon)
            if prior is not None:
                prior_no, prior_msg_idx = prior
                prior_content = str(messages[prior_msg_idx].get("content") or "")
                if not prior_content.endswith(self._TRIM_MARK):
                    entry = {"tool": call["name"],
                             "input": self.toolbox.redact_tool_input(
                                 call["name"], call["arguments"]),
                             "status": "duplicate", "duration_ms": 0}
                    tool_log.append(entry)
                    messages.append({
                        "role": "tool", "tool_call_id": call["id"],
                        "content": _DUPLICATE_CALL_NOTICE.format(n=prior_no)})
                    yield {"type": "tool_call", **entry, "sources": []}
                    return
        t0 = time.time()
        result = self.toolbox.dispatch(call["name"], call["arguments"])
        duration_ms = int((time.time() - t0) * 1000)
        if evidence_observer is not None:
            evidence_observer(call["name"], result)
        if result.status != "tool_error":
            for src in result.sources:
                key = (
                    src.get("type"), src.get("source_id"),
                    json.dumps(src.get("locator"), ensure_ascii=False, sort_keys=True),
                    src.get("path"), src.get("section"),
                )
                if key not in seen:
                    seen.add(key)
                    sources.append(src)
            if call["name"] in {"official_links", "sdk_evidence"}:
                verified_urls.update(_verified_urls_from_payload(result.content))
                for link_type, urls in verified_urls_by_link_type(
                        result.content).items():
                    verified_urls_by_type.setdefault(link_type, set()).update(urls)
        entry = {"tool": call["name"],
                 "input": self.toolbox.redact_tool_input(call["name"], call["arguments"]),
                 "status": result.status, "duration_ms": duration_ms}
        if call["name"] == "analyze_image" and result.diagnostics:
            entry["vision_diagnostics"] = dict(result.diagnostics)
        if call["name"] in {"sdk_evidence", "official_links"}:
            content = result.content if isinstance(result.content, dict) else {}
            entry["device_support_matches"] = [
                {
                    "status": str(match.get("status") or ""),
                    "model_ids": [
                        str(model_id)
                        for model_id in match.get("model_ids", [])
                        if str(model_id)
                    ],
                }
                for match in content.get("device_support_matches", [])
                if isinstance(match, dict)
                and str(match.get("status") or "")
            ]
        if call["name"] == "series_fact_lookup":
            content = result.content if isinstance(result.content, dict) else {}
            canonical_series_id = str(content.get("series_id") or "")
            if canonical_series_id:
                entry["series_id"] = canonical_series_id
            entry["field"] = str(content.get("field") or "")
            entry["governed_members"] = [
                str(model_id) for model_id in content.get("governed_members", [])
                if str(model_id)
            ]
        if call["name"] == "resolve_model":
            content = result.content if isinstance(result.content, dict) else {}
            if content.get("entity_kind") == "series":
                entry["entity_kind"] = "series"
                canonical_series_id = str(content.get("series_id") or "")
                if canonical_series_id:
                    entry["series_id"] = canonical_series_id
                entry["model_ids"] = [
                    str(model_id) for model_id in content.get("model_ids", [])
                    if str(model_id)
                ]
        if call["name"] == "filter_models" and result.status == "ok":
            content = result.content if isinstance(result.content, dict) else {}
            fit_counts: dict[str, int] = {}
            usable_candidate_count = 0
            for fit_payload in (content.get("fit_summary") or {}).values():
                if not isinstance(fit_payload, dict):
                    continue
                fit = str(fit_payload.get("fit") or "")
                if not fit:
                    continue
                fit_counts[fit] = fit_counts.get(fit, 0) + 1
                if fit in {"verified_full_fit", "conditional_fit"}:
                    usable_candidate_count += 1
            entry["constraint_evidence"] = {
                "unparsed_count": len(content.get("unparsed") or []),
                "usable_candidate_count": usable_candidate_count,
                "fit_counts": fit_counts,
            }
        if result.status == "tool_error" and isinstance(result.content, dict):
            error_code = result.content.get("error")
            if error_code:
                entry["error_code"] = str(error_code)
            retry_count = result.content.get("retry_count")
            if isinstance(retry_count, int) and retry_count > 0:
                entry["retry_count"] = retry_count
            provider_stop_reason = result.content.get("provider_stop_reason")
            if provider_stop_reason:
                entry["provider_stop_reason"] = str(provider_stop_reason)
        elif call["name"] == "analyze_image" and isinstance(result.content, dict):
            retry_count = result.content.get("retry_count")
            if isinstance(retry_count, int) and retry_count > 0:
                entry["retry_count"] = retry_count
            provider_stop_reason = result.content.get("provider_stop_reason")
            if provider_stop_reason:
                entry["provider_stop_reason"] = str(provider_stop_reason)
        tool_log.append(entry)
        payload = json.dumps(result.to_payload(), ensure_ascii=False)
        clipped = payload[: self.config.tool_result_char_cap]
        # provenance 只收纯工具证据;收敛提示是运行时可供性,不算证据
        provenance_parts.append(clipped)
        # P2 收敛提示:同一文件反复 read_doc → 附提示对抗地毯式翻书
        if call["name"] == "read_doc" and read_doc_counts is not None:
            key = (str(call["arguments"].get("model_id")),
                   str(call["arguments"].get("file")))
            read_doc_counts[key] = read_doc_counts.get(key, 0) + 1
            n = read_doc_counts[key]
            if n > _READ_DOC_HINT_THRESHOLD:
                clipped += _READ_DOC_HINT.format(n=n)
        messages.append({"role": "tool", "tool_call_id": call["id"],
                         "content": clipped})
        if dup_index is not None:
            dup_index[canon] = (len(tool_log), len(messages) - 1)
        yield {"type": "tool_call", **entry, "sources": result.sources}


def _assistant_message(
    text: str,
    calls: list[dict],
    *,
    provider_blocks: list[dict] | None = None,
) -> dict:
    message = {
        "role": "assistant",
        "content": text or None,
        "tool_calls": [{
            "id": c["id"], "type": "function",
            "function": {"name": c["name"],
                         "arguments": json.dumps(c["arguments"], ensure_ascii=False)},
        } for c in calls],
    }
    if provider_blocks is not None:
        message["_provider_blocks"] = copy.deepcopy(provider_blocks)
    return message
