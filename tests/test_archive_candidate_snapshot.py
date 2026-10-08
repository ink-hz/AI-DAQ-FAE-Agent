"""Black-box checks for the offline, non-authoritative source archive."""

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from unittest.mock import patch

import pytest

from scripts.archive_candidate_snapshot import create_archive


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "archive_candidate_snapshot.py"


def run_archive(*args: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *(str(arg) for arg in args)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_create_records_and_verifies_original_bytes(tmp_path: Path) -> None:
    source = tmp_path / "source"
    (source / "产品 资料").mkdir(parents=True)
    (source / "产品 资料" / "规格.md").write_bytes("双目 EGO\n".encode())
    (source / "sample.bin").write_bytes(b"\x00\x01\xff")
    destination = tmp_path / "archive"

    created = run_archive("create", source, destination, "--source-date", "2026-09-20")
    assert created.returncode == 0, created.stderr
    summary = json.loads(created.stdout)
    assert summary["files"] == 2
    assert summary["bytes"] == len("双目 EGO\n".encode()) + 3
    assert len(summary["manifest_sha256"]) == 64

    manifest = json.loads((destination / "manifest.json").read_text())
    assert manifest["source_date"] == "2026-09-20"
    assert {entry["path"] for entry in manifest["files"]} == {
        "产品 资料/规格.md", "sample.bin",
    }
    assert all("modified_at_utc" in entry for entry in manifest["files"])
    assert stat.S_IMODE((destination / "manifest.json").stat().st_mode) == 0o400
    assert stat.S_IMODE(destination.stat().st_mode) == 0o500
    assert stat.S_IMODE((destination / "files").stat().st_mode) == 0o500
    assert stat.S_IMODE((destination / "files" / "sample.bin").stat().st_mode) == 0o400
    assert {
        entry["path"]: entry["sha256"] for entry in manifest["files"]
    } == {
        "产品 资料/规格.md": hashlib.sha256("双目 EGO\n".encode()).hexdigest(),
        "sample.bin": hashlib.sha256(b"\x00\x01\xff").hexdigest(),
    }
    verified = run_archive("verify", destination, "--expected-manifest-sha256", summary["manifest_sha256"])
    assert verified.returncode == 0, verified.stderr
    assert json.loads(verified.stdout) == summary


def test_verify_rejects_tamper_and_extra_file(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.txt").write_text("original")
    destination = tmp_path / "archive"
    created = run_archive("create", source, destination, "--source-date", "2026-09-20")
    assert created.returncode == 0
    expected = json.loads(created.stdout)["manifest_sha256"]

    archived = destination / "files" / "a.txt"
    os.chmod(archived, 0o600)
    archived.write_text("changed!")
    os.chmod(archived, 0o400)
    tampered = run_archive("verify", destination, "--expected-manifest-sha256", expected)
    assert tampered.returncode != 0
    assert "hash mismatch" in tampered.stderr

    os.chmod(archived, 0o600)
    archived.write_text("original")
    os.chmod(archived, 0o400)
    os.chmod(destination / "files", 0o700)
    extra_path = destination / "files" / "extra.txt"
    extra_path.write_text("extra")
    os.chmod(extra_path, 0o400)
    os.chmod(destination / "files", 0o500)
    extra = run_archive("verify", destination, "--expected-manifest-sha256", expected)
    assert extra.returncode != 0
    assert "unexpected" in extra.stderr


def test_create_rejects_source_link_and_existing_destination(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.txt").write_text("a")
    (source / "linked.txt").symlink_to(source / "a.txt")
    destination = tmp_path / "archive"
    result = run_archive("create", source, destination, "--source-date", "2026-09-20")
    assert result.returncode != 0
    assert "symlink" in result.stderr
    assert not destination.exists()

    (source / "linked.txt").unlink()
    destination.mkdir()
    result = run_archive("create", source, destination, "--source-date", "2026-09-20")
    assert result.returncode != 0
    assert "exists" in result.stderr
    assert not (destination / "manifest.json").exists()


def test_verify_rejects_link_in_archive(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.txt").write_text("a")
    destination = tmp_path / "archive"
    created = run_archive("create", source, destination, "--source-date", "2026-09-20")
    assert created.returncode == 0
    expected = json.loads(created.stdout)["manifest_sha256"]
    archived = destination / "files" / "a.txt"
    os.chmod(destination / "files", 0o700)
    archived.unlink()
    archived.symlink_to(source / "a.txt")
    os.chmod(destination / "files", 0o500)
    linked = run_archive("verify", destination, "--expected-manifest-sha256", expected)
    assert linked.returncode != 0
    assert "symlink" in linked.stderr


def test_verify_rejects_rewritten_file_and_manifest(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.txt").write_text("original")
    destination = tmp_path / "archive"
    created = run_archive("create", source, destination, "--source-date", "2026-09-20")
    assert created.returncode == 0
    expected = json.loads(created.stdout)["manifest_sha256"]

    archived = destination / "files" / "a.txt"
    os.chmod(archived, 0o600)
    archived.write_text("modified")
    os.chmod(archived, 0o400)
    manifest_path = destination / "manifest.json"
    os.chmod(manifest_path, 0o600)
    manifest = json.loads(manifest_path.read_text())
    manifest["files"][0]["sha256"] = hashlib.sha256(b"modified").hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    os.chmod(manifest_path, 0o400)

    result = run_archive("verify", destination, "--expected-manifest-sha256", expected)
    assert result.returncode != 0
    assert "manifest hash mismatch" in result.stderr


def test_verify_rejects_loosened_permissions(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.txt").write_text("original")
    destination = tmp_path / "archive"
    created = run_archive("create", source, destination, "--source-date", "2026-09-20")
    assert created.returncode == 0
    expected = json.loads(created.stdout)["manifest_sha256"]
    os.chmod(destination / "files" / "a.txt", 0o444)

    result = run_archive("verify", destination, "--expected-manifest-sha256", expected)
    assert result.returncode != 0
    assert "mode mismatch" in result.stderr


def test_create_rejects_unreadable_subtree(tmp_path: Path) -> None:
    source = tmp_path / "source"
    blocked = source / "blocked"
    blocked.mkdir(parents=True)
    (blocked / "secret.txt").write_text("candidate")
    destination = tmp_path / "archive"
    os.chmod(blocked, 0o000)
    try:
        result = run_archive("create", source, destination, "--source-date", "2026-09-20")
        assert result.returncode != 0
        assert "Permission denied" in result.stderr
        assert not destination.exists()
    finally:
        os.chmod(blocked, 0o700)


def test_create_rejects_destination_inside_source_before_staging(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "a.txt").write_text("candidate")
    destination = source / "archive"

    with patch("scripts.archive_candidate_snapshot.tempfile.mkdtemp", side_effect=AssertionError("stage created")):
        with pytest.raises(ValueError, match="inside source"):
            create_archive(source, destination, "2026-09-20")
    assert {entry.name for entry in source.iterdir()} == {"a.txt"}
