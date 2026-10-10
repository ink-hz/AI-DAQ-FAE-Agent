# D2 per-item adjudication import contract

This offline contract prepares review proposals and validates signed decisions.
It has no runtime, publication, status-promotion or active-pointer integration.
`ready_for_import` means the review envelope is complete; `online_eligible`
remains false even for complete synthetic signatures. Importing a review is not
approval to publish a knowledge release.

## Review unit and fingerprints

Each item contains its stable ID, kind, complete candidate record, exact source
references and source excerpts/asset metadata, proposed body, applicability and
conditions, proposed view/forward roles, deferred flag, explicit blockers, and
page evidence for links. SHA-256 uses sorted UTF-8 JSON without whitespace or
nonfinite numbers. Separate source, record, body, condition and role digests aid
inspection; the item digest binds all fields together. Full archived files are
rehash-checked by the private builder before exact page/line extraction. Binary
metadata proves package presence only. Private camera snapshots retain exact
commit/ref/file hashes. Captured login HTML remains inaccessible-page evidence.

The current restricted package has 572 units: 47 K7 proposals, 23 deferred K7
items, 114 chapters, 335 new typed candidates, 36 software evidence candidates,
16 link observations and one module relation. Original records and proposals are
preserved together. Neither role proposals nor reviewer assignment are grants.

Edits to content, source, location, condition, role, page observation or blockers
require `refresh_item`, which issues a new fingerprint and records `supersedes`.
Retain the previous immutable packet externally for audit. It never copies
signatures. CSV imports compare the entire item against a trusted current packet;
edits, omissions, unknown IDs and duplicate IDs reject the import. The source and
packet manifest must be revalidated by the operator before each review import;
do not accept a submitted CSV as its own trusted baseline.

## Separate decisions and authentication

Every item requires distinct `fact` and `access` decisions. Links also require a
`page` decision. Each axis contains reviewer, ISO date, decision, summary,
disposition and signature. The same authorized person may review multiple axes,
but a signature for one axis cannot be reused on another. Fact summaries must
cover facts and exact applicability; access summaries must cover view/forward
roles. Page summaries must identify the observed page and SKU mapping.

Signing bytes are produced by `decision_bytes`: canonical JSON with the domain
`daq-item-adjudication/v1`, axis, item ID/hash, reviewer/date, decision, summary
and disposition. Signature bytes are excluded. The importer requires a trusted
axis-specific reviewer allowlist and a `verify_signature(reviewer, payload,
signature) -> bool` callback. Both are supplied by the controlled reviewer
identity workflow, never by CSV contents. There is no default accepting verifier
or signing command. A typed name, assignment, prefilled hash or unsigned CSV is
insufficient. Real reviewer public keys/authentication integration are not
configured by this engineering task; real CSV decision/signature columns remain
empty. Tests use a synthetic HMAC identity and never a real person's signature.

Use `write_review_csv(items, path)` to create a new 0600 file (existing files and
symlinks are refused), then `read_review_csv(path, trusted_items, reviewers=...,
verify_signature=...)` to produce detached results. Run this offline importer in
one process; its bounded CSV parser temporarily adjusts the process field limit
to accommodate full source pages. Store the directory with mode 0700 under an
explicit Git-ignore rule. Do not email or publish the packet automatically.

## Deferred, conflicting and blocked material

The signed fact disposition is one of `continuing_conflict`, `awaiting_source`
or `resolved`. `hold`/`reject` decisions and nonresolved dispositions never pass.
Unsigned items retain `pending_review`; the 23 historical deferred items preserve
all original reasons instead of receiving fabricated dispositions. A signed
`resolved` value cannot clear existing blockers. Correct the proposal, attach
new evidence and obtain fresh signatures for the new fingerprint.

Chapter reconciliation, unresolved identity/version selectors, software evidence
tiers and module republication remain separate mandatory gates. Even signed
review envelopes must pass record/section/source validation and their domain
contracts before controlled release compilation. Links additionally require
C4 exact page URL/snapshot/title/version/SKU/expiry checks; a page signature alone
does not prove reachability or grant delivery permissions. C5 retains its own
four approval gates, exact relation and source durability requirements. The D2
relation review is not a replacement for them. Durable archival, Dev release,
model replay and independent answer review remain downstream requirements.
