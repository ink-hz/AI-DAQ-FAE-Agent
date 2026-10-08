import json
from pathlib import Path

import pytest

from daq_fae.knowledge.releases import (
    activate_release, publish_release, read_active_release,
)


HASH = "a" * 64
LOCATOR = {"kind": "lines", "start": 1, "end": 2}
SNAPSHOT = {
    "archive_manifest_sha256": "f" * 64, "source_date": "2026-10-08",
    "sources": [{"path": "spec.md", "sha256": HASH, "size": 20, "kind": "markdown"}],
    "chunks": [{"source_path": "spec.md", "source_sha256": HASH,
                "locator": LOCATOR, "text": "source text"}],
}
REVIEW = {"reviewer": "release-owner", "reviewed_at": "2026-10-08",
          "dev_batch": "synthetic-k1"}


def _record(status="verified", value="1600x1200"):
    row = {
        "id": "claim:ego-resolution", "kind": "claim", "status": status,
        "scope": {"variant": "1600"},
        "source_refs": [{"path": "spec.md", "sha256": HASH, "locator": LOCATOR}],
        "data": {"entity_id": "entity:ego-1600", "field": "resolution",
                 "value": value, "unit": "px", "conditions": {}},
    }
    if status == "verified":
        row["fact_review"] = {"reviewer": "fae", "reviewed_at": "2026-10-08"}
        row["access_review"] = {"reviewer": "owner", "reviewed_at": "2026-10-08",
                                "view_roles": ["internal_fae"], "forward_roles": []}
    return row


def test_publish_is_immutable_deterministic_and_activation_is_explicit(tmp_path: Path):
    root = tmp_path / "releases"
    release_id = publish_release(root, SNAPSHOT, [_record()], None, REVIEW)
    assert len(release_id) == 64
    assert publish_release(root, SNAPSHOT, [_record()], None, REVIEW) == release_id
    assert read_active_release(root) is None
    manifest_path = root / "releases" / release_id / "manifest.json"
    assert manifest_path.stat().st_mode & 0o222 == 0
    manifest = json.loads(manifest_path.read_text())
    assert manifest["records"][0]["answerable"] is True
    assert manifest["source_count"] == 1
    activate_release(root, release_id)
    assert read_active_release(root)["release_id"] == release_id


def test_conflict_is_preserved_but_not_answerable(tmp_path: Path):
    row = _record(status="conflict")
    row["data"].pop("value")
    row["data"]["candidates"] = [
        {"value": 100, "source_ref": row["source_refs"][0]},
        {"value": 120, "source_ref": row["source_refs"][0]},
    ]
    release_id = publish_release(tmp_path, SNAPSHOT, [row], None, REVIEW)
    manifest = json.loads((tmp_path / "releases" / release_id / "manifest.json").read_text())
    assert manifest["records"][0]["answerable"] is False
    assert manifest["status_counts"]["conflict"] == 1


def test_stale_source_blocks_new_release_and_keeps_active_pointer(tmp_path: Path):
    first = publish_release(tmp_path, SNAPSHOT, [_record()], None, REVIEW)
    activate_release(tmp_path, first)
    new_snapshot = json.loads(json.dumps(SNAPSHOT))
    new_snapshot["sources"][0]["sha256"] = "b" * 64
    new_snapshot["chunks"][0]["source_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="source_hash_mismatch"):
        publish_release(tmp_path, new_snapshot, [_record()], first, REVIEW)
    assert read_active_release(tmp_path)["release_id"] == first


def test_rollback_switches_to_an_existing_immutable_release(tmp_path: Path):
    first = publish_release(tmp_path, SNAPSHOT, [_record()], None, REVIEW)
    second = publish_release(tmp_path, SNAPSHOT, [_record(value="1920x1200")], first, REVIEW)
    activate_release(tmp_path, second)
    assert read_active_release(tmp_path)["release_id"] == second
    activate_release(tmp_path, first)
    assert read_active_release(tmp_path)["release_id"] == first
    assert (tmp_path / "releases" / second / "manifest.json").exists()
