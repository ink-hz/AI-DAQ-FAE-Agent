"""Local Dev API for an independent DAQ FAE with no published knowledge."""

from __future__ import annotations

import os
import json
from pathlib import Path
import time
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
import httpx
from pydantic import BaseModel, Field

from src.agent.loop.adapters import AnthropicAdapter
from src.agent.anthropic_transport import AnthropicTransportError
from src.agent.loop.runtime import LoopRuntime
from src.agent.guardrails import categorize_refusal

from daq_fae.api_attachments import configure_attachments
from src.attachments.models import AttachmentDescriptor, AttachmentError, AttachmentLimits
from src.attachments.archive_repository import AttachmentArchiveRepository
from daq_fae.domain_evidence import DaqEvidencePolicy
from daq_fae.domain_tools import DaqToolBox
from daq_fae.empty_knowledge_synthesis import (EMPTY_KNOWLEDGE_RELEASE,
                                                 refine_empty_release_answer)
from daq_fae.attachment_evidence import extend_with_attachments
from daq_fae.task_context import prepare_turn
from daq_fae.local_state import LocalStateError, LocalStateStore
from daq_fae.knowledge.reviewed_view import ReviewedKnowledge
from daq_fae.knowledge.entitlements import KnowledgeEntitlements
from daq_fae.authenticated_chat import authenticated_chat
from daq_fae.provider_errors import anthropic_failure
from daq_fae.authenticated_persistence import configure_authenticated_persistence
from daq_fae.durable_state import configure_durable_state
from daq_fae.platform_identity import configure_platform_identity
from daq_fae.platform_tasks import configure_platform_tasks
from daq_fae.webui import mount_authenticated_webui, mount_local_webui
from daq_fae.offline_adapter import OfflineAdapter
from daq_fae.api_transport import ChatRequest, LocalRequestRegistry, RequestRecord, is_local_peer
from src.agent.session import SessionStore
from src.api.stream import StreamHeartbeat, iter_with_heartbeat, sse_event
from src.api.concurrency import ChatConcurrencyGate
from src.agent.tracing import TraceConfig, build_recorder, install_trace_ctx
from src.agent.protocol import RUNTIME_FAILURE_OUTCOMES
from src.api.review_routes import register_review_routes
from src.platform_identity.models import PlatformIdentityError
from src.storage.authenticated_conversations import ConversationNotFound, ConversationStoreError


AGENT_ID = "ai-daq-fae-agent"
KNOWLEDGE_RELEASE = EMPTY_KNOWLEDGE_RELEASE
RUNTIME_RELEASE = "fae-bf1ce9a-dev"
_ROOT = Path(__file__).resolve().parent.parent


class FeedbackRequest(BaseModel):
    session_id: str
    message_index: int = Field(default=0, ge=0)
    rating: Literal["good", "bad"]
    comment: str = Field(default="", max_length=4000)
    turn_id: str | None = None
    trace_id: str | None = None
    reason_code: str | None = None


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
               knowledge_release_root: Path | None = None, knowledge_approval_verifier=None,
               request_lease_renew_interval_seconds: float = 60,
               max_concurrent: int = 2, trace_recorder=None,
               attachment_dir: Path | None = None, attachment_limits=None,
               attachment_clock=None, webui_dist: Path | None = None,
               state_db_path: Path | None = None, vision_adapter=None,
               platform_client=None, identity_repository=None,
               conversation_repository=None, feedback_store=None, review_store=None,
               durable_state=None, archive_repository=None,
               task_store=None, task_verifier=None, task_worker=None) -> FastAPI:
    identity_mode = os.getenv("DAQ_PLATFORM_IDENTITY_ENABLED", "false")
    if identity_mode not in {"true", "false"}:
        raise ValueError("daq_platform_identity_enabled_invalid")
    auth_mode = identity_mode == "true"
    task_flag = os.getenv("DAQ_PLATFORM_TASK_ENABLED", "false")
    if task_flag not in {"true", "false"}:
        raise ValueError("daq_platform_task_enabled_invalid")
    if task_flag == "true" and not auth_mode:
        raise ValueError("daq_platform_task_requires_authenticated_mode")
    if auth_mode:
        if not os.getenv("DAQ_DATABASE_URL") or not os.getenv("DAQ_AUTHENTICATED_CONTENT_KEYRING_FILE"):
            raise ValueError("daq_authenticated_persistence_configuration_missing")
    archive_flag = os.getenv("DAQ_ATTACHMENT_ARCHIVE_ENABLED", "false")
    if archive_flag not in {"true", "false"}:
        raise ValueError("daq_attachment_archive_configuration_invalid")
    archive_enabled = archive_flag == "true"
    if archive_enabled and not auth_mode:
        raise ValueError("daq_attachment_archive_requires_authenticated_mode")
    archive_handoff_seconds = int(os.getenv("DAQ_ATTACHMENT_ARCHIVE_HANDOFF_SECONDS", "604800"))
    if archive_handoff_seconds <= 0:
        raise ValueError("daq_attachment_archive_handoff_invalid")
    knowledge_dir = knowledge_dir or _ROOT / "knowledge"
    if not knowledge_dir.is_dir():
        raise ValueError("empty knowledge directory is missing")
    if any(path.is_file() and path.name not in {".gitkeep", "README.md"}
           for path in knowledge_dir.rglob("*")):
        raise ValueError("empty knowledge bootstrap cannot load unreviewed knowledge files")
    release_root = knowledge_release_root or Path(os.getenv(
        "DAQ_KNOWLEDGE_RELEASE_ROOT", str(_ROOT / "data" / "knowledge" / "published"),
    ))
    knowledge = ReviewedKnowledge.load_active(
        release_root, verify_approval=knowledge_approval_verifier,
        runtime_release=RUNTIME_RELEASE,
        upstream_sha=json.loads((_ROOT / 'upstream-source.json').read_text())['revision'],
    )
    if knowledge is not None and any(knowledge.manifest.get(key)
                                     for key in ('sources', 'records', 'sections')) and not auth_mode:
        raise ValueError('daq_nonempty_knowledge_requires_authenticated_mode')
    entitlements = None
    if auth_mode and knowledge is not None:
        entitlement_path = os.getenv("DAQ_KNOWLEDGE_ENTITLEMENTS_FILE")
        if not entitlement_path:
            raise ValueError("daq_knowledge_role_contract_missing")
        try:
            entitlements = KnowledgeEntitlements(Path(entitlement_path))
        except PlatformIdentityError:
            raise ValueError("daq_knowledge_role_contract_invalid") from None
        if task_flag == "true":
            raise ValueError("daq_knowledge_task_role_replay_contract_missing")
    knowledge_release = knowledge.release_id if knowledge is not None else EMPTY_KNOWLEDGE_RELEASE

    mode = provider_mode or os.getenv("DAQ_PROVIDER_MODE", "offline")
    if mode not in {"offline", "anthropic"}:
        raise ValueError("DAQ_PROVIDER_MODE must be offline or anthropic")
    if adapter is None:
        adapter = OfflineAdapter() if mode == "offline" else _anthropic_dev_adapter()

    app = FastAPI(title="AI DAQ FAE Agent Dev Bootstrap")
    app.state.daq_knowledge = knowledge
    app.state.daq_knowledge_entitlements = entitlements

    app.state.session_store = SessionStore(ttl_seconds=3600)
    app.state.chat_concurrency_gate = ChatConcurrencyGate(max_concurrent)
    app.state.trace_recorder = trace_recorder or build_recorder(TraceConfig(
        log_path=Path(os.getenv("DAQ_TRACE_LOG_PATH", str(_ROOT / "data" / "daq_traces.jsonl"))),
        environment="development", release=RUNTIME_RELEASE,
    ))
    app.state.local_state = None if auth_mode else LocalStateStore(
        state_db_path or Path(os.getenv("DAQ_DEV_STATE_DB", str(_ROOT / "data" / "daq_dev.sqlite3"))),
        agent_id=AGENT_ID,
    )
    registry = LocalRequestRegistry()
    configure_attachments(
        app, root=attachment_dir or Path(os.getenv("DAQ_ATTACHMENT_STORAGE_DIR", str(_ROOT / "data" / "daq_attachments"))),
        limits=attachment_limits or AttachmentLimits(), clock=attachment_clock,
        archive_repository=(archive_repository if archive_repository is not None else
                            AttachmentArchiveRepository(os.environ["DAQ_DATABASE_URL"], agent_id=AGENT_ID)
                            if auth_mode else None),
        archive_enabled=archive_enabled, archive_handoff_seconds=archive_handoff_seconds,
    )

    @app.middleware("http")
    async def local_dev_guard(request: Request, call_next):
        if not auth_mode and (request.client is None or not is_local_peer(request.client.host)):
            return JSONResponse(status_code=403, content={"detail": "local_dev_only"})
        return await call_next(request)

    @app.get("/health")
    def health():
        archive_health = {"enabled": archive_enabled, "ready": True,
                          "pending": 0, "failed": 0,
                          "oldest_pending_seconds": 0, "expired_unarchived_total": 0}
        if app.state.attachment_archive_repository is not None:
            try:
                archive_health.update(app.state.attachment_archive_repository.health())
            except Exception:
                archive_health["ready"] = False
        return {
            "status": "ok",
            "environment": "development",
            "agent_id": AGENT_ID,
            "knowledge_release": knowledge_release,
            "runtime_release": RUNTIME_RELEASE,
            "provider_mode": mode,
            "local_dev_only": not auth_mode,
            "attachment_capability": "session_bound_tools",
            "attachment_model_evidence_enabled": True,
            "attachment_archive_enabled": archive_enabled,
            "attachment_archive": archive_health,
            "attachments": {
                "enabled": True,
                "vision_enabled": vision_adapter is not None,
                "vision_reason": "ready" if vision_adapter is not None else "disabled_by_config",
            },
            "platform_identity_enabled": auth_mode,
            "platform_task_enabled": hasattr(app.state, "platform_task_capabilities"),
            "session_persistence": "postgres_daq_authenticated" if auth_mode else "local_sqlite_dev",
            "request_idempotency": "postgres_daq_ledger" if auth_mode else "local_sqlite_dev",
            "trace_persistence": "configured_sink" if trace_recorder else "daq_jsonl_dev",
        }

    @app.get("/history")
    def history(session_id: str, request: Request):
        if auth_mode:
            try:
                return app.state.daq_authenticated_persistence.history(
                    request.state.platform_identity, session_id,
                )
            except ConversationNotFound:
                raise HTTPException(404, "conversation not found") from None
            except ConversationStoreError:
                raise HTTPException(503, "daq_conversation_storage_unavailable") from None
            except PlatformIdentityError as exc:
                raise HTTPException(exc.status_code, exc.code) from None
        session = app.state.session_store.get(session_id)
        if session is None:
            restored = app.state.local_state.load_session(session_id)
            if restored is not None:
                session = app.state.session_store.adopt(restored)
        if session is None:
            raise HTTPException(404, "session not found or expired")
        return {"session_id": session.session_id, "channel": session.channel,
                "messages": list(session.messages), "current_schema": None}

    @app.post("/feedback")
    def feedback(body: FeedbackRequest, request: Request):
        if auth_mode:
            try:
                feedback_id = app.state.daq_authenticated_persistence.record_feedback(
                    request.state.platform_identity,
                    session_id=body.session_id, message_index=body.message_index,
                    rating=body.rating, comment=body.comment, turn_id=body.turn_id,
                    trace_id=body.trace_id, reason_code=body.reason_code,
                )
            except ConversationNotFound:
                raise HTTPException(404, "feedback target not found") from None
            except ConversationStoreError:
                raise HTTPException(503, "daq_feedback_storage_unavailable") from None
            except PlatformIdentityError as exc:
                raise HTTPException(exc.status_code, exc.code) from None
            return {"ok": True, "feedback_id": feedback_id}
        session = app.state.session_store.get(body.session_id)
        if session is None:
            restored = app.state.local_state.load_session(body.session_id)
            if restored is not None:
                session = app.state.session_store.adopt(restored)
        if session is None:
            raise HTTPException(404, "session not found or expired")
        feedback_id = app.state.local_state.record_feedback(
            session_id=body.session_id, turn_index=body.message_index,
            turn_id=body.turn_id, trace_id=body.trace_id,
            rating=body.rating, comment=body.comment, reason_code=body.reason_code,
        )
        if feedback_id is None:
            raise HTTPException(404, "feedback target not found")
        return {"ok": True, "feedback_id": feedback_id}

    @app.post("/chat")
    def chat(request: ChatRequest, http_request: Request):
        if auth_mode:
            return authenticated_chat(
                app, request, http_request.state.platform_identity,
                adapter=adapter, vision_adapter=vision_adapter,
                heartbeat_interval_seconds=heartbeat_interval_seconds,
                request_lease_renew_interval_seconds=request_lease_renew_interval_seconds,
                root=_ROOT, agent_id=AGENT_ID,
                knowledge_release=knowledge_release, runtime_release=RUNTIME_RELEASE,
            )
        fingerprint = (request.message, request.session_id, request.channel, tuple(request.attachment_ids))
        with registry.lock:
            registry.prune()
            if request.client_request_id:
                try:
                    persisted = app.state.local_state.lookup_request(
                        request.client_request_id, fingerprint,
                    )
                except LocalStateError:
                    raise HTTPException(409, "client_request_id_conflict") from None
                if persisted is not None:
                    persisted_session_id, events = persisted
                    if events is None:
                        raise HTTPException(409, "request_in_progress_or_interrupted")
                    if app.state.local_state.load_session(persisted_session_id) is None:
                        raise HTTPException(404, "session not found or expired")
                    return StreamingResponse(iter(events), media_type="text/event-stream")
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
            if session is None and request.session_id:
                restored = app.state.local_state.load_session(request.session_id)
                if restored is not None:
                    session = app.state.session_store.adopt(restored)
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
                try:
                    app.state.local_state.reserve_request(
                        request.client_request_id, fingerprint, session.session_id,
                    )
                except LocalStateError:
                    registry.active_sessions.discard(session.session_id)
                    app.state.chat_concurrency_gate.release()
                    raise HTTPException(409, "client_request_id_conflict") from None
                registry.records[request.client_request_id] = record
            prior_history = list(session.messages)

        ctx = app.state.trace_recorder.start_trace("daq_chat_request", {
            "session_id": session.session_id, "message_length": len(request.message),
            "agent_id": AGENT_ID, "channel": session.channel,
        })
        started_at = time.monotonic()

        def runtime_events():
            progress = []
            plan = None
            try:
                with install_trace_ctx(ctx):
                    done = None
                    refusal = categorize_refusal(request.message)
                    if refusal:
                        category, answer = refusal
                        events = iter(({"type": "done", "answer": answer,
                                        "outcome": "safe_abstained", "sources": [],
                                        "tool_calls": [], "planned_capabilities": [],
                                        "actual_capabilities": [], "capability_coverage": {},
                                        "refusal_category": category},))
                    else:
                        visible_attachments = session.visible_attachments()
                        image_source_ids = [
                            item.source_id for item in visible_attachments if item.kind == "image"
                        ]
                        text_source_ids = [
                            item.source_id for item in visible_attachments if item.kind != "image"
                        ]
                        plan = prepare_turn(
                            request.message,
                            previous=app.state.local_state.load_context(session.session_id),
                            attachment_source_ids=text_source_ids,
                            image_source_ids=image_source_ids,
                        )
                        base_toolbox = DaqToolBox(
                            context=plan.context.tool_context(), knowledge=knowledge,
                            role=None,
                            requirements=plan.requirements,
                        )
                        toolbox = (
                            extend_with_attachments(
                                base_toolbox, session=session,
                                store=app.state.attachment_store, vision=vision_adapter,
                            ) if request.attachment_ids else base_toolbox
                        )
                        runtime = LoopRuntime(
                            adapter=adapter,
                            toolbox=toolbox,
                            system_prompt_path=_ROOT / "prompts" / (
                                "reviewed_knowledge_system.md" if knowledge is not None
                                else "empty_knowledge_system.md"
                            ),
                            evidence_policy=DaqEvidencePolicy(),
                        )
                        events = runtime.run(
                            request.message, history=prior_history,
                            entity_note=plan.context_note,
                            evidence_requirements=plan.evidence_requirements(),
                            required_attachment_source_ids=(
                                list(toolbox.attachment_source_ids) if request.attachment_ids else None
                            ),
                            required_image_source_ids=image_source_ids,
                            attachment_dependency=(
                                "required_for_answer" if request.attachment_ids else "unknown"
                            ),
                        )
                    for event in events:
                        if event.get("type") == "tool_call":
                            progress.append(event)
                            yield "stage", {"stage": "tool_call", **event}
                        elif event.get("type") == "done":
                            done = dict(event)
                    if done is None:
                        raise RuntimeError("runtime_missing_terminal")
            except AnthropicTransportError as exc:
                done = anthropic_failure(exc)
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
            if done.get("attachment_fallback_used"):
                done["fallback_used"] = True
                done["fallback_reason"] = done.get("attachment_fallback_reason")
            calls = done.get("tool_calls") or progress
            done.setdefault(
                "planned_capabilities",
                list(plan.planned_capabilities) if plan is not None else ["search_knowledge"],
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
                "knowledge_release": knowledge_release,
                "runtime_release": RUNTIME_RELEASE,
                "trace_id": ctx.trace_id,
                "session_id": session.session_id,
                "client_request_id": request.client_request_id,
                "actual_capabilities": actual,
                "capability_coverage": coverage,
                "coverage_status": coverage_status,
                "duration_ms": int((time.monotonic() - started_at) * 1000),
            })
            done.pop("provenance", None)
            refine_empty_release_answer(
                done, planned_capabilities=done["planned_capabilities"],
                knowledge_release=knowledge_release,
            )
            session.append_message("user", request.message)
            session.append_message("assistant", done["answer"])
            try:
                app.state.local_state.save_turn(
                    session, done,
                    context_checkpoint=plan.context.to_checkpoint() if plan is not None else None,
                )
            except Exception:
                done.update({
                    "answer": "本轮会话无法安全保存，答案未交付。请检查数采 Dev 存储。",
                    "outcome": "persistence_error", "sources": [],
                    "fallback_used": True, "fallback_reason": "local_state_write_failed",
                    "persistence_failed": True,
                })
                done.pop("turn_id", None)
                session.messages[-1]["content"] = done["answer"]
            ctx.finalize({"outcome": done["outcome"], "answer_length": len(done["answer"]),
                          "fallback_used": done["fallback_used"],
                          "fallback_reason": done["fallback_reason"],
                          "synthesis_mode": done.get("synthesis_mode"),
                          "capability_coverage": coverage,
                          "planned_capabilities": done["planned_capabilities"],
                          "actual_capabilities": actual,
                          "coverage_status": coverage_status,
                          "tool_calls": [
                              {key: call.get(key) for key in ("tool", "status", "duration_ms")
                               if key in call}
                              for call in calls if isinstance(call, dict)
                          ],
                          "duration_ms": done["duration_ms"],
                          "provider_status_code": done.get("provider_status_code"),
                          "error_type": done.get("error_type")}, metadata={
                "agent_id": AGENT_ID, "knowledge_release": knowledge_release,
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
                            if request.client_request_id:
                                app.state.local_state.finish_request(
                                    request.client_request_id, record.events,
                                )
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

    if auth_mode:
        configure_authenticated_persistence(
            app, runtime_release=RUNTIME_RELEASE, knowledge_release=knowledge_release,
            conversation_repository=conversation_repository,
            feedback_store=feedback_store, review_store=review_store,
        )
        app.state.daq_durable_state = durable_state or configure_durable_state(app)
        if entitlements is not None:
            app.state.daq_authenticated_persistence.knowledge_access = (
                entitlements, app.state.daq_durable_state, knowledge_release,
            )
        @app.get("/authenticated/conversations")
        def list_authenticated_conversations(request: Request, cursor: str | None = None,
                                             limit: int = 30):
            try:
                return app.state.daq_authenticated_persistence.list_conversations(
                    request.state.platform_identity, cursor=cursor, limit=limit,
                )
            except ValueError:
                raise HTTPException(400, "invalid pagination request") from None
            except ConversationStoreError:
                raise HTTPException(503, "daq_conversation_storage_unavailable") from None

        @app.get("/authenticated/conversations/{session_id}")
        def authenticated_conversation(session_id: str, request: Request):
            try:
                return app.state.daq_authenticated_persistence.conversation_detail(
                    request.state.platform_identity, session_id,
                )
            except ConversationNotFound:
                raise HTTPException(404, "conversation not found") from None
            except ConversationStoreError:
                raise HTTPException(503, "daq_conversation_storage_unavailable") from None

        app.state.review_center_store = app.state.daq_authenticated_persistence._review
        register_review_routes(app)

        @app.middleware("http")
        async def daq_reviewer_boundary(request: Request, call_next):
            path = request.url.path
            if path == "/review" or path.startswith("/review/"):
                try:
                    app.state.daq_authenticated_persistence.review_for(
                        getattr(request.state, "platform_identity", None),
                    )
                except PlatformIdentityError as exc:
                    return JSONResponse({"error": {"code": exc.code}}, status_code=exc.status_code)
            return await call_next(request)

        configure_platform_identity(
            app, repository=identity_repository, platform_client=platform_client,
        )
        configure_platform_tasks(
            app, adapter=adapter, task_store=task_store,
            task_verifier=task_verifier, task_worker=task_worker,
        )
        mount_authenticated_webui(app, dist=webui_dist or _ROOT / "webui" / "dist")
    else:
        mount_local_webui(app, dist=webui_dist or _ROOT / "webui" / "dist")
    return app


app = create_app()
