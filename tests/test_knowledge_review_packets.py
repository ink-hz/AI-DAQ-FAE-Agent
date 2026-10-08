import copy

import pytest

from daq_fae.knowledge.review_packets import build_review_packet, compare_review_packets


def _snapshot() -> dict:
    return {
        "format_version": 1, "extractor_version": "1", "archive_manifest_sha256": "a" * 64,
        "sources": [
            {"path": "spec/ego.pdf", "sha256": "b" * 64, "kind": "pdf"},
            {"path": "guide/ego.md", "sha256": "c" * 64, "kind": "markdown"},
            {"path": "software/new.zip", "sha256": "d" * 64, "kind": "asset"},
        ],
        "chunks": [
            {"source_path": "spec/ego.pdf", "source_sha256": "b" * 64,
             "locator": {"kind": "page", "page": 1}, "text_sha256": "e" * 64,
             "text": "Document Classification: Confidential\nEGO baseline 100 mm"},
            {"source_path": "guide/ego.md", "source_sha256": "c" * 64,
             "locator": {"kind": "lines", "start": 7, "end": 10}, "text_sha256": "f" * 64,
             "text": "# Pairing\nConnect EGO and WristCam"},
        ],
    }


def _recipe() -> dict:
    return {"version": "k2-1", "cases": [
        {"id": "claim:baseline", "group": "conflict", "question": "Which baseline applies?",
         "owner": "product_rd", "selectors": [
             {"source_glob": "spec/*.pdf", "pattern": "baseline\\s+100\\s*mm"},
             {"source_glob": "spec/*.pdf", "pattern": "baseline\\s+120\\s*mm"},
         ]},
        {"id": "procedure:pairing", "group": "procedure", "question": "What pairing works?",
         "owner": "product_rd", "selectors": [
             {"source_glob": "guide/*.md", "pattern": "Connect EGO and WristCam"},
         ]},
    ]}


def test_packet_is_deterministic_located_and_never_grants_access():
    first = build_review_packet(_snapshot(), _recipe())
    assert first == build_review_packet(_snapshot(), _recipe())
    assert first["status"] == "candidate_review_only"
    assert first["archive_manifest_sha256"] == "a" * 64
    baseline = first["cases"][0]
    assert baseline["status"] == "pending"
    assert baseline["evidence"][0]["source_ref"] == {
        "path": "spec/ego.pdf", "sha256": "b" * 64,
        "locator": {"kind": "page", "page": 1},
    }
    assert "100 mm" in baseline["evidence"][0]["excerpt"]
    assert baseline["missing_selectors"] == [1]
    assert first["source_markings"]["spec/ego.pdf"]["claimed_classification"] == "confidential"
    assert first["source_markings"]["guide/ego.md"]["claimed_classification"] == "unknown"
    assert all(value == "pending" for row in first["access_review"].values()
               for value in row["view_roles"].values())
    assert all(value == "pending" for row in first["access_review"].values()
               for value in row["forward_roles"].values())
    assert first["unmapped_sources"] == ["software/new.zip"]


def test_diff_flags_changed_evidence_new_unmapped_source_and_recipe_change():
    old = build_review_packet(_snapshot(), _recipe())
    snapshot = _snapshot()
    snapshot["sources"][0]["sha256"] = "1" * 64
    snapshot["chunks"][0]["source_sha256"] = "1" * 64
    snapshot["sources"].append({"path": "software/other.zip", "sha256": "2" * 64,
                                "kind": "asset"})
    recipe = _recipe()
    recipe["cases"][1]["question"] = "Changed review question"
    current = build_review_packet(snapshot, recipe)
    delta = compare_review_packets(old, current)
    assert delta["sources"]["changed"] == ["spec/ego.pdf"]
    assert delta["sources"]["added"] == ["software/other.zip"]
    assert delta["cases"]["changed"] == ["claim:baseline", "procedure:pairing"]
    assert delta["recipe_changed"] is True
    assert delta["unmapped_source_changes"] == ["software/other.zip"]


def test_match_excerpts_are_bounded_and_source_labels_do_not_authorize():
    snapshot = _snapshot()
    snapshot["chunks"][0]["text"] = "Document Classification: Public\n" + "x" * 500 + "baseline 100 mm" + "y" * 500
    packet = build_review_packet(snapshot, _recipe())
    assert len(packet["cases"][0]["evidence"][0]["excerpt"]) <= 260
    assert packet["source_markings"]["spec/ego.pdf"]["claimed_classification"] == "public"
    assert packet["access_review"]["spec/ego.pdf"]["view_roles"]["internal_fae"] == "pending"


def test_bad_recipe_and_stale_chunk_are_rejected():
    recipe = _recipe()
    recipe["cases"].append(copy.deepcopy(recipe["cases"][0]))
    with pytest.raises(ValueError, match="duplicate"):
        build_review_packet(_snapshot(), recipe)
    stale = _snapshot()
    stale["chunks"][0]["source_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="source hash"):
        build_review_packet(stale, _recipe())
    bad_pattern = _recipe()
    bad_pattern["cases"][0]["selectors"][0]["pattern"] = "["
    with pytest.raises(ValueError, match="invalid selector pattern"):
        build_review_packet(_snapshot(), bad_pattern)


def test_selector_change_flags_its_case_even_when_hits_stay_the_same():
    old = build_review_packet(_snapshot(), _recipe())
    recipe = _recipe()
    recipe["cases"][0]["selectors"][0]["pattern"] = "[Bb]aseline\\s+100\\s*mm"
    # The candidate match is the same, but the future source coverage rule changed.
    current = build_review_packet(_snapshot(), recipe)
    assert compare_review_packets(old, current)["cases"]["changed"] == ["claim:baseline"]


def test_changed_source_that_loses_match_enters_unmapped_review_queue():
    old = build_review_packet(_snapshot(), _recipe())
    snapshot = _snapshot()
    snapshot["sources"][0]["sha256"] = "1" * 64
    snapshot["chunks"][0]["source_sha256"] = "1" * 64
    snapshot["chunks"][0]["text"] = "Document Classification: Confidential\nNew, unrelated spec"
    current = build_review_packet(snapshot, _recipe())
    delta = compare_review_packets(old, current)
    assert delta["sources"]["changed"] == ["spec/ego.pdf"]
    assert delta["cases"]["changed"] == ["claim:baseline"]
    assert delta["unmapped_source_changes"] == ["spec/ego.pdf"]
