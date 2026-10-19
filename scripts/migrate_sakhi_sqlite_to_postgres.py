"""One-shot, atomic Sakhi SQLite -> PostgreSQL/Supabase migration.

This command deliberately does not switch the worker configuration.  It only
copies and verifies the database, so the operator can validate counts before
putting ``DATABASE_URL`` into the production secret.  Dry-run is the default;
``--apply`` is required for any target write.

The destination must be empty.  That guard is intentional: merging two
clinical databases needs a patient-identity policy and cannot safely be
guessed by a deployment script.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sakhi_schema import POSTGRES, schema_statements


TABLES: tuple[str, ...] = (
    "mothers",
    "bp_records",
    "symptom_records",
    "teleconsults",
    "call_reports",
    "pipeline_events",
    "call_turns",
    "component_health",
)
IDENTITY_TABLES = frozenset(
    {"bp_records", "symptom_records", "teleconsults", "call_reports", "pipeline_events", "call_turns"}
)


@dataclass(frozen=True)
class TableSnapshot:
    name: str
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]

    @property
    def count(self) -> int:
        return len(self.rows)


def read_sqlite_snapshot(path: Path) -> tuple[TableSnapshot, ...]:
    """Read one transactionally consistent, read-only SQLite snapshot."""
    if not path.is_file():
        raise FileNotFoundError(f"SQLite source does not exist: {path}")
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN")
        snapshots: list[TableSnapshot] = []
        known = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        for table in TABLES:
            if table not in known:
                snapshots.append(TableSnapshot(table, (), ()))
                continue
            columns = tuple(row[1] for row in conn.execute(f'PRAGMA table_info("{table}")'))
            rows = tuple(tuple(row) for row in conn.execute(f'SELECT * FROM "{table}"'))
            snapshots.append(TableSnapshot(table, columns, rows))
        return tuple(snapshots)
    finally:
        conn.rollback()
        conn.close()


def counts(snapshots: Iterable[TableSnapshot]) -> dict[str, int]:
    return {snapshot.name: snapshot.count for snapshot in snapshots}


def _destination_counts(conn) -> dict[str, int]:
    result: dict[str, int] = {}
    with conn.cursor() as cur:
        for table in TABLES:
            cur.execute(f'SELECT COUNT(*) FROM "{table}"')
            result[table] = int(cur.fetchone()[0])
    return result


def _copy_snapshot(conn, snapshots: Iterable[TableSnapshot]) -> None:
    with conn.cursor() as cur:
        for statement in schema_statements(POSTGRES):
            cur.execute(statement)

        existing = _destination_counts(conn)
        occupied = {table: n for table, n in existing.items() if n}
        if occupied:
            raise RuntimeError(
                "Postgres target is not empty; refusing to merge clinical records. "
                f"Existing row counts: {json.dumps(occupied, sort_keys=True)}"
            )

        for snapshot in snapshots:
            if not snapshot.rows:
                continue
            if not snapshot.columns:
                raise RuntimeError(f"Source table {snapshot.name} has rows but no columns")
            quoted = ", ".join(f'"{column}"' for column in snapshot.columns)
            placeholders = ", ".join(["%s"] * len(snapshot.columns))
            cur.executemany(
                f'INSERT INTO "{snapshot.name}" ({quoted}) VALUES ({placeholders})',
                snapshot.rows,
            )

        # Bulk inserts preserve historical ids.  Catch each identity sequence
        # up before the worker can allocate a new row after cutover.
        for table in IDENTITY_TABLES:
            cur.execute(
                "SELECT setval(pg_get_serial_sequence(%s, 'id'), "
                f"COALESCE((SELECT MAX(id) FROM \"{table}\"), 1), "
                f"EXISTS (SELECT 1 FROM \"{table}\"))",
                (table,),
            )


def migrate(source: Path, dsn: str) -> dict[str, Any]:
    """Copy atomically and verify every table count before commit."""
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - deployment dependency check
        raise RuntimeError("Install requirements.txt (psycopg[binary,pool]) first") from exc

    snapshots = read_sqlite_snapshot(source)
    source_counts = counts(snapshots)
    with psycopg.connect(dsn, connect_timeout=10) as conn:
        _copy_snapshot(conn, snapshots)
        target_counts = _destination_counts(conn)
        if target_counts != source_counts:
            raise RuntimeError(
                "Post-copy verification failed; transaction rolled back. "
                f"source={source_counts}, target={target_counts}"
            )
    return {"source": source_counts, "target": target_counts, "verified": True}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(os.getenv("SAKHI_DB_PATH", "data/sakhi.sqlite3")),
        help="source SQLite file (default: SAKHI_DB_PATH or data/sakhi.sqlite3)",
    )
    parser.add_argument(
        "--target-env",
        default="SAKHI_MIGRATION_POSTGRES_DSN",
        help="environment variable containing the Postgres DSN; its value is never printed",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="perform the atomic copy; without this flag only source counts are reported",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    snapshots = read_sqlite_snapshot(args.source)
    if not args.apply:
        print(json.dumps({"mode": "dry-run", "source": counts(snapshots)}, sort_keys=True))
        return 0
    dsn = os.getenv(args.target_env)
    if not dsn:
        raise SystemExit(f"{args.target_env} is not set")
    result = migrate(args.source, dsn)
    print(json.dumps({"mode": "applied", **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
