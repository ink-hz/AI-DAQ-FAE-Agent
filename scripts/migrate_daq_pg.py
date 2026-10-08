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
    if len(shared) != 10 or len(daq) != 1:
        raise RuntimeError("daq_migration_manifest_incomplete")
    return (*shared, *daq)


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
            for path in files:
                connection.execute(path.read_text(encoding="utf-8"))
                print(f"applied {path.relative_to(ROOT)}")
        finally:
            connection.execute("select pg_advisory_unlock(hashtextextended('daq-fae-migrations', 0))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
