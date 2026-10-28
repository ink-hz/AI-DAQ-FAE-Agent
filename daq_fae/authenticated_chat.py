"""Owner-bound DAQ browser turns with durable replay and explicit failures."""
from __future__ import annotations

import copy
import time
import threading
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

import httpx
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from src.agent.guardrails import categorize_refusal
from src.platform_identity.models import PlatformIdentityError
from src.agent.anthropic_transport import AnthropicTransportError
from daq_fae.thinking_prefix import DaqLoopRuntime as LoopRuntime
from src.agent.protocol import RUNTIME_FAILURE_OUTCOMES
from src.agent.tracing import install_trace_ctx
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
from daq_fae.provider_errors import anthropic_failure, provider_exception_done, vision_failure_reason
from daq_fae.request_schema import SchemaExtractionError, refers_to_prior_attachment
from daq_fae.workflow import (StageLedger, prepare_workflow, allowed_tools_for,
                              context_clarification_done)
from daq_fae.response_language import localize_answer
from daq_fae.offline_adapter import OfflineAdapter


def _prior_for_binding(previous, history, *, prior_release, prior_role,
                       current_release, current_role, history_floor=0):
    """Keep user assertions while dropping prior reviewed grounding on rebinding."""
    reasons = []
    try:
        floor = int(history_floor)
    except (TypeError, ValueError):
        floor = len(history)
        reasons.append('history_floor_invalid')
    if floor < 0 or floor > len(history):
        floor = len(history)
        reasons.append('history_floor_invalid')
    if prior_release != current_release:
        reasons.append('knowledge_release_changed')
    if prior_role != current_role:
        reasons.append('knowledge_role_changed')
    if not reasons:
        return previous, history[floor:], (), floor
    sanitized = copy.deepcopy(previous)
    if isinstance(sanitized, dict):
        sanitized['grounded_scope'] = {}
    invalidated = tuple({'field': 'grounded_scope', 'reason': reason}
                        for reason in reasons)
    # Earlier assistant turns may contain facts that are not visible under the
    # new role/release. The persisted history stays intact for audit and UI.
    return sanitized, [], invalidated, len(history)


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
    knowledge = getattr(app.state, "daq_knowledge", None)
    policy = getattr(app.state, "daq_knowledge_entitlements", None)
    try:
        binding = policy.binding_for(subject, knowledge_release) if knowledge is not None else None
    except PlatformIdentityError as exc:
        raise HTTPException(exc.status_code, exc.code) from None
    def assert_current_access():
        if binding is not None and policy.binding_for(subject, knowledge_release) != binding:
            raise PlatformIdentityError("daq_knowledge_role_changed", status_code=403)

    def authorized_frames(frames):
        # This is the last iterator boundary before StreamingResponse sends bytes.
        # Check after obtaining each frame, including buffered/replayed frames.
        try:
            for frame in frames:
                assert_current_access()
                yield frame
        except PlatformIdentityError as exc:
            yield sse_event("stage", {"stage": "authorization", "status": "error",
                                      "reason": exc.code})
        finally:
            close = getattr(frames, "close", None)
            if close is not None:
                close()

    payload = body.model_dump(mode="json")
    if binding is not None:
        payload["knowledge_access"] = binding
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
    effective_attachment_ids = (
        list(body.attachment_ids) if body.attachment_ids
        else list(session.active_attachment_ids)
        if refers_to_prior_attachment(body.message) else [])
    payload['effective_attachment_ids'] = effective_attachment_ids
    try:
        reservation = durable.reserve(
            subject, body.client_request_id, payload, session.session_id,
        )
    except (RequestConflict, SessionBusy):
        raise HTTPException(409, "daq_request_conflict_or_busy") from None
    except DurableStateError:
        raise HTTPException(503, "daq_durable_storage_unavailable") from None
    if reservation.status == "replay":
        return StreamingResponse(authorized_frames(iter(reservation.events)), media_type="text/event-stream")
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
            effective_attachment_ids, session.session_id,
            owner_subject_id=str(subject.subject_id),
        )
        session.bind_attachments(
            [AttachmentDescriptor.from_manifest(item) for item in manifests],
            explicit_ids=effective_attachment_ids,
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
        prepared = None
        done = None
        answer_policy = None
        calls = []
        stages = StageLedger(trace.trace_id)
        previous = checkpoint.state.task_context if checkpoint is not None else None
        effective_history = prior_history
        binding_invalidations = ()
        history_floor = 0
        if checkpoint is not None:
            previous, effective_history, binding_invalidations, history_floor = \
                _prior_for_binding(
                    previous, prior_history,
                    prior_release=checkpoint.state.knowledge_release,
                    prior_role=checkpoint.state.roles.get('knowledge_role'),
                    current_release=knowledge_release,
                    current_role=binding['role'] if binding else None,
                    history_floor=checkpoint.state.roles.get(
                        'history_floor_message_index', '0'),
                )

        def emit(name, payload):
            frame = sse_event(name, payload)
            frames.append(frame)
            return frame

        try:
            yield emit("session", {"session_id": session.session_id, "agent_id": agent_id,
                                   "client_request_id": body.client_request_id})
            # Protect local bytes before model latency can outlive processing TTL.
            # The relation is committed with the turn only after the answer exists.
            attachment_relations = app.state.attachment_archive_service.prepare_turn(
                [item.attachment_id for item in session.visible_attachments()],
                explicit_attachment_ids=body.attachment_ids,
                answer_at=datetime.now(UTC),
            )
            yield emit('stage', stages.start('guardrail'))
            refusal = categorize_refusal(body.message)
            yield emit('stage', stages.finish('guardrail', refusal=bool(refusal)))
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
                yield emit('stage', stages.start('schema'))
                try:
                    prepared = prepare_workflow(
                        body.message, adapter=adapter, previous=previous,
                        selected_answer_language=('en' if body.response_language == 'en-US'
                                                  else 'zh' if body.response_language == 'zh-CN'
                                                  else None),
                        knowledge=knowledge, role=binding['role'] if binding else 'internal_fae',
                        runtime_release=runtime_release,
                        prior_user_context='\n'.join(
                            item['content'] for item in effective_history[-8:]
                            if item.get('role') == 'user' and isinstance(item.get('content'), str)),
                        attachment_source_ids=texts, image_source_ids=images,
                        grounded_targets=(knowledge.ground_question(
                            body.message, role=binding['role']) if binding else None),
                    )
                except SchemaExtractionError as exc:
                    yield emit('stage', stages.finish('schema', error=str(exc)))
                    done = {'answer': '本轮请求理解失败，未生成可靠答案。',
                            'outcome': 'schema_error', 'sources': [], 'tool_calls': [],
                            'fallback_used': True, 'fallback_reason': str(exc)}
                except (AnthropicTransportError, httpx.HTTPStatusError,
                        httpx.TransportError) as exc:
                    done = provider_exception_done(exc)
                    yield emit('stage', stages.finish('schema', error=done['outcome']))
                else:
                    plan = prepared.plan
                    if binding_invalidations:
                        plan = replace(
                            plan,
                            invalidated_fields=(*plan.invalidated_fields,
                                                *binding_invalidations),
                            context_note=(plan.context_note + '\nPrior reviewed grounding '
                                          'invalidated after role or release change.'))
                        prepared = replace(prepared, plan=plan)
                    yield emit('stage', stages.finish(
                        'schema', request_kind=prepared.schema.request_kind,
                        goal_ids=[goal.id for goal in prepared.schema.goals],
                        original_question=prepared.schema.original_question,
                        effective_question=prepared.schema.effective_question,
                        entity_scope=sorted({entity for goal in prepared.schema.goals
                                             for entity in goal.entities}),
                        attachment_dependency=prepared.schema.attachment_dependency,
                        confidence=prepared.schema.confidence,
                        field_lineage=plan.field_lineage(),
                        goal_field_lineage=plan.goal_field_lineage(),
                        invalidated_fields=list(plan.invalidated_fields)))
                if done is None and not isinstance(adapter, OfflineAdapter):
                    done = context_clarification_done(prepared)
                    if done is not None:
                        yield emit('stage', stages.observed(
                            'clarification', reason='user_intent_missing'))
                if done is not None:
                    pass
                else:
                    assert plan is not None
                    assert_current_access()
                    allowed_tools = (None if isinstance(adapter, OfflineAdapter)
                                     else allowed_tools_for(prepared.schema))
                    toolbox = DaqToolBox(context=plan.context.tool_context(), knowledge=knowledge,
                                         current_question=prepared.schema.original_question,
                                         role=binding["role"] if binding else None,
                                         requirements=plan.requirements, access_guard=assert_current_access,
                                         allowed_tools=allowed_tools)
                    refer_visible_attachment = (
                        prepared.schema.attachment_dependency in {
                            'relevant', 'required_for_answer'}
                        and bool(texts or images))
                    if effective_attachment_ids or refer_visible_attachment:
                        toolbox = extend_with_attachments(
                            toolbox, session=session, store=app.state.attachment_store,
                            vision=vision_adapter,
                        )
                    answer_policy = DaqEvidencePolicy(
                        knowledge=knowledge, role=binding['role'] if binding else None,
                        allow_dev_source_check=(getattr(knowledge, 'sdk_catalog', None) is not None
                                                and not getattr(knowledge.sdk_catalog, 'published', False)),
                        service_contract=prepared.service_contract,
                        question=prepared.schema.effective_question,
                        answer_language=prepared.schema.answer_language,
                        allowed_tools=allowed_tools,
                        allowed_chinese_terms=tuple({entity for goal in prepared.schema.goals
                                                     for entity in goal.entities
                                                     if any('\u3400' <= char <= '\u9fff'
                                                            for char in entity)}))
                    runtime = LoopRuntime(
                        adapter=adapter, toolbox=toolbox,
                        system_prompt_path=root / "prompts" / (
                            "reviewed_knowledge_system.md" if knowledge is not None else "empty_knowledge_system.md"),
                        evidence_policy=answer_policy,
                    )
                    yield emit('stage', stages.start('loop'))
                    try:
                        for event in runtime.run(
                            prepared.loop_question, history=effective_history,
                            entity_note=plan.context_note,
                            evidence_requirements=plan.evidence_requirements(),
                            required_attachment_source_ids=(
                                list(texts) if effective_attachment_ids or refer_visible_attachment
                                else None
                            ),
                            required_image_source_ids=images,
                            attachment_dependency=prepared.schema.attachment_dependency,
                        ):
                            assert_current_access()
                            if event.get("type") == "tool_call":
                                calls.append(event)
                                yield emit('stage', stages.observed(
                                    'tool_call', tool=event.get('tool') or event.get('name'),
                                    tool_status=event.get('status')))
                            elif event.get("type") == "done":
                                done = dict(event)
                        if done is None:
                            raise RuntimeError("runtime_missing_terminal")
                        yield emit('stage', stages.finish('loop', outcome=done.get('outcome')))
                    except AnthropicTransportError as exc:
                        done = anthropic_failure(exc)
                        yield emit('stage', stages.finish('loop', error=done['outcome']))
                    except httpx.HTTPStatusError as exc:
                        code = exc.response.status_code
                        outcome = ("provider_configuration_error" if code in {400, 401, 403, 404, 422}
                                   else "provider_rate_limited" if code == 429
                                   else "provider_unavailable" if code >= 500 else "provider_error")
                        done = {"answer": "数采模型请求失败，未生成答案。", "outcome": outcome,
                                "sources": [], "tool_calls": [], "provider_status_code": code,
                                "fallback_used": True, "fallback_reason": f"provider_http_{code}"}
                        yield emit('stage', stages.finish('loop', error=outcome))
                    except httpx.TransportError as exc:
                        done = {"answer": "数采模型连接失败，未生成答案。", "outcome": "provider_unavailable",
                                "sources": [], "tool_calls": [], "error_type": type(exc).__name__,
                                "fallback_used": True, "fallback_reason": "provider_transport_error"}
                        yield emit('stage', stages.finish('loop', error='provider_unavailable'))
                    except Exception as exc:
                        done = {"answer": "数采服务运行失败，未生成答案。", "outcome": "internal_error",
                                "sources": [], "tool_calls": [], "error_type": type(exc).__name__,
                                "fallback_used": True, "fallback_reason": "runtime_error"}
                        yield emit('stage', stages.finish('loop', error='internal_error'))

            if done["outcome"] in RUNTIME_FAILURE_OUTCOMES:
                done["fallback_used"] = True
                done["fallback_reason"] = (
                    done.get("answer_contract", {}).get("validation_error")
                    or done.get("evidence_policy", {}).get("failure_reason")
                or done.get("fallback_reason")
                    or done["outcome"]
                )
            else:
                done.setdefault("fallback_used", False)
                done.setdefault("fallback_reason", None)
            if done.get("attachment_fallback_used"):
                done["fallback_used"] = True
                done["fallback_reason"] = (vision_failure_reason(done)
                                           or done.get("attachment_fallback_reason"))
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
            answer_language = (prepared.schema.answer_language if prepared is not None
                               and prepared.schema is not None else
                               'en' if body.response_language == 'en-US' else 'zh')
            localize_answer(done, answer_language,
                            submission=answer_policy.last_submission if answer_policy else None)
            if prepared is not None and prepared.schema is not None:
                done['request_kind'] = prepared.schema.request_kind
                done['request_schema'] = asdict(prepared.schema)
                done['evidence_requirements'] = copy.deepcopy(plan.requirements)
                done['goal_status'] = {
                    requirement['goal_id']: (done.get('evidence_policy') or {}).get(
                        'requirement_status', {}).get(requirement['id'], 'unknown')
                    for requirement in plan.requirements if 'goal_id' in requirement
                }
            yield emit('stage', stages.observed(
                'coverage', requirement_status=(done.get('evidence_policy') or {}).get(
                    'requirement_status', {}), goal_status=done.get('goal_status', {})))
            yield emit('stage', stages.observed(
                'outcome', outcome=done['outcome'], fallback_used=done['fallback_used']))
            assert_current_access()
            if lease_lost.is_set():
                raise RequestInterrupted("daq_execution_lease_lost")
            working_session = copy.deepcopy(session)
            working_session.append_message("user", body.message)
            working_session.append_message("assistant", done["answer"])
            turn = ChatTurnRecord(
                external_session_id=session.session_id, channel=session.channel,
                question=body.message, answer=done["answer"], trace_id=trace.trace_id,
                turn_index=sum(item["role"] == "user" for item in working_session.messages) - 1,
                sources=done.get("sources", []), stages=stages.events, done=done,
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
                context = DaqContextState(
                    task_context=plan.context.to_checkpoint() if plan else None,
                    roles=({"knowledge_role": binding["role"],
                            "history_floor_message_index": str(history_floor)}
                           if binding else {}),
                    knowledge_release=knowledge_release if binding else None,
                ) if plan or binding else None
                def terminal_events(turn_id):
                    assert_current_access()
                    done["turn_id"] = turn_id
                    return [*frames,
                            sse_event("text_delta", {"delta": done["answer"]}),
                            sse_event("sources", done["sources"]),
                            sse_event("done", done)]

                def write_turn(connection):
                    assert_current_access()
                    turn_id = persistence.save_turn(
                        subject, working_session, turn=turn,
                        attachment_relations=attachment_relations, connection=connection,
                    )
                    assert_current_access()
                    return turn_id

                assert_current_access()
                _, all_frames = durable.complete_turn(
                    subject, reservation,
                    write_turn=write_turn,
                    terminal_events=terminal_events, context=context,
                    expected_context_revision=checkpoint.revision if checkpoint else 0,
                )
                assert_current_access()
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
                                "stages": [{key: event[key] for key in
                                            ('stage', 'stage_id', 'status', 'elapsed_ms')
                                            if key in event} for event in stages.events],
                            })
            for frame in terminal_frames:
                assert_current_access()
                yield frame
        except PlatformIdentityError as exc:
            interrupt("knowledge_authorization_changed")
            trace.finalize({"outcome": "authorization_error", "fallback_used": True,
                            "fallback_reason": exc.code})
            yield emit("stage", {"stage": "authorization", "status": "error",
                                 "reason": exc.code})
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
            def traced_produce():
                with install_trace_ctx(trace):
                    yield from produce()

            for item in iter_with_heartbeat(traced_produce(), heartbeat_interval_seconds=heartbeat_interval_seconds):
                if time.monotonic() >= next_renewal:
                    try:
                        durable.renew(subject, reservation)
                    except (DurableStateError, RequestInterrupted):
                        try:
                            current = durable.reserve(
                                subject, body.client_request_id, payload,
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

    return StreamingResponse(authorized_frames(stream()), media_type="text/event-stream")
