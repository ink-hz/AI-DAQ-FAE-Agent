"""Single-host DAQ Dev conversation, request replay, and feedback storage.

This is not the Platform-owned production conversation repository. It keeps the
localhost workbench usable across process restarts and never authenticates a user.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from uuid import uuid4

from src.agent.session import Session


class LocalStateError(RuntimeError):
    pass


class LocalStateStore:
    def __init__(self, path: Path, *, agent_id: str, ttl_seconds: int = 3600):
        self.path = path
        self.agent_id = agent_id
        self.ttl_seconds = ttl_seconds
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript("""
                create table if not exists sessions (
                    session_id text primary key, agent_id text not null,
                    channel text not null, created_at real not null,
                    last_active real not null, messages_json text not null
                );
                create table if not exists task_contexts (
                    session_id text primary key, agent_id text not null,
                    checkpoint_json text not null
                );
                create table if not exists turns (
                    turn_id text primary key, agent_id text not null,
                    session_id text not null, turn_index integer not null,
                    trace_id text not null, done_json text not null,
                    unique(session_id, turn_index)
                );
                create table if not exists requests (
                    client_request_id text primary key, agent_id text not null,
                    fingerprint_json text not null, session_id text not null,
                    state text not null, events_json text,
                    created_at real not null
                );
                create table if not exists feedback (
                    feedback_id text primary key, agent_id text not null,
                    turn_id text not null, session_id text not null,
                    rating text not null, reason_code text,
                    comment text not null, created_at real not null
                );
            """)

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def load_session(self, session_id: str) -> Session | None:
        with self._connect() as connection:
            row = connection.execute(
                "select * from sessions where session_id = ? and agent_id = ?",
                (session_id, self.agent_id),
            ).fetchone()
        if row is None or time.time() - row["last_active"] > self.ttl_seconds:
            return None
        messages = json.loads(row["messages_json"])
        if (row["channel"] not in {"fae", "ecom"}
                or not isinstance(messages, list)
                or any(not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}
                       or not isinstance(item.get("content"), str) for item in messages)):
            raise LocalStateError("session_record_invalid")
        return Session(
            session_id=row["session_id"], channel=row["channel"],
            created_at=row["created_at"], last_active=row["last_active"],
            messages=messages,
        )

    def load_context(self, session_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "select checkpoint_json from task_contexts where session_id=? and agent_id=?",
                (session_id, self.agent_id),
            ).fetchone()
        return json.loads(row["checkpoint_json"]) if row else None

    def save_turn(self, session: Session, done: dict, *, context_checkpoint: dict | None = None) -> str:
        if session.owner_subject_id is not None:
            raise LocalStateError("authenticated_session_requires_platform_repository")
        turn_id = str(uuid4())
        done["turn_id"] = turn_id
        turn_index = sum(item["role"] == "user" for item in session.messages) - 1
        with self._connect() as connection:
            written = connection.execute("""
                insert into sessions(session_id, agent_id, channel, created_at, last_active, messages_json)
                values (?, ?, ?, ?, ?, ?)
                on conflict(session_id) do update set
                    last_active=excluded.last_active, messages_json=excluded.messages_json
                where sessions.agent_id=excluded.agent_id and sessions.channel=excluded.channel
            """, (
                session.session_id, self.agent_id, session.channel, session.created_at,
                session.last_active, json.dumps(session.messages, ensure_ascii=False),
            )).rowcount
            if written != 1:
                raise LocalStateError("session_agent_or_channel_conflict")
            connection.execute("""
                insert into turns(turn_id, agent_id, session_id, turn_index, trace_id, done_json)
                values (?, ?, ?, ?, ?, ?)
            """, (
                turn_id, self.agent_id, session.session_id, turn_index,
                done["trace_id"], json.dumps(done, ensure_ascii=False),
            ))
            if context_checkpoint is not None:
                changed = connection.execute("""
                    insert into task_contexts(session_id, agent_id, checkpoint_json)
                    values (?, ?, ?)
                    on conflict(session_id) do update set checkpoint_json=excluded.checkpoint_json
                    where task_contexts.agent_id=excluded.agent_id
                """, (
                    session.session_id, self.agent_id,
                    json.dumps(context_checkpoint, ensure_ascii=False),
                )).rowcount
                if changed != 1:
                    raise LocalStateError("context_agent_conflict")
        return turn_id

    def lookup_request(self, request_id: str, fingerprint: tuple) -> tuple[str, list[str] | None] | None:
        with self._connect() as connection:
            row = connection.execute(
                "select * from requests where client_request_id = ?",
                (request_id,),
            ).fetchone()
        if row is None:
            return None
        if row["agent_id"] != self.agent_id or row["fingerprint_json"] != _fingerprint(fingerprint):
            raise LocalStateError("client_request_id_conflict")
        return row["session_id"], (
            json.loads(row["events_json"]) if row["state"] == "finished" else None
        )

    def reserve_request(self, request_id: str, fingerprint: tuple, session_id: str) -> None:
        with self._connect() as connection:
            try:
                connection.execute("""
                    insert into requests(client_request_id, agent_id, fingerprint_json,
                                         session_id, state, created_at)
                    values (?, ?, ?, ?, 'in_progress', ?)
                """, (request_id, self.agent_id, _fingerprint(fingerprint), session_id, time.time()))
            except sqlite3.IntegrityError as exc:
                raise LocalStateError("client_request_id_conflict") from exc

    def finish_request(self, request_id: str, events: list[str]) -> None:
        with self._connect() as connection:
            changed = connection.execute("""
                update requests set state='finished', events_json=?
                where client_request_id=? and agent_id=? and state='in_progress'
            """, (json.dumps(events), request_id, self.agent_id)).rowcount
        if changed != 1:
            raise LocalStateError("request_record_missing")

    def record_feedback(self, *, session_id: str, turn_index: int,
                        turn_id: str | None, trace_id: str | None,
                        rating: str, comment: str, reason_code: str | None) -> str | None:
        with self._connect() as connection:
            row = connection.execute("""
                select turn_id, trace_id from turns
                where session_id=? and agent_id=? and turn_index=?
            """, (session_id, self.agent_id, turn_index)).fetchone()
            if row is None or (turn_id and turn_id != row["turn_id"]) or (
                trace_id and trace_id != row["trace_id"]
            ):
                return None
            feedback_id = str(uuid4())
            connection.execute("""
                insert into feedback(feedback_id, agent_id, turn_id, session_id,
                                     rating, reason_code, comment, created_at)
                values (?, ?, ?, ?, ?, ?, ?, ?)
            """, (feedback_id, self.agent_id, row["turn_id"], session_id,
                  rating, reason_code, comment, time.time()))
            return feedback_id


def _fingerprint(value: tuple) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
