# Knowledge update impact v1

`daq_fae.knowledge.update_impact.plan_update(previous, current)` is a pure offline
planner. Its output is private audit metadata, never a release, permission grant,
record mutation, or activation request. It reuses `compare_snapshots` and
`impact_report`; those existing APIs retain their compatibility behavior.

## Input bundle

- `snapshot`: existing source inventory/chunks and an explicit `extractor_version`.
- `records`: existing stable record `id`, kind/status/scope/data/source references
  and optional `dependency_record_ids`.
- `sections`: stable `section_id`, sources, body digest, conditions/roles and
  `dependency_claim_ids`, `dependency_section_ids`, `record_assertions` from B2.
- `coverage`: stable `coverage_id`, status, dimension metadata and explicit
  `record_ids`/`section_ids` dependencies. Empty coverage cells are retained.
- `questions`: stable `question_id` with `coverage_ids`, `record_ids` and/or
  `section_ids`. This binds a supplied Dev suite; it does not freeze or execute it.
- `sku_definitions`: map from entity ID to governed definition.
- `field_definitions`: map from field ID to governed definition.

Sources may declare `impact_scope` with `entity_ids`, `field_ids`, `topology_ids`
and `capabilities`. This is maintained evidence about relevance, never a filename
heuristic. Negative records/coverage also use their typed dimensions. A shared,
explicitly disjoint dimension excludes an unrelated negative; a missing dimension
cannot prove irrelevance, so an unscoped addition revisits all negatives.
Definitions/scopes and explicit dependencies must be complete for the intended
release scope. The planner does not infer undeclared domain relationships.

## Edges and change classes

Source references include conflict alternatives. Sources reach sections and
records; typed entity/topology references and explicit record dependencies reach
consumers. Records reach sections. A section reaches its asserted records, while
context-only record references remain one-way. Section dependencies can propagate
transitively. Records/sections reach coverage, and coverage reaches Dev questions.

The union of old and new graphs retains removed edges and identities. A missing
current dependency that existed previously is a tombstone requiring review;
unknown dependency IDs and duplicate IDs fail closed. Deleted nodes may therefore
remain in the audit graph, never in a newly approved release by implication.

Each reason returns a separate transitive review list: source add/change/remove,
extractor version change, changed extraction output, role expansion, SKU/field
change, and add/change/remove for records/sections/coverage/questions. Permission
contraction is also an ordinary changed-node review. Source byte equality uses
SHA-256, not timestamps; `modified_at_utc` alone does not invalidate reviews.
Extractor version changes affect extracted sources, excluding metadata-only
assets. Changed chunks require review even if version and original bytes agree.

`affected` is the union; `withdraw_positive_record_ids` withdraws previously
verified records reached by source/record removal; `recheck_negative_ids` lists
unknown/unsupported records relevant to added sources. Coverage-level absence
(`audited_no_source`) is also revisited. Withdrawal is an instruction for the next
candidate/release compiler, not a mutation of an immutable historical release.

## Conservative signature reuse

`reusable_signature_record_ids` contains eligibility only. Both snapshots must
identify the extractor; each record must have valid existing fact/access review
fingerprints, named reviewers and dates, matching current source hashes and exact
extracted locators. The record must be unchanged, roles identical, and unaffected
by every transitive review closure. Both fact and permission signatures are
excluded when any condition fails. The planner creates or copies no signatures.
Downstream schema, source archival, consistency and independent review gates still
apply. Candidate/conflict records never become reusable approved evidence.

## Release and privacy boundary

Write reports through the existing restricted, Git-ignored private artifact
writer. They contain structured source paths and are not answer prose or public
Git content. The plan includes an input digest for reproducibility.

This API has no filesystem writes and never imports/calls publication or
activation functions. Existing `publish_release` stages immutable versions only
after validation; `activate_release` explicitly selects a valid version. A failed
plan or rejected publication leaves the active pointer unchanged. D3 must consume
the review/withdrawal obligations alongside its other gates before any switch;
this module alone is not the full D3 publishing workflow.

## Stable coverage and draft question identities

`bind_coverage_questions(cells)` builds canonical IDs from typed dimensions.
`coverage_kind=entity_field` requires `entity_id` and `field_id`;
`coverage_kind=section` requires a nonempty set of stable `section_ids`.
Both include the explicit `scope` object (default empty); callers must put any
additional applicability dimensions there. The identity includes a schema
version and uses canonical JSON plus SHA-256. Status, sources, record membership,
row order and display text do not define identity. Missing identity dimensions
are rejected. Exact duplicate cells collapse; different payloads at one identity,
inconsistent supplied IDs or a detected digest collision fail closed.

Draft question IDs bind the fixed question family and coverage ID, so inserting,
deleting or reordering unrelated cells does not rename surviving questions.
Generated questions always remain draft, not frozen, not replayed and not
approved. This helper is for draft manifests only; it is not a migration tool for
an already approved or frozen Dev suite.
