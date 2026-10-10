# D3 engineering acceptance audit

Status: engineering gate and synthetic rehearsal implemented; real D3 Dev release
blocked. V1, pilot readiness and D4 answer quality are not complete.

The source-neutral test dataset exercises reviewed records and sections, separate
staging/activation, immutable manifests, stale prior IDs, changed content/reviews,
archive custody/retrieval, role/source/section checks, runtime/entrypoint
compatibility, declared exclusions, impact obligations and frozen question batch.
Synthetic health/trace observations verify successful switch and rollback, failed
pointer writes, wrong release observations, initial activation recovery and
rollback restoration failure. No model, production or live Dev operation occurs.

The private real audit rehashes its input artifacts and D2's upstream input
manifest. It refuses current candidates with 13 reason codes and nine enumerated
blockers: missing independent durable archive/retrieval, 572 unsigned D2 items,
293 B2 consistency findings, 325 unnormalized transcriptions, 36 pending software
rows, 16 pending links, pending module projection, mandatory C6 Task/trace exclusion
and 375 draft questions not frozen. These counts describe pending work, not
approved facts or permissions. Published and activated real releases: zero.

Artifact bodies, exact source paths, source-owner review material and candidate
content remain in the Git-ignored main-checkout private audit directory (0700;
files 0600). This tracked audit contains no real source payload, URL or grant.
Independent implementation review remains required. Static signed readiness is
not a substitute for independently authenticated original evidence or D4 answers.


## Independent review correction

The initial standalone wrapper left legacy public APIs, CLI and application
loading able to use an unsigned package. Seven failing tests reproduced those
entrypoint bypasses and a pointer fsync failure after replacement. Public nonempty
publication/activation now require trusted D3 adapters; CLI without adapters
refuses them. Runtime loading independently checks whole-package approval and
actual runtime/upstream identity. No production test bypass switch exists.

Existing offline contract tests explicitly use private storage primitives; actual
application tests sign synthetic D3 bundles and inject a test-owned verifier.
Pointer writes are inside the compensation boundary. A failure after replacement
restores the original pointer, while failure to restore remains an explicit error.

An additional v2 integration regression showed natural link expiry caused section
consistency validation to reject unrelated facts during reload. Runtime integrity
validation now preserves those facts while continuing to hide expired links.
Publication/activation/rollback still require current reviews. This does not grant
any real candidate approval or activate a real release.


A second review reproduced an empty bootstrap overwriting a signed nonempty
active pointer without adapters. Three failing regressions cover this downgrade,
a different empty version, and a competing writer winning the transition lock.
Empty bootstrap activation now checks the pointer inside the shared lock and only
allows first boot or idempotent activation of the same empty ID. Existing active
state is preserved on every rejected bootstrap transition.

Integration review reproduced unauthenticated local chat receiving internal
knowledge even with valid release governance. Startup now refuses nonempty
knowledge without authenticated mode, and the local toolbox has no implicit
`internal_fae` role. The red regression exercised a synthetic `/chat` request and
observed HTTP 200 before correction; the corrected configuration fails before any
model/tool work. Authenticated entitlement and empty-Dev contracts remain covered.

Typed records previously had a URL gate but lacked the section source-path gate;
`lookup_spec` copied their data directly into model-visible evidence. Synthetic
regressions now cover signed staging, manifest validation and an old in-memory
view with Unix/Windows/UNC/relative/normalized path sentinels, plus legitimate
slash terms. Typed content is rejected at the immutable boundary and withheld by
runtime role views; structured provenance and valid official links are preserved.
