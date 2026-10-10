# C6 implementation report — 2026-10-10

## Scope and result

DAQ-only authenticated browser access now accepts a reviewed nonempty release only
with a trusted server entitlement file. Membership/allowlist grants no knowledge
role. Role mapping is reread on requests and tool calls. The three supported roles
are internal_fae, tmall_support and channel; cross-Agent identities fail closed.

The same role reaches section search/read, typed tools, links, tool-result sources
and the resulting trace. Durable request fingerprints and session contexts bind
role + release. Revocation denies protected requests; downgrade denies old history,
continuation and request replay; expansion works on a new session. Links retain
separate forwarding checks. No shared camera code was modified.

Conflict existence has a separate reviewed notice fingerprint covering original
record, exact scope and permitted roles. Authorized claim lookup returns only
conflict status, never candidate values or source IDs. Unreviewed/stale/unauthorized
conflicts return generic evidence gaps. The pre-existing software contradictory-row
branch was aligned to this rule: individual row approval does not approve disclosure
of the conflict itself.

## Explicit incomplete gates

- Nonempty knowledge Platform Tasks are blocked at startup. The task requester is
  trusted, but shared result/event replay routes lack role/release binding. Empty
  knowledge Tasks remain enabled under their existing contract. No claim of full
  knowledge Task support is made.
- Generic shared review/trace HTTP endpoints return 403 for nonempty knowledge;
  source-level authorization for those endpoints is not implemented. Operator-only
  trace storage retains historical authorized records; no retroactive scrubbing.
- No real candidate publication/activation, source-owner signature, model replay,
  production traffic, deployment, shared runtime update or camera behavior change.
  Authentication positive-path tests use synthetic v2 reviewed releases, fake
  Platform identity and an isolated local Postgres, with deterministic Loop output.
- Deployment must place the entitlement file in a trusted server-controlled parent
  directory. Runtime checks absolute path, final symlink, owner and writable bits.

## TDD and exact verification commands

Working directory: DAQ `.worktrees/daq-knowledge-20261010`.
Interpreter: `/Users/neo/Developer/work/AI-FAE-Agent/.venv/bin/python`.

1. `pytest -q tests/test_knowledge_entitlements.py --disable-warnings`: collection
   failed, project import absent from system pytest path.
2. `PYTHONPATH=.:tests pytest -q tests/test_knowledge_entitlements.py --disable-warnings`:
   collection failed, system interpreter lacks anthropic. No dependency changes.
3. `<interpreter> -m pytest -q tests/test_knowledge_entitlements.py --tb=short`:
   initial RED, 2 failures (missing entitlement module; nonempty auth startup guard).
4. Same command after implementation: 2 passed.
5. Same command after adding conflict notice test: RED, 1 failed / 2 passed
   (missing conflict visibility module).
6. `<interpreter> -m pytest -q tests/test_knowledge_entitlements.py tests/test_authenticated_chat.py tests/test_app_authenticated_mode.py tests/test_reviewed_knowledge.py tests/test_section_tools.py --tb=short`:
   132 passed.
7. Same focused entitlement command after adding tool revocation guard: RED,
   1 failed / 3 passed (access_guard argument unsupported), then implementation.
8. `<interpreter> -m pytest -q tests --tb=short`: 605 passed, 6 existing dependency
   deprecation warnings, 12.32 seconds.
9. Entitlement tests after adding link positive path: 4 passed.
10. `<interpreter> -m pytest -q tests/test_software_evidence.py --tb=short`:
    RED, 1 failed / 23 passed: unreviewed software contradiction exposed conflict
    status. Aligned implementation to generic evidence gap.
11. `<interpreter> -m pytest -q tests --tb=short`: 606 passed, 6 warnings, 12.48s.
12. Entitlement test strengthened to require dictionary requirements in authenticated
    toolbox: RED, 1 failed / 4 passed (wrapper dictionary incorrectly passed to
    tool requirements); fixed to `plan.requirements` as in local chat.

Final fresh verification and commit identity are recorded below after execution.

13. `<interpreter> -m pytest -q tests --tb=short`: **606 passed**, 6 existing
    dependency warnings, 13.16s after requirements correction.
14. `<interpreter> -m pytest -q tests/test_knowledge_entitlements.py tests/test_authenticated_chat.py --tb=short`:
    **12 passed**, 6 warnings, 2.91s after explicit mid-flight authorization failure
    reporting. This is the only subsequent behavior change.
15. `git diff --check`: clean (exit 0).

Code/test/contract/audit only; report uses synthetic IDs and no document payloads,
secrets, live grant mappings or release activation pointers. Commit is the commit
containing this report; root agent receives the exact SHA separately.
