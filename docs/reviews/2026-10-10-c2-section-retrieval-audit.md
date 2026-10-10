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
