import json
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

from scripts.archive_candidate_snapshot import create_archive


ROOT = Path(__file__).resolve().parents[1]


def _cli(*args: str) -> dict:
    result = subprocess.run([sys.executable, "scripts/daq_knowledge.py", *args],
                            cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _archive(tmp_path: Path, text: bytes) -> tuple[Path, str]:
    source = tmp_path / "source"
    source.mkdir(parents=True)
    (source / "spec.md").write_bytes(text)
    archive = tmp_path / "archive"
    info = create_archive(source, archive, "2026-10-08")
    return archive, info["manifest_sha256"]


def test_cli_import_diff_publish_activate_and_rollback(tmp_path: Path):
    first_archive, first_hash = _archive(tmp_path / "a", b"# EGO\n1600\n")
    second_archive, second_hash = _archive(tmp_path / "b", b"# EGO\n1920\n")
    first_path = tmp_path / "first.json"
    second_path = tmp_path / "second.json"
    imported = _cli("import", "--archive", str(first_archive),
                    "--manifest-sha256", first_hash, "--output", str(first_path))
    assert imported["sources"] == 1 and imported["chunks"] == 1
    assert first_path.stat().st_mode & 0o077 == 0
    assert _cli("import", "--archive", str(first_archive),
                "--manifest-sha256", first_hash, "--output", str(first_path)) == imported
    _cli("import", "--archive", str(second_archive),
         "--manifest-sha256", second_hash, "--output", str(second_path))
    diff = _cli("diff", "--previous", str(first_path), "--current", str(second_path))
    assert diff["sources"]["changed"] == ["spec.md"]
    snapshot = json.loads(first_path.read_text())
    reference = {"path": "spec.md", "sha256": snapshot["sources"][0]["sha256"],
                 "locator": snapshot["chunks"][0]["locator"]}
    records = [{"id": "claim:ego-resolution", "kind": "claim", "status": "verified",
                "scope": {"variant": "1600"}, "source_refs": [reference],
                "data": {"entity_id": "entity:ego-1600", "field": "resolution",
                         "value": "1600", "unit": "px", "conditions": {}},
                "fact_review": {"reviewer": "fae", "reviewed_at": "2026-10-08"},
                "access_review": {"reviewer": "owner", "reviewed_at": "2026-10-08",
                                  "view_roles": ["internal_fae"], "forward_roles": []}},
               {"id": "entity:ego-1600", "kind": "entity", "status": "verified",
                "scope": {"variant": "1600"}, "source_refs": [reference],
                "data": {"name": "EGO 1600", "entity_type": "variant"},
                "fact_review": {"reviewer": "fae", "reviewed_at": "2026-10-08"},
                "access_review": {"reviewer": "owner", "reviewed_at": "2026-10-08",
                                  "view_roles": ["internal_fae"], "forward_roles": []}}]
    expected_fingerprints = {}
    for row in records:
        core = {key: row[key] for key in
                ("id", "kind", "status", "scope", "source_refs", "data")}
        fingerprint = hashlib.sha256(json.dumps(core, ensure_ascii=False, sort_keys=True,
                                                separators=(",", ":")).encode()).hexdigest()
        access_payload = {"record_sha256": fingerprint, "view_roles": ["internal_fae"],
                          "forward_roles": []}
        access_fingerprint = hashlib.sha256(json.dumps(
            access_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest()
        row["fact_review"]["record_sha256"] = fingerprint
        row["access_review"]["record_sha256"] = access_fingerprint
        expected_fingerprints[row["id"]] = {
            "fact_review": fingerprint, "access_review": access_fingerprint,
        }
    records_path = tmp_path / "records.json"
    records_path.write_text(json.dumps(records))
    fingerprints = _cli("fingerprint", "--records", str(records_path))
    assert fingerprints == expected_fingerprints
    review_path = tmp_path / "review.json"
    review_path.write_text(json.dumps({"reviewer": "owner", "reviewed_at": "2026-10-08",
                                       "dev_batch": "synthetic-cli"}))
    release_root = tmp_path / "releases-root"
    release = _cli("publish", "--root", str(release_root), "--snapshot", str(first_path),
                   "--archive", str(first_archive), "--manifest-sha256", first_hash,
                   "--records", str(records_path), "--review", str(review_path))["release_id"]
    assert _cli("active", "--root", str(release_root))["release_id"] is None
    _cli("activate", "--root", str(release_root), "--release-id", release)
    assert _cli("active", "--root", str(release_root))["release_id"] == release
    _cli("rollback", "--root", str(release_root), "--release-id", release)
    assert _cli("active", "--root", str(release_root))["release_id"] == release


def test_cli_publish_rechecks_archive_against_saved_candidate(tmp_path: Path):
    archive, digest = _archive(tmp_path, b"# EGO\n1600\n")
    snapshot_path = tmp_path / "snapshot.json"
    _cli("import", "--archive", str(archive), "--manifest-sha256", digest,
         "--output", str(snapshot_path))
    snapshot = json.loads(snapshot_path.read_text())
    snapshot["chunks"][0]["text"] = "invented"
    snapshot_path.write_text(json.dumps(snapshot))
    records_path = tmp_path / "records.json"
    records_path.write_text("[]")
    review_path = tmp_path / "review.json"
    review_path.write_text(json.dumps({"reviewer": "owner", "reviewed_at": "2026-10-08",
                                       "dev_batch": "synthetic-cli"}))
    result = subprocess.run([
        sys.executable, "scripts/daq_knowledge.py", "publish", "--root", str(tmp_path / "releases"),
        "--snapshot", str(snapshot_path), "--archive", str(archive),
        "--manifest-sha256", digest, "--records", str(records_path),
        "--review", str(review_path),
    ], cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 2
    assert "differs from verified archive" in result.stderr
    assert not (tmp_path / "releases").exists()


def test_cli_rejects_candidate_output_in_tracked_repo_path(tmp_path: Path):
    archive, digest = _archive(tmp_path, b"# EGO\n1600\n")
    output = ROOT / "knowledge" / "forbidden-candidate-test.json"
    try:
        result = subprocess.run([
            sys.executable, "scripts/daq_knowledge.py", "import",
            "--archive", str(archive), "--manifest-sha256", digest,
            "--output", str(output),
        ], cwd=ROOT, text=True, capture_output=True)
        assert result.returncode == 2
        assert "Git-ignored" in result.stderr
    finally:
        output.unlink(missing_ok=True)


def test_cli_rejects_release_root_in_tracked_repo_path(tmp_path: Path):
    archive, digest = _archive(tmp_path, b"# EGO\n1600\n")
    snapshot_path = tmp_path / "snapshot.json"
    _cli("import", "--archive", str(archive), "--manifest-sha256", digest,
         "--output", str(snapshot_path))
    records_path = tmp_path / "records.json"
    records_path.write_text("[]")
    review_path = tmp_path / "review.json"
    review_path.write_text(json.dumps({"reviewer": "owner", "reviewed_at": "2026-10-08",
                                       "dev_batch": "synthetic-cli"}))
    release_root = ROOT / "knowledge" / "forbidden-release-test"
    try:
        result = subprocess.run([
            sys.executable, "scripts/daq_knowledge.py", "publish",
            "--root", str(release_root), "--snapshot", str(snapshot_path),
            "--archive", str(archive), "--manifest-sha256", digest,
            "--records", str(records_path), "--review", str(review_path),
        ], cwd=ROOT, text=True, capture_output=True)
        assert result.returncode == 2
        assert "Git-ignored" in result.stderr
    finally:
        if release_root.exists():
            for directory, dirs, files in os.walk(release_root):
                for name in files:
                    os.chmod(Path(directory) / name, 0o600)
                for name in dirs:
                    os.chmod(Path(directory) / name, 0o700)
            shutil.rmtree(release_root)


def test_cli_review_packet_rechecks_archive_and_writes_private_candidate(tmp_path: Path):
    archive, digest = _archive(tmp_path, b"# EGO\nBaseline 100 mm\n")
    snapshot_path = tmp_path / "snapshot.json"
    _cli("import", "--archive", str(archive), "--manifest-sha256", digest,
         "--output", str(snapshot_path))
    recipe = {"version": "test-1", "cases": [{
        "id": "baseline", "group": "conflict", "question": "Which baseline?",
        "owner": "product_rd", "selectors": [{"source_glob": "*.md",
                                              "pattern": "Baseline 100 mm"}],
    }]}
    recipe_path = tmp_path / "recipe.json"
    recipe_path.write_text(json.dumps(recipe))
    output = tmp_path / "packet.json"
    result = _cli("review-packet", "--archive", str(archive),
                  "--manifest-sha256", digest, "--snapshot", str(snapshot_path),
                  "--recipe", str(recipe_path), "--output", str(output))
    assert result["cases"] == 1 and result["missing_selectors"] == 0
    assert output.stat().st_mode & 0o077 == 0
    assert json.loads(output.read_text())["cases"][0]["status"] == "pending"
    assert _cli("review-packet", "--archive", str(archive),
                "--manifest-sha256", digest, "--snapshot", str(snapshot_path),
                "--recipe", str(recipe_path), "--output", str(output)) == result
    diff = _cli("review-diff", "--previous", str(output), "--current", str(output))
    assert diff["cases"] == {"added": [], "changed": [], "removed": []}
    snapshot = json.loads(snapshot_path.read_text())
    snapshot["chunks"][0]["text"] = "tampered"
    snapshot_path.write_text(json.dumps(snapshot))
    failed = subprocess.run([sys.executable, "scripts/daq_knowledge.py", "review-packet",
                             "--archive", str(archive), "--manifest-sha256", digest,
                             "--snapshot", str(snapshot_path), "--recipe", str(recipe_path),
                             "--output", str(tmp_path / "invalid.json")], cwd=ROOT,
                            text=True, capture_output=True)
    assert failed.returncode == 2
    assert "differs from verified archive" in failed.stderr
    assert not (tmp_path / "invalid.json").exists()
