"""DAQ feedback summaries must never mix camera rows or expose turn content."""
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
import pytest
from psycopg.types.json import Jsonb

from daq_fae.feedback_analytics import summarize_feedback
from scripts.daq_feedback_analytics import write_private_summary
from src.production_analytics.private_io import PrivatePathError
from scripts.migrate_daq_pg import migrate_connection

pytest_plugins = ('tests.test_durable_state',)

DAQ_DSN = 'postgresql://daq_user@db.invalid/daq_db?options=-csearch_path%3Ddaq_fae'
START = datetime(2026, 10, 12, tzinfo=UTC)
END = START + timedelta(days=1)


def test_feedback_summary_uses_only_daq_schema_and_aggregates(pg_database):
    with psycopg.connect(pg_database, autocommit=True) as connection:
        connection.execute('create table public.camera_probe (marker text)')
        connection.execute("insert into public.camera_probe values ('camera-secret')")
        migrate_connection(connection, isolated_schema=True)
        session_id = f'daq:{uuid4()}'
        connection.execute(
            "insert into daq_fae.chat_sessions (external_session_id, channel) values (%s, 'fae')",
            (session_id,),
        )
        first, second = uuid4(), uuid4()
        for turn_id, index, outcome, fallback in (
            (first, 0, 'resolved', False), (second, 1, 'safe_abstained', True),
        ):
            connection.execute(
                """insert into daq_fae.chat_turns
                   (id, external_session_id, turn_index, trace_id, channel,
                    question, answer, outcome, fallback_used, created_at, metadata)
                   values (%s, %s, %s, %s, 'fae', 'private question',
                           'private answer', %s, %s, %s, %s)""",
                (turn_id, session_id, index, f'trace-{index}', outcome, fallback,
                 START + timedelta(hours=index + 1), Jsonb({'agent_id': 'ai-daq-fae-agent'})),
            )
        connection.execute(
            """insert into daq_fae.turn_feedback
               (turn_id, external_session_id, trace_id, rating, reason_code,
                comment, channel, created_at)
               values (%s, %s, 'trace-1', 'bad', 'fact_error', 'private comment',
                       'fae', %s)""",
            (second, session_id, START + timedelta(hours=3)),
        )
        connection.execute(
            """insert into daq_fae.turn_reviews
               (turn_id, priority, review_status, reviewer, created_at)
               values (%s, 'P1', 'pending', 'human', %s)""",
            (second, START + timedelta(hours=4)),
        )

    def connect(_dsn):
        return psycopg.connect(pg_database, options='-csearch_path=daq_fae')

    result = summarize_feedback(DAQ_DSN, START, END, connect=connect)
    assert result['turns'] == 2
    assert result['outcomes'] == {'resolved': 1, 'safe_abstained': 1}
    assert result['fallback_turns'] == 1
    assert result['feedback_events'] == {'bad': 1}
    assert result['bad_reason_codes'] == {'fact_error': 1}
    assert result['reviews'] == {'pending': 1}
    assert 'private' not in str(result)
    assert 'camera-secret' not in str(result)


def test_feedback_summary_rejects_nonisolated_dsn_before_connect():
    def forbidden(_dsn):
        raise AssertionError('must not connect')

    with pytest.raises(ValueError, match='daq_feedback_schema_required'):
        summarize_feedback('postgresql://daq_user@db.invalid/daq_db', START, END,
                           connect=forbidden)


def test_feedback_summary_rejects_invalid_window_before_connect():
    def forbidden(_dsn):
        raise AssertionError('must not connect')

    with pytest.raises(ValueError, match='daq_feedback_window_invalid'):
        summarize_feedback(DAQ_DSN, END, START, connect=forbidden)


def test_private_feedback_summary_writes_aggregate_json_only(tmp_path):
    root = tmp_path / 'analysis' / 'results'
    output = root / 'daq' / 'daily.json'
    payload = {'turns': 2, 'outcomes': {'resolved': 1, 'safe_abstained': 1}}
    write_private_summary(output, payload, private_root=root,
                          git_ignore_checker=lambda _: True)
    assert output.stat().st_mode & 0o077 == 0
    assert output.parent.stat().st_mode & 0o077 == 0
    assert '"turns": 2' in output.read_text()
    with pytest.raises(PrivatePathError, match='output_exists'):
        write_private_summary(output, payload, private_root=root,
                              git_ignore_checker=lambda _: True)


def test_private_feedback_summary_rejects_outside_path_and_symlink(tmp_path):
    root = tmp_path / 'analysis' / 'results'
    outside = tmp_path / 'outside.json'
    with pytest.raises(PrivatePathError, match='output_outside_private_root'):
        write_private_summary(outside, {'turns': 0}, private_root=root,
                              git_ignore_checker=lambda _: True)
    root.mkdir(parents=True)
    target = tmp_path / 'target'
    target.mkdir()
    (root / 'daq').symlink_to(target, target_is_directory=True)
    with pytest.raises(PrivatePathError, match='private_output_symlink'):
        write_private_summary(root / 'daq' / 'daily.json', {'turns': 0},
                              private_root=root, git_ignore_checker=lambda _: True)
