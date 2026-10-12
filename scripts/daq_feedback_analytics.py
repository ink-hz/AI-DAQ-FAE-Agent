"""Export private, aggregate DAQ feedback metrics from the DAQ schema."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

import psycopg

from daq_fae.feedback_analytics import summarize_feedback
from src.production_analytics.private_io import (
    PrivatePathError, prepare_private_output_directory,
)

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_ROOT = ROOT / 'analysis' / 'results'


def _git_ignored(path: Path) -> bool:
    try:
        return subprocess.run(
            ['git', 'check-ignore', '-q', str(path)], cwd=ROOT,
            capture_output=True, check=False,
        ).returncode == 0
    except OSError:
        return False


def write_private_summary(output: Path, payload: dict, *, private_root: Path = PRIVATE_ROOT,
                          git_ignore_checker=_git_ignored) -> Path:
    if output.suffix != '.json' or output.name in {'.json', '..json'}:
        raise PrivatePathError('output_name_invalid')
    parent = prepare_private_output_directory(
        output.parent, private_root=private_root,
        git_ignore_checker=git_ignore_checker,
    )
    target = parent / output.name
    if target.exists() or target.is_symlink():
        raise PrivatePathError('output_exists')
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=parent,
                                         prefix=f'.{target.name}.', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o600)
        try:
            os.link(temporary, target, follow_symlinks=False)
        except FileExistsError:
            raise PrivatePathError('output_exists') from None
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', required=True, help='Inclusive ISO-8601 instant')
    parser.add_argument('--end', required=True, help='Exclusive ISO-8601 instant')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        start = datetime.fromisoformat(args.start)
        end = datetime.fromisoformat(args.end)
        dsn = os.environ.get('DAQ_DATABASE_URL', '')
        result = summarize_feedback(dsn, start, end)
        write_private_summary(args.output, result)
    except (ValueError, PrivatePathError, psycopg.Error) as exc:
        reason = exc.reason if isinstance(exc, PrivatePathError) else (
            str(exc) if isinstance(exc, ValueError) else 'daq_feedback_database_unavailable'
        )
        print(json.dumps({'status': 'blocked', 'reason': reason}, sort_keys=True))
        return 2
    print(json.dumps({'status': 'complete', 'turns': result['turns'],
                      'schema_version': result['schema_version']}, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
