from scripts.run_daq_workflow_dev_replay import _attachment_fixture


def test_current_turn_log_fixture_uploads_only_on_first_turn():
    first = _attachment_fixture('current_turn_log', 0)
    assert first is not None
    filename, payload, mime = first
    assert filename == 'field-log.txt'
    assert mime == 'text/plain'
    assert b'device=EGO Pro' in payload
    assert b'code=E42' in payload
    assert _attachment_fixture('current_turn_log', 1) is None
