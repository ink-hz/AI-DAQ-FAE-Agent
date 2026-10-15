"""Stage, activate or inspect an independent DAQ SDK knowledge release."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from daq_fae.knowledge.sdk_release import (
    activate_sdk_release, load_active_sdk_release, stage_sdk_release,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    stage = sub.add_parser("stage")
    stage.add_argument("--root", type=Path, required=True)
    stage.add_argument("--catalog-dir", type=Path, required=True)
    stage.add_argument("--archive", type=Path, required=True)
    stage.add_argument("--review-report", type=Path, required=True)
    stage.add_argument("--replay-results", type=Path, required=True)
    stage.add_argument("--runtime-release", required=True)
    stage.add_argument("--upstream-sha", required=True)
    activate = sub.add_parser("activate")
    activate.add_argument("--root", type=Path, required=True)
    activate.add_argument("--release-id", required=True)
    current = sub.add_parser("current")
    current.add_argument("--root", type=Path, required=True)
    current.add_argument("--runtime-release", required=True)
    current.add_argument("--upstream-sha", required=True)
    args = parser.parse_args()
    if args.command == "stage":
        report = json.loads(args.review_report.read_text())
        release = stage_sdk_release(args.root, args.catalog_dir, args.archive, report,
                                    replay_results=args.replay_results,
                                    runtime_release=args.runtime_release,
                                    upstream_sha=args.upstream_sha)
        print(release)
    elif args.command == "activate":
        activate_sdk_release(args.root, args.release_id)
        print(args.release_id)
    else:
        catalog = load_active_sdk_release(args.root,
                                          runtime_release=args.runtime_release,
                                          upstream_sha=args.upstream_sha)
        print(json.dumps({"release_id": catalog.release_id if catalog else None,
                          "record_count": len(catalog.records) if catalog else 0}))


if __name__ == "__main__":
    main()
