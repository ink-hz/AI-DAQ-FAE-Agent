"""Build or inspect the offline, non-authoritative 1015 SDK asset index."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from daq_fae.knowledge.sdk_materials import build_inventory, search_inventory
from scripts.archive_candidate_snapshot import verify_archive


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--directory", type=Path, default=Path("Knowledge_SDK"))
    parser.add_argument("--query", help="Read-only artifact path lookup; no compatibility result")
    args = parser.parse_args()
    verified = verify_archive(args.archive)
    original = json.loads((args.archive / "manifest.json").read_text())
    sdk_manifest = json.loads((args.directory / "SDK_Knowledge_manifest.json").read_text())
    packages = {row["archive_path"] for row in sdk_manifest["packages"].values()}
    rows = build_inventory(original["files"], source_checked_packages=packages)
    payload = b"".join((json.dumps(row, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":")) + "\n").encode() for row in rows)
    digest = hashlib.sha256(payload).hexdigest()
    summary = {"format_version": "daq-sdk-material-inventory/v1",
               "archive_manifest_sha256": verified["manifest_sha256"],
               "index_sha256": digest, "original_count": len(rows),
               "unique_file_hashes": len({row["sha256"] for row in rows}),
               "source_checked_packages": len(packages),
               "device_support": "unknown"}
    if args.query is not None:
        stored = (args.directory / "material_inventory.jsonl").read_bytes()
        if stored != payload or json.loads((args.directory / "material_inventory_manifest.json").read_text()) != summary:
            raise ValueError("SDK material inventory differs from verified archive")
        for row in search_inventory(rows, args.query):
            print(json.dumps(row, ensure_ascii=False, sort_keys=True))
        return
    (args.directory / "material_inventory.jsonl").write_bytes(payload)
    (args.directory / "material_inventory_manifest.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
