import json

from scripts.run_daq_workflow_dev_replay import _cases


def test_replay_can_load_a_frozen_field_question_suite(tmp_path):
    suite = tmp_path / "field.jsonl"
    case = {"id": "FIELD-01", "question": "现场问题", "expected_outcome": "resolved"}
    suite.write_text(json.dumps(case, ensure_ascii=False) + "\n", encoding="utf-8")

    assert _cases("file", suite_file=suite) == [case]
