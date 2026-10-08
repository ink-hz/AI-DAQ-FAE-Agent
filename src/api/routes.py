"""路由:/chat /feedback /history。"""
import json
import logging
import os
from datetime import datetime, timezone
from time import monotonic

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from src.agent.session import session_length_hint
from src.agent.tracing import install_trace_ctx
from src.api.stream import StreamHeartbeat, iter_with_heartbeat, sse_event
from src.attachments.models import AttachmentDescriptor, AttachmentError
from src.storage.authenticated_conversations import (
    ConversationNotFound,
    ConversationStoreError,
)
from src.storage.data_flywheel import (
    ChatTurnRecord,
    FeedbackRecord,
    TurnResolutionUnavailable,
)

logger = logging.getLogger(__name__)

_ATTACHMENT_BEARER_KEYS = {"attachment_id", "attachment_ids"}
_ATTACHMENT_CONTENT_KEYS = {
    "base64",
    "bytes",
    "content",
    "full_text",
    "filename",
    "display_name",
    "original_filename",
    "ocr_text",
    "path",
    "provenance",
    "provider_payload",
    "raw",
}


def _strip_attachment_bearers(value):
    if isinstance(value, dict):
        return {
            key: _strip_attachment_bearers(item)
            for key, item in value.items()
            if key not in _ATTACHMENT_BEARER_KEYS
        }
    if isinstance(value, list):
        return [_strip_attachment_bearers(item) for item in value]
    return value


def _redact_attachment_done(value):
    """Keep coverage/telemetry while removing attachment bodies and local paths."""
    if isinstance(value, dict):
        return {
            key: _redact_attachment_done(item)
            for key, item in value.items()
            if key not in _ATTACHMENT_BEARER_KEYS
            and key not in _ATTACHMENT_CONTENT_KEYS
        }
    if isinstance(value, list):
        return [_redact_attachment_done(item) for item in value]
    return value


def _sanitize_attachment_sources_for_live(sources: list[dict]) -> list[dict]:
    return [
        _redact_attachment_done(dict(source))
        if source.get("type") in {"attachment", "user_attachment"}
        else _strip_attachment_bearers(dict(source))
        for source in sources
    ]


def _redact_attachment_sources_for_persistence(sources: list[dict]) -> list[dict]:
    safe: list[dict] = []
    attachment_number = 0
    for source in sources:
        item = _strip_attachment_bearers(dict(source))
        if item.get("type") in {"attachment", "user_attachment"}:
            attachment_number += 1
            item["title"] = f"用户附件 {attachment_number}"
            item.pop("display_name", None)
            item.pop("filename", None)
        safe.append(item)
    return safe


class ChatRequest(BaseModel):
    session_id: str | None = None
    message: str
    channel: str = "fae"
    client_request_id: str | None = Field(default=None, max_length=128)
    attachment_ids: list[str] = Field(default_factory=list, max_length=5)


class FeedbackRequest(BaseModel):
    session_id: str
    message_index: int = Field(default=0, ge=0)
    rating: str            # "good" | "bad"
    comment: str = ""
    turn_id: str | None = None
    trace_id: str | None = None
    reason_code: str | None = None


def _planned_capabilities_from_done(done_data: dict) -> list[str]:
    explicit = done_data.get("planned_capabilities")
    if isinstance(explicit, list):
        return [str(item) for item in explicit if str(item)]
    plan = done_data.get("plan")
    if not isinstance(plan, dict):
        return []
    planned: list[str] = []
    primary = plan.get("primary_capability")
    extras = plan.get("extra_capabilities") or []
    if primary:
        planned.append(str(primary))
    if isinstance(extras, list):
        planned.extend(str(item) for item in extras)
    return planned


def _turn_index_from_session(session) -> int:
    user_count = len([m for m in session.messages if m.get("role") == "user"])
    assistant_count = len([m for m in session.messages if m.get("role") == "assistant"])
    if user_count:
        return max(user_count - 1, 0)
    if assistant_count:
        return max(assistant_count - 1, 0)
    return 0


def _authenticated_subject(request: Request):
    return getattr(request.state, "platform_identity", None)


def _authentication_mode_for_subject(subject) -> str:
    return (
        "platform_enterprise"
        if subject.subject_type == "enterprise_member"
        else "platform_partner"
    )


def _assert_session_owner(session, request: Request) -> None:
    subject = _authenticated_subject(request)
    if session.authentication_mode == "public_customer":
        if subject is not None:
            raise HTTPException(403, "session access denied")
        return
    if subject is None or session.owner_subject_id != str(subject.subject_id):
        raise HTTPException(404, "session not found")


def _conversation_repository(request: Request):
    """The durable conversation store, or an explicit unavailability failure.

    Generic authenticated ownership is only enforceable against durable storage.
    When it is not wired, the request fails loudly instead of downgrading to an
    anonymous session, issuing a replacement session id, inventing an empty
    page, or reporting an owned conversation as missing.
    """
    repository = getattr(
        request.app.state, "authenticated_conversation_repository", None
    )
    if repository is None:
        raise HTTPException(503, "conversation_storage_unavailable")
    return repository


def _sse_heartbeat_interval_seconds() -> float:
    raw = os.getenv("SSE_HEARTBEAT_INTERVAL_SECONDS", "10")
    try:
        value = float(raw)
    except ValueError:
        return 10.0
    return value if value > 0 else 10.0


def register_routes(app: FastAPI) -> None:
    @app.post("/chat")
    async def chat(body: ChatRequest, request: Request):
        orchestrator = request.app.state.orchestrator
        store = request.app.state.session_store
        data_flywheel_store = request.app.state.data_flywheel_store
        chat_concurrency_gate = request.app.state.chat_concurrency_gate

        subject = _authenticated_subject(request)
        # An authenticated turn is only answered once it is durably checkpointed
        # under its owner: that checkpoint is what makes the conversation
        # restorable and what every later ownership check reads. So the store is
        # required for every authenticated turn -- continuation and brand new
        # conversation alike -- and the request fails here, before a session id
        # is minted or a model call is spent, rather than streaming an answer
        # that would disappear with the in-memory cache. Anonymous and public
        # conversations are ownerless and keep the generic path.
        repository = _conversation_repository(request) if subject is not None else None

        session = store.get(body.session_id) if body.session_id else None
        created_session = False
        if session is None:
            if body.session_id and subject is not None:
                # A caller-supplied session id for an authenticated subject is
                # never silently re-created: it is resolved against the
                # durable repository, or the request fails closed.
                try:
                    restored = repository.load_for_subject(
                        body.session_id, subject.subject_id
                    )
                except ConversationNotFound:
                    raise HTTPException(404, "session not found") from None
                # A concurrent restore may already have seated this conversation.
                # The live entry wins, and ownership is re-checked against
                # whatever the cache actually holds.
                session = store.adopt(restored)
                _assert_session_owner(session, request)
            else:
                created_session = True
                channel = body.channel if body.channel in ("fae", "ecom") else "fae"
                session = store.create(
                    channel=channel,
                    authentication_mode=(
                        _authentication_mode_for_subject(subject)
                        if subject is not None
                        else "public_customer"
                    ),
                    internal_user_id=(
                        str(subject.internal_user_id)
                        if subject is not None and subject.internal_user_id is not None
                        else None
                    ),
                    owner_subject_id=(
                        str(subject.subject_id) if subject is not None else None
                    ),
                )
        else:
            _assert_session_owner(session, request)
        explicit_descriptors: list[AttachmentDescriptor] = []
        if body.attachment_ids:
            try:
                manifests = request.app.state.attachment_store.bind_many(
                    body.attachment_ids,
                    session.session_id,
                    owner_subject_id=session.owner_subject_id,
                )
            except AttachmentError as exc:
                if created_session:
                    store.delete(session.session_id)
                status = {
                    "attachment_not_found": 404,
                    "attachment_expired": 410,
                    "attachment_deleted": 410,
                    "attachment_session_mismatch": 409,
                    "attachment_not_ready": 409,
                    "attachment_owner_mismatch": 403,
                }.get(exc.code, 422)
                return JSONResponse(
                    {"code": exc.code, "message": "附件不可用于当前会话"},
                    status_code=status,
                )
            explicit_descriptors = [
                AttachmentDescriptor.from_manifest(item) for item in manifests
            ]
            session.bind_attachments(
                explicit_descriptors, explicit_ids=body.attachment_ids,
            )
        else:
            session.bind_attachments([], explicit_ids=None)
        visible_attachments = session.visible_attachments()
        request_metadata = (
            {"client_request_id": body.client_request_id}
            if body.client_request_id
            else {}
        )
        if visible_attachments:
            request_metadata.update({
                "contains_attachment": True,
                "attachment_count": len(visible_attachments),
                "attachment_kinds": sorted({item.kind for item in visible_attachments}),
                "review_eligible": False,
                "knowledge_promotion_eligible": False,
            })
        # The owner projection is persistence metadata, not stream metadata: the
        # client already knows who it is, so echoing owner ids on every SSE event
        # and trace stage would spread identity further than it needs to go.
        persisted_metadata = dict(request_metadata)
        if session.owner_subject_id is not None:
            persisted_metadata["authentication_mode"] = session.authentication_mode
            persisted_metadata["owner_subject_id"] = session.owner_subject_id
            persisted_metadata["owner_subject_type"] = session.owner_subject_type

        def gen():
            question_at = datetime.now(timezone.utc)
            yield sse_event("session", {
                "session_id": session.session_id,
                **request_metadata,
            })
            recorder = request.app.state.trace_recorder
            ctx = recorder.start_trace("chat_request", {
                "session_id": session.session_id,
                "user_message": body.message,
                "channel": session.channel,
                **request_metadata,
            })
            text_len = 0
            done_data: dict = {}
            fallback_used = False
            fallback_reason: str | None = None
            error_text: str | None = None
            stream_started_at = monotonic()
            turn_started_at = monotonic()
            heartbeat_interval_seconds = _sse_heartbeat_interval_seconds()
            answer_parts: list[str] = []
            sources: list[dict] = []
            stages: list[dict] = []
            gate_acquired = False
            gate_release_deferred = False

            persistence_attempted = False

            def persist_done_turn(done_payload: dict) -> str | None:
                """Persist this turn at most once, and never truncate the stream.

                The outer crash handler also finalizes a `done` payload, so the
                attempt is latched: a turn is written once or not at all, never
                twice. A storage failure is reported in the terminal payload
                instead of escaping into the SSE generator, where it would cut
                the response off mid-stream and look like a transport fault.
                """
                nonlocal persistence_attempted
                if persistence_attempted:
                    return None
                persistence_attempted = True
                try:
                    return _write_done_turn(done_payload)
                except Exception as exc:
                    logger.exception(
                        "conversation persistence failed for session %s",
                        session.session_id,
                    )
                    done_payload["persistence_failed"] = True
                    done_payload["persistence_failed_reason"] = (
                        str(exc)
                        if isinstance(exc, ConversationStoreError)
                        else "conversation_persistence_failed"
                    )
                    return None

            def _write_done_turn(done_payload: dict) -> str | None:
                capability_coverage = done_payload.get("capability_coverage")
                if not isinstance(capability_coverage, dict):
                    capability_coverage = {}
                answer_at = datetime.now(timezone.utc)
                attachment_relations = (
                    request.app.state.attachment_archive_service.prepare_turn(
                    [item.attachment_id for item in visible_attachments],
                    explicit_attachment_ids=body.attachment_ids,
                    answer_at=answer_at,
                    )
                )
                turn = ChatTurnRecord(
                    external_session_id=session.session_id,
                    channel=session.channel,
                    question=body.message,
                    answer="".join(answer_parts),
                    trace_id=ctx.trace_id,
                    turn_index=_turn_index_from_session(session),
                    user_id=session.internal_user_id,
                    sources=_redact_attachment_sources_for_persistence(sources),
                    stages=_strip_attachment_bearers(stages),
                    done=(
                        _redact_attachment_done(dict(done_payload))
                        if visible_attachments
                        else _strip_attachment_bearers(dict(done_payload))
                    ),
                    planned_capabilities=_planned_capabilities_from_done(done_payload),
                    capability_coverage=capability_coverage,
                    fallback_used=fallback_used,
                    fallback_reason=fallback_reason,
                    outcome=done_payload.get("outcome"),
                    duration_ms=int((monotonic() - turn_started_at) * 1000),
                    question_at=question_at,
                    answer_at=answer_at,
                    metadata=persisted_metadata,
                )
                if session.owner_subject_id is not None:
                    # Authenticated turns have exactly one system of record: the
                    # owner projection, turn and sealed checkpoint commit in one
                    # transaction. Writing the turn again through the generic
                    # flywheel store here would be a second, uncoordinated write.
                    # `repository` is the one the route already required, so an
                    # owned turn can never silently take the generic path.
                    return repository.save_turn_and_checkpoint(
                        session,
                        turn=turn,
                        attachment_relations=attachment_relations,
                    )
                return data_flywheel_store.record_chat_turn(
                    turn, attachment_relations=attachment_relations,
                )

            try:
                try:
                    if not chat_concurrency_gate.acquire(timeout=0):
                        chat_concurrency_gate.wait_started()
                        queue_started_at = monotonic()
                        try:
                            snapshot = chat_concurrency_gate.snapshot()
                            queue_stage = {
                                "stage": "queue",
                                "status": "started",
                                "message": "当前并发请求较多,已进入排队等待",
                                "agent": "Runtime Queue",
                                "metadata": {
                                    "max_concurrent": snapshot.max_concurrent,
                                    "active": snapshot.active,
                                    "waiting": snapshot.waiting,
                                },
                                "session_id": session.session_id,
                                "trace_id": ctx.trace_id,
                                "elapsed_ms": int((monotonic() - stream_started_at) * 1000),
                                **request_metadata,
                            }
                            stages.append(queue_stage)
                            yield sse_event("stage", queue_stage)
                            while not gate_acquired:
                                gate_acquired = chat_concurrency_gate.acquire(
                                    timeout=heartbeat_interval_seconds,
                                )
                                if not gate_acquired:
                                    yield sse_event(
                                        "heartbeat",
                                        {
                                            "session_id": session.session_id,
                                            "trace_id": ctx.trace_id,
                                            "elapsed_ms": int((monotonic() - stream_started_at) * 1000),
                                            "queue_wait_ms": int((monotonic() - queue_started_at) * 1000),
                                            **request_metadata,
                                        },
                                    )
                        finally:
                            chat_concurrency_gate.wait_finished()
                        snapshot = chat_concurrency_gate.snapshot()
                        queue_completed_stage = {
                            "stage": "queue",
                            "status": "completed",
                            "message": "已获得执行槽位,开始处理问题",
                            "agent": "Runtime Queue",
                            "metadata": {
                                "max_concurrent": snapshot.max_concurrent,
                                "active": snapshot.active,
                                "waiting": snapshot.waiting,
                                "queue_wait_ms": int((monotonic() - queue_started_at) * 1000),
                            },
                            "session_id": session.session_id,
                            "trace_id": ctx.trace_id,
                            "elapsed_ms": int((monotonic() - stream_started_at) * 1000),
                            **request_metadata,
                        }
                        stages.append(queue_completed_stage)
                        yield sse_event("stage", queue_completed_stage)
                    else:
                        gate_acquired = True

                    raw_events = orchestrator.handle_stream(
                        session_id=session.session_id,
                        user_message=body.message,
                        required_attachment_source_ids=[
                            item.source_id for item in explicit_descriptors
                        ],
                        required_image_source_ids=[
                            item.source_id
                            for item in explicit_descriptors
                            if item.kind == "image"
                        ],
                    )

                    def traced_events():
                        try:
                            while True:
                                with install_trace_ctx(ctx):
                                    try:
                                        yield next(raw_events)
                                    except StopIteration:
                                        return
                        finally:
                            chat_concurrency_gate.release()

                    gate_release_deferred = True

                    for item in iter_with_heartbeat(
                        traced_events(),
                        heartbeat_interval_seconds=heartbeat_interval_seconds,
                    ):
                        if isinstance(item, StreamHeartbeat):
                            yield sse_event(
                                "heartbeat",
                                {
                                    "session_id": session.session_id,
                                    "trace_id": ctx.trace_id,
                                    "elapsed_ms": int((monotonic() - stream_started_at) * 1000),
                                    "heartbeat_count": item.count,
                                    **request_metadata,
                                },
                            )
                            continue
                        ev = item
                        if ev.kind == "text_delta":
                            delta = ev.data.get("delta", "") if isinstance(ev.data, dict) else ""
                            text_len += len(delta)
                            answer_parts.append(delta)
                            yield sse_event(ev.kind, ev.data)
                        elif ev.kind == "sources":
                            raw_sources = list(ev.data) if isinstance(ev.data, list) else []
                            sources = (
                                _sanitize_attachment_sources_for_live(raw_sources)
                                if visible_attachments
                                else _strip_attachment_bearers(raw_sources)
                            )
                            yield sse_event(ev.kind, sources)
                        elif ev.kind == "done":
                            raw_done = dict(ev.data) if isinstance(ev.data, dict) else {}
                            done_data = (
                                _redact_attachment_done(raw_done)
                                if visible_attachments
                                else _strip_attachment_bearers(raw_done)
                            )
                            # Pull fallback signals if orchestrator surfaced them
                            if done_data.get("fallback_used"):
                                fallback_used = True
                                fallback_reason = done_data.get("fallback_reason")
                            if done_data.get("error"):
                                error_text = done_data.get("error")
                            done_data["session_id"] = session.session_id
                            done_data["trace_id"] = ctx.trace_id
                            done_data["text_len"] = text_len
                            done_data["fallback_used"] = fallback_used
                            done_data["fallback_reason"] = fallback_reason
                            done_data["active_attachment_count"] = len(visible_attachments)
                            done_data["active_attachment_source_ids"] = [
                                item.source_id for item in visible_attachments
                            ]
                            hint = session_length_hint(session)
                            if hint:
                                done_data["session_hint"] = hint
                            done_data.update(request_metadata)
                            turn_id = persist_done_turn(done_data)
                            if turn_id:
                                done_data["turn_id"] = turn_id
                            yield sse_event("done", done_data)
                        elif ev.kind == "stage":
                            stage_data = dict(ev.data) if isinstance(ev.data, dict) else {"data": ev.data}
                            stage_data["session_id"] = session.session_id
                            stage_data["trace_id"] = ctx.trace_id
                            stage_data.setdefault(
                                "elapsed_ms",
                                int((monotonic() - stream_started_at) * 1000),
                            )
                            stage_data.update(request_metadata)
                            stages.append(stage_data)
                            yield sse_event("stage", stage_data)
                        else:
                            yield sse_event(ev.kind, ev.data)
                except StopIteration:
                    pass
                except Exception as e:
                    # LLM 不是稳定 JSON 机器:异常不能让 chunked stream 砍掉,
                    # 否则前端 / eval 看到 ChunkedEncodingError 整批挂。
                    # 改成 yield 一个干净的 error done 事件。
                    logger.exception(
                        "chat stream crashed for session %s", session.session_id,
                    )
                    error_text = str(e)
                    fallback_used = True
                    fallback_reason = "stream_crash"
                    fallback_text = "抱歉,服务暂时异常,请稍后重试或联系 FAE。"
                    text_len += len(fallback_text)
                    answer_parts.append(fallback_text)
                    yield sse_event("text_delta", {"delta": fallback_text})
                    sources = []
                    yield sse_event("sources", [])
                    done_data = {
                        "risk_notes": [],
                        "template": "refusal",
                        "bucket": "out_of_scope",
                        "error": error_text,
                        "session_id": session.session_id,
                        "trace_id": ctx.trace_id,
                        "text_len": text_len,
                        "fallback_used": True,
                        "fallback_reason": "stream_crash",
                        "active_attachment_count": len(visible_attachments),
                        "active_attachment_source_ids": [
                            item.source_id for item in visible_attachments
                        ],
                        **request_metadata,
                    }
                    turn_id = persist_done_turn(done_data)
                    if turn_id:
                        done_data["turn_id"] = turn_id
                    yield sse_event("done", done_data)
            finally:
                if gate_acquired and not gate_release_deferred:
                    chat_concurrency_gate.release()
                ctx.finalize({
                    "text_len": text_len,
                    "bucket": done_data.get("bucket"),
                    "template": done_data.get("template"),
                    "fallback_used": fallback_used,
                    "fallback_reason": fallback_reason,
                    "error": error_text,
                })

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.post("/feedback")
    async def feedback(body: FeedbackRequest, request: Request):
        data_flywheel_store = request.app.state.data_flywheel_store
        store = request.app.state.session_store
        session = store.get(body.session_id)
        if session is not None:
            _assert_session_owner(session, request)
        else:
            subject = _authenticated_subject(request)
            if subject is not None:
                # The conversation may only have aged out of the cache, so the
                # durable owner-scoped record decides: it is restored, or the
                # request fails closed. Never re-created, never downgraded.
                repository = _conversation_repository(request)
                try:
                    session = store.adopt(
                        repository.load_for_subject(
                            body.session_id, subject.subject_id
                        )
                    )
                except ConversationNotFound:
                    raise HTTPException(404, "session not found or expired") from None
                _assert_session_owner(session, request)
        owner_subject_id = session.owner_subject_id if session is not None else None
        # One resolution path for every caller. `body.turn_id` is a candidate to
        # be matched inside the caller's ownership scope, never an authorization
        # token, so a named turn belonging to somebody else cannot be targeted.
        target_named = body.turn_id is not None or body.trace_id is not None
        try:
            resolved_turn_id = data_flywheel_store.resolve_turn_id(
                external_session_id=body.session_id,
                message_index=body.message_index,
                trace_id=body.trace_id,
                candidate_turn_id=body.turn_id,
                owner_subject_id=owner_subject_id,
                internal_user_id=(
                    session.internal_user_id if session is not None else None
                ),
                require_unowned=owner_subject_id is None,
            )
        except TurnResolutionUnavailable:
            # The store could not answer "does this turn exist, and is it
            # yours?", so no ownership check happened. That is not a 404: an
            # unreachable or unconfigured store would otherwise reject every
            # feedback submission as a missing target.
            if owner_subject_id is not None:
                # An owned target has no safe answer without the check, so the
                # request fails closed and nothing is written.
                raise HTTPException(
                    503, "feedback_target_resolution_unavailable"
                ) from None
            if not target_named:
                raise HTTPException(
                    400, "feedback cannot be linked to a chat turn"
                ) from None
            # Public anonymous feedback keeps its long-standing behaviour: the
            # submission is accepted *unlinked*. The caller-supplied turn id is
            # not trusted as a target -- linking it would assert an ownership
            # relation nothing verified -- but the feedback itself is not thrown
            # away just because no database is configured.
            logger.warning(
                "feedback target resolution unavailable; accepting public "
                "feedback unlinked for session %s",
                body.session_id,
            )
            resolved_turn_id = None
        else:
            if resolved_turn_id is None and target_named:
                # An identifier was named and did not resolve inside this scope.
                # Existence stays hidden: no distinction between absent and
                # foreign.
                raise HTTPException(404, "feedback target not found")
            if resolved_turn_id is None:
                raise HTTPException(400, "feedback cannot be linked to a chat turn")
        feedback_id = data_flywheel_store.record_feedback(FeedbackRecord(
            external_session_id=body.session_id,
            trace_id=body.trace_id or "",
            rating=body.rating,
            reason_code=body.reason_code,
            comment=body.comment,
            channel="fae",
            turn_id=resolved_turn_id,
            message_index=body.message_index,
            user_id=(session.internal_user_id if session is not None else None),
            metadata=(
                {
                    # The owner subject is the authorization key, so every
                    # authenticated feedback row carries it -- partner rows have
                    # no internal user id to fall back on.
                    "authentication_mode": session.authentication_mode,
                    "owner_subject_id": owner_subject_id,
                    "owner_subject_type": session.owner_subject_type,
                }
                if session is not None and owner_subject_id is not None
                else {}
            ),
        ))
        log_dir = request.app.state.log_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "session_id": body.session_id,
            "message_index": body.message_index,
            "turn_id": resolved_turn_id,
            "trace_id": body.trace_id,
            "rating": body.rating,
            "reason_code": body.reason_code,
            "comment": body.comment,
        }
        with (log_dir / "feedback.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return {"ok": True, "feedback_id": feedback_id}

    @app.get("/history")
    async def history(session_id: str, request: Request):
        store = request.app.state.session_store
        s = store.get(session_id)
        if s is None:
            raise HTTPException(404, "session not found or expired")
        _assert_session_owner(s, request)
        return {
            "session_id": s.session_id,
            "channel": s.channel,
            "messages": s.messages,
            "current_schema": s.current_schema.model_dump() if s.current_schema else None,
        }

    @app.get("/authenticated/conversations")
    async def list_authenticated_conversations(
        request: Request, cursor: str | None = None, limit: int = 30
    ):
        subject = _authenticated_subject(request)
        if subject is None:
            raise HTTPException(401, "authentication required")
        repository = _conversation_repository(request)
        try:
            page = repository.list_for_subject(
                subject.subject_id, limit=limit, cursor=cursor
            )
        except ValueError:
            raise HTTPException(400, "invalid pagination request") from None
        return {
            "items": [
                {
                    "session_id": item.external_session_id,
                    "title": item.title,
                    "channel": item.channel,
                    "created_at": item.created_at.isoformat(),
                    "last_active_at": item.last_active_at.isoformat(),
                }
                for item in page.items
            ],
            "next_cursor": page.next_cursor,
        }

    @app.get("/authenticated/conversations/{external_session_id}")
    async def get_authenticated_conversation(
        external_session_id: str, request: Request
    ):
        subject = _authenticated_subject(request)
        if subject is None:
            raise HTTPException(401, "authentication required")
        repository = _conversation_repository(request)
        try:
            session = repository.load_for_subject(
                external_session_id, subject.subject_id
            )
        except ConversationNotFound:
            raise HTTPException(404, "conversation not found") from None
        # Ownership is settled by the load above, so attachment rows are only
        # read for a conversation this subject owns.
        attachments = repository.list_attachments_for_subject(
            external_session_id, subject.subject_id
        )
        return {
            "session_id": session.session_id,
            "channel": session.channel,
            "messages": session.messages,
            "current_schema": (
                session.current_schema.model_dump()
                if session.current_schema
                else None
            ),
            # A non-bearer projection: `source_id` identifies the attachment in
            # this conversation without granting access to its bytes, and no
            # filesystem path or raw subject identity is exposed.
            "attachments": [
                {
                    "turn_index": item.turn_index,
                    "source_id": item.source_id,
                    "display_name": item.display_name,
                    "kind": item.kind,
                    "media_type": item.media_type,
                    "size_bytes": item.size_bytes,
                    "direction": item.direction,
                    "ordinal": item.ordinal,
                    "association_kind": item.association_kind,
                    "status": item.status,
                    "created_at": item.created_at.isoformat(),
                }
                for item in attachments
            ],
        }
