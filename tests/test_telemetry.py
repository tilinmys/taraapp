"""sakhi_telemetry.TelemetryWriter - the batched, non-blocking path that
feeds the admin dashboard.

Covers the guarantees the dashboard design depends on:
  - submit() is O(1) and fast enough to sit on the voice hot path
  - every event is attributed to its call via CURRENT_SESSION
  - conversation turns are extracted in order; interim transcripts are not
  - component health is coalesced/throttled, and errors are never throttled
  - a failing database delays telemetry, it does not lose it or break a call
  - the buffer is bounded (drops oldest) so an outage cannot exhaust memory
  - end to end: events land in SQLite within one flush interval
"""

import asyncio
import contextvars
import json
import tempfile
import time
from pathlib import Path

import pytest

from sakhi_sql_repository import SQLitePatientRepository
from sakhi_telemetry import CURRENT_SESSION, TelemetryWriter


def _run(coro):
    return asyncio.run(coro)


def _event(label="TTS metrics", status="ok", stage="Stage 8 TTS", message="m", **details):
    return {"ts": "2026-09-24T10:00:00+00:00", "stage": stage, "status": status,
            "label": label, "message": message, "details": details}


class _Recorder:
    def __init__(self, fail_times: int = 0):
        self.batches = []
        self.fail_times = fail_times

    async def __call__(self, events, turns, health):
        if self.fail_times:
            self.fail_times -= 1
            raise ConnectionError("database unreachable")
        self.batches.append((list(events), list(turns), list(health)))

    @property
    def events(self):
        return [e for b in self.batches for e in b[0]]

    @property
    def turns(self):
        return [t for b in self.batches for t in b[1]]

    @property
    def health(self):
        return [h for b in self.batches for h in b[2]]


def _in_session(session_id, fn):
    ctx = contextvars.copy_context()
    ctx.run(CURRENT_SESSION.set, session_id)
    return ctx.run(fn)


# --- hot path --------------------------------------------------------------

def test_submit_is_fast_enough_for_the_voice_path():
    """p99 under 100 microseconds per event, measured over 5,000 events.
    A single spoken turn emits tens of events; this keeps their total cost
    far below one millisecond."""
    writer = TelemetryWriter(_Recorder(), max_buffer=10_000)
    samples = []
    for i in range(5000):
        start = time.perf_counter()
        writer.submit(_event(message=f"event {i}"))
        samples.append(time.perf_counter() - start)
    samples.sort()
    p99 = samples[int(len(samples) * 0.99)]
    assert p99 < 100e-6, f"submit p99 {p99 * 1e6:.1f}us is too slow for the hot path"


def test_submit_never_raises_on_garbage():
    writer = TelemetryWriter(_Recorder())
    writer.submit({})                      # no fields at all
    writer.submit({"details": "not a dict", "label": "x"})
    writer.submit(None)                    # type: ignore[arg-type]


# --- attribution and extraction -------------------------------------------

def test_events_are_tagged_with_the_current_call():
    rec = _Recorder()
    writer = TelemetryWriter(rec)
    _in_session("room-A", lambda: writer.submit(_event(message="a")))
    _in_session("room-B", lambda: writer.submit(_event(message="b")))
    _run(writer.flush())
    assert {(e["session_id"], e["message"]) for e in rec.events} == {("room-A", "a"), ("room-B", "b")}


def test_conversation_turns_are_extracted_in_order():
    rec = _Recorder()
    writer = TelemetryWriter(rec)

    def call():
        writer.submit(_event("Sakhi said", stage="Stage 8 TTS", message="ತಾಯಿಯ ಹೆಸರು?", event="agent_speech"))
        writer.submit(_event("Transcript final", stage="Stage 5 STT", message="Suma, 24", language="kn"))
        writer.submit(_event("Sakhi said", stage="Stage 8 TTS", message="ಎಷ್ಟು ತಿಂಗಳು?", event="agent_speech"))
    _in_session("room-X", call)
    _run(writer.flush())

    assert [(t["turn_no"], t["speaker"], t["text"]) for t in rec.turns] == [
        (1, "sakhi", "ತಾಯಿಯ ಹೆಸರು?"),
        (2, "asha", "Suma, 24"),
        (3, "sakhi", "ಎಷ್ಟು ತಿಂಗಳು?"),
    ]
    assert rec.turns[1]["language"] == "kn"


def test_interim_and_empty_transcripts_are_not_persisted():
    rec = _Recorder()
    writer = TelemetryWriter(rec)

    def call():
        writer.submit(_event("Transcript interim", stage="Stage 5 STT", status="info", message="Su"))
        writer.submit(_event("Transcript final", stage="Stage 5 STT", message="<empty transcript>"))
    _in_session("room-Y", call)
    _run(writer.flush())
    assert [e["label"] for e in rec.events] == ["Transcript final"]   # interim dropped
    assert rec.turns == []                                           # empty is not a turn


def test_turn_counters_are_per_call_and_can_be_forgotten():
    rec = _Recorder()
    writer = TelemetryWriter(rec)
    for room in ("r1", "r2"):
        _in_session(room, lambda: writer.submit(
            _event("Transcript final", stage="Stage 5 STT", message="hi")))
    _run(writer.flush())
    assert sorted((t["session_id"], t["turn_no"]) for t in rec.turns) == [("r1", 1), ("r2", 1)]
    writer.forget_session("r1")
    assert "r1" not in writer._turn_counters


# --- component health -------------------------------------------------------

def test_health_is_coalesced_not_one_row_per_event():
    rec = _Recorder()
    writer = TelemetryWriter(rec, health_min_interval=5.0)
    for _ in range(1000):
        writer.submit(_event(status="ok"))
    _run(writer.flush())
    assert len(rec.health) == 1 and rec.health[0]["component"] == "tts"


def test_a_status_change_is_written_immediately_even_when_throttled():
    now = [100.0]
    rec = _Recorder()
    writer = TelemetryWriter(rec, health_min_interval=60.0, clock=lambda: now[0])
    writer.submit(_event(status="ok", stage="Stage 7 LLM"))
    _run(writer.flush())
    now[0] += 1  # well inside the 60 s throttle window
    writer.submit(_event(status="error", stage="Stage 7 LLM", label="LLM failed", message="429"))
    _run(writer.flush())
    last = rec.health[-1]
    assert last["component"] == "llm" and last["last_status"] == "error"
    assert last["last_error"] == "LLM failed: 429"
    assert last["error_count"] == 1


def test_unchanged_status_is_throttled():
    now = [0.0]
    rec = _Recorder()
    writer = TelemetryWriter(rec, health_min_interval=5.0, clock=lambda: now[0])
    writer.submit(_event(status="ok"))
    _run(writer.flush())
    now[0] += 1
    writer.submit(_event(status="ok"))
    _run(writer.flush())
    assert len(rec.health) == 1          # second ok inside the window: skipped
    now[0] += 5
    writer.submit(_event(status="ok"))
    _run(writer.flush())
    assert len(rec.health) == 2          # window elapsed: heartbeat written


def test_info_events_do_not_change_health():
    rec = _Recorder()
    writer = TelemetryWriter(rec)
    writer.submit(_event(status="info", label="Voice selected"))
    _run(writer.flush())
    assert rec.health == [] and len(rec.events) == 1


# --- resilience ---------------------------------------------------------------

def test_a_failed_flush_is_retried_not_lost():
    rec = _Recorder(fail_times=1)
    writer = TelemetryWriter(rec)
    writer.submit(_event(message="keep me"))
    _run(writer.flush())                 # database down
    assert rec.events == [] and writer.failures == 1
    _run(writer.flush())                 # database back
    assert [e["message"] for e in rec.events] == ["keep me"]


def test_the_buffer_is_bounded_and_drops_oldest():
    rec = _Recorder()
    writer = TelemetryWriter(rec, max_buffer=3)
    for i in range(5):
        writer.submit(_event(message=str(i)))
    assert writer.dropped == 2
    _run(writer.flush())
    assert [e["message"] for e in rec.events] == ["2", "3", "4"]


def test_large_backlogs_are_written_in_bounded_batches():
    rec = _Recorder()
    writer = TelemetryWriter(rec, batch_size=100, max_buffer=10_000)
    for i in range(250):
        writer.submit(_event(message=str(i)))
    _run(writer.flush())
    assert [len(b[0]) for b in rec.batches] == [100, 100, 50]


# --- end to end ---------------------------------------------------------------

def test_events_reach_sqlite_within_one_flush_interval():
    """The dashboard latency target: an event is queryable within ~250 ms
    (one flush interval) plus the write itself."""
    async def go():
        path = Path(tempfile.mkdtemp()) / "t.sqlite3"
        repo = await SQLitePatientRepository(path).load()
        writer = TelemetryWriter(repo.record_telemetry, flush_interval=0.05)
        writer.start()
        token = CURRENT_SESSION.set("room-e2e")
        try:
            writer.submit(_event("Transcript final", stage="Stage 5 STT", message="hello"))
            writer.submit(_event("LLM failed", stage="Stage 7 LLM", status="error", message="timeout"))
            started = time.perf_counter()
            while time.perf_counter() - started < 2.0:
                await asyncio.sleep(0.02)
                if writer.written >= 3:  # event + turn + health rows
                    break
            elapsed = time.perf_counter() - started
        finally:
            CURRENT_SESSION.reset(token)
            await writer.aclose()
            await repo.close()
        assert elapsed < 0.5, f"telemetry took {elapsed * 1000:.0f} ms to land"

        import sqlite3
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        events = [dict(r) for r in conn.execute("SELECT * FROM pipeline_events ORDER BY id")]
        turns = [dict(r) for r in conn.execute("SELECT * FROM call_turns")]
        health = {r["component"]: dict(r) for r in conn.execute("SELECT * FROM component_health")}
        conn.close()
        assert {e["session_id"] for e in events} == {"room-e2e"}
        assert turns[0]["speaker"] == "asha" and turns[0]["text"] == "hello"
        assert health["llm"]["last_status"] == "error"
        assert health["llm"]["last_error"] == "LLM failed: timeout"
        assert json.loads(events[0]["details"] or "{}") == {}
    _run(go())


def test_an_error_stays_attributed_to_the_call_it_happened_on():
    """Regression (found in the dashboard preview): the error detail showed
    the session of the LATEST event. An error on call A followed by a clean
    call B was then blamed on call B."""
    async def go():
        path = Path(tempfile.mkdtemp()) / "attr.sqlite3"
        repo = await SQLitePatientRepository(path).load()
        writer = TelemetryWriter(repo.record_telemetry, health_min_interval=0)
        token = CURRENT_SESSION.set("call-A")
        writer.submit(_event(stage="Stage 7 LLM", status="error", label="LLM failed", message="429"))
        CURRENT_SESSION.reset(token)
        await writer.flush()
        token = CURRENT_SESSION.set("call-B")
        writer.submit(_event(stage="Stage 7 LLM", status="ok", label="LLM ok"))
        CURRENT_SESSION.reset(token)
        await writer.flush()
        await repo.close()
        import sqlite3
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        row = dict(conn.execute("SELECT * FROM component_health WHERE component='llm'").fetchone())
        conn.close()
        assert row["last_session"] == "call-B"          # latest activity
        assert row["last_error_session"] == "call-A"    # where it actually failed
    _run(go())


def test_health_upsert_keeps_error_history_after_recovery():
    """Green after red must still show WHEN it was red."""
    async def go():
        path = Path(tempfile.mkdtemp()) / "h.sqlite3"
        repo = await SQLitePatientRepository(path).load()
        writer = TelemetryWriter(repo.record_telemetry)
        writer.submit(_event(stage="Stage 7 LLM", status="error", label="LLM failed", message="429"))
        await writer.flush()
        writer.submit(_event(stage="Stage 7 LLM", status="ok", label="LLM ok"))
        await writer.flush()
        await repo.close()
        import sqlite3
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        row = dict(conn.execute("SELECT * FROM component_health WHERE component='llm'").fetchone())
        conn.close()
        assert row["last_status"] == "ok"
        assert row["last_error"] == "LLM failed: 429" and row["last_error_at"]
        assert row["error_count"] == 1
    _run(go())
