# DAQ Scenario and Agent Architecture Upgrade Implementation Plan

> **For agentic workers:** Execute each task sequentially with test-driven development and independent Codex answer review. The user has already authorized direct execution in this workspace.

**Goal:** Expand DAQ real-question coverage, repair failures at their workflow layer, and verify on isolated Dev with a new code identity.

**Architecture:** Preserve the separately versioned DAQ service and independently published knowledge. Introduce request-scope separation and explicit evidence obligations for qualitative power and unverified selection constraints without changing the shared camera runtime. Keep the answering model out of the review gate.

**Tech Stack:** Python 3.11+, pytest, FastAPI `/chat`, isolated PostgreSQL Dev replay, immutable knowledge manifests, JSONL evaluation and Codex semantic review.

## Global Constraints

- Model replay only in Development; never send evaluation prompts to production.
- Governed DAQ knowledge and SDK notes are the only positive factual ground truth.
- Every run preserves question, answer, sources, coverage, fallback, trace, duration and code/knowledge SHA.
- No new runtime keyword scan of final answer, silent fallback, or answering-model self grade.
- A DAQ code SHA change requires new Dev replay and SDK/main publication identity reconciliation before any deployment claim.

---

### Task 1: Freeze broad cases and run discovery

**Files:** Create `evals/daq_complex_core_20261019.jsonl` and `evals/daq_complex_multiturn_20261019.jsonl`; update `docs/reviews/2026-10-19-daq-complex-evaluation.md`.

- [ ] Add at least 30 single-turn and 8 three-turn cases with `expected_boundary`, expected sources where available, and prohibitions on source/scope extrapolation.
- [ ] Validate JSONL IDs and evidence references against the current release.
- [ ] Run a representative discovery subset with `scripts/run_daq_workflow_dev_replay.py --suite file` and the reviewed main/SDK Dev inputs, preserving exact output SHA.
- [ ] Independently read the complete answer and trace for every discovery failure; record layer, mechanism, and a generalizable fix. Do not change the question or result after seeing an answer.

### Task 2: Separate operating evidence by request scope

**Files:** Modify `daq_fae/request_schema.py`, `daq_fae/task_context.py`, `daq_fae/domain_tools.py`, `prompts/daq_schema_extraction.md` only where needed; test in `tests/test_request_schema.py` and `tests/test_operator_pro_real_inputs.py`.

- [ ] Write a test in which one user turn explicitly asks for an EGO Pro single-device procedure and asks whether a multi-device combination inherits it; prove the current schema or tool omits the reviewed procedure.
- [ ] Validate each operating goal against its own user-authored scope while retaining whole-turn provenance, then keep one goal for single-device evidence and one for combination evidence.
- [ ] Preserve negative tests: a question only about a combination must not retrieve a single-device procedure; a model-written tool query must not create a standalone premise absent from user input.
- [ ] Run focused schema, operating-knowledge, and evidence-policy tests; inspect source IDs and requirement status.

### Task 3: Distinguish qualitative power from numeric aggregation

**Files:** Modify `daq_fae/request_schema.py` and the minimum planner interface required; test in `tests/test_request_schema.py` and `tests/test_variant_family_lookup.py`.

- [ ] Write a failing test for the exact `L0_06` question and a paraphrase: both require port-count and single-port goals plus an unproven qualitative total boundary, without a model-inferred simultaneous-port count.
- [ ] Retain the explicit-three-port numeric case and the rejection of a wrong port count or single-port claim used as total evidence.
- [ ] Run focused tests before and after the implementation and confirm the original schema error no longer occurs.

### Task 4: Track unverified hard constraints during selection

**Files:** Modify `daq_fae/request_schema.py`, `daq_fae/task_context.py` and `prompts/reviewed_knowledge_system.md` as needed; test in `tests/test_request_schema.py`, `tests/test_task_context.py`.

- [ ] Write a failing plan/evidence test showing that `1920×1200` as an option does not satisfy the user's distinct `native_resolution` hard condition.
- [ ] Make that qualifier an inspectable missing evidence obligation with the original user authority; do not rewrite a published resolution claim as a native-resolution claim.
- [ ] Run focused tests and verify the planner still answers the positive frame-rate facts at their measured resolutions.

### Task 5: New-identity real replay and independent review

**Files:** Update `evals/daq_release_failure_sentinels_20261018.jsonl` only for newly discovered failures; create `docs/reviews/2026-10-19-daq-complex-codex-review.md` and JSONL.

- [ ] Run every original failure sentinel, L0, broad core, multi-turn, English switch and boundary suite in isolated Dev with the new code SHA and reviewed knowledge.
- [ ] For every turn, record automatic hard gate, protocol and presentation separately from semantic judgment; review every failure against original source and trace.
- [ ] Re-run targeted cases after a mechanism fix under a new identity; never overwrite the old failure.
- [ ] Run affected Python tests, `git diff --check`, JSONL validation and source-claim coverage verification.
- [ ] Publish no code or knowledge to production until the source-bound release identity and answer-quality gates pass.
