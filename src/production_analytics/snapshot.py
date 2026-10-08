from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from src.production_analytics.contracts import AnalysisManifest, AnalysisStatus
from src.production_analytics.private_io import (
    atomic_write_json,
    sha256_file,
    write_jsonl,
)

EARLIEST_TURN_QUERY = """
select min(coalesce(t.question_at, t.created_at)) as earliest_at
from chat_turns t
where coalesce(t.question_at, t.created_at) < %(window_end)s
""".strip()

SNAPSHOT_QUERIES: dict[str, str] = {
    "sessions": """
select s.id, s.external_session_id, s.channel, s.user_id, s.external_user_id,
       s.conversation_title, s.created_at, s.last_active_at, s.metadata
from chat_sessions s
where exists (
    select 1
    from chat_turns t
    where t.external_session_id = s.external_session_id
      and coalesce(t.question_at, t.created_at) >= %(window_start)s
      and coalesce(t.question_at, t.created_at) < %(window_end)s
)
order by s.external_session_id, s.id
""".strip(),
    "turns": """
select t.id, t.session_id, t.external_session_id, t.turn_index, t.trace_id,
       t.channel, t.question, t.answer, t.sources, t.stages, t.done,
       t.planned_capabilities, t.capability_coverage, t.fallback_used,
       t.fallback_reason, t.outcome, t.duration_ms, t.question_at,
       t.answer_at, t.created_at, t.metadata
from chat_turns t
where coalesce(t.question_at, t.created_at) >= %(window_start)s
  and coalesce(t.question_at, t.created_at) < %(window_end)s
order by t.external_session_id, t.turn_index, t.id
""".strip(),
    "feedback": """
select f.id, f.turn_id, f.external_session_id, f.trace_id, f.rating,
       f.reason_code, f.comment, f.channel, f.user_id, f.external_user_id,
       f.created_at, f.metadata
from turn_feedback f
join chat_turns t on t.id = f.turn_id
where coalesce(t.question_at, t.created_at) >= %(window_start)s
  and coalesce(t.question_at, t.created_at) < %(window_end)s
order by f.turn_id, f.created_at, f.id
""".strip(),
    "reviews": """
select r.id, r.turn_id, r.priority, r.review_status, r.failure_layer,
       r.failure_reason, r.expected_answer_notes, r.corrected_answer,
       r.reviewer, r.should_add_to_eval, r.should_update_knowledge,
       r.created_at, r.updated_at, r.metadata
from turn_reviews r
join chat_turns t on t.id = r.turn_id
where coalesce(t.question_at, t.created_at) >= %(window_start)s
  and coalesce(t.question_at, t.created_at) < %(window_end)s
order by r.turn_id, r.created_at, r.id
""".strip(),
    "attachments": """
select a.id, a.turn_id, a.external_session_id, a.trace_id, a.direction,
       a.ordinal, a.association_kind, a.kind, a.media_type, a.size_bytes,
       a.created_at, a.processing_expires_at, a.handoff_deadline_at,
       a.archive_status, a.archive_attempt_count, a.archived_at,
       a.thumbnail_status, a.thumbnail_media_type,
       a.thumbnail_size_bytes, a.updated_at
from chat_turn_attachments a
join chat_turns t on t.id = a.turn_id
where coalesce(t.question_at, t.created_at) >= %(window_start)s
  and coalesce(t.question_at, t.created_at) < %(window_end)s
order by a.turn_id, a.direction, a.ordinal, a.id
""".strip(),
}

SOURCE_COUNT_QUERIES = {
    name: f"select count(*) as row_count from ({query}) as scoped_rows"
    for name, query in SNAPSHOT_QUERIES.items()
}


class SnapshotBlocked(RuntimeError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class SnapshotConfig:
    dsn: str
    output_dir: Path
    analysis_id: str
    window_start: datetime
    window_end: datetime
    query_version: str
    taxonomy_version: str
    reviewer_policy_version: str
    schema_version: str = "production-analytics-v1"
    timezone_name: str = "Asia/Shanghai"

    def __post_init__(self) -> None:
        if self.window_start.tzinfo is None or self.window_end.tzinfo is None:
            raise ValueError("snapshot window must be timezone-aware")
        if self.window_start >= self.window_end:
            raise ValueError("snapshot window start must be before end")


def _default_connect(dsn: str) -> Any:
    return psycopg.connect(dsn, row_factory=dict_row)


def _first_value(row: Any, key: str) -> Any:
    if isinstance(row, Mapping):
        return row.get(key)
    if isinstance(row, (tuple, list)) and row:
        return row[0]
    return None


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


class SnapshotExporter:
    def __init__(self, *, connect: Callable[[str], Any] = _default_connect) -> None:
        self._connect = connect

    def export(self, config: SnapshotConfig) -> AnalysisManifest:
        requested_start = config.window_start.astimezone(timezone.utc)
        effective_start = requested_start
        window_end = config.window_end.astimezone(timezone.utc)
        started_at = datetime.now(timezone.utc).isoformat()
        connection: Any | None = None
        source_counts: dict[str, int] = {}
        exported_counts: dict[str, int] = {}
        hashes: dict[str, str] = {}
        try:
            connection = self._connect(config.dsn)
            connection.execute("set transaction isolation level repeatable read read only")
            read_only = connection.execute("show transaction_read_only").fetchone()
            if str(_first_value(read_only, "transaction_read_only")).lower() != "on":
                raise SnapshotBlocked("source_not_read_only")

            earliest_row = connection.execute(
                EARLIEST_TURN_QUERY, {"window_end": window_end}
            ).fetchone()
            earliest_at = _first_value(earliest_row, "earliest_at")
            if isinstance(earliest_at, datetime):
                earliest_at = earliest_at.astimezone(timezone.utc)
                effective_start = min(requested_start, earliest_at)

            params = {"window_start": effective_start, "window_end": window_end}
            for name, query in SOURCE_COUNT_QUERIES.items():
                count_row = connection.execute(query, params).fetchone()
                source_counts[name] = int(_first_value(count_row, "row_count") or 0)

            raw_dir = config.output_dir / "raw_snapshot"
            for name, query in SNAPSHOT_QUERIES.items():
                rows = connection.execute(query, params).fetchall()
                normalized = [_json_value(dict(row)) for row in rows]
                path = raw_dir / f"{name}.jsonl"
                write_jsonl(path, normalized)
                exported_counts[name] = len(normalized)
                hashes[f"raw_snapshot/{name}.jsonl"] = sha256_file(path)

            if exported_counts.get("turns", 0) == 0:
                raise SnapshotBlocked("empty_snapshot")
            if source_counts != exported_counts:
                raise SnapshotBlocked("snapshot_inconsistent")

            manifest = self._manifest(
                config=config,
                status=AnalysisStatus.SNAPSHOT_COMPLETE,
                block_reason=None,
                requested_start=requested_start,
                effective_start=effective_start,
                window_end=window_end,
                started_at=started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
                source_counts=source_counts,
                exported_counts=exported_counts,
                hashes=hashes,
            )
            atomic_write_json(config.output_dir / "analysis_manifest.json", manifest.to_dict())
            return manifest
        except SnapshotBlocked as exc:
            self._write_blocked(
                config,
                exc.reason,
                requested_start,
                effective_start,
                window_end,
                started_at,
                source_counts,
                exported_counts,
                hashes,
            )
            raise
        except (psycopg.errors.UndefinedTable, psycopg.errors.UndefinedColumn) as exc:
            reason = "schema_mismatch"
            self._write_blocked(
                config,
                reason,
                requested_start,
                effective_start,
                window_end,
                started_at,
                source_counts,
                exported_counts,
                hashes,
            )
            raise SnapshotBlocked(reason) from exc
        except psycopg.OperationalError as exc:
            reason = "source_unavailable"
            self._write_blocked(
                config,
                reason,
                requested_start,
                effective_start,
                window_end,
                started_at,
                source_counts,
                exported_counts,
                hashes,
            )
            raise SnapshotBlocked(reason) from exc
        finally:
            if connection is not None:
                connection.close()

    def _write_blocked(
        self,
        config: SnapshotConfig,
        reason: str,
        requested_start: datetime,
        effective_start: datetime,
        window_end: datetime,
        started_at: str,
        source_counts: Mapping[str, int],
        exported_counts: Mapping[str, int],
        hashes: Mapping[str, str],
    ) -> None:
        manifest = self._manifest(
            config=config,
            status=AnalysisStatus.BLOCKED,
            block_reason=reason,
            requested_start=requested_start,
            effective_start=effective_start,
            window_end=window_end,
            started_at=started_at,
            completed_at=datetime.now(timezone.utc).isoformat(),
            source_counts=source_counts,
            exported_counts=exported_counts,
            hashes=hashes,
        )
        atomic_write_json(config.output_dir / "analysis_manifest.json", manifest.to_dict())

    @staticmethod
    def _manifest(
        *,
        config: SnapshotConfig,
        status: AnalysisStatus,
        block_reason: str | None,
        requested_start: datetime,
        effective_start: datetime,
        window_end: datetime,
        started_at: str,
        completed_at: str,
        source_counts: Mapping[str, int],
        exported_counts: Mapping[str, int],
        hashes: Mapping[str, str],
    ) -> AnalysisManifest:
        return AnalysisManifest(
            analysis_id=config.analysis_id,
            schema_version=config.schema_version,
            query_version=config.query_version,
            taxonomy_version=config.taxonomy_version,
            window_start=effective_start.isoformat(),
            window_end=window_end.isoformat(),
            timezone=config.timezone_name,
            snapshot_started_at=started_at,
            snapshot_completed_at=completed_at,
            source_table_counts=dict(source_counts),
            exported_row_counts=dict(exported_counts),
            population_counts={},
            source_build_identity_if_available=None,
            snapshot_file_hashes=dict(hashes),
            reviewer_policy_version=config.reviewer_policy_version,
            status=status,
            block_reason=block_reason,
            resume_count=0,
            stage_hashes={},
            requested_window_start=requested_start.isoformat(),
            window_start_adjusted=effective_start != requested_start,
        )
