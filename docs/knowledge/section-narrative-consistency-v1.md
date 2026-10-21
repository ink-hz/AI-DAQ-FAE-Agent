# Section narrative consistency v1

This extends the existing section release v2 and B2 audit contracts. It does not
create another publisher, grant source permissions, or turn candidate sections
into reviewed facts. Existing exact `daq-record` bodies and their reviews retain
their original behavior. The implementation is `narrative_units.py`, integrated
with `section_consistency.py`, `section_release.py`, and `update_impact.py`.

## Evidence units

An optional `section.narrative_units` list covers exact, nonoverlapping body
spans. Each unit contains:

| Field | Contract |
| --- | --- |
| `unit_id` | Stable identifier, unique within the section. |
| `unit_type` | `paragraph`, `table_row`, or `step`. |
| `mode` | `record_render`, `source_excerpt`, or `reviewed_narrative`. |
| `start`, `end`, `body_sha256` | Half-open Python Unicode character offsets and SHA-256 of the exact UTF-8 body slice. No normalization. |
| `knowledge_type` | `claim`, `procedure`, `topology`, `entity`, `software`, or `explanation`. |
| `authored_by` | Named author, distinct from the unit fact reviewer. |
| `scope` | Nonempty applicability object exactly equal to the section scope. |
| `entity_ids`, `topology_id` | Explicit entities and topology (or `null`). Typed units must reconcile to their records; declared section identities must match. |
| `conditions` | Typed units: complete mapping of asserted record IDs to each record's `data.conditions` or `{}` when absent. Explanations: explicit object, equal to section conditions when declared. |
| `dependency_record_ids` | Exactly the asserted record IDs, also declared in `section.dependency_claim_ids`. Explanations use an empty list. |
| `record_assertions` | Exact complete projections of `id`, `kind`, `status`, `scope`, `source_refs`, and `data`; explanations use an empty list. |
| `source_evidence` | Nonempty exact chunk/excerpt bindings described below. |
| `view_roles`, `forward_roles` | One explicit role boundary, equal to the section and its typed dependencies. Mixed permissions require separate sections. |
| `fact_review`, `permission_review` | Separate named decisions bound to the unit fingerprint. |

Each `source_evidence` item contains `evidence_id`, `source_ref` (the existing
path/hash/locator object), `chunk_sha256`, `start`, `end`, `excerpt`, and
`excerpt_sha256`. Its offsets refer to the exact snapshot chunk text. The source
reference must occur in the section and resolve to exactly one snapshot chunk
with the same path, original byte hash, and locator. Both the snapshot's
`text_sha256` and the unit's `chunk_sha256` must equal the actual chunk text hash;
the exact source slice must equal `excerpt` and its hash. Missing source text,
wrong locators, hash drift, and duplicate ambiguous chunks fail closed. Typed
record sources must all appear among the unit's source evidence.

The source snapshot is still an extraction artifact. This check does not prove
that an extractor faithfully read a PDF table. The independent fact review must
compare the original hash-verified document and its layout, footnotes, and
conditions, using the private source-intake and page-review ledger. Publication
must use the existing source/readiness gates; supplying invented snapshot text
and matching checksums is not an approval process.

## Exact hard facts and readable rendering

`record_render` requires verified typed records and exact equality of every core
field. `render_narrative_unit(unit)` renders the governed record projections:

- A paragraph contains scope plus every data field in stable label/value lines.
- A table row contains those same fields in one escaped Markdown pipe row.
- A step contains exactly one procedure assertion and a zero-based `step_index`.
  The rendered step selects that precise ordered step while retaining the full
  prerequisites, checks, failure branches, topology, and other data fields.

Values are canonical JSON, preserving types, units, comparators, conditions,
versions, and nested structures. Only structured `source_refs` are omitted from
prose, including nested procedure provenance; they remain in the exact assertion
and its reviews. No arbitrary field projection or natural-language substitution
can be declared machine-equivalent. A changed number, missing condition, added
cell, wrong step, or changed applicability is rejected even after recalculating
body hashes and section reviews. Repeated steps can reference the same complete
procedure; the section audit deduplicates that record for metadata checks.

## Independently reviewed explanation

`source_excerpt` requires the body slice to equal the exact source excerpts,
joined with two newline characters. `reviewed_narrative` permits a source-based
explanatory paraphrase. Both require `knowledge_type=explanation`, no typed
assertions/dependencies, and cannot use the operational `step` unit type.

These modes do **not** certify hard specifications or operational guarantees.
The independent reviewer must determine that the text is explanatory, preserves
all applicable boundaries, and introduces no hard parameter, compatibility
promise, topology inference, or new instruction. Such assertions belong in
reviewed typed records and `record_render` units. A numeric keyword detector is
not used as a substitute for that semantic decision.

The reviewer must actually read the exact original source and proposed body.
Automated scripts must not invent reviewer identities or fill approved
semantic decisions. As with existing record reviews, named metadata alone does
not authenticate a person's identity; the release's independent review and
source-custody gates remain necessary.

## Review fingerprints and coverage

`unit_fingerprint(unit, body, records)` binds every unit field except the two
review objects, the exact body slice, and each dependency's record and access
fingerprint. Both reviews require a named reviewer, ISO review date,
`decision=approved`, and the matching `unit_sha256`.

The fact review additionally requires:

- A reviewer distinct from `authored_by` after case/whitespace normalization.
- `semantic_checks` exactly containing `complete`, `scope_preserved`,
  `conditions_preserved`, and `no_added_claims`, each `true`.
- One `evidence_assessments` entry per evidence ID, with an actual source-specific
  explanation in `assessment` of how the excerpt supports this exact unit.

The permission review requires a nonempty `rationale` for the exact sources and
view/forward roles. The same authorized person may make both decisions, but
both decisions must be recorded. The enclosing existing section fingerprint
binds the complete body, all units **including their review decisions**, section
metadata, sources, and dependency record/access fingerprints. Changing a unit
review therefore requires a new section review.

Every non-whitespace character must be covered by a valid exact typed block or
one validated narrative unit. Headings, navigation, and table headers receive
no automatic semantic exemption: use source-supported reviewed units when
needed. This deliberately avoids hiding claims inside Markdown formatting.
Missing units, duplicate IDs, overlapping spans, malformed spans, unknown
modes, mixed roles, and stale reviews are fatal. Whitespace-only gaps are the
sole exemption. Source paths and unreviewed URLs remain prohibited in body and
deliverable metadata; only structured evidence/review provenance is excluded
from those prose checks.

Formatting changes never silently reuse changed semantics. A changed body slice
or moved span invalidates its unit reviews. Even whitespace outside all spans
changes the section fingerprint and requires a new section review. Unchanged
exact slices may retain unit decisions when their offsets and all other bound
inputs remain identical.

## Publication, immutable load, and impact

The existing publisher compiles these sections through the same v2 path. For
immutable verification it must preserve exact text and `text_sha256` only for
chunks selected by `narrative_source_keys(sections)`, whose key is
`(path, sha256, canonical_locator_json)`. The canonical locator JSON uses sorted
keys and compact separators. Unrelated source text must not be copied into
published metadata. The loader runs the same section compiler and evidence
checks. Internal excerpts and reviews must not be returned through answer tools;
only approved body, safe public metadata, and role-filtered structured sources
are deliverable.

The existing section dependency/source indices remain authoritative because all
unit dependencies and sources must be declared on the section. Update impact
also checks these declarations and treats narrative assertions like existing
assertions: record changes reach sections, and changed section applicability
reaches asserted records. Source deletion, extraction changes, and permission
changes propagate through the existing graph. Unrelated entities retain their
unchanged review eligibility.

## Migration and acceptance

1. Keep historical A2/A3/A4 candidates and released records immutable. Create a
   new private batch and preserve stable section/unit identities.
2. Recheck original hashes and exact extraction locations. Split mixed scopes
   and roles. Create or reuse reviewed typed records for every hard assertion.
3. Render hard facts and procedure steps through the deterministic helper;
   prepare exact-source or explanatory units for remaining useful prose.
4. Compute exact spans/hashes and obtain independent source-specific fact and
   permission decisions. Compute unit fingerprints before section fingerprints.
5. Run the audit, existing release readiness, compile, immutable-load, role-tool,
   and update-impact checks in the independent local Dev path.

Static success is not factual approval or a release. A first real product and
system section still require their own private source and review evidence.
Synthetic positive/negative examples are in `tests/test_narrative_units.py`;
existing section and update-impact suites preserve legacy contract behavior.
