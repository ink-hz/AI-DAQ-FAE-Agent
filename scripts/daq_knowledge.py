#!/usr/bin/env python3
"""Offline DAQ candidate import, impact inspection, and local release control."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from daq_fae.knowledge.records import (  # noqa: E402
    access_fingerprint, impact_report, record_fingerprint,
)
from daq_fae.knowledge.releases import (  # noqa: E402
    activate_release, publish_release, read_active_release,
)
from daq_fae.knowledge.review_packets import (  # noqa: E402
    build_review_packet, compare_review_packets,
)
from daq_fae.knowledge.source_import import (  # noqa: E402
    compare_snapshots, import_archive,
)


def _read_json(path: Path) -> object:
    if path.is_symlink() or not path.is_file():
        raise ValueError("JSON input missing or linked")
    return json.loads(path.read_text(encoding="utf-8"))


def _require_ignored_location(path: Path) -> None:
    target = path.resolve()
    existing = target
    while not existing.exists():
        existing = existing.parent
    repository = subprocess.run(
        ["git", "-C", str(existing), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=False,
    )
    if repository.returncode != 0:
        return
    repo_root = Path(repository.stdout.strip()).resolve()
    relative = target.relative_to(repo_root)
    ignored = subprocess.run(
        ["git", "-C", str(repo_root), "check-ignore", "-q", "--", str(relative)],
        capture_output=True, check=False,
    )
    if ignored.returncode != 0:
        raise ValueError("private knowledge output inside repository must be Git-ignored")


def _private_write(path: Path, value: object) -> None:
    _require_ignored_location(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("candidate output must not be a symlink")
    data = (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if path.read_bytes() != data:
            raise ValueError("candidate snapshot already exists with different content") from None
        if stat.S_IMODE(path.stat().st_mode) != 0o600:
            raise ValueError("candidate snapshot mode mismatch") from None
        return
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    importer = commands.add_parser("import")
    importer.add_argument("--archive", type=Path, required=True)
    importer.add_argument("--manifest-sha256", required=True)
    importer.add_argument("--output", type=Path, required=True)
    difference = commands.add_parser("diff")
    difference.add_argument("--previous", type=Path, required=True)
    difference.add_argument("--current", type=Path, required=True)
    difference.add_argument("--records", type=Path)
    fingerprint = commands.add_parser("fingerprint")
    fingerprint.add_argument("--records", type=Path, required=True)
    review_packet = commands.add_parser("review-packet")
    review_packet.add_argument("--archive", type=Path, required=True)
    review_packet.add_argument("--manifest-sha256", required=True)
    review_packet.add_argument("--snapshot", type=Path, required=True)
    review_packet.add_argument("--recipe", type=Path, required=True)
    review_packet.add_argument("--output", type=Path, required=True)
    review_diff = commands.add_parser("review-diff")
    review_diff.add_argument("--previous", type=Path, required=True)
    review_diff.add_argument("--current", type=Path, required=True)
    publish = commands.add_parser("publish")
    publish.add_argument("--root", type=Path, required=True)
    publish.add_argument("--snapshot", type=Path, required=True)
    publish.add_argument("--archive", type=Path, required=True)
    publish.add_argument("--manifest-sha256", required=True)
    publish.add_argument("--records", type=Path, required=True)
    publish.add_argument("--review", type=Path, required=True)
    publish.add_argument("--previous-release")
    for name in ("activate", "rollback"):
        action = commands.add_parser(name)
        action.add_argument("--root", type=Path, required=True)
        action.add_argument("--release-id", required=True)
    active = commands.add_parser("active")
    active.add_argument("--root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "import":
            snapshot = import_archive(args.archive, args.manifest_sha256)
            _private_write(args.output, snapshot)
            result = {"archive_manifest_sha256": snapshot["archive_manifest_sha256"],
                      "sources": len(snapshot["sources"]), "chunks": len(snapshot["chunks"]),
                      "extraction_status_counts": {
                          status: sum(row["extraction_status"] == status
                                      for row in snapshot["sources"])
                          for status in sorted({row["extraction_status"]
                                                for row in snapshot["sources"]})}}
        elif args.command == "diff":
            change = compare_snapshots(_read_json(args.previous), _read_json(args.current))
            result = {"sources": change}
            if args.records:
                result["records"] = impact_report(_read_json(args.records), change)
        elif args.command == "review-packet":
            snapshot = _read_json(args.snapshot)
            verified_snapshot = import_archive(args.archive, args.manifest_sha256)
            if snapshot != verified_snapshot:
                raise ValueError("candidate snapshot differs from verified archive")
            packet = build_review_packet(snapshot, _read_json(args.recipe))
            _private_write(args.output, packet)
            result = {
                "archive_manifest_sha256": packet["archive_manifest_sha256"],
                "recipe_sha256": packet["recipe_sha256"],
                "cases": len(packet["cases"]),
                "missing_selectors": sum(len(case["missing_selectors"])
                                         for case in packet["cases"]),
                "unmapped_sources": len(packet["unmapped_sources"]),
                "claimed_classification_counts": {
                    label: sum(row["claimed_classification"] == label
                               for row in packet["source_markings"].values())
                    for label in sorted({row["claimed_classification"]
                                         for row in packet["source_markings"].values()})},
            }
        elif args.command == "review-diff":
            result = compare_review_packets(_read_json(args.previous),
                                            _read_json(args.current))
        elif args.command == "fingerprint":
            rows = _read_json(args.records)
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ValueError("records must be a list of objects")
            ids = [row.get("id") for row in rows]
            if any(not isinstance(record_id, str) for record_id in ids) or len(set(ids)) != len(ids):
                raise ValueError("record IDs missing or duplicated")
            result = {row["id"]: {
                "fact_review": record_fingerprint(row),
                "access_review": access_fingerprint(row),
                **({"link_review": record_fingerprint(row)} if row.get("kind") == "link" else {}),
            } for row in rows}
        elif args.command == "publish":
            _require_ignored_location(args.root)
            snapshot = _read_json(args.snapshot)
            verified_snapshot = import_archive(args.archive, args.manifest_sha256)
            if snapshot != verified_snapshot:
                raise ValueError("candidate snapshot differs from verified archive")
            release_id = publish_release(args.root, snapshot, _read_json(args.records),
                                         args.previous_release, _read_json(args.review))
            result = {"release_id": release_id, "activated": False}
        elif args.command in {"activate", "rollback"}:
            activate_release(args.root, args.release_id)
            result = {"release_id": args.release_id, "active": True}
        else:
            current = read_active_release(args.root)
            result = {"release_id": current["release_id"] if current else None}
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"knowledge error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
