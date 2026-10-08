"""Local Dev API for an independent DAQ FAE with no published knowledge."""

from __future__ import annotations

import os
from pathlib import Path
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
import httpx

from src.agent.loop.adapters import AnthropicAdapter
from src.agent.loop.runtime import LoopRuntime

from daq_fae.api_attachments import configure_attachments
from src.attachments.models import AttachmentDescriptor, AttachmentError, AttachmentLimits
from daq_fae.domain_evidence import DaqEvidencePolicy, question_requirements
from daq_fae.domain_tools import DaqToolBox
from daq_fae.offline_adapter import OfflineAdapter
from daq_fae.api_transport import ChatRequest, LocalRequestRegistry, RequestRecord, is_local_peer
from src.agent.session import SessionStore
from src.api.stream import StreamHeartbeat, iter_with_heartbeat, sse_event
from src.api.concurrency import ChatConcurrencyGate
from src.agent.tracing import NoopTraceSink, TraceRecorder, install_trace_ctx
from src.agent.protocol import RUNTIME_FAILURE_OUTCOMES


AGENT_ID = "ai-daq-fae-agent"
KNOWLEDGE_RELEASE = "empty-dev-v0"
RUNTIME_RELEASE = "fae-3d0b06d-dev"
_ROOT = Path(__file__).resolve().parent.parent


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


def create_app(*, provider_mode: str | None = None, adapter=None,
               knowledge_dir: Path | None = None, heartbeat_interval_seconds: float = 10,
               max_concurrent: int = 2, trace_recorder=None,
               attachment_dir: Path | None = None, attachment_limits=None,
               attachment_clock=None) -> FastAPI:
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

    app.state.session_store = SessionStore(ttl_seconds=3600)
    app.state.chat_concurrency_gate = ChatConcurrencyGate(max_concurrent)
    app.state.trace_recorder = trace_recorder or TraceRecorder([NoopTraceSink()])
    registry = LocalRequestRegistry()
    configure_attachments(
        app, root=attachment_dir or Path(os.getenv("DAQ_ATTACHMENT_STORAGE_DIR", str(_ROOT / "data" / "daq_attachments"))),
        limits=attachment_limits or AttachmentLimits(), clock=attachment_clock,
    )

    @app.middleware("http")
    async def local_dev_guard(request: Request, call_next):
        if request.client is None or not is_local_peer(request.client.host):
            return JSONResponse(status_code=403, content={"detail": "local_dev_only"})
        return await call_next(request)

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "environment": "development",
            "agent_id": AGENT_ID,
            "knowledge_release": KNOWLEDGE_RELEASE,
            "runtime_release": RUNTIME_RELEASE,
            "provider_mode": mode,
            "local_dev_only": True,
            "attachment_capability": "http_storage_only",
            "attachment_model_evidence_enabled": False,
            "attachment_archive_enabled": False,
            "platform_identity_enabled": False,
            "session_persistence": "process_memory",
            "request_idempotency": "process_memory",
            "trace_persistence": "configured_sink" if trace_recorder else "disabled",
        }

    @app.get("/history")
    def history(session_id: str):
        session = app.state.session_store.get(session_id)
        if session is None:
            raise HTTPException(404, "session not found or expired")
        return {"session_id": session.session_id, "channel": session.channel,
                "messages": list(session.messages), "current_schema": None}

    @app.post("/chat")
    def chat(request: ChatRequest):
        fingerprint = (request.message, request.session_id, request.channel, tuple(request.attachment_ids))
        with registry.lock:
            registry.prune()
            existing = registry.records.get(request.client_request_id) if request.client_request_id else None
            if existing:
                if existing.fingerprint != fingerprint:
                    raise HTTPException(409, "client_request_id_conflict")
                if not existing.finished:
                    raise HTTPException(409, "request_in_progress")
                if app.state.session_store.get(existing.session_id) is None:
                    raise HTTPException(404, "session not found or expired")
                return StreamingResponse(iter(tuple(existing.events)), media_type="text/event-stream")
            session = app.state.session_store.get(request.session_id) if request.session_id else None
            if request.session_id and session is None:
                raise HTTPException(404, "session not found or expired")
            if session is not None and request.channel != session.channel:
                raise HTTPException(409, "session_channel_conflict")
            if session is None:
                # No trusted Platform identity exists in this localhost-only Dev service.
                session = app.state.session_store.create(channel=request.channel)
            if session.session_id in registry.active_sessions:
                raise HTTPException(409, "session_request_in_progress")
            if not app.state.chat_concurrency_gate.acquire(timeout=0):
                raise HTTPException(429, "chat_concurrency_limit")
            try:
                manifests = app.state.attachment_store.bind_many(
                    request.attachment_ids, session.session_id, owner_subject_id=None,
                )
            except AttachmentError as exc:
                app.state.chat_concurrency_gate.release()
                status = {
                    "attachment_expired": 410, "attachment_deleted": 410,
                    "attachment_owner_mismatch": 403, "attachment_session_mismatch": 409,
                }.get(exc.code, 422)
                raise HTTPException(status, exc.code) from None
            session.bind_attachments(
                [AttachmentDescriptor.from_manifest(manifest) for manifest in manifests],
                explicit_ids=request.attachment_ids,
            )
            registry.active_sessions.add(session.session_id)
            record = RequestRecord(fingerprint=fingerprint, session_id=session.session_id)
            if request.client_request_id:
                registry.records[request.client_request_id] = record
            prior_history = list(session.messages)

        ctx = app.state.trace_recorder.start_trace("daq_chat_request", {
            "session_id": session.session_id, "message": request.message,
            "agent_id": AGENT_ID, "channel": session.channel,
        })
        started_at = time.monotonic()

        def runtime_events():
            runtime = LoopRuntime(
                adapter=adapter,
                toolbox=DaqToolBox(),
                system_prompt_path=_ROOT / "prompts" / "empty_knowledge_system.md",
                evidence_policy=DaqEvidencePolicy(),
            )
            progress = []
            try:
                with install_trace_ctx(ctx):
                    if request.attachment_ids:
                        done = {
                            "type": "done", "outcome": "attachment_evidence_unavailable",
                            "answer": "附件已安全接收并绑定当前会话，但本次数采 Dev 尚未接入附件取证工具，无法根据附件内容回答。",
                            "sources": [], "tool_calls": [],
                            "fallback_used": True,
                            "fallback_reason": "attachment_model_evidence_not_enabled",
                            "attachment_capability": "http_storage_only",
                        }
                    else:
                        done = None
                        for event in runtime.run(
                            request.message, history=prior_history,
                            evidence_requirements=question_requirements(request.message),
                        ):
                            if event.get("type") == "tool_call":
                                progress.append(event)
                                yield "stage", {"stage": "tool_call", **event}
                            elif event.get("type") == "done":
                                done = dict(event)
                        if done is None:
                            raise RuntimeError("runtime_missing_terminal")
            except httpx.HTTPStatusError as exc:
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

            if done["outcome"] in RUNTIME_FAILURE_OUTCOMES:
                done["fallback_used"] = True
                done["fallback_reason"] = (
                    done.get("answer_contract", {}).get("validation_error")
                    or done.get("evidence_policy", {}).get("failure_reason")
                    or done["outcome"]
                )
            else:
                done.setdefault("fallback_used", False)
                done.setdefault("fallback_reason", None)
            calls = done.get("tool_calls") or progress
            done.setdefault(
                "planned_capabilities",
                ["user_attachment"] if request.attachment_ids else ["search_knowledge"],
            )
            actual = done.get("actual_capabilities", [])
            coverage = done.get("capability_coverage", {})
            coverage_status = (
                "full" if coverage and all(value == "full" for value in coverage.values())
                else "partial" if "partial" in coverage.values() or "full" in coverage.values()
                else "empty" if "empty" in coverage.values()
                else "unknown"
            )
            done.update({
                "agent_id": AGENT_ID,
                "knowledge_release": KNOWLEDGE_RELEASE,
                "runtime_release": RUNTIME_RELEASE,
                "trace_id": ctx.trace_id,
                "session_id": session.session_id,
                "client_request_id": request.client_request_id,
                "actual_capabilities": actual,
                "capability_coverage": coverage,
                "coverage_status": coverage_status,
                "duration_ms": int((time.monotonic() - started_at) * 1000),
            })
            session.append_message("user", request.message)
            session.append_message("assistant", done["answer"])
            ctx.finalize({"outcome": done["outcome"], "answer": done["answer"],
                          "fallback_used": done["fallback_used"],
                          "fallback_reason": done["fallback_reason"],
                          "capability_coverage": coverage,
                          "planned_capabilities": done["planned_capabilities"],
                          "actual_capabilities": actual,
                          "coverage_status": coverage_status,
                          "tool_calls": calls,
                          "duration_ms": done["duration_ms"],
                          "provider_status_code": done.get("provider_status_code"),
                          "error_type": done.get("error_type")}, metadata={
                "agent_id": AGENT_ID, "knowledge_release": KNOWLEDGE_RELEASE,
                "runtime_release": RUNTIME_RELEASE, "session_id": session.session_id,
            })
            yield "text_delta", {"delta": done["answer"]}
            yield "sources", done["sources"]
            yield "done", done

        def produce():
            try:
                def opening_events():
                    yield "session", {"session_id": session.session_id, "agent_id": AGENT_ID,
                                      "client_request_id": request.client_request_id}
                    yield "stage", {"stage": "loop", "status": "running"}
                    yield from runtime_events()

                for name, payload in opening_events():
                    encoded = sse_event(name, payload)
                    with registry.lock:
                        record.events.append(encoded)
                        if name == "done":
                            record.finished = True
                    yield encoded
            finally:
                with registry.lock:
                    registry.active_sessions.discard(session.session_id)
                    app.state.chat_concurrency_gate.release()

        def stream():
            # The worker owns execution and gate release even after HTTP disconnect.
            for item in iter_with_heartbeat(produce(), heartbeat_interval_seconds=heartbeat_interval_seconds):
                if isinstance(item, StreamHeartbeat):
                    yield sse_event("heartbeat", {"elapsed_ms": item.elapsed_ms, "count": item.count})
                    continue
                yield item

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app


app = create_app()
