# C2 section retrieval implementation audit

2026-10-10. Scope: DAQ capability evidence and retrieval only. All inputs are
synthetic reviewed releases in pytest temporary directories. No real candidate
approval, activation, model replay, production request or camera code change.

Added deterministic title/alias/domain/body retrieval and authorized full-section
`read_doc`. The implementation preserves v1 search and typed lookups. C1 role
views filter before scoring/reading. Results carry release/scope and stable source
identities; local original paths are omitted from both content and section tool
sources. Retrieval never certifies typed evidence coverage.

TDD evidence:

- After correcting the initial synthetic fixture's accidental same-field conflict,
  new tests failed 10 / passed 8: missing natural-language recall, missing read
  schema, inaccessible read mismatch, and JSON metadata false positive.
- Initial implementation: 18 passed.
- Additional body path sentinels: 2 failed / 24 passed, then corrected to exclude
  local-path-bearing sections before retrieval. Dotted filenames were already
  rejected by the publication URL gate; extensionless paths exercise this gap.
- Source-reference sentinels then failed 7 / passed 19 because `path` was present
  in structured tool sources; section references now use content-hash source ID,
  SHA-256 and locator. New tests: 26 passed.
- Knowledge contracts: 126 passed, 6 existing dependency deprecation warnings.
- The first full suite identified the tool-name enumeration contract needing
  `read_doc`; updated that expected contract. Full suite then: 470 passed,
  6 existing dependency deprecation warnings. Final verification after source
  projection changes: 470 passed, 6 warnings in 11.31s (exit 0).

Limitations: deterministic phrase overlap is retrieval relevance, not semantic
proof. Real natural-language replay and independent answer review remain later
Dev gates. C1 whole bodies remain reconciled typed blocks. Oversize sections
return an explicit error; search excerpts have visible truncation. No publication
or permission gate was relaxed.

## Independent review correction: local source paths

Independent C2 review found that relative archive roots and UNC paths could pass
publication and enter full bodies/excerpts. RED verification: 13 failed, 33 passed
(8 publication failures and 5 additional tool-delivery failures). A shared detector
now runs at C1 compile/manifest-validation time and C2 retrieval time. It checks
exact source_refs.path, explicit tmp/temp/Downloads/Documents/Desktop and
 data/knowledge/knowledge/private roots, slash/backslash and relative-dot forms,
UNC, drive, absolute and home paths. Typed JSON is decoded before scanning strings.
Structured section sources continue to omit path entirely.

Tests include publication and already-hydrated legacy-view defenses, metadata
(title/aliases/domain_terms/scope), arbitrary exact original-source paths, and
positive product alternatives (Viewer/SDK, USB/以太网, RGB-D/IMU, 输入/输出) to avoid
a blanket slash ban. Dotted URL failures retain their existing publication error.
Targeted verification: 102 passed in 0.93s.
Final correction full suite: **502 passed, 6 existing dependency deprecation
warnings in 11.60s**, exit 0. No real knowledge or release state changed.
