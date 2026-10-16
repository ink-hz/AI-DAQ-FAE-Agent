"""Independent immutable SDK usage releases and atomic rollback pointer."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from .sdk_catalog import SdkCatalog
from .release_notes import ReleaseNoteCatalog, _serialized as _serialized_notes
from .code_identity import daq_code_sha


_SHA = re.compile(r"[0-9a-f]{64}\Z")
_UPSTREAM = re.compile(r"[0-9a-f]{40}\Z")


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode()


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _root(root: Path) -> Path:
    if root.is_symlink():
        raise ValueError("SDK release root linked")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    releases = root / "releases"
    if releases.is_symlink():
        raise ValueError("SDK releases directory linked")
    releases.mkdir(exist_ok=True, mode=0o700)
    return releases


def _read(root: Path, release_id: str) -> dict:
    if not isinstance(release_id, str) or not _SHA.fullmatch(release_id):
        raise ValueError("SDK release ID invalid")
    directory = root / "releases" / release_id
    path = directory / "manifest.json"
    if directory.is_symlink() or not directory.is_dir() or path.is_symlink() or not path.is_file():
        raise ValueError("SDK release missing or linked")
    raw = path.read_bytes()
    if _digest(raw) != release_id:
        raise ValueError("SDK release digest mismatch")
    manifest = json.loads(raw)
    if (manifest.get("format_version") != "daq-sdk-published/v1"
            or not _SHA.fullmatch(str(manifest.get("archive_manifest_sha256", "")))
            or not _SHA.fullmatch(str(manifest.get("catalog_sha256", "")))
            or not isinstance(manifest.get("records"), list)
            or manifest.get("record_count") != len(manifest["records"])
            or not _UPSTREAM.fullmatch(str(manifest.get("upstream_sha", "")))
            or not isinstance(manifest.get("runtime_release"), str)
            or not manifest["runtime_release"]):
        raise ValueError("SDK release manifest invalid")
    from .sdk_catalog import _serialized
    if _digest(_serialized(manifest["records"])) != manifest["catalog_sha256"]:
        raise ValueError("SDK release catalog digest mismatch")
    ids = set()
    for row in manifest["records"]:
        if (not isinstance(row, dict) or not isinstance(row.get("kb_id"), str)
                or row["kb_id"] in ids or row.get("view_roles") != ["internal_fae"]
                or not isinstance(row.get("source_refs"), list) or not row["source_refs"]):
            raise ValueError("SDK release record invalid")
        ids.add(row["kb_id"])
        for ref in row["source_refs"]:
            if (not isinstance(ref, dict) or not _SHA.fullmatch(str(ref.get("package_sha256", "")))
                    or not _SHA.fullmatch(str(ref.get("member_sha256", "")))
                    or not isinstance(ref.get("member"), str)
                    or type(ref.get("start_line")) is not int or type(ref.get("end_line")) is not int
                    or not 1 <= ref["start_line"] <= ref["end_line"]):
                raise ValueError("SDK release source reference invalid")
    has_notes = "release_notes" in manifest or "release_notes_sha256" in manifest
    if has_notes:
        notes = manifest.get("release_notes")
        notes_sha = manifest.get("release_notes_sha256")
        if (not isinstance(notes, list) or not _SHA.fullmatch(str(notes_sha or ""))
                or not _SHA.fullmatch(str(manifest.get("daq_code_sha256", "")))
                or not _SHA.fullmatch(str(manifest.get("knowledge_release", "")))
                or _digest(_serialized_notes(notes)) != notes_sha):
            raise ValueError("SDK release-note digest mismatch")
        note_ids = set()
        note_hashes = set()
        for row in notes:
            if (not isinstance(row, dict) or not isinstance(row.get("kb_id"), str)
                    or row["kb_id"] in note_ids
                    or row.get("doc_type") != "release_note"
                    or row.get("evidence_scope") != "release_note_statement_only"
                    or row.get("device_support") != "unknown"
                    or row.get("view_roles") != ["internal_fae"]
                    or not _SHA.fullmatch(str(row.get("file_sha256", "")))
                    or row["file_sha256"] in note_hashes
                    or not isinstance(row.get("source_refs"), list)
                    or not row["source_refs"]):
                raise ValueError("SDK release-note row invalid")
            note_ids.add(row["kb_id"])
            note_hashes.add(row["file_sha256"])
            for ref in row["source_refs"]:
                if (not isinstance(ref, dict)
                        or ref.get("package_sha256") != row["file_sha256"]
                        or ref.get("member_sha256") != row["file_sha256"]
                        or not _SHA.fullmatch(str(ref.get("span_sha256", "")))
                        or type(ref.get("start_line")) is not int
                        or type(ref.get("end_line")) is not int
                        or not 1 <= ref["start_line"] <= ref["end_line"]):
                    raise ValueError("SDK release-note source reference invalid")
    return manifest


def stage_sdk_release(root: Path, directory: Path, archive: Path, review_report: dict, *,
                      replay_results: Path, runtime_release: str, upstream_sha: str) -> str:
    """Source verification and independent Dev answer review precede staging."""
    catalog = SdkCatalog.load(directory, archive)
    release_notes = catalog.release_notes
    release_notes_sha = (release_notes.manifest["records_sha256"]
                         if release_notes is not None else None)
    replay_raw = replay_results.read_bytes()
    replay_rows = [json.loads(line) for line in replay_raw.splitlines() if line]
    replay_by_id = {row.get("case", {}).get("id"): row for row in replay_rows}
    report_cases = review_report.get("cases") if isinstance(review_report, dict) else None
    if (not isinstance(review_report, dict)
            or review_report.get("environment") != "dev"
            or review_report.get("reviewer") not in {"Codex", "human_fae"}
            or review_report.get("catalog_sha256") != catalog.manifest["records_sha256"]
            or (release_notes is not None
                and review_report.get("release_notes_sha256") != release_notes_sha)
            or (release_notes is not None and (
                not _SHA.fullmatch(str(review_report.get("daq_code_sha256", "")))
                or not _SHA.fullmatch(str(review_report.get("knowledge_release", "")))))
            or review_report.get("results_sha256") != _digest(replay_raw)
            or not _SHA.fullmatch(str(review_report.get("suite_sha256", "")))
            or not isinstance(report_cases, list) or not report_cases
            or len(replay_rows) != len(report_cases)
            or len(replay_by_id) != len(replay_rows)
            or {case.get("id") for case in report_cases if isinstance(case, dict)}
               != set(replay_by_id)):
        raise ValueError("SDK Dev answer review missing or failed")
    if release_notes is not None:
        reviewed = review_report.get("record_reviews")
        expected = {row["kb_id"]: _digest(_json_bytes(row))
                    for row in (*catalog.records, *release_notes.records)}
        if (not isinstance(reviewed, list) or len(reviewed) != len(expected)
                or any(not isinstance(item, dict) for item in reviewed)
                or {item.get("kb_id") for item in reviewed} != set(expected)
                or any(item.get("record_sha256") != expected[item["kb_id"]]
                       or item.get("reviewer") not in {"Codex", "human_fae"}
                       or item.get("passed") is not True
                       or item.get("source_span_checked") is not True
                       or not isinstance(item.get("review_note"), str)
                       or not item["review_note"].strip()
                       for item in reviewed)):
            raise ValueError("SDK independent record review missing or failed")
    for case in report_cases:
        row = replay_by_id[case["id"]]
        turns = row.get("turns")
        if (not isinstance(turns, list) or len(turns) != 1
                or not isinstance(row.get("identity"), dict)
                or row["identity"].get("sdk_catalog_sha256") != catalog.manifest["records_sha256"]
                or (release_notes is not None
                    and row["identity"].get("release_notes_sha256") != release_notes_sha)
                or (release_notes is not None and (
                    row["identity"].get("daq_code_sha256") != review_report["daq_code_sha256"]
                    or row["identity"].get("knowledge_release") != review_report["knowledge_release"]))
                or row["identity"].get("suite_file_sha256") != review_report["suite_sha256"]):
            raise ValueError("SDK Dev replay identity mismatch")
        if (row["identity"].get("runtime_release") != runtime_release
                or row["identity"].get("upstream_revision") != upstream_sha):
            raise ValueError("SDK Dev replay runtime identity mismatch")
        turn = turns[0]
        done = turn.get("done") if isinstance(turn, dict) else None
        integrity = turn.get("integrity") if isinstance(turn, dict) else None
        if (not isinstance(done, dict) or not isinstance(integrity, dict)
                or not integrity or not all(value is True for value in integrity.values())
                or case.get("passed") is not True
                or not isinstance(case.get("review_note"), str)
                or not case["review_note"].strip()
                or case.get("fallback_used") is not False
                or done.get("fallback_used") is not False
                or case.get("outcome") != done.get("outcome")
                or done.get("outcome") not in {"resolved", "safe_abstained"}
                or case.get("trace_id") != done.get("trace_id")
                or not re.fullmatch(r"[0-9a-f]{32}", str(done.get("trace_id", "")))
                or case.get("answer_sha256") != _digest(str(done.get("answer", "")).encode())):
            raise ValueError("SDK Dev answer review missing or failed")
    if not runtime_release or not _UPSTREAM.fullmatch(upstream_sha):
        raise ValueError("SDK runtime identity invalid")
    manifest = {"format_version": "daq-sdk-published/v1",
                "archive_manifest_sha256": catalog.manifest["archive_manifest_sha256"],
                "catalog_sha256": catalog.manifest["records_sha256"],
                "record_count": len(catalog.records), "records": list(catalog.records),
                "runtime_release": runtime_release, "upstream_sha": upstream_sha,
                "dev_answer_review": review_report}
    if release_notes is not None:
        note_ids = {row["kb_id"] for row in release_notes.records}
        if not any(
            source.get("source_id") in note_ids
            for row in replay_rows for turn in row["turns"]
            for source in turn["done"].get("sources", [])
            if isinstance(source, dict)
        ):
            raise ValueError("SDK Dev release-note answer review missing")
        manifest["release_notes_sha256"] = release_notes_sha
        manifest["release_notes"] = list(release_notes.records)
        manifest["daq_code_sha256"] = review_report["daq_code_sha256"]
        manifest["knowledge_release"] = review_report["knowledge_release"]
    raw = _json_bytes(manifest)
    release_id = _digest(raw)
    releases = _root(root)
    target = releases / release_id
    if target.exists():
        if _read(root, release_id) != manifest:
            raise ValueError("SDK release ID collision")
        return release_id
    stage = Path(tempfile.mkdtemp(prefix=".sdk-stage-", dir=releases))
    try:
        path = stage / "manifest.json"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        stage.chmod(0o500)
        os.replace(stage, target)
    finally:
        if stage.exists():
            stage.chmod(0o700)
            for path in stage.iterdir():
                path.chmod(0o600)
                path.unlink()
            stage.rmdir()
    return release_id


def activate_sdk_release(root: Path, release_id: str) -> None:
    _read(root, release_id)
    payload = _json_bytes({"release_id": release_id})
    descriptor, name = tempfile.mkstemp(prefix=".sdk-active-", dir=root)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, 0o600)
        os.replace(name, root / "active.json")
    finally:
        if os.path.exists(name):
            os.unlink(name)


def load_active_sdk_release(root: Path, *, runtime_release: str,
                            upstream_sha: str, daq_code_sha256: str | None = None,
                            knowledge_release: str | None = None) -> SdkCatalog | None:
    if root.is_symlink():
        raise ValueError("SDK release root linked")
    active = root / "active.json"
    if not active.exists():
        return None
    if active.is_symlink():
        raise ValueError("SDK active pointer linked")
    pointer = json.loads(active.read_text())
    if not isinstance(pointer, dict) or set(pointer) != {"release_id"}:
        raise ValueError("SDK active pointer invalid")
    release_id = pointer["release_id"]
    manifest = _read(root, release_id)
    if (manifest["runtime_release"] != runtime_release
            or manifest["upstream_sha"] != upstream_sha):
        raise ValueError("SDK release runtime identity mismatch")
    if "release_notes" in manifest and (
            daq_code_sha256 != daq_code_sha()
            or daq_code_sha256 != manifest["daq_code_sha256"]
            or knowledge_release != manifest["knowledge_release"]):
        raise ValueError("SDK release code or main knowledge identity mismatch")
    notes = (ReleaseNoteCatalog(manifest["release_notes"], {
        "records_sha256": manifest["release_notes_sha256"]})
        if "release_notes" in manifest else None)
    catalog = SdkCatalog(manifest["records"], {
        "records_sha256": manifest["catalog_sha256"],
        "archive_manifest_sha256": manifest["archive_manifest_sha256"],
    }, notes)
    catalog.published = True
    catalog.release_id = release_id
    return catalog
