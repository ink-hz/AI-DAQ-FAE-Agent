"""Build a private, unsigned B3 impact graph from a reviewed B2 candidate delta.

The output remains a draft review artifact. It cannot freeze Dev questions,
grant evidence access, activate knowledge or change a release pointer.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

from daq_fae.knowledge.normalized_impact import extend_review_graph
from daq_fae.knowledge.update_impact import plan_update


def _load(path: Path):
    raw = path.read_bytes()
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def _private_write(path: Path, value) -> str:
    body = (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(body)
    return hashlib.sha256(body).hexdigest()


def build(args) -> dict:
    """Verify immutable inputs before writing a new private output directory."""
    if args.outdir.exists():
        raise FileExistsError(args.outdir)
    baseline, baseline_sha = _load(args.baseline_bundle)
    baseline_manifest, baseline_manifest_sha = _load(args.baseline_manifest)
    if baseline_manifest.get(args.baseline_bundle.name) != baseline_sha:
        raise ValueError("baseline graph artifact hash mismatch")
    review_summary, review_summary_sha = _load(args.review_dir / "summary.json")
    names = ("candidate-records.json", "proposal-bindings.json",
             "field-vocabulary-review.json")
    inputs = {}
    input_hashes = {}
    for name in names:
        inputs[name], input_hashes[name] = _load(args.review_dir / name)
        if review_summary.get("output_sha256", {}).get(name) != input_hashes[name]:
            raise ValueError("review artifact hash mismatch: " + name)
    enriched = extend_review_graph(
        baseline, inputs["candidate-records.json"], inputs["proposal-bindings.json"],
        inputs["field-vocabulary-review.json"])
    addition = plan_update(baseline, enriched)
    identity = plan_update(enriched, deepcopy(enriched))
    if any(identity["affected"].values()):
        raise ValueError("enriched graph is not stable under identical inputs")
    used_paths = sorted({ref["path"] for row in inputs["candidate-records.json"]
                         for ref in row["source_refs"]})
    source_impacts = []
    for path in used_paths:
        changed = deepcopy(enriched)
        source = next(row for row in changed["snapshot"]["sources"] if row["path"] == path)
        source["sha256"] = "0" * 64 if source["sha256"] != "0" * 64 else "1" * 64
        report = plan_update(enriched, changed)
        source_impacts.append({"source_path": path, "scenario": "hypothetical_hash_change",
                               "affected": report["affected"],
                               "withdraw_positive_record_ids": report["withdraw_positive_record_ids"],
                               "reusable_signature_record_ids": report["reusable_signature_record_ids"]})
    os.umask(0o077)
    args.outdir.mkdir(mode=0o700, parents=True, exist_ok=False)
    output_hashes = {}
    output_hashes["dependency-bundle.json"] = _private_write(
        args.outdir / "dependency-bundle.json", enriched)
    output_hashes["addition-impact.json"] = _private_write(
        args.outdir / "addition-impact.json", addition)
    output_hashes["source-update-impact-map.json"] = _private_write(
        args.outdir / "source-update-impact-map.json", source_impacts)
    summary = {
        "online_eligible": False, "fact_review": None, "permission_review": None,
        "dev_question_status": "draft_not_frozen_not_replayed_not_approved",
        "baseline_records": len(baseline["records"]),
        "candidate_records": len(inputs["candidate-records.json"]),
        "total_records": len(enriched["records"]),
        "baseline_coverage_cells": len(baseline["coverage"]),
        "new_coverage_cells": len(enriched["coverage"]) - len(baseline["coverage"]),
        "total_coverage_cells": len(enriched["coverage"]),
        "new_draft_questions": len(enriched["questions"]) - len(baseline["questions"]),
        "total_draft_questions": len(enriched["questions"]),
        "candidate_field_definitions": len(inputs["field-vocabulary-review.json"]["fields"]),
        "source_update_scenarios": len(source_impacts),
        "graph_nodes": len(identity["graph"]["nodes"]),
        "graph_edges": len(identity["graph"]["edges"]),
        "input_sha256": {"baseline_bundle": baseline_sha,
                         "baseline_manifest": baseline_manifest_sha,
                         "review_summary": review_summary_sha, **input_hashes},
        "output_sha256": output_hashes,
    }
    _private_write(args.outdir / "summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-bundle", type=Path, required=True)
    parser.add_argument("--baseline-manifest", type=Path, required=True)
    parser.add_argument("--review-dir", type=Path, required=True)
    parser.add_argument("--outdir", type=Path, required=True)
    result = build(parser.parse_args())
    print(json.dumps({k: result[k] for k in (
        "candidate_records", "new_coverage_cells", "new_draft_questions",
        "candidate_field_definitions", "source_update_scenarios", "online_eligible")},
        ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
