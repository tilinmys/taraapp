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

import hmac
import json
import os
import re
import threading
import time
import queue
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import db_helper
import voice_catalog
from sakhi_dashboard_store import get_store
from sakhi_domain import SymptomSet
from sakhi_schema import SYMPTOM_COLUMNS

# SSE client registry for real-time log streaming
_sse_clients: list[queue.Queue] = []
_sse_lock = threading.Lock()

PROJECT_ROOT = Path(__file__).resolve().parent
PIPELINE_LOG_PATH = Path(os.getenv("PIPELINE_LOG_PATH", "logs/pipeline_events.jsonl"))
WORKER_LOG_PATH = PROJECT_ROOT / "logs" / "worker_background.log"

WORKER_READY_RE = re.compile(r"registered worker")
WORKER_DOWN_RE = re.compile(
    r"failed to connect to livekit|worker connection closed unexpectedly|getaddrinfo failed|signal connection timed out",
    re.IGNORECASE,
)
LOG_TS_RE = re.compile(r"(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
# 0 = no age limit (default since 2026-09-28). A healthy worker registers ONCE
# and then stays connected, so "last registration older than 24 h" said nothing
# about health - it blocked every call on a worker that had simply been up for
# more than a day ("Worker not ready yet", 2026-09-27 23:05 onward). Staleness
# is now judged against THIS process's start instead (_PROCESS_STARTED_AT).
WORKER_READY_MAX_AGE_SECONDS = int(os.getenv("WORKER_READY_MAX_AGE_SECONDS", "0"))
# status_server runs inside the worker process: when this module was imported
# is when the current worker started. A "registered worker" line older than
# this belongs to a previous run and must not count as ready.
_PROCESS_STARTED_AT = time.time()
_START_SKEW_SECONDS = 120
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
            ready_at = datetime.strptime(last_ready_ts, "%Y-%m-%d %H:%M:%S").timestamp()
            if ready_at < _PROCESS_STARTED_AT - _START_SKEW_SECONDS:
                return {
                    "ready": False,
                    "reason": "worker has not registered since it restarted",
                    "last_ready": last_ready_line,
                }
            age_seconds = max(0, time.time() - ready_at)
            if WORKER_READY_MAX_AGE_SECONDS and age_seconds > WORKER_READY_MAX_AGE_SECONDS:
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

    # Mirrors build_stt()'s real dispatch order exactly (agent.py) - this had
    # drifted stale twice already: it never grew a "smallest" branch when
    # SmallestAI STT was wired, and it never grew a "gnani" branch either
    # (2026-09-04) - both times meaning this dashboard kept reporting
    # "deepgram" as primary (the `elif _deepgram_key` fallthrough, which
    # fires whenever a Deepgram key merely EXISTS, regardless of what
    # STT_PRIMARY actually names) while the real worker was listening
    # through a different provider entirely. Fixed by mirroring
    # build_stt()'s if/elif chain directly instead of hand-picking two of
    # its four branches.
    _stt_primary_requested = os.getenv("STT_PRIMARY", "deepgram").strip().lower()
    _deepgram_key = bool(os.getenv("DEEPGRAM_API_KEY"))
    _sarvam_key = bool(os.getenv("SARVAM_API_KEY"))
    _smallest_key = bool(os.getenv("SMALLEST_API_KEY"))
    _gnani_key = bool(os.getenv("GNANI_API_KEY"))
    if _stt_primary_requested == "gnani" and _gnani_key:
        stt_primary_name = "gnani"
    elif _stt_primary_requested == "smallest" and _smallest_key:
        stt_primary_name = "smallest"
    elif _stt_primary_requested == "sarvam" and _sarvam_key:
        stt_primary_name = "sarvam"
    elif _deepgram_key:
        stt_primary_name = "deepgram"
    else:
        stt_primary_name = "sarvam"
    stt_fallback_enabled = _env_flag("VOICE_STT_FALLBACK_ENABLED", True)
    _STT_MODELS = {
        "deepgram": os.getenv("DEEPGRAM_STT_MODEL", "nova-3"),
        "sarvam": "saarika",
        "smallest": os.getenv("SMALLEST_STT_MODEL", "pulse"),
        "gnani": "stt/v3",
    }
    # Only Deepgram<->Sarvam ever had a real fallback pair in build_stt();
    # Gnani and SmallestAI return bare (no backup branch exists for them
    # there either), so the dashboard must not invent one.
    _stt_fallback_name = (
        "sarvam" if stt_primary_name == "deepgram"
        else "deepgram" if stt_primary_name == "sarvam"
        else None
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

    # Mirrors build_tts()'s real dispatch (agent.py). Was missing a
    # "smallest_ai" branch entirely (fell through to the vakyam `else` and
    # mislabeled it) and always populated "fallback"/"fallback_chain" with a
    # real-looking provider regardless of VOICE_TTS_FALLBACK_ENABLED - this
    # deployment runs with that flag false (single provider, no fallback,
    # 2026-09-02 pilot direction), so the dashboard was showing a fallback
    # chain that was never actually wired into any live call. Fixed to
    # report "none" when the flag is off, matching how the STT half of this
    # same function already handled its own fallback flag correctly.
    tts_provider = os.getenv("TTS_PROVIDER", "vakyam").strip().lower()
    sarvam_model = os.getenv("SARVAM_TTS_MODEL", "bulbul:v2").strip()
    sarvam_voice = os.getenv("SARVAM_SPEAKER", "manisha")
    gnani_voice = os.getenv("GNANI_VOICE", "Kaveri")
    vakyam_voice = os.getenv("VAKYAM_VOICE", "Ananya")
    smallest_tts_voice = os.getenv("SMALLEST_TTS_VOICE", "maithili")
    tts_fallback_enabled = _env_flag("VOICE_TTS_FALLBACK_ENABLED", True)
    tts_fallback_chain = None
    if tts_provider == "gnani":
        tts_primary = {"provider": "gnani", "model": "timbre-v2.5", "voice": gnani_voice}
        tts_fallback = {"provider": "sarvam", "model": sarvam_model, "voice": sarvam_voice}
    elif tts_provider == "sarvam":
        tts_primary = {"provider": "sarvam", "model": sarvam_model, "voice": sarvam_voice}
        tts_fallback = {"provider": "gnani", "model": "timbre-v2.5", "voice": gnani_voice}
    elif tts_provider == "smallest_ai":
        tts_primary = {"provider": "smallest_ai", "model": "lightning_v3.1_pro", "voice": smallest_tts_voice}
        tts_fallback = {"provider": "none", "model": "-"}  # build_tts() defines no same-language backup for it
    else:
        tts_primary = {"provider": "vakyam", "model": "raaga-v1", "voice": vakyam_voice}
        tts_fallback = {"provider": "gnani", "model": "timbre-v2.5", "voice": gnani_voice}
        if tts_fallback_enabled:
            tts_fallback_chain = [
                tts_fallback,
                {"provider": "sarvam", "model": sarvam_model, "voice": sarvam_voice},
            ]
    if not tts_fallback_enabled:
        tts_fallback = {"provider": "none", "model": "-"}
        tts_fallback_chain = None

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
            "fallback_chain": tts_fallback_chain,  # None unless Vakyam is primary (3-tier chain)
            "same_language_chain": True,
            "pace": float(os.getenv("SARVAM_PACE", "1.0")),
        },
        "stt": {
            "primary": {"provider": stt_primary_name, "model": _STT_MODELS[stt_primary_name]},
            "fallback": (
                {"provider": _stt_fallback_name, "model": _STT_MODELS[_stt_fallback_name]}
                if stt_fallback_enabled and _stt_fallback_name
                else {"provider": "none", "model": "-"}
            ),
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


def _safe_database_error(exc: Exception) -> str:
    """Return a diagnostic without ever reflecting a database password."""
    text = str(exc)
    for name in ("DATABASE_URL", "POSTGRES_URL"):
        value = os.getenv(name)
        if value:
            text = text.replace(value, f"<{name} redacted>")
    # psycopg normally omits passwords, but adapters and proxies are free to
    # include the URI in their own exception text. Cover that shape too.
    text = re.sub(
        r"(?i)(postgres(?:ql)?://[^:/\s]+:)[^@\s]+@",
        r"\1<redacted>@",
        text,
    )
    return text[:1000]


def maternal_db_status() -> dict:
    """Health of the SAKHI store - the maternal database, not the legacy clinic one.

    Opened read-only on each request rather than holding a connection: this is
    a dashboard poll, and a long-lived handle in the HTTP thread would contend
    with the write lock the agent needs during a live call.
    """
    # Keep this endpoint on the exact same backend-selection seam as the
    # worker and the admin dashboard.  The first Supabase implementation
    # selected Postgres from DATABASE_URL in agent.build_repository(), but
    # this health route still read SAKHI_REPOSITORY (defaulting to sqlite).
    # After cutover it therefore reported a missing/stale local file while
    # clinical writes were succeeding in Postgres - precisely the misleading
    # signal an engineering dashboard must never produce during an incident.
    postgres_dsn = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL")
    backend = ("postgres" if postgres_dsn else
               os.getenv("SAKHI_REPOSITORY", "sqlite").strip().lower())
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

    if backend == "postgres":
        try:
            store = get_store()
            summary = store.summary()
            return {
                "backend": "postgres",
                "ok": True,
                "durable": True,
                # Counts prove that the same schema queried by /dashboard is
                # reachable; never include the DSN or pool details here.
                "counts": {
                    "mothers": int(summary.get("mothers", 0)),
                    "call_reports": int(summary.get("calls", 0)),
                    "teleconsults": int(summary.get("escalations", 0)),
                },
            }
        except Exception as exc:
            return {"backend": "postgres", "ok": False,
                    "error": _safe_database_error(exc)}

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


# Mirrors SQLitePatientRepository.FLAGGABLE_FIELDS (sakhi_sql_repository.py).
# Duplicated rather than imported: this server is a plain sync stdlib thread,
# separate from the agent's asyncio loop (see module docstring), so it reads/
# writes SQLite directly here rather than bridging into the async repository -
# same pattern maternal_db_status() already uses for reads.
REPORT_FLAGGABLE_FIELDS = ("manual_risk_flag", "flagged_to_doctor")


def _recording_s3_client():
    """Lazy boto3 client, or None if boto3/creds aren't available.

    A local-mock dev run has neither installed nor configured - this must
    degrade to "no recording info" rather than crash the whole /reports
    endpoint just because the recording feature isn't set up here."""
    bucket = os.getenv("RECORDING_S3_BUCKET", "")
    if not bucket:
        return None, None
    try:
        import boto3

        return boto3.client("s3", region_name=os.getenv("RECORDING_S3_REGION", "ap-south-1")), bucket
    except Exception:
        return None, None


def _enrich_recording_status(rows: list[dict]) -> list[dict]:
    """Confirms in S3, not just claimed by the DB: a row can say
    recording_status='stopped' the instant egress was told to stop, but the
    file only actually lands in S3 asynchronously afterward (seconds to a
    couple minutes, no webhook receiver here to know exactly when). This is
    the "check whether the recording is actually happening" signal - a real
    HeadObject against the bucket, not just trusting what agent.py wrote at
    call-teardown time. Rows already 'available' skip the re-check (nothing
    changes once it's landed); rows 'none' never had a recording to check.
    """
    client, bucket = _recording_s3_client()
    if client is None:
        for row in rows:
            row["recording_url"] = None
        return rows
    for row in rows:
        status = row.get("recording_status") or "none"
        key = row.get("recording_key")
        if status == "none" or not key:
            row["recording_url"] = None
            continue
        if status != "available":
            try:
                client.head_object(Bucket=bucket, Key=key)
                status = "available"
                row["recording_status"] = "available"
                # Write-through so the next /reports load skips the S3 round
                # trip for this row entirely - see update_recording_status().
                _mark_recording_available(row.get("session_id"))
            except Exception:
                pass  # not there yet (or a transient S3 error) - still 'stopped', dashboard shows "processing"
        if status == "available":
            try:
                row["recording_url"] = client.generate_presigned_url(
                    "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=3600
                )
            except Exception:
                row["recording_url"] = None
        else:
            row["recording_url"] = None
    return rows


def _mark_recording_available(session_id: str | None) -> None:
    try:
        get_store().mark_recording_available(session_id)
    except Exception:
        pass  # best-effort cache write; the next load just re-checks S3


def list_reports(limit: int = 50) -> dict:
    """Recent call_reports rows, most recent first - feeds the reports page.

    Reads through sakhi_dashboard_store (SQLite or Postgres, same backend as
    the worker) rather than opening the SQLite file directly - see that
    module's docstring for the silent-staleness bug this replaced."""
    store = get_store()
    if not store.available():
        return {"reports": []}
    return {"reports": _enrich_recording_status(store.list_reports(limit))}


# MCP (Mother and Child Protection) card / RCH register field mapping
# (2026-08-30, Module C). DRAFT - this maps the fields Sakhi already collects
# onto plausible MCP-card/RCH-register field names; it has NOT been checked
# against the actual physical government form layout. Verify against a real
# card before this is used as anything but an on-screen visit summary.
#
# call_reports (one row per call) has no clinical values of its own beyond
# risk_level/escalated/intake progress - the actual readings live keyed by
# patient_id in mothers/bp_records/symptom_records/teleconsults. This joins
# them at read time rather than widening call_reports, since those tables
# already hold the patient's running clinical picture and a call_reports row
# on its own was never meant to duplicate it.
def mcp_card_for_report(*, report_id: int | None = None, session_id: str | None = None) -> dict | None:
    """Build one MCP/RCH-shaped visit summary. Looked up by either the
    call_reports row id (what the /reports dashboard has) or session_id
    (the LiveKit room name, which is what the caller's own frontend page
    already knows right after end_call, with no extra round trip needed to
    learn a SQL row id it was never given)."""
    if report_id is None and not session_id:
        return None
    store = get_store()
    if not store.available():
        return None
    # Never substitutes another recent call when an explicit session id
    # misses (DashboardStore.find_report) - that once presented the wrong
    # mother's card after a reconnect.
    report = store.find_report(report_id=report_id, session_id=session_id)
    if report is None:
        return None
    bundle = store.report_bundle(report)
    mother = bundle["mother"] or {}
    bp = bundle["bp"]
    teleconsult = bundle["teleconsult"]

    # Every danger sign SymptomSet knows about, not a hand-kept five: this
    # list previously omitted convulsions, reduced fetal movement and all
    # seven signs the ANC rewrite added, so a fits- or fever-flagged mother's
    # card showed no danger sign at all.
    danger_signs = [
        name[:1].upper() + name[1:]
        for name in SymptomSet(**{c: bool(mother.get(c)) for c in SYMPTOM_COLUMNS}).present_names
    ]
    return {
        "draft_needs_form_verification": True,
        "mother_name": mother.get("name"),
        "age": mother.get("age"),
        "village": mother.get("village"),
        "phone": mother.get("phone"),
        "gestational_weeks": mother.get("gestational_weeks"),
        "blood_pressure": f"{bp['systolic']}/{bp['diastolic']}" if bp else None,
        "danger_signs": danger_signs,
        "risk_category": report.get("risk_level"),
        "referral_status": "Referred to specialist" if report.get("escalated") else "Not referred",
        "referral_trigger": teleconsult.get("trigger_sign") if (teleconsult and report.get("escalated")) else None,
        "questions_answered": f"{report.get('intake_answered', 0)}/{report.get('intake_total', 0)}",
        "visit_date": report.get("created_at"),
        "language": report.get("language"),
    }


def set_report_flag(report_id: int, field: str, value: bool, flagged_by: str | None) -> tuple[bool, str | None]:
    """Returns (ok, error). error is None on success, else a short reason string."""
    if field not in REPORT_FLAGGABLE_FIELDS:
        return False, f"not a flaggable field: {field!r}"
    store = get_store()
    if not store.available():
        return False, "database not created yet"
    return store.set_report_flag(report_id, field, value, flagged_by)


class _LineIndex:
    """Byte offset of every complete line in an append-only log file.

    The /logs cursor is a line number. Resolving it used to mean reading and
    splitting the whole file on every poll - O(file) per request, per open
    console, every 1.2s, on a file that only ever grows. This keeps the start
    offset of each line and scans only bytes appended since the last call, so
    a poll costs O(new bytes + lines returned). A file that shrank or was
    replaced (rotation, manual truncate) is re-indexed from scratch.
    """

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._reset(None)

    def _reset(self, identity) -> None:
        self._identity = identity
        self._starts: list[int] = []   # byte offset where each complete line begins
        self._scanned = 0              # bytes indexed; always ends on a newline

    def _refresh(self, handle) -> None:
        stat = os.fstat(handle.fileno())
        identity = (stat.st_dev, stat.st_ino)
        if identity != self._identity or stat.st_size < self._scanned:
            self._reset(identity)
        if stat.st_size == self._scanned:
            return
        handle.seek(self._scanned)
        chunk = handle.read(stat.st_size - self._scanned)
        pos = 0
        while True:
            newline = chunk.find(b"\n", pos)
            if newline < 0:
                break                  # partial last line: wait for its newline
            self._starts.append(self._scanned + pos)
            pos = newline + 1
        self._scanned += pos

    def line_count(self) -> int:
        try:
            with self._lock, self.path.open("rb") as handle:
                self._refresh(handle)
                return len(self._starts)
        except FileNotFoundError:
            with self._lock:
                self._reset(None)
            return 0

    def read(self, since: int, limit: int) -> tuple[list[str], int]:
        """Lines [since, since+limit) and the total complete-line count."""
        try:
            with self._lock, self.path.open("rb") as handle:
                self._refresh(handle)
                total = len(self._starts)
                if since >= total:
                    return [], total
                end_line = min(total, since + limit)
                end_byte = self._starts[end_line] if end_line < total else self._scanned
                handle.seek(self._starts[since])
                raw = handle.read(end_byte - self._starts[since])
        except FileNotFoundError:
            with self._lock:
                self._reset(None)
            return [], 0
        return raw.decode("utf-8", errors="ignore").splitlines(), total


_PIPELINE_LOG_INDEX = _LineIndex(PIPELINE_LOG_PATH)


def _read_logs(since: int) -> dict:
    lines, total = _PIPELINE_LOG_INDEX.read(since, 250)

    events = []
    next_cursor = min(since, total)
    for offset, line in enumerate(lines, start=since):
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
    return {"next": min(total, next_cursor), "events": events}


def _broadcast_sse(event: dict) -> None:
    """Broadcast a pipeline event to all connected SSE clients."""
    global _sse_clients
    data = f"data: {json.dumps(event)}\n\n"
    with _sse_lock:
        dead = []
        for q in _sse_clients:
            try:
                q.put_nowait(data)
            except queue.Full:
                dead.append(q)
        for q in dead:
            _sse_clients.remove(q)


def _register_sse_client() -> queue.Queue:
    """Register a new SSE client and return its queue."""
    global _sse_clients
    q: queue.Queue = queue.Queue(maxsize=100)
    with _sse_lock:
        _sse_clients.append(q)
    return q


def _unregister_sse_client(q: queue.Queue) -> None:
    """Unregister an SSE client."""
    global _sse_clients
    with _sse_lock:
        if q in _sse_clients:
            _sse_clients.remove(q)


# Routes that return patient-level data (names, risk, transcripts, clinical
# cards, appointments) need the dashboard's bearer token, as does every POST
# (writes). Open: liveness, config, the doctor list and aggregate counts.
# /events (SSE) is not here: a browser EventSource cannot send a header, so
# it relies on nginx's login and a fixed CORS origin instead.
_PROTECTED_GET = frozenset({
    "/reports", "/api/reports",
    "/reports/mcp-card", "/api/reports/mcp-card",
    "/logs", "/api/logs",
    "/schedule", "/api/schedule",
    "/slot-events", "/api/slot-events",
})
_PROTECTED_PREFIXES = ("/dashboard/", "/api/dashboard/")


# Public, real-time, measurable data (UNICEF Innovation Fund REOI): counts
# only, computed live from the same database the dashboard reads. Never a
# name, id, village, reading or date per person. A count from 1 to 4 is shown
# as "<5" (small-cell suppression): in a single village a small number can
# point to a person.
PUBLIC_METRICS_MIN_CELL = 5
_PUBLIC_METRICS = (
    ("screenings", "calls", "Voice check-ins completed"),
    ("mothers_screened", "mothers", "Mothers with a screening record"),
    ("high_risk_mothers", "high_risk_mothers", "Mothers currently flagged high risk"),
    ("escalations", "escalations", "Danger signs referred to a doctor"),
)


def _suppress(n: int) -> int | str:
    return "<5" if 0 < n < PUBLIC_METRICS_MIN_CELL else n


def public_metrics() -> dict:
    """The public, anonymised, aggregate counters (see _PUBLIC_METRICS)."""
    summary = get_store().summary()
    return {
        "generated_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "suppressed_below": PUBLIC_METRICS_MIN_CELL,
        "metrics": {key: _suppress(int(summary.get(src) or 0)) for key, src, _label in _PUBLIC_METRICS},
        "definitions": {key: label for key, _src, label in _PUBLIC_METRICS},
    }


def _needs_token(method: str, path: str) -> bool:
    return method == "POST" or path in _PROTECTED_GET or path.startswith(_PROTECTED_PREFIXES)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature
        pass  # keep the worker's own log output free of HTTP access-log noise

    def _is_authorized(self) -> bool:
        """Bearer check for patient data (OWASP API1: broken object level
        authorization). Sends the 401/403 itself and returns False on failure.

        SAKHI_DASHBOARD_TOKEN (else SAKHI_GATEWAY_TOKEN) unset: open, for local
        development; nginx's login still guards a deployed instance."""
        expected = os.getenv("SAKHI_DASHBOARD_TOKEN", os.getenv("SAKHI_GATEWAY_TOKEN", "")).strip()
        if not expected:
            return True
        auth_header = self.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            self._send_json({"error": "Unauthorized: Bearer token required"}, status=401)
            return False
        token = auth_header.split(" ", 1)[1].strip()
        if not hmac.compare_digest(token.encode(), expected.encode()):
            self._send_json({"error": "Forbidden: Invalid token"}, status=403)
            return False
        return True

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if _needs_token("GET", parsed.path) and not self._is_authorized():
            return
        if parsed.path == "/":
            self._send_json({"status": "MyStree Voice Agent Worker is running"})
            return
        if parsed.path in {"/events", "/api/events"}:
            self._handle_sse()
            return
        if parsed.path in {"/public/metrics", "/api/public/metrics"}:
            try:
                payload = public_metrics()
            except Exception:
                self._send_json({"error": "metrics unavailable"}, status=503)
                return
            self._send_json(payload, headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "public, max-age=30"})
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
        if parsed.path in {"/reports", "/api/reports"}:
            params = parse_qs(parsed.query)
            try:
                limit = max(1, min(500, int(params.get("limit", ["50"])[0])))
            except ValueError:
                limit = 50
            self._send_json(list_reports(limit))
            return

        if parsed.path in {"/reports/mcp-card", "/api/reports/mcp-card"}:
            params = parse_qs(parsed.query)
            raw_id = params.get("id", [None])[0]
            session_id = params.get("session_id", [None])[0]
            report_id = None
            if raw_id is not None:
                try:
                    report_id = int(raw_id)
                except ValueError:
                    self._send_json({"error": "id must be an integer"}, status=400)
                    return
            card = mcp_card_for_report(report_id=report_id, session_id=session_id)
            if card is None:
                self._send_json({"error": "report not found"}, status=404)
                return
            self._send_json(card)
            return

        # --- Admin dashboard (2026-09-24) -------------------------------
        # Database-backed, same backend as the worker (sakhi_dashboard_store).
        # These return patient health data: they must only ever be reachable
        # behind the dashboard's authentication (nginx), never publicly.
        if parsed.path in {"/dashboard/summary", "/api/dashboard/summary"}:
            store = get_store()
            self._send_json({**store.summary(), "components": store.component_health()})
            return
        if parsed.path in {"/dashboard/health", "/api/dashboard/health"}:
            self._send_json({"components": get_store().component_health()})
            return
        if parsed.path in {"/dashboard/patients", "/api/dashboard/patients"}:
            params = parse_qs(parsed.query)
            self._send_json({"patients": get_store().list_patients(
                search=params.get("q", [None])[0],
                risk=params.get("risk", [None])[0],
                limit=params.get("limit", ["50"])[0],
                offset=params.get("offset", ["0"])[0],
            )})
            return
        if parsed.path in {"/dashboard/patient", "/api/dashboard/patient"}:
            patient_id = parse_qs(parsed.query).get("id", [""])[0].strip()
            if not patient_id:
                self._send_json({"error": "id query param is required"}, status=400)
                return
            report = get_store().patient_report(patient_id)
            if report is None:
                self._send_json({"error": "patient not found"}, status=404)
                return
            self._send_json(report)
            return
        if parsed.path in {"/dashboard/turns", "/api/dashboard/turns"}:
            session_id = parse_qs(parsed.query).get("session_id", [""])[0].strip()
            if not session_id:
                self._send_json({"error": "session_id query param is required"}, status=400)
                return
            self._send_json({"turns": get_store().call_turns(session_id)})
            return
        # --- AWS costs (2026-09-26): real bill from Cost Explorer, cached ---
        if parsed.path in {"/dashboard/costs", "/api/dashboard/costs"}:
            import sakhi_aws_costs
            force = parse_qs(parsed.query).get("refresh", ["0"])[0] == "1"
            self._send_json(sakhi_aws_costs.overview(force=force))
            return
        if parsed.path in {"/dashboard/costs/service", "/api/dashboard/costs/service"}:
            import sakhi_aws_costs
            params = parse_qs(parsed.query)
            self._send_json(sakhi_aws_costs.service_detail(
                params.get("name", [""])[0],
                force=params.get("refresh", ["0"])[0] == "1",
            ))
            return
        if parsed.path in {"/dashboard/events", "/api/dashboard/events"}:
            params = parse_qs(parsed.query)
            self._send_json(get_store().list_events(
                after_id=params.get("after", ["0"])[0],
                limit=params.get("limit", ["200"])[0],
                session_id=params.get("session_id", [None])[0],
                status=params.get("status", [None])[0],
            ))
            return

        if parsed.path in {"/logs", "/api/logs"}:
            params = parse_qs(parsed.query)
            raw_since = params.get("since", ["0"])[0]
            if raw_since == "latest":
                self._send_json({"next": _PIPELINE_LOG_INDEX.line_count(), "events": []})
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
        except ValueError:
            length = 0
        # Read the (small JSON) body BEFORE any refusal: answering 401 with the
        # body unread resets the connection on Windows, and the client sees a
        # network error instead of the 401.
        raw = self.rfile.read(length) if 0 < length <= 1_000_000 else b""
        if _needs_token("POST", parsed.path) and not self._is_authorized():
            return
        try:
            if length > 1_000_000:
                raise ValueError("body too large")
            body = json.loads(raw or b"{}") if length else {}
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

        if parsed.path in {"/reports/flag", "/api/reports/flag"}:
            try:
                report_id = int(body.get("report_id"))
            except (TypeError, ValueError):
                self._send_json({"error": "report_id is required and must be an integer"}, status=400)
                return
            field = str(body.get("field", ""))
            value = bool(body.get("value"))
            flagged_by = body.get("flagged_by")
            flagged_by = str(flagged_by) if flagged_by else None
            ok, reason = set_report_flag(report_id, field, value, flagged_by)
            if not ok:
                status = 404 if reason == "report not found" else 400
                self._send_json({"error": reason}, status=status)
                return
            # A successful flag previously fell through to send_error(404)
            # below: the write had happened, but the dashboard was told it
            # failed. Respond explicitly.
            self._send_json({"ok": True, "report_id": report_id, "field": field, "value": value})
            return

        self.send_error(404)

    def _handle_sse(self) -> None:
        """Handle Server-Sent Events connection for real-time log streaming."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        # A browser EventSource on another origin (the console, port 3100 in
        # development) needs this header. Never "*": any website could then
        # open the stream of live intakes from a browser that can reach this
        # port. ALLOWED_ORIGIN is the console's origin. (/logs needs the
        # dashboard token now; this stream cannot, so it is origin-locked.)
        self.send_header("Access-Control-Allow-Origin", os.getenv("ALLOWED_ORIGIN", "http://localhost:3100"))
        self.send_header("Vary", "Origin")
        self.end_headers()

        q = _register_sse_client()
        try:
            # Send initial connection event
            self.wfile.write(b"data: {\"type\": \"connected\"}\n\n")
            self.wfile.flush()

            while True:
                try:
                    data = q.get(timeout=30)  # 30s heartbeat
                    self.wfile.write(data.encode("utf-8"))
                    self.wfile.flush()
                except queue.Empty:
                    # Send heartbeat comment to keep connection alive
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
                # ConnectionAbortedError / OSError (WinError 10053/10054): how a
                # dropped client shows on Windows; without them the loop spins.
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                    break
        finally:
            _unregister_sse_client(q)

    def _send_json(self, payload: dict, status: int = 200, headers: dict | None = None) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
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
