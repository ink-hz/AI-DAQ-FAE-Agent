# Candidate section / typed record consistency v1

This offline contract adds no runtime loading, release eligibility, factual
approval or access permission. It reuses the six record kinds in `records.py`.

`audit_sections(sections, bodies, records, snapshot, historical_records=...)`
returns exact dependency edges, findings, per-section coverage and aggregate
coverage. Callers must reject `consistent == false`; `online_eligible` is always
false. Supply the original historical records to protect their complete contents,
including IDs, statuses, conflict candidates, conditions and provenance.

Each section lists its context dependencies in `dependency_claim_ids` (the legacy
name also permits topology/procedure/entity/software/link IDs). Context edges
alone make no claim that its prose expresses the record. A `record_assertions`
entry is a complete projection of `id`, `kind`, `status`, `scope`, `source_refs`
and `data`. The projection must exactly match the typed record. No automatic unit
conversion, inequality widening, condition deletion, variant inference or source
locator substitution is permitted. A section containing assertions must have the
same scope as the asserted records; split sections when scopes differ.

`render_record(record)` renders a deterministic private review block from the
record's identity, state, scope and complete data. Top-level source references
remain structured metadata. The auditor requires the exact rendered block in the
body and checks the body hash separately. Thus updating a body's hash cannot
hide a changed value. Any body content outside these blocks remains an explicit
`uncovered_body` finding. Arbitrary narrative is not certified by searching for
matching numbers. This v1 renderer is a review artifact, not customer prose.

All section and record references must resolve to the supplied snapshot with the
exact source hash and locator. Conflict candidate references must also appear in
the section; conflict candidates survive unchanged in dependency output. Every
output edge remains unanswerable. Review markers and permission signatures are
still enforced by the existing record validator; this audit creates neither.

Candidate `source_text` transcriptions can preserve exact original wording while
carrying `normalization_pending`. Passing the generated projection audit means
serialization consistency only. It does not mean those transcriptions have
become normalized numeric facts, or that the original narrative agrees with
historical claims. Keep explicit reconciliation gaps and contextual locator
differences until independent review resolves them.

Private B2 preparation preserves all historical records and original sections,
adds stable candidate IDs and section bindings in a separate ignored directory,
and rehashes/re-extracts referenced archive locations. No restricted source text,
filenames, record payloads or private scripts belong in Git. A sanitized audit
may contain aggregate counts and limitations only.

## Section metadata and section graph

Metadata declared outside the rendered blocks is also audited. `entity_id` must
belong to the union of typed assertion entities; `entity_ids` must exactly match
that union. A procedure's member identities may be resolved through its explicit
typed topology record. Multiple claims are not each forced to equal the section's
primary entity. A declared topology must match the asserted topology/procedure
identity; an unrelated claim cannot substantiate topology metadata.

Declared conditions, hardware revisions and software versions must be represented
in every asserted record and match exactly. Claim `data.conditions` is the normal
condition representation. Transcription envelopes may explicitly store
`conditions.section_conditions`, `conditions.hardware_revision` and
`conditions.software_versions`. Software records may use their `version` field.
Missing typed representation yields `section_<field>_unreconciled`; it never
silently passes. Common section metadata applies to every assertion; different
applicability conditions belong in individual record blocks or separate sections.

Procedure steps, preparation requirements/prerequisites, checks and failure
branches are compared with the ordered concatenation of the asserted procedures.
Step objects are compared in full, including topology, checkpoint, failure branch
and source refs. Embedded checkpoints/failure branches also reconcile against the
separate typed checks/failure arrays; missing step annotations stay unreconciled.
No fallback checkpoint is inferred.

`dependency_section_ids` must resolve within the supplied section inventory.
`section_dependencies` retains these edges for subsequent impact traversal.
Invalid record data remains a finding rather than crashing downstream metadata
or dependency inspection. These checks preserve every existing nonpublication
and independent-review boundary.
