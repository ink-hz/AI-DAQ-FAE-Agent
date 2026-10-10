"""Build an unsigned, private D2 delta from source-bound B2 normalization reviews.

Example: python -m scripts.prepare_b2_normalization_review --originals ...
    --batch ego.json --batch umi-hub.json --snapshot ... --archive-root ...
    --bindings ... --outdir ...
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import subprocess

from daq_fae.knowledge.adjudication import prepare_item, write_review_csv
from daq_fae.knowledge.normalization_review import (
    audit_candidate_vocabulary,
    expand_snapshot_for_review,
    merge_normalization_reviews,
)
from daq_fae.knowledge.records import validate_records


def load(path: Path):
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def write_private(path: Path, value) -> str:
    body = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(body)
    return hashlib.sha256(body).hexdigest()


def audit_original_source_refs(originals: list[dict], read_excerpt) -> int:
    """Independently reread every cited location, including held/no-proposal rows."""
    distinct = set()
    for row in originals:
        for ref in row["source_refs"]:
            key = (ref["path"], ref["sha256"], json.dumps(ref["locator"], sort_keys=True))
            if key not in distinct:
                read_excerpt(*key)
                distinct.add(key)
    return len(distinct)


def validate_review_bindings(originals: list[dict], bindings: list[dict], section_index: dict):
    """Bind each candidate to the exact indexed section that already asserts it."""
    by_original = {row["id"]: row for row in originals}
    sections = section_index.get("sections", [])
    by_section = {section["section_id"]: section for section in sections}
    if len(by_section) != len(sections) or len(by_original) != len(originals):
        raise ValueError("duplicate section or original IDs")
    result = defaultdict(set)
    pairs = set()
    for binding in bindings:
        rid, sid = binding["record_id"], binding["section_id"]
        if rid not in by_original or sid not in by_section or (rid, sid) in pairs:
            raise ValueError(f"unknown or duplicate normalization binding: {rid}, {sid}")
        pairs.add((rid, sid))
        original, section = by_original[rid], by_section[sid]
        assertions = [row for row in section.get("record_assertions", []) if row.get("id") == rid]
        if (binding.get("source_refs") != original["source_refs"] or
                section.get("scope") != original["scope"] or
                any(ref not in section.get("source_refs", []) for ref in original["source_refs"]) or
                rid not in section.get("dependency_claim_ids", []) or
                assertions != [original]):
            raise ValueError(f"normalization binding does not match indexed assertion: {rid}, {sid}")
        result[rid].add(sid)
    if set(result) != set(by_original):
        raise ValueError("normalization has missing section bindings")
    return result


def field_vocabulary_sha256(field_id: str, vocabulary: dict, baseline_dictionary: dict) -> str:
    """Bind item signatures to the exact field definition, not unrelated fields."""
    candidate = [row for row in vocabulary["fields"] if row["field_id"] == field_id]
    baseline = [row for row in baseline_dictionary["fields"] if row["field_id"] == field_id]
    if len(candidate) != 1 or len(baseline) > 1:
        raise ValueError(f"field vocabulary identity invalid: {field_id}")
    payload = {"field_id": field_id, "format_version": baseline_dictionary.get("format_version"),
               "baseline_definition": baseline[0] if baseline else None,
               "candidate_definition": candidate[0]}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def build(args):
    originals, original_sha = load(args.originals)
    batches = []
    batch_sha = {}
    for path in args.batch:
        batch, sha = load(path)
        batches.append(batch)
        batch_sha[str(path)] = sha
    snapshot, snapshot_sha = load(args.snapshot)
    bindings, bindings_sha = load(args.bindings)
    section_index, section_index_sha = load(args.section_index)
    dictionary, dictionary_sha = load(args.dictionary)
    proposals, report = merge_normalization_reviews(originals, batches)
    vocabulary = audit_candidate_vocabulary(proposals, dictionary["fields"])
    blocked_fields = set(vocabulary["blocked_field_ids"])
    archive = args.archive_root.resolve()
    expanded = expand_snapshot_for_review(
        snapshot, [ref for row in proposals for ref in row["source_refs"]], archive)
    _, findings = validate_records(proposals, expanded)
    if findings:
        raise ValueError(f"normalized candidate schema/source findings: {findings[:5]}")

    by_original = {row["original_id"]: row for batch in batches for row in batch}
    section_by_original = validate_review_bindings(originals, bindings, section_index)

    @lru_cache(maxsize=None)
    def excerpt(path: str, sha: str, locator_json: str):
        locator = json.loads(locator_json)
        source = (archive / path).resolve()
        if not source.is_relative_to(archive):
            raise ValueError("source outside archive")
        raw = source.read_bytes()
        if hashlib.sha256(raw).hexdigest() != sha:
            raise ValueError(f"source changed: {path}")
        if locator["kind"] == "lines":
            lines = raw.decode("utf-8-sig").splitlines()
            if not 1 <= locator["start"] <= locator["end"] <= len(lines):
                raise ValueError(f"source lines absent: {path}")
            text = "\n".join(lines[locator["start"] - 1:locator["end"]])
        elif locator["kind"] == "page":
            text = subprocess.check_output([
                "pdftotext", "-layout", "-f", str(locator["page"]), "-l",
                str(locator["page"]), str(source), "-",
            ]).decode()
            if not text.strip():
                raise ValueError(f"PDF page text absent: {path}")
        else:
            raise ValueError("unsupported review locator")
        return text

    original_locations = audit_original_source_refs(originals, excerpt)

    items = []
    edges = []
    for row in proposals:
        original_id = row["data"]["original_record_id"]
        original = by_original[original_id]
        source_bodies = []
        for ref in row["source_refs"]:
            text = excerpt(ref["path"], ref["sha256"], json.dumps(ref["locator"], sort_keys=True))
            source_bodies.append({"source_ref": ref, "text": text,
                                  "sha256": hashlib.sha256(text.encode()).hexdigest()})
            for section_id in sorted(section_by_original[original_id]):
                edges.append({"source_ref": ref, "section_id": section_id,
                              "original_record_id": original_id, "candidate_record_id": row["id"]})
        blockers = []
        if original["disposition"] == "compound_needs_review":
            blockers.append("compound_source_requires_adjudication")
        if row["data"]["field"] in blocked_fields:
            blockers.append("field_vocabulary_requires_adjudication")
        items.append(prepare_item(
            item_id=row["id"], kind="claim", record=row, source_refs=row["source_refs"],
            source_bodies=source_bodies, body="",
            conditions={"scope": row["scope"], "data_conditions": row["data"]["conditions"],
                        "section_ids": sorted(section_by_original[original_id]),
                        "field_vocabulary_sha256": field_vocabulary_sha256(
                            row["data"]["field"], vocabulary, dictionary)},
            view_roles=[], forward_roles=[], deferred=False,
            blockers=blockers,
            page_evidence={},
        ))

    outdir = args.outdir
    outdir.mkdir(mode=0o700, parents=True, exist_ok=False)
    overlay = expanded["chunks"][len(snapshot["chunks"]):]
    hashes = {}
    hashes["candidate-records.json"] = write_private(outdir / "candidate-records.json", proposals)
    hashes["field-vocabulary-review.json"] = write_private(outdir / "field-vocabulary-review.json", vocabulary)
    hashes["subline-locator-overlay.json"] = write_private(outdir / "subline-locator-overlay.json", {
        "review_only": True, "source_snapshot_sha256": snapshot_sha, "chunks": overlay})
    hashes["proposal-bindings.json"] = write_private(outdir / "proposal-bindings.json", edges)
    hashes["review-items.json"] = write_private(outdir / "review-items.json", items)
    write_review_csv(items, outdir / "review.csv")
    hashes["review.csv"] = hashlib.sha256((outdir / "review.csv").read_bytes()).hexdigest()
    summary = {
        **report,
        "review_items": len(items), "source_locations_reread": excerpt.cache_info().currsize,
        "all_original_source_locations_reread": original_locations,
        "new_subline_locators": len(overlay), "record_findings": len(findings),
        "section_bindings": len(edges),
        "originals_sha256": original_sha, "batch_sha256": batch_sha,
        "source_snapshot_sha256": snapshot_sha, "section_bindings_sha256": bindings_sha,
        "section_index_sha256": section_index_sha,
        "baseline_dictionary_sha256": dictionary_sha,
        "candidate_field_count": vocabulary["field_count"],
        "new_field_definitions": len(vocabulary["new_field_ids"]),
        "multi_unit_fields": vocabulary["multi_unit_field_ids"],
        "baseline_field_mismatches": vocabulary["baseline_mismatch_field_ids"],
        "unreviewed_baseline_fields": vocabulary["unreviewed_baseline_field_ids"],
        "vocabulary_blocked_review_items": sum("field_vocabulary_requires_adjudication" in
                                               item["blockers"] for item in items),
        "output_sha256": hashes, "fact_reviews": 0, "access_reviews": 0,
        "online_eligible": False,
        "decision_rows": len([r for r in by_original.values() if r["disposition"] in
                              {"conflict", "compound_needs_review", "source_mismatch", "unresolved"}]),
        "candidate_count_by_product": dict(sorted(Counter(
            row["scope"]["product"] for row in proposals).items())),
    }
    write_private(outdir / "summary.json", summary)
    print(json.dumps({key: summary[key] for key in (
        "coverage", "dispositions", "new_candidate_claims", "new_subline_locators",
        "record_findings", "review_items", "decision_rows", "publication_status")}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--originals", type=Path, required=True)
    parser.add_argument("--batch", type=Path, action="append", required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--archive-root", type=Path, required=True)
    parser.add_argument("--bindings", type=Path, required=True)
    parser.add_argument("--section-index", type=Path, required=True)
    parser.add_argument("--dictionary", type=Path, required=True)
    parser.add_argument("--outdir", type=Path, required=True)
    build(parser.parse_args())


if __name__ == "__main__":
    main()
