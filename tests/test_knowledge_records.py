from copy import deepcopy
import hashlib
import json

from daq_fae.knowledge.records import impact_report, validate_records


HASH = "a" * 64
OTHER_HASH = "b" * 64
SOURCE = {"path": "EGO/spec.md", "sha256": HASH,
          "locator": {"kind": "lines", "start": 1, "end": 2}}
SNAPSHOT = {
    "sources": [{"path": "EGO/spec.md", "sha256": HASH, "kind": "markdown"}],
    "chunks": [{"source_path": "EGO/spec.md", "source_sha256": HASH,
                "locator": SOURCE["locator"], "text": "EGO specification"}],
}


def _bind_reviews(row):
    core = {key: row[key] for key in ("id", "kind", "status", "scope", "source_refs", "data")}
    digest = hashlib.sha256(json.dumps(core, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")).encode()).hexdigest()
    for key in ("fact_review", "link_review"):
        if key in row:
            row[key]["record_sha256"] = digest
    if "access_review" in row:
        access = row["access_review"]
        payload = {"record_sha256": digest,
                   "view_roles": sorted(access["view_roles"]),
                   "forward_roles": sorted(access["forward_roles"])}
        access["record_sha256"] = hashlib.sha256(json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest()
    return row


def _record(kind="claim", status="verified", **overrides):
    data = {
        "entity": {"name": "EGO 1600", "entity_type": "variant"},
        "claim": {"entity_id": "entity:ego-1600", "field": "resolution",
                  "value": "1600x1200", "unit": "px", "conditions": {}},
        "topology": {"members": ["entity:ego-1600"], "roles": {},
                     "connections": [], "power": {}, "platform": "Windows",
                     "sync_target": "none", "storage": "TF"},
        "procedure": {"task": "record", "topology_id": "topology:single",
                      "prerequisites": [], "steps": ["start"], "checks": ["file"],
                      "failure_branches": []},
        "software": {"entity_id": "entity:ego-1600", "hardware_revision": "A",
                     "platform": "Windows", "connection_mode": "USB",
                     "software": "EgoViewer", "version": "2.0.11",
                     "capability": "record", "evidence_level": "end_to_end_verified"},
        "link": {"url": "https://example.com/ego", "title": "EGO",
                 "link_type": "documentation"},
    }[kind]
    row = {
        "id": f"{kind}:test", "kind": kind, "status": status,
        "scope": {"revision": "A"}, "source_refs": [deepcopy(SOURCE)],
        "data": data,
    }
    if status in {"verified", "unsupported"}:
        row["fact_review"] = {"reviewer": "fae-owner", "reviewed_at": "2026-10-08"}
        row["access_review"] = {"reviewer": "content-owner", "reviewed_at": "2026-10-08",
                                "view_roles": ["internal_fae"], "forward_roles": []}
    if kind == "link" and status == "verified":
        row["link_review"] = {"reviewer": "link-owner", "reviewed_at": "2026-10-08",
                              "final_url": data["url"]}
    row.update(overrides)
    return _bind_reviews(row)


def test_all_six_record_kinds_keep_reviewed_provenance():
    rows = [_record(kind, **({"id": "entity:ego-1600"} if kind == "entity" else
                             {"id": "topology:single"} if kind == "topology" else {}))
            for kind in ("entity", "claim", "topology", "procedure", "software", "link")]
    normalized, findings = validate_records(rows, SNAPSHOT)
    assert findings == []
    assert len(normalized) == 6
    assert all(row["answerable"] for row in normalized)


def test_answerable_claim_cannot_reference_an_absent_entity():
    normalized, findings = validate_records([_record()], SNAPSHOT)
    assert "entity_reference_missing" in {finding["code"] for finding in findings}
    assert normalized[0]["answerable"] is False


def test_source_drift_and_duplicate_id_fail_closed():
    stale = _record(source_refs=[{**SOURCE, "sha256": OTHER_HASH}])
    normalized, findings = validate_records([stale, _record()], SNAPSHOT)
    assert len(normalized) == 2
    assert {finding["code"] for finding in findings} >= {
        "source_hash_mismatch", "duplicate_id",
    }


def test_conflict_keeps_candidates_and_never_becomes_answerable():
    row = _record(status="conflict", data={
        "entity_id": "entity:ego-pro", "field": "baseline", "unit": "mm",
        "conditions": {}, "candidates": [
            {"value": 100, "source_ref": deepcopy(SOURCE)},
            {"value": 120, "source_ref": deepcopy(SOURCE)},
        ],
    })
    normalized, findings = validate_records([row], SNAPSHOT)
    assert findings == []
    assert normalized[0]["answerable"] is False
    assert [candidate["value"] for candidate in normalized[0]["data"]["candidates"]] == [100, 120]


def test_verified_needs_independent_fact_and_access_review_and_link_review():
    row = _record(kind="link")
    row.pop("access_review")
    row.pop("link_review")
    _, findings = validate_records([row], SNAPSHOT)
    assert {finding["code"] for finding in findings} == {
        "access_review_missing", "link_review_missing",
    }


def test_added_sources_recheck_negative_claims_and_changed_sources_impact_rows():
    rows = [_record(), _record(status="unsupported", id="claim:negative")]
    report = impact_report(rows, {
        "added": ["new-guide.md"], "changed": ["EGO/spec.md"],
        "removed": [], "unchanged": [],
    })
    assert report["impacted_record_ids"] == ["claim:negative", "claim:test"]
    assert report["recheck_negative_ids"] == ["claim:negative"]


def test_asset_metadata_cannot_verify_device_compatibility():
    snapshot = {"sources": [{"path": "SDK/fw.zip", "sha256": HASH, "kind": "asset"}],
                "chunks": []}
    row = _record(kind="software", source_refs=[{
        "path": "SDK/fw.zip", "sha256": HASH, "locator": {"kind": "file"},
    }])
    _, findings = validate_records([row], snapshot)
    assert "asset_cannot_verify_support" in {finding["code"] for finding in findings}


def test_malformed_review_and_locator_return_findings_instead_of_crashing():
    row = _record(kind="link")
    row["access_review"]["forward_roles"] = [["internal_fae"]]
    row["source_refs"][0]["locator"] = {"kind": "lines", "start": [1], "end": 2}
    _, findings = validate_records([row], SNAPSHOT)
    assert {finding["code"] for finding in findings} >= {
        "access_review_missing", "source_locator_missing",
    }


def test_distinct_verified_values_for_same_claim_scope_are_not_answerable():
    first = _record(id="claim:first")
    second = _record(id="claim:second")
    second["data"]["value"] = "1920x1200"
    _bind_reviews(second)
    normalized, findings = validate_records([first, second], SNAPSHOT)
    assert "unadjudicated_conflict" in {finding["code"] for finding in findings}
    assert not any(row["answerable"] for row in normalized)


def test_unresolved_conflict_blocks_same_field_verified_answer():
    verified = _record(id="claim:verified", data={
        "entity_id": "entity:ego-pro", "field": "baseline", "value": 100,
        "unit": "mm", "conditions": {},
    })
    unresolved = _record(id="claim:unresolved", status="conflict", data={
        "entity_id": "entity:ego-pro", "field": "baseline", "unit": "mm",
        "conditions": {}, "candidates": [
            {"value": 100, "source_ref": deepcopy(SOURCE)},
            {"value": 120, "source_ref": deepcopy(SOURCE)},
        ],
    })
    normalized, findings = validate_records([verified, unresolved], SNAPSHOT)
    assert "unadjudicated_conflict" in {finding["code"] for finding in findings}
    assert not any(row["answerable"] for row in normalized)


def test_review_is_bound_to_exact_source_and_value():
    row = _record()
    changed = deepcopy(row)
    changed["source_refs"][0]["sha256"] = OTHER_HASH
    changed["data"]["value"] = "1920x1200"
    new_snapshot = deepcopy(SNAPSHOT)
    new_snapshot["sources"][0]["sha256"] = OTHER_HASH
    new_snapshot["chunks"][0]["source_sha256"] = OTHER_HASH
    _, findings = validate_records([changed], new_snapshot)
    assert {finding["code"] for finding in findings} >= {
        "fact_review_stale", "access_review_stale",
    }


def test_access_expansion_requires_new_access_review():
    row = _record()
    row["access_review"]["view_roles"].append("channel")
    _, findings = validate_records([row], SNAPSHOT)
    assert "access_review_stale" in {finding["code"] for finding in findings}


def test_empty_claim_fields_and_unknown_role_are_invalid_even_with_matching_reviews():
    row = _record()
    row["data"].update({"entity_id": None, "field": "", "value": None,
                        "unit": None, "conditions": None})
    row["scope"] = {}
    row["access_review"]["view_roles"] = ["arbitrary_role"]
    _bind_reviews(row)
    _, findings = validate_records([row], SNAPSHOT)
    assert {finding["code"] for finding in findings} >= {
        "scope_invalid", "claim_data_invalid", "access_review_missing",
    }


def test_impact_report_includes_conflict_candidate_sources():
    row = _record(status="conflict", data={"entity_id": "entity:ego-pro", "field": "baseline",
                                           "unit": "mm", "conditions": {}, "candidates": [
                                               {"value": 100, "source_ref": {"path": "other.md"}},
                                               {"value": 120, "source_ref": deepcopy(SOURCE)},
                                           ]})
    row["source_refs"] = [deepcopy(SOURCE)]
    report = impact_report([row], {"added": [], "changed": ["other.md"],
                                   "removed": [], "unchanged": []})
    assert report["impacted_record_ids"] == ["claim:test"]
