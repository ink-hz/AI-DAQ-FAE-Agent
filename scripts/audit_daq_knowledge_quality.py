"""Build a private offline source/field coverage batch from explicit input identities.

No model, network, activation or approval operations are performed. The input
release is a reviewed seed, not an observation of current production activity.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from daq_fae.knowledge.source_coverage import build_source_coverage, build_field_matrix, write_quality_batch


ENTITIES = ('ego-1600', 'ego-1920', 'ego-pro', 'eg-db', 'wristcam', 'umi-finger', 'umi-vision-module', 'hub')
SYSTEMS = ('ego-standalone', 'ego-pro-standalone', 'ego-two-wristcam-hub-pc', 'ego-usb-pc', 'ego-wifi-pc')


def baseline_identity(release_id, manifest, local_active):
    return {'release_id': release_id, 'input_scope': 'explicit_local_reviewed_seed',
            'record_count': manifest['record_count'], 'section_count': manifest.get('section_count', 0),
            'record_kind_counts': manifest.get('record_kind_counts', {}),
            'local_active_release_id': local_active.get('release_id'),
            'local_pointer_differs': local_active.get('release_id') != release_id,
            'production_observation': 'not_performed', 'role': 'internal_fae',
            'runtime_contract': manifest.get('runtime_contract'),
            'previous_release': manifest.get('previous_release')}


def argument_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--release-manifest', type=Path, required=True)
    parser.add_argument('--root', type=Path, default=Path('.'))
    parser.add_argument('--output', type=Path, required=True)
    return parser


def main():
    from daq_fae.knowledge.source_import import import_archive
    from daq_fae.knowledge.records import validate_records
    args = argument_parser().parse_args()
    root = args.root.resolve()
    output = args.output.resolve(strict=False)
    private = (root / 'data/knowledge/curated').resolve()
    if not output.is_relative_to(private) or output == private or args.output.is_symlink():
        raise ValueError('output must be a new batch under private curated root')
    inputs = {}
    def load(path):
        raw = path.read_bytes()
        inputs[str(path.resolve())] = hashlib.sha256(raw).hexdigest()
        return json.loads(raw)
    curated = root / 'data/knowledge/curated'
    disposition = load(curated / 'a1-20261010/source-disposition.json')
    assets = load(curated / 'a1-20261010/asset-inventory.json')
    snapshot = import_archive(args.archive.resolve(), disposition['archive_manifest_sha256'])
    inputs[str((args.archive / 'manifest.json').resolve())] = snapshot['archive_manifest_sha256']
    sections = []
    for batch in ('a2', 'a3', 'a4'):
        sections.extend(load(curated / f'{batch}-20261010/section-index.json')['sections'])
    graph = load(curated / 'b3-normalized-20261012/review-graph-v1/dependency-bundle.json')
    software = load(curated / 'c3-20261010/software-evidence-matrix.json')
    manifest = load(args.release_manifest)
    raw_manifest = args.release_manifest.read_bytes()
    release_id = hashlib.sha256(raw_manifest).hexdigest()
    if args.release_manifest.parent.name != release_id:
        raise ValueError('release manifest content address mismatch')
    _, record_findings = validate_records(manifest['records'], snapshot)
    if record_findings:
        raise ValueError('reviewed seed record validation failed: ' +
                         ','.join(sorted({row['code'] for row in record_findings})))
    if manifest['archive_manifest_sha256'] != snapshot['archive_manifest_sha256']:
        raise ValueError('release archive mismatch')
    local_active = load(root / 'data/knowledge/published/active.json')
    baseline = baseline_identity(release_id, manifest, local_active)
    ledger = build_source_coverage(snapshot, disposition, assets, sections, graph['records'], manifest['records'])
    matrix = build_field_matrix(graph['records'], manifest['records'], sections, software, graph['coverage'])
    matrix['field_definitions'] = graph['field_definitions']
    matrix['entities'] = ['entity:' + entity for entity in ENTITIES]
    matrix['systems'] = [{'topology_id': 'topology:' + system,
                          'candidate_sections': [s['section_id'] for s in sections
                                                 if s.get('topology_id') == 'topology:' + system],
                          'variant_scope': 'per_source_only; no_variant_inheritance'} for system in SYSTEMS]
    matrix['baseline'] = baseline
    ledger['baseline'] = baseline
    for code_path in (Path(__file__), root / 'daq_fae/knowledge/source_coverage.py'):
        inputs[str(code_path.resolve())] = hashlib.sha256(code_path.read_bytes()).hexdigest()
    provenance = {'format_version': 'daq-kq-provenance/v1', 'input_sha256': inputs,
                  'archive_verification': 'fresh_all_file_hashes_and_sizes_and_extraction',
                  'fact_review': None, 'permission_review': None,
                  'authority': 'private_candidates_and_inherited_reviewed_seed_only',
                  'source_review': 'not_performed_by_automated_audit', 'baseline': baseline,
                  'entity_ids': matrix['entities'], 'systems': matrix['systems']}
    hashes = write_quality_batch(output, {'source-coverage.json': ledger, 'field-matrix.json': matrix,
                                         'provenance-inventory.json': provenance,
                                         'source-extraction.json': snapshot})
    print(json.dumps({'source_coverage': ledger['summary'], 'field_matrix': matrix['summary'],
                      'output_sha256': hashes}, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
