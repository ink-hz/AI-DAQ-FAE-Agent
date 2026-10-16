"""Release-note statements are source bound and never certify device support."""

import json
import hashlib
from pathlib import Path

import pytest

from daq_fae.knowledge.release_notes import ReleaseNoteCatalog, build_release_notes
from daq_fae.knowledge.code_identity import daq_code_sha
from daq_fae.knowledge.sdk_catalog import SdkCatalog
from daq_fae.knowledge.sdk_release import (activate_sdk_release,
                                           load_active_sdk_release, stage_sdk_release)
from daq_fae.domain_tools import DaqToolBox
from daq_fae.domain_evidence import DaqEvidencePolicy


ARCHIVE = Path("data/knowledge/local_archive/sdk-originals-1015-local-20261015")
DIRECTORY = Path("Knowledge_SDK")


def test_all_22_change_logs_deduplicate_to_17_source_bound_notes():
    if not ARCHIVE.is_dir():
        pytest.skip("local ignored SDK originals unavailable")
    records, manifest = build_release_notes(DIRECTORY / "release_notes_curated.json", ARCHIVE)
    assert manifest["original_log_count"] == 22
    assert manifest["duplicate_log_copies"] == 5
    assert manifest["note_count"] == len(records) == 17
    assert len({row["file_sha256"] for row in records}) == 17
    assert all(row["view_roles"] == ["internal_fae"] and
               row["device_support"] == "unknown" and
               row["evidence_scope"] == "release_note_statement_only" and
               row["source_refs"] for row in records)
    assert sum(len(row["duplicate_archive_paths"]) for row in records) == 5
    disabled = next(row for row in records if row["kb_id"] ==
                    "daq-note-viewer-android-2026092302")
    source = ARCHIVE / "files" / disabled["source_refs"][0]["archive_path"]
    ref = disabled["source_refs"][0]
    cited_lines = source.read_text().splitlines()[ref["start_line"] - 1:ref["end_line"]]
    assert any("临时屏蔽网络设备枚举" in line for line in cited_lines)
    by_id = {row["kb_id"]: row for row in records}
    assert "2026-09-23" not in by_id["daq-note-sdk-20260921"]["answer"]
    assert "2026-09-23" not in by_id["daq-note-sdk-20260929"]["answer"]
    assert "2026092302" not in by_id["daq-note-viewer-android-2026092901"]["answer"]
    assert "v1.1.9 二进制" not in by_id["daq-note-firmware-dual-1110"]["answer"]


def test_android_viewer_two_builds_retrieve_distinct_note_statements():
    if not ARCHIVE.is_dir():
        pytest.skip("local ignored SDK originals unavailable")
    catalog = ReleaseNoteCatalog.load(DIRECTORY, ARCHIVE)
    rows = catalog.search(
        "EgoViewer Android v1.0.11 2026-09-23 与 2026-09-29 网络设备枚举有什么变更",
        "internal_fae")
    assert {row["kb_id"] for row in rows} == {
        "daq-note-viewer-android-2026092302", "daq-note-viewer-android-2026092901"}
    assert any("临时屏蔽网络设备枚举" in row["answer"] for row in rows)
    assert all(row["device_support"] == "unknown" for row in rows)
    compact_range = catalog.search(
        "EgoViewer Android v1.0.11 20260923-20260929 网络设备枚举变更日志",
        "internal_fae")
    assert {row["kb_id"] for row in compact_range} == {
        "daq-note-viewer-android-2026092302", "daq-note-viewer-android-2026092901"}
    assert catalog.search("EgoViewer 2026-09-23", "external") == []


def test_explicit_release_note_version_rejects_other_versions():
    if not ARCHIVE.is_dir():
        pytest.skip("local ignored SDK originals unavailable")
    catalog = ReleaseNoteCatalog.load(DIRECTORY, ARCHIVE)
    for question in (
        "EgoViewer Android v1.0.12 变更日志",
        "EgoViewer Android v1.0.1 变更日志",
        "EgoViewer Android v9.9.9 变更日志",
        "EgoViewer desktop v2.0.16 变更日志",
        "OrbbecSDK 2.9.1 变更日志",
        "双目 EGO 固件 v9.9.9 变更日志",
    ):
        assert catalog.search(question, "internal_fae") == []


def test_release_note_versions_keep_family_platform_and_build_scope():
    if not ARCHIVE.is_dir():
        pytest.skip("local ignored SDK originals unavailable")
    catalog = ReleaseNoteCatalog.load(DIRECTORY, ARCHIVE)
    for question, expected in (
        ("EgoViewer Android v1.0.10 20260910 变更日志",
         {"daq-note-viewer-android-20260910"}),
        ("EgoViewer Android v1.0.11 2026092302 变更日志",
         {"daq-note-viewer-android-2026092302"}),
        ("EgoViewer desktop v2.0.15 变更日志",
         {"daq-note-viewer-desktop-20260929"}),
        ("OrbbecSDK 2.9.0 20260923 变更日志",
         {"daq-note-sdk-20260923"}),
        ("双目 EGO 固件 v1.1.9 变更日志",
         {"daq-note-firmware-dual-1110"}),
    ):
        assert {row["kb_id"] for row in catalog.search(question, "internal_fae")} == expected
    assert catalog.search(
        "OrbbecSDK 2.9.0 2026092302 变更日志", "internal_fae") == []
    assert catalog.search(
        "EgoViewer Android v1.0.11 202609230201 变更日志", "internal_fae") == []


def test_mixed_family_versions_do_not_erase_reviewed_firmware_history():
    if not ARCHIVE.is_dir():
        pytest.skip("local ignored SDK originals unavailable")
    catalog = ReleaseNoteCatalog.load(DIRECTORY, ARCHIVE)
    rows = catalog.search(
        "双目 EGO 固件 v1.1.9 与 OrbbecSDK 2.9.0 的变更日志能证明配对吗？",
        "internal_fae")
    assert "daq-note-firmware-dual-1110" in {row["kb_id"] for row in rows}
    assert any(row["family"] == "OrbbecSDK" for row in rows)
    assert all(row["device_support"] == "unknown" for row in rows)
    mixed_unknown = catalog.search(
        "EgoViewer Android v1.0.12 与 OrbbecSDK 2.9.0 变更日志",
        "internal_fae")
    assert mixed_unknown
    assert {row["family"] for row in mixed_unknown} == {"OrbbecSDK"}
    assert catalog.search(
        "Android OrbbecSDK 2.9.0 变更日志", "internal_fae") == []
    mixed_known = catalog.search(
        "EgoViewer Android v1.0.11 与 OrbbecSDK 2.9.0 变更日志",
        "internal_fae")
    assert {row["family"] for row in mixed_known} == {
        "EgoViewer Android", "OrbbecSDK"}
    two_viewer_platforms = catalog.search(
        "EgoViewer Android v1.0.11 与 desktop v2.0.15 变更日志",
        "internal_fae")
    assert {row["family"] for row in two_viewer_platforms} == {
        "EgoViewer Android", "EgoViewer desktop"}


def test_mixed_family_versions_and_builds_cannot_borrow_other_clause_selectors():
    if not ARCHIVE.is_dir():
        pytest.skip("local ignored SDK originals unavailable")
    catalog = ReleaseNoteCatalog.load(DIRECTORY, ARCHIVE)
    assert catalog.search(
        "EgoViewer Android v1.0.12 与 OrbbecSDK v1.0.11 变更日志",
        "internal_fae") == []
    rows = catalog.search(
        "EgoViewer Android v1.0.11 20260923 与 OrbbecSDK 2.9.0 20260929 变更日志",
        "internal_fae")
    assert {row["kb_id"] for row in rows} == {
        "daq-note-viewer-android-2026092302", "daq-note-sdk-20260929"}


def test_magnetometer_and_firmware_history_keep_builds_separate():
    if not ARCHIVE.is_dir():
        pytest.skip("local ignored SDK originals unavailable")
    catalog = ReleaseNoteCatalog.load(DIRECTORY, ARCHIVE)
    magnetometer = catalog.search(
        "OrbbecSDK 2026-09-21 与 2026-09-23 UMI Finger 磁力计变更日志", "internal_fae")
    assert {row["kb_id"] for row in magnetometer} == {
        "daq-note-sdk-20260921", "daq-note-sdk-20260923"}
    firmware = catalog.search(
        "双目 EGO 固件变更日志 v1.1.9 与 v1.1.10 主从切换和时间戳", "internal_fae")
    assert firmware[0]["kb_id"] == "daq-note-firmware-dual-1110"
    assert all(row["device_support"] == "unknown" for row in firmware)


def test_mixed_firmware_history_and_sdk_pairing_question_keeps_firmware_note():
    if not ARCHIVE.is_dir():
        pytest.skip("local ignored SDK originals unavailable")
    catalog = ReleaseNoteCatalog.load(DIRECTORY, ARCHIVE)
    question = ("双目 EGO 固件变更日志在 v1.1.9 与 v1.1.10 分别记载了哪些主从/同步修改？"
                "这些历史说明能证明我的固件与 OrbbecSDK 2.9.0 已配对实测吗？")
    rows = catalog.search(question, "internal_fae")
    assert len(rows) <= 5
    assert "daq-note-firmware-dual-1110" in {row["kb_id"] for row in rows}
    assert all(row["device_support"] == "unknown" for row in rows)
    dated = catalog.search(
        "双目 EGO 固件 v1.1.9 与 OrbbecSDK 2.9.0 20260929 的变更日志能证明配对吗？",
        "internal_fae")
    assert {row["kb_id"] for row in dated} >= {
        "daq-note-firmware-dual-1110", "daq-note-sdk-20260929"}


def test_change_log_hash_or_line_tamper_blocks_catalog_build(tmp_path):
    if not ARCHIVE.is_dir():
        pytest.skip("local ignored SDK originals unavailable")
    specs = json.loads((DIRECTORY / "release_notes_curated.json").read_text())
    specs[0]["file_sha256"] = "0" * 64
    curated = tmp_path / "notes.json"
    curated.write_text(json.dumps(specs, ensure_ascii=False))
    with pytest.raises(ValueError, match="hash"):
        build_release_notes(curated, ARCHIVE)
    specs[0]["file_sha256"] = json.loads(
        (DIRECTORY / "release_notes_curated.json").read_text())[0]["file_sha256"]
    specs[0]["source_refs"] = [{"start_line": 9999, "end_line": 9999}]
    curated.write_text(json.dumps(specs, ensure_ascii=False))
    with pytest.raises(ValueError, match="line"):
        build_release_notes(curated, ARCHIVE)


def test_dev_sdk_catalog_keeps_release_notes_separate_from_api_usage():
    if not ARCHIVE.is_dir():
        pytest.skip("local ignored SDK originals unavailable")
    catalog = SdkCatalog.load(DIRECTORY, ARCHIVE)
    assert len(catalog.records) == 22
    assert catalog.release_notes.manifest["note_count"] == 17
    assert catalog.search("EgoViewer Android 2026-09-23 网络设备枚举", "internal_fae") == []
    assert {row["kb_id"] for row in catalog.release_notes.search(
        "EgoViewer Android 2026-09-23 网络设备枚举", "internal_fae")} == {
            "daq-note-viewer-android-2026092302"}


def test_sdk_stage_binds_release_note_sha_and_dev_review_identity(tmp_path):
    if not ARCHIVE.is_dir():
        pytest.skip("local ignored SDK originals unavailable")
    catalog = SdkCatalog.load(DIRECTORY, ARCHIVE)
    notes_sha = catalog.release_notes.manifest["records_sha256"]
    current_code_sha = daq_code_sha()
    answer = "变更日志记载 2026092302 临时屏蔽网络枚举；2026092901 未声明恢复。"
    replay = tmp_path / "results.jsonl"
    replay.write_text(json.dumps({"case": {"id": "viewer-note"},
        "identity": {"sdk_catalog_sha256": catalog.manifest["records_sha256"],
                     "release_notes_sha256": notes_sha,
                     "daq_code_sha256": current_code_sha, "knowledge_release": "f" * 64,
                     "suite_file_sha256": "c" * 64,
                     "runtime_release": "daq-tested", "upstream_revision": "b" * 40},
        "turns": [{"done": {"answer": answer, "outcome": "safe_abstained",
                            "trace_id": "d" * 32, "fallback_used": False,
                            "sources": [{"source_id": "daq-note-viewer-android-2026092302"}]},
                    "integrity": {"trace_id_equal": True}}]}) + "\n")
    report = {"environment": "dev", "reviewer": "Codex",
              "catalog_sha256": catalog.manifest["records_sha256"],
              "release_notes_sha256": notes_sha,
              "daq_code_sha256": current_code_sha, "knowledge_release": "f" * 64,
              "results_sha256": hashlib.sha256(replay.read_bytes()).hexdigest(),
              "suite_sha256": "c" * 64,
              "cases": [{"id": "viewer-note", "passed": True,
                         "answer_sha256": hashlib.sha256(answer.encode()).hexdigest(),
                         "trace_id": "d" * 32, "outcome": "safe_abstained",
                         "fallback_used": False,
                         "review_note": "Both build statements checked; status unknown."}]}
    rows = [*catalog.records, *catalog.release_notes.records]
    report["record_reviews"] = [{
        "kb_id": row["kb_id"],
        "record_sha256": hashlib.sha256((json.dumps(
            row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()).hexdigest(),
        "reviewer": "Codex", "passed": True, "source_span_checked": True,
        "review_note": "Exact source span and statement independently checked.",
    } for row in rows]
    missing_note_sha = {**report, "release_notes_sha256": None}
    with pytest.raises(ValueError, match="review"):
        stage_sdk_release(tmp_path / "published", DIRECTORY, ARCHIVE, missing_note_sha,
                          replay_results=replay, runtime_release="daq-tested",
                          upstream_sha="b" * 40)
    missing_record_review = {**report, "record_reviews": report["record_reviews"][:-1]}
    with pytest.raises(ValueError, match="record review"):
        stage_sdk_release(tmp_path / "published", DIRECTORY, ARCHIVE, missing_record_review,
                          replay_results=replay, runtime_release="daq-tested",
                          upstream_sha="b" * 40)
    wrong_code_identity = {**report, "daq_code_sha256": "0" * 64}
    with pytest.raises(ValueError, match="identity"):
        stage_sdk_release(tmp_path / "published", DIRECTORY, ARCHIVE, wrong_code_identity,
                          replay_results=replay, runtime_release="daq-tested",
                          upstream_sha="b" * 40)
    release_id = stage_sdk_release(tmp_path / "published", DIRECTORY, ARCHIVE, report,
                                   replay_results=replay, runtime_release="daq-tested",
                                   upstream_sha="b" * 40)
    manifest = json.loads((tmp_path / "published" / "releases" / release_id / "manifest.json").read_text())
    assert manifest["release_notes_sha256"] == notes_sha
    assert manifest["daq_code_sha256"] == current_code_sha
    assert manifest["knowledge_release"] == "f" * 64
    assert len(manifest["release_notes"]) == 17
    activate_sdk_release(tmp_path / "published", release_id)
    assert load_active_sdk_release(tmp_path / "published", runtime_release="daq-tested",
                                   upstream_sha="b" * 40, daq_code_sha256=current_code_sha,
                                   knowledge_release="f" * 64).release_id == release_id
    for code_sha, main_release in (("0" * 64, "f" * 64),
                                   (current_code_sha, "0" * 64),
                                   (None, "f" * 64)):
        with pytest.raises(ValueError, match="identity"):
            load_active_sdk_release(tmp_path / "published", runtime_release="daq-tested",
                                    upstream_sha="b" * 40, daq_code_sha256=code_sha,
                                    knowledge_release=main_release)


def test_dev_runner_and_sdk_loader_share_exact_daq_code_digest():
    from scripts.run_daq_workflow_dev_replay import _daq_code_sha
    assert _daq_code_sha() == daq_code_sha()


def test_sdk_current_cli_passes_loaded_main_release_identity(monkeypatch, tmp_path, capsys):
    import sys
    from scripts import publish_daq_sdk_release as command

    seen = {}
    def fake_load(root, **identity):
        seen.update(identity)
        return None

    monkeypatch.setattr(command, "load_active_sdk_release", fake_load)
    monkeypatch.setattr(sys, "argv", [
        "publish_daq_sdk_release", "current", "--root", str(tmp_path),
        "--runtime-release", "daq-tested", "--upstream-sha", "b" * 40,
        "--knowledge-release", "f" * 64])
    command.main()
    assert seen["daq_code_sha256"] == daq_code_sha()
    assert seen["knowledge_release"] == "f" * 64
    assert json.loads(capsys.readouterr().out)["record_count"] == 0


def test_search_knowledge_exposes_bounded_notes_without_sdk_or_device_certification():
    if not ARCHIVE.is_dir():
        pytest.skip("local ignored SDK originals unavailable")
    catalog = SdkCatalog.load(DIRECTORY, ARCHIVE)

    class Knowledge:
        release_id = "dev-main"
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []

    question = "Android EgoViewer v1.0.11 2026-09-23 与 2026-09-29 网络设备枚举行为能当作相同吗？"
    requirements = [{"id": "release", "capability": "search_knowledge", "entities": [],
                     "conditions": {"platform": "android"}},
                    {"id": "usage", "capability": "sdk_evidence", "entities": [],
                     "conditions": {"platform": "android"}},
                    {"id": "device", "capability": "check_software_support", "entities": [],
                     "conditions": {"platform": "android"}}]
    toolbox = DaqToolBox(question=question, knowledge=Knowledge(), role="internal_fae",
                         requirements=requirements)
    result = toolbox.dispatch("search_knowledge", {"query": question})
    assert result.status == "ok"
    assert result.content["evidence_scope"] == "release_note_statement_only"
    assert result.content["device_support"] == "unknown"
    assert result.content["matched_requirement_ids"] == []
    assert {source["source_id"] for source in result.sources} == {
        "daq-note-viewer-android-2026092302", "daq-note-viewer-android-2026092901"}
    assert all(source["source_refs"][0]["package_sha256"] for source in result.sources)
    delivered = json.dumps(result.content, ensure_ascii=False)
    assert all(ref["archive_path"] not in delivered for source in result.sources
               for ref in source["source_refs"])
    policy = DaqEvidencePolicy(allow_dev_source_check=True).begin({"requirements": requirements})
    policy.observe("search_knowledge", result)
    assert policy.status == {"release": "unknown", "usage": "unknown", "device": "unknown"}
    for question, expected in (
        ("OrbbecSDK 2026-09-21 与 2026-09-23 UMI Finger 磁力计变更日志",
         {"daq-note-sdk-20260921", "daq-note-sdk-20260923"}),
        ("双目 EGO 固件变更日志 v1.1.9 与 v1.1.10 主从切换和时间戳",
         {"daq-note-firmware-dual-1110"}),
    ):
        result = toolbox.with_request_context(question).dispatch(
            "search_knowledge", {"query": question})
        assert result.status == "ok"
        assert expected <= {source["source_id"] for source in result.sources}
        assert result.content["matched_requirement_ids"] == []
        assert all("archive_path" not in match for match in result.content["matches"])
    unavailable = toolbox.with_request_context(
        "EgoViewer Android v1.0.12 变更日志").dispatch(
            "search_knowledge", {"query": "EgoViewer Android v1.0.12 变更日志"})
    assert unavailable.status == "not_found"
    assert unavailable.content["matches"] == []
    assert unavailable.sources == []
