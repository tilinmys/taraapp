"""The call_reports manual-flag columns must reach a DB file that predates them.

`schema_statements()`'s CREATE TABLE IF NOT EXISTS is a no-op against an
existing table - a database created before manual_risk_flag/flagged_to_doctor/
flagged_by/flagged_at existed would never get them just by starting the worker
again. `SQLitePatientRepository._connect()` runs call_reports_migration_
statements() as a guarded ALTER TABLE step specifically to cover that case;
this file proves the guard actually works and is safe to run repeatedly.
"""

import sqlite3

from sakhi_schema import SQLITE, call_reports_migration_statements, schema_statements

# The exact call_reports shape that existed before the manual-flag columns
# were added to schema_statements()'s own DDL. schema_statements() itself can
# no longer be used to build a "pre-migration" table for this test, since it
# now creates the columns directly - that's the fresh-DB path, covered
# separately below. This literal DDL is what call_reports actually looked
# like on disk for any database created before this change.
_OLD_CALL_REPORTS_DDL = """
CREATE TABLE IF NOT EXISTS call_reports (
    id              INTEGER PRIMARY KEY,
    session_id      TEXT,
    patient_id      TEXT,
    language        TEXT,
    risk_level      TEXT,
    intake_answered INTEGER NOT NULL DEFAULT 0,
    intake_total    INTEGER NOT NULL DEFAULT 0,
    escalated       INTEGER NOT NULL DEFAULT 0,
    duration_ms     INTEGER,
    created_at      TEXT NOT NULL
)
"""


def _pre_migration_db(path):
    conn = sqlite3.connect(str(path))
    conn.execute(_OLD_CALL_REPORTS_DDL)
    conn.commit()
    return conn


def test_migration_adds_the_missing_columns(tmp_path):
    conn = _pre_migration_db(tmp_path / "old.sqlite3")
    before = {row[1] for row in conn.execute("PRAGMA table_info(call_reports)")}
    assert "manual_risk_flag" not in before, "test setup should predate the columns"

    for statement in call_reports_migration_statements(SQLITE):
        conn.execute(statement)
    conn.commit()

    after = {row[1] for row in conn.execute("PRAGMA table_info(call_reports)")}
    for col in ("manual_risk_flag", "flagged_to_doctor", "flagged_by", "flagged_at"):
        assert col in after
    conn.close()


def test_migration_is_idempotent(tmp_path):
    conn = _pre_migration_db(tmp_path / "old.sqlite3")

    for _ in range(2):
        for statement in call_reports_migration_statements(SQLITE):
            try:
                conn.execute(statement)
            except sqlite3.OperationalError as exc:
                assert "duplicate column name" in str(exc), (
                    f"unexpected failure on repeat migration: {exc}"
                )
        conn.commit()

    after = {row[1] for row in conn.execute("PRAGMA table_info(call_reports)")}
    assert "manual_risk_flag" in after
    conn.close()


def test_fresh_db_gets_the_columns_directly_from_ddl(tmp_path):
    """A brand-new database (this session's own SQLitePatientRepository.load(),
    or any tmp_path-based test fixture) must not need the migration step at
    all - the CREATE TABLE DDL already includes these columns."""
    conn = sqlite3.connect(str(tmp_path / "fresh.sqlite3"))
    for statement in schema_statements(SQLITE):
        conn.execute(statement)
    conn.commit()
    columns = {row[1] for row in conn.execute("PRAGMA table_info(call_reports)")}
    for col in ("manual_risk_flag", "flagged_to_doctor", "flagged_by", "flagged_at"):
        assert col in columns
    conn.close()
