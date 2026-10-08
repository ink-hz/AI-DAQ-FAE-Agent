# DAQ authenticated persistence adapter

This batch provides an explicit adapter; it does not modify `daq_fae/app.py`,
register HTTP routes, run migrations or enable a Platform deployment.

## Assembly and route wiring

Call `configure_authenticated_persistence(app, runtime_release=...,
knowledge_release=..., environ=...)` only for the authenticated service path.
Configuration is mandatory and never downgrades to the anonymous SQLite store.
Use the returned adapter or `app.state.daq_authenticated_persistence`:

- `create_session(subject, store=...)` mints `daq:<uuid>` and binds the trusted
  DAQ enterprise member. Pass the resulting session to the turn executor.
- `load_session(subject, session_id, store=...)` resolves durable ownership,
  verifies DAQ namespace and checks any live cache adoption again.
- `history(subject, session_id)`, `list_conversations(subject, cursor=..., limit=...)`
  and `conversation_detail(subject, session_id)` supply history/navigation.
  Detail checks ownership before reading non-bearer attachment projections.
- `save_turn(subject, session, turn=ChatTurnRecord, attachment_relations=...)`
  writes the turn, owner projection and sealed checkpoint through the shared
  repository's one transaction. Do not additionally write through the generic
  data flywheel store or anonymous SQLite store. The caller must update messages
  and context before saving and emit `done.turn_id` only after success.
- `record_feedback(subject, session_id=..., message_index=..., rating=...,
  comment=..., turn_id=..., trace_id=..., reason_code=...)` owner-scopes target
  resolution before feedback insertion. Named turn/trace identifiers must agree.
  `message_index` follows the inherited UI message-index convention: the shared
  resolver maps it to `turn_index` via integer division by two.
- `review_for(subject)` checks the separate reviewer allowlist and returns the
  shared review store. Every review route must invoke this gate before delegation.
  Do not register the generic review routes against an unguarded raw store.

`app.state.authenticated_conversation_repository` exposes the underlying
repository for compatibility, but it is not an Agent namespace boundary. Route
wiring must use the adapter methods for externally supplied session identifiers.

A rejected/missing conversation remains non-enumerating `ConversationNotFound`;
foreign/inactive identities are `PlatformIdentityError`. Storage failures are
explicit `ConversationStoreError` and must produce a failed persistence terminal
or HTTP 503, never success or an anonymous fallback. No DB-error text containing
connection details is returned. HTTP mapping and terminal handling belong to
application integration.

## Storage and keys

Required trusted config:

- `DAQ_DATABASE_URL`: PostgreSQL URL with explicit DAQ user and database. No
  fallback to `DATABASE_URL`. When the old FAE URL is supplied as `DATABASE_URL`,
  reusing its database or user on that cluster is rejected.
- `DAQ_AUTHENTICATED_CONTENT_KEYRING_FILE`: absolute, owned, mode-0600 keyring
  accepted by shared `ConversationContentCodec`.
- `DAQ_PLATFORM_REVIEWER_SUBJECT_IDS`: optional comma-separated enterprise UUIDs;
  omission grants no review access.

Content keys must be different from configured DAQ browser/session and task
keys. Both resolved paths and actual validated key material are checked, so
copying a keyring under another filename does not create isolation.

Shared conversation checkpoints have no `agent_id` field. The adapter uses DAQ
session namespace, trusted turn/feedback `agent_id` + runtime/knowledge release
metadata, and dedicated DB/key material as the boundary. Pilot membership and
per-document entitlements remain the identity/retrieval layers' responsibility.

The shared repository seals checkpoint state; the inherited `chat_turns` and
feedback rows still store their structured text fields in the dedicated DB.
Database access roles and operational retention must protect those tables.
There is no plaintext fallback file: shared feedback's outage hook raises an
explicit error. Attachment body/bearer/local-path data and credential-bearing
metadata URLs are redacted before turn storage; official structured source
locations remain. Attachment archive publication remains disabled.

## Verification and remaining acceptance

TDD: new module/contract absence produced 15 initial failures; additional tests
caught leaked credential URLs, copied key material and missing attachment/detail
semantics before their fixes. Final batch: 78 DAQ Python tests passed, including
23 authenticated persistence contracts; Ruff and whitespace checks passed.
Tests use deterministic ports, not a live Postgres or Platform deployment.

Before pilot acceptance, apply the shared conversation/feedback/review schema
and ownership migrations to the dedicated DAQ DB; verify actual transactions,
restart restoration, DB roles and reviewer authorization end to end. Root must
wire application identity, route ownership, terminal persistence failures and
review gates. Platform registration and production approval remain separate.


Request idempotency is not implemented by this adapter. Shared turn insertion
and checkpoint atomicity do not reserve or replay `client_request_id`, and
calling `save_turn` twice is not an exactly-once execution guarantee. Application
integration must owner/Agent-scope its request registry and reject conflicts;
durable cross-worker reservations and terminal replay need a separate storage
contract. The existing shared checkpoint restores messages, structured session
context and clarification count; transient `loop_state` and attachment
manifests are excluded by upstream. DAQ-specific ledger/context state outside
that envelope needs an explicit versioned checkpoint extension before restart
continuation can claim complete domain-state parity.
