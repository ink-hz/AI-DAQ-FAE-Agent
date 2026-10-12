"""Read-only, DAQ-schema-only aggregate feedback analysis.

Detailed answers and reviewer notes remain in the authenticated Review Center.
This module emits counts only and cannot export camera or user content.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlsplit

import psycopg

from daq_fae.authenticated_persistence import _validate_database
from daq_fae.platform_identity import AGENT_ID


def _require_daq_schema(dsn: str) -> None:
    try:
        _validate_database(dsn, None)
        query = parse_qsl(urlsplit(dsn).query, keep_blank_values=True)
    except (TypeError, ValueError):
        raise ValueError('daq_feedback_schema_required') from None
    if query != [('options', '-csearch_path=daq_fae')]:
        raise ValueError('daq_feedback_schema_required')


def _grouped(connection, query: str, params: tuple[datetime, datetime]) -> dict[str, int]:
    return {str(key): int(count) for key, count in connection.execute(query, params).fetchall()}


def summarize_feedback(
    dsn: str,
    start: datetime,
    end: datetime,
    *,
    connect: Callable[[str], object] = psycopg.connect,
) -> dict[str, object]:
    """Summarize DAQ turns in a half-open UTC window without returning content."""
    _require_daq_schema(dsn)
    if (not isinstance(start, datetime) or not isinstance(end, datetime)
            or start.tzinfo is None or end.tzinfo is None
            or start.utcoffset() is None or end.utcoffset() is None
            or start >= end):
        raise ValueError('daq_feedback_window_invalid')
    bounds = (start.astimezone(UTC), end.astimezone(UTC))
    with connect(dsn) as connection:
        connection.execute('set transaction isolation level repeatable read read only')
        marker = connection.execute(
            """select current_schema(), agent_id,
                      database_name = current_database()
               from daq_installation_identity where singleton"""
        ).fetchone()
        if marker != ('daq_fae', AGENT_ID, True):
            raise ValueError('daq_feedback_identity_unverified')
        foreign = connection.execute(
            """select count(*) from chat_turns
               where created_at >= %s and created_at < %s
                 and metadata->>'agent_id' is distinct from %s""",
            (*bounds, AGENT_ID),
        ).fetchone()[0]
        if foreign:
            raise ValueError('daq_feedback_foreign_turn')
        outcomes = _grouped(connection, """
            select coalesce(nullif(outcome, ''), 'unknown'), count(*)
            from chat_turns where created_at >= %s and created_at < %s
            group by 1 order by 1
        """, bounds)
        feedback = _grouped(connection, """
            select f.rating, count(*) from turn_feedback f
            join chat_turns t on t.id = f.turn_id
            where t.created_at >= %s and t.created_at < %s
            group by f.rating order by f.rating
        """, bounds)
        reasons = _grouped(connection, """
            select coalesce(nullif(f.reason_code, ''), 'unspecified'), count(*)
            from turn_feedback f join chat_turns t on t.id = f.turn_id
            where t.created_at >= %s and t.created_at < %s
              and f.rating = 'bad'
            group by 1 order by 1
        """, bounds)
        reviews = _grouped(connection, """
            select r.review_status, count(*) from turn_reviews r
            join chat_turns t on t.id = r.turn_id
            where t.created_at >= %s and t.created_at < %s
            group by r.review_status order by r.review_status
        """, bounds)
        fallback = connection.execute(
            """select count(*) from chat_turns
               where created_at >= %s and created_at < %s and fallback_used""",
            bounds,
        ).fetchone()[0]
    return {
        'schema_version': 'daq-feedback-summary/v1',
        'agent_id': AGENT_ID,
        'window_start': bounds[0].isoformat(),
        'window_end': bounds[1].isoformat(),
        'turns': sum(outcomes.values()),
        'outcomes': outcomes,
        'fallback_turns': int(fallback),
        'feedback_events': feedback,
        'bad_reason_codes': reasons,
        'reviews': reviews,
    }
