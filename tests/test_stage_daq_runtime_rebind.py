"""A reviewed trace must bind the exact answer bytes before release staging."""

import hashlib
import json

import pytest

from scripts.stage_daq_runtime_rebind import _audit_batch


def _write(path, rows):
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows))


def test_rebind_audit_rejects_answer_tampering(tmp_path):
    directory = tmp_path / 'single'
    directory.mkdir()
    (directory / 'identity.json').write_text(json.dumps({
        'publication_eligible': False,
        'knowledge_binding': 'dev_code_preview_of_immutable_release',
    }))
    done = {'answer': 'reviewed answer', 'trace_id': 'trace',
            'fallback_used': False}
    turn = {'http_status': 200, 'done': done, 'integrity': {'sse': True}}
    result = {'case': {'id': 'CC01'}, 'turns': [turn]}
    review = {'case_id': 'CC01', 'turn': 1, 'trace_id': 'trace',
              'verdict': 'pass', 'axes': {'semantic_fact': 'pass'}}
    _write(directory / 'results.jsonl', [result])
    _write(directory / 'independent-review.jsonl', [review])
    _write(directory / 'independent-review-bound.jsonl', [{
        **review,
        'answer_sha256': hashlib.sha256(done['answer'].encode()).hexdigest(),
    }])
    _audit_batch(directory, expected_cases=1, expected_turns=1)

    done['answer'] = 'changed after review'
    _write(directory / 'results.jsonl', [result])
    with pytest.raises(ValueError, match='independent Dev answer review mismatch'):
        _audit_batch(directory, expected_cases=1, expected_turns=1)
