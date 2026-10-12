# DAQ Platform Task Knowledge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow authenticated, text-only Platform tasks to use the exact active DAQ knowledge release without leaking answers after a role or release change.

**Architecture:** Add a DAQ-only binding table keyed by Platform task ID. A DAQ store adapter records the trusted requester, role and release before queue insertion and checks them before worker reads. A DAQ token verifier checks current entitlement plus the immutable binding before every HTTP task read or mutation. The task orchestrator uses the same role-filtered `ReviewedKnowledge`, prompt and evidence policy as browser chat.

**Tech Stack:** FastAPI, psycopg 3, PostgreSQL migrations, pytest, pinned shared Platform Task queue.

## Global Constraints

- Keep `src/` and the camera service unchanged; DAQ code owns the binding adapter.
- Trust the signed Platform task token's `internal_user_id`; never trust task prose for role or document access.
- Bind role and active release before queue insertion. Current role/release must still match at execution and event replay.
- Continue to reject task attachment refs until they map to owner-bound DAQ bytes.
- Preserve empty-knowledge task behavior and fail closed for nonempty knowledge without the binding migration.
- Run real model replay in Dev only and independently review task answers before production activation.

---

### Task 1: Durable role/release binding

**Files:** Create `migrations/daq/004_task_knowledge_binding.sql`, `daq_fae/task_knowledge_binding.py`, `tests/test_task_knowledge_binding.py`; modify `scripts/migrate_daq_pg.py`.

**Interfaces:** `TaskKnowledgeBindings.bind(platform_task_id, requester_id, role, release_id)` inserts or checks an immutable binding; `require_current(platform_task_id, requester_id)` checks current entitlement and active release. `BoundTaskStore` delegates the shared queue contract and guards create/read/worker load.

- [ ] Write failing PostgreSQL tests for first bind, idempotent duplicate, changed role/release, foreign requester, and public camera table isolation.
- [ ] Run focused tests and confirm RED.
- [ ] Add the migration and DAQ-owned binding/store adapter, then rerun focused tests GREEN.
- [ ] Commit the tested binding layer.

### Task 2: Signed HTTP replay boundary

**Files:** Modify `daq_fae/platform_tasks.py`; extend `tests/test_platform_task_adapter.py`.

**Interfaces:** `DaqTaskVerifier.verify(token)` calls the pinned signature verifier and rejects a task token when a queued task has no matching binding or current role/release. It raises `TaskIdentityError(status_code=403, code="scope_denied")` before shared routes read stored task content.

- [ ] Write failing signed HTTP tests for create, event replay, entitlement revocation and release change.
- [ ] Run tests RED, implement verifier and configure it for nonempty knowledge, rerun GREEN.
- [ ] Check the existing camera/default shared task route contracts without modifying `src/`.
- [ ] Commit.

### Task 3: Reviewed task answer path and release gate

**Files:** Modify `daq_fae/platform_tasks.py`, `daq_fae/app.py`, `daq_fae/knowledge/operator_release.py`; extend `tests/test_platform_task_adapter.py` and operator release tests.

**Interfaces:** The task orchestrator consumes the active `ReviewedKnowledge`, bound role and release; its tool access guard rereads entitlement. The operator release runtime may advertise `tasks_enabled=true` only with a passing Dev task batch and binding contract version.

- [ ] Write failing tests proving a reviewed claim can resolve, a missing claim abstains, and a revoked role prevents terminal delivery.
- [ ] Run RED, implement the role-filtered task runtime and release gate, then rerun GREEN.
- [ ] Run signed HTTP + queue worker + persistence integration, and a minimal real Dev task replay with independent Codex review.
- [ ] Record exact results and commit. Production flags stay off until separate deployment checks pass.
