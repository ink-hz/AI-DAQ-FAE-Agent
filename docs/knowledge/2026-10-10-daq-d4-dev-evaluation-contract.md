# D4 Dev question and independent answer review contract

This is preparation for the real D4 gate, not a model replay or answer-quality
result. `daq_fae.knowledge.dev_evaluation` is an offline validator and packet
builder. It neither calls a model nor authenticates a reviewer or a release.
Trusted adapters must verify the D3 release and both detached approvals. The
answering model must not grade its own answer. Raw questions, answers, traces
and source locators stay in controlled Git-ignored storage.

## Question preparation

B3 generated 375 stable draft IDs for 375 coverage cells. Every question has
`question_family=coverage_state_and_boundaries`, the same generic prompt,
`status=draft`, `frozen=false`, `replay_status=not_replayed` and
`approval_status=not_approved`. Their private original file SHA-256 is
`9a85cf4d805ee2440fbea8526a6ab095f1d49aea3449a5b90f635af2a00fc931`.
The offline audit maps this generic family only to a *candidate* for
`evidence_gap`; it reports nine missing candidate families and all ten missing
frozen families. It never changes those 375 records or counts them as D4 passes.
The private audit is `data/knowledge/curated/d4-20261010/draft-audit.json`.

Use the following source-neutral authoring guide. Each row requires at least
one concrete question, an expected evidence boundary, coverage IDs, an
authorized or intentionally unauthorized role, and a named review. Product
facts, version claims and expected answers come only from the reviewed release.

| Family | Concrete case to author and review | Observation required |
| --- | --- | --- |
| `product_identity` | Ask about an exact named model/variant and a nearby ambiguous name. | Entity grounding, variant conditions and no unsupported merge. |
| `spec_conflict` | Ask for a field with competing candidate values. | Conflict is visible only at its approved scope; no resolved value without adjudication. |
| `combination_flow` | Ask for a multi-component connection or acquisition procedure. | Topology, preconditions, version, ordered checks and documented gaps. |
| `software_links` | Ask for a specified software/firmware combination and a deliverable page. | Exact compatibility tier, current reviewed URL and role-specific forwarding right. |
| `permissions` | Query a restricted section/link using a role without access, then an entitled role. | Filtering before retrieval and no source, content, link or trace leak. |
| `multi_turn` | Establish one confirmed setup condition, then ask a short follow-up or change the scenario. | Confirmed context is carried or reset correctly across at least two turns. |
| `evidence_gap` | Ask for a capability absent from an audited coverage cell. | Explicit missing/unknown boundary and appropriate fallback. |
| `provider_http_400` | Inject or observe a controlled Provider HTTP 400 in Dev. | Trace, error classification and visible runtime/configuration outcome. |
| `provider_http_503` | Inject or observe a controlled Provider HTTP 503 in Dev. | Trace, retry/fallback and runtime outcome without false knowledge-gap labeling. |
| `update_rollback` | Revisit an affected question across a new release and explicit rollback. | Before/after release identities, affected evidence and restored answer boundary. |

The frozen suite uses `format=daq-dev-questions/v1`, `environment=dev`,
`status=frozen`, a batch ID, a canonical SHA-256 of `questions`, a named ISO
review date, `required_question_ids` from the B3 impact map, and a detached
approval over every other suite field. A question has `id`, `family`, final
`question`, ordered user `turns`, `role`, nonempty `coverage_ids`, and
`expected_boundary`. The multi-turn case has at least two turns. The ten
families and every impact-required ID must be present. Generic B3 prompts,
unsigned approvals, changed question text or a production environment reject.
The actual D3 `dev_batch` must bind the same batch ID and question digest.

## Dev replay evidence

Only after a signed nonempty D3 release is active in Dev may an operator replay
the frozen suite. `validate_replay_batch` requires a trusted release verifier;
an arbitrary `release_id` string does not satisfy it. The verifier must check
the actual signed D3 manifest, active Dev pointer and observed release identity.
Each replay also needs a detached trusted **capture** approval over the whole
record. Its adapter must compare the record to retained Dev request/response,
authenticated role, terminal frame, health and trace evidence. Merely signing
caller-supplied JSON is insufficient. Each case records exact
suite/release/runtime/upstream IDs, observed Agent and role, answer model,
prompt version, an offset-aware capture timestamp, and
one record per user turn: question, answer, structured sources, planned and
actual capabilities, requirement coverage, fallback, **exact wire outcome**,
trace ID, latency, and observed Provider HTTP statuses. The 400 and 503 cases
must actually record those respective **terminal** statuses. A terminal Provider status
keeps its original error class (`provider_configuration_error` for 400,
`provider_unavailable` for 503); it cannot be relabelled `safe_abstained`.
Other current shared Loop and DAQ HTTP terminal outcomes are retained verbatim.
Answer prose is checked by the same local source-path
scanner used for section publication. The controlled runner must retain complete trace details;
this module is only its minimum integrity contract, not that runner.

The update/rollback case requires three **separately captured answers** on
old → new → old knowledge releases, with an observed role and trace ID on
each turn. Activation and rollback each record before/after release IDs plus
health and trace observations bound to the following answer turn. The new D3
manifest's `previous_release` must equal the old ID, and the IDs must differ.
An additional trusted release-pair verifier must authenticate both signed D3
manifests and their exact adjacency; comparing caller-supplied SHA strings
alone is not approval. A fake, unrelated or equal-ID transition rejects.

`prepare_review_packet` copies the full per-case replay and expected boundary
to a private packet with `review=null` for every item. It grants no approval.
An independent Codex or named FAE then records a decision bound to the exact
replay SHA-256. Decision fields are `case_id`, `replay_sha256`, `reviewer`, ISO
`reviewed_at`, `verdict=pass|fail`, `serious_failures`, `failure_layer`, `notes`
and detached approval. The trusted reviewer adapter authenticates the
signature; the reviewer ID must differ from the recorded answering model.
Failures must name one of source, governance, retrieval, coverage, synthesis or
runtime. Severe categories are model identity mix, permission leak,
unsupported `resolved`, and runtime error masked as a knowledge gap.

`audit_answer_reviews` accepts only a complete, signed per-case review set
with zero failed cases and zero severe findings. A reviewer must inspect
semantic accuracy; structural validation alone cannot establish it. Failed
records remain in the private batch for diagnosis and re-review. The Dev
batch result cannot be reused across a changed suite or knowledge release.

## Current state

No real D3 knowledge release, frozen D4 suite, Dev model replay or independent
answer review exists. B2 content consistency, D1 independent archive, D2
fact/access signatures and other D3 source/role/link gates remain prerequisites.
Synthetic tests include actual `create_app` offline, HTTP 400 and HTTP 503
terminal frames copied into a synthetic replay record. They verify exact
outcomes without running a model or activating real knowledge. Production and the
camera FAE runtime are untouched by this D4 preparation.
