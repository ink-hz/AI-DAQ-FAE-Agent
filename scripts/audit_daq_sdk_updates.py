"""Compare EGO source members across local host SDK drops without extraction."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import tarfile

from scripts.archive_candidate_snapshot import verify_archive


def _members(path: Path) -> dict[str, str]:
    result = {}
    with tarfile.open(path) as bundle:
        for member in bundle:
            if not member.isfile():
                continue
            match = re.search(r"/examples/src/(6\.ego|7\.net\.ego_depth)/(.+)$", member.name)
            if not match:
                continue
            stream = bundle.extractfile(member)
            if stream is None:
                raise ValueError("SDK source member cannot be read")
            result[f"{match.group(1)}/{match.group(2)}"] = hashlib.sha256(stream.read()).hexdigest()
    return result


def audit(archive: Path) -> dict:
    verified = verify_archive(archive)
    packages = sorted((archive / "files" / "SDK").glob(
        "SDK&OrbbecViewer_v2.9.0_*/linux/OrbbecSDK*_linux_x86_64.tar.gz"))
    if len(packages) < 2:
        raise ValueError("at least two SDK drops required")
    builds = []
    previous = None
    for path in packages:
        revision = re.search(r"SDK&OrbbecViewer_v2\.9\.0_(\d{8})", str(path))
        if revision is None:
            raise ValueError("SDK drop date missing")
        current = _members(path)
        change = {"added": sorted(current.keys() - previous.keys()) if previous else [],
                  "removed": sorted(previous.keys() - current.keys()) if previous else [],
                  "changed": sorted(name for name in current.keys() & previous.keys()
                                    if current[name] != previous[name]) if previous else []}
        builds.append({"drop": revision.group(1),
                       "package_path": path.relative_to(archive / "files").as_posix(),
                       "member_count": len(current), "members": current,
                       "change_from_previous": change})
        previous = current
    return {"format_version": "daq-sdk-update-audit/v1",
            "archive_manifest_sha256": verified["manifest_sha256"], "builds": builds}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.archive)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    for build in report["builds"]:
        change = build["change_from_previous"]
        print(build["drop"], build["member_count"],
              "+", len(change["added"]), "-", len(change["removed"]),
              "~", len(change["changed"]))


if __name__ == "__main__":
    main()
