"""SQL-backed PatientRepository. Drop-in replacement for the JSON mock.

Implements the same `PatientRepository` ABC, so swapping storage changes no
call site in `sakhi_tools.py` or `agent.py` - which is the reason that ABC
exists.

WHY THIS REPLACES THE JSON MOCK
--------------------------------
`MockSixtyDBRepository` keeps everything in memory and rewrites the whole file
write-behind. That is fine for a demo and wrong for a pilot:

  - **Lost writes.** Two concurrent calls both hold the full dict, both
    serialise it, and the second overwrites the first. The loser's blood
    pressure is gone with no error anywhere.
  - **No crash durability.** A reading recorded 400ms before the worker
    process dies was never on disk. On a pre-eclampsia screening call, that is
    the reading that mattered.
  - **No constraints.** Nothing stops an orphan BP row or a duplicate mother.

SQLite fixes all three with real transactions, and the schema is written to
move to Postgres unchanged (see `sakhi_schema.py`).

THREADING
---------
`sqlite3` is blocking. This runs on the LiveKit event loop, where a blocked
loop stalls audio for every concurrent call on the worker - so every database
call goes through `asyncio.to_thread`. The connection is created with
`check_same_thread=False` and guarded by a lock, because the thread-pool worker
that runs a query is not guaranteed to be the one that opened the connection.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sakhi_domain import BloodPressure, RiskAssessment, RiskLevel, SymptomSet
from sakhi_repository import PatientRecord, PatientRepository, normalize_phone
from sakhi_schema import (
    POSTGRES,
    SQLITE,
    SQLITE_PRAGMAS,
    call_reports_migration_statements,
    danger_sign_columns_migration_statements,
    recording_columns_migration_statements,
    schema_statements,
    teleconsults_migration_statements,
)

# Optional: only PostgresPatientRepository needs this, and only when
# DATABASE_URL/POSTGRES_URL is actually set (see agent.py's build_repository).
# A machine running the SQLite path - every dev box, and the current single
# EC2 pilot deployment - never imports psycopg and must not fail to import
# this module because it isn't installed.
try:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool
except ImportError:
    psycopg = None
    dict_row = None
    AsyncConnectionPool = None

logger = logging.getLogger("sakhi.sql")


def _utc_now() -> str:
    """ISO-8601 UTC. Stored as TEXT so it ports to timestamptz in one cast."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SQLitePatientRepository(PatientRepository):
    """SQLite implementation of the persistence seam."""

    def __init__(self, db_path: str | Path = "data/sakhi.sqlite3") -> None:
        self._path = Path(db_path)
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._dialect = SQLITE

    # --- lifecycle -----------------------------------------------------

    async def load(self) -> "SQLitePatientRepository":
        """Open the database and apply the schema. Idempotent."""
        await asyncio.to_thread(self._connect)
        return self

    def _connect(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._path), check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        for pragma in SQLITE_PRAGMAS:
            conn.execute(pragma)
        for statement in schema_statements(self._dialect):
            conn.execute(statement)
        # Poor-man's migration: widens a call_reports table created before the
        # manual-flag columns existed. CREATE TABLE IF NOT EXISTS above is a
        # no-op against a pre-existing file, so a fresh DB gets these columns
        # from the DDL and an existing one gets them here. No schema-version
        # table exists to check first, so we just attempt each ADD COLUMN and
        # swallow the "already there" case - safe to run on every startup.
        for statement in call_reports_migration_statements(self._dialect):
            try:
                conn.execute(statement)
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc):
                    raise
        # Same poor-man's migration for teleconsults.trigger_sign (2026-08-30).
        for statement in teleconsults_migration_statements(self._dialect):
            try:
                conn.execute(statement)
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc):
                    raise
        # Same poor-man's migration for mothers/symptom_records' two new
        # Tier-1 danger-sign columns (2026-08-30).
        for statement in danger_sign_columns_migration_statements(self._dialect):
            try:
                conn.execute(statement)
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc):
                    raise
        # Same poor-man's migration for call_reports' recording_status/
        # recording_key columns (2026-09-02, LiveKit egress-to-S3 wiring).
        for statement in recording_columns_migration_statements(self._dialect):
            try:
                conn.execute(statement)
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc):
                    raise
        conn.commit()
        self._conn = conn
        logger.info("SQLite repository ready at %s", self._path)

    async def flush(self) -> None:
        """Commit anything outstanding.

        Part of the repository contract the agent calls at the end of every
        call. The JSON mock needed it to drain its write-behind buffer; here
        each write already commits, so this only has to be safe and present.
        Its absence raised AttributeError on every single call teardown.
        """
        def _commit() -> None:
            with self._lock:
                if self._conn is not None:
                    self._conn.commit()
        await asyncio.to_thread(_commit)

    async def close(self) -> None:
        def _close() -> None:
            with self._lock:
                if self._conn is not None:
                    self._conn.commit()
                    self._conn.close()
                    self._conn = None
        await asyncio.to_thread(_close)

    # --- internals -----------------------------------------------------

    def _require(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("repository used before load()")
        return self._conn

    def _query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._require().execute(self._dialect.render(sql), params).fetchall()

    def _execute(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            conn = self._require()
            conn.execute(self._dialect.render(sql), params)
            conn.commit()

    @staticmethod
    def _to_record(row: sqlite3.Row, history: list[dict[str, Any]] | None = None) -> PatientRecord:
        return PatientRecord(
            patient_id=row["patient_id"],
            name=row["name"],
            age=row["age"],
            village=row["village"],
            phone=row["phone"],
            gestational_weeks=row["gestational_weeks"],
            risk_status=RiskLevel(row["risk_status"]) if row["risk_status"] else RiskLevel.ROUTINE,
            symptoms=SymptomSet(
                headache=bool(row["headache"]),
                blurry_vision=bool(row["blurry_vision"]),
                swelling=bool(row["swelling"]),
                # keys() guard: rows from a DB that hasn't run the 2026-08-30
                # migration yet (only possible mid-restart, before _connect()
                # has run) would KeyError on these two without it.
                vaginal_bleeding=bool(row["vaginal_bleeding"]) if "vaginal_bleeding" in row.keys() else False,
                severe_abdominal_pain=bool(row["severe_abdominal_pain"]) if "severe_abdominal_pain" in row.keys() else False,
                convulsions=bool(row["convulsions"]) if "convulsions" in row.keys() else False,
                reduced_fetal_movement=bool(row["reduced_fetal_movement"]) if "reduced_fetal_movement" in row.keys() else False,
            ),
            history=history or [],
        )

    def _history(self, patient_id: str) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT systolic, diastolic, risk_level, recorded_at FROM bp_records "
            "WHERE patient_id = ? ORDER BY id",
            (patient_id,),
        )
        return [
            {"date": r["recorded_at"], "bp": f"{r['systolic']}/{r['diastolic']}",
             "risk": r["risk_level"]}
            for r in rows
        ]

    def _next_patient_id(self) -> str:
        """Human-readable M-NNN ids, matching the existing dataset.

        Derived from the current maximum rather than a counter so it survives
        restarts. Called inside create_patient's lock, so two concurrent
        registrations cannot collide on the same number.
        """
        rows = self._query("SELECT patient_id FROM mothers WHERE patient_id LIKE 'M-%'")
        highest = 200
        for row in rows:
            try:
                highest = max(highest, int(str(row["patient_id"]).split("-", 1)[1]))
            except (IndexError, ValueError):
                continue
        return f"M-{highest + 1}"

    # --- PatientRepository ---------------------------------------------

    async def find_patients(
        self, *, name: str | None = None, phone: str | None = None
    ) -> list[PatientRecord]:
        def _find() -> list[PatientRecord]:
            if phone:
                normalised = normalize_phone(phone)
                rows = self._query("SELECT * FROM mothers WHERE phone = ?", (normalised,))
                return [self._to_record(r, self._history(r["patient_id"])) for r in rows]
            if not name or not name.strip():
                return []
            # Parameterised LIKE: the pattern is bound, never concatenated, so
            # a spoken name containing a quote or a wildcard is data, not SQL.
            rows = self._query(
                "SELECT * FROM mothers WHERE name LIKE ? COLLATE NOCASE ORDER BY name",
                (f"%{name.strip()}%",),
            )
            return [self._to_record(r, self._history(r["patient_id"])) for r in rows]

        return await asyncio.to_thread(_find)

    async def get_patient(self, patient_id: str) -> PatientRecord | None:
        def _get() -> PatientRecord | None:
            rows = self._query("SELECT * FROM mothers WHERE patient_id = ?", (patient_id,))
            if not rows:
                return None
            return self._to_record(rows[0], self._history(patient_id))

        return await asyncio.to_thread(_get)

    async def create_patient(
        self,
        *,
        name: str,
        age: int | None = None,
        gestational_weeks: int | None = None,
        village: str | None = None,
        phone: str | None = None,
    ) -> PatientRecord:
        def _create() -> PatientRecord:
            now = _utc_now()
            with self._lock:
                conn = self._require()
                patient_id = self._next_patient_id_locked(conn)
                conn.execute(
                    "INSERT INTO mothers (patient_id, name, age, village, phone, "
                    "gestational_weeks, risk_status, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (patient_id, name, age, village,
                     normalize_phone(phone) if phone else None,
                     gestational_weeks, RiskLevel.ROUTINE.value, now, now),
                )
                conn.commit()
            return PatientRecord(
                patient_id=patient_id, name=name, age=age, village=village,
                phone=normalize_phone(phone) if phone else None,
                gestational_weeks=gestational_weeks, risk_status=RiskLevel.ROUTINE,
            )

        return await asyncio.to_thread(_create)

    def _next_patient_id_locked(self, conn: sqlite3.Connection) -> str:
        """Id allocation while already holding the lock (see create_patient)."""
        rows = conn.execute("SELECT patient_id FROM mothers WHERE patient_id LIKE 'M-%'").fetchall()
        highest = 200
        for row in rows:
            try:
                highest = max(highest, int(str(row["patient_id"]).split("-", 1)[1]))
            except (IndexError, ValueError):
                continue
        return f"M-{highest + 1}"

    async def record_blood_pressure(
        self, patient_id: str, bp: BloodPressure, assessment: RiskAssessment
    ) -> None:
        def _record() -> None:
            now = _utc_now()
            with self._lock:
                conn = self._require()
                # One transaction: the reading and the mother's risk level move
                # together or not at all. A committed reading with a stale risk
                # status is the de-sync that made triage disagree with the
                # record it was computed from.
                conn.execute(
                    "INSERT INTO bp_records (patient_id, systolic, diastolic, "
                    "risk_level, recorded_at) VALUES (?, ?, ?, ?, ?)",
                    (patient_id, bp.systolic, bp.diastolic, assessment.level.value, now),
                )
                conn.execute(
                    "UPDATE mothers SET risk_status = ?, updated_at = ? WHERE patient_id = ?",
                    (assessment.level.value, now, patient_id),
                )
                conn.commit()

        await asyncio.to_thread(_record)

    async def record_symptoms(self, patient_id: str, symptoms: SymptomSet) -> None:
        def _record() -> None:
            now = _utc_now()
            with self._lock:
                conn = self._require()
                conn.execute(
                    "INSERT INTO symptom_records (patient_id, headache, blurry_vision, "
                    "swelling, vaginal_bleeding, severe_abdominal_pain, convulsions, "
                    "reduced_fetal_movement, recorded_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (patient_id, int(symptoms.headache), int(symptoms.blurry_vision),
                     int(symptoms.swelling), int(symptoms.vaginal_bleeding),
                     int(symptoms.severe_abdominal_pain), int(symptoms.convulsions),
                     int(symptoms.reduced_fetal_movement), now),
                )
                # Symptoms only ever accumulate (OR), never clear: a later
                # "no headache" must not erase a headache the mother reported
                # earlier in the same call.
                conn.execute(
                    "UPDATE mothers SET headache = CASE WHEN headache = 1 OR ? = 1 THEN 1 ELSE 0 END, "
                    "blurry_vision = CASE WHEN blurry_vision = 1 OR ? = 1 THEN 1 ELSE 0 END, "
                    "swelling = CASE WHEN swelling = 1 OR ? = 1 THEN 1 ELSE 0 END, "
                    "vaginal_bleeding = CASE WHEN vaginal_bleeding = 1 OR ? = 1 THEN 1 ELSE 0 END, "
                    "severe_abdominal_pain = CASE WHEN severe_abdominal_pain = 1 OR ? = 1 THEN 1 ELSE 0 END, "
                    "convulsions = CASE WHEN convulsions = 1 OR ? = 1 THEN 1 ELSE 0 END, "
                    "reduced_fetal_movement = CASE WHEN reduced_fetal_movement = 1 OR ? = 1 THEN 1 ELSE 0 END, "
                    "updated_at = ? WHERE patient_id = ?",
                    (int(symptoms.headache), int(symptoms.blurry_vision),
                     int(symptoms.swelling), int(symptoms.vaginal_bleeding),
                     int(symptoms.severe_abdominal_pain), int(symptoms.convulsions),
                     int(symptoms.reduced_fetal_movement), now, patient_id),
                )
                conn.commit()

        await asyncio.to_thread(_record)

    async def log_teleconsult(
        self,
        patient_id: str,
        reason: str,
        assessment: RiskAssessment | None = None,
        *,
        trigger_sign: str | None = None,
    ) -> str:
        def _log() -> str:
            now = _utc_now()
            channel_id = f"SMFMI-{int(datetime.now(timezone.utc).timestamp())}"
            self._execute(
                "INSERT INTO teleconsults (patient_id, channel_id, reason, risk_level, "
                "trigger_sign, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (patient_id, channel_id, reason,
                 assessment.level.value if assessment else None, trigger_sign, now),
            )
            return channel_id

        return await asyncio.to_thread(_log)

    # --- reporting (used by the dashboard, not on the voice hot path) ---

    async def save_call_report(self, report: dict[str, Any]) -> int:
        def _save() -> int:
            with self._lock:
                conn = self._require()
                cur = conn.execute(
                    "INSERT INTO call_reports (session_id, patient_id, language, "
                    "risk_level, intake_answered, intake_total, escalated, duration_ms, "
                    "manual_risk_flag, flagged_to_doctor, flagged_by, flagged_at, "
                    "recording_status, recording_key, "
                    "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (report.get("session_id"), report.get("patient_id"),
                     report.get("language"), report.get("risk_level"),
                     int(report.get("intake_answered") or 0),
                     int(report.get("intake_total") or 0),
                     int(bool(report.get("escalated"))),
                     report.get("duration_ms"),
                     0, 0, None, None,
                     report.get("recording_status") or "none",
                     report.get("recording_key"),
                     _utc_now()),
                )
                conn.commit()
                return int(cur.lastrowid)

        return await asyncio.to_thread(_save)

    async def update_recording_status(self, session_id: str, status: str) -> None:
        """Write-through cache for the dashboard's S3-confirmation check
        (status_server.py): once it HEADs the object and finds it actually
        landed, it flips 'stopped' -> 'available' here so it doesn't have to
        re-check S3 on every single page load. Looked up by session_id, not
        row id - status_server.py's check runs per-report from a SELECT *,
        which already has session_id, not a second id round trip."""
        def _update() -> None:
            with self._lock:
                conn = self._require()
                conn.execute(
                    "UPDATE call_reports SET recording_status = ? WHERE session_id = ?",
                    (status, session_id),
                )
                conn.commit()

        await asyncio.to_thread(_update)

    # Column name is never taken from a caller (e.g. an HTTP body) without
    # being checked against this set first - interpolating an unvalidated
    # column name into SQL text is an injection path even though the value
    # itself is parameterised.
    FLAGGABLE_FIELDS = ("manual_risk_flag", "flagged_to_doctor")

    async def list_call_reports(self, limit: int = 50) -> list[dict[str, Any]]:
        def _list() -> list[dict[str, Any]]:
            rows = self._query(
                "SELECT * FROM call_reports ORDER BY id DESC LIMIT ?", (limit,)
            )
            return [dict(row) for row in rows]

        return await asyncio.to_thread(_list)

    async def set_report_flag(
        self, report_id: int, *, field: str, value: bool, flagged_by: str | None = None
    ) -> bool:
        if field not in self.FLAGGABLE_FIELDS:
            raise ValueError(f"not a flaggable field: {field!r}")

        def _set() -> bool:
            with self._lock:
                conn = self._require()
                cur = conn.execute(
                    f"UPDATE call_reports SET {field} = ?, flagged_by = ?, "
                    f"flagged_at = ? WHERE id = ?",
                    (int(bool(value)), flagged_by, _utc_now(), report_id),
                )
                conn.commit()
                return cur.rowcount > 0

        return await asyncio.to_thread(_set)

    async def stats(self) -> dict[str, Any]:
        """Counts for the dashboard's database health panel."""
        def _stats() -> dict[str, Any]:
            out: dict[str, Any] = {"backend": "sqlite", "path": str(self._path)}
            for table in ("mothers", "bp_records", "symptom_records",
                          "teleconsults", "call_reports"):
                rows = self._query(f"SELECT COUNT(*) AS n FROM {table}")
                out[table] = int(rows[0]["n"]) if rows else 0
            return out

        return await asyncio.to_thread(_stats)


class PostgresPatientRepository(PatientRepository):
    """Postgres implementation of the same persistence seam, for ECS.

    WHY THIS IS THE SAME SQL AS SQLitePatientRepository, NOT A REWRITE
    --------------------------------------------------------------------
    Every query string below is copy-identical to the one in
    `SQLitePatientRepository` - same neutral `?` placeholders, same column
    list, same WHERE clause - with exactly one deliberate exception:
    `find_patients`'s name search uses `ILIKE`, because SQLite's
    `COLLATE NOCASE` has no Postgres equivalent and Postgres's `ILIKE` has no
    SQLite equivalent; that single clause is dialect-specific by necessity,
    every other statement is shared text. `sakhi_schema.py`'s `Dialect.render()`
    is what turns `?` into `%s` for this backend; the query text itself never
    diverges between the two repositories anywhere else. That is deliberate: two
    independently hand-written SQL strings against the same table drift the
    moment one of them gets a bugfix the other doesn't (this is exactly the
    mistake found and reverted in the previous version of this module, which
    duplicated an entire parallel `patients`/`call_telemetry` schema instead
    of pointing a real Postgres client at the mothers/bp_records/
    symptom_records/teleconsults/call_reports tables `sakhi_schema.py`
    already defines).

    `mothers`/`bp_records`/etc. themselves come from `schema_statements(POSTGRES)`
    - the exact same function `SQLitePatientRepository` calls with `SQLITE` -
    so the two backends are guaranteed to agree on structure, not just by
    convention but because they run the same DDL generator.

    DRIVER CHOICE
    -------------
    psycopg (v3), not asyncpg: `sakhi_schema.py`'s `POSTGRES` dialect was
    already defined with `%s` placeholders (psycopg's style) before this class
    existed, specifically so `Dialect.render()` could be reused verbatim here.
    asyncpg needs `$1, $2, ...` positional placeholders, which would have
    forced either a second placeholder-rendering path in `sakhi_schema.py` or
    hand-written query text that no longer matches SQLite's - the drift risk
    above, reintroduced. `psycopg[binary,pool]` ships a maintained async
    connection pool (`AsyncConnectionPool`), which is the actual requirement
    for ECS: many task instances, each pooling connections to one RDS/Aurora
    Postgres endpoint, instead of SQLite's single local file no two ECS tasks
    can share.

    THE POOL CANNOT OPEN DURING agent.py's prewarm_process() - WHY, AND WHAT
    THAT MEANS FOR load()
    --------------------------------------------------------------------------
    `AsyncConnectionPool.open()` binds its internal queue, scheduler, and
    background worker tasks to whichever asyncio event loop is running at
    that moment (psycopg_pool's own source comment: "Create these objects now
    to attach them to the right loop"). `agent.py`'s `prewarm_process()` calls
    `asyncio.run(repository.load())` - `asyncio.run()` creates a throwaway
    loop, runs the coroutine, and destroys that loop the instant it returns.
    Worse, verified against the installed livekit-agents 1.6.10 source
    (`livekit.agents.ipc.proc_client._ProcClient.initialize()` /`.run()`):
    `prewarm_process()` actually runs with NO event loop at all (plain
    synchronous call, before `_ProcClient.run()` ever calls
    `asyncio.new_event_loop()`), and the real per-job work
    (`entrypoint_fnc`, where this repository actually gets used) runs later
    on a SEPARATE, persistent loop that `_ProcClient.run()` creates
    afterward and reuses for every job that process handles. A pool opened
    during prewarm would be bound to a loop that is already gone by the time
    any real call needs it - every query would hang until `PoolTimeout`.

    So `load()` deliberately does no I/O and opens no pool - it is safe to
    call from prewarm (or not call at all) precisely because it does nothing
    loop-sensitive. Every query method below calls `_ensure_pool()` instead,
    which opens (or transparently reopens) the pool bound to whichever loop
    is ACTUALLY running the current call - correct the first time
    `entrypoint_fnc`'s real, persistent loop makes a query, and correct again
    if this repository object is ever reused across more than one such loop.
    The cost: prewarm can no longer hide Postgres connection latency ahead of
    the first real call the way it does for SQLite (`sqlite3.Connection` has
    no loop affinity, so prewarming it has no such restriction) - the first
    query in a process pays the pool-open + schema cost. Given that cost is
    milliseconds against a warm RDS/Aurora instance, that is the correct
    trade for "the pool actually works" over "the pool prewarms but hangs".

    CONCURRENCY MODEL ON ECS
    -------------------------
    Real row-level locking and MVCC, unlike SQLite's database-level lock
    (see `sakhi_schema.py`'s CONCURRENCY MODEL note). Every multi-statement
    write below (a BP insert + the mother's risk_status update; a symptoms
    insert + the mother's OR-accumulated flags) runs inside one
    `pool.connection()` context, which psycopg commits as a single transaction
    on clean exit and rolls back on any exception - the same "reading and risk
    level move together or not at all" guarantee `SQLitePatientRepository`
    gives via `conn.commit()` after both statements, just backed by real
    per-transaction ACID instead of a single-writer file lock.

    Patient ids are allocated from a real Postgres `SEQUENCE`
    (`mothers_patient_id_seq`), not a `SELECT MAX(...)` read followed by an
    INSERT - the latter race (two concurrent registrations both reading the
    same max before either commits, both computing the same "next" id, the
    second INSERT dying on a duplicate primary key) is exactly what
    SQLite's version of this avoids with a process-level `threading.Lock`
    around the whole read-then-write, and there is no equivalent single lock
    to reuse here: two concurrent `create_patient()` calls get two different
    connections from the pool, on purpose, because serialising all writes
    through one connection is the SQLite-only compromise this class exists to
    remove. `nextval()` is atomic at the database level regardless of how
    many connections call it concurrently.

    WHAT THIS DOES NOT COVER
    -------------------------
    `status_server.py`'s dashboard (`list_reports`, `mcp_card_for_report`,
    `set_report_flag`, `maternal_db_status`) opens its own raw `sqlite3`
    connection straight to `SAKHI_DB_PATH` - it does not go through
    `PatientRepository` at all. Pointing the agent at Postgres via
    `DATABASE_URL` makes every clinical write and read here Postgres-backed;
    it does NOT make the dashboard Postgres-aware. That is a separate,
    still-open piece of work before an ECS cutover is complete - flagged here
    rather than silently left for someone to discover in production.

    Windows: `psycopg`'s async mode refuses to run under
    `asyncio.ProactorEventLoop` (confirmed against the installed psycopg
    source - it raises `InterfaceError` at connect time), which is Python's
    default loop on Windows and is what this worker actually uses locally
    (`Using proactor: IocpProactor` in this project's own dev logs).
    `_ensure_pool()` below turns that into one clear log line instead of a
    query silently hanging or a cryptic driver error three calls later. It
    does not affect the ECS/Linux target, which uses the default selector
    loop.
    """

    FLAGGABLE_FIELDS = ("manual_risk_flag", "flagged_to_doctor")

    def __init__(self, dsn: str, *, min_size: int = 2, max_size: int = 10) -> None:
        if psycopg is None or AsyncConnectionPool is None:
            raise RuntimeError(
                "PostgresPatientRepository requires the 'psycopg[binary,pool]' "
                "package (see requirements.txt). It is not installed - "
                "DATABASE_URL/POSTGRES_URL was set but the driver was not."
            )
        if not dsn or not dsn.strip():
            # Silent-`None` was the actual failure mode this replaces: an
            # operator setting SAKHI_REPOSITORY=postgres without DATABASE_URL
            # used to get PostgresPatientRepository(None), which failed deep
            # inside AsyncConnectionPool with a confusing error far from the
            # actual misconfiguration.
            raise ValueError(
                "PostgresPatientRepository requires a DSN. Set DATABASE_URL "
                "or POSTGRES_URL - SAKHI_REPOSITORY=postgres alone is not "
                "enough to know which database to connect to."
            )
        self._dsn = dsn
        self._min_size = min_size
        self._max_size = max_size
        self._pool: "AsyncConnectionPool | None" = None
        self._pool_loop: "asyncio.AbstractEventLoop | None" = None
        # Lazily created, same reasoning as the pool itself: an asyncio.Lock
        # is loop-bound too (psycopg_pool's own AsyncConnectionPool does the
        # identical lazy-create-on-the-current-loop dance internally, via its
        # _ensure_lock()). Guards _ensure_pool() below against the race
        # confirmed against a real Postgres while building this: several
        # concurrent first calls (e.g. several simultaneous registrations
        # right after a worker process starts and nothing has queried yet)
        # each seeing self._pool as None and each independently opening a
        # pool and running the CREATE TABLE/ALTER TABLE/CREATE SEQUENCE setup
        # at the same time - which genuinely deadlocks Postgres, since DDL
        # takes conflicting locks and two sessions issuing it concurrently
        # against the same objects can each be waiting on the other.
        self._ensure_lock: "asyncio.Lock | None" = None
        self._ensure_lock_loop: "asyncio.AbstractEventLoop | None" = None
        self._dialect = POSTGRES

    # --- lifecycle -----------------------------------------------------

    async def load(self) -> "PostgresPatientRepository":
        """Deliberately does no I/O - see the class docstring's section on
        why the pool cannot open during agent.py's prewarm_process(). Kept as
        an async method (rather than removed) so it stays a drop-in for the
        `await repository.load()` call site every other repository shares."""
        return self

    async def _ensure_pool(self) -> "AsyncConnectionPool":
        """Return a pool bound to the CURRENTLY running event loop, opening
        one (or transparently replacing a pool bound to some other, likely
        already-dead, loop) if needed. See the class docstring - this is what
        makes the class safe to use after agent.py's prewarm_process() call,
        which cannot open the pool itself."""
        running_loop = asyncio.get_running_loop()
        if self._pool is not None and self._pool_loop is running_loop:
            return self._pool

        if self._ensure_lock is None or self._ensure_lock_loop is not running_loop:
            self._ensure_lock = asyncio.Lock()
            self._ensure_lock_loop = running_loop

        async with self._ensure_lock:
            # Re-check now that the lock is held: another coroutine may have
            # already finished opening the pool while this one was waiting -
            # the actual fix for the deadlock above, this recheck is what
            # makes every concurrent caller after the first just reuse the
            # pool the first one opened, instead of each opening its own.
            if self._pool is not None and self._pool_loop is running_loop:
                return self._pool
            return await self._open_pool(running_loop)

    async def _open_pool(self, running_loop: "asyncio.AbstractEventLoop") -> "AsyncConnectionPool":
        """The actual pool-open + schema-setup work, called with
        self._ensure_lock held so only one coroutine ever runs this at a
        time."""
        if self._pool is not None:
            # Bound to a different loop - almost always prewarm's throwaway
            # one, already destroyed. Do not attempt to close it: closing an
            # AsyncConnectionPool needs to run on the loop it opened on, and
            # that loop may no longer exist. A handful of connections opened
            # during that abandoned attempt (bounded by min_size) is the
            # accepted cost - it is why load() no longer opens the pool
            # eagerly at all; this branch is a defensive fallback for reuse
            # across loops in general, not the expected path.
            logger.warning(
                "PostgresPatientRepository pool was bound to a stale event "
                "loop (min_size=%d connections abandoned there) - opening a "
                "fresh pool on the current loop", self._min_size,
            )

        if sys.platform == "win32" and isinstance(running_loop, asyncio.ProactorEventLoop):
            raise RuntimeError(
                "psycopg's async mode cannot run on asyncio's ProactorEventLoop "
                "(Windows' default loop, and what this worker is using right "
                "now). This only affects local Windows testing against a real "
                "Postgres - the Linux/ECS target uses the default selector "
                "loop and is unaffected. To test Postgres locally on Windows, "
                "set the event loop policy to WindowsSelectorEventLoopPolicy "
                "before the worker starts."
            )

        pool = AsyncConnectionPool(
            self._dsn,
            min_size=self._min_size,
            max_size=self._max_size,
            # autocommit is deliberately NOT set here (psycopg defaults to
            # False): `pool.connection()` then wraps every statement run
            # inside its `async with` block in one implicit transaction,
            # committed on clean exit and rolled back on exception. That is
            # what makes a BP insert + the mother's risk_status UPDATE two
            # statements later in this file land together or not at all -
            # setting autocommit=True here would commit each statement the
            # instant it runs and silently drop that guarantee.
            kwargs={"row_factory": dict_row},
            open=False,
        )
        await pool.open(wait=True, timeout=15)

        async with pool.connection() as conn:
            for statement in schema_statements(self._dialect):
                await conn.execute(statement)
            # Same poor-man's migration as SQLitePatientRepository - no
            # schema-version table, so each ADD COLUMN is attempted and a
            # "column already exists" failure is swallowed. Unlike SQLite,
            # a failed statement inside a Postgres transaction poisons every
            # later statement in that same transaction
            # (`InFailedSqlTransaction`) until a ROLLBACK - so each attempt
            # runs inside its own `conn.transaction()` block, which psycopg
            # implements as a SAVEPOINT when already inside a transaction:
            # a DuplicateColumn there rolls back only that one statement,
            # and the outer transaction (and every migration statement still
            # to come) is unaffected. Confirmed this matters, not just in
            # theory: without the per-statement savepoint, the very next
            # migration statement after the first "already exists" failure
            # raises InFailedSqlTransaction and load() never finishes.
            for statement_group in (
                call_reports_migration_statements(self._dialect),
                teleconsults_migration_statements(self._dialect),
                danger_sign_columns_migration_statements(self._dialect),
                recording_columns_migration_statements(self._dialect),
            ):
                for statement in statement_group:
                    try:
                        async with conn.transaction():
                            await conn.execute(statement)
                    except psycopg.errors.DuplicateColumn:
                        pass

            # Atomic id allocation (see class docstring's CONCURRENCY MODEL
            # section) - CREATE SEQUENCE IF NOT EXISTS is idempotent, and the
            # setval() re-catches the sequence up to the current data's
            # highest M-NNN suffix every time this runs, so a sequence
            # created fresh against a database that already has migrated
            # rows (see sakhi_schema.py's POSTGRES_MIGRATION_NOTES) can never
            # hand out an id below what already exists - it only ever leaves
            # gaps (a rolled-back create_patient consumes a number it never
            # uses), never collisions.
            await conn.execute(
                "CREATE SEQUENCE IF NOT EXISTS mothers_patient_id_seq START WITH 201"
            )
            await conn.execute(
                """
                SELECT setval(
                    'mothers_patient_id_seq',
                    GREATEST(
                        200,
                        COALESCE(
                            (SELECT MAX(CAST(SUBSTRING(patient_id FROM 3) AS INTEGER))
                             FROM mothers
                             WHERE patient_id ~ '^M-[0-9]+$'),
                            200
                        )
                    )
                )
                """
            )

        self._pool = pool
        self._pool_loop = running_loop
        logger.info("Postgres repository ready (pool %d-%d)", self._min_size, self._max_size)
        return pool

    async def flush(self) -> None:
        """No-op: every write below already commits when its own
        `pool.connection()` block exits cleanly (see _ensure_pool()'s note on
        why autocommit is left off). Present only so callers that duck-type
        against SQLitePatientRepository's contract (agent.py calls this at
        the end of every call) don't hit AttributeError."""
        return None

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            self._pool_loop = None

    # --- internals -----------------------------------------------------

    @staticmethod
    def _to_record(row: dict[str, Any], history: list[dict[str, Any]] | None = None) -> PatientRecord:
        return PatientRecord(
            patient_id=row["patient_id"],
            name=row["name"],
            age=row["age"],
            village=row["village"],
            phone=row["phone"],
            gestational_weeks=row["gestational_weeks"],
            risk_status=RiskLevel(row["risk_status"]) if row["risk_status"] else RiskLevel.ROUTINE,
            symptoms=SymptomSet(
                headache=bool(row["headache"]),
                blurry_vision=bool(row["blurry_vision"]),
                swelling=bool(row["swelling"]),
                vaginal_bleeding=bool(row.get("vaginal_bleeding", 0)),
                severe_abdominal_pain=bool(row.get("severe_abdominal_pain", 0)),
                convulsions=bool(row.get("convulsions", 0)),
                reduced_fetal_movement=bool(row.get("reduced_fetal_movement", 0)),
            ),
            history=history or [],
        )

    async def _history(self, conn, patient_id: str) -> list[dict[str, Any]]:
        """Single-patient history - used by get_patient(), where there is
        only ever one id to look up so batching would add nothing."""
        cur = await conn.execute(
            self._dialect.render(
                "SELECT systolic, diastolic, risk_level, recorded_at FROM bp_records "
                "WHERE patient_id = ? ORDER BY id"
            ),
            (patient_id,),
        )
        rows = await cur.fetchall()
        return [
            {"date": r["recorded_at"], "bp": f"{r['systolic']}/{r['diastolic']}",
             "risk": r["risk_level"]}
            for r in rows
        ]

    async def _history_batch(self, conn, patient_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        """History for MULTIPLE patients in one round trip - used by
        find_patients(), which can match several mothers on a name search.
        Fetching each match's history with its own query is an N+1 pattern
        that costs nothing measurable on SQLite (in-process, no network) but
        is a real, avoidable added-latency-per-match cost against a real
        network Postgres endpoint on every voice turn that searches by name."""
        if not patient_ids:
            return {}
        placeholders = ",".join(["?"] * len(patient_ids))
        cur = await conn.execute(
            self._dialect.render(
                "SELECT patient_id, systolic, diastolic, risk_level, recorded_at "
                f"FROM bp_records WHERE patient_id IN ({placeholders}) ORDER BY id"
            ),
            tuple(patient_ids),
        )
        rows = await cur.fetchall()
        out: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            out.setdefault(r["patient_id"], []).append(
                {"date": r["recorded_at"], "bp": f"{r['systolic']}/{r['diastolic']}",
                 "risk": r["risk_level"]}
            )
        return out

    # --- PatientRepository -----------------------------------------------

    async def find_patients(
        self, *, name: str | None = None, phone: str | None = None
    ) -> list[PatientRecord]:
        pool = await self._ensure_pool()
        async with pool.connection() as conn:
            if phone:
                normalised = normalize_phone(phone)
                cur = await conn.execute(
                    self._dialect.render("SELECT * FROM mothers WHERE phone = ?"),
                    (normalised,),
                )
                rows = await cur.fetchall()
            else:
                if not name or not name.strip():
                    return []
                cur = await conn.execute(
                    self._dialect.render(
                        "SELECT * FROM mothers WHERE name ILIKE ? ORDER BY name"
                    ),
                    (f"%{name.strip()}%",),
                )
                rows = await cur.fetchall()

            if not rows:
                return []
            histories = await self._history_batch(conn, [r["patient_id"] for r in rows])
            return [self._to_record(r, histories.get(r["patient_id"], [])) for r in rows]

    async def get_patient(self, patient_id: str) -> PatientRecord | None:
        pool = await self._ensure_pool()
        async with pool.connection() as conn:
            cur = await conn.execute(
                self._dialect.render("SELECT * FROM mothers WHERE patient_id = ?"),
                (patient_id,),
            )
            rows = await cur.fetchall()
            if not rows:
                return None
            return self._to_record(rows[0], await self._history(conn, patient_id))

    async def create_patient(
        self,
        *,
        name: str,
        age: int | None = None,
        gestational_weeks: int | None = None,
        village: str | None = None,
        phone: str | None = None,
    ) -> PatientRecord:
        pool = await self._ensure_pool()
        now = _utc_now()
        async with pool.connection() as conn:
            # nextval() is atomic at the database level regardless of how
            # many connections call it concurrently - see the class
            # docstring's CONCURRENCY MODEL section for why this replaces
            # SQLite's SELECT-MAX-then-INSERT (safe there only because of the
            # process-level lock this class does not, and should not, have).
            cur = await conn.execute("SELECT nextval('mothers_patient_id_seq') AS n")
            row = await cur.fetchone()
            patient_id = f"M-{row['n']}"
            await conn.execute(
                self._dialect.render(
                    "INSERT INTO mothers (patient_id, name, age, village, phone, "
                    "gestational_weeks, risk_status, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                ),
                (patient_id, name, age, village,
                 normalize_phone(phone) if phone else None,
                 gestational_weeks, RiskLevel.ROUTINE.value, now, now),
            )
        return PatientRecord(
            patient_id=patient_id, name=name, age=age, village=village,
            phone=normalize_phone(phone) if phone else None,
            gestational_weeks=gestational_weeks, risk_status=RiskLevel.ROUTINE,
        )

    async def record_blood_pressure(
        self, patient_id: str, bp: BloodPressure, assessment: RiskAssessment
    ) -> None:
        pool = await self._ensure_pool()
        now = _utc_now()
        async with pool.connection() as conn:
            # One transaction (pool.connection()'s default commit-on-exit):
            # the reading and the mother's risk level move together or not at
            # all - see the class docstring's CONCURRENCY MODEL note.
            await conn.execute(
                self._dialect.render(
                    "INSERT INTO bp_records (patient_id, systolic, diastolic, "
                    "risk_level, recorded_at) VALUES (?, ?, ?, ?, ?)"
                ),
                (patient_id, bp.systolic, bp.diastolic, assessment.level.value, now),
            )
            await conn.execute(
                self._dialect.render(
                    "UPDATE mothers SET risk_status = ?, updated_at = ? WHERE patient_id = ?"
                ),
                (assessment.level.value, now, patient_id),
            )

    async def record_symptoms(self, patient_id: str, symptoms: SymptomSet) -> None:
        pool = await self._ensure_pool()
        now = _utc_now()
        async with pool.connection() as conn:
            await conn.execute(
                self._dialect.render(
                    "INSERT INTO symptom_records (patient_id, headache, blurry_vision, "
                    "swelling, vaginal_bleeding, severe_abdominal_pain, convulsions, "
                    "reduced_fetal_movement, recorded_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                ),
                (patient_id, int(symptoms.headache), int(symptoms.blurry_vision),
                 int(symptoms.swelling), int(symptoms.vaginal_bleeding),
                 int(symptoms.severe_abdominal_pain), int(symptoms.convulsions),
                 int(symptoms.reduced_fetal_movement), now),
            )
            # Symptoms only ever accumulate (OR), never clear - see
            # SQLitePatientRepository.record_symptoms for why.
            await conn.execute(
                self._dialect.render(
                    "UPDATE mothers SET headache = CASE WHEN headache = 1 OR ? = 1 THEN 1 ELSE 0 END, "
                    "blurry_vision = CASE WHEN blurry_vision = 1 OR ? = 1 THEN 1 ELSE 0 END, "
                    "swelling = CASE WHEN swelling = 1 OR ? = 1 THEN 1 ELSE 0 END, "
                    "vaginal_bleeding = CASE WHEN vaginal_bleeding = 1 OR ? = 1 THEN 1 ELSE 0 END, "
                    "severe_abdominal_pain = CASE WHEN severe_abdominal_pain = 1 OR ? = 1 THEN 1 ELSE 0 END, "
                    "convulsions = CASE WHEN convulsions = 1 OR ? = 1 THEN 1 ELSE 0 END, "
                    "reduced_fetal_movement = CASE WHEN reduced_fetal_movement = 1 OR ? = 1 THEN 1 ELSE 0 END, "
                    "updated_at = ? WHERE patient_id = ?"
                ),
                (int(symptoms.headache), int(symptoms.blurry_vision),
                 int(symptoms.swelling), int(symptoms.vaginal_bleeding),
                 int(symptoms.severe_abdominal_pain), int(symptoms.convulsions),
                 int(symptoms.reduced_fetal_movement), now, patient_id),
            )

    async def log_teleconsult(
        self,
        patient_id: str,
        reason: str,
        assessment: RiskAssessment | None = None,
        *,
        trigger_sign: str | None = None,
    ) -> str:
        pool = await self._ensure_pool()
        now = _utc_now()
        channel_id = f"SMFMI-{int(datetime.now(timezone.utc).timestamp())}"
        async with pool.connection() as conn:
            await conn.execute(
                self._dialect.render(
                    "INSERT INTO teleconsults (patient_id, channel_id, reason, risk_level, "
                    "trigger_sign, created_at) VALUES (?, ?, ?, ?, ?, ?)"
                ),
                (patient_id, channel_id, reason,
                 assessment.level.value if assessment else None, trigger_sign, now),
            )
        return channel_id

    # --- reporting (used by the dashboard, not on the voice hot path) ---

    async def save_call_report(self, report: dict[str, Any]) -> int:
        pool = await self._ensure_pool()
        async with pool.connection() as conn:
            cur = await conn.execute(
                self._dialect.render(
                    "INSERT INTO call_reports (session_id, patient_id, language, "
                    "risk_level, intake_answered, intake_total, escalated, duration_ms, "
                    "manual_risk_flag, flagged_to_doctor, flagged_by, flagged_at, "
                    "recording_status, recording_key, "
                    "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "RETURNING id"
                ),
                (report.get("session_id"), report.get("patient_id"),
                 report.get("language"), report.get("risk_level"),
                 int(report.get("intake_answered") or 0),
                 int(report.get("intake_total") or 0),
                 int(bool(report.get("escalated"))),
                 report.get("duration_ms"),
                 0, 0, None, None,
                 report.get("recording_status") or "none",
                 report.get("recording_key"),
                 _utc_now()),
            )
            row = await cur.fetchone()
            return int(row["id"])

    async def update_recording_status(self, session_id: str, status: str) -> None:
        pool = await self._ensure_pool()
        async with pool.connection() as conn:
            await conn.execute(
                self._dialect.render(
                    "UPDATE call_reports SET recording_status = ? WHERE session_id = ?"
                ),
                (status, session_id),
            )

    async def list_call_reports(self, limit: int = 50) -> list[dict[str, Any]]:
        pool = await self._ensure_pool()
        async with pool.connection() as conn:
            cur = await conn.execute(
                self._dialect.render("SELECT * FROM call_reports ORDER BY id DESC LIMIT ?"),
                (limit,),
            )
            rows = await cur.fetchall()
            return [dict(row) for row in rows]

    async def set_report_flag(
        self, report_id: int, *, field: str, value: bool, flagged_by: str | None = None
    ) -> bool:
        if field not in self.FLAGGABLE_FIELDS:
            raise ValueError(f"not a flaggable field: {field!r}")
        pool = await self._ensure_pool()
        async with pool.connection() as conn:
            cur = await conn.execute(
                self._dialect.render(
                    f"UPDATE call_reports SET {field} = ?, flagged_by = ?, "
                    f"flagged_at = ? WHERE id = ?"
                ),
                (int(bool(value)), flagged_by, _utc_now(), report_id),
            )
            return cur.rowcount > 0

    async def stats(self) -> dict[str, Any]:
        """Counts for the dashboard's database health panel."""
        pool = await self._ensure_pool()
        out: dict[str, Any] = {"backend": "postgres"}
        async with pool.connection() as conn:
            for table in ("mothers", "bp_records", "symptom_records",
                          "teleconsults", "call_reports"):
                cur = await conn.execute(f"SELECT COUNT(*) AS n FROM {table}")
                row = await cur.fetchone()
                out[table] = int(row["n"]) if row else 0
        return out
