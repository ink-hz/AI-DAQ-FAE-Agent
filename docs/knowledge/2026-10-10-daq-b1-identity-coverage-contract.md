# B1 private identity and coverage dictionary

Status: 工程已实现待真实验收. This offline compiler indexes existing evidence and candidate vocabulary; it does not publish knowledge or authorize access.

`daq_fae.knowledge.identity_coverage.build_dictionary(records, sources, sections, config)` accepts existing K1/K7 records, an explicit source path/hash inventory, candidate section metadata, and private vocabulary configuration. `scripts/daq_identity_coverage.py --input INPUT --output OUTPUT` compiles a JSON bundle and binds the complete input bytes by SHA-256. The CLI prints counts only. Output must be Git ignored; the destination directory is 0700, JSON is atomically written as 0600, and symlink destinations are rejected.

## Identity and evidence preservation

- Original records, IDs, statuses, source refs, conditions, conflict alternatives and comparator values are retained whole. Inventory status is mirrored, never upgraded; an incoming `verified` label is not a new review by B1.
- Name normalization applies Unicode NFKC, case folding and whitespace folding only. Variant selectors and punctuation remain significant. Qualified display names are distinguished from names literally recorded in source records.
- Aliases require existing entity and record references, remain candidate, and carry those records' provenance. Explicit pending mappings remain ambiguous even when only one local entity is registered. Any shared normalized name returns all matching entity IDs. The private lookup never returns automatic resolution or a release authorization.
- Field vocabulary contains candidate Chinese/English names, synonyms and unit aliases. Observed units and comparison operators come from records. Each original condition is retained; absent comparison means the stated value, not an inferred upper/lower bound. No automatic unit conversion or comparator widening occurs.
- Topology membership, device roles, connections, power, platform, synchronization and storage retain their original record scope. Additional component descriptions require explicit candidate relation metadata and provenance; a description does not create verified component identity or transfer component performance to a system.

## Coverage and role boundaries

Coverage includes entity × observed field cells and separate entity, field, topology, source and role indexes. Section coverage retains its separate candidate evidence layer, scope and gap IDs; a chapter's existence does not fill a structured fact gap. Cross-product topics retain their explicit topic scope without invented entity bindings.

Status aggregation is `conflict` first, then `verified`, then `candidate`. Counts and record IDs expose every contributing status. This is an inventory rollup, not evidence that all versions or conditions are covered. No aggregation selects a value or combines measurement conditions.

Empty cells use `audited_no_source` with `audit_scope=supplied_record_inventory_only`: the deterministic audit found no matching structured record in the supplied inventory. This does **not** assert that no relevant material exists in original files, chapters, other versions or the world. Source cells with no linked K7 record use the same limited scope. It never manufactures `unsupported`; inputs outside the supported three record statuses are rejected rather than rewritten.

Role indexes explicitly report `not_assessed_by_b1`, zero granted viewing/forwarding records, and inventory status separately. Existing record content is private audit data and is not a role-filtered retrieval response. Proposed chapter roles do not become grants. Configuration cannot contain nonempty review/permission fields. Candidate sections with granted roles are rejected. `online_eligible=false`, empty output role lists and empty reviews are unconditional.

Private product vocabulary, external source snapshots, source paths and candidate facts remain in the ignored restricted workspace. Git contains only the source-neutral compiler, synthetic tests, contract and aggregate audit. Fact adjudication, permission review, source-content applicability checks, B2 same-source consistency and any release are separate gates.
