"""A B2 normalization delta extends only the private B3 review graph."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from daq_fae.knowledge.normalized_impact import extend_review_graph
from daq_fae.knowledge.update_impact import plan_update
from daq_fae.knowledge.section_consistency import render_record
from scripts.prepare_b3_normalized_impact import _review_snapshot, build


REF = {"path": "restricted/spec.md", "sha256": "a" * 64,
       "locator": {"kind": "lines", "start": 1, "end": 2}}


def inputs():
    original = {"id": "claim:old", "kind": "claim", "status": "candidate",
                "scope": {"product": "ego", "resolution_variant": "1600x1200"},
                "source_refs": [deepcopy(REF)],
                "data": {"entity_id": "entity:ego", "field": "source_text",
                         "value": "2 cameras", "unit": "source_text", "conditions": {}}}
    section_body = render_record(original)
    section = {"section_id": "section:imaging", "source_refs": [deepcopy(REF)],
               "scope": deepcopy(original["scope"]), "dependency_claim_ids": ["claim:old"],
               "record_assertions": [deepcopy(original)],
               "body_sha256": hashlib.sha256(section_body.encode()).hexdigest()}
    baseline = {
        "snapshot": {"sources": [{"path": REF["path"], "sha256": REF["sha256"],
                                  "kind": "markdown"}], "extractor_version": "1",
                     "chunks": [{"source_path": REF["path"], "source_sha256": REF["sha256"],
                                 "locator": REF["locator"]}]},
        "records": [original], "sections": [section], "coverage": [], "questions": [],
        "sku_definitions": {}, "field_definitions": {},
    }
    candidate = {"id": "claim:new", "kind": "claim", "status": "candidate",
                 "scope": deepcopy(original["scope"]),
                 "source_refs": [deepcopy(REF)],
                 "data": {"entity_id": "entity:ego", "field": "camera_count",
                          "value": 2, "unit": "camera", "comparator": "=",
                          "conditions": {"document_only": True},
                          "original_record_id": "claim:old"}}
    binding = {"candidate_record_id": candidate["id"], "original_record_id": original["id"],
               "section_id": section["section_id"], "source_ref": deepcopy(REF)}
    vocabulary = {"fields": [{"field_id": "camera_count", "units": ["camera"],
                              "vocabulary_status": "candidate_review_pending"}],
                  "blocked_field_ids": ["camera_count"]}
    return baseline, [candidate], [binding], vocabulary


def test_exact_scoped_candidate_reaches_section_coverage_and_question_without_promotion():
    baseline, candidates, bindings, vocabulary = inputs()
    untouched = deepcopy((baseline, candidates, bindings, vocabulary))
    enriched = extend_review_graph(baseline, candidates, bindings, vocabulary)
    assert (baseline, candidates, bindings, vocabulary) == untouched
    assert len(enriched["records"]) == 2
    row = enriched["records"][-1]
    assert row["status"] == "candidate"
    assert "claim:new" in enriched["sections"][0]["dependency_claim_ids"]
    assert "dependency_section_ids" not in row
    assert row["dependency_record_ids"] == ["claim:old"]
    cell = enriched["coverage"][0]
    assert cell["record_ids"] == ["claim:new"]
    assert cell["scope"] == {"product_scope": candidate_scope(),
                             "conditions": {"document_only": True}}
    assert cell["status"] == "candidate"
    assert enriched["questions"][0]["status"] == "draft"
    assert enriched["questions"][0]["frozen"] is False
    assert enriched["field_definitions"]["camera_count"]["status"] == "candidate_review_pending"
    added = plan_update(baseline, enriched)
    assert "claim:new" in added["affected"]["record_ids"]
    assert enriched["questions"][0]["question_id"] in added["affected"]["question_ids"]
    assert added["reusable_signature_record_ids"] == []


def candidate_scope():
    return {"product": "ego", "resolution_variant": "1600x1200"}


def test_source_change_and_field_definition_change_reach_only_bound_candidate():
    baseline, candidates, bindings, vocabulary = inputs()
    enriched = extend_review_graph(baseline, candidates, bindings, vocabulary)
    changed = deepcopy(enriched)
    changed["snapshot"]["sources"][0]["sha256"] = "c" * 64
    report = plan_update(enriched, changed)
    assert "claim:new" in report["affected"]["record_ids"]
    assert enriched["questions"][0]["question_id"] in report["affected"]["question_ids"]
    assert report["withdraw_positive_record_ids"] == []
    changed = deepcopy(enriched)
    changed["field_definitions"]["camera_count"]["candidate"]["units"] = ["piece"]
    assert enriched["questions"][0]["question_id"] in plan_update(enriched, changed)["affected"]["question_ids"]


@pytest.mark.parametrize("mutate", [
    lambda c, b, v: c[0].update(status="verified"),
    lambda c, b, v: b[0].update(original_record_id="claim:wrong"),
    lambda c, b, v: b[0].update(section_id="section:wrong"),
    lambda c, b, v: b[0]["source_ref"].update(sha256="f" * 64),
    lambda c, b, v: c[0]["scope"].update(product="eg-db"),
    lambda c, b, v: c[0]["scope"].update(extra_variant="invented"),
    lambda c, b, v: c[0]["source_refs"][0]["locator"].update(start=3),
    lambda c, b, v: b.clear(),
    lambda c, b, v: v.update(blocked_field_ids=[]),
    lambda c, b, v: v["fields"][0].update(vocabulary_status="verified"),
])
def test_unreviewed_graph_rejects_unbound_or_premature_data(mutate):
    baseline, candidates, bindings, vocabulary = inputs()
    mutate(candidates, bindings, vocabulary)
    with pytest.raises(ValueError):
        extend_review_graph(baseline, candidates, bindings, vocabulary)


def test_candidate_cannot_repoint_source_location_even_if_binding_changes_with_it():
    baseline, candidates, bindings, vocabulary = inputs()
    candidates[0]["source_refs"][0]["locator"]["start"] = 3
    bindings[0]["source_ref"]["locator"]["start"] = 3
    with pytest.raises(ValueError):
        extend_review_graph(baseline, candidates, bindings, vocabulary)


def test_different_applicability_stays_in_distinct_coverage_cells():
    baseline, candidates, bindings, vocabulary = inputs()
    another = deepcopy(candidates[0])
    another["id"] = "claim:another"
    another["data"]["conditions"]["document_revision"] = "v2"
    candidates.append(another)
    bindings.append(dict(bindings[0], candidate_record_id=another["id"]))
    enriched = extend_review_graph(baseline, candidates, bindings, vocabulary)
    assert len(enriched["coverage"]) == len(enriched["questions"]) == 2
    assert {tuple(c["record_ids"]) for c in enriched["coverage"]} == {
        ("claim:new",), ("claim:another",)}


def test_two_sources_in_one_section_recheck_both_assertions_and_normalizations():
    baseline, candidates, bindings, vocabulary = inputs()
    other_ref = deepcopy(REF)
    other_ref["path"] = "restricted/other.md"
    baseline["snapshot"]["sources"].append({"path": other_ref["path"],
                                               "sha256": other_ref["sha256"], "kind": "markdown"})
    baseline["snapshot"]["chunks"].append({"source_path": other_ref["path"],
                                              "source_sha256": other_ref["sha256"],
                                              "locator": other_ref["locator"]})
    original = deepcopy(baseline["records"][0])
    original["id"] = "claim:other-old"
    original["source_refs"] = [deepcopy(other_ref)]
    baseline["records"].append(original)
    baseline["sections"][0]["source_refs"].append(deepcopy(other_ref))
    baseline["sections"][0]["record_assertions"].append(deepcopy(original))
    baseline["sections"][0]["dependency_claim_ids"].append(original["id"])
    candidate = deepcopy(candidates[0])
    candidate["id"] = "claim:other-new"
    candidate["source_refs"] = [deepcopy(other_ref)]
    candidate["data"]["original_record_id"] = original["id"]
    candidate["data"]["field"] = "other_field"
    candidates.append(candidate)
    bindings.append({"candidate_record_id": candidate["id"],
                     "original_record_id": original["id"],
                     "section_id": "section:imaging", "source_ref": deepcopy(other_ref)})
    vocabulary["fields"].append({"field_id": "other_field", "units": ["camera"],
                                  "vocabulary_status": "candidate_review_pending"})
    vocabulary["blocked_field_ids"].append("other_field")
    enriched = extend_review_graph(baseline, candidates, bindings, vocabulary)
    changed = deepcopy(enriched)
    changed["snapshot"]["sources"][0]["sha256"] = "c" * 64
    affected = plan_update(enriched, changed)["affected"]
    assert "section:imaging" in affected["section_ids"]
    assert "claim:new" in affected["record_ids"]
    assert "claim:other-new" in affected["record_ids"]
    other_question = next(q["question_id"] for q in enriched["questions"]
                          if any(c["record_ids"] == ["claim:other-new"] and
                                 c["coverage_id"] in q["coverage_ids"] for c in enriched["coverage"]))
    assert other_question in affected["question_ids"]


def _write_json(path: Path, value) -> str:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True))
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_alternate_pdf_extraction_is_recorded_without_replacing_snapshot_text():
    baseline, _, _, _ = inputs()
    source_audit = [{"source_ref": deepcopy(REF), "raw_text": "layout text",
                     "text_sha256": hashlib.sha256(b"layout text").hexdigest()}]
    baseline["snapshot"]["chunks"][0]["text"] = "extractor text"
    expanded, alternatives = _review_snapshot(baseline, source_audit, {"chunks": []})
    assert expanded["snapshot"]["chunks"][0]["text"] == "extractor text"
    assert alternatives == [{"source_ref": REF,
                             "snapshot_text_sha256": hashlib.sha256(b"extractor text").hexdigest(),
                             "review_text_sha256": hashlib.sha256(b"layout text").hexdigest()}]


def test_private_cli_verifies_input_hashes_and_refuses_reused_output(tmp_path):
    baseline, candidates, bindings, vocabulary = inputs()
    baseline["snapshot"]["chunks"] = []
    context_ref = deepcopy(REF)
    context_ref["path"] = "restricted/context.md"
    baseline["snapshot"]["sources"].append({"path": context_ref["path"],
                                               "sha256": context_ref["sha256"], "kind": "markdown"})
    baseline["sections"][0]["source_refs"].append(context_ref)
    review = tmp_path / "review"
    review.mkdir()
    hashes = {}
    for name, value in (("candidate-records.json", candidates),
                        ("proposal-bindings.json", bindings),
                        ("field-vocabulary-review.json", vocabulary),
                        ("subline-locator-overlay.json", {
                            "review_only": True, "source_snapshot_sha256": "a" * 64,
                            "chunks": [{"source_path": REF["path"],
                                        "source_sha256": REF["sha256"],
                                        "locator": REF["locator"], "text": "2 cameras",
                                        "text_sha256": hashlib.sha256(b"2 cameras").hexdigest()}]})):
        hashes[name] = _write_json(review / name, value)
    _write_json(review / "summary.json", {"output_sha256": hashes,
                                           "source_snapshot_sha256": "a" * 64})
    baseline_path = tmp_path / "baseline.json"
    baseline_sha = _write_json(baseline_path, baseline)
    manifest = tmp_path / "baseline-manifest.json"
    _write_json(manifest, {baseline_path.name: baseline_sha})
    source_audit = tmp_path / "source-extraction-audit.json"
    _write_json(source_audit, [{"source_ref": context_ref, "raw_text": "context",
                                "text_sha256": hashlib.sha256(b"context").hexdigest()}])
    section_index = tmp_path / "bound-section-index.json"
    _write_json(section_index, {"sections": baseline["sections"],
                                "bodies": {"section:imaging": render_record(baseline["records"][0])}})
    output = tmp_path / "output"
    args = SimpleNamespace(baseline_bundle=baseline_path, baseline_manifest=manifest,
                           review_dir=review, source_extraction_audit=source_audit,
                           bound_section_index=section_index, outdir=output)
    summary = build(args)
    assert summary["candidate_records"] == 1
    assert summary["new_coverage_cells"] == 1
    assert summary["source_update_scenarios"] == 2
    assert summary["record_findings"] == 0
    assert summary["new_section_findings"] == 0
    assert summary["online_eligible"] is False
    assert (output / "dependency-bundle.json").stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        build(args)
    hashes["candidate-records.json"] = "0" * 64
    _write_json(review / "summary.json", {"output_sha256": hashes,
                                           "source_snapshot_sha256": "a" * 64})
    args.outdir = tmp_path / "other"
    with pytest.raises(ValueError, match="review artifact hash"):
        build(args)


def test_private_cli_rejects_missing_candidate_locator_even_when_source_hash_matches(tmp_path):
    baseline, candidates, bindings, vocabulary = inputs()
    baseline["snapshot"]["chunks"] = []
    review = tmp_path / "review"
    review.mkdir()
    hashes = {}
    for name, value in (("candidate-records.json", candidates),
                        ("proposal-bindings.json", bindings),
                        ("field-vocabulary-review.json", vocabulary),
                        ("subline-locator-overlay.json", {
                            "review_only": True, "source_snapshot_sha256": "a" * 64,
                            "chunks": []})):
        hashes[name] = _write_json(review / name, value)
    _write_json(review / "summary.json", {"output_sha256": hashes,
                                           "source_snapshot_sha256": "a" * 64})
    baseline_path = tmp_path / "baseline.json"
    baseline_sha = _write_json(baseline_path, baseline)
    manifest = tmp_path / "manifest.json"
    _write_json(manifest, {baseline_path.name: baseline_sha})
    source_audit = tmp_path / "source-audit.json"
    _write_json(source_audit, [])
    section_index = tmp_path / "sections.json"
    _write_json(section_index, {"sections": baseline["sections"],
                                "bodies": {"section:imaging": render_record(baseline["records"][0])}})
    args = SimpleNamespace(baseline_bundle=baseline_path, baseline_manifest=manifest,
                           review_dir=review, source_extraction_audit=source_audit,
                           bound_section_index=section_index, outdir=tmp_path / "output")
    with pytest.raises(ValueError, match="source_locator_missing"):
        build(args)
