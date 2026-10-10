# B3 update impact audit

Offline source-neutral planner and synthetic contracts are implemented. No
runtime, shared camera code, upstream pin, knowledge publication, permission,
production evaluation or active pointer changed.

The private B2 inventory maps 276 sources, 114 sections and 405 records into 375
coverage cells, with 1,545 graph nodes and 3,820 edges. The 16 used originals have
hypothetical hash-change impact maps. No actual incoming source update was
provided. The unchanged baseline has zero affected records and zero reusable
signatures; existing records remain candidates/conflicts.

The 375 Dev dependency questions are draft templates, not frozen cases, not
replayed, and not approved. They establish coverage-to-question edges for later
review. D4 model replay and independent answer review remain separate work.
B2's 293 consistency findings remain unresolved and are retained in private
coverage metadata; B3 does not promote this material to answerable knowledge.

Validation: 29 update-impact tests plus the related import, record, release,
section, identity and candidate suites: **159 passed** on Python 3.12. Five
PyMuPDF/SWIG dependency deprecation warnings are reported. Regression cases cover
scoped and unscoped additions, unknown/unsupported revisit, deletion with dangling
historical references, withdrawal, extractor and extraction changes, SKU/field
changes, role expansion/contraction, exact locator/source binding, context-only
isolation, transitive question impact and active-pointer preservation on failure.

Only generic code, synthetic tests, this audit and the contract are versioned.
Real dependency bundles, source mappings, draft questions and input manifests are
Git-ignored private artifacts with 0700 directory and 0600 file permissions.

## Review correction: stable coverage identity

The private preparation script originally assigned field coverage IDs by list
position. Review reproduced unrelated ID reassignment after inserting one cell.
It now uses the tested typed-dimension binder for both field and section cells;
question IDs derive from stable cell identity and question family. Duplicate
cells collapse; conflicting data at one identity is rejected.

The rebuilt private 375-cell inventory verifies insertion affects exactly one
cell/question while all 375 old IDs survive, deletion affects exactly one pair
with 374 surviving IDs unchanged, and reordering has zero impact. No record or
section is affected by those inventory-only scenarios. Private bundles/maps and
artifact hashes were rebuilt. All 375 questions remain unapproved draft templates.
The corrected suite has 35 impact tests; related regression total: **165 passed**,
with the same five dependency deprecation warnings.
