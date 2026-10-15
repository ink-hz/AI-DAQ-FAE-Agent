"""SDK usage is source checked and separate from software support."""

import io
import json
from pathlib import Path
import tarfile

import pytest

from daq_fae.domain_tools import DaqToolBox
from daq_fae.domain_evidence import DaqEvidencePolicy
from daq_fae.knowledge.sdk_catalog import SdkCatalog, _serialized, build_catalog
from daq_fae.knowledge.sdk_release import activate_sdk_release, load_active_sdk_release, stage_sdk_release
from daq_fae.request_schema import DaqRequestSchema
from scripts.archive_candidate_snapshot import create_archive


def _package(path: Path, member: str, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:gz") as bundle:
        info = tarfile.TarInfo(member)
        info.size = len(content)
        bundle.addfile(info, io.BytesIO(content))


def _fixture(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    from daq_fae.knowledge import sdk_catalog
    monkeypatch.setattr(sdk_catalog, "PACKAGES", {
        "host": {"glob": "host.tar.gz", "version": "2.9.0", "build": "20260929", "platform": "Linux"},
    })
    source = tmp_path / "source"
    _package(source / "host.tar.gz", "pkg/examples/sync.cpp",
             b"int main() {\n ob_capture_config_t cfg; cfg.enable_mask = setSyncMode();\n}\n")
    archive = tmp_path / "archive"
    create_archive(source, archive, "2026-10-15")
    directory = tmp_path / "catalog"
    directory.mkdir()
    (directory / "curated.json").write_text(json.dumps([{
        "kb_id": "sdk-sync", "package": "host", "sdk_layer": "host_cpp",
        "doc_type": "example", "title": "EGO 多设备同步", "question_patterns": ["怎么设置同步模式"],
        "answer": "调用 setSyncMode 前检查位图。", "symbols": ["setSyncMode"], "topics": ["sync"],
        "source_refs": [{"member_suffix": "examples/sync.cpp", "start_line": 1, "end_line": 3}],
    }]))
    records, manifest = build_catalog(directory / "curated.json", archive)
    (directory / "SDK_Knowledge.jsonl").write_bytes(_serialized(records))
    (directory / "SDK_Knowledge_manifest.json").write_text(json.dumps(manifest))
    return directory, archive


def test_catalog_source_binding_and_tamper(tmp_path, monkeypatch):
    directory, archive = _fixture(tmp_path, monkeypatch)
    catalog = SdkCatalog.load(directory, archive)
    assert catalog.search("EGO 同步模式", "internal_fae")[0]["kb_id"] == "sdk-sync"
    assert {"ob_capture_config_t", "enable_mask"} <= set(catalog.records[0]["code_tokens"])
    assert catalog.search("EGO 同步模式", "channel") == []
    assert catalog.search("20260923 EGO 同步模式", "internal_fae") == []
    assert catalog.search("2026-09-23 EGO 同步模式", "internal_fae") == []
    records = (directory / "SDK_Knowledge.jsonl").read_text().replace("检查位图", "直接设置")
    (directory / "SDK_Knowledge.jsonl").write_text(records)
    with pytest.raises(ValueError, match="differs"):
        SdkCatalog.load(directory, archive)


def test_firmware_version_does_not_hide_sdk_usage_evidence(tmp_path, monkeypatch):
    directory, archive = _fixture(tmp_path, monkeypatch)
    catalog = SdkCatalog.load(directory, archive)
    assert catalog.search("EGO 固件 1.1.8 怎么设置同步模式", "internal_fae")
    assert catalog.search("OrbbecSDK 1.1.8 怎么设置同步模式", "internal_fae") == []


def test_catalog_rejects_invalid_source_lines(tmp_path, monkeypatch):
    directory, archive = _fixture(tmp_path, monkeypatch)
    curated = json.loads((directory / "curated.json").read_text())
    curated[0]["source_refs"][0]["end_line"] = 4
    (directory / "curated.json").write_text(json.dumps(curated))
    with pytest.raises(ValueError, match="line span"):
        build_catalog(directory / "curated.json", archive)


def test_catalog_rejects_symbol_outside_selected_source_spans(tmp_path, monkeypatch):
    directory, archive = _fixture(tmp_path, monkeypatch)
    curated = json.loads((directory / "curated.json").read_text())
    curated[0]["symbols"].append("inventedApi")
    (directory / "curated.json").write_text(json.dumps(curated))
    with pytest.raises(ValueError, match="SDK symbol lacks source span"):
        build_catalog(directory / "curated.json", archive)


def test_sdk_tool_returns_usage_without_certifying_device_support(tmp_path, monkeypatch):
    directory, archive = _fixture(tmp_path, monkeypatch)
    catalog = SdkCatalog.load(directory, archive)

    class Knowledge:
        release_id = "dev-sdk"
        sdk_catalog = catalog

        def records_for(self, role):
            return []

        def sections_for(self, role):
            return []

    toolbox = DaqToolBox(knowledge=Knowledge(), role="internal_fae")
    result = toolbox.dispatch("sdk_evidence", {"query": "EGO 同步模式怎么设置"})
    assert result.status == "ok"
    assert result.content["device_support"] == "unknown"
    assert result.content["matched_requirement_ids"] == []
    assert result.sources and "source_refs" in result.sources[0]
    assert toolbox.dispatch("check_software_support", {"query": "EGO 同步模式"}).status == "not_found"


def test_sdk_usage_covers_only_usage_goal_in_dev(tmp_path, monkeypatch):
    directory, archive = _fixture(tmp_path, monkeypatch)
    catalog = SdkCatalog.load(directory, archive)

    class Knowledge:
        release_id = "dev-sdk"
        sdk_catalog = catalog

        def records_for(self, role):
            return []

        def sections_for(self, role):
            return []

    requirements = [
        {"id": "usage", "capability": "sdk_evidence", "entities": ["EGO"],
         "conditions": {"sdk_version": "2.9.0"}, "evidence_class": "governed_required"},
        {"id": "support", "capability": "check_software_support", "entities": ["EGO"],
         "conditions": {"firmware_version": "1.1.9"}, "evidence_class": "governed_required"},
    ]
    toolbox = DaqToolBox(question="EGO 同步模式怎么设置", knowledge=Knowledge(),
                         role="internal_fae", requirements=requirements)
    result = toolbox.dispatch("sdk_evidence", {"query": "EGO 同步模式"})
    assert result.content["matched_requirement_ids"] == ["usage"]
    dev = DaqEvidencePolicy(allow_dev_source_check=True).begin({"requirements": requirements})
    dev.observe("sdk_evidence", result)
    assert dev.status == {"usage": "satisfied", "support": "unknown"}
    regular = DaqEvidencePolicy().begin({"requirements": requirements})
    regular.observe("sdk_evidence", result)
    assert regular.status == {"usage": "unknown", "support": "unknown"}


def test_sdk_build_date_is_a_supported_request_condition():
    question = "用 2026-09-29 的 OrbbecSDK 2.9.0 示例怎样设置 EGO 同步模式？"
    schema = DaqRequestSchema.from_candidate(question, {
        "confidence": "high", "answer_language": "zh", "request_kind": "product",
        "goals": [{"kind": "product", "capability": "sdk_evidence",
                   "entities": ["EGO"], "conditions": {
                       "sdk_version": "2.9.0", "sdk_release_date": "2026-09-29"}}],
    })
    assert schema.goals[0].unmapped_condition_keys == ()


def test_sdk_task_context_requires_exact_source_checked_usage_task():
    class Knowledge:
        release_id = "dev-sdk"

        def records_for(self, role):
            return []

        def sections_for(self, role):
            return []

    toolbox = DaqToolBox(knowledge=Knowledge(), role="internal_fae", requirements=[
        {"id": "capture", "capability": "sdk_evidence", "entities": [],
         "conditions": {"task": ["capture", "startup"], "language": "C", "sdk": "libobkit"}},
        {"id": "other", "capability": "sdk_evidence", "entities": [],
         "conditions": {"task": ["firmware_upgrade"], "sdk": "libobkit"}},
        {"id": "composite", "capability": "sdk_evidence", "entities": [],
         "conditions": {"feature": "camera_imu_audio_capture_init_start_order",
                        "language": "c", "sdk": "libobkit"}},
        {"id": "phrased_task", "capability": "sdk_evidence", "entities": [],
         "conditions": {"feature": "camera_imu_audio_capture",
                        "task": "initialization_and_start_order",
                        "language": "c", "sdk": "libobkit"}},
    ])
    row = {"sdk_layer": "device_c", "version": "20260909", "build": "20260909",
           "platform": "RK3576 AArch64 Linux", "doc_type": "example",
           "usage_tasks": ["capture", "startup"],
           "topics": ["camera", "imu", "audio"],
           "symbols": ["ob_capture_init", "ob_capture_start"]}
    assert toolbox._sdk_usage_requirement_ids([row]) == ["capture", "composite", "phrased_task"]


def test_sdk_name_alias_can_match_package_usage_without_user_build_selector():
    class Knowledge:
        release_id = "dev-sdk"

        def records_for(self, role):
            return []

        def sections_for(self, role):
            return []

    toolbox = DaqToolBox(knowledge=Knowledge(), role="internal_fae", requirements=[
        {"id": "capture", "capability": "sdk_evidence", "entities": [],
         "conditions": {"sdk_name": "libobkit", "language_binding": "C",
                        "task": ["capture", "startup"]}},
    ])
    row = {"sdk_layer": "device_c", "version": "20260909", "build": "20260909",
           "platform": "RK3576 AArch64 Linux", "doc_type": "example",
           "usage_tasks": ["capture", "startup"]}
    assert toolbox._sdk_usage_requirement_ids([row]) == ["capture"]

    host = DaqToolBox(knowledge=Knowledge(), role="internal_fae", requirements=[
        {"id": "host", "capability": "sdk_evidence", "entities": ["EGO"],
         "conditions": {"sdk_name": "OrbbecSDK", "language_binding": "C++"}},
    ])
    assert host._sdk_usage_requirement_ids([{**row, "sdk_layer": "host_cpp"}]) == ["host"]


def test_sdk_build_date_given_as_version_selects_exact_build():
    class Knowledge:
        release_id = "dev-sdk"

        def records_for(self, role):
            return []

        def sections_for(self, role):
            return []

    toolbox = DaqToolBox(knowledge=Knowledge(), role="internal_fae", requirements=[
        {"id": "old", "capability": "sdk_evidence", "entities": ["EGO"],
         "conditions": {"sdk_version": "2026-09-23", "api": "downloadSdcardFile",
                        "feature": "sd_card_file_service"}},
    ])
    row = {"sdk_layer": "host_cpp", "version": "2.9.0", "build": "20260923",
           "platform": "Linux x86_64", "doc_type": "example",
           "symbols": ["downloadSdcardFile"], "usage_tasks": ["sdcard_file_service"]}
    assert toolbox._sdk_usage_requirement_ids([row]) == ["old"]


def test_calibration_output_names_are_checked_against_source_record():
    class Knowledge:
        release_id = "dev-sdk"

        def records_for(self, role):
            return []

        def sections_for(self, role):
            return []

    toolbox = DaqToolBox(knowledge=Knowledge(), role="internal_fae", requirements=[
        {"id": "yaml", "capability": "sdk_evidence", "entities": ["EGO"],
         "conditions": {"export_files": ["align_calib.yaml", "imu_calib.yaml"]}},
        {"id": "wrong", "capability": "sdk_evidence", "entities": ["EGO"],
         "conditions": {"export_files": ["secret.yaml"]}},
    ])
    row = {"sdk_layer": "host_cpp", "version": "2.9.0", "build": "20260929",
           "platform": "Linux x86_64", "doc_type": "example",
           "outputs": ["align_calib.yaml", "imu_calib.yaml"]}
    assert toolbox._sdk_usage_requirement_ids([row]) == ["yaml"]


def test_sdk_release_is_immutable_and_reversible(tmp_path, monkeypatch):
    directory, archive = _fixture(tmp_path, monkeypatch)
    catalog = SdkCatalog.load(directory, archive)
    answer = "调用 setSyncMode 前检查位图。"
    import hashlib
    replay = tmp_path / "results.jsonl"
    replay.write_text(json.dumps({"case": {"id": "usage"},
        "identity": {"sdk_catalog_sha256": catalog.manifest["records_sha256"],
                     "suite_file_sha256": "c" * 64,
                     "runtime_release": "daq-tested",
                     "upstream_revision": "b" * 40},
        "turns": [{"done": {"answer": answer, "outcome": "resolved",
                            "trace_id": "d" * 32, "fallback_used": False},
                    "integrity": {"stage_lifecycle_closed": True}}]}) + "\n")
    report = {"environment": "dev", "reviewer": "Codex",
              "catalog_sha256": catalog.manifest["records_sha256"],
              "results_sha256": hashlib.sha256(replay.read_bytes()).hexdigest(),
              "suite_sha256": "c" * 64,
              "cases": [{"id": "usage", "passed": True,
                         "answer_sha256": hashlib.sha256(answer.encode()).hexdigest(),
                         "trace_id": "d" * 32, "outcome": "resolved",
                         "fallback_used": False, "review_note": "Source matched."}]}
    root = tmp_path / "published"
    with pytest.raises(ValueError, match="runtime identity"):
        stage_sdk_release(root, directory, archive, report, replay_results=replay,
                          runtime_release="daq-untested", upstream_sha="b" * 40)
    with pytest.raises(ValueError, match="runtime identity"):
        stage_sdk_release(root, directory, archive, report, replay_results=replay,
                          runtime_release="daq-tested", upstream_sha="a" * 40)
    release_id = stage_sdk_release(root, directory, archive, report, replay_results=replay,
                                   runtime_release="daq-tested", upstream_sha="b" * 40)
    assert load_active_sdk_release(root, runtime_release="daq-tested",
                                   upstream_sha="b" * 40) is None
    activate_sdk_release(root, release_id)
    published = load_active_sdk_release(root, runtime_release="daq-tested",
                                        upstream_sha="b" * 40)
    assert published.published is True
    assert published.release_id == release_id
    assert published.search("EGO 同步模式", "internal_fae")
    assert published.search("EGO 同步模式", "channel") == []
    with pytest.raises(ValueError, match="runtime"):
        load_active_sdk_release(root, runtime_release="wrong", upstream_sha="b" * 40)
    manifest = root / "releases" / release_id / "manifest.json"
    manifest.chmod(0o600)
    manifest.write_text(manifest.read_text().replace("调用", "直接"))
    with pytest.raises(ValueError, match="digest"):
        load_active_sdk_release(root, runtime_release="daq-tested", upstream_sha="b" * 40)


def test_published_sdk_source_satisfies_regular_evidence_policy(tmp_path, monkeypatch):
    directory, archive = _fixture(tmp_path, monkeypatch)
    catalog = SdkCatalog.load(directory, archive)
    catalog.published = True
    catalog.release_id = "a" * 64

    class Knowledge:
        release_id = "main-release"
        sdk_catalog = catalog

        def records_for(self, role):
            return []

        def sections_for(self, role):
            return []

    requirement = {"id": "usage", "capability": "sdk_evidence",
                   "entities": ["EGO"], "conditions": {"sdk_version": "2.9.0"}}
    result = DaqToolBox(question="EGO 同步模式怎么设置", knowledge=Knowledge(),
                        role="internal_fae", requirements=[requirement]).dispatch(
                            "sdk_evidence", {"query": "EGO 同步模式"})
    assert result.sources[0]["type"] == "daq_governed_sdk"
    assert result.sources[0]["release_id"] == catalog.release_id
    session = DaqEvidencePolicy().begin({"requirements": [requirement]})
    session.observe("sdk_evidence", result)
    assert session.status["usage"] == "satisfied"


def test_device_side_package_aliases_can_cover_usage_without_image_support():
    class Knowledge:
        release_id = "dev-sdk"

        def records_for(self, role):
            return []

        def sections_for(self, role):
            return []

    toolbox = DaqToolBox(knowledge=Knowledge(), role="internal_fae", requirements=[
        {"id": "usage", "capability": "sdk_evidence", "entities": ["四目 EGO"],
         "conditions": {"sdk": "libobkit", "platform": "device_side"}},
        {"id": "support", "capability": "check_software_support", "entities": ["四目 EGO"],
         "conditions": {"image_version": "any"}},
    ])
    matches = [{"sdk_layer": "device_c", "version": "20260909",
                "build": "20260909", "platform": "RK3576 AArch64 Linux",
                "doc_type": "build"}]
    assert toolbox._sdk_usage_requirement_ids(matches) == ["usage"]
    schema = DaqRequestSchema.from_candidate(
        "四目 EGO 设备端 libobkit 示例怎么编译？",
        {"confidence": "high", "answer_language": "zh", "request_kind": "product",
         "goals": [{"kind": "product", "capability": "sdk_evidence",
                    "entities": ["四目 EGO"],
                    "conditions": {"sdk": "libobkit", "platform": "device_side"}}]})
    assert schema.goals[0].unmapped_condition_keys == ()
