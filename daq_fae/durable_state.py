"""PostgreSQL DAQ execution reservations and versioned domain context.

No lease takeover, automatic rerun, migration or HTTP route is enabled here.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Literal
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field

from src.storage.authenticated_conversations import (
    ConversationContentCodec,
    ConversationStoreError,
    SealedConversationState,
)

from daq_fae.authenticated_persistence import (
    _assert_session_id, _assert_subject, _load_content_codec, _validate_database,
)
from daq_fae.platform_identity import AGENT_ID


class DurableStateError(RuntimeError):
    pass


class RequestConflict(DurableStateError):
    pass


class RequestInterrupted(DurableStateError):
    pass


class SessionBusy(DurableStateError):
    pass


class ContextConflict(DurableStateError):
    pass


class RequirementState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(min_length=1, max_length=128)
    capability: str = Field(min_length=1, max_length=128)
    critical: bool = True
    status: Literal["satisfied", "missing", "conflict", "unknown"] = "unknown"
    source_ids: list[str] = Field(default_factory=list, max_length=100)


class DaqContextState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal[1] = 1
    topic_id: str | None = None
    acquisition_task: str | None = None
    device_variants: dict[str, str] = Field(default_factory=dict)
    device_combinations: list[dict[str, str]] = Field(default_factory=list, max_length=100)
    roles: dict[str, str] = Field(default_factory=dict)
    platform: str | None = None
    connections: list[str] = Field(default_factory=list, max_length=100)
    power_constraints: list[str] = Field(default_factory=list, max_length=100)
    viewer_version: str | None = None
    sdk_version: str | None = None
    firmware_versions: dict[str, str] = Field(default_factory=dict)
    storage_format: str | None = None
    recording_format: str | None = None
    tried_steps: list[str] = Field(default_factory=list, max_length=200)
    error_source_ids: list[str] = Field(default_factory=list, max_length=100)
    error_summaries: list[str] = Field(default_factory=list, max_length=100)
    pending_questions: list[str] = Field(default_factory=list, max_length=100)
    previous_conclusions: list[str] = Field(default_factory=list, max_length=100)
    requirements: list[RequirementState] = Field(default_factory=list, max_length=200)
    actual_capabilities: list[str] = Field(default_factory=list, max_length=100)
    runtime_release: str | None = None
    knowledge_release: str | None = None


@dataclass(frozen=True)
class ContextCheckpoint:
    state: DaqContextState
    revision: int


@dataclass(frozen=True)
class RequestReservation:
    status: Literal["execute", "in_progress", "replay", "interrupted"]
    session_id: str
    client_request_id: str
    execution_token: str | None = field(default=None, repr=False)
    events: tuple[str, ...] = ()
    reason: str | None = None


def _json(payload):
    try:
        value = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError):
        raise DurableStateError("daq_request_payload_invalid") from None
    if len(value) > 2 * 1024 * 1024:
        raise DurableStateError("daq_state_payload_too_large")
    return value


def _scope(subject, client_request_id):
    _assert_subject(subject)
    if not isinstance(client_request_id, str) or not 1 <= len(client_request_id) <= 128:
        raise DurableStateError("daq_client_request_id_invalid")
    return (AGENT_ID, subject.subject_id, client_request_id)


def _lease(seconds):
    if isinstance(seconds, bool) or not isinstance(seconds, int) or not 1 <= seconds <= 3600:
        raise DurableStateError("daq_request_lease_invalid")


def _session(session_id):
    try:
        _assert_session_id(session_id)
    except ConversationStoreError:
        raise DurableStateError("daq_session_access_denied") from None


class DaqDurableState:
    def __init__(self, database_url: str, *, codec: ConversationContentCodec):
        self.database_url = database_url
        self.codec = codec

    @contextmanager
    def _transaction(self):
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                yield connection
        except psycopg.Error:
            raise DurableStateError("daq_durable_storage_unavailable") from None

    @staticmethod
    def _lock(connection, value):
        connection.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (value,))

    def _bind_session(self, connection, subject, session_id):
        _session(session_id)
        self._lock(connection, f"daq-session:{session_id}")
        connection.execute("""insert into daq_state_sessions (agent_id, external_session_id, owner_subject_id)
                              values (%s,%s,%s) on conflict do nothing""", (AGENT_ID, session_id, subject.subject_id))
        row = connection.execute("select owner_subject_id from daq_state_sessions where agent_id=%s and external_session_id=%s",
                                 (AGENT_ID, session_id)).fetchone()
        if row["owner_subject_id"] != subject.subject_id:
            raise DurableStateError("daq_session_access_denied")

    def _seal(self, binding, payload):
        _json(payload)
        try:
            return self.codec.seal_state(binding, payload)
        except ConversationStoreError:
            raise DurableStateError("daq_state_encrypt_failed") from None

    def _unseal(self, binding, row, prefix):
        try:
            sealed = SealedConversationState(bytes(row[f"{prefix}_ciphertext"]), row[f"{prefix}_key_version"],
                                             bytes(row[f"{prefix}_sha256"]))
            payload = self.codec.unseal_state(binding, sealed)
            if hashlib.sha256(_json(payload)).digest() != sealed.state_sha256:
                raise ValueError
            return payload
        except (ConversationStoreError, ValueError, TypeError):
            raise DurableStateError("daq_state_checkpoint_invalid") from None

    @staticmethod
    def _request_binding(subject, key):
        return f"daq-request:{AGENT_ID}:{subject.subject_id}:{key}"

    def _reservation(self, subject, row):
        state = row["state"]
        events = ()
        if state == "completed":
            payload = self._unseal(self._request_binding(subject, row["client_request_id"]), row, "terminal")
            events = tuple(payload["events"])
            self._validate_terminal(row["external_session_id"], events)
        return RequestReservation(
            {"running": "in_progress", "completed": "replay", "interrupted": "interrupted"}[state],
            row["external_session_id"], row["client_request_id"], events=events,
            reason=row["interruption_reason"],
        )

    def reserve(self, subject, client_request_id, payload, session_id, *, lease_seconds=600):
        scope = _scope(subject, client_request_id)
        _session(session_id)
        _lease(lease_seconds)
        digest = hashlib.sha256(_json(payload)).digest()
        with self._transaction() as connection:
            self._lock(connection, f"daq-request:{subject.subject_id}:{client_request_id}")
            row = connection.execute("""select *, lease_expires_at <= clock_timestamp() as expired
                from daq_request_ledger where agent_id=%s and owner_subject_id=%s and client_request_id=%s
                for update""", scope).fetchone()
            if row:
                if bytes(row["payload_sha256"]) != digest:
                    raise RequestConflict("daq_request_payload_conflict")
                if row["state"] == "running" and row["expired"]:
                    connection.execute("""update daq_request_ledger set state='interrupted',
                        interruption_reason='execution_lease_expired', updated_at=clock_timestamp()
                        where agent_id=%s and owner_subject_id=%s and client_request_id=%s""", scope)
                    row.update(state="interrupted", interruption_reason="execution_lease_expired")
                return self._reservation(subject, row)
            self._bind_session(connection, subject, session_id)
            connection.execute("""update daq_request_ledger set state='interrupted',
                interruption_reason='execution_lease_expired', updated_at=clock_timestamp()
                where agent_id=%s and external_session_id=%s and state='running'
                and lease_expires_at <= clock_timestamp()""", (AGENT_ID, session_id))
            if connection.execute("""select 1 from daq_request_ledger where agent_id=%s
                and external_session_id=%s and state='running'""", (AGENT_ID, session_id)).fetchone():
                raise SessionBusy("daq_session_busy")
            token = str(uuid4())
            connection.execute("""insert into daq_request_ledger
                (agent_id,owner_subject_id,client_request_id,external_session_id,payload_sha256,
                 execution_token,state,lease_expires_at) values (%s,%s,%s,%s,%s,%s,'running',
                 clock_timestamp()+make_interval(secs => %s))""", (*scope, session_id, digest, token, lease_seconds))
            return RequestReservation("execute", session_id, client_request_id, token)

    def _execution(self, connection, subject, reservation):
        scope = _scope(subject, reservation.client_request_id)
        _session(reservation.session_id)
        self._lock(connection, f"daq-session:{reservation.session_id}")
        row = connection.execute("""select *,lease_expires_at <= clock_timestamp() as expired
            from daq_request_ledger where agent_id=%s and owner_subject_id=%s and client_request_id=%s
            for update""", scope).fetchone()
        if (row is None or reservation.execution_token is None
                or str(row["execution_token"]) != reservation.execution_token
                or row["external_session_id"] != reservation.session_id):
            raise RequestInterrupted("daq_execution_not_owned")
        if row["state"] == "interrupted" or (row["state"] == "running" and row["expired"]):
            raise RequestInterrupted("daq_execution_interrupted")
        return row, scope

    @staticmethod
    def _validate_terminal(session_id, events):
        if not isinstance(events, (list, tuple)) or not events or not all(isinstance(item, str) for item in events):
            raise DurableStateError("daq_terminal_invalid")
        names = []
        payload = None
        try:
            for frame in events:
                lines = frame.strip().splitlines()
                event_lines = [line[7:] for line in lines if line.startswith("event: ")]
                data_lines = [line[6:] for line in lines if line.startswith("data: ")]
                if not frame.endswith("\n\n") or len(event_lines) != 1 or len(data_lines) != 1:
                    raise ValueError
                names.append(event_lines[0])
                if names[-1] == "done":
                    payload = json.loads(next(line[6:] for line in lines if line.startswith("data: ")))
            if (names.count("done") != 1 or names[-1] != "done" or not isinstance(payload, dict)
                    or payload.get("session_id") != session_id or payload.get("agent_id") != AGENT_ID
                    or not payload.get("trace_id") or not payload.get("outcome")
                    or not isinstance(payload.get("fallback_used"), bool)):
                raise ValueError
        except (ValueError, StopIteration):
            raise DurableStateError("daq_terminal_invalid") from None
        _json({"events": events})

    def finish(self, subject, reservation, events, *, context=None, expected_context_revision=None):
        self._validate_terminal(reservation.session_id, events)
        with self._transaction() as connection:
            row, scope = self._execution(connection, subject, reservation)
            if row["state"] == "completed":
                existing = self._reservation(subject, row)
                if existing.events != tuple(events):
                    raise RequestConflict("daq_terminal_conflict")
                return existing
            if context is not None:
                self._save_context(connection, subject, reservation.session_id, context, expected_context_revision)
            sealed = self._seal(self._request_binding(subject, reservation.client_request_id), {"events": list(events)})
            connection.execute("""update daq_request_ledger set state='completed', terminal_ciphertext=%s,
                terminal_key_version=%s,terminal_sha256=%s,updated_at=clock_timestamp()
                where agent_id=%s and owner_subject_id=%s and client_request_id=%s""",
                               (sealed.ciphertext, sealed.key_version, sealed.state_sha256, *scope))
            return RequestReservation("replay", reservation.session_id, reservation.client_request_id, events=tuple(events))

    def interrupt(self, subject, reservation, *, reason="client_disconnected"):
        if not isinstance(reason, str) or re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", reason) is None:
            raise DurableStateError("daq_interruption_reason_invalid")
        scope = _scope(subject, reservation.client_request_id)
        with self._transaction() as connection:
            row = connection.execute("""select * from daq_request_ledger where agent_id=%s
                and owner_subject_id=%s and client_request_id=%s for update""", scope).fetchone()
            if (row is None or reservation.execution_token is None
                    or str(row["execution_token"]) != reservation.execution_token):
                raise RequestInterrupted("daq_execution_not_owned")
            if row["state"] == "completed":
                raise RequestConflict("daq_terminal_already_completed")
            connection.execute("""update daq_request_ledger set state='interrupted',interruption_reason=%s,
                updated_at=clock_timestamp() where agent_id=%s and owner_subject_id=%s and client_request_id=%s""",
                               (reason, *scope))

    def renew(self, subject, reservation, *, lease_seconds=600):
        _lease(lease_seconds)
        with self._transaction() as connection:
            row, scope = self._execution(connection, subject, reservation)
            if row["state"] != "running":
                raise RequestInterrupted("daq_execution_interrupted")
            connection.execute("""update daq_request_ledger set lease_expires_at=
                clock_timestamp()+make_interval(secs => %s),updated_at=clock_timestamp()
                where agent_id=%s and owner_subject_id=%s and client_request_id=%s""", (lease_seconds, *scope))
        return True

    def _save_context(self, connection, subject, session_id, state, expected_revision):
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
            raise DurableStateError("daq_context_revision_invalid")
        state = DaqContextState.model_validate(state)
        self._bind_session(connection, subject, session_id)
        row = connection.execute("""select revision from daq_context_checkpoints where agent_id=%s
            and external_session_id=%s and owner_subject_id=%s for update""", (AGENT_ID, session_id, subject.subject_id)).fetchone()
        revision = row["revision"] if row else 0
        if revision != expected_revision:
            raise ContextConflict("daq_context_revision_conflict")
        revision += 1
        binding = f"daq-context:{AGENT_ID}:{subject.subject_id}:{session_id}:r{revision}"
        sealed = self._seal(binding, state.model_dump())
        connection.execute("""insert into daq_context_checkpoints
            (agent_id,owner_subject_id,external_session_id,revision,state_ciphertext,state_key_version,state_sha256)
            values (%s,%s,%s,%s,%s,%s,%s) on conflict (agent_id,external_session_id) do update
            set revision=excluded.revision,state_ciphertext=excluded.state_ciphertext,
            state_key_version=excluded.state_key_version,state_sha256=excluded.state_sha256,
            updated_at=clock_timestamp()""",
                           (AGENT_ID, subject.subject_id, session_id, revision, sealed.ciphertext, sealed.key_version, sealed.state_sha256))
        return revision

    def save_context(self, subject, session_id, state, *, expected_revision):
        _assert_subject(subject)
        _session(session_id)
        with self._transaction() as connection:
            return self._save_context(connection, subject, session_id, state, expected_revision)

    def load_context(self, subject, session_id):
        _assert_subject(subject)
        _session(session_id)
        with self._transaction() as connection:
            row = connection.execute("""select * from daq_context_checkpoints where agent_id=%s
                and owner_subject_id=%s and external_session_id=%s""", (AGENT_ID, subject.subject_id, session_id)).fetchone()
        if row is None:
            return None
        binding = f"daq-context:{AGENT_ID}:{subject.subject_id}:{session_id}:r{row['revision']}"
        state = DaqContextState.model_validate(self._unseal(binding, row, "state"))
        return ContextCheckpoint(state, row["revision"])


def configure_durable_state(app, *, environ=None):
    env = os.environ if environ is None else environ
    database_url = env.get("DAQ_DATABASE_URL", "")
    keyring_file = env.get("DAQ_AUTHENTICATED_CONTENT_KEYRING_FILE", "")
    if not database_url or not keyring_file:
        raise DurableStateError("daq_durable_configuration_missing")
    _validate_database(database_url, env.get("DATABASE_URL"))
    codec = _load_content_codec(env)
    state = DaqDurableState(database_url, codec=codec)
    app.state.daq_durable_state = state
    return state
