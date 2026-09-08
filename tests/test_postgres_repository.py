"""PostgresPatientRepository - the ECS-ready twin of SQLitePatientRepository.

Two tiers, deliberately:

1. Tests that always run, everywhere, with no live database. These catch the
   two failure modes that actually bit this codebase once already (see
   git history / CHANGELOG): the Postgres path silently drifting onto a
   different, disconnected schema, and `build_repository()` routing to a
   class whose constructor or dependency isn't actually there.

2. Integration tests against a *real* Postgres, gated behind
   `SAKHI_TEST_POSTGRES_DSN` and skipped everywhere that variable is unset -
   which is every laptop and CI job that doesn't provision one. Point it at a
   throwaway database (docker `postgres:16`, or an RDS/Aurora dev instance)
   to actually exercise this before trusting it on ECS:

    docker run --rm -e POSTGRES_PASSWORD=test -p 55432:5432 postgres:16
    SAKHI_TEST_POSTGRES_DSN=postgresql://postgres:test@localhost:55432/postgres \\
        python -m pytest tests/test_postgres_repository.py -v

Mirrors tests/test_sql_repository.py's scenarios one-for-one where a live
database is required, so the two backends are proven to agree on behaviour,
not just on schema text.
"""

import asyncio
import os

import pytest

from sakhi_domain import BloodPressure, RiskLevel, SymptomSet
from sakhi_repository import PatientRepository
from sakhi_sql_repository import PostgresPatientRepository, SQLitePatientRepository
from sakhi_triage import TRIAGE

try:
    import psycopg  # noqa: F401
    _PSYCOPG_INSTALLED = True
except ImportError:
    _PSYCOPG_INSTALLED = False

_LIVE_DSN = os.getenv("SAKHI_TEST_POSTGRES_DSN")

skip_without_live_postgres = pytest.mark.skipif(
    not (_PSYCOPG_INSTALLED and _LIVE_DSN),
    reason="set SAKHI_TEST_POSTGRES_DSN to a real Postgres to run these",
)


def _run(coro):
    return asyncio.run(coro)


# --- always-on: no database required ----------------------------------

def test_it_is_a_drop_in_for_the_abc():
    """Constructing it needs no connection yet - load() is what opens the
    pool - so this runs everywhere psycopg is installed."""
    if not _PSYCOPG_INSTALLED:
        pytest.skip("psycopg not installed - the SQLite path never needs it")
    repo = PostgresPatientRepository("postgresql://unused/unused")
    assert isinstance(repo, PatientRepository)
    for method in ("find_patients", "get_patient", "create_patient",
                   "record_blood_pressure", "record_symptoms", "log_teleconsult", "close"):
        assert callable(getattr(repo, method))


def test_method_surface_matches_sqlite_repository_exactly():
    """The two backends must offer the same extra (non-ABC) reporting
    methods `status_server.py`/`agent.py` duck-type against - save_call_report,
    update_recording_status, list_call_reports, set_report_flag, stats,
    flush. A method that exists on one and not the other is exactly the kind
    of drift the class docstring warns about; this test fails loudly instead
    of that showing up as an AttributeError on a live emergency call after an
    ECS cutover."""
    if not _PSYCOPG_INSTALLED:
        pytest.skip("psycopg not installed - the SQLite path never needs it")
    sqlite_public = {
        name for name in dir(SQLitePatientRepository)
        if not name.startswith("_")
    }
    postgres_public = {
        name for name in dir(PostgresPatientRepository)
        if not name.startswith("_")
    }
    assert sqlite_public == postgres_public, (
        f"only on SQLite: {sqlite_public - postgres_public}; "
        f"only on Postgres: {postgres_public - sqlite_public}"
    )


def test_flaggable_fields_match_sqlite_repository():
    """A field flaggable on one backend and not the other means a doctor's
    flag silently fails after a cutover, rather than the same behaviour on
    both."""
    if not _PSYCOPG_INSTALLED:
        pytest.skip("psycopg not installed - the SQLite path never needs it")
    assert PostgresPatientRepository.FLAGGABLE_FIELDS == SQLitePatientRepository.FLAGGABLE_FIELDS


def test_empty_dsn_raises_at_construction_not_deep_inside_the_pool(monkeypatch):
    """SAKHI_REPOSITORY=postgres set without DATABASE_URL/POSTGRES_URL used to
    construct PostgresPatientRepository(None) and fail confusingly inside
    AsyncConnectionPool. Must now fail immediately, at the constructor, with
    a message that says what to set."""
    if not _PSYCOPG_INSTALLED:
        pytest.skip("psycopg not installed - the SQLite path never needs it")
    with pytest.raises(ValueError, match="DATABASE_URL"):
        PostgresPatientRepository(None)
    with pytest.raises(ValueError, match="DATABASE_URL"):
        PostgresPatientRepository("")


def test_missing_psycopg_raises_a_clear_error_not_an_import_crash(monkeypatch):
    """DATABASE_URL set on a box that never got `pip install -r
    requirements.txt` (a stale ECS task definition, a Dockerfile that skipped
    a layer) must fail with an actionable message the moment the repository
    is constructed - not an ImportError three modules deep, and not a
    successful import that then does nothing."""
    import sakhi_sql_repository as mod
    monkeypatch.setattr(mod, "psycopg", None)
    monkeypatch.setattr(mod, "AsyncConnectionPool", None)
    with pytest.raises(RuntimeError, match="psycopg"):
        mod.PostgresPatientRepository("postgresql://unused/unused")


def test_build_repository_routes_to_postgres_when_database_url_is_set(monkeypatch):
    """The regression this whole feature was rebuilt to close: DATABASE_URL
    must actually select PostgresPatientRepository, not silently stay on
    SQLite (a config that looks like it moved to Postgres but didn't) and not
    crash build_repository() itself (a config that breaks the worker on
    boot). No live connection happens here - load() is never called - so
    this runs everywhere psycopg is installed, with no database."""
    if not _PSYCOPG_INSTALLED:
        pytest.skip("psycopg not installed - the SQLite path never needs it")
    import agent
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused:unused@localhost/unused")
    monkeypatch.delenv("SAKHI_REPOSITORY", raising=False)
    repo = agent.build_repository()
    assert isinstance(repo, PostgresPatientRepository)


def test_build_repository_stays_on_sqlite_with_no_postgres_url(monkeypatch, tmp_path):
    """The inverse regression: nothing about adding Postgres support may
    change the default (single-EC2 pilot) path when neither DATABASE_URL nor
    POSTGRES_URL is set."""
    import agent
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.delenv("SAKHI_REPOSITORY", raising=False)
    monkeypatch.setenv("SAKHI_DB_PATH", str(tmp_path / "t.sqlite3"))
    repo = agent.build_repository()
    assert isinstance(repo, SQLitePatientRepository)


# --- integration: requires a real Postgres (SAKHI_TEST_POSTGRES_DSN) ---
#
# `pg_repo` intentionally does NOT open or close the pool itself (that would
# put setup on one asyncio.run() loop, the test body on a second, and
# teardown on a third - exactly the cross-loop mismatch PostgresRepository's
# _ensure_pool() exists to survive in production, but a test fixture calling
# close() from a loop the pool was never opened on would just fail instead of
# self-healing). Construction alone does no I/O (see class docstring), so
# each test's own single `_run(go())` call is the only loop involved -
# opening the pool on first use and closing it at the end of the same coroutine.

@pytest.fixture
def pg_repo():
    if not (_PSYCOPG_INSTALLED and _LIVE_DSN):
        pytest.skip("set SAKHI_TEST_POSTGRES_DSN to a real Postgres to run these")

    # Truncate the clinical tables before every test. Without this, whatever
    # a previous run of this file left behind (or a previous test in the same
    # run) is still there - this was found for real, not hypothesised: a
    # first pass left "Asha Bai" registered from test_phone_lookup_normalises,
    # and the next run's test_phone_lookup_normalises then found 3 matching
    # rows instead of 1, failing on a test that has nothing to do with the
    # bug it looked like it was reporting. CASCADE handles the FK order
    # (bp_records/symptom_records/teleconsults/call_reports all reference
    # mothers.patient_id) in one statement; RESTART IDENTITY resets
    # call_reports' serial id column so id-based assertions are also stable
    # across runs.
    async def _truncate():
        repo = PostgresPatientRepository(_LIVE_DSN, min_size=1, max_size=1)
        pool = await repo._ensure_pool()
        async with pool.connection() as conn:
            await conn.execute(
                "TRUNCATE mothers, bp_records, symptom_records, teleconsults, "
                "call_reports RESTART IDENTITY CASCADE"
            )
        await repo.close()
    _run(_truncate())

    return PostgresPatientRepository(_LIVE_DSN, min_size=1, max_size=2)


@skip_without_live_postgres
def test_every_table_exists_after_load(pg_repo):
    async def go():
        stats = await pg_repo.stats()
        for table in ("mothers", "bp_records", "symptom_records", "teleconsults", "call_reports"):
            assert table in stats
        await pg_repo.close()
    _run(go())


@skip_without_live_postgres
def test_create_find_and_history_round_trip(pg_repo):
    async def go():
        m = await pg_repo.create_patient(name="Suma Gowda", age=24, village="Kamalnagar",
                                          phone="9876543210", gestational_weeks=28)
        assert m.patient_id.startswith("M-")

        bp = BloodPressure(145, 95)
        await pg_repo.record_blood_pressure(m.patient_id, bp, TRIAGE.assess(bp, SymptomSet()))

        found = await pg_repo.find_patients(name="suma")
        assert len(found) == 1
        assert found[0].name == "Suma Gowda"
        assert len(found[0].history) == 1
        assert found[0].history[0]["bp"] == "145/95"
        await pg_repo.close()
    _run(go())


@skip_without_live_postgres
def test_risk_status_and_reading_move_together(pg_repo):
    """Same guarantee as SQLite's version of this test, backed here by a real
    Postgres transaction instead of a single-writer file lock."""
    async def go():
        m = await pg_repo.create_patient(name="Priya Patil", age=29)
        bp = BloodPressure(165, 115)
        assessment = TRIAGE.assess(bp, SymptomSet())
        await pg_repo.record_blood_pressure(m.patient_id, bp, assessment)

        fetched = await pg_repo.get_patient(m.patient_id)
        assert fetched.risk_status is assessment.level
        assert fetched.risk_status is RiskLevel.HIGH_RISK
        await pg_repo.close()
    _run(go())


@skip_without_live_postgres
def test_symptoms_accumulate_and_never_clear(pg_repo):
    async def go():
        m = await pg_repo.create_patient(name="Suma Gowda")
        await pg_repo.record_symptoms(m.patient_id, SymptomSet(headache=True))
        await pg_repo.record_symptoms(m.patient_id, SymptomSet(swelling=True))

        fetched = await pg_repo.get_patient(m.patient_id)
        assert fetched.symptoms.headache, "an earlier reported symptom was erased"
        assert fetched.symptoms.swelling
        await pg_repo.close()
    _run(go())


@skip_without_live_postgres
def test_danger_sign_symptoms_persist(pg_repo):
    async def go():
        m = await pg_repo.create_patient(name="Suma Gowda")
        await pg_repo.record_symptoms(m.patient_id, SymptomSet(vaginal_bleeding=True))
        fetched = await pg_repo.get_patient(m.patient_id)
        assert fetched.symptoms.vaginal_bleeding
        await pg_repo.close()
    _run(go())


@skip_without_live_postgres
def test_a_spoken_name_with_sql_in_it_is_data_not_sql(pg_repo):
    async def go():
        await pg_repo.create_patient(name="Suma'; DROP TABLE mothers; --")
        found = await pg_repo.find_patients(name="Suma'; DROP TABLE mothers; --")
        assert len(found) == 1
        stats = await pg_repo.stats()
        assert "mothers" in stats  # table still exists
        await pg_repo.close()
    _run(go())


@skip_without_live_postgres
def test_phone_lookup_normalises(pg_repo):
    async def go():
        m = await pg_repo.create_patient(name="Asha Bai", phone="+91 98765 43210")
        found = await pg_repo.find_patients(phone="9876543210")
        assert len(found) == 1
        assert found[0].patient_id == m.patient_id
        await pg_repo.close()
    _run(go())


@skip_without_live_postgres
def test_save_list_and_flag_call_reports(pg_repo):
    async def go():
        report_id = await pg_repo.save_call_report({
            "session_id": "SESS-PG-1", "patient_id": "M-999",
            "language": "kn", "risk_level": "ROUTINE",
            "intake_answered": 5, "intake_total": 26,
            "escalated": False, "duration_ms": 12345,
        })
        assert isinstance(report_id, int)

        reports = await pg_repo.list_call_reports(limit=10)
        assert any(r["id"] == report_id for r in reports)

        ok = await pg_repo.set_report_flag(report_id, field="manual_risk_flag", value=True)
        assert ok is True

        with pytest.raises(ValueError):
            await pg_repo.set_report_flag(report_id, field="not_a_real_column", value=True)
        await pg_repo.close()
    _run(go())


@skip_without_live_postgres
def test_teleconsult_log_records_trigger_sign(pg_repo):
    async def go():
        m = await pg_repo.create_patient(name="Suma Gowda")
        bp = BloodPressure(170, 120)
        assessment = TRIAGE.assess(bp, SymptomSet())
        channel_id = await pg_repo.log_teleconsult(
            m.patient_id, "severe BP", assessment, trigger_sign="severe_bp",
        )
        assert channel_id.startswith("SMFMI-")
        await pg_repo.close()
    _run(go())


@skip_without_live_postgres
def test_survives_being_used_on_a_different_loop_than_it_was_loaded_on(pg_repo):
    """Reproduces, against a real Postgres, the exact failure agent.py's
    prewarm_process() would otherwise cause: it opens the repository via
    `asyncio.run(repository.load())`, which creates a throwaway loop, runs
    load() to completion, and destroys that loop the instant it returns -
    then the real per-job work happens later on an entirely different,
    persistent loop (confirmed against the installed livekit-agents 1.6.10
    IPC source). This test does exactly that shape - one asyncio.run() for
    "prewarm", a second, separate one standing in for the job's real loop -
    against a live database, not a mock, so a regression that reintroduces
    eager pool-opening in load() would hang here until PoolTimeout instead of
    only failing once on a real ECS deployment."""
    _run(pg_repo.load())  # prewarm: throwaway loop #1, destroyed on return

    async def real_job():  # the job's own, separate loop
        m = await pg_repo.create_patient(name="Loop Survivor")
        assert m.patient_id.startswith("M-")
        fetched = await pg_repo.get_patient(m.patient_id)
        assert fetched is not None
        await pg_repo.close()

    _run(real_job())  # throwaway loop #2 - must not be loop #1


@skip_without_live_postgres
def test_concurrent_registrations_never_collide_on_patient_id(pg_repo):
    """The race SQLite avoids with a process-level threading.Lock and this
    class avoids instead with a real Postgres SEQUENCE (see class
    docstring's CONCURRENCY MODEL section): many concurrent create_patient()
    calls, each getting its own connection from the pool as the pool is
    designed to hand out, must never compute the same 'next' id twice. A
    SELECT-MAX-based allocator would intermittently fail this under load -
    the whole point of moving to nextval() is that it cannot, regardless of
    concurrency."""
    async def go():
        results = await asyncio.gather(*[
            pg_repo.create_patient(name=f"Concurrent Mother {i}")
            for i in range(20)
        ])
        ids = [r.patient_id for r in results]
        assert len(ids) == len(set(ids)), f"duplicate patient_id allocated: {ids}"
        await pg_repo.close()
    _run(go())
