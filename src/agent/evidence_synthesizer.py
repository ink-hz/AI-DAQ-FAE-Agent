"""evidence_synthesizer.py — Phase B6 核心:并发取证 + LLM 合成 + outcome 自检。

synthesize_stream() 负责:
  1. 早期退出(refused / needs_clarify)
  2. 并发跑 capability runners(ThreadPoolExecutor max_workers=3)
  3. 全空证据:有咨询上下文则调 LLM 综合,无上下文才短提示
  4. 构造 evidence_synth.md 提示词
  5. 调 LLM(complete_json)
  6. 解析输出 + outcome 自检(is_unsafe_fail)
  7. yield delta / sources / done 事件
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import re
from typing import Iterator

from src.agent.answer_quality import with_fae_answer_quality
from src.agent.capabilities import REGISTRY
from src.agent.evidence import Evidence
from src.agent.guardrails import clean_answer_text
from src.agent.planner import PlanResult
from src.agent.schema import RequestSchema

_PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "evidence_synth.md"

_SYS = "你是 Orbbec FAE Agent 的 Evidence Synthesizer。严格按要求输出 JSON。"
_CTX_SYS = "你是 Orbbec AI FAE Agent 的上下文咨询综合节点。直接输出用户可见答复。"
_REWRITE_SYS = "你是 Orbbec AI FAE Agent 的回答质量改写节点。直接输出用户可见答复。"
_MAX_PUBLIC_SOURCES = 8
_SECTION_HEADING_RE = re.compile(
    r"(?m)^\s*(?:#+\s*)?(?:\*\*)?\s*"
    r"(结论|依据|注意|注意 / 风险|适用条件 / 注意事项|"
    r"验证 / 下一步|验证建议 / 下一步|验证|下一步)"
    r"\s*(?:\*\*)?\s*(?:[:：])?\s*$"
)
_NUMBERED_STEP_RE = re.compile(r"(?m)^\s*(\d+)[.、]\s+")
_CODE_FENCE_RE = re.compile(r"```([^\n`]*)\n(.*?)```", re.DOTALL)
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_EXECUTABLE_CODE_LANGS = {
    "python", "py", "cpp", "c++", "c", "cc", "h", "hpp",
    "bash", "sh", "shell", "zsh", "powershell", "ps1",
    "javascript", "js", "typescript", "ts", "java", "c#",
    "cs", "xml", "yaml", "yml", "json",
}
_SDK_PROGRAMMING_CODE_LANGS = {
    "python", "py", "cpp", "c++", "c", "cc", "h", "hpp",
    "javascript", "js", "typescript", "ts", "java", "c#", "cs",
}

_CAPABILITY_AGENTS: dict[str, str] = {
    "catalog": "Catalog Agent",
    "spec": "Spec Agent",
    "selection": "Selection Agent",
    "experience": "Troubleshooting Agent",
    "sdk": "SDK Agent",
    "risk_compliance": "Risk Reviewer Agent",
}


def _progress(
    stage: str,
    status: str,
    message: str,
    *,
    agent: str | None = None,
    metadata: dict | None = None,
) -> dict:
    payload: dict = {
        "stage": stage,
        "status": status,
        "message": message,
        "agent": agent or "AI FAE Agent",
    }
    if metadata:
        payload["metadata"] = metadata
    return {"stage": payload}


def _capability_agent(capability: str) -> str:
    return _CAPABILITY_AGENTS.get(capability, f"{capability} Agent")


def synthesize_stream(
    user_message: str,
    plan: PlanResult,
    *,
    schema: RequestSchema | None,
    channel: str,
    product_catalog: dict[str, dict],
    llm_provider: str,
    llm_api_key: str,
    llm_base_url: str,
    model_main: str,
    qa_collection=None,
    qa_enabled: bool = True,
    fact_store=None,
    model_resolver=None,
    embed_api_key: str = "",
    embed_model: str = "",
    sdk_knowledge_dir: Path | None = None,
    consultation_context: str = "",
    timeout_per_capability: float = 30.0,
) -> Iterator[dict]:
    """Orchestrate capability runners concurrently then synthesize via LLM.

    Yields dicts matching SSE pattern:
      {"delta": str}
      {"sources": list[str]}
      {"outcome": str, "capability_coverage": dict, "fallback_used": bool,
       "fallback_reason": str | None, "bucket": "composition"}
    """

    # ── 1. Early exit: refused ──────────────────────────────────────────────
    if plan.refused:
        yield {"delta": plan.refusal_text or "本助手暂不支持该问题。"}
        yield {
            "outcome": "safe_abstained",
            "capability_coverage": {},
            "fallback_used": False,
            "fallback_reason": None,
            "bucket": "composition",
        }
        return

    # ── 2. Early exit: needs_clarify ────────────────────────────────────────
    if plan.needs_clarify:
        yield {"delta": _render_needs_clarify_answer(user_message, plan)}
        yield {
            "outcome": "safe_abstained",
            "capability_coverage": {},
            "fallback_used": False,
            "fallback_reason": None,
            "bucket": "composition",
        }
        return

    # ── 3. Concurrent capability runs ───────────────────────────────────────
    caps_to_run: list[str] = [plan.primary_capability] + list(plan.extra_capabilities)
    # deduplicate, preserve order
    seen: set[str] = set()
    ordered: list[str] = []
    for c in caps_to_run:
        if c not in seen:
            seen.add(c)
            ordered.append(c)

    online_qa_collection = qa_collection if qa_enabled else None
    evidences: dict[str, Evidence] = {}

    pool = ThreadPoolExecutor(max_workers=3)
    futures: dict = {}
    for cap in ordered:
        agent = _capability_agent(cap)
        yield _progress(
            "capability",
            "started",
            f"{agent} 开始取证",
            agent=agent,
            metadata={
                "capability": cap,
                "query": plan.capability_queries.get(cap, user_message)[:120],
            },
        )
        runner = REGISTRY.get(cap)
        if runner is None:
            evidences[cap] = Evidence(
                source_module=cap,  # type: ignore[arg-type]
                facts=[],
                confidence="low",
                coverage="empty",
                notes=[f"未注册 capability: {cap}"],
                latency_ms=0,
                error="not_registered",
            )
            yield _progress(
                "capability",
                "error",
                f"{agent} 未注册,已记录为空证据",
                agent=agent,
                metadata={
                    "capability": cap,
                    "coverage": "empty",
                    "facts": 0,
                    "error": "not_registered",
                },
            )
            continue
        query = plan.capability_queries.get(cap, user_message)
        fut = pool.submit(
            runner.run, query,
            schema=schema, channel=channel,
            product_catalog=product_catalog,
            qa_collection=online_qa_collection,
            qa_enabled=qa_enabled,
            fact_store=fact_store,
            model_resolver=model_resolver,
            api_key=embed_api_key or llm_api_key,
            embed_model=embed_model,
            sdk_knowledge_dir=sdk_knowledge_dir,
        )
        futures[fut] = cap

    # Collect all results with per-future timeout
    for fut, cap in futures.items():
        agent = _capability_agent(cap)
        try:
            evidences[cap] = fut.result(timeout=timeout_per_capability)
            ev = evidences[cap]
            stage_metadata = {
                "capability": cap,
                "coverage": ev.coverage,
                "confidence": ev.confidence,
                "facts": len(ev.facts),
                "latency_ms": ev.latency_ms,
                "error": ev.error,
            }
            if ev.metadata:
                stage_metadata["evidence_metadata"] = ev.metadata
            yield _progress(
                "capability",
                "completed",
                f"{agent} 取证完成: {ev.coverage}, {len(ev.facts)} 条事实",
                agent=agent,
                metadata=stage_metadata,
            )
        except Exception as exc:
            evidences[cap] = Evidence(
                source_module=cap,  # type: ignore[arg-type]
                facts=[],
                confidence="low",
                coverage="empty",
                notes=[f"runner crash: {exc}"],
                latency_ms=0,
                error=str(exc),
            )
            yield _progress(
                "capability",
                "error",
                f"{agent} 取证失败: {type(exc).__name__}",
                agent=agent,
                metadata={
                    "capability": cap,
                    "coverage": "empty",
                    "facts": 0,
                    "error": str(exc)[:160],
                },
            )

    # Abandon timed-out threads; do not block on their completion
    pool.shutdown(wait=False)

    enriched_spec = _enrich_spec_from_candidate_evidence(
        user_message=user_message,
        plan=plan,
        schema=schema,
        channel=channel,
        evidences=evidences,
        product_catalog=product_catalog,
        qa_collection=online_qa_collection,
        fact_store=fact_store,
        model_resolver=model_resolver,
        embed_api_key=embed_api_key,
        embed_model=embed_model,
        sdk_knowledge_dir=sdk_knowledge_dir,
    )
    if enriched_spec is not None:
        evidences["spec"] = enriched_spec
        stage_metadata = {
            "capability": "spec",
            "coverage": enriched_spec.coverage,
            "confidence": enriched_spec.confidence,
            "facts": len(enriched_spec.facts),
            "latency_ms": enriched_spec.latency_ms,
            "enrichment": "candidate_spec",
        }
        if enriched_spec.metadata:
            stage_metadata["evidence_metadata"] = enriched_spec.metadata
        yield _progress(
            "capability",
            "completed",
            f"Spec Agent 候选补证完成: {enriched_spec.coverage}, {len(enriched_spec.facts)} 条事实",
            agent="Spec Agent",
            metadata=stage_metadata,
        )

    coverage_map = {cap: ev.coverage for cap, ev in evidences.items()}
    facts_count = sum(len(ev.facts) for ev in evidences.values())
    yield _progress(
        "retrieval",
        "completed",
        f"证据检索完成: {len(evidences)} 个能力, {facts_count} 条事实",
        agent="Evidence Retrieval Agent",
        metadata={
            "capabilities": list(evidences.keys()),
            "coverage": coverage_map,
            "facts": facts_count,
            # 进 prompt 的事实清单(可观测性):定位"证据在库但没进上下文"类问题
            "prompt_fact_refs": prompt_fact_refs(evidences),
        },
    )

    # ── 4. All-empty evidence recovery ──────────────────────────────────────
    if all(ev.coverage == "empty" for ev in evidences.values()):
        context_answer = _synthesize_context_only_answer(
            user_message,
            plan,
            channel,
            consultation_context=consultation_context,
            llm_provider=llm_provider,
            llm_api_key=llm_api_key,
            llm_base_url=llm_base_url,
            model_main=model_main,
        )
        if context_answer:
            # M5 退役(20260709):基于答案文本语义的 outcome 提升已删,
            # 无证据的上下文综合答复一律 safe_abstained。
            yield {"delta": context_answer}
            yield {"sources": []}
            yield {
                "outcome": "safe_abstained",
                "capability_coverage": {cap: ev.coverage for cap, ev in evidences.items()},
                "fallback_used": False,
                "fallback_reason": None,
                "bucket": "composition",
            }
            return

        yield {"delta": "我没有足够上下文继续判断。请补充场景、型号、距离/精度目标或现场现象。"}
        yield {"sources": []}
        yield {
            "outcome": "safe_abstained",
            "capability_coverage": {cap: ev.coverage for cap, ev in evidences.items()},
            "fallback_used": True,
            "fallback_reason": "all_empty",
            "bucket": "composition",
        }
        return

    # ── 5. Build prompt ─────────────────────────────────────────────────────
    prompt_text = _build_prompt(
        user_message,
        plan,
        channel,
        evidences,
        consultation_context=consultation_context,
    )

    # ── 6. Call LLM ─────────────────────────────────────────────────────────
    from src.agent.llm_client import LLMJsonError, complete, complete_json

    yield _progress(
        "llm",
        "started",
        f"调用主模型综合 {len(evidences)} 个能力的证据",
        agent="Answer Synthesizer Agent",
        metadata={
            "model": model_main,
            "capabilities": list(evidences.keys()),
            "facts": facts_count,
        },
    )
    try:
        raw = complete_json(
            provider=llm_provider,
            api_key=llm_api_key,
            base_url=llm_base_url,
            model=model_main,
            system=_SYS,
            user=prompt_text,
            max_tokens=4096,
            temperature=0.2,
            max_retries=2,
        )
        json_degraded = False
        yield _progress(
            "llm",
            "completed",
            "主模型结构化合成完成",
            agent="Answer Synthesizer Agent",
            metadata={"model": model_main},
        )
    except LLMJsonError:
        yield _progress(
            "llm",
            "warning",
            "主模型 JSON 输出不稳定,切换为文本合成重试",
            agent="Answer Synthesizer Agent",
            metadata={"model": model_main, "repair": "text_retry"},
        )
        compact_prompt = _build_compact_prompt(
            user_message,
            plan,
            channel,
            evidences,
            consultation_context=consultation_context,
        )
        try:
            compact_text = complete(
                provider=llm_provider,
                api_key=llm_api_key,
                base_url=llm_base_url,
                model=model_main,
                system=_SYS,
                user=compact_prompt,
                max_tokens=3072,
                temperature=0.0,
            )
            compact_text = clean_answer_text(compact_text)
            # 降级不隐藏(20260707 铁律执行):文本重试是降级路径——事实配额
            # 更紧、无结构化元数据,必须以 fallback 形式暴露并计入评测失败,
            # 不得硬编码 resolved 冒充一等答案(336L 深度范围静默丢失案)。
            json_degraded = True
            raw = {
                "text": compact_text,
                "outcome": "resolved",
                "coverage_per_capability": {
                    cap: ev.coverage for cap, ev in evidences.items()
                },
            }
            yield _progress(
                "llm",
                "completed",
                "主模型文本合成重试完成",
                agent="Answer Synthesizer Agent",
                metadata={"model": model_main, "repair": "text_retry"},
            )
        except Exception as exc:
            yield _progress(
                "llm",
                "error",
                f"主模型合成失败: {type(exc).__name__}",
                agent="Answer Synthesizer Agent",
                metadata={"model": model_main, "error": str(exc)[:160]},
            )
            yield {"delta": "系统暂时无法回答,请稍后重试。"}
            yield {"sources": []}
            yield {
                "outcome": "safe_abstained",
                "capability_coverage": {cap: ev.coverage for cap, ev in evidences.items()},
                "fallback_used": True,
                "fallback_reason": f"llm_error: {type(exc).__name__}: {str(exc)[:120]}",
                "bucket": "composition",
            }
            return
    except Exception as exc:
        yield _progress(
            "llm",
            "error",
            f"主模型合成失败: {type(exc).__name__}",
            agent="Answer Synthesizer Agent",
            metadata={"model": model_main, "error": str(exc)[:160]},
        )
        yield {"delta": "系统暂时无法回答,请稍后重试。"}
        yield {"sources": []}
        yield {
            "outcome": "safe_abstained",
            "capability_coverage": {cap: ev.coverage for cap, ev in evidences.items()},
            "fallback_used": True,
            "fallback_reason": f"llm_error: {type(exc).__name__}: {str(exc)[:120]}",
            "bucket": "composition",
        }
        return

    # ── 7. Parse LLM output ─────────────────────────────────────────────────
    text_out = clean_answer_text(raw.get("text") or "")
    sources = _select_public_sources(evidences)
    declared_outcome = _normalize_declared_outcome(
        raw.get("outcome") or "resolved",
        plan=plan,
        evidences=evidences,
        sources=sources,
    )

    yield _progress(
        "quality",
        "started",
        "检查答复可读性、证据覆盖和安全边界",
        agent="Quality Reviewer Agent",
        metadata={
            "declared_outcome": declared_outcome,
            "sources_count": len(sources),
        },
    )

    if not text_out:
        yield _progress(
            "quality",
            "error",
            "质量检查发现空答复",
            agent="Quality Reviewer Agent",
            metadata={"reason": "empty_text", "sources_count": len(sources)},
        )
        yield {"delta": "系统暂时无法回答,请稍后重试。"}
        yield {"sources": sources}
        yield {
            "outcome": "safe_abstained",
            "capability_coverage": {cap: ev.coverage for cap, ev in evidences.items()},
            "fallback_used": True,
            "fallback_reason": "empty_text",
            "bucket": "composition",
        }
        return
    if _structurally_broken(text_out):
        yield _progress(
            "quality",
            "warning",
            "检测到答复结构不完整,调用质量改写",
            agent="Quality Reviewer Agent",
            metadata={"reason": "incomplete_answer"},
        )
        repaired_text = _rewrite_incomplete_answer(
            user_message,
            plan,
            channel,
            evidences,
            consultation_context=consultation_context,
            llm_provider=llm_provider,
            llm_api_key=llm_api_key,
            llm_base_url=llm_base_url,
            model_main=model_main,
        )
        if repaired_text:
            repaired_text = _sanitize_answer_contract_violation(
                repaired_text,
                _answer_contract_violation(repaired_text, plan, evidences),
                evidences,
            )
            text_out = repaired_text
            followup_violation = _answer_contract_violation(text_out, plan, evidences)
            if followup_violation:
                yield {"delta": "系统暂时无法生成可靠的 FAE 答复,请稍后重试。"}
                yield {"sources": sources}
                yield {
                    "outcome": "safe_abstained",
                    "capability_coverage": {cap: ev.coverage for cap, ev in evidences.items()},
                    "fallback_used": True,
                    "fallback_reason": f"answer_contract_violation:{followup_violation}",
                    "bucket": "composition",
                }
                return
        else:
            yield {"delta": "系统暂时无法生成可靠的 FAE 答复,请稍后重试。"}
            yield {"sources": sources}
            yield {
                "outcome": "safe_abstained",
                "capability_coverage": {cap: ev.coverage for cap, ev in evidences.items()},
                "fallback_used": True,
                "fallback_reason": "quality_rewrite_failed",
                "bucket": "composition",
            }
            return
    text_out = _ensure_verified_sdk_code_snippet(text_out, evidences)

    contract_violation = _answer_contract_violation(text_out, plan, evidences)
    if contract_violation:
        sanitized_text = _sanitize_answer_contract_violation(text_out, contract_violation, evidences)
        followup_violation = _answer_contract_violation(sanitized_text, plan, evidences)
        if not followup_violation:
            text_out = sanitized_text
            contract_violation = None
    if contract_violation:
        yield _progress(
            "quality",
            "warning",
            "检测到答复违反 planner 答复协议,调用质量改写",
            agent="Quality Reviewer Agent",
            metadata={"reason": contract_violation},
        )
        repaired_text = _rewrite_low_quality_answer(
            user_message,
            plan,
            channel,
            evidences,
            candidate_text=text_out,
            consultation_context=consultation_context,
            llm_provider=llm_provider,
            llm_api_key=llm_api_key,
            llm_base_url=llm_base_url,
            model_main=model_main,
        )
        if repaired_text:
            repaired_text = _sanitize_answer_contract_violation(
                repaired_text,
                _answer_contract_violation(repaired_text, plan, evidences),
                evidences,
            )
            followup_violation = _answer_contract_violation(repaired_text, plan, evidences)
            if followup_violation:
                yield {"delta": "系统暂时无法生成可靠的 FAE 答复,请稍后重试。"}
                yield {"sources": sources}
                yield {
                    "outcome": "safe_abstained",
                    "capability_coverage": {cap: ev.coverage for cap, ev in evidences.items()},
                    "fallback_used": True,
                    "fallback_reason": f"quality_rewrite_still_violates_contract:{followup_violation}",
                    "bucket": "composition",
                }
                return
            text_out = repaired_text
        else:
            yield {"delta": "系统暂时无法生成可靠的 FAE 答复,请稍后重试。"}
            yield {"sources": sources}
            yield {
                "outcome": "safe_abstained",
                "capability_coverage": {cap: ev.coverage for cap, ev in evidences.items()},
                "fallback_used": True,
                "fallback_reason": f"answer_contract_violation:{contract_violation}",
                "bucket": "composition",
            }
            return

    text_out = _ensure_verified_sdk_code_snippet(text_out, evidences)
    text_out = _preserve_requested_sdk_terms(text_out, user_message, evidences)

    # ── 8. outcome 自检 via is_unsafe_fail ──────────────────────────────────
    from src.agent.composition.self_check import is_unsafe_fail

    self_check = is_unsafe_fail(
        {
            "expected_outcome": declared_outcome,
            "must_not": [],
            "question": user_message,
        },
        {
            "outcome": declared_outcome,
            "text": text_out,
            "sources": sources,
            "answer_contract": list(getattr(plan, "answer_contract", []) or []),
            "evidence_texts": [
                f"{fact.statement}"
                for ev in evidences.values()
                for fact in ev.facts
            ],
        },
    )
    final_outcome = "safe_abstained" if self_check else declared_outcome
    fallback_used = self_check or json_degraded
    if self_check:
        fallback_reason: str | None = "self_check_downgrade"
    elif json_degraded:
        fallback_reason = "synthesis_json_parse_failed_text_retry"
    else:
        fallback_reason = None

    yield _progress(
        "quality",
        "completed",
        "质量检查通过" if not self_check else "质量检查触发安全降级",
        agent="Quality Reviewer Agent",
        metadata={
            "outcome": final_outcome,
            "fallback_used": fallback_used,
            "fallback_reason": fallback_reason,
            "sources_count": len(sources),
        },
    )

    # ── 9. Yield results ────────────────────────────────────────────────────
    yield {"delta": text_out}
    yield {"sources": sources}
    yield {
        "outcome": final_outcome,
        "capability_coverage": {cap: ev.coverage for cap, ev in evidences.items()},
        "fallback_used": fallback_used,
        "fallback_reason": fallback_reason,
        "bucket": "composition",
    }


def _enrich_spec_from_candidate_evidence(
    *,
    user_message: str,
    plan: PlanResult,
    schema: RequestSchema | None,
    channel: str,
    evidences: dict[str, Evidence],
    product_catalog: dict[str, dict],
    qa_collection,
    fact_store=None,
    model_resolver=None,
    embed_api_key: str,
    embed_model: str,
    sdk_knowledge_dir: Path | None,
) -> Evidence | None:
    """Use candidate models discovered by other capabilities to fetch specs.

    Capability runners are intentionally parallel. For selection questions this
    means `selection` may discover candidate models while `spec` has already run
    with no product anchors. This second pass is a generic evidence-enrichment
    step: only catalog-backed model mentions from sourced facts are used.
    """
    if not product_catalog:
        return None
    if "spec" not in REGISTRY:
        return None
    if not _should_enrich_candidate_specs(plan, evidences):
        return None

    products = _candidate_products_from_evidences(evidences, product_catalog)
    if not products:
        return None

    candidate_schema = _schema_with_candidate_products(schema, products, user_message)
    runner = REGISTRY["spec"]
    enriched = runner.run(
        user_message,
        schema=candidate_schema,
        channel=channel,
        product_catalog=product_catalog,
        qa_collection=qa_collection,
        fact_store=fact_store,
        model_resolver=model_resolver,
        api_key=embed_api_key,
        embed_model=embed_model,
        sdk_knowledge_dir=sdk_knowledge_dir,
    )
    if enriched.coverage == "empty" or not enriched.facts:
        return None

    current = evidences.get("spec")
    if current is None or not current.facts:
        return enriched
    return _merge_evidence(current, enriched)


def _should_enrich_candidate_specs(
    plan: PlanResult,
    evidences: dict[str, Evidence],
) -> bool:
    question_type = getattr(plan, "question_type", "unknown")
    planned = {plan.primary_capability, *list(plan.extra_capabilities)}
    if "spec" not in planned and question_type not in {"selection", "integration_design"}:
        return False
    current = evidences.get("spec")
    if current is None:
        return True
    return current.coverage != "full" or not any(fact.source_ref for fact in current.facts)


def _candidate_products_from_evidences(
    evidences: dict[str, Evidence],
    product_catalog: dict[str, dict],
    *,
    max_products: int = 3,
) -> list[str]:
    text_parts: list[str] = []
    for cap in ("selection", "catalog", "experience"):
        ev = evidences.get(cap)
        if ev is None:
            continue
        for fact in ev.facts:
            if fact.source_ref:
                text_parts.append(fact.statement)
    if not text_parts:
        return []

    text = "\n".join(text_parts)
    matches: list[tuple[int, int, str]] = []
    for model in sorted(product_catalog, key=len, reverse=True):
        display = model.replace("_", " ")
        for alias in _candidate_model_aliases(model):
            match = _find_candidate_alias(alias, text)
            if match is None:
                continue
            matches.append((match.start(), -len(alias), display))
            break

    products: list[str] = []
    seen: set[str] = set()
    for _, _, display in sorted(matches):
        if display in seen:
            continue
        seen.add(display)
        products.append(display)
        if len(products) >= max_products:
            break
    return products


def _candidate_model_aliases(model: str) -> list[str]:
    parts = [part for part in str(model).split("_") if part]
    aliases = {
        model,
        model.replace("_", " "),
        model.replace("_", ""),
    }
    if len(parts) >= 2:
        suffix = " ".join(parts[1:])
        aliases.add(suffix)
        aliases.add("".join(parts[1:]))
        aliases.add(parts[-1])
    return sorted(
        (alias for alias in aliases if alias and not alias.isdigit()),
        key=len,
        reverse=True,
    )


def _find_candidate_alias(alias: str, text: str) -> re.Match | None:
    escaped = re.escape(alias)
    escaped = escaped.replace(r"\ ", r"[\s_-]*")
    pattern = re.compile(
        rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])",
        re.IGNORECASE,
    )
    return pattern.search(text)


def _schema_with_candidate_products(
    schema: RequestSchema | None,
    products: list[str],
    user_message: str,
) -> RequestSchema:
    if schema is None:
        return RequestSchema(
            intent="selection",
            intent_confidence="low",
            products=list(products),
            scenario=[user_message],
        )
    merged_products = list(getattr(schema, "products", []) or [])
    for product in products:
        if product not in merged_products:
            merged_products.append(product)
    scenario = list(getattr(schema, "scenario", []) or [])
    if not scenario and user_message:
        scenario = [user_message]
    return RequestSchema(
        intent=schema.intent,
        intent_confidence=schema.intent_confidence,
        intent_alternatives=list(schema.intent_alternatives),
        scenario=scenario,
        constraints=list(schema.constraints),
        products=merged_products,
        platforms=list(schema.platforms),
        technical_components=list(schema.technical_components),
        channel=schema.channel,
        missing_for_bucket=list(schema.missing_for_bucket),
        is_topic_switch=schema.is_topic_switch,
    )


def _merge_evidence(base: Evidence, extra: Evidence) -> Evidence:
    seen: set[tuple[str, str]] = set()
    facts = []
    for fact in [*base.facts, *extra.facts]:
        key = (fact.statement, fact.source_ref or "")
        if key in seen:
            continue
        seen.add(key)
        facts.append(fact)
    coverage = "full" if base.coverage == "full" or extra.coverage == "full" else extra.coverage
    confidence = "high" if base.confidence == "high" or extra.confidence == "high" else "low"
    return Evidence(
        source_module=base.source_module,
        facts=facts,
        confidence=confidence,
        coverage=coverage,
        notes=[*base.notes, *extra.notes, "候选型号补充规格取证"],
        latency_ms=base.latency_ms + extra.latency_ms,
        error=base.error or extra.error,
        metadata=_merge_evidence_metadata(base.metadata, extra.metadata),
    )


def _merge_evidence_metadata(
    base: dict[str, object],
    extra: dict[str, object],
) -> dict[str, object]:
    merged: dict[str, object] = dict(extra)
    for key, value in base.items():
        existing = merged.get(key)
        if isinstance(value, dict) and isinstance(existing, dict):
            merged[key] = {**existing, **value}
        else:
            merged[key] = value
    return merged


def _normalize_declared_outcome(
    declared: str,
    *,
    plan: PlanResult,
    evidences: dict[str, Evidence],
    sources: list[str],
) -> str:
    """Promote overly conservative model outcomes when evidence is conclusive.

    M5 退役(20260709):基于答案文本语义的提升分支(bounded engineering /
    confirmed+unknown 关键词判定)与编造案例文本守卫已删——语义质量判断
    交独立评审;此处只保留协议位(escalate 透传、context 契约)与证据
    结构(coverage / source_ref)判断。误弃答可接受,误断言不可接受。
    """
    # escalate_rd/escalate_fae 是一等 outcome(D4):模型判定"只能由研发内部
    # 确认"或"需要人工 FAE 介入"时原样透传,不降级也不提升。
    if declared in ("escalate_rd", "escalate_fae"):
        return declared
    if (
        getattr(plan, "context_required", False)
        or "requires_product_anchor_for_model_specific_specs"
        in (getattr(plan, "answer_contract", []) or [])
    ):
        return "safe_abstained"
    if declared == "resolved":
        return "resolved"
    if declared != "safe_abstained":
        return "safe_abstained"
    if not sources:
        return "safe_abstained"

    required_caps = [
        cap
        for cap in [plan.primary_capability, *plan.extra_capabilities]
        if cap != "risk_compliance"
    ]
    if not required_caps:
        return "safe_abstained"
    supported_caps = [
        cap for cap in required_caps
        if (ev := evidences.get(cap)) is not None
        and ev.coverage != "empty"
        and any(f.source_ref for f in ev.facts)
    ]
    if not supported_caps:
        return "safe_abstained"

    primary_ev = evidences.get(plan.primary_capability)
    if plan.primary_capability == "risk_compliance":
        primary_ev = next((evidences.get(cap) for cap in required_caps if evidences.get(cap)), None)
    if (
        primary_ev is not None
        and primary_ev.coverage != "empty"
        and any(f.source_ref for f in primary_ev.facts)
    ):
        return "resolved"

    # Planner primary can be over-specific (for example spec) while the answer is
    # actually supported by selection + experience evidence. Require at least two
    # non-risk evidence modules so one weak auxiliary hit cannot turn an unknown
    # primary into a resolved answer.
    if len(supported_caps) >= 2:
        return "resolved"
    return "safe_abstained"


def _synthesize_context_only_answer(
    user_message: str,
    plan: PlanResult,
    channel: str,
    *,
    consultation_context: str,
    llm_provider: str,
    llm_api_key: str,
    llm_base_url: str,
    model_main: str,
) -> str:
    """Use prior consultation state when evidence is empty but the dialog has useful context."""
    if not consultation_context.strip():
        return ""

    from src.agent.llm_client import complete

    prompt = _build_context_only_prompt(
        user_message,
        plan,
        channel,
        consultation_context=consultation_context,
    )
    try:
        text = complete(
            provider=llm_provider,
            api_key=llm_api_key,
            base_url=llm_base_url,
            model=model_main,
            system=_CTX_SYS,
            user=prompt,
            max_tokens=3072,
            temperature=0.2,
        )
    except Exception:
        return ""
    return clean_answer_text(text)


def _rewrite_incomplete_answer(
    user_message: str,
    plan: PlanResult,
    channel: str,
    evidences: dict[str, Evidence],
    *,
    consultation_context: str,
    llm_provider: str,
    llm_api_key: str,
    llm_base_url: str,
    model_main: str,
) -> str:
    """Repair a structurally incomplete synthesized answer with text-mode LLM.

    This is not a business fallback. It reuses the same evidence prompt in a
    non-JSON format so the model can complete the answer body without fighting
    JSON escaping and token overhead.
    """
    from src.agent.llm_client import complete

    compact_prompt = _build_compact_prompt(
        user_message,
        plan,
        channel,
        evidences,
        consultation_context=consultation_context,
    )
    try:
        text = complete(
            provider=llm_provider,
            api_key=llm_api_key,
            base_url=llm_base_url,
            model=model_main,
            system=_SYS,
            user=compact_prompt,
            max_tokens=3072,
            temperature=0.0,
        )
    except Exception:
        text = ""
    text = clean_answer_text(text)
    text = _defang_unsafe_executable_code_blocks(text)
    if text and not _structurally_broken(text):
        return text
    return ""


def _rewrite_low_quality_answer(
    user_message: str,
    plan: PlanResult,
    channel: str,
    evidences: dict[str, Evidence],
    *,
    candidate_text: str,
    consultation_context: str,
    llm_provider: str,
    llm_api_key: str,
    llm_base_url: str,
    model_main: str,
) -> str:
    """Rewrite raw evidence dumps into a concise FAE answer using the same evidence."""
    from src.agent.llm_client import complete

    prompt = _build_quality_rewrite_prompt(
        user_message,
        plan,
        channel,
        evidences,
        candidate_text=candidate_text,
        consultation_context=consultation_context,
    )
    try:
        text = complete(
            provider=llm_provider,
            api_key=llm_api_key,
            base_url=llm_base_url,
            model=model_main,
            system=_REWRITE_SYS,
            user=prompt,
            max_tokens=3072,
            temperature=0.1,
        )
    except Exception:
        return ""
    text = clean_answer_text(text)
    text = _defang_unsafe_executable_code_blocks(text)
    if text and not _structurally_broken(text):
        return text
    return ""


def _build_context_only_prompt(
    user_message: str,
    plan: PlanResult,
    channel: str,
    *,
    consultation_context: str,
) -> str:
    prompt = f"""# 上下文咨询综合

当前用户问题:
{user_message}

计划能力:
- primary: {plan.primary_capability}
- extras: {", ".join(plan.extra_capabilities) if plan.extra_capabilities else "无"}
- channel: {channel}

证据状态:
- 本轮能力检索没有返回可用 Fact。
- 不要编造规格书没有支持的精确数字、型号结论或认证结论。
- 可以基于已知咨询上下文给出工程判断、风险排序、验证路径和下一步需要确认的信息。

回答要求:
- 承接上下文,不要重复追问已经明确的信息。
- 直接回答当前追问,说明现有条件意味着什么。
- 如果不能锁定型号,给出合理候选方向和验证路径,不要只说"资料不足"。
- 正文不要输出 source_ref、Knowledge/ 路径、Knowledge_SDK 编号、FAQ/GAP/FAE 编号或行号。
- 使用自然的 FAE 口吻,结构清晰,但不要机械套模板。
"""
    return with_fae_answer_quality(prompt, consultation_context)


def _render_needs_clarify_answer(user_message: str, plan: PlanResult) -> str:
    """Render an explicit safe-abstain answer for missing context.

    This is a protocol-level answer, not a sample-specific fallback. It keeps
    domain questions useful while avoiding over-resolved claims when the target
    object, model, image, or prior requirement is unavailable.
    """
    questions = [q.strip() for q in (plan.clarify_questions or []) if str(q).strip()]
    if not questions:
        questions = ["请补充具体型号、场景或上一轮需求。"]
    question_type = getattr(plan, "question_type", "unknown")
    context_required = bool(getattr(plan, "context_required", False))
    answer_contract = getattr(plan, "answer_contract", []) or []

    if "requires_product_anchor_for_model_specific_specs" in answer_contract:
        conclusion = (
            "这是产品材料/结构参数问题,不同型号不能混用;当前缺少具体型号,我不能把相近产品资料当成结论。"
        )
    elif context_required:
        conclusion = (
            "这句话依赖上文对象、图片或“这个/这种/那款”的具体指代；"
            "当前上下文不足,我不能直接锁定型号、接口或结论。"
        )
    elif question_type in {"selection", "integration_design"}:
        conclusion = "这是域内技术问题,但缺少关键约束,现在只能给选型/集成判断框架,不能直接定型号。"
    elif question_type == "sdk_howto":
        conclusion = "这是 SDK/平台类问题,但缺少具体型号、平台或 SDK 版本,不能直接给确定支持结论。"
    else:
        conclusion = "这个问题还缺关键对象或条件,我不能把不完整信息写成确定结论。"

    capability_text = _clarification_capability_text(question_type)
    question_lines = "\n".join(f"- {q}" for q in questions[:2])
    return (
        "**结论**\n"
        f"{conclusion}\n\n"
        "**我能先判断**\n"
        f"- {capability_text}\n"
        "- 缺少对象或约束时,我可以给判断边界和验证路径,但不会把缺证据当成支持或不支持。\n\n"
        "**需要补充**\n"
        f"{question_lines}\n\n"
        "**验证 / 下一步**\n"
        "- 补充后我会按规格、SDK/接口、场景选型、风险边界这些能力一起取证,再给可执行建议。"
    )


def _clarification_capability_text(question_type: str) -> str:
    if question_type == "selection":
        return "如果是选型问题,需要把场景、距离、精度、FOV、接口、平台或环境光照等硬约束补齐。"
    if question_type == "integration_design":
        return "如果是集成问题,需要明确设备型号、接口位置、系统平台和希望验证的能力边界。"
    if question_type == "sdk_howto":
        return "如果是 SDK/平台支持问题,需要明确产品型号、操作系统、语言/SDK 版本和目标功能。"
    if question_type == "field_troubleshooting":
        return "如果是现场排障问题,需要明确型号、现象、复现条件、原始 depth/点云和版本信息。"
    return "如果是规格或型号问题,需要先明确被指代的产品、图片位置或上一轮需求。"


def _build_prompt(
    user_message: str,
    plan: PlanResult,
    channel: str,
    evidences: dict[str, Evidence],
    *,
    consultation_context: str = "",
) -> str:
    """Build the evidence_synth.md prompt, filling in all placeholders."""
    template = _PROMPT_PATH.read_text(encoding="utf-8")

    evidences_block = _format_evidences_block(evidences)

    # Use str.replace() to avoid KeyError from JSON {..} examples in the template
    result = template
    result = result.replace("{USER_QUERY}", user_message)
    result = result.replace("{PRIMARY_CAPABILITY}", plan.primary_capability)
    result = result.replace("{EXTRA_CAPABILITIES}", ", ".join(plan.extra_capabilities) if plan.extra_capabilities else "无")
    result = result.replace("{NEEDS_CLARIFY}", str(plan.needs_clarify))
    result = result.replace("{CLARIFY_QUESTIONS}", "; ".join(plan.clarify_questions) if plan.clarify_questions else "无")
    result = result.replace("{QUESTION_TYPE}", getattr(plan, "question_type", "unknown"))
    result = result.replace("{CONTEXT_REQUIRED}", str(getattr(plan, "context_required", False)))
    result = result.replace(
        "{EVIDENCE_REQUIREMENTS}",
        ", ".join(getattr(plan, "evidence_requirements", []) or ["无"]),
    )
    result = result.replace(
        "{ANSWER_CONTRACT}",
        ", ".join(getattr(plan, "answer_contract", []) or ["无"]),
    )
    result = result.replace("{CHANNEL}", channel)
    result = result.replace("{EVIDENCES_BLOCK}", evidences_block)
    result = with_fae_answer_quality(result, consultation_context)
    return result


def _build_compact_prompt(
    user_message: str,
    plan: PlanResult,
    channel: str,
    evidences: dict[str, Evidence],
    *,
    consultation_context: str = "",
) -> str:
    """Build a shorter synthesis prompt for JSON-repair retries."""
    facts_block = _format_compact_evidences_block(evidences)
    sdk_gate_block = _format_sdk_gate_block(evidences)
    sdk_gate_section = f"\nSDK 代码证据 Gate:\n{sdk_gate_block}\n" if sdk_gate_block else ""
    prompt = f"""# Evidence Synthesizer 压缩证据重试

用户问题:
{user_message}

主 capability: {plan.primary_capability}
附加 capability: {", ".join(plan.extra_capabilities) if plan.extra_capabilities else "无"}
问题类型: {getattr(plan, "question_type", "unknown")}
证据要求: {", ".join(getattr(plan, "evidence_requirements", []) or ["无"])}
答复协议: {", ".join(getattr(plan, "answer_contract", []) or ["无"])}
channel: {channel}
{sdk_gate_section}

压缩证据:
{facts_block}

要求:
- 只依据带 source_ref 的事实回答。
- 正文不要输出 source_ref、Knowledge/ 路径、Knowledge_SDK 编号、FAQ/GAP/FAE 编号或行号。
- 不要编造 Fact 中没有的数字、阈值、像素误差、距离、帧率、频率、功耗或版本。
- 即使是简单参数,也要给"结论 / 依据 / 适用条件或风险 / 验证建议或下一步",不要只报数字。
- 对比 / 取舍型问题必须先给工程取舍结论,不能用"暂无直接对比证据"回避;再说明双方优劣、适用边界和验证方式。
- SDK / API / 命令类 how-to 问题必须给最小代码块或命令片段;证据不足以完整编译时,标成最小骨架,不要编造参数。
- 代码块只能放可执行代码/命令或合法注释;如果证据不足以保证可执行,改用"最小骨架 / 伪代码方向"的文字列表,不要伪装成可执行代码块。
- 直接输出用户可见的 markdown 正文,不要 JSON,不要解释自己在重试。
- 有足够事实就直接给排查/操作结论;必要事实缺失才明说缺失。
"""
    return with_fae_answer_quality(prompt, consultation_context)


def _build_quality_rewrite_prompt(
    user_message: str,
    plan: PlanResult,
    channel: str,
    evidences: dict[str, Evidence],
    *,
    candidate_text: str,
    consultation_context: str = "",
) -> str:
    facts_block = _format_compact_evidences_block(evidences)
    sdk_gate_block = _format_sdk_gate_block(evidences)
    sdk_gate_section = f"\nSDK 代码证据 Gate:\n{sdk_gate_block}\n" if sdk_gate_block else ""
    prompt = f"""# FAE 答复质量改写

用户问题:
{user_message}

主 capability: {plan.primary_capability}
附加 capability: {", ".join(plan.extra_capabilities) if plan.extra_capabilities else "无"}
问题类型: {getattr(plan, "question_type", "unknown")}
证据要求: {", ".join(getattr(plan, "evidence_requirements", []) or ["无"])}
答复协议: {", ".join(getattr(plan, "answer_contract", []) or ["无"])}
channel: {channel}
{sdk_gate_section}

可用证据:
{facts_block}

当前候选答复存在问题,不要沿用其表达方式:
{candidate_text}

改写要求:
- 只依据"可用证据"中带 source_ref 的事实,不要编造事实、数字、阈值或承诺。
- 第一段必须直接回答用户当前问题,不要先说"基于已检索到的证据"。
- 不要逐条复述 evidence,不要输出"关键符号"、"常用命令"这类资料索引式字段。
- 对比 / 取舍型问题必须先给工程取舍结论,不能用"暂无直接对比证据"回避;再说明双方优劣、适用边界和验证方式。
- SDK / API / 命令类 how-to 问题必须给最小代码块或命令片段;证据不足以完整编译时,标成最小骨架,不要编造参数。
- 代码块只能放可执行代码/命令或合法注释;如果证据不足以保证可执行,改用"最小骨架 / 伪代码方向"的文字列表,不要伪装成可执行代码块。
- SDK / 排障 / 现场问题要给可执行判断路径:怎么看、查哪个配置或 API、结果分别意味着什么。
- 使用 `**结论**`、`**依据**`、`**验证 / 下一步**` 等短标题,适合钉钉阅读。
- 正文不要输出 source_ref、Knowledge/ 路径、Knowledge_SDK 编号、FAQ/GAP/FAE 编号或行号。
- 直接输出用户可见 markdown 正文,不要 JSON,不要解释自己在改写。
"""
    return with_fae_answer_quality(prompt, consultation_context)


def _format_compact_evidences_block(evidences: dict[str, Evidence]) -> str:
    """紧凑证据块(文本重试/质量改写路径)——与主路径同一选择逻辑。

    M5 核销(20260709):随事实推送三件套退役,不再做段落配额与矩阵行
    保底区分,全量注入带溯源事实。
    """
    parts: list[str] = []
    for cap, ev in evidences.items():
        parts.append(f"## {cap} coverage={ev.coverage} confidence={ev.confidence}")
        selected = _select_prompt_facts(ev)
        for fact in selected:
            parts.append(f"- {fact.statement} [{fact.source_ref}]")
        if not selected:
            parts.append("- 无 Fact")
    return "\n".join(parts)


def _select_public_sources(
    evidences: dict[str, Evidence],
    *,
    max_sources: int = _MAX_PUBLIC_SOURCES,
) -> list[str]:
    """Pick a compact source set for user-facing answer metadata.

    The full evidence remains in traces and prompts. The public `sources` event
    should stay readable: keep at least one representative source from each
    participating capability, then fill by capability order.
    """
    if max_sources <= 0:
        return []

    ranked_by_cap: list[list[str]] = []
    for ev in evidences.values():
        refs: list[str] = []
        for fact in ev.facts:
            if fact.source_ref and fact.source_ref not in refs:
                refs.append(fact.source_ref)
        if refs:
            ranked_by_cap.append(refs)

    selected: list[str] = []
    seen: set[str] = set()

    def add(ref: str) -> None:
        if len(selected) >= max_sources or ref in seen:
            return
        seen.add(ref)
        selected.append(ref)

    for refs in ranked_by_cap:
        add(refs[0])

    max_len = max((len(refs) for refs in ranked_by_cap), default=0)
    for idx in range(1, max_len):
        for refs in ranked_by_cap:
            if idx < len(refs):
                add(refs[idx])
            if len(selected) >= max_sources:
                return selected

    return selected


def _structurally_broken(text: str) -> bool:
    """结构完整性门禁——三个保留的结构检查的组合,不含语义判断。

    M5 退役(20260709):原 _looks_incomplete_answer 的语义部分(段落模板
    形态、结尾连接词/标点猜测)已删,语义质量判断交独立评审。
    """
    stripped = text.rstrip()
    if not stripped:
        return True
    return (
        _has_truncated_short_tail(stripped)
        or _has_empty_required_section(stripped)
        or _has_broken_numbered_steps(stripped)
    )


def _has_truncated_short_tail(text: str) -> bool:
    """Detect clipped short answers from streaming/clarification paths."""
    if len(text) >= 90:
        return False
    if not _CJK_RE.search(text):
        return False
    if text[-1] in {"。", "？", "?", "！", "!", ")", "）"}:
        return False
    tail = re.split(r"[。！？?]", text)[-1].strip()
    if 0 < len(tail) <= 4 and _CJK_RE.search(tail):
        return True
    return len(text) < 70


def _answer_contract_violation(
    text: str,
    plan: PlanResult,
    evidences: dict[str, Evidence],
) -> str | None:
    """答复协议红线检查(仅代码红线)。

    M5 退役(20260709):negative_claim / question_only / overstated_root_cause
    三个语义协议分支已删——协议落实交 prompt 与独立评审;此处只保留
    "伪装可执行代码"两条红线(用户可能复制假脚本,结构可判定)。
    """
    contract = set(getattr(plan, "answer_contract", []) or [])
    planned_capabilities = {plan.primary_capability, *list(plan.extra_capabilities)}
    if _has_disallowed_sdk_programming_code_block(text, evidences):
        return "sdk_executable_code_disallowed_by_gate"
    if (
        (
            "mark_code_as_executable_or_pseudocode" in contract
            or "sdk" in planned_capabilities
            or getattr(plan, "question_type", "unknown") == "sdk_howto"
        )
        and _has_unsafe_executable_code_block(text)
    ):
        return "unsafe_executable_code_block"
    return None


def _preserve_requested_sdk_terms(
    text: str,
    user_message: str,
    evidences: dict[str, Evidence],
) -> str:
    """Keep user-requested SDK layer terms when they are present in evidence.

    Some model outputs correctly name the repo but omit the layer term the user
    asked about, e.g. "ROS2 wrapper". That is a lossy answer for SDK entry-point
    questions. This function only re-inserts terms already present in SDK facts.
    """
    if not text:
        return text
    query = (user_message or "").lower()
    requested_wrapper = "wrapper" in query or "封装" in (user_message or "")
    if not requested_wrapper:
        return text
    if "wrapper" in text.lower() or "封装" in text:
        return text

    term = _sdk_wrapper_term_from_evidence(user_message, evidences)
    if not term:
        return text

    repo_label_match = re.match(r"^(OrbbecSDK_[A-Za-z0-9_]+)\s+(.+Wrapper)$", term)
    if repo_label_match:
        repo, label = repo_label_match.groups()
        if repo in text:
            return text.replace(repo, f"{repo}（{label}）", 1)
    if term in text:
        return text
    return text.rstrip() + f"\n\n补充：这里的 wrapper/封装入口指 **{term}**。"


def _sdk_wrapper_term_from_evidence(
    user_message: str,
    evidences: dict[str, Evidence],
) -> str | None:
    sdk_evidence = evidences.get("sdk")
    if sdk_evidence is None:
        return None
    query = (user_message or "").lower()
    terms: list[str] = []
    patterns = (
        r"OrbbecSDK_[A-Za-z0-9_]+\s+(?:ROS2|ROS1|Python|DotNet|K4A|C#|C\+\+)\s+Wrapper",
        r"(?:ROS2|ROS1|Python|DotNet|K4A|C#|C\+\+)\s+Wrapper",
    )
    for fact in sdk_evidence.facts:
        statement = getattr(fact, "statement", "") or ""
        for pattern in patterns:
            for match in re.finditer(pattern, statement):
                term = match.group(0).strip()
                if term and term not in terms:
                    terms.append(term)
    if not terms:
        return None
    if "ros2" in query:
        for term in terms:
            if "ROS2" in term:
                return term
    return terms[0]


def _sanitize_answer_contract_violation(
    text: str,
    reason: str | None,
    evidences: dict[str, Evidence] | None = None,
) -> str:
    if reason == "sdk_executable_code_disallowed_by_gate" and evidences is not None:
        return _defang_disallowed_sdk_programming_code_blocks(text, evidences)
    if reason == "unsafe_executable_code_block":
        return _defang_unsafe_executable_code_blocks(text)
    return text


def _defang_unsafe_executable_code_blocks(text: str) -> str:
    """Convert unsafe executable fences into user-facing pseudo steps.

    SDK answers often need implementation direction, but a fenced `python` block
    that mixes prose and code is worse than no code: users may copy a fake script.
    This keeps the engineering guidance while making it explicitly non-executable.
    """
    if not text or "```" not in text:
        return text

    def replace(match: re.Match) -> str:
        lang = _normalize_code_lang(match.group(1))
        body = match.group(2)
        if lang not in _EXECUTABLE_CODE_LANGS or not _unsafe_code_block_body(body):
            return match.group(0)
        steps = _pseudo_steps_from_unsafe_code_body(body)
        if not steps:
            steps = ["按 SDK 官方示例确认实际 API 名称后,再把流程落成可执行脚本。"]
        bullets = "\n".join(f"- {step}" for step in steps)
        return f"**伪代码 / 操作步骤**\n{bullets}"

    return _CODE_FENCE_RE.sub(replace, text)


def _has_unsafe_executable_code_block(text: str) -> bool:
    """Reject code fences that mix executable languages with prose steps."""
    for match in _CODE_FENCE_RE.finditer(text):
        lang = _normalize_code_lang(match.group(1))
        if lang not in _EXECUTABLE_CODE_LANGS:
            continue
        if _unsafe_code_block_body(match.group(2)):
            return True
    return False


def _sdk_metadata(evidences: dict[str, Evidence]) -> dict:
    sdk_evidence = evidences.get("sdk")
    if sdk_evidence is None:
        return {}
    metadata = getattr(sdk_evidence, "metadata", {}) or {}
    if not isinstance(metadata, dict):
        return {}
    sdk_metadata = metadata.get("sdk") or {}
    return sdk_metadata if isinstance(sdk_metadata, dict) else {}


def _format_bool(value: object) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    return str(value)


def _format_sdk_gate_block(evidences: dict[str, Evidence]) -> str:
    sdk = _sdk_metadata(evidences)
    if not sdk:
        return ""
    gate = sdk.get("confidence_gate") or {}
    if not isinstance(gate, dict):
        return ""
    intent = sdk.get("sdk_intent") or {}
    if not isinstance(intent, dict):
        intent = {}
    repo_candidates = sdk.get("repo_candidates") or []
    if not isinstance(repo_candidates, list):
        repo_candidates = []
    code_hits = sdk.get("code_hits") or []
    if not isinstance(code_hits, list):
        code_hits = []

    executable = gate.get("executable_code_allowed")
    level = gate.get("code_evidence_level") or "unknown"
    record_confidence = gate.get("record_confidence") or []
    if isinstance(record_confidence, list):
        record_confidence_text = ",".join(str(item) for item in record_confidence)
    else:
        record_confidence_text = str(record_confidence)

    lines = [
        f"- sdk_layers={','.join(str(x) for x in (intent.get('sdk_layers') or [])) or 'unknown'}",
        f"- task_type={intent.get('task_type') or 'unknown'}",
        f"- repo_candidates={','.join(str(x) for x in repo_candidates) or 'unknown'}",
        f"- record_confidence={record_confidence_text or 'unknown'}",
        f"- code_evidence_level={level}",
        f"- executable_code_allowed={_format_bool(executable)}",
        f"- reason={gate.get('reason') or 'unknown'}",
        f"- code_hits={len(code_hits)}",
    ]
    if executable is True:
        lines.append(
            "- 约束:允许输出真实代码块,但只能使用 Knowledge_SDK_CODE/code_verified snippet 中出现的 API、参数和顺序;不要补未见参数。"
        )
    elif executable is False:
        lines.append(
            "- 约束:禁止输出 Python/C/C++/C#/Java/JavaScript 等真实代码块;只能输出命令、步骤、API 路径或明确标注的最小骨架 / 伪代码方向。Shell/CLI 命令块可在有命令证据时输出。"
        )
    else:
        lines.append(
            "- 约束:未获得可执行代码授权时,不要输出真实编程语言代码块。"
        )
    return "\n".join(lines)


def _sdk_gate_disallows_programming_code(evidences: dict[str, Evidence]) -> bool:
    sdk = _sdk_metadata(evidences)
    gate = sdk.get("confidence_gate") if sdk else None
    if not isinstance(gate, dict):
        return False
    return gate.get("executable_code_allowed") is False


def _sdk_gate_allows_programming_code(evidences: dict[str, Evidence]) -> bool:
    sdk = _sdk_metadata(evidences)
    gate = sdk.get("confidence_gate") if sdk else None
    if not isinstance(gate, dict):
        return False
    return gate.get("executable_code_allowed") is True


def _ensure_verified_sdk_code_snippet(text: str, evidences: dict[str, Evidence]) -> str:
    """Keep verified SDK snippets literal when the code gate allows execution.

    The LLM may summarize a `Knowledge_SDK_CODE` fact into pseudo steps. For
    code-example questions that is a correctness failure: the verified snippet
    is the contract source, and the answer should include it verbatim.
    """
    if not text or not _sdk_gate_allows_programming_code(evidences):
        return text

    snippet = _verified_sdk_code_snippet(evidences)
    if snippet is None:
        return text

    code = snippet["code"]
    if not code:
        return text
    if code in text and "伪代码" not in text:
        return text

    code_block = _render_verified_sdk_code_block(snippet)
    updated, code_replaced = _replace_first_sdk_programming_code_block(text, code_block)
    updated, pseudo_replaced = _replace_pseudo_sdk_section(
        updated,
        "" if code_replaced else code_block,
    )
    if code_replaced or pseudo_replaced:
        return updated
    if _answer_contains_verified_sdk_code(updated, snippet):
        return updated.replace("伪代码 / 操作步骤", "可复制代码片段").replace(
            "最小骨架 / 伪代码方向",
            "可复制代码片段",
        )
    return _insert_verified_sdk_code_block(updated, code_block)


def _verified_sdk_code_snippet(evidences: dict[str, Evidence]) -> dict[str, str] | None:
    sdk_evidence = evidences.get("sdk")
    if sdk_evidence is None:
        return None

    code_meta_by_ref = _sdk_code_hit_metadata_by_ref(evidences)
    for fact in sdk_evidence.facts:
        source_ref = getattr(fact, "source_ref", "") or ""
        if not source_ref.startswith("Knowledge_SDK_CODE:"):
            continue
        statement = getattr(fact, "statement", "") or ""
        meta = code_meta_by_ref.get(source_ref, {})
        metadata_code = str(meta.get("snippet") or "").strip()
        marker = "代码:\n"
        code = metadata_code
        if not code and marker in statement:
            code = statement.split(marker, 1)[1].strip()
        if not code:
            continue
        language = str(meta.get("language") or _language_from_code_fact_statement(statement) or "code")
        title = str(meta.get("title") or _title_from_code_fact_statement(statement) or "SDK verified snippet")
        return {
            "language": _markdown_code_language(language),
            "title": title.strip(),
            "code": code,
        }
    return None


def _sdk_code_hit_metadata_by_ref(evidences: dict[str, Evidence]) -> dict[str, dict]:
    sdk = _sdk_metadata(evidences)
    code_hits = sdk.get("code_hits") if sdk else None
    if not isinstance(code_hits, list):
        return {}
    result: dict[str, dict] = {}
    for hit in code_hits:
        if not isinstance(hit, dict):
            continue
        snippet_id = str(hit.get("snippet_id") or "")
        if not snippet_id:
            continue
        result[f"Knowledge_SDK_CODE:{snippet_id}"] = hit
    return result


def _language_from_code_fact_statement(statement: str) -> str:
    match = re.search(r"代码片段:\s*([^/\s]+)\s*/", statement)
    return match.group(1).strip() if match else ""


def _title_from_code_fact_statement(statement: str) -> str:
    match = re.search(r"代码片段:\s*[^/]+/\s*(.*?)\.\s*confidence=", statement, re.DOTALL)
    if not match:
        return ""
    return " ".join(match.group(1).split())


def _markdown_code_language(language: str) -> str:
    lang = _normalize_code_lang(language)
    aliases = {
        "c++": "cpp",
        "cc": "cpp",
        "h": "c",
        "hpp": "cpp",
        "py": "python",
        "cs": "csharp",
        "c#": "csharp",
    }
    return aliases.get(lang, lang or "text")


def _render_verified_sdk_code_block(snippet: dict[str, str]) -> str:
    title = snippet.get("title") or "SDK verified snippet"
    language = snippet.get("language") or "text"
    code = snippet.get("code") or ""
    return f"**可复制代码片段（{title}）**\n\n```{language}\n{code}\n```"


def _replace_pseudo_sdk_section(text: str, replacement: str) -> tuple[str, bool]:
    pattern = re.compile(
        r"(?ms)\n?\*\*(?:伪代码\s*/\s*操作步骤|最小骨架\s*/\s*伪代码方向)\*\*"
        r"\s*\n.*?(?=\n\*\*|$)"
    )
    block = ("\n\n" + replacement + "\n") if replacement else "\n"
    updated, count = pattern.subn(block, text, count=1)
    return updated.strip(), bool(count)


def _replace_first_sdk_programming_code_block(text: str, code_block: str) -> tuple[str, bool]:
    replaced = False

    def replace(match: re.Match) -> str:
        nonlocal replaced
        if replaced:
            return match.group(0)
        if _normalize_code_lang(match.group(1)) not in _SDK_PROGRAMMING_CODE_LANGS:
            return match.group(0)
        replaced = True
        return code_block

    updated = _CODE_FENCE_RE.sub(replace, text or "")
    return updated.strip(), replaced


def _answer_contains_verified_sdk_code(text: str, snippet: dict[str, str]) -> bool:
    code = snippet.get("code") or ""
    if not code:
        return False
    key_lines = [
        line.strip()
        for line in code.splitlines()
        if line.strip() and not line.strip().startswith(("#include", "{", "}"))
    ][:4]
    if not key_lines:
        return code in text
    return all(line in text for line in key_lines[:2])


def _insert_verified_sdk_code_block(text: str, code_block: str) -> str:
    match = re.search(
        r"(?m)^\s*\*\*(?:每一步作用|注意|适用条件|验证\s*/\s*下一步|验证|下一步)",
        text,
    )
    if match:
        return (text[:match.start()].rstrip() + "\n\n" + code_block + "\n\n" + text[match.start():].lstrip()).strip()
    return (text.rstrip() + "\n\n" + code_block).strip()


def _has_disallowed_sdk_programming_code_block(
    text: str,
    evidences: dict[str, Evidence],
) -> bool:
    if not _sdk_gate_disallows_programming_code(evidences):
        return False
    for match in _CODE_FENCE_RE.finditer(text or ""):
        if _normalize_code_lang(match.group(1)) in _SDK_PROGRAMMING_CODE_LANGS:
            return True
    return False


def _defang_disallowed_sdk_programming_code_blocks(
    text: str,
    evidences: dict[str, Evidence],
) -> str:
    if not _sdk_gate_disallows_programming_code(evidences) or "```" not in (text or ""):
        return text

    replacement = (
        "**最小骨架 / 伪代码方向**\n"
        "- 当前 SDK 证据没有命中 code_verified snippet,不能给可复制代码块。\n"
        "- 请按上面的 API 路径和官方示例确认实际类名、参数和版本后再实现。"
    )

    def replace(match: re.Match) -> str:
        lang = _normalize_code_lang(match.group(1))
        if lang not in _SDK_PROGRAMMING_CODE_LANGS:
            return match.group(0)
        return replacement

    return _CODE_FENCE_RE.sub(replace, text)


def _unsafe_code_block_body(body: str) -> bool:
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.match(r"^\d+[.)、]\s+", line):
            return True
        if _CJK_RE.search(line) and not _is_code_comment_line(line):
            return True
    return False


def _pseudo_steps_from_unsafe_code_body(body: str) -> list[str]:
    steps: list[str] = []
    seen: set[str] = set()
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"^\d+[.)、]\s+", "", line).strip()
        line = _strip_code_comment_prefix(line).strip()
        if not line or not _CJK_RE.search(line):
            continue
        if line in seen:
            continue
        seen.add(line)
        steps.append(line)
    return steps


def _strip_code_comment_prefix(line: str) -> str:
    for prefix in ("#", "//", "--", "*"):
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    if line.startswith("/*"):
        return line[2:].strip()
    if line.startswith("<!--"):
        return line[4:].strip()
    return line


def _normalize_code_lang(raw: str) -> str:
    parts = (raw or "").strip().lower().split(maxsplit=1)
    if not parts:
        return ""
    lang = parts[0]
    aliases = {
        "csharp": "c#",
        "shell-session": "shell",
        "console": "shell",
        "terminal": "shell",
        "cmd": "shell",
    }
    return aliases.get(lang, lang)


def _is_code_comment_line(line: str) -> bool:
    return line.startswith(("#", "//", "/*", "*", "--", "<!--"))


def _has_empty_required_section(text: str) -> bool:
    matches = list(_SECTION_HEADING_RE.finditer(text))
    if not matches:
        return False
    for idx, match in enumerate(matches):
        next_start = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        body = text[match.end():next_start].strip()
        if not body:
            return True
    return False


def _has_broken_numbered_steps(text: str) -> bool:
    numbers = [int(m.group(1)) for m in _NUMBERED_STEP_RE.finditer(text)]
    if len(numbers) < 2:
        return False
    for prev, cur in zip(numbers, numbers[1:]):
        if cur > prev + 1:
            return True
    return False


def _select_prompt_facts(ev: Evidence) -> list:
    """进 prompt 的事实 = 全部带 source_ref 的事实。

    M5 核销(20260709):事实推送三件套(top-N 配额 + 相关性排序 +
    verified 矩阵行保底)按登记删除条件退役——循环骨架经 fact_lookup
    按需拉取;pipeline 显式模式不再做推送侧筛选,全量注入带溯源事实。
    """
    return [fact for fact in ev.facts if fact.source_ref]


def prompt_fact_refs(evidences: dict[str, Evidence]) -> dict[str, list[str]]:
    """每能力最终进入 prompt 的事实 source_ref 清单——trace 可见性
    (20260707 教训:xlsx-430 三轮定位慢,因为看不到'证据是否进了 prompt')。"""
    return {
        cap: [f.source_ref for f in _select_prompt_facts(ev)]
        for cap, ev in evidences.items()
    }


def _format_evidences_block(evidences: dict[str, Evidence]) -> str:
    """Format all evidences into the EVIDENCES_BLOCK for the prompt."""
    parts: list[str] = []
    for cap, ev in evidences.items():
        header = f"### {cap} (coverage={ev.coverage}, confidence={ev.confidence})"
        preface = ""
        if cap == "sdk":
            sdk_gate_block = _format_sdk_gate_block({cap: ev})
            if sdk_gate_block:
                preface = f"#### SDK 代码证据 Gate\n{sdk_gate_block}\n\n"
        prompt_facts = _select_prompt_facts(ev)
        if prompt_facts:
            fact_lines = "\n".join(
                f"- {f.statement} [{f.source_ref}]" for f in prompt_facts
            )
            parts.append(f"{header}\n\n{preface}{fact_lines}")
        else:
            parts.append(f"{header}\n\n{preface}_(无 Fact)_")
    return "\n\n".join(parts)
