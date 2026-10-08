from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from src.storage.review_center import ReviewCenterUnavailable, TurnDecisionInput


class DecisionFlags(BaseModel):
    add_to_eval: bool = False
    update_knowledge: bool = False
    create_qa: bool = False


class TurnDecisionRequest(BaseModel):
    priority: str = Field(pattern="^P[0-3]$")
    review_status: str
    failure_layer: str | None = None
    failure_reason: str = ""
    expected_answer_notes: str = ""
    corrected_answer: str = ""
    reviewer: str = "web-reviewer"
    flags: DecisionFlags = Field(default_factory=DecisionFlags)
    knowledge_area: str = ""
    gap_summary: str = ""
    qa_tags: dict[str, list[str]] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


def _store(request: Request):
    return request.app.state.review_center_store


def _unavailable(exc: ReviewCenterUnavailable):
    raise HTTPException(status_code=503, detail=str(exc)) from exc


def register_review_routes(app: FastAPI) -> None:
    @app.get("/review/sessions")
    async def list_sessions(
        request: Request,
        rating: str | None = None,
        review_status: str | None = None,
        priority: str | None = None,
        channel: str | None = None,
        q: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ):
        try:
            return _store(request).list_sessions(
                rating=rating,
                review_status=review_status,
                priority=priority,
                channel=channel,
                q=q,
                limit=limit,
                offset=offset,
            )
        except ReviewCenterUnavailable as exc:
            _unavailable(exc)

    @app.get("/review/sessions/{external_session_id}")
    async def get_session(external_session_id: str, request: Request):
        try:
            session = _store(request).get_session(external_session_id)
        except ReviewCenterUnavailable as exc:
            _unavailable(exc)
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        return session

    @app.post("/review/turns/{turn_id}/decision")
    async def save_turn_decision(turn_id: str, body: TurnDecisionRequest, request: Request):
        reviewer = body.reviewer.strip() or "web-reviewer"
        decision = TurnDecisionInput(
            turn_id=turn_id,
            priority=body.priority,
            review_status=body.review_status,
            failure_layer=body.failure_layer,
            failure_reason=body.failure_reason,
            expected_answer_notes=body.expected_answer_notes,
            corrected_answer=body.corrected_answer,
            reviewer=reviewer,
            should_add_to_eval=body.flags.add_to_eval,
            should_update_knowledge=body.flags.update_knowledge,
            should_create_qa=body.flags.create_qa,
            knowledge_area=body.knowledge_area,
            gap_summary=body.gap_summary,
            qa_tags=body.qa_tags,
            metadata=body.metadata,
        )
        try:
            return _store(request).save_turn_decision(decision)
        except ReviewCenterUnavailable as exc:
            _unavailable(exc)

    @app.post("/review/turns/{turn_id}/export-replay")
    async def export_replay_placeholder(turn_id: str):
        return {"ok": False, "turn_id": turn_id, "detail": "export replay is handled by ops script in MVP"}

    @app.get("/review/qa-items")
    async def list_qa_items(
        request: Request,
        source_type: str | None = None,
        review_status: str | None = None,
        tag: str | None = None,
        q: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ):
        try:
            return _store(request).list_qa_items(
                source_type=source_type,
                review_status=review_status,
                tag=tag,
                q=q,
                limit=limit,
                offset=offset,
            )
        except ReviewCenterUnavailable as exc:
            _unavailable(exc)

    @app.post("/review/qa-items")
    async def create_qa_item(body: dict[str, Any], request: Request):
        try:
            return _store(request).create_qa_item(body)
        except ReviewCenterUnavailable as exc:
            _unavailable(exc)

    @app.patch("/review/qa-items/{item_id}")
    async def update_qa_item(item_id: str, body: dict[str, Any], request: Request):
        try:
            return _store(request).update_qa_item(item_id, body)
        except ReviewCenterUnavailable as exc:
            _unavailable(exc)

    @app.post("/review/qa-items/{item_id}/promote")
    async def promote_qa_item(item_id: str, request: Request):
        try:
            return _store(request).promote_qa_item(item_id)
        except ReviewCenterUnavailable as exc:
            _unavailable(exc)

    @app.get("/review/metrics")
    async def metrics(request: Request):
        try:
            return _store(request).metrics()
        except ReviewCenterUnavailable as exc:
            _unavailable(exc)

    @app.get("/review/feedback")
    async def list_feedback(
        request: Request,
        rating: str | None = "bad",
        channel: str | None = None,
        q: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ):
        try:
            return _store(request).list_feedback(
                rating=rating,
                channel=channel,
                q=q,
                limit=limit,
                offset=offset,
            )
        except ReviewCenterUnavailable as exc:
            _unavailable(exc)
