"""Coverage is counted from evidence references, not archive presence."""

import hashlib
import json
import stat

import pytest

from scripts.audit_daq_material_coverage import (
    build_coverage, compare_coverage_reports, load_candidate_files,
    verify_sdk_material_inventory, write_private_report)


def _source(path, digest):
    return {"path": path, "sha256": digest, "size": 3}


def test_every_original_has_separate_intake_and_answer_status():
    product = {"files": [_source("product.md", "a" * 64),
                         _source("diagram.png", "b" * 64)]}
    sdk = {"files": [_source("drop/changelog.txt", "c" * 64),
                     _source("drop/sdk.zip", "b" * 64)]}
    disposition = {"text_dispositions": [_source("product.md", "a" * 64)]}
    assets = {"asset_inventory": [_source("diagram.png", "b" * 64)]}
    candidate = [{"id": "draft-1", "source_refs": [
        {"path": "product.md", "sha256": "a" * 64}]}]
    published = [{"id": "fact-1", "answerable": True, "status": "verified",
                  "access_review": {"view_roles": ["internal_fae"]}, "source_refs": [
        {"path": "product.md", "sha256": "a" * 64}]}]
    sdk_records = [{"kb_id": "sdk-1", "view_roles": ["internal_fae"], "source_refs": [
        {"archive_path": "drop/sdk.zip", "package_sha256": "b" * 64}]}]

    report = build_coverage(product, sdk, disposition, assets, candidate, [],
                            published, sdk_records)

    assert report["summary"]["product"] == {
        "total": 2, "published": 1, "candidate_only": 0, "archive_only": 1}
    assert report["summary"]["sdk"] == {
        "total": 2, "published": 1, "candidate_only": 0, "archive_only": 1}
    rows = {(row["archive"], row["path"]): row for row in report["files"]}
    assert rows["product", "product.md"]["status"] == "published"
    assert rows["product", "diagram.png"]["status"] == "archive_only"
    assert rows["sdk", "drop/sdk.zip"]["status"] == "published"
    assert rows["sdk", "drop/changelog.txt"]["status"] == "archive_only"
    assert rows["product", "diagram.png"]["same_bytes_elsewhere"] == [
        "sdk:drop/sdk.zip"]
    assert rows["product", "diagram.png"]["status"] != "published"
    assert report["unpublished_product_candidate_records"] == ["draft-1"]


def test_references_must_match_archived_path_and_hash():
    product = {"files": [_source("product.md", "a" * 64)]}
    disposition = {"text_dispositions": [_source("product.md", "a" * 64)]}
    with pytest.raises(ValueError, match="reference hash mismatch"):
        build_coverage(product, {"files": []}, disposition,
                       {"asset_inventory": []},
                       [{"id": "draft", "source_refs": [
                           {"path": "product.md", "sha256": "b" * 64}]}],
                       [], [], [])


def test_product_disposition_must_cover_every_original_exactly_once():
    product = {"files": [_source("product.md", "a" * 64)]}
    with pytest.raises(ValueError, match="disposition inventory mismatch"):
        build_coverage(product, {"files": []}, {"text_dispositions": []},
                       {"asset_inventory": []}, [], [], [], [])


def test_non_answerable_or_role_hidden_release_rows_do_not_count_as_published():
    product = {"files": [_source("product.md", "a" * 64)]}
    sdk = {"files": [_source("sdk.zip", "b" * 64)]}
    report = build_coverage(
        product, sdk, {"text_dispositions": [_source("product.md", "a" * 64)]},
        {"asset_inventory": []}, [], [],
        [{"id": "draft", "answerable": False, "status": "candidate",
          "source_refs": [{"path": "product.md", "sha256": "a" * 64}]}],
        [{"kb_id": "restricted", "view_roles": ["engineering"],
          "source_refs": [{"archive_path": "sdk.zip", "package_sha256": "b" * 64}]}],
    )
    assert report["summary"]["product"]["published"] == 0
    assert report["summary"]["sdk"]["published"] == 0


def test_software_and_link_candidates_count_as_candidate_only():
    product = {"files": [_source("sdk.zip", "a" * 64),
                         _source("README.txt", "b" * 64)]}
    report = build_coverage(
        product, {"files": []}, {"text_dispositions": [
            _source("README.txt", "b" * 64)]},
        {"asset_inventory": [_source("sdk.zip", "a" * 64)]},
        [], [], [], [],
        software_candidates=[{"id": "software-1", "source_refs": [
            {"path": "sdk.zip", "sha256": "a" * 64}]}],
        link_candidates=[{"id": "link-1", "source_ref": {
            "source_path": "README.txt", "source_sha256": "b" * 64}}],
    )
    assert report["summary"]["product"]["candidate_only"] == 2
    assert report["summary"]["product"]["archive_only"] == 0


def test_sdk_catalog_candidate_does_not_imply_activation():
    sdk = {"files": [_source("sdk.zip", "a" * 64)]}
    report = build_coverage(
        {"files": []}, sdk, {"text_dispositions": []},
        {"asset_inventory": []}, [], [], [], [],
        sdk_candidate_records=[{"kb_id": "candidate-sdk", "source_refs": [
            {"archive_path": "sdk.zip", "package_sha256": "a" * 64}]}],
    )
    assert report["summary"]["sdk"]["candidate_only"] == 1
    assert report["summary"]["sdk"]["published"] == 0


def test_unpublished_sdk_record_on_published_package_is_counted():
    sdk = {"files": [_source("sdk.zip", "a" * 64)]}
    source_ref = {"archive_path": "sdk.zip", "package_sha256": "a" * 64}
    report = build_coverage(
        {"files": []}, sdk, {"text_dispositions": []},
        {"asset_inventory": []}, [], [], [],
        [{"kb_id": "published", "view_roles": ["internal_fae"],
          "source_refs": [source_ref]}],
        sdk_candidate_records=[
            {"kb_id": "published", "source_refs": [source_ref]},
            {"kb_id": "new-draft", "source_refs": [source_ref]}],
    )
    assert report["summary"]["sdk"]["candidate_only"] == 0
    assert report["unpublished_sdk_candidate_records"] == ["new-draft"]


def test_sdk_release_note_and_identical_copy_are_candidate_not_published():
    digest = "a" * 64
    sdk = {"files": [_source("linux/changelog.txt", digest),
                     _source("windows/changelog.txt", digest)]}
    note = {"kb_id": "note-one", "file_sha256": digest,
            "source_refs": [{"archive_path": "linux/changelog.txt",
                             "package_sha256": digest}],
            "duplicate_archive_paths": ["windows/changelog.txt"]}
    report = build_coverage(
        {"files": []}, sdk, {"text_dispositions": []},
        {"asset_inventory": []}, [], [], [], [],
        sdk_note_candidate_records=[note])
    assert report["summary"]["sdk"]["candidate_only"] == 2
    assert report["unpublished_sdk_note_candidate_records"] == ["note-one"]
    assert report["summary"]["sdk"]["published"] == 0
    published = {**note, "view_roles": ["internal_fae"]}
    active = build_coverage(
        {"files": []}, sdk, {"text_dispositions": []},
        {"asset_inventory": []}, [], [], [], [],
        sdk_note_candidate_records=[note],
        sdk_published_note_records=[published])
    assert active["summary"]["sdk"]["published"] == 2
    assert active["unpublished_sdk_note_candidate_records"] == []


def test_external_link_discovery_is_reported_without_fabricated_archive_ref():
    report = build_coverage(
        {"files": []}, {"files": []}, {"text_dispositions": []},
        {"asset_inventory": []}, [], [], [], [],
        link_candidates=[{"id": "external-1", "source_ref": {
            "discovery": "official_site_navigation"}}],
    )
    assert report["external_link_discoveries"] == ["external-1"]


def test_candidate_files_are_bound_to_frozen_summary_and_archive(tmp_path):
    names = ["_sources/source-disposition.json", "_sources/asset-inventory.json",
             "_facts/all-records.json", "_sections/section-index.json",
             "sdk/software-evidence-matrix.json", "links/candidate-link-ledger.json"]
    hashes = {}
    for name in names:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = b"[]" if name.endswith(("all-records.json", "section-index.json",
                                      "software-evidence-matrix.json")) else b"{}"
        path.write_bytes(raw)
        hashes[name] = hashlib.sha256(raw).hexdigest()
    summary = {"archive_manifest_sha256": "a" * 64,
               "input_sha256": {"archive_manifest": "a" * 64},
               "output_sha256": hashes}
    (tmp_path / "summary.json").write_text(json.dumps(summary))
    assert len(load_candidate_files(tmp_path, "a" * 64)) == 6
    (tmp_path / names[0]).write_text('{"changed":true}')
    with pytest.raises(ValueError, match="candidate file digest mismatch"):
        load_candidate_files(tmp_path, "a" * 64)
    with pytest.raises(ValueError, match="candidate archive mismatch"):
        load_candidate_files(tmp_path, "b" * 64)


def test_report_is_private_and_confined_to_private_root(tmp_path):
    private_root = tmp_path / "curated"
    private_root.mkdir()
    target = private_root / "audit" / "report.json"
    write_private_report(target, {"files": []}, private_root)
    assert stat.S_IMODE(target.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert json.loads(target.read_text()) == {"files": []}
    with pytest.raises(ValueError, match="private output root"):
        write_private_report(tmp_path / "public.json", {}, private_root)


def test_incremental_report_detects_source_and_knowledge_changes():
    def item(path, digest, status="archive_only", candidates=()):
        return {"archive": "product", "path": path, "sha256": digest,
                "status": status, "candidate_records": list(candidates),
                "published_records": []}
    previous = {"format_version": "daq-material-coverage/v1", "files": [
        item("removed", "a"), item("changed", "a"),
        item("promoted", "a"), item("refs", "a", "candidate_only", ["x"])]}
    current = {"format_version": "daq-material-coverage/v1", "files": [
        item("added", "b"), item("changed", "b"),
        item("promoted", "a", "published"),
        item("refs", "a", "candidate_only", ["x", "y"])]}
    delta = compare_coverage_reports(previous, current)
    assert delta == {
        "added": ["product:added"], "removed": ["product:removed"],
        "source_changed": ["product:changed"],
        "lifecycle_changed": ["product:promoted"],
        "references_changed": ["product:refs"], "unchanged": []}


def test_visual_candidate_requires_exact_image_and_markdown_mention():
    digest = "a" * 64
    md_digest = "b" * 64
    product = {"files": [_source("figure.png", digest),
                         _source("guide.md", md_digest)]}
    disposition = {"text_dispositions": [_source("guide.md", md_digest)]}
    asset = _source("figure.png", digest)
    asset["document_references"] = [{"source_path": "guide.md",
                                     "source_sha256": md_digest,
                                     "locator": {"kind": "lines", "start": 7, "end": 9}}]
    candidate = {"id": "visual-one", "decision": "private_dev_candidate_only",
                 "authority": {"answerable": False, "published": False,
                               "view_roles": [], "forward_roles": []},
                 "source": {"image_path": "figure.png", "image_sha256": digest,
                            "image_size": 3, "image_dimensions_px": [100, 100],
                            "region_px": [1, 2, 80, 90],
                            "markdown_mentions": [{"document_path": "guide.md",
                                                   "document_sha256": md_digest,
                                                   "line": 8}]}}
    report = build_coverage(product, {"files": []}, disposition,
                            {"asset_inventory": [asset]}, [], [], [], [],
                            visual_candidates=[candidate])
    assert report["summary"]["product"]["candidate_only"] == 1
    assert report["unpublished_visual_candidate_records"] == ["visual-one"]
    tampered = json.loads(json.dumps(candidate))
    tampered["source"]["markdown_mentions"][0]["line"] = 10
    with pytest.raises(ValueError, match="visual mention mismatch"):
        build_coverage(product, {"files": []}, disposition,
                       {"asset_inventory": [asset]}, [], [], [], [],
                       visual_candidates=[tampered])

    redacted = json.loads(json.dumps(candidate))
    redacted["decision"] = "redacted_crop_only_if_promoted"
    report = build_coverage(product, {"files": []}, disposition,
                            {"asset_inventory": [asset]}, [], [], [], [],
                            visual_candidates=[redacted])
    assert report["summary"]["product"]["archive_only"] == 2
    assert report["unpublished_visual_candidate_records"] == []

    authorized = json.loads(json.dumps(candidate))
    authorized["authority"]["answerable"] = True
    with pytest.raises(ValueError, match="visual candidate authority invalid"):
        build_coverage(product, {"files": []}, disposition,
                       {"asset_inventory": [asset]}, [], [], [], [],
                       visual_candidates=[authorized])


def test_sdk_material_inventory_requires_every_exact_original(tmp_path):
    files = [_source("SDK/drop/sdk.zip", "a" * 64),
             _source("Tool/drop/ChangeLog.txt", "b" * 64)]
    catalog = {"packages": {"sdk": {"archive_path": "SDK/drop/sdk.zip"}}}
    from daq_fae.knowledge.sdk_materials import build_inventory
    rows = build_inventory(files, source_checked_packages={"SDK/drop/sdk.zip"})
    payload = b"".join((json.dumps(row, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":")) + "\n").encode()
                       for row in rows)
    summary = {"format_version": "daq-sdk-material-inventory/v1",
               "archive_manifest_sha256": "c" * 64,
               "index_sha256": hashlib.sha256(payload).hexdigest(),
               "original_count": 2, "unique_file_hashes": 2,
               "source_checked_packages": 1, "device_support": "unknown"}
    (tmp_path / "material_inventory.jsonl").write_bytes(payload)
    (tmp_path / "material_inventory_manifest.json").write_text(json.dumps(summary))
    assert verify_sdk_material_inventory(files, catalog, tmp_path, "c" * 64) == summary
    (tmp_path / "material_inventory.jsonl").write_bytes(payload.replace(b"unknown", b"supported"))
    with pytest.raises(ValueError, match="SDK material inventory"):
        verify_sdk_material_inventory(files, catalog, tmp_path, "c" * 64)
