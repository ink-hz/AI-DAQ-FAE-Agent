"""Apply the pinned shared schema, then DAQ-owned state, to the DAQ database.

This is an explicit operator command. ASGI startup never changes the schema.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
from urllib.parse import unquote, urlsplit

import psycopg

from daq_fae.authenticated_persistence import _validate_database


ROOT = Path(__file__).resolve().parents[1]


def migration_files() -> tuple[Path, ...]:
    shared = tuple(sorted((ROOT / "migrations" / "shared").glob("0*.sql")))
    daq = tuple(sorted((ROOT / "migrations" / "daq").glob("0*.sql")))
    if len(shared) != 10 or len(daq) != 3:
        raise RuntimeError("daq_migration_manifest_incomplete")
    return (*shared, *daq)


def preflight_database(connection) -> None:
    """Inspect existing data before the first shared migration changes schema."""
    if connection.execute("select to_regclass('public.fae_enterprise_sessions')").fetchone()[0]:
        foreign_identity = connection.execute(
            "select exists (select 1 from public.fae_enterprise_sessions "
            "where agent_id <> 'ai-daq-fae-agent')"
        ).fetchone()[0]
        if foreign_identity:
            raise ValueError("daq_migration_foreign_database")
    if connection.execute("select to_regclass('public.chat_sessions')").fetchone()[0]:
        foreign = connection.execute(
            "select exists (select 1 from public.chat_sessions "
            "where external_session_id not like 'daq:%')"
        ).fetchone()[0]
        if foreign:
            raise ValueError("daq_migration_foreign_database")
    identity_exists = connection.execute(
        "select to_regclass('public.daq_installation_identity')"
    ).fetchone()[0]
    if identity_exists:
        marker = connection.execute(
            "select agent_id, database_name = current_database() "
            "from public.daq_installation_identity where singleton"
        ).fetchone()
        if marker != ("ai-daq-fae-agent", True):
            raise ValueError("daq_migration_foreign_database")
    elif connection.execute("select to_regclass('public.platform_tasks')").fetchone()[0]:
        if connection.execute("select exists (select 1 from public.platform_tasks)").fetchone()[0]:
            raise ValueError("daq_migration_foreign_database")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DAQ-only Postgres schema migration")
    parser.add_argument("--apply", action="store_true", help="Apply to DAQ_DATABASE_URL")
    parser.add_argument("--confirm-database-name", help="Exact DAQ database name expected in DAQ_DATABASE_URL")
    args = parser.parse_args(argv)
    files = migration_files()
    if not args.apply:
        for path in files:
            print(path.relative_to(ROOT))
        return 0
    database_url = os.getenv("DAQ_DATABASE_URL", "")
    _validate_database(database_url, os.getenv("DATABASE_URL"))
    database_name = unquote(urlsplit(database_url).path.lstrip("/"))
    if not args.confirm_database_name or args.confirm_database_name != database_name:
        raise ValueError("daq_migration_database_confirmation_invalid")
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("select pg_advisory_lock(hashtextextended('daq-fae-migrations', 0))")
        try:
            preflight_database(connection)
            for path in files:
                connection.execute(path.read_text(encoding="utf-8"))
                print(f"applied {path.relative_to(ROOT)}")
        finally:
            connection.execute("select pg_advisory_unlock(hashtextextended('daq-fae-migrations', 0))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
