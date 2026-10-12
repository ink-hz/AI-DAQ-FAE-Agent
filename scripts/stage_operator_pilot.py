"""Stage the exact source-checked internal pilot from private Dev evidence."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from daq_fae.knowledge.operator_release import EXCLUDED, stage_operator_release
from daq_fae.knowledge.releases import read_active_release


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--proposal', type=Path, required=True)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--aliases', type=Path, required=True)
    parser.add_argument('--dev-report', type=Path, required=True)
    parser.add_argument('--release-root', type=Path, required=True)
    parser.add_argument('--runtime-release', required=True)
    parser.add_argument('--upstream-sha', required=True)
    args = parser.parse_args()
    bundle = json.loads(args.proposal.read_text(encoding='utf-8'))
    aliases = json.loads(args.aliases.read_text(encoding='utf-8'))
    report = json.loads(args.dev_report.read_text(encoding='utf-8'))
    bundle['field_aliases'] = aliases['field_aliases']
    bundle['scoped_field_aliases'] = aliases['scoped_field_aliases']
    bundle['scope']['excluded_topics'] = sorted(set(
        bundle['scope']['excluded_topics']) | EXCLUDED)
    bundle['runtime'].update(release=args.runtime_release,
                             upstream_sha=args.upstream_sha,
                             tasks_enabled=False, generic_trace_enabled=False)
    bundle['dev_batch'] = report['batch']
    active = read_active_release(args.release_root)
    bundle['previous_release'] = active['release_id'] if active else None
    release_id = stage_operator_release(
        args.release_root, bundle, args.archive, report,
        runtime_release=args.runtime_release, upstream_sha=args.upstream_sha)
    print(release_id)


if __name__ == '__main__':
    main()
