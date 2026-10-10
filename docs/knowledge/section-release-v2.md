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
- `source_locations`: exact hash/locator identities, with no extracted source text;
- `section_count`, `section_status_counts`, `record_kind_counts`, `role_summary`;
- `source_index` from source identity to record/section IDs and `dependency_index`
  from section to record, section and link IDs.

The reader accepts integer versions 1 and 2 only. V1 means records only: embedding
sections or v2 section metadata is an error, never an implicit upgrade. Its
runtime record filters remain in force and its section view is empty. V2 requires
the exact runtime contract and recomputes the section gate and derived indices on
load and before activation. Legacy staging still permits records that its runtime
reader will refuse; v2 applies all runtime gates before staging.

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
**Current gate limitation:** B2 v1 only reconciles exact typed assertion blocks.
Free narrative outside those blocks remains `uncovered_body`, even with fresh
human review envelopes. The synthetic positive tests establish immutable body
transport and review/role contracts using reconciled blocks; they do not claim
that arbitrary narrative is now reconciled or ready for customer answers. A later
explicit B2 narrative reconciliation contract is required before such bodies can
pass. No real A2/A3/A4 narrative becomes eligible in this change.

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
