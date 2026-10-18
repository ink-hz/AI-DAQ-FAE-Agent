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


def test_release_note_answers_do_not_embed_reviewer_inferences():
    """A source statement must not make reviewer analysis look like log text."""
    specs = json.loads((DIRECTORY / "release_notes_curated.json").read_text())
    answers = {row["kb_id"]: row["answer"] for row in specs}
    assert "不能把包内旧示例当作恢复证据" not in answers["daq-note-sdk-20260929"]
    assert "第 8 行" not in answers["daq-note-viewer-android-2026092302"]
    assert "不能直接证明后续构建是否恢复" not in answers["daq-note-viewer-android-2026092302"]
    assert "不能据此判断该构建的网络枚举实际状态" not in answers["daq-note-viewer-android-2026092901"]


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
    assert len(catalog.records) == 23
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
    assert result.content["matched_requirement_ids"] == ["release"]
    assert {source["source_id"] for source in result.sources} == {
        "daq-note-viewer-android-2026092302", "daq-note-viewer-android-2026092901"}
    assert all(source["source_refs"][0]["package_sha256"] for source in result.sources)
    delivered = json.dumps(result.content, ensure_ascii=False)
    assert all(ref["archive_path"] not in delivered for source in result.sources
               for ref in source["source_refs"])
    policy = DaqEvidencePolicy(allow_dev_source_check=True).begin({"requirements": requirements})
    policy.observe("search_knowledge", result)
    assert policy.status == {"release": "satisfied", "usage": "unknown", "device": "unknown"}
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


def test_viewer_connection_question_does_not_borrow_release_note_evidence():
    if not ARCHIVE.is_dir():
        pytest.skip('local ignored SDK originals unavailable')
    catalog = SdkCatalog.load(DIRECTORY, ARCHIVE)
    class Knowledge:
        release_id = 'dev-main'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []
    requirements = [{'id': 'connect', 'capability': 'search_knowledge',
                     'entities': [], 'conditions': {}}]
    result = DaqToolBox(question='EgoViewer Android 怎么连接 EGO？',
                        knowledge=Knowledge(), role='internal_fae',
                        requirements=requirements).dispatch(
                            'search_knowledge', {'query': 'EgoViewer Android 怎么连接 EGO'})
    assert result.status == 'not_found'
    assert not result.sources or all(
        source['type'] not in {'daq_release_note_source_checked',
                               'daq_governed_release_note'}
        for source in result.sources)


def test_exact_viewer_build_certifies_only_its_own_note():
    if not ARCHIVE.is_dir():
        pytest.skip('local ignored SDK originals unavailable')
    catalog = SdkCatalog.load(DIRECTORY, ARCHIVE)
    class Knowledge:
        release_id = 'dev-main'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []
    result = DaqToolBox(
        question='EgoViewer Android 2026092302 的变更日志写了什么？',
        knowledge=Knowledge(), role='internal_fae', requirements=[
            {'id': 'exact', 'capability': 'search_knowledge', 'entities': [],
             'conditions': {'viewer_release_date': '2026092302'}},
            {'id': 'other', 'capability': 'search_knowledge', 'entities': [],
             'conditions': {'viewer_release_date': '2026092901'}}]).dispatch(
                 'search_knowledge', {'query': 'EgoViewer Android 2026092302 变更日志'})
    assert result.status == 'ok'
    assert result.content['matched_requirement_ids'] == ['exact']


def test_dated_sdk_usage_question_does_not_borrow_firmware_notes():
    if not ARCHIVE.is_dir():
        pytest.skip('local ignored SDK originals unavailable')
    catalog = SdkCatalog.load(DIRECTORY, ARCHIVE)
    class Knowledge:
        release_id = 'dev-main'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []
    question = ('用 2026-09-29 Linux x86_64 OrbbecSDK 2.9.0 C++ 示例，'
                '怎样核对网络设备移除回调中的 UID？不要推断我的设备和固件已经兼容。')
    box = DaqToolBox(question=question, knowledge=Knowledge(), role='internal_fae')
    for query in ('OrbbecSDK 2.9.0 网络设备移除回调 UID C++ 示例',
                  'OrbbecSDK 2026-09-29 网络设备移除回调变更日志'):
        result = box.dispatch('search_knowledge', {'query': query})
        assert result.status == 'not_found'
        assert not result.sources


def test_firmware_notes_stay_on_named_dual_or_pro_product():
    if not ARCHIVE.is_dir():
        pytest.skip('local ignored SDK originals unavailable')
    notes = ReleaseNoteCatalog.load(DIRECTORY, ARCHIVE)
    dual = next(row for row in notes.records if row['kb_id'] == 'daq-note-firmware-dual-1110')
    pro = next(row for row in notes.records if row['kb_id'] == 'daq-note-firmware-pro-107')
    assert [row['kb_id'] for row in notes.search(
        '双目 EGO 固件变更日志', 'internal_fae')] == [dual['kb_id']]
    assert [row['kb_id'] for row in notes.search(
        '四目 EGO Pro 固件变更日志', 'internal_fae')] == [pro['kb_id']]
    assert notes.search('EGO 固件变更日志', 'internal_fae') == []
    requirement = [{'id': 'dual', 'capability': 'search_knowledge',
                    'entities': ['EGO'], 'conditions': {}}]
    box = DaqToolBox(question='双目 EGO 固件变更日志', role='internal_fae',
                     requirements=requirement)
    assert box._release_note_requirement_ids([dual]) == ['dual']
    assert box._release_note_requirement_ids([pro]) == []


def test_generic_ego_version_does_not_select_a_firmware_product():
    if not ARCHIVE.is_dir():
        pytest.skip('local ignored SDK originals unavailable')
    notes = ReleaseNoteCatalog.load(DIRECTORY, ARCHIVE)
    pro = next(row for row in notes.records if row['kb_id'] == 'daq-note-firmware-pro-107')
    for version in ('v1.0.7', 'v1.1.10'):
        question = f'EGO 固件 {version} 变更日志写了什么？'
        assert notes.search(question, 'internal_fae') == []
        requirement = [{'id': 'generic', 'capability': 'search_knowledge',
                        'entities': ['EGO'],
                        'conditions': {'firmware_version': version}}]
        box = DaqToolBox(question=question, role='internal_fae',
                         requirements=requirement)
        assert box._release_note_requirement_ids([pro]) == []


def test_generic_firmware_note_search_reports_identity_gap_instead_of_absence():
    if not ARCHIVE.is_dir():
        pytest.skip('local ignored SDK originals unavailable')
    catalog = SdkCatalog.load(DIRECTORY, ARCHIVE)
    class Knowledge:
        release_id = 'dev-main'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []
    question = 'EGO 固件 v1.0.7 的变更日志写了什么？我没说明是双目还是 Pro。'
    box = DaqToolBox(question=question, knowledge=Knowledge(), role='internal_fae',
                     requirements=[{'id': 'generic', 'capability': 'search_knowledge',
                                    'entities': ['EGO'],
                                    'conditions': {'firmware_version': 'v1.0.7'}}])
    result = box.dispatch('search_knowledge',
                          {'query': 'EGO Pro firmware 1.0.7 release notes'})
    assert result.status == 'not_found'
    assert result.content['reason'] == 'firmware_product_identity_required'
    assert result.content['availability_checked'] is False
    assert result.sources == []
    # Even if schema extraction guesses Pro from the user's disjunction,
    # that guess cannot overrule the user's explicit uncertainty.
    guessed = DaqToolBox(question=question, knowledge=Knowledge(),
                         role='internal_fae', requirements=[
                             {'id': 'guessed', 'capability': 'search_knowledge',
                              'entities': ['EGO Pro'],
                              'conditions': {'firmware_version': 'v1.0.7'}}]).dispatch(
                                  'search_knowledge',
                                  {'query': 'EGO Pro firmware 1.0.7 release notes'})
    assert guessed.status == 'not_found'
    assert guessed.content['reason'] == 'firmware_product_identity_required'
    alternative = DaqToolBox(
        question='EGO 固件 v1.1.10 是双目还是 Pro 的变更日志？',
        knowledge=Knowledge(), role='internal_fae', requirements=[
            {'id': 'alternative', 'capability': 'search_knowledge',
             'entities': ['EGO'],
             'conditions': {'firmware_version': 'v1.1.10'}}]).dispatch(
                 'search_knowledge', {'query': '双目 EGO 固件 v1.1.10 变更日志'})
    assert alternative.status == 'not_found'
    assert alternative.content['reason'] == 'firmware_product_identity_required'


def test_dual_and_pro_firmware_comparison_keeps_each_product_version():
    if not ARCHIVE.is_dir():
        pytest.skip('local ignored SDK originals unavailable')
    notes = ReleaseNoteCatalog.load(DIRECTORY, ARCHIVE)
    question = ('双目 EGO 固件 v1.1.10 与 四目 EGO Pro 固件 v1.0.7 '
                '的变更日志各记载了什么？')
    rows = notes.search(question, 'internal_fae')
    assert {row['kb_id'] for row in rows} == {
        'daq-note-firmware-dual-1110', 'daq-note-firmware-pro-107'}
    requirements = [
        {'id': 'dual', 'capability': 'search_knowledge', 'entities': ['EGO'],
         'conditions': {'firmware_version': 'v1.1.10'}},
        {'id': 'pro', 'capability': 'search_knowledge', 'entities': ['EGO'],
         'conditions': {'firmware_version': 'v1.0.7'}},
    ]
    box = DaqToolBox(question=question, role='internal_fae', requirements=requirements)
    dual = next(row for row in rows if 'dual' in row['family'].casefold())
    pro = next(row for row in rows if 'pro' in row['family'].casefold())
    assert box._release_note_requirement_ids([dual]) == ['dual']
    assert box._release_note_requirement_ids([pro]) == ['pro']


def test_pro_firmware_note_in_prior_sentence_is_not_erased_by_dual_upgrade_question():
    if not ARCHIVE.is_dir():
        pytest.skip('local ignored SDK originals unavailable')
    catalog = SdkCatalog.load(DIRECTORY, ARCHIVE)
    question = ('我只知道设备叫 EGO，看到 EGO Pro 固件 v1.0.7 变更日志。'
                '可以据此给我的 EGO 双目升级建议吗？')
    assert {row['kb_id'] for row in catalog.release_notes.search(
        question, 'internal_fae')} == {'daq-note-firmware-pro-107'}

    class Knowledge:
        release_id = 'dev-main'
        sdk_catalog = catalog

        def records_for(self, role):
            return []

        def sections_for(self, role):
            return []

    result = DaqToolBox(
        question=question, knowledge=Knowledge(), role='internal_fae',
        requirements=[{'id': 'pro_note', 'capability': 'search_knowledge',
                       'entities': ['EGO Pro'],
                       'conditions': {'firmware_version': 'v1.0.7'}}],
    ).dispatch('search_knowledge', {
        'query': 'EGO Pro 固件 v1.0.7 变更日志',
    })
    assert result.status == 'ok'
    assert [source['source_id'] for source in result.sources] == [
        'daq-note-firmware-pro-107']
    assert result.content['matched_requirement_ids'] == ['pro_note']


def test_viewer_version_goal_cannot_be_covered_by_sdk_note():
    if not ARCHIVE.is_dir():
        pytest.skip('local ignored SDK originals unavailable')
    notes = ReleaseNoteCatalog.load(DIRECTORY, ARCHIVE)
    sdk = next(row for row in notes.records if row['kb_id'] == 'daq-note-sdk-20260929')
    requirements = [
        {'id': 'viewer', 'capability': 'search_knowledge', 'entities': [],
         'conditions': {'viewer_version': 'v2.9.0'}},
        {'id': 'sdk', 'capability': 'search_knowledge', 'entities': [],
         'conditions': {'sdk_version': '2.9.0'}},
    ]
    box = DaqToolBox(question='EgoViewer v2.9.0 与 OrbbecSDK 2.9.0 的变更日志',
                     role='internal_fae', requirements=requirements)
    assert box._release_note_requirement_ids([sdk]) == ['sdk']


def test_code_identity_changes_when_a_runtime_prompt_changes(tmp_path):
    (tmp_path / 'daq_fae').mkdir()
    (tmp_path / 'daq_fae' / '__init__.py').write_text('')
    (tmp_path / 'prompts').mkdir()
    for name in ('reviewed_knowledge_system.md',
                 'candidate_knowledge_system.md', 'daq_schema_extraction.md'):
        (tmp_path / 'prompts' / name).write_text('initial')
    before = daq_code_sha(tmp_path)
    (tmp_path / 'prompts' / 'daq_schema_extraction.md').write_text('changed')
    assert daq_code_sha(tmp_path) != before


def test_firmware_history_does_not_attach_unasked_sdk_change_logs():
    if not ARCHIVE.is_dir():
        pytest.skip('local ignored SDK originals unavailable')
    catalog = SdkCatalog.load(DIRECTORY, ARCHIVE)
    class Knowledge:
        release_id = 'dev-main'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []
    question = ('双目 EGO 固件变更日志在 v1.1.9 与 v1.1.10 分别记载了哪些'
                '主从/同步修改？这些历史说明能证明与 OrbbecSDK 2.9.0 已配对实测吗？')
    requirements = [
        {'id': 'log19', 'capability': 'search_knowledge',
         'entities': ['双目 EGO'],
         'conditions': {'firmware_version': 'v1.1.9',
                        'feature': 'master_slave_sync'}},
        {'id': 'log10', 'capability': 'search_knowledge',
         'entities': ['双目 EGO'],
         'conditions': {'firmware_version': 'v1.1.10',
                        'feature': 'master_slave_sync'}},
        {'id': 'support', 'capability': 'check_software_support',
         'entities': ['双目 EGO'],
         'conditions': {'sdk_version': '2.9.0'}},
    ]
    result = DaqToolBox(question=question, knowledge=Knowledge(),
                        role='internal_fae', requirements=requirements).dispatch(
                            'search_knowledge',
                            {'query': '双目 EGO 固件变更日志 v1.1.9 主从 同步'})
    assert result.status == 'ok'
    assert {source['source_id'] for source in result.sources} == {
        'daq-note-firmware-dual-1110'}
    assert result.content['matched_requirement_ids'] == ['log10', 'log19']


def test_typed_release_note_goals_require_matching_family_and_sdk_version():
    if not ARCHIVE.is_dir():
        pytest.skip('local ignored SDK originals unavailable')
    notes = ReleaseNoteCatalog.load(DIRECTORY, ARCHIVE)
    viewer = next(row for row in notes.records if row['kb_id'] ==
                  'daq-note-viewer-android-2026092302')
    sdk = next(row for row in notes.records if row['kb_id'] ==
               'daq-note-sdk-20260929')
    requirements = [
        {'id': 'sdk_build', 'capability': 'search_knowledge', 'entities': [],
         'conditions': {'sdk_release_date': '2026092302'}},
        {'id': 'viewer_build', 'capability': 'search_knowledge', 'entities': [],
         'conditions': {'viewer_release_date': '2026092302'}},
        {'id': 'sdk_version', 'capability': 'search_knowledge', 'entities': [],
         'conditions': {'sdk': 'OrbbecSDK', 'sdk_version': '2.9.0',
                        'sdk_release_date': '20260929'}},
        {'id': 'wrong_sdk_version', 'capability': 'search_knowledge', 'entities': [],
         'conditions': {'sdk': 'OrbbecSDK', 'sdk_version': '2.8.9',
                        'sdk_release_date': '20260929'}},
    ]
    box = DaqToolBox(question='SDK 与 Viewer 的变更日志', role='internal_fae',
                     requirements=requirements)
    assert box._release_note_requirement_ids([viewer]) == ['viewer_build']
    assert box._release_note_requirement_ids([sdk]) == ['sdk_version']


def test_dated_magnetometer_log_goals_are_satisfied_only_by_their_own_builds():
    if not ARCHIVE.is_dir():
        pytest.skip('local ignored SDK originals unavailable')
    catalog = SdkCatalog.load(DIRECTORY, ARCHIVE)
    class Knowledge:
        release_id = 'dev-main'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []
    question = ('OrbbecSDK 2026-09-21 与 2026-09-23 两份变更日志分别如何记载 '
                'UMI Finger 磁力计？')
    requirements = [
        {'id': 'log21', 'capability': 'search_knowledge', 'entities': ['UMI Finger'],
         'conditions': {'feature': 'magnetometer', 'sdk': 'OrbbecSDK',
                        'sdk_release_date': '2026-09-21'}},
        {'id': 'log23', 'capability': 'search_knowledge', 'entities': ['UMI Finger'],
         'conditions': {'feature': 'magnetometer', 'sdk': 'OrbbecSDK',
                        'sdk_release_date': '2026-09-23'}},
        {'id': 'log17', 'capability': 'search_knowledge', 'entities': ['UMI Finger'],
         'conditions': {'feature': 'magnetometer', 'sdk': 'OrbbecSDK',
                        'sdk_release_date': '2026-09-17'}},
        {'id': 'support', 'capability': 'check_software_support',
         'entities': ['UMI Finger'], 'conditions': {'feature': 'magnetometer'}},
    ]
    result = DaqToolBox(question=question, knowledge=Knowledge(),
                        role='internal_fae', requirements=requirements).dispatch(
                            'search_knowledge', {'query': question})
    assert result.status == 'ok'
    assert result.content['matched_requirement_ids'] == ['log21', 'log23']
    policy = DaqEvidencePolicy(allow_dev_source_check=True).begin(
        {'requirements': requirements})
    policy.observe('search_knowledge', result)
    assert policy.status == {'log21': 'satisfied', 'log23': 'satisfied',
                             'log17': 'unknown', 'support': 'unknown'}


def test_explicit_log_search_uses_original_question_scope_even_without_log_word():
    if not ARCHIVE.is_dir():
        pytest.skip('local ignored SDK originals unavailable')
    catalog = SdkCatalog.load(DIRECTORY, ARCHIVE)
    class Knowledge:
        release_id = 'dev-main'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []
    question = ('2026-09-29 SDK 包里还带有 magnetometer.cpp，'
                '就能确定 UMI Finger 磁力计仍可用吗？')
    result = DaqToolBox(question=question, knowledge=Knowledge(),
                        role='internal_fae').dispatch(
                            'search_knowledge',
                            {'query': '2026-09-29 SDK UMI Finger 磁力计变更日志'})
    assert result.status == 'ok'
    assert 'daq-note-sdk-20260929' in {
        source['source_id'] for source in result.sources}
    assert result.content['evidence_scope'] == 'release_note_statement_only'


def test_dated_sample_availability_question_returns_related_change_history():
    if not ARCHIVE.is_dir():
        pytest.skip('local ignored SDK originals unavailable')
    catalog = SdkCatalog.load(DIRECTORY, ARCHIVE)
    class Knowledge:
        release_id = 'dev-main'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []
    question = ('2026-09-29 SDK 包里还带有 magnetometer.cpp，'
                '就能确定 UMI Finger 磁力计仍可用吗？')
    result = DaqToolBox(question=question, knowledge=Knowledge(),
                        role='internal_fae').dispatch(
                            'search_knowledge',
                            {'query': 'UMI Finger 磁力计 magnetometer 支持 可用性'})
    assert result.status == 'ok'
    assert {source['source_id'] for source in result.sources} == {
        'daq-note-sdk-20260921', 'daq-note-sdk-20260923',
        'daq-note-sdk-20260929'}
    assert result.content['matched_requirement_ids'] == []
    assert result.content['device_support'] == 'unknown'


def test_one_source_bound_firmware_note_covers_named_historical_version_only():
    if not ARCHIVE.is_dir():
        pytest.skip('local ignored SDK originals unavailable')
    catalog = SdkCatalog.load(DIRECTORY, ARCHIVE)
    class Knowledge:
        release_id = 'dev-main'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []
    question = '双目 EGO 固件变更日志 v1.1.9 与 v1.1.10 主从切换和时间戳'
    requirements = [
        {'id': version, 'capability': 'search_knowledge',
         'entities': ['EGO'], 'conditions': {'firmware_version': version}}
        for version in ('v1.1.9', 'v1.1.10', 'v1.1.8')]
    result = DaqToolBox(question=question, knowledge=Knowledge(),
                        role='internal_fae', requirements=requirements).dispatch(
                            'search_knowledge', {'query': question})
    assert result.status == 'ok'
    assert result.content['matched_requirement_ids'] == ['v1.1.10', 'v1.1.9']


def test_viewer_model_sdk_aliases_attributed_to_both_dated_notes():
    if not ARCHIVE.is_dir():
        pytest.skip('local ignored SDK originals unavailable')
    from daq_fae.request_schema import DaqRequestSchema
    catalog = SdkCatalog.load(DIRECTORY, ARCHIVE)
    class Knowledge:
        release_id = 'dev-main'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []
    question = ('Android EgoViewer 都显示 v1.0.11，2026-09-23 与 2026-09-29 '
                '的网络设备枚举行为能当作相同吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'search_knowledge',
             'entities': ['EgoViewer'],
             'conditions': {'feature': 'network_device_enumeration',
                            'platform': 'android', 'sdk': 'egoviewer',
                            'sdk_release_date': date, 'sdk_version': '1.0.11'}}
            for date in ('2026-09-23', '2026-09-29')
        ]}, sdk_catalog=catalog)
    requirements = [
        {'id': str(index), 'capability': 'search_knowledge',
         'entities': list(goal.entities), 'conditions': goal.condition_map()}
        for index, goal in enumerate(schema.goals)]
    result = DaqToolBox(question=question, knowledge=Knowledge(),
                        role='internal_fae', requirements=requirements).dispatch(
                            'search_knowledge', {'query': question})
    assert result.status == 'ok'
    assert result.content['matched_requirement_ids'] == ['0', '1']


def test_firmware_note_aspect_goals_match_historical_and_current_sections():
    if not ARCHIVE.is_dir():
        pytest.skip('local ignored SDK originals unavailable')
    from daq_fae.request_schema import DaqRequestSchema
    catalog = SdkCatalog.load(DIRECTORY, ARCHIVE)
    class Knowledge:
        release_id = 'dev-main'
        sdk_catalog = catalog
        def records_for(self, role): return []
        def sections_for(self, role): return []
    question = ('双目 EGO 固件变更日志在 v1.1.9 与 v1.1.10 分别记载了哪些'
                '主从/同步修改？这些历史说明能证明与 OrbbecSDK 2.9.0 已配对实测吗？')
    schema = DaqRequestSchema.from_candidate(question, {
        'confidence': 'high', 'goals': [
            {'kind': 'product', 'capability': 'search_knowledge',
             'entities': ['EGO'],
             'conditions': {'aspect': 'master_slave_sync',
                            'firmware_version': version}}
            for version in ('v1.1.9', 'v1.1.10')
        ]}, sdk_catalog=catalog)
    requirements = [
        {'id': str(index), 'capability': 'search_knowledge',
         'entities': list(goal.entities), 'conditions': goal.condition_map()}
        for index, goal in enumerate(schema.goals)]
    result = DaqToolBox(question=question, knowledge=Knowledge(),
                        role='internal_fae', requirements=requirements).dispatch(
                            'search_knowledge', {'query': question})
    assert result.status == 'ok'
    assert result.content['matched_requirement_ids'] == ['0', '1']
