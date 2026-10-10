"""Camera-style DAQ candidate files remain source-bound and unpublished."""

from copy import deepcopy

import pytest
import yaml

from daq_fae.knowledge.candidate_layout import compose_layout


def fixture():
    source = {"path": "spec.pdf", "sha256": "a" * 64, "size": 12}
    ref = {"path": source["path"], "sha256": source["sha256"],
           "locator": {"kind": "page", "page": 1}}
    manifest = {"files": [source]}
    text = {"archive_manifest_sha256": "m" * 64,
            "text_dispositions": [{**source, "status": "curated"}]}
    assets = {"archive_manifest_sha256": "m" * 64, "asset_inventory": []}
    product = {"section_id": "product:ego-1600:index:identity", "entity_id": "entity:ego-1600",
               "document": "products/ego-1600/index.md", "title": "型号身份",
               "source_refs": [ref], "review_status": "candidate", "fact_review": None,
               "permission_review": None, "view_roles": [], "forward_roles": []}
    system = {"section_id": "system:ego-standalone:overview",
              "topology_id": "topology:ego-standalone",
              "document": "systems/ego-standalone/overview.md", "title": "适用范围",
              "source_refs": [ref], "review_status": "candidate", "fact_review": None,
              "permission_review": None, "view_roles": [], "forward_roles": []}
    selection = {**system, "section_id": "guidance:ego-standalone:selection",
                 "document": "systems/ego-standalone/selection.md", "title": "选型"}
    groups = {
        "a2": ({"archive_manifest_sha256": "m" * 64, "sections": [product],
                "online_eligible": False}, {"products/ego-1600/index.md": b"# EGO\n"}),
        "a3": ({"archive_manifest_sha256": "m" * 64, "sections": [system],
                "online_eligible": False}, {"systems/ego-standalone/overview.md": b"# EGO system\n"}),
        "a4": ({"archive_manifest_sha256": "m" * 64, "sections": [selection],
                "online_eligible": False}, {"systems/ego-standalone/selection.md": b"# Selection\n"}),
    }
    old = [{"id": "claim:old", "kind": "claim", "status": "candidate",
            "scope": {"product": "ego"}, "source_refs": [ref],
            "data": {"entity_id": "entity:ego-1600", "field": "camera_count",
                     "value": 2, "unit": "camera"}}]
    transcriptions = [{"id": "claim:source", "kind": "claim", "status": "candidate",
                       "scope": {"product": "ego"}, "source_refs": [ref],
                       "data": {"entity_id": "entity:ego-1600",
                                "field": "source_transcription.camera", "source_label": "相机"}}]
    normalized = [{"id": "claim:new", "kind": "claim", "status": "candidate",
                   "scope": {"product": "ego"}, "source_refs": [ref],
                   "data": {"entity_id": "entity:ego-1600", "field": "camera_count",
                            "value": 2, "unit": "camera", "original_record_id": "claim:source"}}]
    graph = {"records": [*deepcopy(old), *deepcopy(transcriptions),
                         {**deepcopy(normalized[0]), "dependency_record_ids": ["claim:source"]}],
             "sections": deepcopy([product, system, selection]),
             "coverage": [{"entity_id": "entity:ego-1600", "status": "candidate"}]}
    dictionary = {"record_inventory": old, "fields": [], "names": [],
                  "relations": [], "ambiguities": []}
    vocabulary = {"fields": [{"field_id": "camera_count", "vocabulary_status":
                               "candidate_review_pending"}], "blocked_field_ids": ["camera_count"]}
    links = {"archive_manifest_sha256": "m" * 64, "approved_delivery_count": 0,
             "candidate_count": 0, "links": []}
    return ("m" * 64, manifest, text, assets, groups, dictionary, transcriptions, normalized,
            vocabulary, graph, {"findings": []}, [], links)


def test_layout_materializes_camera_layers_as_candidate_only():
    args = fixture()
    before = deepcopy(args)
    files, summary = compose_layout(*args)
    assert args == before
    assert files["products/ego-1600/index.md"] == b"# EGO\n"
    assert "systems/ego-standalone/overview.md" in files
    assert "systems/ego-standalone/selection.md" in files
    facts = yaml.safe_load(files["products/ego-1600/facts.yaml"])
    assert [row["record_id"] for row in facts["facts"]] == ["claim:old", "claim:new"]
    assert all(row["status"] == "candidate" for row in facts["facts"])
    assert all("record" not in row for row in facts["facts"])
    assert "_facts/field_dictionary.yaml" in files
    assert "_sources/source-disposition.json" in files
    assert "_sources/asset-index.md" in files
    assert "_facts/source-transcriptions.json" in files
    assert summary["online_eligible"] is False
    assert summary["sections"] == 3


@pytest.mark.parametrize("mutate", [
    lambda args: args[4]["a2"][0]["sections"][0]["source_refs"][0].update(sha256="b" * 64),
    lambda args: args[4]["a2"][1].clear(),
    lambda args: args[4]["a4"][0]["sections"][0].update(section_id="system:ego-standalone:overview"),
    lambda args: args[7][0].update(status="verified"),
    lambda args: args[6][0]["data"].update(source_label="tampered"),
    lambda args: args[9]["sections"][0].update(document="products/other/index.md"),
    lambda args: args[9]["sections"][0].update(review_status="verified"),
    lambda args: args[12].update(approved_delivery_count=1),
    lambda args: args[11].append({"id": "software:bad", "status": "candidate",
                                  "online_eligible": False, "fact_review": "pending",
                                  "permission_review": "pending", "view_roles": [],
                                  "forward_roles": [], "source_refs": [{"path": "spec.pdf",
                                  "sha256": "b" * 64}]}),
])
def test_layout_rejects_unbound_or_approved_candidate_input(mutate):
    args = list(fixture())
    mutate(args)
    with pytest.raises(ValueError):
        compose_layout(*args)
