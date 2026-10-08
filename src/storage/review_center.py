from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import psycopg
from psycopg.rows import dict_row


class ReviewCenterUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class TurnDecisionInput:
    turn_id: str
    priority: str
    review_status: str
    failure_layer: str | None = None
    failure_reason: str = ""
    expected_answer_notes: str = ""
    corrected_answer: str = ""
    reviewer: str = "web-reviewer"
    should_add_to_eval: bool = False
    should_update_knowledge: bool = False
    should_create_qa: bool = False
    knowledge_area: str = ""
    gap_summary: str = ""
    qa_tags: dict[str, list[str]] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class DisabledReviewCenterStore:
    def _raise(self):
        raise ReviewCenterUnavailable("review center requires PostgreSQL")

    def list_sessions(self, **kwargs):
        self._raise()

    def get_session(self, external_session_id: str):
        self._raise()

    def save_turn_decision(self, decision: TurnDecisionInput):
        self._raise()

    def list_qa_items(self, **kwargs):
        self._raise()

    def list_feedback(self, **kwargs):
        self._raise()

    def create_qa_item(self, payload: dict[str, Any]):
        self._raise()

    def update_qa_item(self, item_id: str, payload: dict[str, Any]):
        self._raise()

    def promote_qa_item(self, item_id: str):
        self._raise()

    def metrics(self):
        self._raise()


class PostgresReviewCenterStore:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def _connect(self):
        return psycopg.connect(self._database_url, row_factory=dict_row)

    def list_sessions(
        self,
        *,
        rating: str | None = None,
        review_status: str | None = None,
        priority: str | None = None,
        channel: str | None = None,
        q: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        where = ["1 = 1"]
        params: list[Any] = []
        if rating:
            where.append(
                """
                exists (
                    select 1 from turn_feedback f
                    where f.external_session_id = s.external_session_id
                      and f.rating = %s
                )
                """
            )
            params.append(rating)
        if review_status:
            where.append("latest_review.review_status = %s")
            params.append(review_status)
        if priority:
            where.append("latest_review.priority = %s")
            params.append(priority)
        if channel:
            where.append("s.channel = %s")
            params.append(channel)
        if q:
            where.append(
                """
                exists (
                    select 1 from chat_turns qt
                    where qt.external_session_id = s.external_session_id
                      and (qt.question ilike %s or qt.answer ilike %s)
                )
                """
            )
            needle = f"%{q}%"
            params.extend([needle, needle])

        page_limit = max(1, min(limit, 200))
        page_offset = max(offset, 0)
        count_sql = f"""
            select count(*)::int as total
            from chat_sessions s
            left join lateral (
                select r.priority, r.review_status, r.created_at
                from turn_reviews r
                join chat_turns rt on rt.id = r.turn_id
                where rt.external_session_id = s.external_session_id
                order by r.created_at desc
                limit 1
            ) latest_review on true
            where {' and '.join(where)}
        """
        sql = f"""
            select
                s.external_session_id,
                s.channel,
                first_turn.question as first_question,
                count(distinct t.id)::int as turns_count,
                max(t.created_at) as last_turn_at,
                count(distinct f.id) filter (where f.rating = 'bad')::int as bad_feedback_count,
                count(distinct t.id) filter (where t.fallback_used)::int as fallback_count,
                latest_review.priority as latest_priority,
                latest_review.review_status as latest_review_status
            from chat_sessions s
            left join chat_turns t on t.external_session_id = s.external_session_id
            left join turn_feedback f on f.turn_id = t.id
            left join lateral (
                select question
                from chat_turns ft
                where ft.external_session_id = s.external_session_id
                order by ft.turn_index asc, ft.created_at asc
                limit 1
            ) first_turn on true
            left join lateral (
                select r.priority, r.review_status, r.created_at
                from turn_reviews r
                join chat_turns rt on rt.id = r.turn_id
                where rt.external_session_id = s.external_session_id
                order by r.created_at desc
                limit 1
            ) latest_review on true
            where {' and '.join(where)}
            group by
                s.external_session_id,
                s.channel,
                first_turn.question,
                latest_review.priority,
                latest_review.review_status,
                latest_review.created_at
            order by
                count(distinct f.id) filter (where f.rating = 'bad') desc,
                latest_review.created_at desc nulls last,
                max(t.created_at) desc nulls last
            limit %s offset %s
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(count_sql, tuple(params))
                count_row = cur.fetchone() or {"total": 0}
                cur.execute(sql, tuple(params + [page_limit, page_offset]))
                return {
                    "items": list(cur.fetchall()),
                    "total": int(count_row["total"]),
                    "limit": page_limit,
                    "offset": page_offset,
                }

    def list_feedback(
        self,
        *,
        rating: str | None = "bad",
        channel: str | None = None,
        q: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        where = ["1 = 1"]
        params: list[Any] = []
        if rating:
            where.append("f.rating = %s")
            params.append(rating)
        if channel:
            where.append("f.channel = %s")
            params.append(channel)
        if q:
            where.append("(t.question ilike %s or t.answer ilike %s or f.comment ilike %s)")
            needle = f"%{q}%"
            params.extend([needle, needle, needle])

        page_limit = max(1, min(limit, 200))
        page_offset = max(offset, 0)
        count_sql = f"""
            select count(*)::int as total
            from turn_feedback f
            left join chat_turns t on t.id = f.turn_id
            where {' and '.join(where)}
        """
        sql = f"""
            select
                f.id as feedback_id,
                f.turn_id,
                f.external_session_id,
                f.rating,
                f.reason_code,
                f.comment,
                f.trace_id,
                f.created_at,
                f.channel,
                f.metadata ->> 'synced_from' as synced_from,
                t.question,
                t.answer,
                t.outcome,
                t.fallback_used,
                latest_review.review_status as latest_review_status,
                latest_review.priority as latest_priority
            from turn_feedback f
            left join chat_turns t on t.id = f.turn_id
            left join lateral (
                select r.review_status, r.priority
                from turn_reviews r
                where r.turn_id = f.turn_id
                order by r.created_at desc
                limit 1
            ) latest_review on true
            where {' and '.join(where)}
            order by f.created_at desc
            limit %s offset %s
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(count_sql, tuple(params))
                count_row = cur.fetchone() or {"total": 0}
                cur.execute(sql, tuple(params + [page_limit, page_offset]))
                return {
                    "items": list(cur.fetchall()),
                    "total": int(count_row["total"]),
                    "limit": page_limit,
                    "offset": page_offset,
                }

    def get_session(self, external_session_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select *
                    from chat_sessions
                    where external_session_id = %s
                    """,
                    (external_session_id,),
                )
                session = cur.fetchone()
                if not session:
                    return None

                cur.execute(
                    """
                    select *
                    from chat_turns
                    where external_session_id = %s
                    order by turn_index asc, created_at asc
                    """,
                    (external_session_id,),
                )
                turns = list(cur.fetchall())

                cur.execute(
                    """
                    select *
                    from turn_feedback
                    where external_session_id = %s
                    order by created_at asc
                    """,
                    (external_session_id,),
                )
                feedback = list(cur.fetchall())

                cur.execute(
                    """
                    select r.*
                    from turn_reviews r
                    join chat_turns t on t.id = r.turn_id
                    where t.external_session_id = %s
                    order by r.created_at asc
                    """,
                    (external_session_id,),
                )
                reviews = list(cur.fetchall())

        return {
            "session": dict(session),
            "turns": turns,
            "feedback": feedback,
            "reviews": reviews,
        }

    def save_turn_decision(self, decision: TurnDecisionInput) -> dict[str, Any]:
        reviewer = decision.reviewer.strip() or "web-reviewer"
        with self._connect() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        insert into turn_reviews (
                            turn_id, priority, review_status, failure_layer,
                            failure_reason, expected_answer_notes, corrected_answer,
                            reviewer, should_add_to_eval, should_update_knowledge, metadata
                        )
                        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                        returning id as review_id
                        """,
                        (
                            decision.turn_id,
                            decision.priority,
                            decision.review_status,
                            decision.failure_layer,
                            decision.failure_reason,
                            decision.expected_answer_notes,
                            decision.corrected_answer,
                            reviewer,
                            decision.should_add_to_eval,
                            decision.should_update_knowledge,
                            json.dumps(decision.metadata, ensure_ascii=False),
                        ),
                    )
                    row = cur.fetchone()
                    review_id = str(row["review_id"])
                    if decision.should_add_to_eval:
                        self._insert_eval_candidate(cur, decision)
                    if decision.should_update_knowledge:
                        self._insert_knowledge_task(cur, decision)
                    if decision.should_create_qa:
                        self._insert_qa_item(cur, decision, reviewer)
                conn.commit()
                return {"review_id": review_id}
            except Exception:
                conn.rollback()
                raise

    def _insert_eval_candidate(self, cur, decision: TurnDecisionInput) -> None:
        case_json = {
            "source": "review_center",
            "turn_id": decision.turn_id,
            "priority": decision.priority,
            "failure_layer": decision.failure_layer,
            "failure_reason": decision.failure_reason,
            "expected_answer_notes": decision.expected_answer_notes,
            "corrected_answer": decision.corrected_answer,
        }
        cur.execute(
            """
            insert into eval_candidates (
                turn_id, candidate_status, testset_name, case_json
            )
            values (%s, 'candidate', 'feedback_candidates', %s::jsonb)
            """,
            (decision.turn_id, json.dumps(case_json, ensure_ascii=False)),
        )

    def _insert_knowledge_task(self, cur, decision: TurnDecisionInput) -> None:
        cur.execute(
            """
            insert into knowledge_improvement_tasks (
                turn_id, task_status, knowledge_area, gap_summary, proposed_source
            )
            values (%s, 'open', %s, %s, %s)
            """,
            (
                decision.turn_id,
                decision.knowledge_area or decision.failure_layer or "unknown",
                decision.gap_summary or decision.failure_reason,
                "review_center",
            ),
        )

    def _insert_qa_item(self, cur, decision: TurnDecisionInput, reviewer: str) -> None:
        tags = decision.qa_tags or {}
        source_type = "corrected_feedback" if decision.corrected_answer.strip() else "chat_turn"
        cur.execute(
            """
            insert into qa_review_items (
                source_type, source_ref, turn_id, question, original_answer,
                reviewed_answer, product_tags, scenario_tags, technical_tags,
                sdk_tags, review_status, reviewer, review_notes, metadata
            )
            select
                %s, t.trace_id, t.id, t.question, t.answer, %s,
                %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb,
                'candidate', %s, %s, %s::jsonb
            from chat_turns t
            where t.id = %s
            """,
            (
                source_type,
                decision.corrected_answer,
                json.dumps(tags.get("product", []), ensure_ascii=False),
                json.dumps(tags.get("scenario", []), ensure_ascii=False),
                json.dumps(tags.get("technical", []), ensure_ascii=False),
                json.dumps(tags.get("sdk", []), ensure_ascii=False),
                reviewer,
                decision.expected_answer_notes or decision.failure_reason,
                json.dumps({"source": "review_center"}, ensure_ascii=False),
                decision.turn_id,
            ),
        )

    def list_qa_items(
        self,
        *,
        source_type: str | None = None,
        review_status: str | None = None,
        tag: str | None = None,
        q: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        where = ["1 = 1"]
        params: list[Any] = []
        if source_type:
            where.append("source_type = %s")
            params.append(source_type)
        if review_status:
            where.append("review_status = %s")
            params.append(review_status)
        if tag:
            where.append(
                """
                (
                    product_tags ? %s or scenario_tags ? %s
                    or technical_tags ? %s or sdk_tags ? %s
                )
                """
            )
            params.extend([tag, tag, tag, tag])
        if q:
            where.append("(question ilike %s or reviewed_answer ilike %s or original_answer ilike %s)")
            needle = f"%{q}%"
            params.extend([needle, needle, needle])
        page_limit = max(1, min(limit, 200))
        page_offset = max(offset, 0)
        count_sql = f"""
            select count(*)::int as total
            from qa_review_items
            where {' and '.join(where)}
        """
        sql = f"""
            select *
            from qa_review_items
            where {' and '.join(where)}
            order by updated_at desc
            limit %s offset %s
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(count_sql, tuple(params))
                count_row = cur.fetchone() or {"total": 0}
                cur.execute(sql, tuple(params + [page_limit, page_offset]))
                return {
                    "items": list(cur.fetchall()),
                    "total": int(count_row["total"]),
                    "limit": page_limit,
                    "offset": page_offset,
                }

    def create_qa_item(self, payload: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    insert into qa_review_items (
                        source_type, source_ref, turn_id, question, original_answer,
                        reviewed_answer, product_tags, scenario_tags, technical_tags,
                        sdk_tags, quality_score, review_status, reviewer, review_notes, metadata
                    )
                    values (
                        %s, %s, %s, %s, %s, %s,
                        %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb,
                        %s, %s, %s, %s, %s::jsonb
                    )
                    returning id
                    """,
                    (
                        payload.get("source_type", "manual"),
                        payload.get("source_ref", ""),
                        payload.get("turn_id"),
                        payload.get("question", ""),
                        payload.get("original_answer", ""),
                        payload.get("reviewed_answer", ""),
                        json.dumps(payload.get("product_tags", []), ensure_ascii=False),
                        json.dumps(payload.get("scenario_tags", []), ensure_ascii=False),
                        json.dumps(payload.get("technical_tags", []), ensure_ascii=False),
                        json.dumps(payload.get("sdk_tags", []), ensure_ascii=False),
                        payload.get("quality_score"),
                        payload.get("review_status", "pending"),
                        payload.get("reviewer") or "web-reviewer",
                        payload.get("review_notes", ""),
                        json.dumps(payload.get("metadata", {}), ensure_ascii=False),
                    ),
                )
                row = cur.fetchone()
                return {"id": str(row["id"])}

    def upsert_qa_items(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        if not items:
            return {"count": 0, "ids": []}
        ids: list[str] = []
        with self._connect() as conn:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        insert into qa_review_items (
                            source_type, source_ref, turn_id, question, original_answer,
                            reviewed_answer, product_tags, scenario_tags, technical_tags,
                            sdk_tags, quality_score, review_status, reviewer, review_notes,
                            should_export_to_knowledge, metadata
                        )
                        select
                            source_type, source_ref, turn_id, question, original_answer,
                            reviewed_answer, product_tags, scenario_tags, technical_tags,
                            sdk_tags, quality_score, review_status, reviewer, review_notes,
                            should_export_to_knowledge, metadata
                        from jsonb_to_recordset(%s::jsonb) as x(
                            source_type text,
                            source_ref text,
                            turn_id uuid,
                            question text,
                            original_answer text,
                            reviewed_answer text,
                            product_tags jsonb,
                            scenario_tags jsonb,
                            technical_tags jsonb,
                            sdk_tags jsonb,
                            quality_score integer,
                            review_status text,
                            reviewer text,
                            review_notes text,
                            should_export_to_knowledge boolean,
                            metadata jsonb
                        )
                        on conflict (source_type, source_ref) where source_ref <> ''
                        do update set
                            question = excluded.question,
                            original_answer = excluded.original_answer,
                            reviewed_answer = excluded.reviewed_answer,
                            product_tags = excluded.product_tags,
                            scenario_tags = excluded.scenario_tags,
                            technical_tags = excluded.technical_tags,
                            sdk_tags = excluded.sdk_tags,
                            quality_score = excluded.quality_score,
                            reviewer = excluded.reviewer,
                            review_notes = excluded.review_notes,
                            metadata = excluded.metadata,
                            updated_at = now()
                        returning id
                        """,
                        (json.dumps(items, ensure_ascii=False),),
                    )
                    rows = cur.fetchall()
                    ids = [str(row["id"]) for row in rows]
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return {"count": len(ids), "ids": ids}

    def update_qa_item(self, item_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        allowed = {
            "reviewed_answer",
            "product_tags",
            "scenario_tags",
            "technical_tags",
            "sdk_tags",
            "quality_score",
            "review_status",
            "review_notes",
            "should_export_to_knowledge",
        }
        sets = []
        params: list[Any] = []
        for key in allowed:
            if key not in payload:
                continue
            if key.endswith("_tags"):
                sets.append(f"{key} = %s::jsonb")
                params.append(json.dumps(payload[key], ensure_ascii=False))
            else:
                sets.append(f"{key} = %s")
                params.append(payload[key])
        if not sets:
            return {"id": item_id, "updated": False}
        sets.append("updated_at = now()")
        params.append(item_id)
        sql = f"""
            update qa_review_items
            set {', '.join(sets)}
            where id = %s
            returning id
        """
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                row = cur.fetchone()
                return {"id": str(row["id"]), "updated": True} if row else {"id": item_id, "updated": False}

    def promote_qa_item(self, item_id: str) -> dict[str, Any]:
        return self.update_qa_item(item_id, {
            "review_status": "candidate",
            "should_export_to_knowledge": True,
        })

    def metrics(self) -> dict[str, Any]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select
                        (
                            select count(*)
                            from turn_feedback f
                            where f.rating = 'bad'
                              and not exists (
                                  select 1
                                  from turn_reviews r
                                  where r.turn_id = f.turn_id
                              )
                        )::int as bad_feedback_pending,
                        (
                            select count(*)
                            from qa_review_items
                            where review_status = 'pending'
                        )::int as qa_pending,
                        (
                            select count(*)
                            from turn_reviews
                            where priority in ('P0', 'P1')
                              and review_status in ('pending', 'reviewed', 'fix_planned')
                        )::int as p0_p1_open,
                        (
                            select count(*)
                            from qa_review_items
                            where should_export_to_knowledge
                               or review_status = 'candidate'
                        )::int as qa_candidates
                    """
                )
                row = cur.fetchone()
                return dict(row or {})
