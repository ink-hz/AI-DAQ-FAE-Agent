# DAQ FAE capability expansion design

Date: 2026-10-12. The owner selected broader governed knowledge, Platform tasks and attachment archive, feedback analysis, answer copying, and English. Product selection and SDK remain undecided. A separate DingTalk or MetaBot integration is outside this scope.

## Decision and order

There are three implementation choices: copy the camera service and its data flow; change shared camera runtime for both services; or keep the exact pinned runtime and add DAQ-owned adapters. Use DAQ-owned adapters. Shared runtime changes are allowed only where a generic interface is necessary, after camera quality gates and an exact repin. This keeps DAQ knowledge release, roles, schema, and rollback independent.

Deliver the work in four separately testable batches. (1) Extend the source-checked knowledge release incrementally from local hashed originals. (2) Bind Platform tasks to the same active release and role contract as browser chat, then enable task routing; turn attachments need a separate source-authorization contract. (3) Enable the existing DAQ attachment archive producer and give the Platform archive consumer an agent-scoped handoff, then add DAQ-only feedback analysis. (4) Add English UI and answer behavior over the same immutable facts. The copy button and per-answer feedback controls already exist; verify their production build and interaction instead of duplicating them.

## Knowledge publication

The active release has 6 entities and 17 claims, with no reviewed sections. The 742-record candidate graph and 114 candidate sections are inputs, not authority. A batch may add a claim or section only when original file SHA-256 and exact locator are rechecked, product and variant scope are exact, conflicts are either resolved or excluded, and its role visibility is explicit. Source paths stay in structured provenance, never answer prose. Keep selection and SDK records out of positive answers until the owner decides their scope. Each update has a changed-source impact report, a frozen Dev question batch, independent Codex or human FAE review, a new immutable release ID, and an explicit rollback target. No model evaluation uses production.

## Platform tasks

The current task worker uses the empty-knowledge prompt and rejects attachments; the application also refuses to start with both nonempty knowledge and tasks. The task route must bind the trusted requester identity to the active knowledge release and current role at submission. Execution checks the binding before each governed tool result and before persistence. Event/result replay checks current role and release again; a changed or revoked role yields a sanitized authorization failure, not the stored answer. Task traces record the real release and coverage. Plain text tasks may be enabled after this contract and a signed HTTP plus persistent worker Dev test. Platform attachment references stay explicitly unsupported until they can be mapped to owner-bound DAQ attachment bytes and checked for every continuation.

## Attachment archive and feedback

Browser turns already record attachment relations through `AttachmentArchiveService`; production has the producer disabled. Enable only with DAQ-scoped storage, database identity, retention/handoff settings, and a Platform consumer that can list, read, acknowledge, and verify bytes without crossing the camera schema. Test upload, answered turn, relation, handoff, consumer acknowledgement, deletion and expiry through authenticated HTTP and the real DAQ schema. Archive failure remains visible in health and does not claim a successful handoff.

Per-answer feedback and reviewer routes exist. Run the pinned analytics pipeline against the DAQ schema with explicit DAQ agent identity, read-only extraction, private output, and no camera rows. Reviewed outcomes and issue closure remain human/Codex decisions; the answering model cannot grade itself. Expose the resulting status through the current review workflow only after the agent and role boundary is verified. The copy button copies the full rendered Markdown answer and reports success or failure, with English labels under English locale.

## English

Use a visible Chinese/English switch persisted in the browser. Localize identity, chat, attachment, feedback, review, progress and error UI; source-derived product names and numeric values stay exact. The answer language follows the selected UI language (and an English task request for task results), while the same role-filtered release remains authoritative. Add reviewed English aliases for entity and field retrieval, preserve variant and measurement conditions, render answer headings in the requested language in a DAQ-owned layer, and keep structured sources unchanged. Failures, missing evidence and permission denials must be natural English when English is selected. Dev evaluation includes English positive, ambiguous-variant, missing-source, attachment and feedback cases, independently reviewed for semantic fidelity.

## Acceptance

Each batch must pass its contract tests, authenticated HTTP/persistence path where applicable, and the minimal real Dev model replay with independent semantic review for changed answer behavior. Shared runtime edits additionally pass the camera contract and quality gates before DAQ repins. Production deployment requires an immutable source/release identity, backup and rollback evidence, read-only health checks of both services, and no production model evaluation. Browser business acceptance remains separate from deployment.
