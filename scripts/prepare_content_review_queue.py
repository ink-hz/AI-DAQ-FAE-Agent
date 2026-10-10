"""Join source-bound B2/B3 artifacts into an unsigned private content review queue."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import defaultdict
from copy import deepcopy
from pathlib import Path

from daq_fae.knowledge.content_review_queue import assemble_queue
from daq_fae.knowledge.section_consistency import audit_sections


def _load(path: Path):
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def _checked(path: Path, expected: str):
    value, actual = _load(path)
    if actual != expected:
        raise ValueError(f"input hash mismatch: {path.name}")
    return value, actual


def _write(path: Path, value) -> str:
    raw = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
    return hashlib.sha256(raw).hexdigest()


def _sections_match(baseline: list[dict], enriched: list[dict],
                    candidates_by_section: dict[str, set[str]]) -> bool:
    """Only normalized candidate dependency IDs may extend indexed sections."""
    if len(baseline) != len(enriched):
        return False
    expected = deepcopy(baseline)
    actual = deepcopy(enriched)
    for old, new in zip(expected, actual):
        old_ids = old["dependency_claim_ids"]
        new_ids = new["dependency_claim_ids"]
        if (len(old_ids) != len(set(old_ids)) or len(new_ids) != len(set(new_ids)) or
                set(new_ids) != set(old_ids) |
                candidates_by_section.get(old["section_id"], set())):
            return False
        old["dependency_claim_ids"] = sorted(new_ids)
        new["dependency_claim_ids"] = sorted(new_ids)
    return expected == actual


def build(args) -> dict:
    """Refuse changed input versions and write only unsigned, Git-ignored output."""
    if args.outdir.exists():
        raise FileExistsError(args.outdir)
    b2_summary, b2_sha = _load(args.b2_review_dir / "summary.json")
    b3_summary, b3_sha = _load(args.b3_review_dir / "summary.json")
    if b3_summary["input_sha256"]["review_summary"] != b2_sha:
        raise ValueError("B3 graph is not bound to this B2 review")
    originals, originals_sha = _checked(args.originals, b2_summary["originals_sha256"])
    dispositions = []
    batch_hashes = {}
    if set(map(str, args.batch)) != set(b2_summary["batch_sha256"]):
        raise ValueError("disposition batch inventory differs from B2 review")
    for path in args.batch:
        batch, sha = _checked(path, b2_summary["batch_sha256"][str(path)])
        dispositions.extend(batch)
        batch_hashes[str(path)] = sha
    b2_names = ("candidate-records.json", "proposal-bindings.json",
                "field-vocabulary-review.json", "review-items.json")
    b2 = {}
    b2_hashes = {}
    for name in b2_names:
        b2[name], b2_hashes[name] = _checked(
            args.b2_review_dir / name, b2_summary["output_sha256"][name])
        if name in b3_summary["input_sha256"] and b3_summary["input_sha256"][name] != b2_hashes[name]:
            raise ValueError("B3 graph is not bound to B2 artifact: " + name)
    section_index, section_sha = _checked(
        args.bound_section_index, b2_summary["section_index_sha256"])
    if b3_summary["input_sha256"]["bound_section_index"] != section_sha:
        raise ValueError("B3 graph is not bound to indexed section bodies")
    old_bindings, old_bindings_sha = _checked(
        args.old_bindings, b2_summary["section_bindings_sha256"])
    graph, graph_sha = _checked(
        args.b3_review_dir / "dependency-bundle.json",
        b3_summary["output_sha256"]["dependency-bundle.json"])
    audit, audit_sha = _checked(
        args.b3_review_dir / "section-consistency-audit.json",
        b3_summary["output_sha256"]["section-consistency-audit.json"])
    candidate_by_section = defaultdict(set)
    for binding in b2["proposal-bindings.json"]:
        candidate_by_section[binding["section_id"]].add(binding["candidate_record_id"])
    if not _sections_match(section_index["sections"], graph["sections"],
                           candidate_by_section):
        raise ValueError("B3 sections differ from B2 bound section index")
    if audit_sections(graph["sections"], section_index["bodies"],
                      graph["records"], graph["snapshot"]) != audit:
        raise ValueError("B3 section audit differs from dependency graph")
    queue = assemble_queue(
        originals, dispositions, b2["candidate-records.json"],
        b2["proposal-bindings.json"], b2["field-vocabulary-review.json"],
        section_index, audit, old_bindings, b2["review-items.json"])
    if (queue["summary"]["fields"] != b2_summary["candidate_field_count"] or
            queue["summary"]["content_decisions"] != b2_summary["decision_rows"] or
            queue["summary"]["sections"] != len(graph["sections"]) or
            queue["summary"]["section_findings"] != b3_summary["inherited_section_findings"]):
        raise ValueError("content queue counts differ from audited inputs")
    os.umask(0o077)
    args.outdir.mkdir(mode=0o700, parents=True, exist_ok=False)
    output_hashes = {}
    for name, key in (("field-definitions.json", "field_definitions"),
                      ("content-decisions.json", "content_decisions"),
                      ("section-reviews.json", "sections")):
        output_hashes[name] = _write(args.outdir / name, queue[key])
    summary = {**queue["summary"], "status": "unsigned_candidate_review_queue",
               "input_sha256": {"b2_summary": b2_sha, "b3_summary": b3_sha,
                                "originals": originals_sha, "batch": batch_hashes,
                                "bound_section_index": section_sha,
                                "old_bindings": old_bindings_sha,
                                "dependency_bundle": graph_sha,
                                "section_consistency_audit": audit_sha, **b2_hashes},
               "output_sha256": output_hashes}
    _write(args.outdir / "summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--b2-review-dir", type=Path, required=True)
    parser.add_argument("--b3-review-dir", type=Path, required=True)
    parser.add_argument("--originals", type=Path, required=True)
    parser.add_argument("--batch", type=Path, action="append", required=True)
    parser.add_argument("--bound-section-index", type=Path, required=True)
    parser.add_argument("--old-bindings", type=Path, required=True)
    parser.add_argument("--outdir", type=Path, required=True)
    result = build(parser.parse_args())
    print(json.dumps({key: result[key] for key in (
        "fields", "content_decisions", "sections", "section_findings", "online_eligible")},
        ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
