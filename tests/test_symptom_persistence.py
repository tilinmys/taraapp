"""Every symptom SymptomSet knows about must survive a database round trip.

Regression for a real clinical data-loss bug (found 2026-09-24): the ANC
rewrite added seven SymptomSet fields - contractions, upper_abdominal_pain,
fever, urinary_symptoms, breathlessness, palpitations, severe_vomiting - and
TriageEngine escalated on them, but the symptom column list was hand-typed in
six places across sakhi_schema.py and sakhi_sql_repository.py and none of
them was updated. A mother sent to hospital for fever had no record of the
fever. The column list is now generated from sakhi_schema.SYMPTOM_COLUMNS,
and the first test below is what keeps it equal to SymptomSet.

Runs against SQLite always, and against Postgres/Supabase too when
SAKHI_TEST_POSTGRES_DSN is set (same convention as test_postgres_repository).
"""

import asyncio
import dataclasses
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

from sakhi_domain import SymptomSet
from sakhi_schema import SQLITE, SYMPTOM_COLUMNS, danger_sign_columns_migration_statements
from sakhi_sql_repository import PostgresPatientRepository, SQLitePatientRepository


def _run(coro):
    return asyncio.run(coro)


_LIVE_DSN = os.getenv("SAKHI_TEST_POSTGRES_DSN")


def _backends():
    params = ["sqlite"]
    if _LIVE_DSN:
        params.append("postgres")
    return params


async def _open(kind: str):
    if kind == "sqlite":
        return await SQLitePatientRepository(Path(tempfile.mkdtemp()) / "s.sqlite3").load()
    return await PostgresPatientRepository(_LIVE_DSN).load()


def test_symptom_columns_equal_symptomset_fields():
    """The guard that makes the original bug impossible to reintroduce."""
    assert SYMPTOM_COLUMNS == tuple(f.name for f in dataclasses.fields(SymptomSet))


@pytest.mark.parametrize("kind", _backends())
@pytest.mark.parametrize("column", SYMPTOM_COLUMNS)
def test_every_symptom_round_trips(kind, column):
    async def go():
        repo = await _open(kind)
        try:
            mother = await repo.create_patient(name=f"RoundTrip {column}", age=25)
            await repo.record_symptoms(mother.patient_id, SymptomSet(**{column: True}))
            loaded = await repo.get_patient(mother.patient_id)
            assert getattr(loaded.symptoms, column) is True, f"{column} was not persisted"
            others = [c for c in SYMPTOM_COLUMNS if c != column and getattr(loaded.symptoms, c)]
            assert others == [], f"unrelated symptoms set: {others}"
        finally:
            await repo.close()
    _run(go())


@pytest.mark.parametrize("kind", _backends())
def test_symptoms_accumulate_and_a_later_no_never_erases(kind):
    async def go():
        repo = await _open(kind)
        try:
            mother = await repo.create_patient(name="Accumulate Test", age=24)
            await repo.record_symptoms(mother.patient_id, SymptomSet(fever=True))
            await repo.record_symptoms(mother.patient_id, SymptomSet(breathlessness=True))
            await repo.record_symptoms(mother.patient_id, SymptomSet())  # "no symptoms"
            loaded = await repo.get_patient(mother.patient_id)
            assert loaded.symptoms.fever and loaded.symptoms.breathlessness
        finally:
            await repo.close()
    _run(go())


def test_an_existing_database_is_widened_on_startup():
    """A pilot database created before this fix has only the original seven
    symptom columns. Opening it must add the rest without losing data."""
    path = Path(tempfile.mkdtemp()) / "old.sqlite3"
    conn = sqlite3.connect(path)
    old_cols = ", ".join(f"{c} INTEGER NOT NULL DEFAULT 0" for c in SYMPTOM_COLUMNS[:7])
    conn.execute(
        "CREATE TABLE mothers (patient_id TEXT PRIMARY KEY, name TEXT NOT NULL, age INTEGER, "
        "village TEXT, phone TEXT, gestational_weeks INTEGER, "
        f"risk_status TEXT NOT NULL DEFAULT 'ROUTINE', {old_cols}, "
        "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO mothers (patient_id, name, headache, created_at, updated_at) "
        "VALUES ('M-201', 'Old Record', 1, 'x', 'x')"
    )
    conn.execute(
        "CREATE TABLE symptom_records (id INTEGER PRIMARY KEY, patient_id TEXT NOT NULL "
        f"REFERENCES mothers(patient_id), {old_cols}, recorded_at TEXT NOT NULL)"
    )
    conn.commit()
    conn.close()

    async def go():
        repo = await SQLitePatientRepository(path).load()
        try:
            await repo.record_symptoms("M-201", SymptomSet(fever=True))
            loaded = await repo.get_patient("M-201")
            assert loaded.symptoms.headache, "pre-existing data lost during migration"
            assert loaded.symptoms.fever, "new column not added to the old database"
        finally:
            await repo.close()
    _run(go())


def test_migration_is_idempotent():
    """Running the ALTERs twice (every worker restart) must be harmless."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE mothers (patient_id TEXT PRIMARY KEY)")
    conn.execute("CREATE TABLE symptom_records (id INTEGER PRIMARY KEY)")
    for _ in range(2):
        for stmt in danger_sign_columns_migration_statements(SQLITE):
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError as exc:
                assert "duplicate column name" in str(exc)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(mothers)")}
    assert set(SYMPTOM_COLUMNS[3:]) <= cols
