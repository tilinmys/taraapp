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
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sakhi_domain import BloodPressure, RiskAssessment, RiskLevel, SymptomSet
from sakhi_repository import PatientRecord, PatientRepository, normalize_phone
from sakhi_schema import SQLITE, SQLITE_PRAGMAS, schema_statements

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
        conn.commit()
        self._conn = conn
        logger.info("SQLite repository ready at %s", self._path)

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
                    "swelling, recorded_at) VALUES (?, ?, ?, ?, ?)",
                    (patient_id, int(symptoms.headache), int(symptoms.blurry_vision),
                     int(symptoms.swelling), now),
                )
                # Symptoms only ever accumulate (OR), never clear: a later
                # "no headache" must not erase a headache the mother reported
                # earlier in the same call.
                conn.execute(
                    "UPDATE mothers SET headache = CASE WHEN headache = 1 OR ? = 1 THEN 1 ELSE 0 END, "
                    "blurry_vision = CASE WHEN blurry_vision = 1 OR ? = 1 THEN 1 ELSE 0 END, "
                    "swelling = CASE WHEN swelling = 1 OR ? = 1 THEN 1 ELSE 0 END, "
                    "updated_at = ? WHERE patient_id = ?",
                    (int(symptoms.headache), int(symptoms.blurry_vision),
                     int(symptoms.swelling), now, patient_id),
                )
                conn.commit()

        await asyncio.to_thread(_record)

    async def log_teleconsult(
        self, patient_id: str, reason: str, assessment: RiskAssessment | None = None
    ) -> str:
        def _log() -> str:
            now = _utc_now()
            channel_id = f"SMFMI-{int(datetime.now(timezone.utc).timestamp())}"
            self._execute(
                "INSERT INTO teleconsults (patient_id, channel_id, reason, risk_level, "
                "created_at) VALUES (?, ?, ?, ?, ?)",
                (patient_id, channel_id, reason,
                 assessment.level.value if assessment else None, now),
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
                    "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (report.get("session_id"), report.get("patient_id"),
                     report.get("language"), report.get("risk_level"),
                     int(report.get("intake_answered") or 0),
                     int(report.get("intake_total") or 0),
                     int(bool(report.get("escalated"))),
                     report.get("duration_ms"), _utc_now()),
                )
                conn.commit()
                return int(cur.lastrowid)

        return await asyncio.to_thread(_save)

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
