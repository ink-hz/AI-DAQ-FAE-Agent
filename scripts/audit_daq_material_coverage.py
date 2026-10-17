"""Reconcile every DAQ original with candidate and published evidence refs.

The report is an intake ledger. An archived or duplicate file is not an
answerable fact, and references in a candidate graph are not publication.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import tempfile

from daq_fae.knowledge.releases import read_active_release
from daq_fae.knowledge.code_identity import daq_code_sha
from daq_fae.knowledge.reviewed_view import ReviewedKnowledge
from daq_fae.knowledge.sdk_catalog import SdkCatalog
from daq_fae.knowledge.sdk_materials import build_inventory
from daq_fae.knowledge.sdk_release import load_active_sdk_release
from scripts.archive_candidate_snapshot import verify_archive


_CANDIDATE_FILES = (
    "_sources/source-disposition.json", "_sources/asset-inventory.json",
    "_facts/all-records.json", "_sections/section-index.json",
    "sdk/software-evidence-matrix.json", "links/candidate-link-ledger.json",
)


def _load_sdk_for_audit(root: Path, runtime_release: str, upstream_sha: str,
                        knowledge_release: str):
    """Bind the SDK notes to the exact DAQ code and active main knowledge."""
    return load_active_sdk_release(
        root, runtime_release=runtime_release, upstream_sha=upstream_sha,
        daq_code_sha256=daq_code_sha(), knowledge_release=knowledge_release)


def _inventory(rows: list[dict], label: str) -> dict[str, dict]:
    result = {}
    for row in rows:
        path = row["path"]
        if path in result:
            raise ValueError(f"duplicate {label} path: {path}")
        result[path] = row
    return result


def _check_ref(ref: dict, inventory: dict[str, dict], *, sdk: bool) -> str:
    path_key, hash_key = (("archive_path", "package_sha256") if sdk
                          else ("path", "sha256"))
    path = ref[path_key]
    source = inventory.get(path)
    if source is None or source["sha256"] != ref[hash_key]:
        raise ValueError("reference hash mismatch: " + path)
    return path


def load_candidate_files(root: Path, archive_manifest_sha256: str) -> dict[str, object]:
    """Read only source-bound files from one frozen candidate layout."""
    summary = _json(root / "summary.json")
    if (summary.get("archive_manifest_sha256") != archive_manifest_sha256 or
            summary.get("input_sha256", {}).get("archive_manifest") !=
            archive_manifest_sha256):
        raise ValueError("candidate archive mismatch")
    results = {}
    for name in _CANDIDATE_FILES:
        path = root / name
        if path.is_symlink():
            raise ValueError("candidate file linked")
        raw = path.read_bytes()
        if (hashlib.sha256(raw).hexdigest() !=
                summary.get("output_sha256", {}).get(name)):
            raise ValueError("candidate file digest mismatch: " + name)
        value = json.loads(raw)
        if (isinstance(value, dict) and "archive_manifest_sha256" in value
                and value["archive_manifest_sha256"] != archive_manifest_sha256):
            raise ValueError("candidate archive mismatch: " + name)
        results[name] = value
    return results


def verify_sdk_material_inventory(files: list[dict], catalog_manifest: dict,
                                  directory: Path, archive_manifest_sha256: str) -> dict:
    """Rebuild the non-authoritative index and reject stale or partial intake."""
    packages = {row["archive_path"] for row in catalog_manifest["packages"].values()}
    rows = build_inventory(files, source_checked_packages=packages)
    payload = b"".join((json.dumps(row, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":")) + "\n").encode()
                       for row in rows)
    manifest = {"format_version": "daq-sdk-material-inventory/v1",
                "archive_manifest_sha256": archive_manifest_sha256,
                "index_sha256": hashlib.sha256(payload).hexdigest(),
                "original_count": len(rows),
                "unique_file_hashes": len({row["sha256"] for row in rows}),
                "source_checked_packages": len(packages),
                "device_support": "unknown"}
    index_path = directory / "material_inventory.jsonl"
    summary_path = directory / "material_inventory_manifest.json"
    if (index_path.is_symlink() or summary_path.is_symlink()
            or index_path.read_bytes() != payload or _json(summary_path) != manifest):
        raise ValueError("SDK material inventory differs from verified originals")
    return manifest


def write_private_report(path: Path, report: dict, private_root: Path) -> None:
    """Atomically write private source paths and hashes under a restricted dir."""
    if private_root.is_symlink() or not private_root.is_dir():
        raise ValueError("private output root invalid")
    root = private_root.resolve()
    if (path.is_symlink() or not path.resolve(strict=False).is_relative_to(root)
            or path.parent.resolve(strict=False) == root):
        raise ValueError("report must be below private output root")
    parent = path.parent
    if parent.is_symlink():
        raise ValueError("report directory linked")
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent.chmod(0o700)
    raw = (json.dumps(report, ensure_ascii=False, indent=2,
                      sort_keys=True) + "\n").encode()
    descriptor, stage_name = tempfile.mkstemp(prefix=".coverage-", dir=parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(stage_name, path)
        path.chmod(0o600)
    finally:
        if os.path.exists(stage_name):
            os.unlink(stage_name)


def compare_coverage_reports(previous: dict, current: dict) -> dict[str, list[str]]:
    """Separate source churn from knowledge and citation lifecycle changes."""
    if (previous.get("format_version") != "daq-material-coverage/v1" or
            current.get("format_version") != "daq-material-coverage/v1"):
        raise ValueError("coverage report format mismatch")

    def indexed(report: dict) -> dict[str, dict]:
        rows = {}
        for row in report["files"]:
            key = f"{row['archive']}:{row['path']}"
            if key in rows:
                raise ValueError("duplicate coverage path: " + key)
            rows[key] = row
        return rows

    old, new = indexed(previous), indexed(current)
    delta = {key: [] for key in ("added", "removed", "source_changed",
                                "lifecycle_changed", "references_changed",
                                "unchanged")}
    for key in sorted(old.keys() | new.keys()):
        if key not in old:
            category = "added"
        elif key not in new:
            category = "removed"
        elif old[key]["sha256"] != new[key]["sha256"]:
            category = "source_changed"
        elif old[key]["status"] != new[key]["status"]:
            category = "lifecycle_changed"
        elif any(old[key][field] != new[key][field]
                 for field in ("candidate_records", "published_records")):
            category = "references_changed"
        else:
            category = "unchanged"
        delta[category].append(key)
    return delta


def build_coverage(product_manifest: dict, sdk_manifest: dict,
                   disposition: dict, assets: dict,
                   candidate_records: list[dict], candidate_sections: list[dict],
                   published_records: list[dict],
                   sdk_published_records: list[dict], *,
                   software_candidates: list[dict] = (),
                   link_candidates: list[dict] = (),
                   sdk_candidate_records: list[dict] = (),
                   sdk_note_candidate_records: list[dict] = (),
                   sdk_published_note_records: list[dict] = (),
                   visual_candidates: list[dict] = ()) -> dict:
    """Return an exact, source-bound inventory with independent lifecycle states."""
    inventories = {
        "product": _inventory(product_manifest["files"], "product original"),
        "sdk": _inventory(sdk_manifest["files"], "SDK original"),
    }
    intake_rows = (disposition["text_dispositions"] + assets["asset_inventory"])
    intake = _inventory(intake_rows, "product disposition")
    if set(intake) != set(inventories["product"]) or any(
            intake[path]["sha256"] != row["sha256"] or
            intake[path]["size"] != row["size"]
            for path, row in inventories["product"].items()):
        raise ValueError("disposition inventory mismatch")

    candidate_refs: dict[str, set[str]] = defaultdict(set)
    sdk_candidate_refs: dict[str, set[str]] = defaultdict(set)
    published_refs: dict[str, set[str]] = defaultdict(set)
    sdk_refs: dict[str, set[str]] = defaultdict(set)
    external_link_discoveries = []
    for row in [*candidate_records, *candidate_sections, *software_candidates]:
        for ref in row.get("source_refs", []):
            path = _check_ref(ref, inventories["product"], sdk=False)
            candidate_refs[path].add(row.get("id", row.get("section_id", "unknown")))
    for row in link_candidates:
        ref = row.get("source_ref")
        if ref is None:
            continue
        if ref.get("discovery") == "official_site_navigation":
            external_link_discoveries.append(row["id"])
            continue
        path = _check_ref({"path": ref["source_path"],
                           "sha256": ref["source_sha256"]},
                          inventories["product"], sdk=False)
        candidate_refs[path].add(row["id"])
    for row in sdk_candidate_records:
        for ref in row.get("source_refs", []):
            path = _check_ref(ref, inventories["sdk"], sdk=True)
            sdk_candidate_refs[path].add(row["kb_id"])
    for row in sdk_note_candidate_records:
        for ref in row.get("source_refs", []):
            path = _check_ref(ref, inventories["sdk"], sdk=True)
            sdk_candidate_refs[path].add(row["kb_id"])
        for path in row.get("duplicate_archive_paths", []):
            _check_ref({"archive_path": path,
                        "package_sha256": row["file_sha256"]},
                       inventories["sdk"], sdk=True)
            sdk_candidate_refs[path].add(row["kb_id"])
    visual_candidate_ids = []
    visual_decisions = {}
    for row in visual_candidates:
        record_id, decision, source = row["id"], row["decision"], row["source"]
        if record_id in visual_decisions or decision not in {
                "private_dev_candidate_only",
                "attach_image_evidence_to_existing_private_candidate",
                "redacted_crop_only_if_promoted"}:
            raise ValueError("visual candidate identity or decision invalid")
        visual_decisions[record_id] = decision
        authority = row["authority"]
        if (authority.get("answerable") is not False or
                authority.get("published") is not False or
                authority.get("view_roles") != [] or
                authority.get("forward_roles") != [] or
                authority.get("compatibility_verified", False) is not False):
            raise ValueError("visual candidate authority invalid")
        path = _check_ref({"path": source["image_path"],
                           "sha256": source["image_sha256"]},
                          inventories["product"], sdk=False)
        if path not in {item["path"] for item in assets["asset_inventory"]} or \
                source["image_size"] != inventories["product"][path]["size"]:
            raise ValueError("visual image identity mismatch")
        dimensions, region = source["image_dimensions_px"], source["region_px"]
        if (not isinstance(dimensions, list) or len(dimensions) != 2 or
                any(type(value) is not int or value <= 0 for value in dimensions) or
                not isinstance(region, list) or len(region) != 4 or
                any(type(value) is not int for value in region) or
                not 0 <= region[0] < region[2] <= dimensions[0] or
                not 0 <= region[1] < region[3] <= dimensions[1]):
            raise ValueError("visual region invalid")
        references = intake[path].get("document_references", [])
        if not isinstance(source.get("markdown_mentions"), list) or not source["markdown_mentions"]:
            raise ValueError("visual mention mismatch")
        for mention in source["markdown_mentions"]:
            document = inventories["product"].get(mention["document_path"])
            if (document is None or document["sha256"] != mention["document_sha256"]
                    or not any(ref.get("source_path") == mention["document_path"]
                               and ref.get("source_sha256") == mention["document_sha256"]
                               and ref.get("locator", {}).get("kind") == "lines"
                               and ref["locator"]["start"] <= mention["line"] <=
                               ref["locator"]["end"] for ref in references)):
                raise ValueError("visual mention mismatch")
        if decision != "redacted_crop_only_if_promoted":
            candidate_refs[path].add(record_id)
        if decision == "private_dev_candidate_only":
            visual_candidate_ids.append(record_id)
    for row in published_records:
        for ref in row.get("source_refs", []):
            path = _check_ref(ref, inventories["product"], sdk=False)
            if (row.get("answerable") is True and row.get("status") == "verified"
                    and "internal_fae" in row.get("access_review", {}).get("view_roles", [])):
                published_refs[path].add(row["id"])
    for row in sdk_published_records:
        for ref in row.get("source_refs", []):
            path = _check_ref(ref, inventories["sdk"], sdk=True)
            if "internal_fae" in row.get("view_roles", []):
                sdk_refs[path].add(row["kb_id"])
    for row in sdk_published_note_records:
        if "internal_fae" not in row.get("view_roles", []):
            continue
        for ref in row.get("source_refs", []):
            path = _check_ref(ref, inventories["sdk"], sdk=True)
            sdk_refs[path].add(row["kb_id"])
        for path in row.get("duplicate_archive_paths", []):
            _check_ref({"archive_path": path,
                        "package_sha256": row["file_sha256"]},
                       inventories["sdk"], sdk=True)
            sdk_refs[path].add(row["kb_id"])

    identical: dict[str, list[str]] = defaultdict(list)
    for archive, inventory in inventories.items():
        for path, source in inventory.items():
            identical[source["sha256"]].append(f"{archive}:{path}")
    files = []
    summary = {}
    text_paths = {item["path"] for item in disposition["text_dispositions"]}
    for archive, inventory in inventories.items():
        counts = {"total": len(inventory), "published": 0,
                  "candidate_only": 0, "archive_only": 0}
        for path, source in sorted(inventory.items()):
            published = (sdk_refs if archive == "sdk" else published_refs).get(path, set())
            candidate = (candidate_refs if archive == "product" else
                         sdk_candidate_refs).get(path, set())
            status = "published" if published else (
                "candidate_only" if candidate else "archive_only")
            counts[status] += 1
            label = f"{archive}:{path}"
            row = {"archive": archive, "path": path,
                   "sha256": source["sha256"], "size": source["size"],
                   "status": status, "published_records": sorted(published),
                   "candidate_records": sorted(candidate),
                   "same_bytes_elsewhere": sorted(
                       item for item in identical[source["sha256"]] if item != label)}
            if archive == "product":
                row.update(intake_kind=("text" if path in text_paths else "asset"),
                           intake_status=intake[path].get("status",
                                                          intake[path].get("content_review_status")))
            else:
                row["intake_kind"] = ("text" if Path(path).suffix.lower() in {
                    ".txt", ".url"} else "package_or_asset")
            files.append(row)
        summary[archive] = counts
    published_sdk_ids = {row["kb_id"] for row in sdk_published_records
                         if "internal_fae" in row.get("view_roles", [])}
    unpublished_sdk_ids = sorted({row["kb_id"] for row in sdk_candidate_records}
                                 - published_sdk_ids)
    published_note_ids = {row["kb_id"] for row in sdk_published_note_records
                          if "internal_fae" in row.get("view_roles", [])}
    unpublished_note_ids = sorted({row["kb_id"] for row in sdk_note_candidate_records}
                                  - published_note_ids)
    published_product_ids = {row["id"] for row in published_records}
    unpublished_product_ids = sorted({row["id"] for row in candidate_records}
                                     - published_product_ids)
    return {"format_version": "daq-material-coverage/v1", "summary": summary,
            "unique_content_hashes": len(identical),
            "unpublished_product_candidate_records": unpublished_product_ids,
            "unpublished_sdk_candidate_records": unpublished_sdk_ids,
            "unpublished_sdk_note_candidate_records": unpublished_note_ids,
            "unpublished_visual_candidate_records": sorted(visual_candidate_ids),
            "visual_review_decisions": visual_decisions,
            "external_link_discoveries": sorted(external_link_discoveries),
            "files": files}


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--product-archive", type=Path, required=True)
    parser.add_argument("--sdk-archive", type=Path, required=True)
    parser.add_argument("--candidate-root", type=Path, required=True)
    parser.add_argument("--product-release-root", type=Path, required=True)
    parser.add_argument("--sdk-release-root", type=Path, required=True)
    parser.add_argument("--product-release-id", required=True)
    parser.add_argument("--sdk-release-id", required=True)
    parser.add_argument("--runtime-release", required=True)
    parser.add_argument("--upstream-sha", required=True)
    parser.add_argument("--sdk-candidate-directory", type=Path, required=True)
    parser.add_argument("--visual-review-ledger", type=Path,
                        help="Private visual review candidates with exact source refs")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--previous-report", type=Path,
                        help="Previous private coverage report for source/review delta")
    args = parser.parse_args()
    product_verified = verify_archive(args.product_archive)
    sdk_verified = verify_archive(args.sdk_archive)
    candidate = load_candidate_files(args.candidate_root,
                                     product_verified["manifest_sha256"])
    product_active = read_active_release(args.product_release_root)
    product_view = ReviewedKnowledge.load_active(
        args.product_release_root, runtime_release=args.runtime_release,
        upstream_sha=args.upstream_sha)
    sdk_active = _load_sdk_for_audit(
        args.sdk_release_root, args.runtime_release, args.upstream_sha,
        args.product_release_id)
    if (product_active is None or product_view is None
            or product_active["release_id"] != args.product_release_id
            or product_view.release_id != args.product_release_id
            or sdk_active is None or sdk_active.release_id != args.sdk_release_id
            or product_active["manifest"]["archive_manifest_sha256"] !=
            product_verified["manifest_sha256"]
            or sdk_active.manifest["archive_manifest_sha256"] !=
            sdk_verified["manifest_sha256"]):
        raise ValueError("active release identity or archive mismatch")
    sdk_candidate = SdkCatalog.load(args.sdk_candidate_directory, args.sdk_archive)
    sdk_inventory = verify_sdk_material_inventory(
        _json(args.sdk_archive / "manifest.json")["files"],
        sdk_candidate.manifest, args.sdk_candidate_directory,
        sdk_verified["manifest_sha256"])
    visual_review = _json(args.visual_review_ledger) if args.visual_review_ledger else None
    if (visual_review is not None and
            (visual_review.get("format_version") != 1 or
             visual_review.get("product_archive_manifest_sha256") !=
             product_verified["manifest_sha256"])):
        raise ValueError("visual review archive or format mismatch")
    report = build_coverage(
        _json(args.product_archive / "manifest.json"),
        _json(args.sdk_archive / "manifest.json"),
        candidate["_sources/source-disposition.json"],
        candidate["_sources/asset-inventory.json"],
        candidate["_facts/all-records.json"],
        candidate["_sections/section-index.json"],
        product_view.records_for("internal_fae"),
        list(sdk_active.records),
        software_candidates=candidate["sdk/software-evidence-matrix.json"],
        link_candidates=candidate["links/candidate-link-ledger.json"]["links"],
        sdk_candidate_records=list(sdk_candidate.records),
        sdk_note_candidate_records=(list(sdk_candidate.release_notes.records)
                                    if sdk_candidate.release_notes else []),
        sdk_published_note_records=(list(sdk_active.release_notes.records)
                                    if sdk_active.release_notes else []),
        visual_candidates=visual_review["private_dev_candidates"] if visual_review else [],
    )
    report["archive_manifest_sha256"] = {
        "product": product_verified["manifest_sha256"],
        "sdk": sdk_verified["manifest_sha256"]}
    report["input_identity"] = {
        "candidate_summary_sha256": hashlib.sha256(
            (args.candidate_root / "summary.json").read_bytes()).hexdigest(),
        "product_release_id": product_active["release_id"],
        "sdk_release_id": sdk_active.release_id,
        "sdk_candidate_catalog_sha256": sdk_candidate.manifest["records_sha256"],
        "sdk_material_inventory_sha256": sdk_inventory["index_sha256"],
        "visual_review_ledger_sha256": hashlib.sha256(
            args.visual_review_ledger.read_bytes()).hexdigest()
            if args.visual_review_ledger else None,
        "runtime_release": args.runtime_release,
        "upstream_sha": args.upstream_sha,
        "release_scope": "local_active_copies; production_requires_independent_health_check",
    }
    if args.previous_report:
        report["delta_from_previous"] = compare_coverage_reports(
            _json(args.previous_report), report)
    private_root = Path(__file__).resolve().parents[1] / "data" / "knowledge" / "curated"
    write_private_report(args.output, report, private_root)
    print(json.dumps(report["summary"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
