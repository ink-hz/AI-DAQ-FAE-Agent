# A1 source disposition and asset inventory

This offline tool records source handling decisions. It does not clean chapters,
verify product facts, establish compatibility, grant access, or publish knowledge.
Use `scripts/daq_source_disposition.py` with `--archive`, `--snapshot`,
`--decisions`, and `--output data/knowledge/curated/<new-run>`.
The archive is reverified and re-extracted; the complete snapshot must match before
any output directory is created. Existing runs are preserved. Outputs are Git
ignored, directories use mode 0700, and files use mode 0600.

The private decision JSON binds `archive_manifest_sha256` and
`extractor_version`. Its `text` list covers exactly every non-asset source, with
`path`, `sha256`, `status`, `reason`, `owner`, `reviewer`, `reviewed_on`,
`evidence_locators`, and `update_relation`. Locators must match extracted page or
line ranges. A wholly unextractable source may use a file locator only with
`needs_ocr_or_parse`. Responsibility assignment is not a fact/access signature.

The status vocabulary follows A1: `curated`, `historical_duplicate`,
`needs_ocr_or_parse`, `needs_fact_adjudication`, `out_of_first_scope`.
This implementation rejects `curated`: the current A1 input has no independently
checked cleaned chapter artifact. Chapter cleaning is A2/A3 work. Duplicate
handling requires a byte-identical source and retains each original identity.
Update relations remain `unresolved`; reviewed supersession evidence requires a
later explicit contract. Filenames, sizes, modification dates and shared bytes
never establish a newer authoritative version or equivalent platform support.

`source-disposition.json` preserves original source identity, extraction status,
all extracted locators, cited decision locators, byte identity relations and
pending role review. `asset-inventory.json` preserves the other source identities,
file locators, provisional format categories and intended uses. Category/purpose
classification is explicitly based on extension metadata. Existing document
links add source/hash/page-or-line references and labels; these describe how the
asset is referenced, not its validated visual meaning. Packages are never
unpacked into the text index. Every asset has `default_text_index=false`,
`compatibility_status=not_established`, and `content_review_status=not_reviewed`.
Actual header/image/container inspection may be kept as a separate private audit;
that inspection does not upgrade content or delivery status.

Both outputs carry input identity, a deterministic decision digest, partition
counts, status counts and zero delivery approvals. No raw extracted text is
copied into the outputs. Source paths and labels in these private inventories
are structured provenance and must not be emitted as user answer prose.
