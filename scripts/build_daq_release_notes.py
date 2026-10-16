"""Build the internal-FAE release-note statement catalog from verified originals."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from daq_fae.knowledge.release_notes import _serialized, build_release_notes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--directory", type=Path, default=Path("Knowledge_SDK"))
    args = parser.parse_args()
    records, manifest = build_release_notes(
        args.directory / "release_notes_curated.json", args.archive)
    (args.directory / "Release_Notes.jsonl").write_bytes(_serialized(records))
    (args.directory / "Release_Notes_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
