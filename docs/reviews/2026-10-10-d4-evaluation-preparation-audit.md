# D4 evaluation preparation audit — 2026-10-10

**Status:** engineering preparation implemented; real D4 is blocked by the
absence of a signed, activated D3 Dev knowledge release. No model call or
semantic answer review was performed.

The original private B3 draft-question file has SHA-256
`9a85cf4d805ee2440fbea8526a6ab095f1d49aea3449a5b90f635af2a00fc931`.
It contains 375 unique question IDs. All 375 have `status=draft`,
`frozen=false`, `replay_status=not_replayed` and
`approval_status=not_approved`; all use the generic
`coverage_state_and_boundaries` prompt. The D4 audit treats this as only an
`evidence_gap` *candidate*. It lists nine missing candidate families:
`product_identity`, `spec_conflict`, `combination_flow`, `software_links`,
`permissions`, `multi_turn`, `provider_http_400`, `provider_http_503`, and
`update_rollback`. **All ten frozen families are missing.** The private
machine-readable report is `data/knowledge/curated/d4-20261010/draft-audit.json`.

The new offline D4 contract validates: exact frozen question identity and
trusted approval; required family and impact-question coverage; trusted D3 Dev
release and exact replay version binding; per-turn answer, structured sources,
capability plan/actual, coverage, fallback, exact terminal outcome, trace and latency;
Provider 400/503 and rollback observations; and one detached, independent
review per answer. The review packet begins unsigned. Severe findings or
failed reviews reject the batch. No accepting verifier is bundled.

Independent review found three D4 contract gaps in the first commit. The
follow-up binds every replay to a trusted capture approval, records the
observed Agent/role and release on every turn, and requires a trusted verifier
for both adjacent signed releases. The update/rollback case now needs three
old → new → old answers with distinct traces and two health/trace transition
observations. Provider terminal outcomes keep their exact app wire values;
captured 400 cannot be changed into a knowledge abstention. Malformed case IDs
return a validation error. Synthetic `create_app` offline, HTTP 400 and HTTP
503 terminals are copied into D4 replay records to exercise these contracts.

The source-neutral family authoring guide and field contract are in
`docs/knowledge/2026-10-10-daq-d4-dev-evaluation-contract.md`. It does not
promote the generic B3 questions or invent product facts. Real questions must
be written against the approved release scope, reviewed, then replayed only in
Dev. The reviewer's signed semantic judgment must remain separate from the
answering model.

**Verification:** `python -m pytest -q tests/test_dev_evaluation.py` reported
13 passed; `python -m pytest -q tests` reported 717 passed, 6 third-party
deprecation warnings. Neither command runs a model or verifies real
knowledge. The candidate file and private report are Git-ignored.

**Remaining gates:** D1 independent durable archive/retrieval, D2 named fact
and access decisions, B2 content consistency, C3/C4/C5 real source decisions,
D3 signed Dev publication and observation, then ten-family frozen questions,
real Dev replay and independent per-answer review. Camera FAE runtime and
production state were not changed.
