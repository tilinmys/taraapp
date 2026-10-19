"""Batched, non-blocking telemetry for the admin dashboard.

WHAT THIS IS FOR
----------------
Every pipeline event (agent.py's `pipeline_event`) is turned into up to three
kinds of database row:

  - `pipeline_events`  - the log, now tagged with the call's session id
  - `call_turns`       - the ASHA <-> Sakhi conversation, in order
  - `component_health` - one live row per STT/LLM/TTS/... for the red/green
                         board

THE ONE RULE: THE DATABASE NEVER SLOWS DOWN A SPOKEN REPLY
-----------------------------------------------------------
`submit()` runs on the voice hot path, inside `pipeline_event()`, sometimes
several times per second. It therefore does only O(1) in-memory work - a
bounded-deque append and a dict update - and returns. It never awaits, never
does I/O and never raises. A background task drains the buffer every
`flush_interval` seconds and writes ONE batch per flush through the
repository's `record_telemetry()`, so a 10-minute call is ~2,400 flush ticks
at most, not one round trip per event.

Data-structure choices, and why:

  - `collections.deque(maxlen=N)` as the buffer: O(1) append/popleft, and the
    bound means a long database outage costs the OLDEST telemetry (counted in
    `dropped`), never unbounded memory growth and never a blocked call. CPython
    documents deque append/popleft as atomic, so producers on other threads
    need no lock for the buffer itself.
  - Health is COALESCED, not queued: a dict keyed by component holds only the
    latest pending row (O(1) per event, O(components) per flush). A status
    CHANGE is written on the next flush; an unchanged status is throttled to
    one write per `health_min_interval` seconds. Thousands of "TTS ok" events
    in a call become a handful of upserts.
  - Turn numbers come from a per-session counter dict - O(1), no
    `SELECT MAX(turn_no)` round trip per utterance.

Interim transcripts are not persisted: they arrive several times a second
while someone speaks, carry nothing the final transcript does not, and would
dominate the table. They stay in logs/pipeline_events.jsonl as before.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

logger = logging.getLogger("sakhi.telemetry")

# The call (LiveKit room name) the current code is running on behalf of. Set
# once at the top of agent.py's entrypoint; asyncio copies context into every
# task created afterwards, so every pipeline_event raised anywhere in that
# call - tools, turn handlers, TTS nodes - is attributed to it with no
# argument threading.
CURRENT_SESSION: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "sakhi_current_session", default=None
)

# pipeline_event stage labels (agent.py STAGES) -> dashboard component. The
# dashboard draws exactly these boxes.
COMPONENT_BY_STAGE: dict[str, str] = {
    "Stage 0 Worker": "worker",
    "Stage 1 Auth": "livekit",
    "Stage 2 WebRTC": "livekit",
    "Stage 3 Microphone": "microphone",
    "Stage 4 Worker Dispatch": "livekit",
    "Stage 5 STT": "stt",
    "Stage 6 Semantic Turn": "turn",
    "Stage 7 LLM": "llm",
    "Stage 8 TTS": "tts",
    "Stage 9 Playback": "playback",
    "Stage 10 Tools DB": "database",
}

# Only these statuses say anything about a component's health. "info" events
# are descriptive ("provider selected", "config") and leave health untouched.
_HEALTH_STATUSES = {"ok", "warn", "error"}

SKIPPED_LABELS = frozenset({"Transcript interim"})
_EMPTY_TRANSCRIPT = "<empty transcript>"

MAX_MESSAGE_CHARS = 1000
MAX_DETAILS_CHARS = 4000

Sink = Callable[[list, list, list], Awaitable[None]]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _details_json(details: Any) -> str | None:
    if not details:
        return None
    try:
        text = json.dumps(details, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(details)
    return text[:MAX_DETAILS_CHARS]


class TelemetryWriter:
    """Buffers pipeline events and writes them to the database in batches.

    `sink` is an async callable `(events, turns, health) -> None` - normally
    a repository's `record_telemetry`. Injected rather than imported so this
    module is testable without a database.
    """

    def __init__(
        self,
        sink: Sink,
        *,
        flush_interval: float = 0.25,
        batch_size: int = 500,
        max_buffer: int = 5000,
        health_min_interval: float = 5.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._sink = sink
        self.flush_interval = flush_interval
        self.batch_size = batch_size
        self._events: deque[dict[str, Any]] = deque(maxlen=max_buffer)
        self._turns: deque[dict[str, Any]] = deque(maxlen=max_buffer)
        self._health: dict[str, dict[str, Any]] = {}
        self._health_last: dict[str, tuple[str, float]] = {}
        self._turn_counters: dict[str, int] = {}
        # Guards only the small dicts above (health coalescing, turn
        # counters) - never held across I/O.
        self._lock = threading.Lock()
        self._clock = clock
        self._health_min_interval = health_min_interval
        self._task: asyncio.Task | None = None
        self._flush_lock: asyncio.Lock | None = None
        self.dropped = 0
        self.written = 0
        self.failures = 0

    # --- producer side (hot path) -------------------------------------

    def submit(self, event: dict[str, Any]) -> None:
        """Accept one pipeline event. O(1), non-blocking, never raises."""
        try:
            self._submit(event)
        except Exception:  # observability must never break a call
            logger.debug("telemetry submit failed", exc_info=True)

    def _submit(self, event: dict[str, Any]) -> None:
        label = str(event.get("label") or "")
        if label in SKIPPED_LABELS:
            return
        details = event.get("details") or {}
        session_id = CURRENT_SESSION.get()
        ts = str(event.get("ts") or _now_iso())
        stage = str(event.get("stage") or "unknown")
        status = str(event.get("status") or "info")
        message = str(event.get("message") or "")[:MAX_MESSAGE_CHARS]

        self._append(self._events, {
            "session_id": session_id,
            "ts": ts,
            "stage": stage,
            "status": status,
            "label": label,
            "message": message,
            "event": details.get("event") if isinstance(details, dict) else None,
            "details": _details_json(details),
        })

        speaker = self._speaker_for(label, details, message)
        if speaker and session_id:
            with self._lock:
                turn_no = self._turn_counters.get(session_id, 0) + 1
                self._turn_counters[session_id] = turn_no
            self._append(self._turns, {
                "session_id": session_id,
                "turn_no": turn_no,
                "speaker": speaker,
                "text": message,
                "language": details.get("language") if isinstance(details, dict) else None,
                "ts": ts,
            })

        component = COMPONENT_BY_STAGE.get(stage)
        if component and status in _HEALTH_STATUSES:
            self._note_health(component, status, label, message, session_id, ts)

    @staticmethod
    def _speaker_for(label: str, details: Any, message: str) -> str | None:
        if not message or message == _EMPTY_TRANSCRIPT:
            return None
        if label == "Transcript final":
            return "asha"
        if isinstance(details, dict) and details.get("event") == "agent_speech":
            return "sakhi"
        return None

    def _append(self, buffer: deque, row: dict[str, Any]) -> None:
        if buffer.maxlen is not None and len(buffer) >= buffer.maxlen:
            self.dropped += 1  # deque evicts the oldest row on append
        buffer.append(row)

    def _note_health(
        self, component: str, status: str, label: str, message: str,
        session_id: str | None, ts: str,
    ) -> None:
        now = self._clock()
        with self._lock:
            pending = self._health.get(component)
            previous = self._health_last.get(component)
            changed = previous is None or previous[0] != status
            due = previous is None or (now - previous[1]) >= self._health_min_interval
            if not (changed or due or status == "error" or pending is not None):
                return
            row = pending or {"component": component, "error_count": 0,
                              "last_error": None, "last_error_at": None,
                              "last_error_session": None, "last_ok_at": None}
            row.update({
                "last_status": status,
                "last_label": label,
                "last_message": message,
                "last_session": session_id,
                "updated_at": ts,
            })
            if status == "error":
                row["error_count"] = int(row.get("error_count") or 0) + 1
                row["last_error"] = f"{label}: {message}"[:MAX_MESSAGE_CHARS]
                row["last_error_at"] = ts
                row["last_error_session"] = session_id
            elif status == "ok":
                row["last_ok_at"] = ts
            self._health[component] = row
            self._health_last[component] = (status, now)

    # --- consumer side -------------------------------------------------

    @staticmethod
    def _drain(buffer: deque, limit: int) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        while buffer and len(out) < limit:
            out.append(buffer.popleft())
        return out

    async def flush(self) -> int:
        """Write everything buffered right now. Returns rows written.

        On a sink failure the batch is put back at the front of the buffer
        (bounded - anything beyond max_buffer is dropped and counted), so a
        transient database blip delays telemetry instead of losing it.
        """
        if self._flush_lock is None:
            self._flush_lock = asyncio.Lock()
        async with self._flush_lock:
            total = 0
            while True:
                events = self._drain(self._events, self.batch_size)
                turns = self._drain(self._turns, self.batch_size)
                with self._lock:
                    health = list(self._health.values())
                    self._health = {}
                if not (events or turns or health):
                    return total
                try:
                    await self._sink(events, turns, health)
                except Exception:
                    self.failures += 1
                    logger.warning("telemetry flush failed; will retry", exc_info=True)
                    self._requeue(self._events, events)
                    self._requeue(self._turns, turns)
                    with self._lock:
                        for row in health:
                            self._health.setdefault(row["component"], row)
                    return total
                batch = len(events) + len(turns) + len(health)
                self.written += batch
                total += batch

    def _requeue(self, buffer: deque, rows: list[dict[str, Any]]) -> None:
        for row in reversed(rows):
            if buffer.maxlen is not None and len(buffer) >= buffer.maxlen:
                self.dropped += 1
                continue
            buffer.appendleft(row)

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self.flush_interval)
            try:
                await self.flush()
            except Exception:  # never let the loop die
                logger.warning("telemetry loop error", exc_info=True)

    def start(self) -> None:
        """Start the background flush loop on the running event loop.

        Idempotent per loop. If the process has moved to a NEW event loop (a
        later job), the old task and lock belong to a loop that no longer
        runs - both are replaced rather than reused, since asyncio primitives
        are loop-bound.
        """
        loop = asyncio.get_running_loop()
        if self._task is not None and not self._task.done() and self._task.get_loop() is loop:
            return
        self._flush_lock = None
        self._task = loop.create_task(self._run(), name="sakhi-telemetry-writer")

    async def aclose(self) -> None:
        """Stop the loop and write whatever is left."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        await self.flush()

    def forget_session(self, session_id: str | None) -> None:
        """Drop a finished call's turn counter so the dict never grows."""
        if session_id:
            with self._lock:
                self._turn_counters.pop(session_id, None)

    @property
    def stats(self) -> dict[str, int]:
        return {
            "buffered_events": len(self._events),
            "buffered_turns": len(self._turns),
            "written": self.written,
            "dropped": self.dropped,
            "failures": self.failures,
        }
