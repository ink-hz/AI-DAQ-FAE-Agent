"""Transfer immutable candidate originals; evidence validation never certifies D1."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

try:
    from scripts.archive_candidate_snapshot import verify_archive
except ModuleNotFoundError:
    from archive_candidate_snapshot import verify_archive

FIELDS = ('target_id', 'version_id', 'storage_independence_record', 'authorization_record',
          'permission_record', 'read_only_record', 'custodian')
TOKEN = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z')


def validate_evidence(evidence: dict, *, retrieval: bool = False) -> dict:
    """Require opaque audit references; review of their truth is a human gate."""
    if not isinstance(evidence, dict) or evidence.get('format_version') != 1:
        raise ValueError('unsupported evidence format')
    fields = FIELDS + (('retrieval_record', 'retrieval_environment_id') if retrieval else ())
    result = {'format_version': 1}
    for key in fields:
        value = evidence.get(key)
        if not isinstance(value, str) or not TOKEN.fullmatch(value):
            raise ValueError('missing or invalid evidence: ' + key)
        result[key] = value
    readers = evidence.get('authorized_readers')
    if not isinstance(readers, list) or not readers or any(
        not isinstance(value, str) or not TOKEN.fullmatch(value) for value in readers
    ):
        raise ValueError('missing or invalid evidence: authorized_readers')
    result['authorized_readers'] = readers
    return result


def copy_archive(source: Path, destination: Path, expected_sha256: str, evidence: dict) -> dict:
    facts = validate_evidence(evidence)
    if destination.exists() or destination.is_symlink():
        raise ValueError('destination already exists')
    if destination.parent.is_symlink() or not destination.parent.is_dir():
        raise ValueError('destination parent missing or linked')
    if destination.parent.resolve().is_relative_to(source.resolve()):
        raise ValueError('destination cannot be inside source')
    verify_archive(source, expected_sha256)
    stage = Path(tempfile.mkdtemp(prefix='.daq-durable-', dir=destination.parent))
    try:
        # copytree preserves the exact manifest bytes and required read-only modes.
        shutil.copytree(source, stage, dirs_exist_ok=True, symlinks=True)
        summary = verify_archive(stage, expected_sha256)
        verify_archive(source, expected_sha256)
        if destination.exists() or destination.is_symlink():
            raise ValueError('destination already exists')
        stage.rename(destination)
        return {**summary, 'evidence': facts,
                'verified_at_utc': datetime.now(timezone.utc).isoformat(),
                'status': 'copy_verified_pending_independent_retrieval'}
    except Exception:
        if stage.exists():
            # Private failed stage is retained for the operator; never alter originals.
            os.chmod(stage, 0o700)
        raise


def verify_retrieval(archive: Path, expected_sha256: str, evidence: dict) -> dict:
    facts = validate_evidence(evidence, retrieval=True)
    summary = verify_archive(archive, expected_sha256)
    return {**summary, 'evidence': facts,
            'verified_at_utc': datetime.now(timezone.utc).isoformat(),
            'status': 'retrieved_bytes_verified_pending_review'}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('copy', 'retrieve'))
    parser.add_argument('archive', type=Path)
    parser.add_argument('--destination', type=Path)
    parser.add_argument('--expected-manifest-sha256', required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    try:
        evidence = json.loads(args.evidence.read_text())
        if args.command == 'copy':
            if args.destination is None:
                raise ValueError('destination required')
            result = copy_archive(args.archive, args.destination, args.expected_manifest_sha256, evidence)
        else:
            result = verify_retrieval(args.archive, args.expected_manifest_sha256, evidence)
    except (OSError, ValueError, KeyError, TypeError):
        # Exceptions can contain original filenames and custody locations.
        print('durable archive failed; inspect privately; D1 remains pending', file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
