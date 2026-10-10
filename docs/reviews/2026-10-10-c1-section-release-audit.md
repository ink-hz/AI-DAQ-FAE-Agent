# C1 reviewed section release audit

Implemented DAQ-only v2 immutable section/record manifests, exact dual review
binding, source/dependency and role/state summaries, prepublication URL/link and
uniform section permission gates. V1 compatibility is explicit. No source text,
real candidate payloads or archive filenames are committed.

Synthetic verification: 24 new section release tests and the complete repository
suite pass (424 tests). Six existing dependency deprecation warnings were emitted.
Tests include changed body/source/roles, stale reviews, candidate and mixed-role
rejection, unresolved dependencies, reviewed link roundtrip and permission failure,
inline URL rejection, reader index checks, immutable role view, storage failure
cleanup and preservation of the previous pointer. Existing v1 tests remain green.

Read-only eligibility verification rejected all 114 real bound A2/A3/A4 chapters
and all 405 B2 generated candidate views. All inspected private JSON inputs had
unchanged hashes. Zero real artifacts were published or activated; no runtime,
camera source, deployment or model evaluation was changed.

The v2 body schema supports free text. Current B2 consistency can only reconcile
exact typed assertion blocks; signing unnormalized prose still fails. The real
narrative audit remains inconsistent, and no factual or access approval is implied.
