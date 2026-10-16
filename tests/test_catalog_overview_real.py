"""Catalog overview contract with a self-contained governed release."""

from daq_fae.domain_tools import DaqToolBox
from daq_fae.knowledge.records import access_fingerprint, record_fingerprint
from daq_fae.knowledge.releases import _publish_release, _read_release
from daq_fae.knowledge.reviewed_view import ReviewedKnowledge
from daq_fae.request_schema import DaqRequestSchema
from daq_fae.task_context import prepare_turn


SOURCE_SHA = "a" * 64
SOURCE_REF = {"path": "catalog.md", "sha256": SOURCE_SHA,
              "locator": {"kind": "lines", "start": 1, "end": 2}}
SNAPSHOT = {
    "archive_manifest_sha256": "f" * 64,
    "sources": [{"path": "catalog.md", "sha256": SOURCE_SHA,
                 "size": 20, "kind": "markdown"}],
    "chunks": [{"source_path": "catalog.md", "source_sha256": SOURCE_SHA,
                "locator": SOURCE_REF["locator"], "text": "Synthetic catalog"}],
}
REVIEW = {"reviewer": "catalog-contract-fixture", "reviewed_at": "2026-10-16",
          "dev_batch": "synthetic-catalog"}


def _entity(identifier: str, name: str, entity_type: str, *,
            scope: dict | None = None, role: str = "internal_fae") -> dict:
    row = {
        "id": f"entity:{identifier}", "kind": "entity", "status": "verified",
        "scope": scope or {"product": identifier}, "source_refs": [SOURCE_REF],
        "data": {"name": name, "entity_type": entity_type},
        "fact_review": {"reviewer": "fixture", "reviewed_at": "2026-10-16"},
        "access_review": {"reviewer": "fixture", "reviewed_at": "2026-10-16",
                          "view_roles": [role], "forward_roles": []},
    }
    row["fact_review"]["record_sha256"] = record_fingerprint(row)
    row["access_review"]["record_sha256"] = access_fingerprint(row)
    return row


def _six_entities() -> list[dict]:
    return [
        _entity("ego-1600", "EGO", "variant", scope={
            "product": "ego", "required_selectors": ["resolution_variant"],
            "resolution_variant": "1600x1200"}),
        _entity("ego-1920", "EGO", "variant", scope={
            "product": "ego", "required_selectors": ["resolution_variant"],
            "resolution_variant": "1920x1200"}),
        _entity("ego-pro", "EGO Pro", "device"),
        _entity("hub", "数采 HUB", "hub"),
        _entity("umi-finger", "UMI Finger", "device"),
        _entity("umi-vision-module", "UMI 视觉模块", "component"),
    ]


def _box(tmp_path, question: str, *, rows: list[dict] | None = None,
         role: str = "internal_fae", candidate_preview: bool = False,
         requirements: list[dict] | None = None) -> DaqToolBox:
    release_id = _publish_release(tmp_path, SNAPSHOT,
                                  rows if rows is not None else _six_entities(),
                                  None, REVIEW, sections=[], bodies={})
    manifest, _ = _read_release(tmp_path, release_id)
    knowledge = ReviewedKnowledge.from_manifest(release_id, manifest)
    if candidate_preview:
        knowledge.is_candidate_preview = True
    return DaqToolBox(question=question, knowledge=knowledge, role=role,
                      requirements=(requirements if requirements is not None
                                    else prepare_turn(question).requirements))


def test_how_many_products_is_recognized_in_both_workflow_paths():
    question = "你知道多少款数采产品"
    assert "catalog" in prepare_turn(question).planned_capabilities
    schema = DaqRequestSchema.from_candidate(question, {
        "confidence": "high", "goals": [{"kind": "product", "capability": "catalog",
                                   "entities": [], "conditions": {}}],
    })
    plan = prepare_turn(question, request_schema=schema)
    assert "catalog" in plan.planned_capabilities
    assert any(row["capability"] == "catalog" for row in plan.requirements)


def test_natural_language_question_scans_all_visible_entities(tmp_path):
    question = "你知道多少款数采产品"
    result = _box(tmp_path, question).dispatch("catalog", {"query": question})
    assert result.status == "ok"
    assert len(result.content["matches"]) == len(result.sources) == 6
    assert result.content["catalog_summary"] == {
        "entry_count": 6, "distinct_product_name_count": 4,
        "product_names": ["EGO", "EGO Pro", "UMI Finger", "数采 HUB"],
        "by_entity_type": {"component": 1, "device": 2, "hub": 1, "variant": 2},
        "scope": "released_role_visible_knowledge", "exhaustive_product_range": False,
    }
    assert result.content["matched_requirement_ids"]


def test_catalog_coverage_and_sources_include_entry_after_twenty(tmp_path):
    rows = [_entity(f"p{index:02d}", f"P{index:02d}", "device")
            for index in range(21)]
    rows.append(_entity("hidden", "Hidden", "device", role="channel"))
    requirement = {"id": "last", "capability": "catalog", "entities": ["P20"],
                   "conditions": {}, "conditions_authority": "user_supplied"}
    result = _box(tmp_path, "有哪些产品", rows=rows,
                  requirements=[requirement]).dispatch("catalog", {"query": "有哪些产品"})
    assert result.status == "ok"
    assert result.content["catalog_summary"]["entry_count"] == 21
    assert result.content["catalog_summary"]["distinct_product_name_count"] == 21
    assert len(result.content["matches"]) == len(result.sources) == 21
    assert result.content["matched_requirement_ids"] == ["last"]
    assert "Hidden" not in result.content["catalog_summary"]["product_names"]
    assert {source["source_id"] for source in result.sources} == {
        f"entity:p{index:02d}" for index in range(21)}


def test_dev_preview_scope_is_not_reported_as_published(tmp_path):
    result = _box(tmp_path, "有哪些产品", candidate_preview=True).dispatch(
        "catalog", {"query": "有哪些产品"})
    assert result.status == "ok"
    assert result.content["catalog_summary"]["scope"] == "source_checked_dev_preview"
    assert result.content["evidence_level"] == "dev_source_checked"


def test_catalog_with_platform_constraint_does_not_certify_availability(tmp_path):
    question = "平台是 Linux。有哪些数采产品可用？"
    result = _box(tmp_path, question).dispatch("catalog", {"query": question})
    assert result.status == "ok"
    assert result.content["catalog_summary"]["entry_count"] == 6
    assert result.content["matched_requirement_ids"] == []
