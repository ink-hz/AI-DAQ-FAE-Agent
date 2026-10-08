"""Local Dev API for an independent DAQ FAE with no published knowledge."""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
import httpx
from pydantic import BaseModel, Field

from src.agent.loop.adapters import AnthropicAdapter
from src.agent.loop.runtime import LoopRuntime

from daq_fae.empty_knowledge import EmptyKnowledgeToolBox
from daq_fae.offline_adapter import OfflineAdapter


AGENT_ID = "ai-daq-fae-agent"
KNOWLEDGE_RELEASE = "empty-dev-v0"
RUNTIME_RELEASE = "fae-a6234f6"
_ROOT = Path(__file__).resolve().parent.parent
_EMPTY_ANSWER = (
    "当前数采知识库还没有已审核的产品资料，因此无法确认这个问题的具体结论。"
    "请提供设备型号、硬件或软件版本，以及可审核的资料后再核查。"
)
_UNGROUNDED_ANSWER = (
    "本次模型在空知识库下提交了无证据的确定结论，答案已拦截。"
    "请检查终稿协议与证据门。"
)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1)


def _anthropic_dev_adapter() -> AnthropicAdapter:
    api_key = os.getenv("DAQ_ANTHROPIC_API_KEY", "")
    auth_token = os.getenv("DAQ_ANTHROPIC_AUTH_TOKEN", "")
    if not (api_key or auth_token):
        raise ValueError("anthropic mode requires DAQ_ANTHROPIC_API_KEY or DAQ_ANTHROPIC_AUTH_TOKEN")
    return AnthropicAdapter(
        api_key=api_key,
        auth_token=auth_token,
        base_url=os.getenv("DAQ_ANTHROPIC_BASE_URL", ""),
        model=os.getenv("DAQ_ANTHROPIC_MODEL", "claude-opus-5-5"),
        thinking_mode="adaptive",
        effort="high",
        tool_choice_strategy="submit_only_auto",
    )


def _sse(event: dict) -> str:
    return "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"


def create_app(*, provider_mode: str | None = None, adapter=None,
               knowledge_dir: Path | None = None) -> FastAPI:
    knowledge_dir = knowledge_dir or _ROOT / "knowledge"
    if not knowledge_dir.is_dir():
        raise ValueError("empty knowledge directory is missing")
    if any(path.is_file() and path.name not in {".gitkeep", "README.md"}
           for path in knowledge_dir.rglob("*")):
        raise ValueError("empty knowledge bootstrap cannot load unreviewed knowledge files")

    mode = provider_mode or os.getenv("DAQ_PROVIDER_MODE", "offline")
    if mode not in {"offline", "anthropic"}:
        raise ValueError("DAQ_PROVIDER_MODE must be offline or anthropic")
    if adapter is None:
        adapter = OfflineAdapter() if mode == "offline" else _anthropic_dev_adapter()

    app = FastAPI(title="AI DAQ FAE Agent Dev Bootstrap")

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "environment": "development",
            "agent_id": AGENT_ID,
            "knowledge_release": KNOWLEDGE_RELEASE,
            "runtime_release": RUNTIME_RELEASE,
            "provider_mode": mode,
        }

    @app.post("/chat")
    def chat(request: ChatRequest):
        trace_id = uuid4().hex

        def stream():
            runtime = LoopRuntime(
                adapter=adapter,
                toolbox=EmptyKnowledgeToolBox(),
                system_prompt_path=_ROOT / "prompts" / "empty_knowledge_system.md",
            )
            try:
                events = list(runtime.run(request.question))
                done = next(event for event in reversed(events)
                            if event.get("type") == "done")
                progress = [event for event in events if event.get("type") == "tool_call"]
            except httpx.HTTPStatusError as exc:
                progress = []
                status_code = exc.response.status_code
                outcome = (
                    "provider_configuration_error" if status_code in {400, 401, 403, 404, 422}
                    else "provider_rate_limited" if status_code == 429
                    else "provider_unavailable" if status_code >= 500
                    else "provider_error"
                )
                done = {
                    "type": "done",
                    "answer": "本次数采 Dev 模型请求失败，未生成答案。请检查网关状态与配置。",
                    "outcome": outcome,
                    "sources": [],
                    "tool_calls": [],
                    "provider_status_code": status_code,
                    "fallback_used": True,
                    "fallback_reason": f"provider_http_{status_code}",
                }
            except httpx.TransportError as exc:
                progress = []
                done = {
                    "type": "done",
                    "answer": "本次数采 Dev 模型连接失败，未生成答案。请检查网关连接。",
                    "outcome": "provider_unavailable",
                    "sources": [],
                    "tool_calls": [],
                    "error_type": type(exc).__name__,
                    "fallback_used": True,
                    "fallback_reason": "provider_transport_error",
                }
            except Exception as exc:
                progress = []
                done = {
                    "type": "done",
                    "answer": "本次数采 Dev 服务运行失败，未生成答案。请检查服务日志。",
                    "outcome": "internal_error",
                    "sources": [],
                    "tool_calls": [],
                    "error_type": type(exc).__name__,
                    "fallback_used": True,
                    "fallback_reason": "runtime_error",
                }

            if done["outcome"] in {"resolved", "escalate_rd", "escalate_fae"}:
                original_outcome = done["outcome"]
                done.update({
                    "answer": _UNGROUNDED_ANSWER,
                    "outcome": "invalid_answer_contract",
                    "sources": [],
                    "fallback_used": True,
                    "fallback_reason": (
                        "empty_knowledge_ungrounded_resolution"
                        if original_outcome == "resolved"
                        else "empty_knowledge_unsupported_outcome"
                    ),
                })
            elif done["outcome"] == "safe_abstained":
                done["answer"] = _EMPTY_ANSWER
                done["sources"] = []
                done["fallback_used"] = False
                done["fallback_reason"] = None
            else:
                done.setdefault("fallback_used", False)
                done.setdefault("fallback_reason", None)

            done.update({
                "agent_id": AGENT_ID,
                "knowledge_release": KNOWLEDGE_RELEASE,
                "runtime_release": RUNTIME_RELEASE,
                "trace_id": trace_id,
                "planned_capabilities": ["search_knowledge"],
                "capability_coverage": {"search_knowledge": "missing"},
            })
            for event in progress:
                yield _sse(event)
            yield _sse({"type": "text_delta", "text": done["answer"]})
            yield _sse(done)

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app


app = create_app()
