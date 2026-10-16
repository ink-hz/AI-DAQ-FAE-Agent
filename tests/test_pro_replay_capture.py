"""The private Dev runner must retain schema diagnostics without changing calls."""

import importlib.util
from pathlib import Path

import pytest


def _runner():
    path = (Path(__file__).resolve().parents[1] /
            'data/knowledge/curated/topology-procedure-review-20261016/'
            'run_real_pro_replay.py')
    if not path.is_file():
        pytest.skip('private Dev runner absent from this checkout')
    spec = importlib.util.spec_from_file_location('pro_replay_capture', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_capture_schema_tool_calls_preserves_events_and_excludes_other_tools():
    runner = _runner()
    seen = []
    events = [
        {'type': 'tool_call', 'name': 'submit_daq_request_schema',
         'arguments': {'confidence': 'low', 'goals': []}},
        {'type': 'stop', 'stop_reason': 'tool_use'},
    ]

    def original(_self, _messages, tools=None, required_tool=None):
        yield from events

    wrapped = runner.capture_schema_tool_calls(original, seen.append)
    assert list(wrapped(None, [], required_tool='submit_daq_request_schema')) == events
    assert seen == [events[0]]
    seen.clear()
    assert list(wrapped(None, [], required_tool='lookup_spec')) == events
    assert seen == []


def test_replay_requires_fresh_private_output_directory(tmp_path, monkeypatch):
    runner = _runner()
    target = tmp_path / 'new-run'
    monkeypatch.setenv('DAQ_PRO_REPLAY_OUTPUT_DIR', str(target))
    assert runner.replay_output_dir() == target
    assert target.is_dir()
    assert target.stat().st_mode & 0o777 == 0o700
    with pytest.raises(FileExistsError):
        runner.replay_output_dir()
