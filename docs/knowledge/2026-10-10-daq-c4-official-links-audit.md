# C4 official link evidence audit

C4 changes the DAQ capability evidence layer. Offline and runtime validation now
share page review requirements, including captured page hash, title/version,
exact applicability scope and expiry. Current-role view and forward permissions
remain mandatory; expiry is checked again for each role view. Redirect changes,
local paths, package names, missing reviews and changed page evidence fail closed.

Sixteen real candidate entries were inspected: twelve source-index document or
asset-library addresses and four official product/download pages. All remain
candidates, with zero fact/access/link approvals and zero deliverable real URLs.
Document library content could not be independently viewed. Public pages require
SKU/revision adjudication and owner authorization; a generic camera SDK page
cannot serve as a DAQ compatibility or download approval. The eight target entity
boundaries remain explicit in the private ledger.

Exact addresses, capture timestamps, transport results, page titles, source
bindings, snapshot hashes and role decisions are retained only in the ignored,
restricted C4 workspace. No raw pages, source URLs, credentials or owner
signatures are committed. No knowledge pointer, deployment or camera code changed.

Validation: TDD reproduced eleven gate failures before implementation. The final
DAQ suite passes 571 tests (six existing dependency warnings). Synthetic cases
cover positive current-role delivery, unreviewed candidates, view-only access,
redirect mismatch, malformed/local URLs, missing browser metadata, stale page
fingerprints and expiry in an already loaded view. Dev model replay and human
fact/access/link approval remain later release gates.
