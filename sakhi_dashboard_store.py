"""Dashboard data access - one synchronous store for SQLite AND Postgres.

WHY THIS EXISTS
---------------
The voice worker writes through the async `PatientRepository` (SQLite or
Postgres, chosen by DATABASE_URL). The dashboard is served by
status_server.py - a synchronous, threaded HTTP server - and used to open the
SQLite FILE directly for every read. Switch the worker to Supabase and the
dashboard would have gone on reading the old file: stale or empty reports,
no error anywhere. This module is the dashboard's single read path, and it
picks its backend from the same environment variable the worker does, so the
two can never point at different databases.

It is deliberately synchronous: status_server handles each request on its own
thread, and a sync connection there is simpler and safer than bridging into
the worker's event loop (which must stay dedicated to audio).

EFFICIENCY NOTES
----------------
  - Event paging is keyset (`WHERE id > cursor ORDER BY id LIMIT n`) on the
    primary key: O(log n + page) per poll however large the table grows. The
    JSONL reader it replaces re-read and split the ENTIRE log file on every
    poll - O(total log size), getting slower every day the system ran.
  - The patient report card is a fixed number of indexed queries (one per
    table, plus ONE `IN (...)` query for every call's turns) - never a query
    per call or per reading.
  - Postgres uses a small bounded pool (psycopg_pool), with prepared
    statements disabled for Supabase's transaction-mode pooler, same
    reasoning as PostgresPatientRepository.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sakhi_domain import SymptomSet
from sakhi_schema import POSTGRES, SQLITE, SYMPTOM_COLUMNS, Dialect

try:
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool
except ImportError:  # SQLite-only deployments never need it
    dict_row = None
    ConnectionPool = None

FLAGGABLE_FIELDS = ("manual_risk_flag", "flagged_to_doctor")
MAX_PAGE = 500


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _symptoms(row: dict[str, Any] | None) -> SymptomSet:
    row = row or {}
    return SymptomSet(**{c: bool(row.get(c)) for c in SYMPTOM_COLUMNS})


def _clamp(limit: Any, default: int = 50) -> int:
    try:
        value = int(limit)
    except (TypeError, ValueError):
        value = default
    return max(1, min(MAX_PAGE, value))


class DashboardStore:
    """Backend-agnostic queries. Subclasses supply `_rows` and `_write`."""

    dialect: Dialect = SQLITE
    # Case-insensitive match operator. SQLite's LIKE is already
    # case-insensitive for ASCII; Postgres's is not, so a search for "laksh"
    # would silently miss "Lakshmi" there without ILIKE.
    like_op: str = "LIKE"

    # --- backend hooks ----------------------------------------------------

    def _rows(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        raise NotImplementedError

    def _write(self, sql: str, params: tuple = ()) -> int:
        """Execute one write; return rows affected."""
        raise NotImplementedError

    def available(self) -> bool:
        return True

    def backend(self) -> str:
        return self.dialect.name

    def _one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        rows = self._rows(sql, params)
        return rows[0] if rows else None

    # --- reports ----------------------------------------------------------

    def list_reports(self, limit: Any = 50) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM call_reports ORDER BY id DESC LIMIT ?", (_clamp(limit),))

    def find_report(self, *, report_id: int | None = None,
                    session_id: str | None = None) -> dict[str, Any] | None:
        if report_id is not None:
            return self._one("SELECT * FROM call_reports WHERE id = ?", (report_id,))
        if not session_id:
            return None
        clean = str(session_id).strip()
        if clean.lower() == "latest":
            return self._one("SELECT * FROM call_reports ORDER BY id DESC LIMIT 1")
        report = self._one(
            "SELECT * FROM call_reports WHERE session_id = ? ORDER BY id DESC LIMIT 1", (clean,)
        )
        if report is None and clean:
            # Never substitute another call when an explicit id misses - see
            # status_server.mcp_card_for_report's history note.
            report = self._one(
                "SELECT * FROM call_reports WHERE session_id LIKE ? ORDER BY id DESC LIMIT 1",
                (f"%{clean}%",),
            )
        return report

    def set_report_flag(self, report_id: int, field: str, value: bool,
                        flagged_by: str | None) -> tuple[bool, str | None]:
        if field not in FLAGGABLE_FIELDS:
            return False, f"not a flaggable field: {field!r}"
        # `field` is whitelisted above, never taken raw from the request.
        affected = self._write(
            f"UPDATE call_reports SET {field} = ?, flagged_by = ?, flagged_at = ? WHERE id = ?",
            (int(bool(value)), flagged_by, _utc_now(), int(report_id)),
        )
        return (True, None) if affected else (False, "report not found")

    def mark_recording_available(self, session_id: str | None) -> None:
        if session_id:
            self._write(
                "UPDATE call_reports SET recording_status = 'available' WHERE session_id = ?",
                (session_id,),
            )

    # --- patients -----------------------------------------------------------

    def list_patients(self, *, search: str | None = None, risk: str | None = None,
                      limit: Any = 50, offset: Any = 0) -> list[dict[str, Any]]:
        clauses, params = [], []
        if search and search.strip():
            op = self.like_op
            clauses.append(f"(name {op} ? OR patient_id {op} ? OR phone {op} ?)")
            like = f"%{search.strip()}%"
            params += [like, like, like]
        if risk and risk.strip():
            clauses.append("risk_status = ?")
            params.append(risk.strip().upper())
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        try:
            skip = max(0, int(offset))
        except (TypeError, ValueError):
            skip = 0
        rows = self._rows(
            f"SELECT * FROM mothers {where} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (*params, _clamp(limit), skip),
        )
        for row in rows:
            row["danger_signs"] = list(_symptoms(row).present_names)
        return rows

    def patient_report(self, patient_id: str) -> dict[str, Any] | None:
        """Everything on one mother - the downloadable report card.

        Six indexed queries total, independent of how many visits she has had.
        """
        mother = self._one("SELECT * FROM mothers WHERE patient_id = ?", (patient_id,))
        if mother is None:
            return None
        bp = self._rows(
            "SELECT systolic, diastolic, risk_level, recorded_at FROM bp_records "
            "WHERE patient_id = ? ORDER BY id", (patient_id,),
        )
        symptom_rows = self._rows(
            "SELECT * FROM symptom_records WHERE patient_id = ? ORDER BY id", (patient_id,),
        )
        teleconsults = self._rows(
            "SELECT channel_id, reason, risk_level, trigger_sign, created_at "
            "FROM teleconsults WHERE patient_id = ? ORDER BY id", (patient_id,),
        )
        calls = self._rows(
            "SELECT * FROM call_reports WHERE patient_id = ? ORDER BY id", (patient_id,),
        )
        turns_by_session = self._turns_for_sessions([c["session_id"] for c in calls if c.get("session_id")])
        for call in calls:
            call["turns"] = turns_by_session.get(call.get("session_id"), [])
        return {
            "mother": mother,
            "danger_signs": list(_symptoms(mother).present_names),
            "bp_readings": bp,
            "symptom_history": [
                {"recorded_at": r.get("recorded_at"),
                 "present": list(_symptoms(r).present_names)}
                for r in symptom_rows
            ],
            "teleconsults": teleconsults,
            "calls": calls,
            "generated_at": _utc_now(),
        }

    def report_bundle(self, report: dict[str, Any]) -> dict[str, Any]:
        """Mother + latest BP + latest teleconsult for one call report."""
        pid = report.get("patient_id")
        if not pid:
            return {"mother": None, "bp": None, "teleconsult": None}
        return {
            "mother": self._one("SELECT * FROM mothers WHERE patient_id = ?", (pid,)),
            "bp": self._one(
                "SELECT * FROM bp_records WHERE patient_id = ? ORDER BY id DESC LIMIT 1", (pid,)),
            "teleconsult": self._one(
                "SELECT * FROM teleconsults WHERE patient_id = ? ORDER BY id DESC LIMIT 1", (pid,)),
        }

    # --- conversation and logs ----------------------------------------------

    def _turns_for_sessions(self, session_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
        if not session_ids:
            return {}
        marks = ",".join(["?"] * len(session_ids))
        rows = self._rows(
            "SELECT session_id, turn_no, speaker, text, language, ts FROM call_turns "
            f"WHERE session_id IN ({marks}) ORDER BY session_id, turn_no",
            tuple(session_ids),
        )
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(row["session_id"], []).append(row)
        return grouped

    def call_turns(self, session_id: str) -> list[dict[str, Any]]:
        return self._turns_for_sessions([session_id]).get(session_id, [])

    def list_events(self, *, after_id: Any = 0, limit: Any = 200,
                    session_id: str | None = None,
                    status: str | None = None) -> dict[str, Any]:
        """Keyset page of pipeline events: O(log n + page) on any table size.
        Returns {"events": [...], "next": <cursor for the next poll>}."""
        try:
            cursor = max(0, int(after_id))
        except (TypeError, ValueError):
            cursor = 0
        clauses, params = ["id > ?"], [cursor]
        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
        if status:
            clauses.append("status = ?")
            params.append(status)
        rows = self._rows(
            f"SELECT * FROM pipeline_events WHERE {' AND '.join(clauses)} ORDER BY id LIMIT ?",
            (*params, _clamp(limit, 200)),
        )
        for row in rows:
            raw = row.get("details")
            if isinstance(raw, str):
                try:
                    row["details"] = json.loads(raw)
                except (TypeError, ValueError):
                    pass
        return {"events": rows, "next": rows[-1]["id"] if rows else cursor}

    def component_health(self) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM component_health ORDER BY component")

    def summary(self) -> dict[str, Any]:
        """Header counters for the admin dashboard - one query per counter."""
        def count(sql: str, params: tuple = ()) -> int:
            row = self._one(sql, params)
            return int(row["n"]) if row else 0

        return {
            "backend": self.backend(),
            "mothers": count("SELECT COUNT(*) AS n FROM mothers"),
            "high_risk_mothers": count(
                "SELECT COUNT(*) AS n FROM mothers WHERE risk_status = ?", ("HIGH_RISK",)),
            "calls": count("SELECT COUNT(*) AS n FROM call_reports"),
            "escalations": count("SELECT COUNT(*) AS n FROM teleconsults"),
            "errors": count("SELECT COUNT(*) AS n FROM pipeline_events WHERE status = ?", ("error",)),
        }


class SQLiteDashboardStore(DashboardStore):
    dialect = SQLITE

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def available(self) -> bool:
        return self._path.exists()

    def _connect(self, *, readonly: bool) -> sqlite3.Connection:
        if readonly:
            conn = sqlite3.connect(f"file:{self._path}?mode=ro", uri=True, timeout=5)
        else:
            conn = sqlite3.connect(str(self._path), timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def _rows(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        if not self.available():
            return []
        conn = self._connect(readonly=True)
        try:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        except sqlite3.OperationalError as exc:
            # A table this build expects but an older file lacks (the worker
            # creates it on its next start) reads as empty, not as a 500.
            if "no such table" in str(exc):
                return []
            raise
        finally:
            conn.close()

    def _write(self, sql: str, params: tuple = ()) -> int:
        if not self.available():
            return 0
        conn = self._connect(readonly=False)
        try:
            cur = conn.execute(sql, params)
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()


class PostgresDashboardStore(DashboardStore):
    dialect = POSTGRES
    like_op = "ILIKE"

    def __init__(self, dsn: str, *, max_size: int = 4) -> None:
        if ConnectionPool is None:
            raise RuntimeError("PostgresDashboardStore requires 'psycopg[binary,pool]'")
        self._dsn = dsn
        self._max_size = max_size
        self._pool: Any = None
        self._lock = threading.Lock()

    def _pool_or_open(self):
        if self._pool is None:
            with self._lock:
                if self._pool is None:
                    self._pool = ConnectionPool(
                        self._dsn, min_size=1, max_size=self._max_size,
                        kwargs={"row_factory": dict_row, "prepare_threshold": None,
                                "connect_timeout": int(os.getenv("DATABASE_CONNECT_TIMEOUT", "10"))},
                        open=True,
                    )
        return self._pool

    def _rows(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._pool_or_open().connection() as conn:
            cur = conn.execute(self.dialect.render(sql), params)
            return [dict(r) for r in cur.fetchall()]

    def _write(self, sql: str, params: tuple = ()) -> int:
        with self._pool_or_open().connection() as conn:
            cur = conn.execute(self.dialect.render(sql), params)
            return cur.rowcount


_STORE: DashboardStore | None = None
_STORE_LOCK = threading.Lock()


def get_store() -> DashboardStore:
    """The process-wide store, chosen exactly the way agent.build_repository
    chooses the worker's backend: DATABASE_URL/POSTGRES_URL -> Postgres,
    otherwise the SQLite file at SAKHI_DB_PATH."""
    global _STORE
    if _STORE is None:
        with _STORE_LOCK:
            if _STORE is None:
                dsn = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL")
                _STORE = (PostgresDashboardStore(dsn) if dsn else
                          SQLiteDashboardStore(os.getenv("SAKHI_DB_PATH", "data/sakhi.sqlite3")))
    return _STORE


def reset_store() -> None:
    """Tests only: forget the cached store so env changes take effect."""
    global _STORE
    _STORE = None
