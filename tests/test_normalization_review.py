"""B2 normalization review stays bound to exact candidate records and sources."""

from copy import deepcopy

import pytest

from daq_fae.knowledge.normalization_review import (
    audit_candidate_vocabulary,
    expand_snapshot_for_review,
    merge_normalization_reviews,
)
from daq_fae.knowledge.records import record_fingerprint
from scripts.prepare_b2_normalization_review import (
    audit_original_source_refs,
    field_vocabulary_sha256,
    validate_review_bindings,
)


REF = {"path": "private/spec.pdf", "sha256": "a" * 64,
       "locator": {"kind": "page", "page": 3}}


def original():
    return {"id": "claim:b2:original", "kind": "claim", "status": "candidate",
            "scope": {"product": "ego", "resolution_variant": "1600x1200",
                      "required_selectors": ["resolution_variant"]},
            "source_refs": [deepcopy(REF)],
            "data": {"entity_id": "entity:ego-1600", "field": "source_transcription.x",
                     "value": "two cameras", "unit": "source_text", "conditions": {}}}


def disposition(row):
    return {"original_id": row["id"], "original_sha256": record_fingerprint(row),
            "source_refs": deepcopy(row["source_refs"]), "disposition": "typed_proposal",
            "proposed_claims": [{"status": "candidate", "entity_id": "entity:ego-1600",
                                 "scope": deepcopy(row["scope"]), "source_refs": deepcopy(row["source_refs"]),
                                 "field": "camera_count", "value": 2, "unit": "camera",
                                 "comparator": "=", "conditions": {"document_only": True}}],
            "reason": "source table split",
            "source_check": {"status": "hash_and_locator_matched", "checks": [
                {"source_ref": deepcopy(REF), "actual_sha256": "a" * 64,
                 "hash_matches": True, "locator_exists": True}]}}


def test_merge_flat_proposal_is_stable_and_candidate_only():
    row = original()
    proposed, report = merge_normalization_reviews([row], [[disposition(row)]])
    assert len(proposed) == 1
    assert proposed[0]["id"].startswith("claim:b2n:")
    assert proposed[0]["status"] == "candidate"
    assert proposed[0]["data"]["original_record_id"] == row["id"]
    assert proposed[0]["scope"] == row["scope"]
    assert report["coverage"] == {"originals": 1, "dispositions": 1, "missing": 0}
    assert merge_normalization_reviews([row], [[disposition(row)]])[0] == proposed


@pytest.mark.parametrize("mutation", [
    lambda d: d.update(original_sha256="0" * 64),
    lambda d: d["source_check"]["checks"][0].update(actual_sha256="0" * 64),
    lambda d: d["proposed_claims"][0]["scope"].pop("resolution_variant"),
    lambda d: d["proposed_claims"][0].update(status="verified"),
    lambda d: d["proposed_claims"][0].update(source_refs=[]),
    lambda d: d["proposed_claims"][0].update(entity_id="entity:other-product"),
])
def test_merge_rejects_unbound_or_premature_proposal(mutation):
    row = original()
    entry = disposition(row)
    mutation(entry)
    with pytest.raises(ValueError):
        merge_normalization_reviews([row], [[entry]])


def test_merge_requires_exactly_one_disposition_per_original():
    row = original()
    with pytest.raises(ValueError):
        merge_normalization_reviews([row], [[]])
    with pytest.raises(ValueError):
        merge_normalization_reviews([row], [[disposition(row), disposition(row)]])


def test_conflict_keeps_no_positive_claim():
    row = original()
    entry = disposition(row)
    entry.update(disposition="conflict", proposed_claims=[])
    proposed, report = merge_normalization_reviews([row], [[entry]])
    assert proposed == []
    assert report["dispositions"] == {"conflict": 1}


def test_full_candidate_gets_original_binding_and_cannot_repoint():
    row = original()
    entry = disposition(row)
    flat = entry["proposed_claims"][0]
    entry["proposed_claims"] = [{"id": "claim:b2n:full", "kind": "claim", "status": "candidate",
                                 "scope": deepcopy(flat["scope"]), "source_refs": deepcopy(flat["source_refs"]),
                                 "data": {"entity_id": flat["entity_id"], "field": flat["field"],
                                          "value": flat["value"], "unit": flat["unit"],
                                          "conditions": flat["conditions"]}}]
    proposed, _ = merge_normalization_reviews([row], [[entry]])
    assert proposed[0]["data"]["original_record_id"] == row["id"]
    entry["proposed_claims"][0]["data"]["original_record_id"] = "claim:b2:other"
    with pytest.raises(ValueError):
        merge_normalization_reviews([row], [[entry]])


def test_review_snapshot_adds_only_rehashed_subline_locator(tmp_path):
    source = tmp_path / "private" / "guide.md"
    source.parent.mkdir()
    source.write_text("heading\nsource fact\nending\n")
    import hashlib
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    parent_ref = {"path": "private/guide.md", "sha256": sha,
                  "locator": {"kind": "lines", "start": 1, "end": 3}}
    child_ref = {"path": "private/guide.md", "sha256": sha,
                 "locator": {"kind": "lines", "start": 2, "end": 2}}
    snapshot = {"sources": [{"path": "private/guide.md", "sha256": sha,
                              "kind": "markdown", "encoding": "utf-8"}],
                "chunks": [{"source_path": parent_ref["path"],
                            "source_sha256": sha, "locator": parent_ref["locator"],
                            "text": source.read_text()}]}
    expanded = expand_snapshot_for_review(snapshot, [child_ref], tmp_path)
    assert len(expanded["chunks"]) == 2
    assert expanded["chunks"][1]["text"] == "source fact"
    assert len(snapshot["chunks"]) == 1
    source.write_text("heading\nchanged\nending\n")
    with pytest.raises(ValueError):
        expand_snapshot_for_review(snapshot, [child_ref], tmp_path)


def test_review_snapshot_rejects_unindexed_line_range(tmp_path):
    source = tmp_path / "guide.md"
    source.write_text("one\ntwo\nthree\n")
    import hashlib
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    snapshot = {"sources": [{"path": "guide.md", "sha256": sha,
                              "kind": "markdown", "encoding": "utf-8"}],
                "chunks": [{"source_path": "guide.md", "source_sha256": sha,
                            "locator": {"kind": "lines", "start": 1, "end": 1}}]}
    with pytest.raises(ValueError):
        expand_snapshot_for_review(snapshot, [{"path": "guide.md", "sha256": sha,
                                               "locator": {"kind": "lines", "start": 2, "end": 2}}], tmp_path)


def test_original_source_audit_reads_refs_even_for_no_proposal_rows():
    row = original()
    row2 = deepcopy(row)
    row2["id"] = "claim:b2:unresolved"
    row2["source_refs"] = [{"path": "private/other.pdf", "sha256": "b" * 64,
                            "locator": {"kind": "page", "page": 7}}]
    seen = []

    def read(path, sha, locator):
        seen.append(path)
        return "fact"

    assert audit_original_source_refs([row, row2], read) == 2
    assert seen == ["private/spec.pdf", "private/other.pdf"]


def test_review_binding_must_match_indexed_section_scope_and_sources():
    row = original()
    section = {"section_id": "product:ego:camera", "scope": deepcopy(row["scope"]),
               "source_refs": deepcopy(row["source_refs"]),
               "dependency_claim_ids": [row["id"]], "record_assertions": [deepcopy(row)]}
    binding = {"section_id": section["section_id"], "record_id": row["id"],
               "source_refs": deepcopy(row["source_refs"])}
    assert validate_review_bindings([row], [binding], {"sections": [section]})[row["id"]] == {
        section["section_id"]}
    bad = deepcopy(binding)
    bad["source_refs"] = []
    with pytest.raises(ValueError):
        validate_review_bindings([row], [bad], {"sections": [section]})
    bad = deepcopy(binding)
    bad["section_id"] = "product:other:camera"
    with pytest.raises(ValueError):
        validate_review_bindings([row], [bad], {"sections": [section]})
    wrong_section = deepcopy(section)
    wrong_section["scope"]["resolution_variant"] = "1920x1200"
    with pytest.raises(ValueError):
        validate_review_bindings([row], [binding], {"sections": [wrong_section]})
    stale_section = deepcopy(section)
    stale_section["record_assertions"][0]["data"]["value"] = "stale fact"
    with pytest.raises(ValueError):
        validate_review_bindings([row], [binding], {"sections": [stale_section]})


def test_candidate_vocabulary_flags_new_fields_and_unit_conflicts():
    row = original()
    p1, _ = merge_normalization_reviews([row], [[disposition(row)]])
    p2 = deepcopy(p1[0])
    p2["id"] = "claim:b2n:second"
    p2["data"]["unit"] = "pieces"
    report = audit_candidate_vocabulary(p1 + [p2], [{"field_id": "camera_count",
                                                       "units": ["camera"], "comparators": ["="],
                                                       "vocabulary_status": "candidate"}])
    assert report["field_count"] == 1
    assert report["multi_unit_field_ids"] == ["camera_count"]
    assert report["baseline_mismatch_field_ids"] == ["camera_count"]
    assert report["unreviewed_baseline_field_ids"] == ["camera_count"]
    assert set(report["blocked_field_ids"]) == {"camera_count"}
    p3 = deepcopy(p1[0])
    p3["id"] = "claim:b2n:third"
    p3["data"]["field"] = "new_field"
    report = audit_candidate_vocabulary([p3], [])
    assert report["new_field_ids"] == ["new_field"]
    assert report["blocked_field_ids"] == ["new_field"]


def test_matching_candidate_baseline_field_still_blocks_import():
    row = original()
    proposed, _ = merge_normalization_reviews([row], [[disposition(row)]])
    baseline = [{"field_id": "camera_count", "units": ["camera"], "comparators": ["="],
                 "vocabulary_status": "candidate"}]
    report = audit_candidate_vocabulary(proposed, baseline)
    assert report["baseline_mismatch_field_ids"] == []
    assert report["unreviewed_baseline_field_ids"] == ["camera_count"]
    assert report["blocked_field_ids"] == ["camera_count"]
    baseline[0]["vocabulary_status"] = "verified"
    # A plain status string is not a trusted signed field-definition review.
    assert audit_candidate_vocabulary(proposed, baseline)["blocked_field_ids"] == ["camera_count"]


def test_review_item_field_vocabulary_digest_changes_with_definition():
    row = original()
    proposed, _ = merge_normalization_reviews([row], [[disposition(row)]])
    baseline = {"format_version": "b1-v1", "fields": [
        {"field_id": "camera_count", "units": ["camera"], "comparators": ["="],
         "vocabulary_status": "candidate", "conversion_policy": "no conversion"}]}
    vocabulary = audit_candidate_vocabulary(proposed, baseline["fields"])
    before = field_vocabulary_sha256("camera_count", vocabulary, baseline)
    changed = deepcopy(baseline)
    changed["fields"][0]["conversion_policy"] = "convert"
    assert field_vocabulary_sha256("camera_count", vocabulary, changed) != before
    unrelated = deepcopy(baseline)
    unrelated["fields"].append({"field_id": "other_field", "units": ["ms"]})
    assert field_vocabulary_sha256("camera_count", vocabulary, unrelated) == before
