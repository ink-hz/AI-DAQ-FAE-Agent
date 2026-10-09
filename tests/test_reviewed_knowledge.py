from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from daq_fae.app import create_app
from daq_fae.knowledge.records import access_fingerprint, record_fingerprint
from daq_fae.knowledge.releases import activate_release, publish_release
from daq_fae.knowledge.reviewed_view import ReviewedKnowledge
from daq_fae.domain_tools import DaqToolBox
from daq_fae.task_context import prepare_turn


SOURCE_SHA = "a" * 64
SOURCE_REF = {"path": "spec.md", "sha256": SOURCE_SHA,
              "locator": {"kind": "lines", "start": 1, "end": 2}}
SNAPSHOT = {
    "archive_manifest_sha256": "f" * 64,
    "sources": [{"path": "spec.md", "sha256": SOURCE_SHA,
                 "size": 20, "kind": "markdown"}],
    "chunks": [{"source_path": "spec.md", "source_sha256": SOURCE_SHA,
                "locator": SOURCE_REF["locator"], "text": "EGO specification"}],
}
RELEASE_REVIEW = {"reviewer": "release-owner", "reviewed_at": "2026-10-09",
                  "dev_batch": "synthetic-k4"}


def _row(record_id, kind, data, *, view=("internal_fae",), forward=()):
    row = {"id": record_id, "kind": kind, "status": "verified",
           "scope": {"variant": "1600"}, "source_refs": [deepcopy(SOURCE_REF)],
           "data": data,
           "fact_review": {"reviewer": "fae", "reviewed_at": "2026-10-09"},
           "access_review": {"reviewer": "owner", "reviewed_at": "2026-10-09",
                             "view_roles": list(view), "forward_roles": list(forward)}}
    row["fact_review"]["record_sha256"] = record_fingerprint(row)
    row["access_review"]["record_sha256"] = access_fingerprint(row)
    if kind == "link":
        row["link_review"] = {"reviewer": "link-owner", "reviewed_at": "2026-10-09",
                              "final_url": data["url"],
                              "record_sha256": record_fingerprint(row)}
    return row


def _records(*, claim_view=("internal_fae",), link_forward=()):
    return [
        _row("entity:ego-1600", "entity", {"name": "EGO 1600", "entity_type": "variant"}),
        _row("claim:resolution", "claim", {"entity_id": "entity:ego-1600",
             "field": "resolution", "value": "1600x1200", "unit": "px", "conditions": {}},
             view=claim_view),
        _row("link:ego", "link", {"url": "https://example.com/ego", "title": "EGO",
             "link_type": "documentation"}, forward=link_forward),
    ]


def test_active_release_is_frozen_and_reviewed_records_are_role_filtered(tmp_path):
    first = publish_release(tmp_path, SNAPSHOT, _records(), None, RELEASE_REVIEW)
    activate_release(tmp_path, first)
    view = ReviewedKnowledge.load_active(tmp_path)
    assert view.release_id == first
    assert [row["id"] for row in view.records_for("internal_fae")] == [
        "claim:resolution", "entity:ego-1600",
    ]
    assert view.records_for("channel") == []
    assert view.records_for("internal_fae", for_delivery=True) == []
    second = publish_release(tmp_path, SNAPSHOT, _records(claim_view=("internal_fae", "channel")),
                             first, RELEASE_REVIEW)
    activate_release(tmp_path, second)
    assert view.release_id == first
    assert ReviewedKnowledge.load_active(tmp_path).release_id == second


def test_forward_authorization_is_separate_from_view_permission(tmp_path):
    release_id = publish_release(tmp_path, SNAPSHOT,
                                 _records(link_forward=("internal_fae",)),
                                 None, RELEASE_REVIEW)
    activate_release(tmp_path, release_id)
    view = ReviewedKnowledge.load_active(tmp_path)
    assert [row["id"] for row in view.records_for("internal_fae", for_delivery=True)] == [
        "link:ego",
    ]
    assert view.records_for("channel", for_delivery=True) == []


def test_manifest_that_lies_about_review_or_source_fails_closed(tmp_path):
    release_id = publish_release(tmp_path, SNAPSHOT, _records(), None, RELEASE_REVIEW)
    activate_release(tmp_path, release_id)
    manifest = ReviewedKnowledge.load_active(tmp_path).manifest
    stale = deepcopy(manifest)
    stale["records"][0]["data"]["value"] = "fabricated"
    with pytest.raises(ValueError, match="review|record"):
        ReviewedKnowledge.from_manifest(release_id, stale)
    stale = deepcopy(manifest)
    stale["sources"][0]["sha256"] = "b" * 64
    with pytest.raises(ValueError, match="source"):
        ReviewedKnowledge.from_manifest(release_id, stale)


def test_toolbox_returns_only_visible_matching_claim_with_governed_source(tmp_path):
    release_id = publish_release(tmp_path, SNAPSHOT, _records(), None, RELEASE_REVIEW)
    activate_release(tmp_path, release_id)
    view = ReviewedKnowledge.load_active(tmp_path)
    requirements = [
        {"id": "resolution", "capability": "lookup_spec", "field": "resolution",
         "entities": ["EGO 1600"], "conditions": {}},
        {"id": "wrong-field", "capability": "lookup_spec", "field": "fps",
         "entities": ["EGO 1600"], "conditions": {}},
    ]
    box = DaqToolBox(knowledge=view, role="internal_fae", requirements=requirements)
    result = box.dispatch("lookup_spec", {"entity": "EGO 1600", "field": "resolution"})
    assert result.status == "ok"
    assert result.content["matches"][0]["data"]["value"] == "1600x1200"
    assert result.content["matched_requirement_ids"] == ["resolution"]
    assert result.sources[0]["type"] == "daq_governed_claim"
    assert result.sources[0]["release_id"] == release_id
    assert result.sources[0]["source_refs"] == [SOURCE_REF]
    missing = box.dispatch("lookup_spec", {"entity": "EGO 1600", "field": "fps"})
    assert missing.status == "not_found"
    assert missing.content["matched_requirement_ids"] == ["wrong-field"]
    assert box.dispatch("lookup_spec", {"entity": "another device", "field": "fps"}).content[
        "matched_requirement_ids"] == []
    channel = DaqToolBox(knowledge=view, role="channel", requirements=requirements)
    assert channel.dispatch("lookup_spec", {"entity": "EGO 1600", "field": "resolution"}).status == "not_found"
    assert channel.dispatch("lookup_spec", {"entity": "EGO 1600", "field": "resolution"}).sources == []


def test_link_needs_forward_permission_in_all_tool_paths(tmp_path):
    release_id = publish_release(tmp_path, SNAPSHOT, _records(), None, RELEASE_REVIEW)
    activate_release(tmp_path, release_id)
    box = DaqToolBox(knowledge=ReviewedKnowledge.load_active(tmp_path), role="internal_fae")
    for tool in ("official_links", "search_knowledge"):
        result = box.dispatch(tool, {"query": "example.com/ego"})
        assert result.status == "not_found"
        assert result.sources == []
        assert "example.com" not in str(result.content)


def test_forwardable_link_is_delivered_only_by_link_tool(tmp_path):
    release_id = publish_release(tmp_path, SNAPSHOT,
                                 _records(link_forward=("internal_fae",)),
                                 None, RELEASE_REVIEW)
    activate_release(tmp_path, release_id)
    box = DaqToolBox(knowledge=ReviewedKnowledge.load_active(tmp_path), role="internal_fae")
    assert box.dispatch("official_links", {"query": "example.com/ego"}).status == "ok"
    assert box.dispatch("search_knowledge", {"query": "example.com/ego"}).status == "not_found"


def test_lookup_spec_requires_scope_when_verified_values_differ(tmp_path):
    rows = _records()
    other = _row("claim:resolution-other", "claim", {
        "entity_id": "entity:ego-1600", "field": "resolution",
        "value": "1920x1200", "unit": "px", "conditions": {},
    })
    other["scope"] = {"variant": "1920"}
    other["fact_review"]["record_sha256"] = record_fingerprint(other)
    other["access_review"]["record_sha256"] = access_fingerprint(other)
    rows.append(other)
    release_id = publish_release(tmp_path, SNAPSHOT, rows, None, RELEASE_REVIEW)
    activate_release(tmp_path, release_id)
    box = DaqToolBox(knowledge=ReviewedKnowledge.load_active(tmp_path), role="internal_fae")
    assert box.dispatch("lookup_spec", {"entity": "EGO 1600", "field": "resolution"}).status == "not_found"
    selected = box.dispatch("lookup_spec", {
        "entity": "EGO 1600", "field": "resolution", "conditions": {"variant": "1600"},
    })
    assert selected.status == "ok"
    assert [row["data"]["value"] for row in selected.content["matches"]] == ["1600x1200"]


def test_shared_product_name_requires_explicit_resolution_variant(tmp_path):
    rows = []
    for variant, fov in (("1600x1200", 165), ("1920x1200", 149)):
        suffix = variant.split("x")[0]
        scope = {"product": "ego", "resolution_variant": variant,
                 "required_selectors": ["resolution_variant"]}
        entity = _row(f"entity:ego-{suffix}", "entity",
                      {"name": "EGO", "entity_type": "variant"})
        claim = _row(f"claim:ego-{suffix}-fov", "claim", {
            "entity_id": entity["id"], "field": "horizontal_fov", "value": fov,
            "unit": "deg", "conditions": {},
        })
        for row in (entity, claim):
            row["scope"] = deepcopy(scope)
            row["fact_review"]["record_sha256"] = record_fingerprint(row)
            row["access_review"]["record_sha256"] = access_fingerprint(row)
            rows.append(row)
    release_id = publish_release(tmp_path, SNAPSHOT, rows, None, RELEASE_REVIEW)
    activate_release(tmp_path, release_id)
    requirements = [
        {"id": "generic", "capability": "lookup_spec", "field": "horizontal_fov",
         "entities": ["EGO"], "conditions": {}},
        {"id": "selected", "capability": "lookup_spec", "field": "horizontal_fov",
         "entities": ["EGO"], "conditions": {"resolution_variant": "1600x1200"}},
    ]
    box = DaqToolBox(knowledge=ReviewedKnowledge.load_active(tmp_path),
                     role="internal_fae", requirements=requirements)
    generic = box.dispatch("lookup_spec", {"entity": "EGO", "field": "horizontal_fov"})
    assert generic.status == "not_found"
    selected = box.dispatch("lookup_spec", {
        "entity": "EGO", "field": "horizontal_fov",
        "conditions": {"resolution_variant": "1600x1200"},
    })
    assert [row["data"]["value"] for row in selected.content["matches"]] == [165]
    assert selected.content["matched_requirement_ids"] == ["selected"]
    search = box.dispatch("search_knowledge", {"query": "EGO"})
    assert all(row["kind"] != "claim" for row in search.content["matches"])
    single_root = tmp_path / "single"
    single_release = publish_release(single_root, SNAPSHOT, rows[:2], None, RELEASE_REVIEW)
    activate_release(single_root, single_release)
    single = DaqToolBox(knowledge=ReviewedKnowledge.load_active(single_root),
                        role="internal_fae")
    assert single.dispatch("lookup_spec", {
        "entity": "EGO", "field": "horizontal_fov",
    }).status == "not_found"
    planned = prepare_turn("设备是 EGO；分辨率版本是 1600×1200；水平视场角是多少？")
    planned_box = DaqToolBox(knowledge=ReviewedKnowledge.load_active(tmp_path),
                             role="internal_fae", requirements=planned.requirements)
    planned_result = planned_box.dispatch("lookup_spec", {
        "entity": "EGO", "field": "horizontal_fov",
        "conditions": {"resolution_variant": "1600x1200"},
    })
    assert planned_result.content["matched_requirement_ids"] == [
        next(item["id"] for item in planned.requirements
             if item["capability"] == "lookup_spec")
    ]
    broad = prepare_turn("设备是 EGO；分辨率版本是 1600×1200；规格是什么？")
    broad_box = DaqToolBox(knowledge=ReviewedKnowledge.load_active(tmp_path),
                           role="internal_fae", requirements=broad.requirements)
    broad_result = broad_box.dispatch("lookup_spec", {
        "entity": "EGO", "field": "horizontal_fov",
        "conditions": {"resolution_variant": "1600x1200"},
    })
    assert broad_result.content["matched_requirement_ids"] == []


def test_query_only_experience_cannot_expose_variant_scoped_claim(tmp_path):
    entity = _row("entity:ego-1600", "entity",
                  {"name": "EGO", "entity_type": "variant"})
    claim = _row("claim:ego-experience", "claim", {
        "entity_id": entity["id"], "field": "experience",
        "value": "EGO variant observation", "unit": "note", "conditions": {},
    })
    for row in (entity, claim):
        row["scope"] = {"product": "ego", "resolution_variant": "1600x1200",
                        "required_selectors": ["resolution_variant"]}
        row["fact_review"]["record_sha256"] = record_fingerprint(row)
        row["access_review"]["record_sha256"] = access_fingerprint(row)
    release_id = publish_release(tmp_path, SNAPSHOT, [entity, claim], None, RELEASE_REVIEW)
    activate_release(tmp_path, release_id)
    box = DaqToolBox(knowledge=ReviewedKnowledge.load_active(tmp_path), role="internal_fae")
    assert box.dispatch("experience", {"query": "EGO"}).status == "not_found"


def test_software_support_accepts_explicit_variant_selector(tmp_path):
    entity = _row("entity:ego-1600", "entity",
                  {"name": "EGO", "entity_type": "variant"})
    software = _row("software:ego-viewer-1600", "software", {
        "entity_id": entity["id"], "hardware_revision": "A", "platform": "Windows",
        "connection_mode": "USB", "software": "EgoViewer", "version": "2.0",
        "capability": "record", "evidence_level": "end_to_end_verified",
    })
    for row in (entity, software):
        row["scope"] = {"product": "ego", "resolution_variant": "1600x1200",
                        "required_selectors": ["resolution_variant"]}
        row["fact_review"]["record_sha256"] = record_fingerprint(row)
        row["access_review"]["record_sha256"] = access_fingerprint(row)
    release_id = publish_release(tmp_path, SNAPSHOT, [entity, software], None, RELEASE_REVIEW)
    activate_release(tmp_path, release_id)
    box = DaqToolBox(knowledge=ReviewedKnowledge.load_active(tmp_path), role="internal_fae")
    args = {"entity": "EGO", "software": "EgoViewer", "platform": "Windows",
            "version": "2.0", "hardware_revision": "A", "connection_mode": "USB",
            "capability": "record"}
    assert box.dispatch("check_software_support", args).status == "not_found"
    assert box.dispatch("check_software_support", {
        **args, "conditions": {"resolution_variant": "1600x1200"},
    }).status == "ok"
    schema = next(item["function"] for item in box.tool_schemas()
                  if item["function"]["name"] == "check_software_support")
    assert "conditions" in schema["parameters"]["properties"]


def test_local_dev_app_reports_loaded_immutable_release(tmp_path):
    release_id = publish_release(tmp_path, SNAPSHOT, _records(), None, RELEASE_REVIEW)
    activate_release(tmp_path, release_id)
    app = create_app(provider_mode="offline", knowledge_release_root=tmp_path,
                     state_db_path=tmp_path / "state.sqlite3")
    assert TestClient(app).get("/health").json()["knowledge_release"] == release_id
    assert app.state.daq_knowledge.release_id == release_id


def test_authenticated_app_rejects_real_release_without_role_contract(tmp_path, monkeypatch):
    release_id = publish_release(tmp_path, SNAPSHOT, _records(), None, RELEASE_REVIEW)
    activate_release(tmp_path, release_id)
    monkeypatch.setenv("DAQ_PLATFORM_IDENTITY_ENABLED", "true")
    monkeypatch.setenv("DAQ_DATABASE_URL", "postgresql://placeholder/daq")
    monkeypatch.setenv("DAQ_AUTHENTICATED_CONTENT_KEYRING_FILE", str(tmp_path / "keyring.json"))
    with pytest.raises(ValueError, match="role_contract"):
        create_app(provider_mode="offline", knowledge_release_root=tmp_path)


def test_release_reader_rejects_linked_root_and_malformed_roles(tmp_path):
    release_id = publish_release(tmp_path / "real", SNAPSHOT, _records(), None, RELEASE_REVIEW)
    activate_release(tmp_path / "real", release_id)
    (tmp_path / "linked").symlink_to(tmp_path / "real", target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        ReviewedKnowledge.load_active(tmp_path / "linked")
    manifest = ReviewedKnowledge.load_active(tmp_path / "real").manifest
    manifest["records"][0]["access_review"]["view_roles"] = [["internal_fae"]]
    with pytest.raises(ValueError, match="review"):
        ReviewedKnowledge.from_manifest(release_id, manifest)


def test_release_reader_rejects_unknown_record_kind_and_unsafe_source_path(tmp_path):
    release_id = publish_release(tmp_path, SNAPSHOT, _records(), None, RELEASE_REVIEW)
    activate_release(tmp_path, release_id)
    manifest = ReviewedKnowledge.load_active(tmp_path).manifest
    bad = deepcopy(manifest)
    bad["records"][0]["kind"] = "unreviewed_note"
    with pytest.raises(ValueError, match="record"):
        ReviewedKnowledge.from_manifest(release_id, bad)
    bad = deepcopy(manifest)
    bad["sources"][0]["path"] = "../private/spec.md"
    with pytest.raises(ValueError, match="source"):
        ReviewedKnowledge.from_manifest(release_id, bad)


def test_unresolved_or_platform_hinted_scope_cannot_satisfy_evidence(tmp_path):
    release_id = publish_release(tmp_path, SNAPSHOT, _records(), None, RELEASE_REVIEW)
    activate_release(tmp_path, release_id)
    view = ReviewedKnowledge.load_active(tmp_path)
    requirements = [
        {"id": "unresolved", "capability": "lookup_spec", "field": "resolution",
         "entities": [], "conditions": {}, "reason": "intent_or_entity_not_grounded"},
        {"id": "hinted", "capability": "lookup_spec", "field": "resolution",
         "entities": ["EGO 1600"], "conditions": {"variant": "1600"},
         "conditions_authority": "platform_context_unverified"},
    ]
    box = DaqToolBox(knowledge=view, role="internal_fae", requirements=requirements)
    result = box.dispatch("lookup_spec", {
        "entity": "EGO 1600", "field": "resolution", "conditions": {"variant": "1600"},
    })
    assert result.status == "ok"
    assert result.content["matched_requirement_ids"] == []


def test_non_link_record_cannot_embed_unreviewed_url(tmp_path):
    rows = _records()
    software = _row("software:viewer", "software", {
        "entity_id": "entity:ego-1600", "hardware_revision": "A",
        "platform": "Windows", "connection_mode": "USB", "software": "EgoViewer",
        "version": "2.0", "capability": "record",
        "evidence_level": "end_to_end_verified",
        "url": "https://example.com/private-download",
    })
    rows.append(software)
    release_id = publish_release(tmp_path, SNAPSHOT, rows, None, RELEASE_REVIEW)
    activate_release(tmp_path, release_id)
    with pytest.raises(ValueError, match="URL"):
        ReviewedKnowledge.load_active(tmp_path)


def test_non_link_record_cannot_embed_protocol_relative_delivery_link(tmp_path):
    rows = _records()
    rows[1]["data"]["value"] = "//example.com/private?token=abc"
    rows[1]["fact_review"]["record_sha256"] = record_fingerprint(rows[1])
    rows[1]["access_review"]["record_sha256"] = access_fingerprint(rows[1])
    release_id = publish_release(tmp_path, SNAPSHOT, rows, None, RELEASE_REVIEW)
    activate_release(tmp_path, release_id)
    with pytest.raises(ValueError, match="URL"):
        ReviewedKnowledge.load_active(tmp_path)


@pytest.mark.parametrize("value", ["www.example.com/private", "example.com/private?token=abc",
                                     "www.example.com", "example.com?token=abc"])
def test_non_link_record_cannot_embed_bare_delivery_link(tmp_path, value):
    rows = _records()
    rows[1]["data"]["value"] = value
    rows[1]["fact_review"]["record_sha256"] = record_fingerprint(rows[1])
    rows[1]["access_review"]["record_sha256"] = access_fingerprint(rows[1])
    release_id = publish_release(tmp_path, SNAPSHOT, rows, None, RELEASE_REVIEW)
    activate_release(tmp_path, release_id)
    with pytest.raises(ValueError, match="URL"):
        ReviewedKnowledge.load_active(tmp_path)


def test_non_link_record_cannot_hide_delivery_link_in_data_key(tmp_path):
    rows = _records()
    rows[1]["data"]["//example.com/private?token=abc"] = True
    rows[1]["fact_review"]["record_sha256"] = record_fingerprint(rows[1])
    rows[1]["access_review"]["record_sha256"] = access_fingerprint(rows[1])
    release_id = publish_release(tmp_path, SNAPSHOT, rows, None, RELEASE_REVIEW)
    activate_release(tmp_path, release_id)
    with pytest.raises(ValueError, match="URL"):
        ReviewedKnowledge.load_active(tmp_path)


def test_procedure_and_software_require_exact_product_topology_and_applicability(tmp_path):
    rows = _records()
    rows.append(_row("topology:ego-single", "topology", {
        "members": ["entity:ego-1600"], "roles": {}, "connections": [],
        "power": {}, "platform": "Windows", "sync_target": "none", "storage": "TF",
    }))
    rows.append(_row("procedure:record", "procedure", {
        "task": "record", "topology_id": "topology:ego-single", "prerequisites": [],
        "steps": ["start"], "checks": ["file"], "failure_branches": [],
    }))
    rows.append(_row("software:viewer", "software", {
        "entity_id": "entity:ego-1600", "hardware_revision": "A",
        "platform": "Windows", "connection_mode": "USB", "software": "EgoViewer",
        "version": "2.0", "capability": "record",
        "evidence_level": "end_to_end_verified",
    }))
    release_id = publish_release(tmp_path, SNAPSHOT, rows, None, RELEASE_REVIEW)
    activate_release(tmp_path, release_id)
    box = DaqToolBox(knowledge=ReviewedKnowledge.load_active(tmp_path), role="internal_fae",
                     requirements=[{"id": "record", "capability": "lookup_procedure",
                                    "entities": [], "conditions": {}}])
    assert box.dispatch("lookup_procedure", {"task": "record", "entity": "other-product"}).status == "not_found"
    procedure = box.dispatch("lookup_procedure", {"task": "record", "entity": "EGO 1600"})
    assert procedure.status == "ok"
    assert procedure.content["matched_requirement_ids"] == []
    request = {"entity": "EGO 1600", "software": "EgoViewer", "platform": "Windows",
               "version": "2.0", "hardware_revision": "B", "connection_mode": "Wi-Fi",
               "capability": "record"}
    assert box.dispatch("check_software_support", request).status == "not_found"
    request.update({"hardware_revision": "A", "connection_mode": "USB"})
    assert box.dispatch("check_software_support", request).status == "ok"


def test_exact_user_software_scope_can_cover_matching_requirement(tmp_path):
    rows = _records()
    rows.append(_row("software:viewer", "software", {
        "entity_id": "entity:ego-1600", "hardware_revision": "A",
        "platform": "Windows", "connection_mode": "USB", "software": "EgoViewer",
        "version": "2.0", "capability": "record",
        "evidence_level": "end_to_end_verified",
    }))
    release_id = publish_release(tmp_path, SNAPSHOT, rows, None, RELEASE_REVIEW)
    activate_release(tmp_path, release_id)
    plan = prepare_turn("设备是 EGO 1600，硬件修订是 A，连接方式是 USB，平台是 Windows，Viewer 2.0 的录制兼容吗？")
    requirement = next(row for row in plan.requirements
                       if row["capability"] == "check_software_support")
    assert requirement["conditions"]["connection"] == "USB"
    box = DaqToolBox(knowledge=ReviewedKnowledge.load_active(tmp_path), role="internal_fae",
                     requirements=plan.requirements)
    result = box.dispatch("check_software_support", {
        "entity": "EGO 1600", "software": "EgoViewer", "platform": "Windows",
        "version": "2.0", "hardware_revision": "A", "connection_mode": "USB",
        "capability": "record",
    })
    assert result.status == "ok"
    assert result.content["matched_requirement_ids"] == [requirement["id"]]


def test_broad_software_question_is_not_covered_by_recording_only_evidence(tmp_path):
    rows = _records()
    rows.append(_row("software:viewer", "software", {
        "entity_id": "entity:ego-1600", "hardware_revision": "A",
        "platform": "Windows", "connection_mode": "USB", "software": "EgoViewer",
        "version": "2.0", "capability": "record", "evidence_level": "device_tested",
    }))
    release_id = publish_release(tmp_path, SNAPSHOT, rows, None, RELEASE_REVIEW)
    activate_release(tmp_path, release_id)
    plan = prepare_turn("设备是 EGO 1600，硬件修订是 A，连接方式是 USB，平台是 Windows，Viewer 2.0 兼容吗？")
    requirement = next(row for row in plan.requirements
                       if row["capability"] == "check_software_support")
    box = DaqToolBox(knowledge=ReviewedKnowledge.load_active(tmp_path), role="internal_fae",
                     requirements=plan.requirements)
    result = box.dispatch("check_software_support", {
        "entity": "EGO 1600", "software": "EgoViewer", "platform": "Windows",
        "version": "2.0", "hardware_revision": "A", "connection_mode": "USB",
        "capability": "record",
    })
    assert result.status == "ok"
    assert requirement["id"] not in result.content["matched_requirement_ids"]


def test_viewer_record_cannot_certify_separate_sdk_version(tmp_path):
    rows = _records()
    rows.append(_row("software:viewer", "software", {
        "entity_id": "entity:ego-1600", "hardware_revision": "A",
        "platform": "Windows", "connection_mode": "USB", "software": "EgoViewer",
        "version": "2.0", "capability": "record",
        "evidence_level": "end_to_end_verified",
    }))
    release_id = publish_release(tmp_path, SNAPSHOT, rows, None, RELEASE_REVIEW)
    activate_release(tmp_path, release_id)
    box = DaqToolBox(knowledge=ReviewedKnowledge.load_active(tmp_path), role="internal_fae",
                     requirements=[{"id": "combo", "capability": "check_software_support",
                                    "entities": ["EGO 1600"], "software": "viewer",
                                    "conditions": {"platform": "Windows", "variant": "A",
                                                   "connection": "USB", "task": ["recording"],
                                                   "viewer_version": "2.0",
                                                   "sdk_version": "2.0"}}])
    result = box.dispatch("check_software_support", {
        "entity": "EGO 1600", "software": "EgoViewer", "platform": "Windows",
        "version": "2.0", "hardware_revision": "A", "connection_mode": "USB",
        "capability": "record",
    })
    assert result.status == "ok"
    assert result.content["matched_requirement_ids"] == []


def test_generic_search_does_not_certify_unresolved_product_evidence(tmp_path):
    release_id = publish_release(tmp_path, SNAPSHOT, _records(), None, RELEASE_REVIEW)
    activate_release(tmp_path, release_id)
    box = DaqToolBox(knowledge=ReviewedKnowledge.load_active(tmp_path), role="internal_fae",
                     requirements=[{"id": "search", "capability": "search_knowledge",
                                    "entities": [], "conditions": {}}])
    result = box.dispatch("search_knowledge", {"query": "resolution"})
    assert result.status == "ok"
    assert result.content["matched_requirement_ids"] == []


def test_entity_identity_does_not_certify_platform_filtered_catalog(tmp_path):
    release_id = publish_release(tmp_path, SNAPSHOT, [_records()[0]], None, RELEASE_REVIEW)
    activate_release(tmp_path, release_id)
    plan = prepare_turn("平台是 Linux，有哪些型号可用？")
    box = DaqToolBox(knowledge=ReviewedKnowledge.load_active(tmp_path), role="internal_fae",
                     requirements=plan.requirements)
    result = box.dispatch("catalog", {"query": ""})
    assert result.status == "ok"
    assert result.content["matched_requirement_ids"] == []
    result = box.dispatch("resolve_entity", {"text": "EGO"})
    assert result.status == "ok"
    assert result.content["matched_requirement_ids"] == []
