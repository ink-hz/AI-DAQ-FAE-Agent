"""Stage unchanged DAQ knowledge for a user-directed, partial-gate runtime release.

This command never activates a pointer or sends a model request. It preserves the
19/55 and 3/3 Dev evidence as partial evidence, rather than claiming G2 passed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil

from daq_fae.knowledge.code_identity import daq_code_sha
from daq_fae.knowledge.operator_release import stage_operator_runtime_rebind
from daq_fae.knowledge.reviewed_view import ReviewedKnowledge
from daq_fae.knowledge.sdk_release import (
    _read as read_sdk_release, load_active_sdk_release, rebind_sdk_release,
)


ROOT = Path(__file__).resolve().parents[1]
RELEASE_ONLY_MODULES = {
    'daq_fae/knowledge/operator_release.py',
    'daq_fae/knowledge/reviewed_view.py',
    'daq_fae/knowledge/sdk_release.py',
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rows(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _shared_src_sha() -> str:
    digest = hashlib.sha256()
    for path in sorted((ROOT / 'src').rglob('*.py')):
        digest.update(str(path.relative_to(ROOT)).encode())
        digest.update(b'\0')
        digest.update(path.read_bytes())
        digest.update(b'\0')
    return digest.hexdigest()


def _audit_batch(directory: Path, *, expected_cases: int,
                 expected_turns: int) -> tuple[dict, str, str]:
    results = _rows(directory / 'results.jsonl')
    original_reviews = _rows(directory / 'independent-review.jsonl')
    reviews = _rows(directory / 'independent-review-bound.jsonl')
    identity = json.loads((directory / 'identity.json').read_text())
    if (len(results) != expected_cases or len(reviews) != expected_turns
            or len(original_reviews) != expected_turns):
        raise ValueError('Dev evidence count mismatch')
    actual = [(row['case']['id'], index, turn['done']['trace_id'],
               hashlib.sha256(turn['done']['answer'].encode()).hexdigest())
              for row in results for index, turn in enumerate(row['turns'], 1)]
    checked = [(row['case_id'], row['turn'], row['trace_id'], row['answer_sha256'])
               for row in reviews]
    if (actual != checked
            or [{key: value for key, value in row.items() if key != 'answer_sha256'}
                for row in reviews] != original_reviews
            or any(row['verdict'] != 'pass'
                   or set(row['axes'].values()) != {'pass'} for row in reviews)):
        raise ValueError('independent Dev answer review mismatch')
    if any(turn['http_status'] != 200 or turn['done']['fallback_used']
           or not turn['integrity'] or not all(turn['integrity'].values())
           for row in results for turn in row['turns']):
        raise ValueError('Dev replay integrity failed')
    if identity['publication_eligible'] is not False \
            or identity['knowledge_binding'] != 'dev_code_preview_of_immutable_release':
        raise ValueError('unexpected Dev preview identity')
    if directory.name.startswith('focus-') and identity.get('code_unchanged_during_replay') is not True:
        raise ValueError('focused Dev code changed during replay')
    return (identity, _sha(directory / 'results.jsonl'),
            _sha(directory / 'independent-review-bound.jsonl'))


def _assert_answer_code_unchanged(path: Path) -> None:
    before = json.loads(path.read_text())
    after = {str(p.relative_to(ROOT)): _sha(p) for p in (ROOT / 'daq_fae').rglob('*.py')}
    if set(before) != set(after) or {key for key in before if before[key] != after[key]} != RELEASE_ONLY_MODULES:
        raise ValueError('answer code changed after reviewed Dev replay')


def _preview(root: Path, target: Path, origin_id: str, rebound_id: str) -> Path:
    target.mkdir(parents=True, exist_ok=False)
    (target / 'releases').mkdir()
    for release_id in (origin_id, rebound_id):
        shutil.copytree(root / 'releases' / release_id,
                        target / 'releases' / release_id)
    (target / 'active.json').write_text(json.dumps({'release_id': rebound_id}) + '\n')
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--main-root', type=Path, required=True)
    parser.add_argument('--sdk-root', type=Path, required=True)
    parser.add_argument('--single-dir', type=Path, required=True)
    parser.add_argument('--focus-dir', type=Path, required=True)
    parser.add_argument('--pre-release-hashes', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists():
        raise ValueError('rebind output directory already exists')
    single, single_results, single_review = _audit_batch(
        args.single_dir, expected_cases=19, expected_turns=19)
    focus, focus_results, focus_review = _audit_batch(
        args.focus_dir, expected_cases=1, expected_turns=3)
    keys = ('daq_code_sha256', 'shared_src_sha256', 'prompt_sha256',
            'schema_prompt_sha256', 'sdk_catalog_sha256',
            'release_notes_sha256', 'knowledge_release', 'model',
            'gateway_base_url_sha256')
    if any(single[key] != focus[key] for key in keys):
        raise ValueError('Dev candidate identity differs across evidence')
    if focus['daq_code_sha256'] != '407f31f7bd5709fa53793e3ff95f97c102b2acb837a58178c3d613b9860bbd4a':
        raise ValueError('unexpected reviewed candidate')
    if (focus['shared_src_sha256'] != _shared_src_sha()
            or focus['prompt_sha256'] != _sha(ROOT / 'prompts/reviewed_knowledge_system.md')
            or focus['schema_prompt_sha256'] != _sha(ROOT / 'prompts/daq_schema_extraction.md')):
        raise ValueError('reviewed shared source or prompt changed')
    _assert_answer_code_unchanged(args.pre_release_hashes)
    current_code = daq_code_sha()
    runtime = 'daq-' + current_code[:12]
    upstream = json.loads((ROOT / 'upstream-source.json').read_text())['revision']
    old_main = json.loads((args.main_root / 'active.json').read_text())['release_id']
    old_sdk = json.loads((args.sdk_root / 'active.json').read_text())['release_id']
    if old_main != focus['knowledge_release']:
        raise ValueError('main release differs from Dev preview')
    sdk_origin = read_sdk_release(args.sdk_root, old_sdk)
    if (sdk_origin['catalog_sha256'] != focus['sdk_catalog_sha256']
            or sdk_origin.get('release_notes_sha256') != focus['release_notes_sha256']):
        raise ValueError('SDK or release notes differ from Dev preview')
    evidence = {
        'reviewer': 'Codex', 'dev_preview_code_sha256': focus['daq_code_sha256'],
        'single_results_sha256': single_results, 'single_review_sha256': single_review,
        'single_reviewed_turns': 19, 'single_unrun_cases': 36,
        'focus_results_sha256': focus_results, 'focus_review_sha256': focus_review,
        'focus_reviewed_turns': 3,
        'quality_gate_status': 'user_directed_partial_no_repeat',
    }
    new_main = stage_operator_runtime_rebind(
        args.main_root, origin_release=old_main,
        runtime_release=runtime, upstream_sha=upstream, evidence=evidence)
    new_sdk = rebind_sdk_release(
        args.sdk_root, origin_release=old_sdk,
        runtime_release=runtime, upstream_sha=upstream,
        daq_code_sha256=current_code, knowledge_release=new_main,
        evidence={'reviewer': 'Codex', 'focus_review_sha256': focus_review,
                  'quality_gate_status': 'user_directed_partial_no_repeat'})
    args.output_dir.mkdir(parents=True)
    main_preview = _preview(args.main_root, args.output_dir / 'main-preview',
                            old_main, new_main)
    sdk_preview = _preview(args.sdk_root, args.output_dir / 'sdk-preview',
                           old_sdk, new_sdk)
    knowledge = ReviewedKnowledge.load_active(
        main_preview, runtime_release=runtime, upstream_sha=upstream)
    catalog = load_active_sdk_release(
        sdk_preview, runtime_release=runtime, upstream_sha=upstream,
        daq_code_sha256=current_code, knowledge_release=knowledge.release_id)
    if len(knowledge.records_for('internal_fae')) != 27 or len(catalog.records) != 24 \
            or len(catalog.release_notes.records) != 17:
        raise ValueError('rebound knowledge counts differ')
    report = {
        'old_main': old_main, 'new_main': new_main,
        'old_sdk': old_sdk, 'new_sdk': new_sdk,
        'daq_code_sha256': current_code, 'runtime_release': runtime,
        'upstream_sha': upstream, 'dev_evidence': evidence,
        'main_records': 27, 'sdk_records': 24, 'release_notes': 17,
        'quality_gate_status': 'user_directed_partial_no_repeat',
    }
    (args.output_dir / 'release-identity.json').write_text(
        json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + '\n')
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
