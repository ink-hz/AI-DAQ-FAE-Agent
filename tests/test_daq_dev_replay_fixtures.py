from scripts.run_daq_workflow_dev_replay import _attachment_fixture


def test_dev_replay_identity_hashes_actual_shared_runtime_bytes(tmp_path, monkeypatch):
    from scripts import run_daq_workflow_dev_replay as runner

    source = tmp_path / 'src' / 'agent' / 'loop'
    source.mkdir(parents=True)
    runtime = source / 'runtime.py'
    runtime.write_text('before\n')
    monkeypatch.setattr(runner, 'ROOT', tmp_path)
    before = runner._shared_src_sha()
    runtime.write_text('after\n')
    assert before != runner._shared_src_sha()


def test_current_turn_log_fixture_uploads_only_on_first_turn():
    first = _attachment_fixture('current_turn_log', 0)
    assert first is not None
    filename, payload, mime = first
    assert filename == 'field-log.txt'
    assert mime == 'text/plain'
    assert b'device=EGO Pro' in payload
    assert b'code=E42' in payload
    assert _attachment_fixture('current_turn_log', 1) is None
