"""Build a private, unsigned B3 impact graph from a source-checked B2 delta.

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
from daq_fae.knowledge.records import validate_records
from daq_fae.knowledge.section_consistency import audit_sections
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


def _locator_key(chunk: dict) -> tuple[str, str, str]:
    return (chunk["source_path"], chunk["source_sha256"],
            json.dumps(chunk["locator"], sort_keys=True))


def _review_snapshot(baseline: dict, source_audit: list[dict], overlay: dict) -> tuple[dict, list[dict]]:
    result = deepcopy(baseline)
    snapshot = result["snapshot"]
    sources = {row["path"]: row for row in snapshot["sources"]}
    chunks = {_locator_key(row): row for row in snapshot["chunks"]}
    if len(chunks) != len(snapshot["chunks"]):
        raise ValueError("duplicate baseline extraction locator")
    baseline_keys = set(chunks)
    alternatives = []
    proposed = []
    for item in source_audit:
        ref, body = item["source_ref"], item["raw_text"]
        if hashlib.sha256(body.encode()).hexdigest() != item["text_sha256"]:
            raise ValueError("source extraction text hash mismatch")
        proposed.append({"source_path": ref["path"], "source_sha256": ref["sha256"],
                         "locator": ref["locator"], "text": body,
                         "text_sha256": item["text_sha256"]})
    proposed.extend(overlay["chunks"])
    for chunk in proposed:
        if (sources.get(chunk["source_path"], {}).get("sha256") != chunk["source_sha256"] or
                not isinstance(chunk.get("text"), str) or
                hashlib.sha256(chunk["text"].encode()).hexdigest() != chunk["text_sha256"]):
            raise ValueError("review locator differs from source or text hash")
        key = _locator_key(chunk)
        if key in chunks:
            if chunks[key].get("text") != chunk["text"]:
                if key not in baseline_keys:
                    raise ValueError("conflicting review locator text")
                old_text = chunks[key].get("text")
                alternatives.append({
                    "source_ref": {"path": chunk["source_path"],
                                   "sha256": chunk["source_sha256"],
                                   "locator": chunk["locator"]},
                    "snapshot_text_sha256": hashlib.sha256(old_text.encode()).hexdigest()
                    if isinstance(old_text, str) else None,
                    "review_text_sha256": chunk["text_sha256"],
                })
            continue
        chunks[key] = chunk
        snapshot["chunks"].append(chunk)
    return result, alternatives


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
             "field-vocabulary-review.json", "subline-locator-overlay.json")
    inputs = {}
    input_hashes = {}
    for name in names:
        inputs[name], input_hashes[name] = _load(args.review_dir / name)
        if review_summary.get("output_sha256", {}).get(name) != input_hashes[name]:
            raise ValueError("review artifact hash mismatch: " + name)
    overlay = inputs["subline-locator-overlay.json"]
    if (overlay.get("review_only") is not True or
            overlay.get("source_snapshot_sha256") != review_summary.get("source_snapshot_sha256")):
        raise ValueError("review locator overlay is not bound to B2 snapshot")
    source_audit, source_audit_sha = _load(args.source_extraction_audit)
    section_index, section_index_sha = _load(args.bound_section_index)
    if section_index["sections"] != baseline["sections"]:
        raise ValueError("section bodies do not match baseline section index")
    review_baseline, extractor_alternatives = _review_snapshot(baseline, source_audit, overlay)
    enriched = extend_review_graph(
        review_baseline, inputs["candidate-records.json"], inputs["proposal-bindings.json"],
        inputs["field-vocabulary-review.json"])
    _, record_findings = validate_records(enriched["records"], enriched["snapshot"])
    if record_findings:
        raise ValueError("normalized graph record findings: " + str(record_findings[:3]))
    bodies = section_index["bodies"]
    before_audit = audit_sections(review_baseline["sections"], bodies,
                                  review_baseline["records"], review_baseline["snapshot"])
    after_audit = audit_sections(enriched["sections"], bodies,
                                 enriched["records"], enriched["snapshot"])
    if after_audit["findings"] != before_audit["findings"]:
        raise ValueError("normalized graph introduces section consistency findings")
    addition = plan_update(review_baseline, enriched)
    identity = plan_update(enriched, deepcopy(enriched))
    if any(identity["affected"].values()):
        raise ValueError("enriched graph is not stable under identical inputs")
    candidate_sections = {row["section_id"] for row in inputs["proposal-bindings.json"]}
    used_paths = sorted(
        {ref["path"] for row in inputs["candidate-records.json"] for ref in row["source_refs"]} |
        {ref["path"] for row in enriched["sections"] if row["section_id"] in candidate_sections
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
    output_hashes["section-consistency-audit.json"] = _private_write(
        args.outdir / "section-consistency-audit.json", after_audit)
    output_hashes["extraction-alternatives.json"] = _private_write(
        args.outdir / "extraction-alternatives.json", extractor_alternatives)
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
        "record_findings": len(record_findings),
        "inherited_section_findings": len(before_audit["findings"]),
        "new_section_findings": len(after_audit["findings"]) - len(before_audit["findings"]),
        "alternate_extractions_same_locator": len(extractor_alternatives),
        "graph_nodes": len(identity["graph"]["nodes"]),
        "graph_edges": len(identity["graph"]["edges"]),
        "input_sha256": {"baseline_bundle": baseline_sha,
                         "baseline_manifest": baseline_manifest_sha,
                         "review_summary": review_summary_sha,
                         "source_extraction_audit": source_audit_sha,
                         "bound_section_index": section_index_sha, **input_hashes},
        "output_sha256": output_hashes,
    }
    _private_write(args.outdir / "summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-bundle", type=Path, required=True)
    parser.add_argument("--baseline-manifest", type=Path, required=True)
    parser.add_argument("--review-dir", type=Path, required=True)
    parser.add_argument("--source-extraction-audit", type=Path, required=True)
    parser.add_argument("--bound-section-index", type=Path, required=True)
    parser.add_argument("--outdir", type=Path, required=True)
    result = build(parser.parse_args())
    print(json.dumps({k: result[k] for k in (
        "candidate_records", "new_coverage_cells", "new_draft_questions",
        "candidate_field_definitions", "source_update_scenarios", "online_eligible")},
        ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
