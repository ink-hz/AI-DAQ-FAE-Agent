from pathlib import Path

import fitz

from scripts.archive_candidate_snapshot import create_archive
from daq_fae.knowledge.source_import import compare_snapshots, import_archive


def _archive(tmp_path: Path, files: dict[str, bytes]) -> tuple[Path, str]:
    source = tmp_path / "source"
    source.mkdir(parents=True)
    for name, content in files.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    archive = tmp_path / "archive"
    result = create_archive(source, archive, "2026-10-08")
    return archive, result["manifest_sha256"]


def test_import_is_deterministic_and_keeps_exact_text_locations(tmp_path: Path):
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), "Hub power requirement")
    payload = pdf.tobytes()
    pdf.close()
    archive, digest = _archive(tmp_path, {
        "EGO/guide.md": b"# EGO\n\n## USB\nConnect cable.\n",
        "HUB/spec.pdf": payload,
        "SDK/fw.zip": b"not searchable",
    })

    first = import_archive(archive, digest)
    assert first == import_archive(archive, digest)
    assert {row["path"]: row["kind"] for row in first["sources"]} == {
        "EGO/guide.md": "markdown", "HUB/spec.pdf": "pdf", "SDK/fw.zip": "asset",
    }
    assert any(c["locator"] == {"kind": "lines", "start": 3, "end": 4}
               and "Connect cable." in c["text"] for c in first["chunks"])
    assert any(c["locator"] == {"kind": "page", "page": 1}
               and "Hub power requirement" in c["text"] for c in first["chunks"])
    assert all(c["source_path"] != "SDK/fw.zip" for c in first["chunks"])
    assert all(len(c["source_sha256"]) == 64 and len(c["chunk_id"]) == 64
               for c in first["chunks"])


def test_source_diff_reports_add_change_remove_without_time_heuristics(tmp_path: Path):
    archive_a, digest_a = _archive(tmp_path / "a", {
        "unchanged.md": b"same", "changed.md": b"old", "removed.md": b"gone",
    })
    archive_b, digest_b = _archive(tmp_path / "b", {
        "unchanged.md": b"same", "changed.md": b"new", "added.md": b"here",
    })
    before = import_archive(archive_a, digest_a)
    after = import_archive(archive_b, digest_b)

    assert compare_snapshots(before, after) == {
        "added": ["added.md"], "changed": ["changed.md"], "removed": ["removed.md"],
        "unchanged": ["unchanged.md"],
    }


def test_wrong_manifest_hash_rejects_import(tmp_path: Path):
    archive, _digest = _archive(tmp_path, {"guide.md": b"text"})
    try:
        import_archive(archive, "0" * 64)
    except ValueError as exc:
        assert "manifest hash mismatch" in str(exc)
    else:
        raise AssertionError("untrusted archive was imported")
