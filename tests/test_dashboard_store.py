"""sakhi_dashboard_store + the status_server dashboard endpoints.

The dashboard must read the SAME database the worker writes (the bug this
store replaced: status_server opened the SQLite file directly, so a
Supabase-backed worker would have left the dashboard silently stale), and
its report card must show every danger sign that was recorded.
"""

import asyncio
import json
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

import sakhi_dashboard_store
import status_server
from sakhi_domain import BloodPressure, SymptomSet
from sakhi_sql_repository import SQLitePatientRepository
from sakhi_telemetry import CURRENT_SESSION, TelemetryWriter
from sakhi_triage import TRIAGE


def _run(coro):
    return asyncio.run(coro)


def _event(label, stage, status="ok", message="m", **details):
    return {"ts": "2026-09-24T10:00:00+00:00", "stage": stage, "status": status,
            "label": label, "message": message, "details": details}


@pytest.fixture
def seeded(monkeypatch):
    """A SQLite database with one fully-recorded call, and the dashboard
    store pointed at it exactly the way production selects it (env vars)."""
    path = Path(tempfile.mkdtemp()) / "dash.sqlite3"

    async def seed():
        repo = await SQLitePatientRepository(path).load()
        mother = await repo.create_patient(name="Lakshmi Patil", age=26, gestational_weeks=30)
        bp = BloodPressure(152, 98)
        symptoms = SymptomSet(fever=True, convulsions=True)
        await repo.record_blood_pressure(mother.patient_id, bp, TRIAGE.assess(bp, symptoms))
        await repo.record_symptoms(mother.patient_id, symptoms)
        await repo.log_teleconsult(mother.patient_id, "fever with raised BP",
                                   TRIAGE.assess(bp, symptoms), trigger_sign="fever")
        await repo.save_call_report({"session_id": "room-1", "patient_id": mother.patient_id,
                                     "language": "kn", "risk_level": "HIGH_RISK",
                                     "intake_answered": 5, "intake_total": 24, "escalated": True})
        writer = TelemetryWriter(repo.record_telemetry)
        token = CURRENT_SESSION.set("room-1")
        writer.submit(_event("Sakhi said", "Stage 8 TTS", message="ತಾಯಿಯ ಹೆಸರು?", event="agent_speech"))
        writer.submit(_event("Transcript final", "Stage 5 STT", message="Lakshmi, 26"))
        writer.submit(_event("LLM failed", "Stage 7 LLM", status="error", message="timeout"))
        CURRENT_SESSION.reset(token)
        await writer.flush()
        await repo.close()
        return mother.patient_id

    patient_id = _run(seed())
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.setenv("SAKHI_DB_PATH", str(path))
    sakhi_dashboard_store.reset_store()
    yield {"path": path, "patient_id": patient_id, "store": sakhi_dashboard_store.get_store()}
    sakhi_dashboard_store.reset_store()


def test_store_backend_follows_the_worker_env(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    sakhi_dashboard_store.reset_store()
    assert isinstance(sakhi_dashboard_store.get_store(), sakhi_dashboard_store.SQLiteDashboardStore)
    sakhi_dashboard_store.reset_store()


def test_database_status_uses_postgres_when_database_url_is_set(monkeypatch):
    """The health endpoint must not inspect stale SQLite after cutover."""
    class FakePostgresStore:
        def summary(self):
            return {"mothers": 3, "calls": 7, "escalations": 2}

    monkeypatch.setenv("DATABASE_URL", "postgresql://redacted.invalid/postgres")
    monkeypatch.delenv("POSTGRES_URL", raising=False)
    monkeypatch.setattr(status_server, "get_store", lambda: FakePostgresStore())

    result = status_server.maternal_db_status()

    assert result == {
        "backend": "postgres",
        "ok": True,
        "durable": True,
        "counts": {"mothers": 3, "call_reports": 7, "teleconsults": 2},
    }


def test_database_status_redacts_postgres_connection_failures(monkeypatch):
    class BrokenStore:
        def summary(self):
            raise ConnectionError(
                "database unavailable at postgresql://secret:secret@db.invalid/postgres"
            )

    monkeypatch.setenv("DATABASE_URL", "postgresql://secret:secret@db.invalid/postgres")
    monkeypatch.setattr(status_server, "get_store", lambda: BrokenStore())

    result = status_server.maternal_db_status()

    assert result["backend"] == "postgres" and result["ok"] is False
    assert "secret" not in result["error"]


def test_patient_report_card_contains_everything(seeded):
    report = seeded["store"].patient_report(seeded["patient_id"])
    assert report["mother"]["name"] == "Lakshmi Patil"
    assert "fever" in report["danger_signs"] and "convulsions or fits" in report["danger_signs"]
    assert [(r["systolic"], r["diastolic"]) for r in report["bp_readings"]] == [(152, 98)]
    assert report["teleconsults"][0]["trigger_sign"] == "fever"
    call = report["calls"][0]
    assert call["session_id"] == "room-1"
    assert [t["speaker"] for t in call["turns"]] == ["sakhi", "asha"]


def test_unknown_patient_is_none(seeded):
    assert seeded["store"].patient_report("M-99999") is None


def test_event_paging_is_keyset_and_filterable(seeded):
    store = seeded["store"]
    first = store.list_events(after_id=0, limit=1)
    assert len(first["events"]) == 1
    rest = store.list_events(after_id=first["next"], limit=100)
    assert all(e["id"] > first["next"] for e in rest["events"])
    errors = store.list_events(status="error")
    assert [e["label"] for e in errors["events"]] == ["LLM failed"]
    assert all(e["session_id"] == "room-1" for e in store.list_events(session_id="room-1")["events"])


def test_patient_search_and_risk_filter(seeded):
    store = seeded["store"]
    assert [p["name"] for p in store.list_patients(search="laksh")] == ["Lakshmi Patil"]
    assert store.list_patients(risk="ROUTINE") == []
    assert len(store.list_patients(risk="high_risk")) == 1


def test_summary_counts(seeded):
    s = seeded["store"].summary()
    assert s["mothers"] == 1 and s["high_risk_mothers"] == 1
    assert s["calls"] == 1 and s["escalations"] == 1 and s["errors"] == 1


def test_missing_tables_read_as_empty_not_500(tmp_path, monkeypatch):
    import sqlite3
    path = tmp_path / "bare.sqlite3"
    sqlite3.connect(path).close()
    store = sakhi_dashboard_store.SQLiteDashboardStore(path)
    assert store.component_health() == []
    assert store.list_events()["events"] == []


# --- the HTTP layer ---------------------------------------------------------

@pytest.fixture
def server(seeded):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), status_server._Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield {"base": f"http://127.0.0.1:{httpd.server_address[1]}", **seeded}
    httpd.shutdown()


def _get(url):
    with urllib.request.urlopen(url, timeout=5) as resp:
        return resp.status, json.loads(resp.read())


def _post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, None


def test_mcp_card_lists_every_recorded_danger_sign(server):
    """Regression: the card used to hard-code five signs, so a fever- or
    fits-flagged mother showed no danger sign at all."""
    status, card = _get(f"{server['base']}/reports/mcp-card?session_id=room-1")
    assert status == 200
    assert "Fever" in card["danger_signs"]
    assert "Convulsions or fits" in card["danger_signs"]
    assert card["referral_trigger"] == "fever"


def test_flagging_a_report_returns_success(server):
    """Regression: a successful flag fell through to a 404 response."""
    report_id = server["store"].list_reports()[0]["id"]
    status, body = _post(f"{server['base']}/reports/flag",
                         {"report_id": report_id, "field": "flagged_to_doctor", "value": True})
    assert status == 200 and body["ok"] is True
    assert server["store"].list_reports()[0]["flagged_to_doctor"] == 1


def test_flagging_rejects_unknown_fields(server):
    status, _ = _post(f"{server['base']}/reports/flag",
                      {"report_id": 1, "field": "risk_level; DROP TABLE mothers", "value": True})
    assert status == 400


def test_dashboard_endpoints(server):
    base = server["base"]
    status, summary = _get(f"{base}/dashboard/summary")
    assert status == 200 and summary["mothers"] == 1
    assert {c["component"] for c in summary["components"]} >= {"llm", "stt", "tts"}

    status, patient = _get(f"{base}/dashboard/patient?id={server['patient_id']}")
    assert status == 200 and patient["mother"]["name"] == "Lakshmi Patil"

    status, turns = _get(f"{base}/dashboard/turns?session_id=room-1")
    assert status == 200 and len(turns["turns"]) == 2

    status, events = _get(f"{base}/dashboard/events?status=error")
    assert status == 200 and events["events"][0]["label"] == "LLM failed"


def test_dashboard_patient_requires_an_id(server):
    try:
        urllib.request.urlopen(f"{server['base']}/dashboard/patient", timeout=5)
        raise AssertionError("expected 400")
    except urllib.error.HTTPError as exc:
        assert exc.code == 400
