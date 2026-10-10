# DAQ reviewed knowledge authorization v1

The authenticated browser path requires both a DAQ Agent identity and an explicit
server-managed subject entitlement. Enterprise membership and the pilot allowlist
only grant service entry. Browser headers, request text, channel names and task
context cannot grant document access.

`DAQ_KNOWLEDGE_ENTITLEMENTS_FILE` is an absolute local file, owned by the service
UID or root, neither group nor world writable, with no final symlink. Deploy it
in a trusted server-controlled directory. JSON has exactly `agent_id` (fixed to
`ai-daq-fae-agent`) and `subjects` (canonical subject UUID to one of
`internal_fae`, `tmall_support`, `channel`). Missing, malformed or revoked mappings
fail closed. Empty releases preserve existing behavior. The service rereads the
file on each protected request and before each tool call; operators should replace
it atomically. No live subject IDs or entitlement files belong in Git.

All browser knowledge tools receive the same governed role and immutable release.
Search, read_doc, typed facts and links filter before model input. Links additionally
require forwarding permission. Role/release are part of durable request fingerprints
and session context. A changed role/release cannot replay an old request, resume an
old session or read its history. Conversation lists omit incompatible sessions.
Start a new conversation after a role/release change. This is deliberately stricter
than attempting to sanitize old natural-language answers. An old role restored for
the same release can access its previous authorized session again.

The generic review/trace HTTP surface has no source-level authorization contract;
with nonempty knowledge it returns 403. Internal trace storage remains an operator
artifact and must not be exposed directly. Current-request tool evidence is already
role-filtered; revoked in-flight requests are checked again before events and final
persistence. Previously authorized trace records are not retroactively rewritten.

Conflict existence is itself governed. `conflict_notice_review` separately binds
reviewer/date, `statement=conflict_exists`, `view_roles`, and a SHA-256 covering the
exact conflict record (including scope, provenance and candidates) and permissions.
Only a valid authorized claim notice permits `lookup_spec` to emit a generic
conflict status. It never returns candidate values or candidate sources. Missing,
stale or unauthorized review yields the ordinary evidence gap. Contradictory
software result rows likewise produce a generic gap, not an unreviewed conflict
notice. This v1 does not publish software conflict notices.

Platform Tasks have a trusted requester identity, but the reused result/event
routes do not bind persisted outputs to current document entitlements. Therefore
nonempty knowledge plus Task enablement fails startup with
`daq_knowledge_task_role_replay_contract_missing`. Empty-knowledge Tasks are
unchanged. A future Task contract must bind role/release at submission, execution
and result replay before enabling this combination. This is not production or
pilot authorization, and does not activate real candidate knowledge.

## Revocation during execution or delivery

The authorization check also runs after each domain tool computes its result,
before and after the transactional conversation writer, when terminal replay
frames are constructed, and after the completion transaction returns. Both new
responses and idempotent replays pass through the same final iterator guard: it
checks current entitlements after obtaining each frame and immediately before
handing that frame to StreamingResponse. This includes buffered terminal frames,
source frames, done/trace metadata, stages and heartbeats. A failed check terminates
the remaining stream with a sanitized authorization error stage. No previously
buffered private frames follow that error.

If revocation occurs after commit, the committed record stays bound to the old
role/release and cannot be delivered to the revoked role. It is not rewritten or
uncommitted. Frames already transmitted while authorized cannot be recalled;
revocation controls subsequent application-level frame deliveries. Filesystem
policy updates and network writes are not one atomic transaction.
