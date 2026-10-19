"""Live-Postgres checks for telemetry, the dashboard store, and write latency.

Skipped unless SAKHI_TEST_POSTGRES_DSN points at a real Postgres - a local
throwaway database, or the Supabase project itself (use the SESSION pooler
connection string, port 5432). Against Supabase these are the acceptance
tests for the migration: correctness on the real engine, plus the latency
budget that keeps the database off the critical path of a spoken reply.

Tables are created by the repository's own load(), exactly as in production.
"""

import asyncio
import os
import statistics
import time
import uuid

import pytest

pytest.importorskip("psycopg")

from sakhi_dashboard_store import PostgresDashboardStore
from sakhi_domain import BloodPressure, SymptomSet
from sakhi_sql_repository import PostgresPatientRepository
from sakhi_telemetry import CURRENT_SESSION, TelemetryWriter
from sakhi_triage import TRIAGE

_DSN = os.getenv("SAKHI_TEST_POSTGRES_DSN")
pytestmark = pytest.mark.skipif(not _DSN, reason="set SAKHI_TEST_POSTGRES_DSN to run")

# p95 budget for one clinical write (BP insert + risk update, one
# transaction). Same-region Supabase (EC2 ap-south-1 -> Supabase Mumbai) is
# expected at single-digit milliseconds; 50 ms leaves room for pooler hops
# while still catching a wrong-region project (typically 40-250 ms/query).
WRITE_P95_BUDGET_S = float(os.getenv("SAKHI_TEST_WRITE_P95_BUDGET_S", "0.050"))


def _run(coro):
    return asyncio.run(coro)


def _event(label, stage, status="ok", message="m", **details):
    return {"ts": "2026-09-24T10:00:00+00:00", "stage": stage, "status": status,
            "label": label, "message": message, "details": details}


def test_telemetry_and_dashboard_round_trip_on_postgres():
    session = f"room-{uuid.uuid4().hex[:8]}"

    async def write():
        repo = await PostgresPatientRepository(_DSN).load()
        try:
            mother = await repo.create_patient(name=f"Live {session}", age=27)
            await repo.record_symptoms(mother.patient_id, SymptomSet(fever=True))
            await repo.save_call_report({"session_id": session, "patient_id": mother.patient_id,
                                         "language": "kn", "risk_level": "HIGH_RISK"})
            writer = TelemetryWriter(repo.record_telemetry)
            token = CURRENT_SESSION.set(session)
            writer.submit(_event("Sakhi said", "Stage 8 TTS", message="ನಮಸ್ಕಾರ", event="agent_speech"))
            writer.submit(_event("Transcript final", "Stage 5 STT", message="Hello"))
            writer.submit(_event("LLM failed", "Stage 7 LLM", status="error", message="timeout"))
            CURRENT_SESSION.reset(token)
            await writer.flush()
            return mother.patient_id
        finally:
            await repo.close()

    patient_id = _run(write())
    store = PostgresDashboardStore(_DSN)
    report = store.patient_report(patient_id)
    assert "fever" in report["danger_signs"]
    call = next(c for c in report["calls"] if c["session_id"] == session)
    assert [t["speaker"] for t in call["turns"]] == ["sakhi", "asha"]
    events = store.list_events(session_id=session)["events"]
    assert [e["label"] for e in events] == ["Sakhi said", "Transcript final", "LLM failed"]
    assert any(c["component"] == "llm" and c["last_status"] == "error"
               for c in store.component_health())
    # ILIKE: case-insensitive search works on Postgres too.
    assert store.list_patients(search=f"live {session}".upper())


def test_clinical_write_latency_is_within_budget():
    """100 BP writes; p95 must stay under WRITE_P95_BUDGET_S."""
    async def go():
        repo = await PostgresPatientRepository(_DSN).load()
        try:
            mother = await repo.create_patient(name="Latency Probe", age=25)
            bp = BloodPressure(128, 84)
            assessment = TRIAGE.assess(bp, SymptomSet())
            await repo.record_blood_pressure(mother.patient_id, bp, assessment)  # warm the pool
            samples = []
            for _ in range(100):
                started = time.perf_counter()
                await repo.record_blood_pressure(mother.patient_id, bp, assessment)
                samples.append(time.perf_counter() - started)
            return samples
        finally:
            await repo.close()

    samples = sorted(_run(go()))
    p95 = samples[94]
    print(f"\nBP write latency: p50={statistics.median(samples) * 1000:.1f}ms "
          f"p95={p95 * 1000:.1f}ms max={samples[-1] * 1000:.1f}ms")
    assert p95 < WRITE_P95_BUDGET_S, (
        f"p95 {p95 * 1000:.1f} ms exceeds {WRITE_P95_BUDGET_S * 1000:.0f} ms - "
        "check the Supabase project region is ap-south-1 (Mumbai)"
    )


def test_telemetry_batch_flush_is_fast():
    """A worst-case backlog (500 events) flushes in one batch well under a
    second, so the 250 ms flush loop never falls behind a live call."""
    async def go():
        repo = await PostgresPatientRepository(_DSN).load()
        try:
            writer = TelemetryWriter(repo.record_telemetry, batch_size=500)
            token = CURRENT_SESSION.set(f"room-{uuid.uuid4().hex[:8]}")
            for i in range(500):
                writer.submit(_event("TTS metrics", "Stage 8 TTS", message=str(i)))
            CURRENT_SESSION.reset(token)
            started = time.perf_counter()
            await writer.flush()
            return time.perf_counter() - started
        finally:
            await repo.close()

    elapsed = _run(go())
    print(f"\n500-event flush: {elapsed * 1000:.0f} ms")
    assert elapsed < 1.0
