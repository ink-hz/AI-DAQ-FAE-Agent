# DAQ durable request ledger and context

DDL: `migrations/daq/001_durable_request_context.sql`. Install explicitly on the
reviewed dedicated DAQ PostgreSQL database with `ON_ERROR_STOP`; application
startup does not run migrations. Example operator command after approving the
DB target: `psql "$DAQ_DATABASE_URL" -v ON_ERROR_STOP=1 -f migrations/daq/001_durable_request_context.sql`.
It creates DAQ session-owner bindings, request reservations and sealed context
checkpoints. Runtime DB role grants and live deployment remain separate gates.

## Integration API

`configure_durable_state(app, environ=...)` uses the same mandatory
`DAQ_DATABASE_URL` and `DAQ_AUTHENTICATED_CONTENT_KEYRING_FILE` as the authenticated
adapter. The validated content-key loader and DB isolation checks are reused.
It returns `DaqDurableState`, also at `app.state.daq_durable_state`.

- `reserve(subject, client_request_id, payload, session_id, lease_seconds=600)`
  returns a `RequestReservation`: status `execute`, `in_progress`, `replay` or
  `interrupted`; canonical session ID; client request ID; completed SSE `events`;
  optional reason. Only `execute` receives a private execution token. Never send
  that token to clients. Nonempty client IDs are bounded to 128 characters.
- `finish(subject, reservation, events, context=None, expected_context_revision=None)`
  seals a complete named SSE sequence. Exactly one final `done` is required with
  matching DAQ Agent/session, trace, outcome and fallback state. Optional context
  and request completion commit in the same PostgreSQL transaction. Repeating
  identical finish is safe; changing an already completed terminal conflicts.
- `renew(subject, reservation, lease_seconds=600)` extends an owned, unexpired
  execution lease. Late, interrupted or completed execution cannot be revived.
- `interrupt(subject, reservation, reason='client_disconnected')` records an
  explicit interruption code. It cannot rewrite a completed request.
- `load_context(subject, session_id)` returns `ContextCheckpoint(state, revision)`
  or `None` for absent/foreign-owner context, without disclosing its existence.
- `save_context(subject, session_id, state, expected_revision=...)` uses compare
  and swap: revision zero means absent; a successful write returns the new
  revision. Concurrent stale updates raise `ContextConflict`.

Fingerprint the original validated request payload, including its caller-supplied
session ID (possibly null). Do not put a newly generated candidate session ID in
that fingerprint. On duplicate reservation the canonical stored session ID wins.
Authenticate and prove main conversation ownership with the adapter before
supplying an existing session ID to the ledger. Ledger session-owner binding adds
a second boundary but does not replace the shared conversation repository.

Key scope is trusted Agent + owner + client request ID. Advisory transaction locks
serialize reservation decisions; one running request per DAQ session prevents
cross-worker concurrent context updates. Duplicate payload changes raise
`RequestConflict`; another active request for the session raises `SessionBusy`.
An expired execution becomes `interrupted`; it is never automatically taken over
or rerun. Completed requests replay their sealed terminal after process restart.
Only fingerprints and state metadata are cleartext; replay/context bodies use
shared AES-GCM with separate domain/Agent/owner/session/revision AAD bindings.

## Context semantics and transaction boundary

`DaqContextState` is a closed version-1 schema: acquisition task, device
combinations/variants, roles, platform, connections/power, Viewer/SDK/firmware,
storage/recording format, tried steps, error source IDs/summaries, pending questions,
previous conclusions, requirement coverage, actual capabilities and release IDs.
Unknown fields/versions fail rather than silently discarding state. A save replaces
the whole schema; root handles topic reset by constructing a fresh state.

Stored requirement statuses and prior conclusions are historical consultation
state, not new authoritative evidence. New turns must collect current governed
evidence and must not inherit `satisfied` as a release/permission-independent gate.
Visibility checks remain before retrieval/tool returns. Root must supply approved
and redacted SSE events; encrypted replay is not permission to retain raw image
payloads or credential-bearing metadata.

The shared chat turn/checkpoint transaction remains separate from durable ledger
finish/context. Root should reserve, execute, successfully save the shared turn,
then finish with DAQ context, then expose success. A failure between those commits
must stay interrupted/failed; never automatically rerun or count it as complete.
A unified turn+request+domain-context commit would need another shared transaction
extension. This batch does not claim that stronger atomicity.

Map typed conflicts/busy/interrupted to explicit API outcomes; map
`DurableStateError('daq_durable_storage_unavailable')` to storage failure. Do not
replace it with process memory/SQLite. Root owns heartbeat renewal, live stream
cancellation, review of interrupted records and terminal emission. Operational
retention/purge policy is not implemented; do not delete a replay key and then
silently execute the same client request again.

## Verification

TDD initial eight contracts failed on missing implementation; strict final-frame
validation was also caught failing before its fix. Twelve ledger/context contracts
run actual PostgreSQL SQL and DDL in a fresh temporary cluster using a unique Unix
socket, a dedicated test role/database and no TCP listener. They verify concurrent
single execution, instance-restart replay, payload conflicts, owner/Agent isolation,
expired/interrupted non-rerun, session busy, terminal shape, lease renewal, encrypted
bodies, schema closure, ciphertext tampering and context/finish atomic rollback.
The cluster is stopped and removed after the test module. These are Dev contracts,
not production evals. Focused ledger + adapter run: 35 passed; Ruff and whitespace
checks passed. Main application wiring, live DAQ migration/role acceptance,
Platform registration and deployment remain pending.
