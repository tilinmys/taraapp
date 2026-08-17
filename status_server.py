"""Lightweight public HTTP surface for the LiveKit agent worker process.

Only needed when the worker runs on its own host (e.g. Railway) separate
from the frontend/token API (e.g. Vercel) - see DEPLOY.md. When everything
ran on one machine, frontend/local_server.py read the worker's log files
directly off the shared filesystem; once the worker is on a different host
entirely, there is no shared filesystem, so the worker exposes this instead
and the Vercel-hosted /api/worker-status and /api/logs functions proxy to it
server-side (the browser never talks to this directly).

Deliberately NOT part of agent.py's own asyncio event loop - the livekit-
agents CLI (`cli.run_app`) owns that loop entirely. This runs in a plain
background thread using the stdlib http.server, same pattern already used
by frontend/local_server.py, so the health/log-parsing logic here is a
straight port of that file's worker_status()/handle_logs() - same shapes,
same regexes, same defaults.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import db_helper
import voice_catalog

PROJECT_ROOT = Path(__file__).resolve().parent
PIPELINE_LOG_PATH = Path(os.getenv("PIPELINE_LOG_PATH", "logs/pipeline_events.jsonl"))
WORKER_LOG_PATH = PROJECT_ROOT / "logs" / "worker_background.log"

WORKER_READY_RE = re.compile(r"registered worker")
WORKER_DOWN_RE = re.compile(
    r"failed to connect to livekit|worker connection closed unexpectedly|getaddrinfo failed|signal connection timed out",
    re.IGNORECASE,
)
LOG_TS_RE = re.compile(r"(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
WORKER_READY_MAX_AGE_SECONDS = int(os.getenv("WORKER_READY_MAX_AGE_SECONDS", "86400"))
WORKER_HEALTH_TAIL_LINES = int(os.getenv("WORKER_HEALTH_TAIL_LINES", "100000"))


def worker_status() -> dict:
    if not WORKER_LOG_PATH.exists():
        return {"ready": False, "reason": "worker log not found"}

    events = []
    for order, line in enumerate(
        WORKER_LOG_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()[-WORKER_HEALTH_TAIL_LINES:]
    ):
        match = LOG_TS_RE.search(line)
        ts_key = match.group("ts") if match else ""
        if WORKER_READY_RE.search(line):
            events.append((ts_key, order, "ready", line.strip()))
        elif WORKER_DOWN_RE.search(line):
            events.append((ts_key, order, "down", line.strip()))

    if not events:
        return {"ready": False, "reason": "worker has not emitted health events yet"}

    events.sort(key=lambda item: (item[0], item[1]))
    last_ready_line = ""
    last_ready_ts = ""
    last_down_line = ""
    last_kind = ""
    for _ts, _order, kind, line in events:
        last_kind = kind
        if kind == "ready":
            last_ready_line = line
            last_ready_ts = _ts
        else:
            last_down_line = line

    if not last_ready_line:
        return {"ready": False, "reason": "worker has not registered yet"}
    if last_kind == "down":
        return {"ready": False, "reason": "worker is reconnecting", "last_error": last_down_line}
    if last_ready_ts:
        try:
            age_seconds = max(0, time.time() - datetime.strptime(last_ready_ts, "%Y-%m-%d %H:%M:%S").timestamp())
            if age_seconds > WORKER_READY_MAX_AGE_SECONDS:
                return {
                    "ready": False,
                    "reason": "worker registration is stale",
                    "age_seconds": round(age_seconds, 1),
                    "last_ready": last_ready_line,
                }
        except ValueError:
            pass
    return {"ready": True, "reason": "worker registered", "last_ready": last_ready_line}


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def provider_summary() -> dict:
    """Env-only snapshot of what agent.py's build_stt()/build_llm() would
    construct - no provider objects are built here (no API calls, no
    side effects), so this is safe to call on every UI page load. Mirrors
    the exact env vars and defaults those functions read; if this drifts
    from agent.py, the defaults below should be updated to match.
    """
    # THIS HAD DRIFTED FAR FROM THE PIPELINE IT CLAIMS TO DESCRIBE.
    #
    # It reported OpenRouter as the primary LLM (Groq now leads, and the two are
    # raced - see llm_hedge.py), and STT as Deepgram-or-AssemblyAI when
    # build_stt() defaults to Sarvam and AssemblyAI is not wired at all. It also
    # had no TTS section, on an agent whose headline choice is Sarvam vs Gnani.
    # A dashboard that misreports the pipeline is worse than no dashboard.
    openrouter_key = os.getenv("OPENROUTER_API_KEY")
    openrouter_model = os.getenv("OPENROUTER_LLM_MODEL", "meta-llama/llama-3.3-70b-instruct")
    groq_model = os.getenv("GROQ_MODEL_T1", "openai/gpt-oss-120b")
    openai_model = os.getenv("OPENAI_LLM_MODEL", os.getenv("LLM_MODEL", "gpt-4o-mini"))
    # Must match agent.groq_api_keys() EXACTLY, including the numbered
    # variants. It previously checked only GROQ_API_KEYS/GROQ_API_KEY, so a
    # worker configured through GROQ_API_KEY_1/_2 (as this deployment is)
    # counted zero keys here - and the dashboard reported OpenRouter as the
    # primary LLM while the worker was actually running Groq.
    _seen: set[str] = set()
    for _name in ("GROQ_API_KEYS", "GROQ_API_KEY", "GROQ_API_KEY_1",
                  "GROQ_API_KEY_2", "GROQ_API_KEY_3"):
        for _part in (os.getenv(_name) or "").split(","):
            if _part.strip():
                _seen.add(_part.strip())
    groq_key_count = len(_seen)

    # Mirrors build_stt(): Sarvam unless STT_PRIMARY explicitly says deepgram.
    stt_primary_deepgram = (
        os.getenv("STT_PRIMARY", "sarvam").strip().lower() == "deepgram"
        and bool(os.getenv("DEEPGRAM_API_KEY"))
    )

    # Mirrors build_llm(): Groq group leads, OpenRouter is the cross-vendor
    # backup, and when both exist they are HEDGED rather than queued.
    if groq_key_count:
        llm_primary = {"provider": "groq", "model": groq_model, "key_count": groq_key_count}
        llm_fallback = (
            {"provider": "openrouter", "model": openrouter_model}
            if openrouter_key else {"provider": "openai", "model": openai_model}
        )
    elif openrouter_key:
        llm_primary = {"provider": "openrouter", "model": openrouter_model}
        llm_fallback = {"provider": "openai", "model": openai_model}
    else:
        llm_primary = {"provider": "openai", "model": openai_model}
        llm_fallback = {"provider": "none", "model": "-"}

    hedged = bool(groq_key_count and openrouter_key and _env_flag("SAKHI_LLM_HEDGE", True))

    # Mirrors build_tts(): same-language chain only. The backup is the OTHER
    # Indic provider, never an English voice - a cross-language failover
    # changed the voice mid-sentence (see changed.md 14.1).
    tts_provider = os.getenv("TTS_PROVIDER", "sarvam").strip().lower()
    sarvam_voice = os.getenv("SARVAM_SPEAKER", "manisha")
    gnani_voice = os.getenv("GNANI_VOICE", "Janaki")
    if tts_provider == "gnani":
        tts_primary = {"provider": "gnani", "model": "timbre-v2.5", "voice": gnani_voice}
        tts_fallback = {"provider": "sarvam", "model": "bulbul:v2", "voice": sarvam_voice}
    else:
        tts_primary = {"provider": "sarvam", "model": "bulbul:v2", "voice": sarvam_voice}
        tts_fallback = {"provider": "gnani", "model": "timbre-v2.5", "voice": gnani_voice}

    return {
        "llm": {
            "primary": llm_primary,
            "fallback": llm_fallback,
            "hedged": hedged,
            "hedge_delay_s": float(os.getenv("LLM_HEDGE_DELAY", "0.8")),
            "groq_timeout_s": float(os.getenv("GROQ_ATTEMPT_TIMEOUT", "1.5")),
            "openrouter_timeout_s": float(os.getenv("OPENROUTER_ATTEMPT_TIMEOUT", "4.0")),
        },
        "tts": {
            "primary": tts_primary,
            "fallback": tts_fallback,
            "same_language_chain": True,
            "pace": float(os.getenv("SARVAM_PACE", "1.0")),
        },
        "stt": {
            "primary": (
                {"provider": "deepgram", "model": os.getenv("DEEPGRAM_STT_MODEL", "nova-3")}
                if stt_primary_deepgram
                else {"provider": "sarvam", "model": "saarika"}
            ),
            "fallback": {"provider": "none", "model": "-"},
        },
        "turn_detection": {
            "mode": os.getenv("VOICE_TURN_DETECTION_MODE", "vad"),
            "min_endpointing_delay_s": float(os.getenv("MIN_ENDPOINTING_DELAY", "0.35")),
            "max_endpointing_delay_s": float(os.getenv("MAX_ENDPOINTING_DELAY", "1.2")),
        },
        "features": {
            "fast_path_enabled": _env_flag("VOICE_FAST_PATH_ENABLED", True),
            "filler_threshold_ms": int(os.getenv("VOICE_FILLER_THRESHOLD_MS", "900")),
            "async_slot_refresh_enabled": _env_flag("VOICE_ASYNC_SLOT_REFRESH_ENABLED", True),
            "incremental_tts_enabled": _env_flag("VOICE_INCREMENTAL_TTS_ENABLED", True),
        },
    }


def maternal_db_status() -> dict:
    """Health of the SAKHI store - the maternal database, not the legacy clinic one.

    Opened read-only on each request rather than holding a connection: this is
    a dashboard poll, and a long-lived handle in the HTTP thread would contend
    with the write lock the agent needs during a live call.
    """
    backend = os.getenv("SAKHI_REPOSITORY", "sqlite").strip().lower()
    if backend == "json":
        path = Path(os.getenv("SIXTYDB_MOCK_PATH", "mock_sixtydb.json"))
        if not path.exists():
            return {"backend": "json", "ok": False, "error": "file missing", "path": str(path)}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            return {"backend": "json", "ok": False, "error": str(exc), "path": str(path)}
        return {
            "backend": "json", "ok": True, "path": str(path),
            "durable": False,
            "warning": "write-behind: a reading can be lost if the worker dies before flush",
            "counts": {"mothers": len(raw.get("patients", [])),
                       "bp_records": len(raw.get("bp_records", []))},
        }

    path = Path(os.getenv("SAKHI_DB_PATH", "data/sakhi.sqlite3"))
    if not path.exists():
        return {"backend": "sqlite", "ok": False, "path": str(path),
                "error": "database not created yet - run migrate_to_sqlite.py"}
    try:
        import sqlite3

        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        try:
            counts = {}
            for table in ("mothers", "bp_records", "symptom_records",
                          "teleconsults", "call_reports"):
                counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
            integrity = conn.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            conn.close()
    except Exception as exc:
        return {"backend": "sqlite", "ok": False, "path": str(path), "error": str(exc)}

    return {
        "backend": "sqlite", "ok": integrity == "ok", "path": str(path),
        "durable": True, "journal_mode": journal, "integrity": integrity,
        "postgres_portable": True, "counts": counts,
    }


def config_payload() -> dict:
    return {"providers": provider_summary(), "tts_catalog": voice_catalog.as_json_catalog()}


def _read_logs(since: int) -> dict:
    lines = []
    if PIPELINE_LOG_PATH.exists():
        lines = PIPELINE_LOG_PATH.read_text(encoding="utf-8", errors="ignore").splitlines()

    events = []
    next_cursor = since
    for offset, line in enumerate(lines[since : since + 250], start=since):
        next_cursor = offset + 1
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            events.append(
                {
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "stage": "Stage ? Logs",
                    "status": "warn",
                    "label": "Malformed log line",
                    "message": line,
                    "details": {},
                }
            )
    return {"next": min(len(lines), next_cursor), "events": events}


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature
        pass  # keep the worker's own log output free of HTTP access-log noise

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_json({"status": "MyStree Voice Agent Worker is running"})
            return
        if parsed.path in {"/health", "/api/worker-status"}:
            self._send_json(worker_status())
            return
        if parsed.path in {"/config", "/api/config"}:
            self._send_json(config_payload())
            return
        if parsed.path in {"/doctors", "/api/doctors"}:
            self._send_json({"doctors": db_helper.get_doctors()})
            return
        if parsed.path in {"/schedule", "/api/schedule"}:
            params = parse_qs(parsed.query)
            doctor = params.get("doctor", [""])[0]
            week_start = params.get("week_start", [None])[0]
            if not doctor:
                self._send_json({"error": "doctor query param is required"}, status=400)
                return
            self._send_json(db_helper.get_week_schedule(doctor, week_start))
            return
        if parsed.path in {"/stats", "/api/stats"}:
            self._send_json(db_helper.get_dashboard_stats())
            return
        if parsed.path in {"/slot-events", "/api/slot-events"}:
            params = parse_qs(parsed.query)
            try:
                since = max(-1, int(params.get("since", ["0"])[0]))
            except ValueError:
                since = 0
            self._send_json(db_helper.get_slot_events(since))
            return
        if parsed.path in {"/db", "/api/db"}:
            self._send_json(maternal_db_status())
            return

        if parsed.path in {"/logs", "/api/logs"}:
            params = parse_qs(parsed.query)
            raw_since = params.get("since", ["0"])[0]
            if raw_since == "latest":
                lines = PIPELINE_LOG_PATH.read_text(encoding="utf-8", errors="ignore").splitlines() if PIPELINE_LOG_PATH.exists() else []
                self._send_json({"next": len(lines), "events": []})
                return
            try:
                since = max(0, int(raw_since))
            except ValueError:
                since = 0
            self._send_json(_read_logs(since))
            return
        self.send_error(404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}") if length else {}
            if not isinstance(body, dict):
                raise ValueError("body must be a JSON object")
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json({"error": f"invalid JSON body: {exc}"}, status=400)
            return

        if parsed.path in {"/doctors", "/api/doctors"}:
            doctor, reason = db_helper.add_doctor(
                str(body.get("name", "")), str(body.get("speciality", "") or "General")
            )
            if doctor is None:
                self._send_json({"error": reason}, status=409 if reason == "already_exists" else 400)
                return
            self._send_json({"doctor": doctor}, status=201)
            return

        if parsed.path in {"/slots", "/api/slots"}:
            ok, reason = db_helper.add_slot(
                str(body.get("doctor", "")), str(body.get("date", "")), str(body.get("time", ""))
            )
            if not ok:
                self._send_json({"error": reason}, status=409 if reason == "slot_exists" else 400)
                return
            self._send_json({"ok": True}, status=201)
            return

        self.send_error(404)

    def _send_json(self, payload: dict, status: int = 200) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def start_status_server(port: int | None = None) -> ThreadingHTTPServer | None:
    """Start the status/logs HTTP server in a daemon thread.

    Returns None (and logs a warning) instead of raising if the port is
    already taken - this is a monitoring convenience, not core to call
    handling, so it should never take down the worker if it can't bind.
    """
    import logging

    logger = logging.getLogger("status_server")
    resolved_port = port if port is not None else int(os.getenv("PORT", "8080"))
    try:
        server = ThreadingHTTPServer(("0.0.0.0", resolved_port), _Handler)
    except OSError as exc:
        logger.warning("status server could not bind to port %s: %s", resolved_port, exc)
        return None

    thread = threading.Thread(target=server.serve_forever, name="status-server", daemon=True)
    thread.start()
    logger.info("status server listening on 0.0.0.0:%s (/health, /logs)", resolved_port)
    return server
