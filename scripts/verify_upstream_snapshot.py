"""Verify that the copied shared runtime matches its pinned upstream commit."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path


class SourceMismatch(RuntimeError):
    pass


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ['git', '-C', str(repo), *args], capture_output=True, text=True,
    )
    if result.returncode:
        raise SourceMismatch('git object unavailable: ' + ' '.join(args))
    return result.stdout.strip()


def verify_upstream_snapshot(daq_repo, upstream_repo, manifest_path) -> dict[str, str]:
    daq_repo = Path(daq_repo)
    upstream_repo = Path(upstream_repo)
    try:
        manifest = json.loads(Path(manifest_path).read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise SourceMismatch('invalid upstream source manifest') from exc
    revision = manifest.get('revision')
    if not isinstance(revision, str) or not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise SourceMismatch('invalid upstream revision')
    try:
        actual_revision = _git(upstream_repo, 'rev-parse', '--verify', f'{revision}^{{commit}}')
    except SourceMismatch as exc:
        raise SourceMismatch('unavailable upstream revision') from exc
    if actual_revision != revision:
        raise SourceMismatch('upstream revision mismatch')
    if _git(daq_repo, 'status', '--porcelain', '--untracked-files=all', '--',
            'src', 'requirements.txt'):
        raise SourceMismatch('dirty shared source worktree')
    result = {'revision': revision}
    for path, key, kind in (
        ('src', 'src_tree', 'src tree'),
        ('requirements.txt', 'requirements_blob', 'requirements blob'),
    ):
        upstream_oid = _git(upstream_repo, 'rev-parse', f'{revision}:{path}')
        daq_oid = _git(daq_repo, 'rev-parse', f'HEAD:{path}')
        if upstream_oid != daq_oid:
            raise SourceMismatch(f'{kind} differs from pinned upstream revision')
        result[key] = upstream_oid
    return result


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream', type=Path, required=True,
                        help='checkout containing the pinned upstream commit')
    parser.add_argument('--repo', type=Path, default=root)
    parser.add_argument('--manifest', type=Path, default=root / 'upstream-source.json')
    args = parser.parse_args()
    try:
        result = verify_upstream_snapshot(args.repo, args.upstream, args.manifest)
    except SourceMismatch as exc:
        parser.exit(1, f'upstream snapshot verification failed: {exc}\n')
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
