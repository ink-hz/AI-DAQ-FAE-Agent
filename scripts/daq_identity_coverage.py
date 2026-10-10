#!/usr/bin/env python3
"""Compile a private B1 bundle; stdout contains counts only."""
from pathlib import Path
import argparse
import hashlib
import json
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from daq_fae.knowledge.identity_coverage import build_dictionary, write_private_dictionary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raw = args.input.read_bytes()
    bundle = json.loads(raw)
    result = build_dictionary(bundle['records'], bundle['sources'], bundle['sections'], bundle['config'])
    result['input_sha256'] = hashlib.sha256(raw).hexdigest()
    result['input_manifest'] = bundle.get('input_manifest', {})
    write_private_dictionary(args.output, result)
    print(json.dumps({'records': len(result['record_inventory']), 'fields': len(result['fields']), 'coverage_cells': len(result['coverage']), 'online_eligible': False}, sort_keys=True))


if __name__ == '__main__':
    main()
