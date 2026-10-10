"""A private content queue joins source-checked B2 work without approvals."""

from copy import deepcopy

import pytest

from daq_fae.knowledge.content_review_queue import assemble_queue
from daq_fae.knowledge.records import record_fingerprint
from scripts.prepare_content_review_queue import _sections_match

REF = {"path": "private/spec.md", "sha256": "a" * 64,
       "locator": {"kind": "lines", "start": 2, "end": 2}}


def inputs():
    original = {"id": "claim:source", "kind": "claim", "status": "candidate",
                "scope": {"product": "ego"}, "source_refs": [deepcopy(REF)],
                "data": {"entity_id": "entity:ego", "field": "source_transcription.x",
                         "source_label": "相机/曝光", "value": "2 cameras",
                         "unit": "source_text", "conditions": {}}}
    candidate = {"id": "claim:camera", "kind": "claim", "status": "candidate",
                 "scope": deepcopy(original["scope"]), "source_refs": [deepcopy(REF)],
                 "data": {"entity_id": "entity:ego", "field": "camera_count",
                          "value": 2, "unit": "camera", "comparator": "=",
                          "conditions": {"document_only": True},
                          "original_record_id": original["id"]}}
    disposition = {"original_id": original["id"],
                   "original_sha256": record_fingerprint(original),
                   "source_refs": deepcopy(original["source_refs"]),
                   "disposition": "compound_needs_review",
                   "reason": "Exposure remains unresolved", "source_check": {"status": "matched"},
                   "proposed_claims": []}
    section = {"section_id": "section:imaging", "scope": deepcopy(original["scope"]),
               "source_refs": deepcopy(original["source_refs"]),
               "record_assertions": [deepcopy(original)],
               "dependency_claim_ids": [original["id"]], "title": "成像"}
    section_index = {"sections": [section], "bodies": {"section:imaging": "candidate body"}}
    old_bindings = [{"section_id": section["section_id"], "record_id": original["id"],
                     "source_refs": deepcopy(original["source_refs"])}]
    proposal_bindings = [{"section_id": section["section_id"],
                          "original_record_id": original["id"],
                          "candidate_record_id": candidate["id"], "source_ref": deepcopy(REF)}]
    vocabulary = {"fields": [{"field_id": "camera_count", "units": ["camera"],
                              "comparators": ["="], "record_ids": [candidate["id"]],
                              "review_reasons": ["new_field_definition"],
                              "vocabulary_status": "candidate_review_pending"}],
                  "blocked_field_ids": ["camera_count"]}
    audit = {"findings": [{"section_id": section["section_id"],
                           "record_id": None, "code": "uncovered_body"}]}
    review_items = [{"item_id": candidate["id"], "record": deepcopy(candidate),
                     "source_refs": deepcopy(candidate["source_refs"]),
                     "source_bodies": [{"source_ref": deepcopy(REF), "text": "原件摘录"}]}]
    return ([original], [disposition], [candidate], proposal_bindings,
            vocabulary, section_index, audit, old_bindings, review_items)


def test_queue_joins_field_decision_and_section_without_granting_approval():
    args = inputs()
    before = deepcopy(args)
    result = assemble_queue(*args)
    assert args == before
    assert result["summary"] == {"fields": 1, "content_decisions": 1,
                                 "sections": 1, "section_findings": 1,
                                 "fact_reviews": 0, "access_reviews": 0,
                                 "online_eligible": False}
    field = result["field_definitions"][0]
    assert field["field_id"] == "camera_count"
    assert field["observed_source_labels"] == ["相机/曝光"]
    assert field["observed_units"] == ["camera"]
    assert field["status"] == "candidate_review_pending"
    assert field["source_refs"] == [REF]
    assert field["proposed_name_zh"] is None
    assert field["candidate_review_items"][0]["source_bodies"][0]["text"] == "原件摘录"
    assert len(field["review_sha256"]) == 64
    decision = result["content_decisions"][0]
    assert decision["original_record_id"] == "claim:source"
    assert decision["section_ids"] == ["section:imaging"]
    assert decision["status"] == "pending_human_adjudication"
    section = result["sections"][0]
    assert section["candidate_record_ids"] == ["claim:camera"]
    assert section["content_decision_ids"] == ["claim:source"]
    assert section["findings"][0]["code"] == "uncovered_body"
    assert section["body"] == "candidate body"
    assert section["status"] == "candidate_requires_human_review"


@pytest.mark.parametrize("mutate", [
    lambda a: a[1][0].update(original_sha256="0" * 64),
    lambda a: a[2][0]["scope"].update(product="other"),
    lambda a: a[3][0].update(original_record_id="claim:wrong"),
    lambda a: a[3][0]["source_ref"].update(sha256="0" * 64),
    lambda a: a[4].update(blocked_field_ids=[]),
    lambda a: a[6]["findings"][0].update(section_id="section:other"),
    lambda a: a[7].clear(),
    lambda a: a[8][0]["source_bodies"][0]["source_ref"].update(sha256="0" * 64),
])
def test_queue_rejects_stale_or_incomplete_join(mutate):
    args = list(inputs())
    mutate(args)
    with pytest.raises(ValueError):
        assemble_queue(*args)


def test_enriched_graph_sections_allow_only_bound_candidate_dependencies():
    base = inputs()[5]["sections"]
    graph = deepcopy(base)
    graph[0]["dependency_claim_ids"].append("claim:camera")
    assert _sections_match(base, graph, {"section:imaging": {"claim:camera"}})
    assert not _sections_match(base, graph, {})
    graph[0]["title"] = "changed"
    assert not _sections_match(base, graph, {"section:imaging": {"claim:camera"}})
