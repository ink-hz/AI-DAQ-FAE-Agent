"""Troubleshoot capability — QA retrieval for field debugging questions."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from src.agent.capabilities import register
from src.agent.capabilities.capability_sdk import _record_to_fact, _score_sdk_record
from src.agent.evidence import Evidence, Fact
from src.agent.planner import Capability
from src.agent.qa_search import qa_search

_TOP_K = 8
_FACT_MAX_CHARS = 240
_SDK_TOP_K = 5
_SDK_TROUBLE_TOPICS = {
    "troubleshooting",
    "performance",
    "frame_drop",
    "timeout",
    "empty_frame",
    "multi_camera",
    "sync",
    "usbfs",
    "diagnostics",
    "reset_reconnect",
    "d2c",
}


def _is_troubleshoot_hit(hit) -> bool:
    metadata = hit.metadata or {}
    answer_type = str(metadata.get("answer_type") or "")
    doc_type = str(metadata.get("doc_type") or "")
    if answer_type == "排查型":
        return True
    if doc_type == "troubleshooting":
        return True
    return False


def _fact_from_hit(hit) -> Fact:
    title = (hit.title or "").strip()
    snippet = (hit.snippet or "").strip()
    statement = f"{title}: {snippet}" if title else snippet
    statement = statement[:_FACT_MAX_CHARS].strip()
    source_ref = hit.kb_id or title or "qa"
    return Fact(statement=statement, source_ref=source_ref, confidence=float(hit.score))


def _is_sdk_troubleshoot_record(record: dict) -> bool:
    doc_type = str(record.get("doc_type") or "")
    if doc_type == "troubleshooting":
        return True
    topics = {str(t) for t in (record.get("topics") or [])}
    return bool(topics & _SDK_TROUBLE_TOPICS)


@dataclass
class CapabilityTroubleshoot:
    name: Capability = "troubleshoot"

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
        **kwargs,
    ) -> Evidence:
        t0 = perf_counter()
        sdk_knowledge_dir = kwargs.get("sdk_knowledge_dir")
        qa_enabled = kwargs.get("qa_enabled", True) is not False
        if not qa_enabled:
            sdk_evidence = self._run_sdk_troubleshooting(query, sdk_knowledge_dir, t0)
            if sdk_evidence is not None:
                sdk_evidence.notes = ["QA KB 已关闭,跳过 QA 排障检索"] + sdk_evidence.notes
                return sdk_evidence
            return Evidence(
                source_module="troubleshoot",
                facts=[],
                confidence="low",
                coverage="empty",
                notes=["QA KB 已关闭,在线 troubleshoot QA 检索已跳过"],
                latency_ms=int((perf_counter() - t0) * 1000),
            )
        if qa_collection is None:
            sdk_evidence = self._run_sdk_troubleshooting(query, sdk_knowledge_dir, t0)
            if sdk_evidence is not None:
                return sdk_evidence
            return Evidence(
                source_module="troubleshoot",
                facts=[],
                confidence="low",
                coverage="empty",
                notes=["缺 QA collection,无法执行 troubleshoot 检索"],
                latency_ms=int((perf_counter() - t0) * 1000),
            )
        if not api_key or not embed_model:
            sdk_evidence = self._run_sdk_troubleshooting(query, sdk_knowledge_dir, t0)
            if sdk_evidence is not None:
                return sdk_evidence
            return Evidence(
                source_module="troubleshoot",
                facts=[],
                confidence="low",
                coverage="empty",
                notes=["缺 embedding 配置,无法执行 troubleshoot 检索"],
                latency_ms=int((perf_counter() - t0) * 1000),
            )

        try:
            hits = qa_search(
                schema=schema,
                user_query=query,
                collection=qa_collection,
                api_key=api_key,
                embed_model=embed_model,
                top_k=_TOP_K,
            )
        except Exception as exc:
            return Evidence(
                source_module="troubleshoot",
                facts=[],
                confidence="low",
                coverage="empty",
                notes=[f"troubleshoot 检索失败: {exc}"],
                latency_ms=int((perf_counter() - t0) * 1000),
                error=str(exc),
            )

        relevant = [hit for hit in hits if _is_troubleshoot_hit(hit)]
        facts = [_fact_from_hit(hit) for hit in relevant]
        notes: list[str] = []
        if not facts:
            if hits:
                notes.append("QA 命中存在,但未命中排查型 answer_type/doc_type")
            else:
                notes.append("QA 未命中排查记录")
            sdk_evidence = self._run_sdk_troubleshooting(query, sdk_knowledge_dir, t0)
            if sdk_evidence is not None:
                sdk_evidence.notes = notes + sdk_evidence.notes
                return sdk_evidence

        return Evidence(
            source_module="troubleshoot",
            facts=facts,
            confidence="high" if facts else "low",
            coverage="full" if facts else "empty",
            notes=notes,
            latency_ms=int((perf_counter() - t0) * 1000),
        )

    def _run_sdk_troubleshooting(
        self,
        query: str,
        sdk_knowledge_dir,
        t0: float,
    ) -> Evidence | None:
        if not sdk_knowledge_dir:
            return None
        jsonl_path = Path(sdk_knowledge_dir) / "SDK_Knowledge.jsonl"
        if not jsonl_path.exists():
            return None
        try:
            records = [
                json.loads(line)
                for line in jsonl_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except Exception as exc:
            return Evidence(
                source_module="troubleshoot",
                facts=[],
                confidence="low",
                coverage="empty",
                notes=[f"Knowledge_SDK troubleshoot 读取失败: {exc}"],
                latency_ms=int((perf_counter() - t0) * 1000),
                error=str(exc),
            )

        scored = [
            (_score_sdk_record(query, record), i, record)
            for i, record in enumerate(records)
            if record.get("source_refs") and _is_sdk_troubleshoot_record(record)
        ]
        hits = [
            record
            for score, _i, record in sorted(scored, key=lambda item: (-item[0], item[1]))
            if score > 0
        ][:_SDK_TOP_K]
        if not hits:
            return None

        return Evidence(
            source_module="troubleshoot",
            facts=[_record_to_fact(record) for record in hits],
            confidence="high",
            coverage="full",
            notes=[f"Knowledge_SDK troubleshoot 命中 {len(hits)} 条"],
            latency_ms=int((perf_counter() - t0) * 1000),
        )


register(CapabilityTroubleshoot())
