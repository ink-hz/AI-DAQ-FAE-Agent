import hashlib
import json
from pathlib import Path

import pytest

from daq_fae.domain_evidence import DaqEvidencePolicy, question_requirements
from daq_fae.domain_tools import DaqToolBox
from daq_fae.knowledge.dev_candidate import DevCandidateKnowledge
from daq_fae.knowledge.records import record_fingerprint
from daq_fae.task_context import prepare_turn
from src.agent.loop.answer_contract import AnswerSubmission


def _write(path, value):
    raw = (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode()
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _fixture(tmp_path):
    graph_dir = tmp_path / "graph"
    graph_dir.mkdir()
    bound = tmp_path / "bound.json"
    archive = tmp_path / "archive"
    archive.mkdir()
    source = {"path": "guide.md", "sha256": "a" * 64, "size": 1, "kind": "text"}
    ref = {"path": source["path"], "sha256": source["sha256"],
           "locator": {"kind": "lines", "start": 1, "end": 1}}
    body = "EGO 单机试录步骤。"
    section = {"section_id": "system:ego:recording", "title": "EGO 单机录制",
               "scope": {"product": "ego"}, "source_refs": [ref],
               "dependency_claim_ids": [], "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
               "review_status": "candidate", "proposed_view_roles": ["internal_fae"]}
    snapshot = {"archive_manifest_sha256": "b" * 64, "sources": [source],
                "chunks": [{"source_path": source["path"],
                            "source_sha256": source["sha256"], "locator": ref["locator"]}]}
    bundle = {"records": [], "sections": [section], "snapshot": snapshot}
    bundle_sha = _write(graph_dir / "dependency-bundle.json", bundle)
    bound_sha = _write(bound, {"sections": [section], "bodies": {section["section_id"]: body}})
    _write(archive / "manifest.json", {"files": [source]})
    _write(graph_dir / "summary.json", {
        "output_sha256": {"dependency-bundle.json": bundle_sha},
        "input_sha256": {"bound_section_index": bound_sha}})
    return graph_dir, bound, archive, snapshot


def test_real_candidate_sections_are_searchable_but_not_governed(monkeypatch, tmp_path):
    graph_dir, bound, archive, snapshot = _fixture(tmp_path)
    monkeypatch.setattr("daq_fae.knowledge.dev_candidate.verify_archive",
                        lambda *_: {"manifest_sha256": snapshot["archive_manifest_sha256"],
                                    "sources": snapshot["sources"]})
    knowledge = DevCandidateKnowledge.load(graph_dir, bound, archive)
    assert knowledge.release_id.startswith("candidate-dev-")
    assert knowledge.records_for("internal_fae") == []
    assert knowledge.sections_for("channel") == []
    result = DaqToolBox(knowledge=knowledge, role="internal_fae").dispatch(
        "search_knowledge", {"query": "EGO 单机录制"})
    assert result.status == "ok"
    assert result.content["matches"][0]["status"] == "candidate"
    assert result.sources[0]["verification_status"] == "candidate"
    assert result.content["matched_requirement_ids"] == []
    session = DaqEvidencePolicy().begin(question_requirements("EGO 单机怎么录制？"))
    session.observe("search_knowledge", result)
    assert session.snapshot().requirement_status == {"question_evidence": "unknown"}
    assert session.evaluate(AnswerSubmission(outcome="resolved", conclusion="按候选步骤录制")).action != "allow"


def test_candidate_input_hash_and_body_tampering_fail(monkeypatch, tmp_path):
    graph_dir, bound, archive, snapshot = _fixture(tmp_path)
    monkeypatch.setattr("daq_fae.knowledge.dev_candidate.verify_archive",
                        lambda *_: {"manifest_sha256": snapshot["archive_manifest_sha256"],
                                    "sources": snapshot["sources"]})
    data = json.loads(bound.read_text())
    data["bodies"]["system:ego:recording"] = "tampered"
    _write(bound, data)
    with pytest.raises(ValueError, match="hash"):
        DevCandidateKnowledge.load(graph_dir, bound, archive)


def test_source_check_reuses_unchanged_records_across_graph_updates(monkeypatch, tmp_path):
    graph_dir, bound, archive, snapshot = _fixture(tmp_path)
    monkeypatch.setattr("daq_fae.knowledge.dev_candidate.verify_archive",
                        lambda *_: {"manifest_sha256": snapshot["archive_manifest_sha256"]})
    bundle = json.loads((graph_dir / "dependency-bundle.json").read_text())
    entity = {"id": "entity:ego", "kind": "entity", "status": "candidate",
              "scope": {"product": "ego"},
              "data": {"name": "EGO", "entity_type": "device"},
              "source_refs": bundle["sections"][0]["source_refs"]}
    bundle["records"] = [entity]
    graph_sha = _write(graph_dir / "dependency-bundle.json", bundle)
    _write(graph_dir / "summary.json", {
        "output_sha256": {"dependency-bundle.json": graph_sha},
        "input_sha256": {"bound_section_index": hashlib.sha256(bound.read_bytes()).hexdigest()}})
    checked = {**entity, "status": "verified"}
    ledger = tmp_path / "source-check.json"
    _write(ledger, {"format_version": "daq-dev-source-check/v1",
                    "graph_sha256": graph_sha, "reviewer": "Codex",
                    "reviewed_at": "2026-10-12",
                    "scope": "loopback_dev_internal_fae_only",
                    "records": {"entity:ego": record_fingerprint(checked)}})
    knowledge = DevCandidateKnowledge.load(graph_dir, bound, archive, ledger)
    assert knowledge.manifest["source_checked_count"] == 1
    assert knowledge.records_for("internal_fae")[0]["id"] == "entity:ego"
    assert knowledge.records_for("channel") == []
    original_release = knowledge.release_id
    bundle["records"].append({**entity, "id": "entity:unrelated",
                              "data": {"name": "Unrelated", "entity_type": "device"}})
    updated_sha = _write(graph_dir / "dependency-bundle.json", bundle)
    _write(graph_dir / "summary.json", {
        "output_sha256": {"dependency-bundle.json": updated_sha},
        "input_sha256": {"bound_section_index": hashlib.sha256(bound.read_bytes()).hexdigest()}})
    updated = DevCandidateKnowledge.load(graph_dir, bound, archive, ledger)
    assert updated.manifest["source_checked_count"] == 1
    assert updated.release_id != original_release
    bundle["records"][0]["data"]["name"] = "Changed EGO"
    changed_sha = _write(graph_dir / "dependency-bundle.json", bundle)
    _write(graph_dir / "summary.json", {
        "output_sha256": {"dependency-bundle.json": changed_sha},
        "input_sha256": {"bound_section_index": hashlib.sha256(bound.read_bytes()).hexdigest()}})
    with pytest.raises(ValueError, match="fingerprint"):
        DevCandidateKnowledge.load(graph_dir, bound, archive, ledger)
    broken = json.loads(ledger.read_text())
    broken["records"]["entity:ego"] = "0" * 64
    _write(ledger, broken)
    with pytest.raises(ValueError, match="fingerprint"):
        DevCandidateKnowledge.load(graph_dir, bound, archive, ledger)


def test_source_checked_natural_variant_question_can_satisfy_dev_ledger():
    ref = {"path": "guide.md", "sha256": "a" * 64,
           "locator": {"kind": "lines", "start": 1, "end": 1}}
    entity = {"id": "entity:ego-1600", "kind": "entity", "status": "verified",
              "scope": {"product": "ego", "required_selectors": ["resolution_variant"],
                        "resolution_variant": "1600x1200"},
              "data": {"name": "EGO", "entity_type": "variant"}, "source_refs": [ref]}
    claim = {"id": "claim:ego-1600-resolution", "kind": "claim", "status": "verified",
             "scope": entity["scope"], "source_refs": [ref],
             "data": {"entity_id": entity["id"], "field": "rgb_resolution",
                      "value": "1600x1200", "unit": "px", "conditions": {}}}
    knowledge = DevCandidateKnowledge("candidate-dev-" + "a" * 64, [], [entity, claim],
                                      {"rgb分辨率": "rgb_resolution"})
    question = "EGO 双目 1600×1200 变体的 RGB 分辨率是多少？"
    plan = prepare_turn(question, grounded_targets=knowledge.ground_question(question))
    toolbox = DaqToolBox(question=question, knowledge=knowledge, role="internal_fae",
                         requirements=plan.requirements)
    session = DaqEvidencePolicy(allow_dev_source_check=True).begin(plan.evidence_requirements())
    identity = toolbox.dispatch("resolve_entity", {"text": "EGO 双目 1600×1200 变体"})
    fact = toolbox.dispatch("lookup_spec", {"entity": "EGO 双目", "field": "RGB 分辨率",
                                            "conditions": "{'variant': '双目 1600×1200'}"})
    assert identity.status == "ok"
    assert fact.status == "ok"
    assert fact.content["query_scope_normalized"] is True
    assert fact.content["matches"][0]["status"] == "source_checked"
    for name, result in [("resolve_entity", identity), ("lookup_spec", fact)]:
        session.observe(name, result)
    assert set(session.snapshot().requirement_status.values()) == {"satisfied"}
    assert session.evaluate(AnswerSubmission(outcome="resolved", conclusion="1600×1200")).action == "allow"


def test_candidate_preview_rejects_authenticated_mode_and_active_pointer(monkeypatch, tmp_path):
    from daq_fae.app import create_app

    monkeypatch.setenv("DAQ_DEV_CANDIDATE_MODE", "true")
    monkeypatch.setenv("DAQ_PLATFORM_IDENTITY_ENABLED", "true")
    monkeypatch.setenv("DAQ_DATABASE_URL", "postgresql://unused/unused")
    monkeypatch.setenv("DAQ_AUTHENTICATED_CONTENT_KEYRING_FILE", str(tmp_path / "unused"))
    with pytest.raises(ValueError, match="requires_local_mode"):
        create_app(provider_mode="offline")

    monkeypatch.setenv("DAQ_PLATFORM_IDENTITY_ENABLED", "false")
    monkeypatch.setenv("DAQ_DEV_CANDIDATE_GRAPH_DIR", "unused")
    monkeypatch.setenv("DAQ_DEV_CANDIDATE_BOUND_INDEX", "unused")
    monkeypatch.setenv("DAQ_DEV_CANDIDATE_ARCHIVE", "unused")
    releases = tmp_path / "releases"
    releases.mkdir()
    (releases / "active.json").write_text("{}")
    with pytest.raises(ValueError, match="cannot_shadow_active_release"):
        create_app(provider_mode="offline", knowledge_release_root=releases)


def test_generic_variant_spec_reports_checked_scopes_without_answering():
    ref = {"path": "guide.md", "sha256": "a" * 64,
           "locator": {"kind": "lines", "start": 1, "end": 1}}
    rows = []
    for variant, value in (("1600x1200", 165), ("1920x1200", 149)):
        entity_id = "entity:ego-" + variant[:4]
        scope = {"product": "ego", "required_selectors": ["resolution_variant"],
                 "resolution_variant": variant}
        rows.extend([
            {"id": entity_id, "kind": "entity", "status": "verified",
             "scope": scope, "data": {"name": "EGO", "entity_type": "variant"},
             "source_refs": [ref]},
            {"id": "claim:ego-" + variant[:4] + "-horizontal-fov", "kind": "claim",
             "status": "verified", "scope": scope, "source_refs": [ref],
             "data": {"entity_id": entity_id, "field": "horizontal_fov",
                      "value": value, "unit": "deg", "conditions": {}}},
        ])
    knowledge = DevCandidateKnowledge("candidate-dev-" + "a" * 64, [], rows,
                                      {"水平视场角": "horizontal_fov"})
    question = "EGO 双目的水平视场角是多少？"
    plan = prepare_turn(question, grounded_targets=knowledge.ground_question(question))
    toolbox = DaqToolBox(question=question, knowledge=knowledge, role="internal_fae",
                         requirements=plan.requirements)
    result = toolbox.dispatch("lookup_spec", {"entity": "EGO 双目", "field": "horizontal_fov"})
    assert result.status == "not_found"
    assert result.content["reason"] == "variant_selector_required"
    assert result.content["source_checked_variant_scopes"] == {
        "resolution_variant": ["1600x1200", "1920x1200"]}
    assert result.content["matches"] == []
    assert result.sources == []


@pytest.mark.parametrize("field", ["mono_stereo_baseline", "mono_baseline",
                                     "stereo_baseline", "baseline"])
def test_source_checked_mono_baseline_accepts_model_field_variants(field):
    ref = {"path": "guide.md", "sha256": "a" * 64,
           "locator": {"kind": "lines", "start": 1, "end": 1}}
    entity = {"id": "entity:umi-finger", "kind": "entity", "status": "verified",
              "scope": {"product": "umi-finger"},
              "data": {"name": "UMI Finger", "entity_type": "device"},
              "source_refs": [ref]}
    claim = {"id": "claim:umi-finger-mono-baseline", "kind": "claim",
             "status": "verified", "scope": entity["scope"], "source_refs": [ref],
             "data": {"entity_id": entity["id"], "field": "stereo_mono_baseline",
                      "value": 50, "unit": "mm", "conditions": {}}}
    aliases = {alias: "stereo_mono_baseline" for alias in
               ("mono_stereo_baseline", "mono_baseline", "stereo_baseline", "baseline")}
    knowledge = DevCandidateKnowledge("candidate-dev-" + "a" * 64, [],
                                      [entity, claim], aliases)
    question = "UMI Finger 的双目 Mono 基线是多少？"
    plan = prepare_turn(question, grounded_targets=knowledge.ground_question(question))
    result = DaqToolBox(question=question, knowledge=knowledge, role="internal_fae",
                        requirements=plan.requirements).dispatch(
                            "lookup_spec", {"entity": "UMI Finger", "field": field})
    assert result.status == "ok"
    assert result.content["matches"][0]["data"]["value"] == 50
    assert result.content["matched_requirement_ids"]


def test_dev_source_check_ledger_declares_mono_baseline_model_aliases():
    ledger = json.loads((Path(__file__).resolve().parents[1] /
                         "review_recipes/dev_source_checked_v1.json").read_text())
    aliases = set(ledger["field_aliases"]["stereo_mono_baseline"])
    assert {"mono_stereo_baseline", "mono_baseline", "stereo_baseline",
            "baseline"} <= aliases
