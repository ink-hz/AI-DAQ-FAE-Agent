"""Owner-bound DAQ browser turns with durable replay and explicit failures."""
from __future__ import annotations

import copy
import time
import threading
from datetime import UTC, datetime
from pathlib import Path

import httpx
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from src.agent.guardrails import categorize_refusal
from src.agent.anthropic_transport import AnthropicTransportError
from src.agent.loop.runtime import LoopRuntime
from src.agent.protocol import RUNTIME_FAILURE_OUTCOMES
from src.api.stream import StreamHeartbeat, iter_with_heartbeat, sse_event
from src.attachments.models import AttachmentDescriptor, AttachmentError
from src.storage.authenticated_conversations import ConversationNotFound, ConversationStoreError
from src.storage.data_flywheel import ChatTurnRecord

from daq_fae.attachment_evidence import extend_with_attachments
from daq_fae.domain_evidence import DaqEvidencePolicy
from daq_fae.domain_tools import DaqToolBox
from daq_fae.empty_knowledge_synthesis import refine_empty_release_answer
from daq_fae.durable_state import (
    ContextConflict, DaqContextState, DurableStateError, RequestConflict,
    RequestInterrupted, SessionBusy,
)
from daq_fae.task_context import prepare_turn
from daq_fae.provider_errors import anthropic_failure


def authenticated_chat(app, body, subject, *, adapter, vision_adapter,
                       heartbeat_interval_seconds: float,
                       request_lease_renew_interval_seconds: float, root: Path,
                       agent_id: str, knowledge_release: str, runtime_release: str):
    if request_lease_renew_interval_seconds <= 0 or request_lease_renew_interval_seconds >= 600:
        raise ValueError("daq_request_lease_renew_interval_invalid")
    if not body.client_request_id:
        raise HTTPException(422, "client_request_id_required")
    if body.channel != "fae":
        raise HTTPException(409, "authenticated_channel_conflict")
    persistence = app.state.daq_authenticated_persistence
    durable = app.state.daq_durable_state
    if body.session_id:
        try:
            session = persistence.load_session(subject, body.session_id, store=app.state.session_store)
        except ConversationNotFound:
            raise HTTPException(404, "conversation not found") from None
        except ConversationStoreError:
            raise HTTPException(503, "daq_conversation_storage_unavailable") from None
    else:
        session = persistence.create_session(subject, store=app.state.session_store)
    try:
        reservation = durable.reserve(
            subject, body.client_request_id, body.model_dump(mode="json"), session.session_id,
        )
    except (RequestConflict, SessionBusy):
        raise HTTPException(409, "daq_request_conflict_or_busy") from None
    except DurableStateError:
        raise HTTPException(503, "daq_durable_storage_unavailable") from None
    if reservation.status == "replay":
        return StreamingResponse(iter(reservation.events), media_type="text/event-stream")
    if reservation.status != "execute":
        raise HTTPException(409, f"daq_request_{reservation.status}")

    def interrupt(reason):
        try:
            durable.interrupt(subject, reservation, reason=reason)
        except DurableStateError:
            pass

    if not app.state.chat_concurrency_gate.acquire(timeout=0):
        interrupt("concurrency_limit")
        raise HTTPException(429, "chat_concurrency_limit")
    try:
        manifests = app.state.attachment_store.bind_many(
            body.attachment_ids, session.session_id,
            owner_subject_id=str(subject.subject_id),
        )
        session.bind_attachments(
            [AttachmentDescriptor.from_manifest(item) for item in manifests],
            explicit_ids=body.attachment_ids,
        )
        checkpoint = durable.load_context(subject, session.session_id)
    except AttachmentError as exc:
        interrupt("attachment_binding_failed")
        app.state.chat_concurrency_gate.release()
        status = {"attachment_expired": 410, "attachment_deleted": 410,
                  "attachment_owner_mismatch": 403, "attachment_session_mismatch": 409}.get(exc.code, 422)
        raise HTTPException(status, exc.code) from None
    except DurableStateError:
        interrupt("context_load_failed")
        app.state.chat_concurrency_gate.release()
        raise HTTPException(503, "daq_durable_storage_unavailable") from None

    prior_history = list(session.messages)
    lease_lost = threading.Event()
    trace = app.state.trace_recorder.start_trace("daq_authenticated_chat_request", {
        "agent_id": agent_id, "session_id": session.session_id,
        "owner_subject_id": str(subject.subject_id), "message_length": len(body.message),
    })
    start = time.monotonic()

    def produce():
        frames = []
        plan = None
        done = None
        calls = []

        def emit(name, payload):
            frame = sse_event(name, payload)
            frames.append(frame)
            return frame

        try:
            yield emit("session", {"session_id": session.session_id, "agent_id": agent_id,
                                   "client_request_id": body.client_request_id})
            yield emit("stage", {"stage": "loop", "status": "running"})
            # Protect local bytes before model latency can outlive processing TTL.
            # The relation is committed with the turn only after the answer exists.
            attachment_relations = app.state.attachment_archive_service.prepare_turn(
                [item.attachment_id for item in session.visible_attachments()],
                explicit_attachment_ids=body.attachment_ids,
                answer_at=datetime.now(UTC),
            )
            refusal = categorize_refusal(body.message)
            if refusal:
                category, answer = refusal
                done = {"type": "done", "answer": answer, "outcome": "safe_abstained",
                        "sources": [], "tool_calls": [], "planned_capabilities": [],
                        "actual_capabilities": [], "capability_coverage": {},
                        "refusal_category": category}
            else:
                visible = session.visible_attachments()
                images = [item.source_id for item in visible if item.kind == "image"]
                texts = [item.source_id for item in visible if item.kind != "image"]
                previous = checkpoint.state.task_context if checkpoint is not None else None
                plan = prepare_turn(body.message, previous=previous,
                                    attachment_source_ids=texts, image_source_ids=images)
                toolbox = DaqToolBox(context=plan.context.tool_context())
                if body.attachment_ids:
                    toolbox = extend_with_attachments(
                        toolbox, session=session, store=app.state.attachment_store,
                        vision=vision_adapter,
                    )
                runtime = LoopRuntime(
                    adapter=adapter, toolbox=toolbox,
                    system_prompt_path=root / "prompts" / "empty_knowledge_system.md",
                    evidence_policy=DaqEvidencePolicy(),
                )
                try:
                    for event in runtime.run(
                        body.message, history=prior_history, entity_note=plan.context_note,
                        evidence_requirements=plan.evidence_requirements(),
                        required_attachment_source_ids=(
                            list(toolbox.attachment_source_ids) if body.attachment_ids else None
                        ),
                        required_image_source_ids=images,
                        attachment_dependency=(
                            "required_for_answer" if body.attachment_ids else "unknown"
                        ),
                    ):
                        if event.get("type") == "tool_call":
                            calls.append(event)
                            yield emit("stage", {"stage": "tool_call", **event})
                        elif event.get("type") == "done":
                            done = dict(event)
                    if done is None:
                        raise RuntimeError("runtime_missing_terminal")
                except AnthropicTransportError as exc:
                    done = anthropic_failure(exc)
                except httpx.HTTPStatusError as exc:
                    code = exc.response.status_code
                    outcome = ("provider_configuration_error" if code in {400, 401, 403, 404, 422}
                               else "provider_rate_limited" if code == 429
                               else "provider_unavailable" if code >= 500 else "provider_error")
                    done = {"answer": "数采模型请求失败，未生成答案。", "outcome": outcome,
                            "sources": [], "tool_calls": [], "provider_status_code": code,
                            "fallback_used": True, "fallback_reason": f"provider_http_{code}"}
                except httpx.TransportError as exc:
                    done = {"answer": "数采模型连接失败，未生成答案。", "outcome": "provider_unavailable",
                            "sources": [], "tool_calls": [], "error_type": type(exc).__name__,
                            "fallback_used": True, "fallback_reason": "provider_transport_error"}
                except Exception as exc:
                    done = {"answer": "数采服务运行失败，未生成答案。", "outcome": "internal_error",
                            "sources": [], "tool_calls": [], "error_type": type(exc).__name__,
                            "fallback_used": True, "fallback_reason": "runtime_error"}

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
            done.setdefault("planned_capabilities", list(plan.planned_capabilities) if plan else [])
            coverage = done.get("capability_coverage", {})
            done.update({"agent_id": agent_id, "knowledge_release": knowledge_release,
                         "runtime_release": runtime_release, "trace_id": trace.trace_id,
                         "session_id": session.session_id,
                         "client_request_id": body.client_request_id,
                         "coverage_status": (
                             "full" if coverage and all(v == "full" for v in coverage.values())
                             else "partial" if any(v in {"full", "partial"} for v in coverage.values())
                             else "empty" if "empty" in coverage.values() else "unknown"
                         ),
                         "duration_ms": int((time.monotonic() - start) * 1000)})
            done.pop("provenance", None)
            refine_empty_release_answer(
                done, planned_capabilities=done["planned_capabilities"],
                knowledge_release=knowledge_release,
            )
            if lease_lost.is_set():
                raise RequestInterrupted("daq_execution_lease_lost")
            working_session = copy.deepcopy(session)
            working_session.append_message("user", body.message)
            working_session.append_message("assistant", done["answer"])
            turn = ChatTurnRecord(
                external_session_id=session.session_id, channel=session.channel,
                question=body.message, answer=done["answer"], trace_id=trace.trace_id,
                turn_index=sum(item["role"] == "user" for item in working_session.messages) - 1,
                sources=done.get("sources", []), stages=calls, done=done,
                planned_capabilities=done["planned_capabilities"],
                capability_coverage=coverage, fallback_used=done["fallback_used"],
                fallback_reason=done["fallback_reason"], outcome=done["outcome"],
                duration_ms=done["duration_ms"],
            )
            try:
                # A progress stream can run for longer than the lease without
                # ever emitting a heartbeat. Check ownership immediately before
                # writing the durable turn so expiry cannot create a visible
                # turn with an interrupted request ID.
                durable.renew(subject, reservation)
                context = DaqContextState(task_context=plan.context.to_checkpoint()) if plan else None
                def terminal_events(turn_id):
                    done["turn_id"] = turn_id
                    return [*frames,
                            sse_event("text_delta", {"delta": done["answer"]}),
                            sse_event("sources", done["sources"]),
                            sse_event("done", done)]

                _, all_frames = durable.complete_turn(
                    subject, reservation,
                    write_turn=lambda connection: persistence.save_turn(
                        subject, working_session, turn=turn,
                        attachment_relations=attachment_relations, connection=connection,
                    ),
                    terminal_events=terminal_events, context=context,
                    expected_context_revision=checkpoint.revision if checkpoint else 0,
                )
                terminal_frames = all_frames[len(frames):]
                session.messages = working_session.messages
                session.session_context = working_session.session_context
                session.last_active = working_session.last_active
            except (ConversationStoreError, DurableStateError, ContextConflict,
                    RequestInterrupted, RequestConflict):
                interrupt("persistence_failed")
                trace.finalize({"outcome": "persistence_error", "fallback_used": True,
                                "fallback_reason": "authenticated_persistence_failed"})
                yield emit("stage", {"stage": "persistence", "status": "error",
                                     "reason": "authenticated_persistence_failed"})
                return
            trace.finalize({"outcome": done["outcome"], "answer_length": len(done["answer"]),
                            "planned_capabilities": done["planned_capabilities"],
                            "capability_coverage": coverage, "fallback_used": done["fallback_used"],
                            "fallback_reason": done["fallback_reason"],
                            "synthesis_mode": done.get("synthesis_mode"),
                            "duration_ms": done["duration_ms"]}, metadata={
                                "agent_id": agent_id, "session_id": session.session_id,
                                "runtime_release": runtime_release,
                                "knowledge_release": knowledge_release,
                            })
            yield from terminal_frames
        except Exception as exc:
            interrupt("authenticated_execution_failed")
            trace.finalize({"outcome": "internal_error", "fallback_used": True,
                            "fallback_reason": "authenticated_execution_failed",
                            "error_type": type(exc).__name__})
            yield emit("stage", {"stage": "runtime", "status": "error",
                                 "reason": "authenticated_execution_failed"})
        finally:
            app.state.chat_concurrency_gate.release()

    def stream():
        next_renewal = time.monotonic() + request_lease_renew_interval_seconds
        terminal_sent = False
        try:
            for item in iter_with_heartbeat(produce(), heartbeat_interval_seconds=heartbeat_interval_seconds):
                if time.monotonic() >= next_renewal:
                    try:
                        durable.renew(subject, reservation)
                    except (DurableStateError, RequestInterrupted):
                        try:
                            current = durable.reserve(
                                subject, body.client_request_id, body.model_dump(mode="json"),
                                session.session_id,
                            )
                        except DurableStateError:
                            current = None
                        if current is None or current.status != "replay":
                            lease_lost.set()
                            interrupt("execution_lease_renewal_failed")
                            yield sse_event("stage", {"stage": "durable_state", "status": "error",
                                                      "reason": "execution_lease_renewal_failed"})
                            return
                    next_renewal = time.monotonic() + request_lease_renew_interval_seconds
                if isinstance(item, StreamHeartbeat):
                    yield sse_event("heartbeat", {"elapsed_ms": item.elapsed_ms, "count": item.count})
                else:
                    if item.startswith("event: done\n"):
                        terminal_sent = True
                    yield item
        finally:
            if not terminal_sent:
                lease_lost.set()
                interrupt("client_disconnected_or_stream_incomplete")

    return StreamingResponse(stream(), media_type="text/event-stream")
