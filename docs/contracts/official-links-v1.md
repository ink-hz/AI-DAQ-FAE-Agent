# DAQ official link evidence and delivery

A `link` record is deliverable only after fact, access, and independent page
reviews bind the same record fingerprint. Browser observation alone is not an
approval. Candidate URLs, README addresses, archive paths and package filenames
are never delivery evidence. The runtime does not browse pages on user requests.

In addition to `url`, `title`, and `link_type`, reviewed links require
`data.page_evidence`:

- `title`: observed page title;
- `version`: observed version, or explicit `not_stated`;
- `captured_at`: observation date (`YYYY-MM-DD`);
- `snapshot_sha256`: hash of the privately retained page capture;
- `sku_scope`: the exact reviewed record applicability scope;
- `valid_until`: inclusive review expiry date chosen by the reviewer.

All these fields are covered by the fact/access/link record fingerprints.
`link_review` requires a reviewer, review date, matching fingerprint and
`final_url` equal to `data.url`. Capture must precede review, and review must be
current and unexpired. A redirect to a different URL requires a new record URL,
page capture, applicability check and new reviews. Only absolute HTTPS URLs
without credentials or a nonstandard port pass; malformed URLs produce findings.
The page title does not establish official ownership or exact SKU identity;
those remain explicit human review responsibilities. Review fingerprints bind
content but do not provide cryptographic identity authentication.

Staging and activation require every answerable link to be current; failed
activation leaves the active pointer unchanged. Runtime loading validates static
page evidence, date ordering and all review fingerprints, while allowing a
previously valid link to have since expired. The role view omits expired links
on every request, both before and after reload, preserving unrelated reviewed
knowledge in the same immutable release. Expiry never excuses invalid metadata,
redirect mismatch or tampered reviews. The current role needs both view and
forward permission. `official_links` returns matching verified records;
`search_knowledge` excludes link records. The reused runtime still requires a URL
to have been returned by an allowed tool in the current turn before final output.
No changes are made to the camera runtime or its URL policy.

Private candidate ledgers distinguish inaccessible pages, reachable pages with
unresolved SKU mapping, and reachable pages with a proposed exact mapping.
These are observation states, never substitutes for named approvals. A login
page, incomplete fetch or HTTP 200 alone does not establish document access.
Real source-owner signatures must be obtained before publishing any candidate.
