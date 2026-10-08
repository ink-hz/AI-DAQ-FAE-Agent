# Empty Knowledge Dev Bootstrap Implementation Plan

**Goal:** Start an independent local DAQ FAE API with the existing FAE Loop and an intentionally empty knowledge release.

**Architecture:** Keep a tracked snapshot of the deployed FAE source at `a6234f6be546efebb230ffc74bd9a00bebdc2814` for this local bootstrap. A new DAQ app supplies its own prompt and empty evidence tool, and applies a terminal guard so an ungrounded `resolved` answer is never delivered. The old FAE app and knowledge are never mounted.

**Scope:** Local Dev `/health` and SSE `/chat` only. Offline adapter mode exercises the Loop without a gateway; Anthropic mode uses a separately configured Dev gateway. Platform identity, attachments, feedback, governed knowledge ingestion, and deployment remain under the main task book.

**Global constraints:** Preserve structured `sources`, outcome, coverage, trace, and visible fallback. Do not accept a `resolved` answer while the DAQ knowledge release is empty. Opus 5.5 must use adaptive/high and `submit_only_auto`. Never run evaluation against production. The source snapshot is temporary and must be replaced by a durable pinned dependency before the main milestone-0 gate is claimed.

### Task 1: Lock the empty-knowledge contract

- Write failing API tests for health, empty evidence abstention, and rejection of a model's ungrounded `resolved` submission.
- Run the focused tests to confirm the app is missing.
- Copy the tracked upstream `src/` tree at the fixed commit and record its provenance.

### Task 2: Implement the Dev API

- Add a DAQ system prompt, empty evidence ToolBox, explicit offline transport, Anthropic Dev transport configuration, and a DAQ app with SSE `/chat`.
- Buffer terminal output until the evidence guard has inspected it; attach `agent_id`, `knowledge_release`, `runtime_release`, `trace_id`, planned capability, coverage, and fallback state.
- Run focused tests and a local HTTP smoke test.

### Task 3: Make startup reproducible

- Document local setup, offline smoke request, and Dev gateway configuration in the DAQ README.
- Verify there is no camera knowledge or secret in the DAQ commit, run tests, and commit the bootstrap branch.
