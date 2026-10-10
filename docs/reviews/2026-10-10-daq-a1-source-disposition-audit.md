# A1 source disposition audit — 2026-10-10

Task status: **工程已实现待真实验收**. Codex implemented and self-reviewed the
offline tool and checked the candidate originals. This is source handling review;
苍渊's product fact and access decisions remain pending. No knowledge was published.

Input archive manifest SHA-256:
`e35fe11f6773bc157734d65a3b19c87a18fdf14bab3c5458d1b4d75b22387d56`.
The real run reverified/re-extracted all 276 originals and exactly matched the
existing extractor-1 candidate snapshot. The two output partitions have 27 text
rows and 249 asset rows, with 276 unique original paths and no omissions.

All 27 text sources are `needs_fact_adjudication`: extraction is available, but
chapter cleaning, identity/variant conditions, software scope, links and roles
are not signed. Each has source-specific reasoning, an assigned owner, exact
extracted page/line citations, original hash and an unresolved version relation.
There are zero `curated` or superseded sources. Bilingual material and parallel
resolution variants remain separate. Byte-identical desktop release notes retain
separate original identities; they do not prove identical platform capabilities.

Asset categories: 217 images, 2 videos, 1 document asset, 3 firmware binary
candidates, 25 software package candidates and 1 unclassified asset. All 249
original headers were inspected privately; image dimensions and ZIP member
metadata were recorded where applicable. 218 assets have explicit references
from extracted documents, with original document locators. Visual semantics,
package functions and the extensionless asset's purpose remain unreviewed.
No package member bytes were added to the default text index. All access and
forwarding roles remain empty and pending; delivery approvals: zero.

Two complete real builds produced byte-identical output files:

| Artifact | SHA-256 |
| --- | --- |
| Text disposition | `dc2fbeea4c9f421f0944d81d673819844ee9f10d55aa0eece5f43cb8e0b76987` |
| Asset inventory | `f3513bec7c80b05d7547e37a253feaed1c41bd54dd06c614671f52abf6ff6a42` |
| Canonical decision input | `be5589f7443f357144ab153179dd2c68e3f60f2a1ab29cc71be0b6e71974b3d1` |

Private decisions, metadata inspection and both inventories are retained in the
main checkout's ignored curated directory. No source excerpts, source filenames,
package member names, private links or raw files are committed.

Validation: 13 focused tests passed; full repository suite: 267 passed. Existing
PyMuPDF and Starlette dependency deprecation warnings remain. Tests cover complete
partitioning, identity preservation, stale/missing/duplicate decisions, locator
bounds, missing responsibility, unsubstantiated cleanup/supersession, binary text
exclusion, explicit asset references, archive/snapshot mismatch, protected new-run
writing and rejection of public output locations. The tests were established with
failing behavior before implementation; the real run provides separate source
coverage evidence.

Remaining acceptance work: independent source spot checks, asset semantic/use
review where needed, and named fact/access adjudication. Controlled durable
storage and recovery acceptance belong to D1. A1's private local archive check
does not satisfy that release gate or authorize an online knowledge switch.
