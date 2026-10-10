"""Build a private A1 inventory from a verified archive and explicit private decisions."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from daq_fae.knowledge.source_disposition import verified_inventory, write_inventory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--decisions', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    private_root = root / 'data' / 'knowledge' / 'curated'
    if not output.is_relative_to(private_root) or output == private_root:
        parser.error('output must be a new run under data/knowledge/curated/')
    ignored = subprocess.run(['git', 'check-ignore', '-q', str(output)], cwd=root)
    if ignored.returncode != 0:
        parser.error('output must be Git ignored')
    inventory = verified_inventory(args.archive, json.loads(args.snapshot.read_text()),
                                   json.loads(args.decisions.read_text()))
    write_inventory(output, inventory)
    print(json.dumps(inventory['summary'], sort_keys=True))


if __name__ == '__main__':
    main()
