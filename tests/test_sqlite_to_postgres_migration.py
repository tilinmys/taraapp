import sqlite3

import pytest

from scripts.migrate_sakhi_sqlite_to_postgres import (
    TABLES,
    TableSnapshot,
    _copy_snapshot,
    counts,
    read_sqlite_snapshot,
)


def test_snapshot_reads_a_consistent_portable_shape(tmp_path):
    path = tmp_path / "sakhi.sqlite3"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE mothers (patient_id TEXT PRIMARY KEY, name TEXT NOT NULL)")
    conn.execute("INSERT INTO mothers VALUES ('M-201', 'Lakshmi')")
    conn.commit()
    conn.close()

    snapshots = read_sqlite_snapshot(path)
    by_name = {snapshot.name: snapshot for snapshot in snapshots}

    assert tuple(by_name) == TABLES
    assert by_name["mothers"].columns == ("patient_id", "name")
    assert by_name["mothers"].rows == (("M-201", "Lakshmi"),)
    assert counts(snapshots)["bp_records"] == 0


def test_missing_source_is_rejected(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_sqlite_snapshot(tmp_path / "missing.sqlite3")


class _Cursor:
    def __init__(self, destination_count=0):
        self.destination_count = destination_count
        self.statements = []
        self.inserted = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        return self

    def executemany(self, sql, rows):
        self.inserted.append((sql, tuple(rows)))

    def fetchone(self):
        return (self.destination_count,)


class _Connection:
    def __init__(self, destination_count=0):
        self.cur = _Cursor(destination_count)

    def cursor(self):
        return self.cur


def test_copy_refuses_a_nonempty_destination():
    conn = _Connection(destination_count=1)
    with pytest.raises(RuntimeError, match="not empty"):
        _copy_snapshot(conn, ())


def test_copy_uses_explicit_columns_and_preserves_ids():
    conn = _Connection(destination_count=0)
    snapshot = TableSnapshot(
        "bp_records",
        ("id", "patient_id", "systolic"),
        ((41, "M-201", 142),),
    )
    _copy_snapshot(conn, (snapshot,))
    sql, rows = conn.cur.inserted[0]
    assert 'INSERT INTO "bp_records" ("id", "patient_id", "systolic")' in sql
    assert rows == ((41, "M-201", 142),)
