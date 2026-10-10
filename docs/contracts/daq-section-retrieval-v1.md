# DAQ reviewed section retrieval v1

C2 consumes C1 `daq-reviewed-sections-v2` releases. It does not publish or activate
knowledge. The model receives only `sections_for(role)` and `records_for(role)`
results; candidate and conflict records, attachments, inaccessible sections and
their references cannot contribute retrieval terms or results.

- `search_knowledge(query)` searches verified section title, signed `aliases` and
  `domain_terms` metadata, authorized related entity names/aliases, and readable
  body values. Normalization uses Unicode NFKC and case folding. Latin terms match
  complete tokens; Chinese recall uses overlapping 2–4 character phrases. Title,
  aliases, domain terms and body have weights 8, 6, 5 and 1. Phrase length weights
  overlap; equal scores sort by stable section ID. There is no semantic model or
  unreviewed query expansion. At most 20 results carry 600-character excerpts and
  an explicit truncation flag. Record-only v1 releases retain existing search.
- `read_doc(section_id)` returns the exact whole authorized body, up to 32,000
  characters. Larger sections return `tool_error / section_too_large`; they are
  never silently truncated. The caller must supply a string; query and section ID
  arguments above 2,000 characters return `invalid_tool_arguments`.
- Results carry release ID, section ID, knowledge type, `reviewed_section` evidence
  layer, scope and any declared entity/topology/conditions/versions/link IDs.
  Whole section review assertions, reviewer metadata and source references are
  absent from content. References remain in structured `sources` with release,
  section ID and scope; per-source references contain content-hash source ID, SHA-256
  and locator, never the local source path. Sections containing local source paths in content are
  rejected by C1 publication/manifest validation and excluded again before C2 scoring
  or reading. The shared detector checks exact original-source paths, explicit
  restricted roots (including relative/backslash forms), UNC, drive, absolute and
  home paths, including decoded typed JSON. Product alternatives such as Viewer/SDK
  and USB/以太网 remain valid. It does not rewrite source content or waive reviews.
- Missing and unauthorized section IDs return identical `not_found` shapes with
  empty matches and sources. Query arguments are not echoed in these results.
  Unknown roles retain the reviewed view's explicit authorization failure.
- Search relevance and reading do not satisfy typed evidence requirements:
  `matched_requirement_ids` stays empty. `lookup_spec`, topology, procedure and
  software tools keep their exact applicability/condition rules. A section's
  applicability metadata is evidence context, not proof that the current request
  meets those conditions.

The synthetic tests exercise product, acquisition flow and topic recall, role
filtering, candidate/conflict and attachment exclusion, source separation,
deterministic ranking, full reads, bounds and legacy typed lookups. They do not
constitute real product approval, model answer evaluation, or production readiness.
C1 currently admits reconciled typed blocks; arbitrary prose requires a separately
reviewed future consistency contract. A source_text claim preserves reviewed prose
inside that existing typed representation.
