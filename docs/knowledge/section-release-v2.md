# DAQ reviewed section release v2

`publish_release(..., sections=[...], bodies={section_id: text})` builds a v2
content-addressed manifest. Omitting `sections` explicitly retains the v1
records-only writer and its existing staging behavior. Bodies without sections
are rejected. Neither writer activates a release; `activate_release` is the
separate atomic pointer operation. There is no camera pointer or upstream change.

## Format and compatibility

V2 retains v1 archive identity, sources, typed records, record status/counts,
release review (`reviewer`, ISO date, `dev_batch`) and previous release ID. It adds:

- `runtime_contract: daq-reviewed-sections-v2`;
- `sections`, with exact UTF-8 `body`, stable `section_id`, nonempty `title` and
  `knowledge_type`, body digest, scope, source references, record assertions,
  dependency IDs, reviewed link IDs, review status and two named reviews;
- `source_locations`: exact hash/locator identities. Narrative-referenced chunks additionally retain exact `text` and `text_sha256` for immutable evidence verification; unrelated chunks retain location metadata only. These are private release metadata, never unfiltered model context;
- `section_count`, `section_status_counts`, `record_kind_counts`, `role_summary`;
- `source_index` from source identity to record/section IDs and `dependency_index`
  from section to record, section and link IDs.

The reader accepts integer versions 1 and 2 only. V1 means records only: embedding
sections or v2 section metadata is an error, never an implicit upgrade. Its
runtime record filters remain in force and its section view is empty. Both versions use the same runtime read validator before activation, so an
unreadable manifest never replaces the current pointer. V1 rejects all reserved
v2 fields, including section metadata, counts, indices, role summary and runtime
contract. V2 requires the exact runtime contract and recomputes the section gate
and derived indices on load and before activation, including record status_counts.
Legacy staging still permits records that its runtime reader will refuse; such
records are now rejected before activation. V2 applies all runtime gates before
staging.

## Review binding and eligibility

`section_fingerprint(section, body, records)` hashes the exact body, all section
metadata except review envelopes and the redundant embedded body, and the fact
and access fingerprints of every record/link dependency. Thus changing body,
source path/hash/locator, applicability, roles or dependency contents invalidates
both `fact_review.section_sha256` and `permission_review.section_sha256`.
Both reviews require a named reviewer and ISO review date. This is integrity
binding, not reviewer authentication or an automatic factual approval.

Only `review_status: verified` sections enter v2. Every referenced record is
independently answerable under the existing typed-record review contract. The
compiler re-runs B2 consistency against a temporary candidate-validation
projection; it does not edit stored status or manufacture review signatures.
Caller-provided consistency flags or audit summaries cannot waive the gate.

The body field can represent free prose; it has no generated-block-only schema.
**Legacy typed-block mode:** B2 v1 reconciles exact typed assertion blocks.
Free narrative outside those blocks remains `uncovered_body`, even with fresh
human review envelopes. The synthetic positive tests establish immutable body
transport and review/role contracts using reconciled blocks; they do not claim
that arbitrary narrative is now reconciled or ready for customer answers. The optional [source-bound narrative-unit extension](section-narrative-consistency-v1.md) now reconciles exact spans, source excerpts, independent unit reviews and deterministic hard-fact renders. It does not automatically approve A2/A3/A4 candidates. The first two real chapters and their exact Dev scope are recorded in the [KQ status report](../reviews/2026-10-21-daq-knowledge-quality-status.md).

## Roles and links

All record dependencies and section dependencies must have the same view/forward
role sets as the citing section. This deliberately conservative rule requires
splitting differently permitted paragraphs into separately reviewed sections.
Invalid, duplicate, empty viewing roles and forward roles outside viewing roles
fail closed. `sections_for(role, for_delivery=...)` filters entire sections before
retrieval and returns detached copies from a frozen internal view.

Bodies and non-provenance metadata are scanned for scheme, protocol-relative and
bare-domain URLs. Deliverable links use `link_ids`; they must resolve to reviewed
link records and be forwardable to every section viewing role. Link records may
not be embedded as ordinary assertion/dependency blocks. URL changes invalidate
link review and section dependency binding. Actual delivery still uses the
existing governed link tool; C1 does not insert a URL into a body.

## Atomicity and scope

Validation completes before creating the staged artifact. Existing immutable
manifest hashing and restricted modes remain. Duplicate builds yield the same
ID. Validation/storage failures preserve the old active pointer; temporary stage
cleanup and rollback remain explicit. C1 changes publication/storage and the
request-independent reviewed view only. Search/read_doc, authenticated role
mapping, real source approval and real activation belong to later tasks.


## Explicit local Dev custody

The existing public writer, D3 readiness and active loader support an explicit
`allow_local_dev=True` path for the owner-authorized, verified local archive.
`scope.execution_scope` must be `loopback_dev_internal_fae_only`, custody must
name the existing 2026-10-12 authorization and verify exact original bytes, and
all answerable records/sections must retain internal FAE roles. Independent
source/content and permission decisions remain required; no external signature
or independently durable copy is invented. The approval verifier is still a
trusted operator adapter checking exact recorded content decisions.

Default `ReviewedKnowledge.load_active` rejects this mode, including when the
caller supplies an approval verifier. Authenticated application startup has no
opt-in. Local stage/activation/rollback uses the same immutable writer, compiler,
indices and compensation logic; production owner modes remain unchanged.

Reviewed field aliases and operating guards are retained in approval-bound
`readiness.scope.domain_evidence`; the loader validates their target fields,
entities and veto labels. Mixing this authority with `operator_release` is
rejected. Runtime `sections_for` omits private narrative-unit reviews/excerpts,
while retaining approved body, dependencies and structured source references.
