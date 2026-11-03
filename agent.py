import os
import sys

# Suppress noisy dependency warnings before they load
os.environ["ORT_LOGGING_LEVEL"] = "3"  # Error only for ONNX
os.environ["PYTHONWARNINGS"] = "ignore"

import asyncio
import atexit
import json
import logging
import os
import queue
import random
import re
import threading
import time
import traceback
from collections import OrderedDict, deque
from datetime import datetime, timezone
import dataclasses
import aiohttp
from pathlib import Path
from urllib.parse import urlparse, urlencode
from livekit.agents.utils import is_given
from livekit.agents import APIConnectOptions, DEFAULT_API_CONNECT_OPTIONS
from livekit.agents import stt
from livekit.agents.types import NOT_GIVEN, NotGivenOr

from dotenv import load_dotenv

# Imported this early (stdlib-only module) because pipeline_event() below
# references TelemetryWriter at module level, above the main import block.
from sakhi_telemetry import CURRENT_SESSION, TelemetryWriter

PROJECT_ROOT = Path(__file__).resolve().parent


def load_project_env() -> None:
    candidates = [
        PROJECT_ROOT / ".env",
        PROJECT_ROOT.parent / ".env",
        PROJECT_ROOT / "frontend" / ".env.local",
    ]
    loaded = []
    for candidate in candidates:
        if candidate.exists():
            load_dotenv(candidate, override=False)
            loaded.append(str(candidate))
    if not loaded:
        load_dotenv()


load_project_env()

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
logger = logging.getLogger("agent")
PIPELINE_LOG_PATH = Path(os.getenv("PIPELINE_LOG_PATH", "logs/pipeline_events.jsonl"))

# Also write our own log file, not just stdout. Locally this used to be done
# by shell redirection (`python agent.py dev >> logs/worker_background.log`);
# on a host like Railway there's no such redirection, and status_server.py's
# health check reads this exact file (same regex-based logic local_server.py
# used when it ran on the same machine as the worker - see DEPLOY.md).
try:
    _worker_log_path = Path("logs/worker_background.log")
    _worker_log_path.parent.mkdir(parents=True, exist_ok=True)
    _file_handler = logging.FileHandler(_worker_log_path, encoding="utf-8")
    _file_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s %(name)s - %(message)s"))
    logging.getLogger().addHandler(_file_handler)
except OSError:
    logger.warning("Could not open logs/worker_background.log for writing; status server health check will report 'not found'.")

STAGES = {
    "worker": "Stage 0 Worker",
    "auth": "Stage 1 Auth",
    "webrtc": "Stage 2 WebRTC",
    "microphone": "Stage 3 Microphone",
    "dispatch": "Stage 4 Worker Dispatch",
    "stt": "Stage 5 STT",
    "turn": "Stage 6 Semantic Turn",
    "llm": "Stage 7 LLM",
    "tts": "Stage 8 TTS",
    "playback": "Stage 9 Playback",
    "tools": "Stage 10 Tools DB",
}


def _jsonable(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, BaseException):
        return {"type": type(value).__name__, "message": str(value)}
    return str(value)


# Pipeline events used to open+write+close the log file synchronously inside
# every call - including once per interim STT transcript, several times a
# second while the caller speaks, all on the asyncio event loop. A bounded
# queue drained by one daemon writer thread keeps the hot path to a dict
# construction and a lock-free put. Format on disk is unchanged. If the queue
# is ever full (writer stalled), the event is dropped with a stderr note
# rather than blocking the call path - these logs are observability, not
# call-critical state.
_PIPELINE_QUEUE: "queue.Queue[dict | None]" = None  # type: ignore[assignment]
_PIPELINE_WRITER: "threading.Thread | None" = None


def _pipeline_writer_loop() -> None:
    while True:
        event = _PIPELINE_QUEUE.get()
        if event is None:  # shutdown sentinel
            return
        try:
            PIPELINE_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with PIPELINE_LOG_PATH.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, default=_jsonable, ensure_ascii=True) + "\n")
                # Drain whatever else queued while we held the handle open.
                while True:
                    try:
                        extra = _PIPELINE_QUEUE.get_nowait()
                    except queue.Empty:
                        break
                    if extra is None:
                        return
                    handle.write(json.dumps(extra, default=_jsonable, ensure_ascii=True) + "\n")
        except Exception:
            logger.warning("Unable to write pipeline event", exc_info=True)


def _ensure_pipeline_writer() -> None:
    global _PIPELINE_QUEUE, _PIPELINE_WRITER
    if _PIPELINE_WRITER is not None and _PIPELINE_WRITER.is_alive():
        return
    if _PIPELINE_QUEUE is None:
        _PIPELINE_QUEUE = queue.Queue(maxsize=int(os.getenv("PIPELINE_LOG_QUEUE_MAX", "2000")))
    _PIPELINE_WRITER = threading.Thread(target=_pipeline_writer_loop, name="pipeline-log-writer", daemon=True)
    _PIPELINE_WRITER.start()
    atexit.register(_flush_pipeline_events)


def _flush_pipeline_events() -> None:
    """Best-effort drain on shutdown so tail-of-call events are not lost."""
    if _PIPELINE_QUEUE is None:
        return
    try:
        _PIPELINE_QUEUE.put_nowait(None)
    except queue.Full:
        pass
    if _PIPELINE_WRITER is not None:
        _PIPELINE_WRITER.join(timeout=3.0)


# Database telemetry (2026-09-24): the same events, batched into
# pipeline_events / call_turns / component_health for the admin dashboard.
# One writer per worker process, created on the first call whose repository
# can persist telemetry - see _telemetry_for(). None means "not persisting",
# and pipeline_event() then does exactly what it did before.
_TELEMETRY: TelemetryWriter | None = None


def _telemetry_for(repository) -> TelemetryWriter | None:
    """Start (or reuse) this process's TelemetryWriter for `repository`.

    Skipped when SAKHI_TELEMETRY_DB=false, or when the backend has no
    telemetry tables (it inherits the ABC's no-op record_telemetry - the JSON
    demo mock), so a demo box never runs a flush loop that writes nothing.
    """
    global _TELEMETRY
    if not env_flag("SAKHI_TELEMETRY_DB", True):
        return None
    if type(repository).record_telemetry is PatientRepository.record_telemetry:
        return None
    if _TELEMETRY is None:
        _TELEMETRY = TelemetryWriter(
            repository.record_telemetry,
            flush_interval=float(os.getenv("SAKHI_TELEMETRY_FLUSH_S", "0.25")),
        )
    _TELEMETRY.start()
    return _TELEMETRY


def _job_room_name(ctx) -> str | None:
    """The LiveKit room name for a job - the session id every call_reports,
    call_turns and pipeline_events row is keyed by. Read from the dispatched
    job first (known before the room is even connected), falling back to
    the connected room."""
    job_room = getattr(getattr(getattr(ctx, "job", None), "room", None), "name", None)
    return job_room or getattr(getattr(ctx, "room", None), "name", None) or None


def _stt_debug_to_pipeline(record: dict) -> None:
    """sarvam_wrappers' [SAKHI_STT_DEBUG] record -> the call's pipeline log.
    Carries no transcript text (length + hash only)."""
    pipeline_event(
        "stt", "info", "[SAKHI_STT_DEBUG]",
        f"{record.get('stt_provider')} {'final' if record.get('is_final') else 'interim'} "
        f"{record.get('transcript_length')} chars from {record.get('audio_duration_ms')} ms "
        f"@ {record.get('audio_rms_dbfs')} dBFS",
        event="stt_debug", session_id=CURRENT_SESSION.get(None), **record,
    )


def pipeline_event(stage_key: str, status: str, label: str, message: str, **details) -> None:
    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "stage": STAGES.get(stage_key, stage_key),
        "status": status,
        "label": label,
        "message": message,
        "details": details,
    }
    _ensure_pipeline_writer()
    try:
        _PIPELINE_QUEUE.put_nowait(event)
    except queue.Full:
        print("pipeline event queue full; dropping event", file=sys.stderr)

    # O(1) and non-blocking - see sakhi_telemetry.TelemetryWriter.submit.
    telemetry = _TELEMETRY
    if telemetry is not None:
        telemetry.submit(event)

    # Broadcast to SSE clients for real-time UI updates
    try:
        from status_server import _broadcast_sse
        _broadcast_sse(event)
    except Exception:
        pass  # SSE not available or failed - not critical

    log_method = logger.error if status == "error" else logger.warning if status == "warn" else logger.info
    log_method("[PIPELINE] %s %s %s - %s %s", event["stage"], status, label, message, details or "")


import sarvam_wrappers as _sarvam_wrappers_module  # noqa: E402

_sarvam_wrappers_module.STT_DEBUG_SINK = _stt_debug_to_pipeline


def _metric_stage(metric_type: str) -> tuple[str, str]:
    return {
        "stt_metrics": ("stt", "STT metrics"),
        "llm_metrics": ("llm", "LLM metrics"),
        "tts_metrics": ("tts", "TTS metrics"),
        "vad_metrics": ("microphone", "VAD metrics"),
        "eou_metrics": ("turn", "End of utterance"),
        "eot_inference_metrics": ("turn", "Semantic turn inference"),
        "interruption_metrics": ("turn", "Interruption metrics"),
        "realtime_model_metrics": ("llm", "Realtime model metrics"),
        "avatar_metrics": ("playback", "Playback metrics"),
    }.get(metric_type, ("worker", metric_type or "metrics"))


def _metric_message(metric) -> str:
    metric_type = getattr(metric, "type", "")
    if metric_type == "stt_metrics":
        return (
            f"audio={getattr(metric, 'audio_duration', 0):.3f}s "
            f"duration={getattr(metric, 'duration', 0):.3f}s "
            f"acquire={getattr(metric, 'acquire_time', 0):.3f}s"
        )
    if metric_type == "llm_metrics":
        return (
            f"ttft={getattr(metric, 'ttft', 0):.3f}s "
            f"duration={getattr(metric, 'duration', 0):.3f}s "
            f"tokens={getattr(metric, 'total_tokens', 0)}"
        )
    if metric_type == "tts_metrics":
        return (
            f"ttfb={getattr(metric, 'ttfb', 0):.3f}s "
            f"duration={getattr(metric, 'duration', 0):.3f}s "
            f"audio={getattr(metric, 'audio_duration', 0):.3f}s "
            f"chars={getattr(metric, 'characters_count', 0)}"
        )
    if metric_type == "eou_metrics":
        return (
            f"eou_delay={getattr(metric, 'end_of_utterance_delay', 0):.3f}s "
            f"transcript_delay={getattr(metric, 'transcription_delay', 0):.3f}s"
        )
    if metric_type == "eot_inference_metrics":
        return (
            f"total={getattr(metric, 'total_duration', 0):.3f}s "
            f"detection={getattr(metric, 'detection_delay', 0):.3f}s "
            f"prediction={getattr(metric, 'prediction_duration', 0):.3f}s"
        )
    return repr(metric)

import db_helper

db_helper.init_db(reset=os.getenv("SQLITE_RESET_ON_START", "false").lower() == "true")

import dataclasses
import aiohttp
from urllib.parse import urlencode
from livekit.agents.utils import is_given
from livekit.agents import APIConnectOptions, DEFAULT_API_CONNECT_OPTIONS
from livekit.agents.types import NOT_GIVEN, NotGivenOr
from livekit.agents import (
    Agent,
    AgentSession,
    EndpointingOptions,
    JobContext,
    JobProcess,
    JobRequest,
    MetricsCollectedEvent,
    PreemptiveGenerationOptions,
    RoomInputOptions,
    RunContext,
    StopResponse,
    TurnHandlingOptions,
    WorkerOptions,
    cli,
    llm,
    metrics,
    stt,
    tts,
)
from livekit.agents.voice.agent_session import SessionConnectOptions
from livekit.agents.tokenize.tokenizer import SentenceStream, SentenceTokenizer, TokenData
from livekit.agents import utils
from livekit.agents.utils import shortuuid
from livekit.plugins import assemblyai, deepgram, gnani, noise_cancellation, openai, silero, smallestai

# Cartesia is deliberately removed from the runtime TTS chain. Direct probing returned HTTP 402 Payment Required; re-add after billing fixed.
cartesia = None

try:
    from livekit.plugins import google
except Exception:  # pragma: no cover - optional dependency
    google = None

# Each optional dependency gets its own try block. They used to share one, and
# because `token_manager` does not exist in this repo the whole block raised
# ModuleNotFoundError on every boot - silently forcing MultilingualModel to None
# (so ENABLE_MULTILINGUAL_TURN_DETECTOR could never work) and leaving
# SmallestTTS/SkilliesTTS undefined. See changed.md C17.
try:
    from livekit.plugins.turn_detector.multilingual import MultilingualModel
    multilingual_model = MultilingualModel
except Exception:  # pragma: no cover - optional dependency
    MultilingualModel = None
    multilingual_model = None

try:
    from smallest_wrappers import SmallestTTS
except Exception:  # pragma: no cover - optional dependency
    SmallestTTS = None

try:
    from skillies_wrappers import SkilliesTTS
except Exception:  # pragma: no cover - optional dependency
    SkilliesTTS = None

from sarvam_wrappers import (
    SARVAM_DEFAULT_MODEL,
    SARVAM_DEFAULT_SPEAKER,
    SarvamBatchSTT,
    SarvamSTT,
    SarvamTTS,
    resolve_sarvam_voice,
)
from tts_audio_cache import CachedTTS
from rumik_wrappers import RumikTTS
from gemini_wrappers import GeminiTTS
from vakyam_wrappers import (
    VAKYAM_DEFAULT_VOICE,
    VAKYAM_KANNADA_VOICES,
    VakyamTTS,
    resolve_vakyam_voice,
)
import guardrails

# --- Sakhi maternal-health core ---------------------------------------------
# The clinical model, the triage decision, and persistence all live outside
# agent.py so they stay unit-testable without LiveKit. agent.py's job is to
# wire them into a voice session, not to own clinical logic.
from sakhi_domain import (
    BloodPressure,
    BloodPressureError,
    Language,
    detect_language_switch_request,
    RiskLevel,
    SakhiCallState,
    danger_sign_lock_reason,
    detect_patient_demographics,
    detect_gestational_age,
    detect_pregnancy_number,
    detect_symptoms,
    kannada_symptom_terms,
)
from sakhi_avatar import build_avatar_backend
from sakhi_kannada import kannada_number_normalization, number_normalization_for
from llm_hedge import DEFAULT_HEDGE_DELAY, HedgedLLM
from clinical_terms import CLINICAL_KEYWORDS, canonicalise
from sakhi_intake import (
    INTAKE_QUESTIONS, QUESTIONS_BY_ID, IntakeProgress, accepts_assent,
    armed_question_text, has_localised_text, is_assent, question_text, stage_name,
)
from sakhi_pacing import PacingTracker
from sakhi_prompts import LLM_CONFIG, system_prompt_for
from sakhi_question_lock import QuestionLock
from sakhi_repository import MockSixtyDBRepository, PatientRepository
from sakhi_sql_repository import PostgresPatientRepository, SQLitePatientRepository
from sakhi_tools import ClinicalToolset
from sakhi_triage import TRIAGE, TriageEngine
from audio_bank import AUDIO_BANK
from sakhi_slot_engine import (
    EMERGENCY_WAV, all_prompt_paths, extract_all, localized, prompt_text,
)
from voice_catalog import (
    CATALOG as VOICE_CATALOG,
    PROVIDERS as TTS_PROVIDERS,
    RUMIK_DEFAULT_MODEL,
    RUMIK_VOICES,
    SMALLEST_DEFAULT_MODEL,
    SMALLEST_SAMPLE_RATE,
    SMALLEST_VOICES,
    GEMINI_VOICES,
    default_voice as voice_catalog_default,
    is_valid as voice_catalog_is_valid,
)

try:
    from sixtydb_wrappers import SixtyDbTTS
except Exception:  # pragma: no cover - optional direct provider
    SixtyDbTTS = None
try:
    from kitten_tts_provider import KittenLocalTTS
except Exception:  # pragma: no cover - optional dependency
    KittenLocalTTS = None


CLINIC_KEY_TERMS = [
    "MyStree Clinic",
    "MyStree",
    "Indiranagar",
    "gynecologist",
    "gynaecologist",
    "obstetrician",
    "PCOS",
    "pregnancy",
    "fertility",
    "infertility",
    "dermatologist",
    "physiotherapy",
    "radiologist",
    "psychologist",
    "nutritionist",
    "scan",
    "follow up",
    "Dr. Smitha",
    "Dr. Surbhi Sinha",
    "Priyanka Savina",
    "Dr. Chaitra Nayak",
    "Dr. Priyadarshini",
    "Dr. Swathi Pai",
    "Dr. Jasmine Flora",
    "Dr. Nivetha",
    "Dr. Shreyashi",
    "Nupur Karmarkar",
    "Jigyasa Thakur",
    "Hindi",
    "Hinglish",
    "slot",
    "OPD",
]

# Common Indian first names biased into the STT vocabulary via Deepgram/
# AssemblyAI keyterm prompting, so a caller's own name isn't mis-heard as the
# nearest English word the model already knows (observed live: "Ayesha" or
# similar transcribed as "idea", repeatedly, across an entire call). This is
# a bias list, not a whitelist - any name is still accepted; unlisted names
# just don't get the extra recognition weight. Deliberately broad across
# regions/genders for a Bengaluru clinic's real caller mix; add more if a
# name is consistently mis-heard rather than assuming this list is complete.
COMMON_INDIAN_NAMES = [
    "Priya", "Pooja", "Divya", "Ayesha", "Aisha", "Ananya", "Anjali", "Anita",
    "Sunita", "Kavya", "Kavitha", "Shreya", "Shruti", "Neha", "Meera", "Deepa",
    "Deepti", "Ritu", "Rupa", "Rupali", "Suhani", "Sneha", "Swathi", "Chaitra",
    "Nivetha", "Nithya", "Lakshmi", "Latha", "Radha", "Rekha", "Sowmya",
    "Vidya", "Vandana", "Kiran", "Komal", "Farida", "Fatima", "Zainab",
    "Sana", "Sania", "Reshma", "Asha", "Usha", "Geetha", "Gayathri",
    "Tanvi", "Tanya", "Ishita", "Isha", "Jyothi", "Manasa", "Manisha",
    "Preethi", "Preeti", "Rachana", "Roopa", "Sindhu", "Soundarya",
    "Amit", "Arjun", "Rahul", "Rohan", "Rohit", "Vinayak", "Vijay", "Vikram",
    "Vishal", "Karthik", "Kumar", "Suresh", "Ramesh", "Ganesh",
    "Ganapathi", "Mahesh", "Naveen", "Nagesh", "Praveen", "Prakash", "Pradeep",
    "Sandeep", "Sanjay", "Santosh", "Manoj", "Manjunath", "Mohan", "Mohammed",
    "Imran", "Irfan", "Faisal", "Farhan", "Ashwin", "Ashok", "Anand", "Anil",
    "Ajay", "Akash", "Abhishek", "Aditya", "Deepak", "Dinesh", "Gopal",
    "Girish", "Harish", "Jagadish", "Krishna", "Lokesh", "Nithin", "Raghav",
    "Raj", "Rajesh", "Ravi", "Ravindra", "Sathish", "Shankar", "Srinivas",
    "Tarun", "Umesh", "Venkatesh", "Yogesh", "Zaid",
]


def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


# The greeting must open on the FIRST INTAKE QUESTION, not on blood pressure.
#
# It used to say "shall we check the mother's blood pressure?" - which is the
# very first thing spoken on every call. No amount of intake tracking can beat
# that: Sakhi opened by asking for BP, the ASHA worker answered with BP, and the
# whole 24-question flow was skipped before it began. This was the single
# largest cause of "it asks blood pressure at the first shot itself".
#
# Keep this in sync with INTAKE_QUESTIONS[0] (name and age).
GREETING_TEXT = (
    "ನಮಸ್ಕಾರ, ನಾನು ಸಖಿ. ತಾಯಿಯ ಹೆಸರು ಮತ್ತು ವಯಸ್ಸು ಹೇಳಿ."
)

GREETINGS = {
    Language.KANNADA: "ನಮಸ್ಕಾರ, ನಾನು ಸಖಿ. ತಾಯಿಯ ಹೆಸರು ಮತ್ತು ವಯಸ್ಸು ಹೇಳಿ.",
    Language.HINDI: "नमस्ते, मैं सखी हूँ। माँ का नाम और उम्र बताइए।",
    Language.ENGLISH: "Hello, I am Sakhi. Please tell me the mother's name and age.",
}


def greeting_for(language: "Language | str" = Language.KANNADA) -> str:
    """Return the localized initial greeting for the given call language."""
    lang = language if isinstance(language, Language) else Language.parse(language)
    return GREETINGS.get(lang, GREETINGS[Language.KANNADA])


GREETING_CACHE_DIR = Path(os.getenv("GREETING_CACHE_DIR", "assets/audio/greetings"))


def _greeting_cache_path(voice: str, language: "Language | str" = Language.KANNADA) -> Path:
    """Cache path keyed by voice, LANGUAGE, greeting text AND pace.

    Pace belongs in the key. It was previously omitted, so retuning the
    speaking rate left every cached greeting rendered at the OLD speed while
    the live conversation used the new one - the greeting and the first real
    turn audibly differed, and no amount of fixing the pace constants could
    change it until the cache expired (it never did).

    Language belongs in the key too (2026-09-08, added when this cache was
    finally wired into the live greeting path): the original version hashed
    only the module-level GREETING_TEXT constant, which is the Kannada
    string - every call cached (or looked up) the SAME file regardless of
    which of the three GREETINGS the call actually needed, so a Hindi or
    English caller would have gotten the Kannada greeting's audio back.
    """
    import hashlib

    lang = language if isinstance(language, Language) else Language.parse(language)
    text = GREETINGS.get(lang, GREETING_TEXT)
    text_tag = hashlib.md5(text.encode()).hexdigest()[:8]
    pace_tag = f"p{default_tts_pace():.2f}".replace(".", "")
    return GREETING_CACHE_DIR / f"{voice}-{lang.value}-{text_tag}-{pace_tag}.wav"


def load_cached_greeting(voice: str, language: "Language | str" = Language.KANNADA):
    """Pre-rendered greeting frames for this voice+language, or None on first use.

    Playing cached audio makes the agent speak the instant the session starts,
    instead of waiting on a live TTS round-trip for the very first thing the
    caller hears. The cache key includes a hash of the greeting text so editing
    GREETINGS invalidates stale audio automatically.
    """
    import wave as _wave

    from livekit import rtc as _rtc

    path = _greeting_cache_path(voice, language)
    if not path.exists():
        return None
    try:
        with _wave.open(str(path), "rb") as w:
            rate, data = w.getframerate(), w.readframes(w.getnframes())
        frames = []
        chunk = rate // 50 * 2  # 20ms of s16le mono
        for i in range(0, len(data), chunk):
            piece = data[i : i + chunk]
            frames.append(
                _rtc.AudioFrame(data=piece, sample_rate=rate, num_channels=1, samples_per_channel=len(piece) // 2)
            )
        return frames
    except Exception:
        logger.warning("Failed to load cached greeting for %s", voice, exc_info=True)
        return None


async def ensure_greeting_cache(
    voice: str, cache_tts, language: "Language | str" = Language.KANNADA
) -> None:
    """Background: synthesize and store this voice+language's greeting for
    future calls."""
    import wave as _wave

    path = _greeting_cache_path(voice, language)
    if path.exists():
        return
    lang = language if isinstance(language, Language) else Language.parse(language)
    text = GREETINGS.get(lang, GREETING_TEXT)
    try:
        stream = cache_tts.synthesize(text)
        frames = [ev.frame async for ev in stream]
        await stream.aclose()
        if not frames:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with _wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(frames[0].sample_rate)
            for f in frames:
                w.writeframes(bytes(f.data))
        pipeline_event("tts", "ok", "Greeting cached", f"Pre-rendered greeting stored for voice {voice}")
    except Exception:
        logger.warning("Greeting cache synthesis failed for %s", voice, exc_info=True)
# NOTE: FILLER_TEXTS and _choose_filler_text lived here - English stall phrases
# ("just checking our calendar for you") played by delayed_filler while the
# clinic's SQLite queries ran. Sakhi's repository serves from memory, so there
# is nothing to stall for, and the phrases were English-only.

_use_phonetic_fallback = False


async def indian_english_phonetic_normalization(text):
    """Transform text phonetically for non-Indian TTS models, making them sound
    more natural for Indian English/Hinglish pronunciations.
    """
    mappings = {
        r"\b[Nn]amaste\b": "Nuh-muh-stay",
        r"\b[Hh]aan ji\b": "hahn jee",
        r"\b[Hh]aan\b": "hahn",
        r"\b[Tt]heek hai\b": "theek hay",
        r"\b[Tt]heek\b": "theek",
        r"\b[Ee]k minute\b": "eck minute",
        r"\b[Jj]ust a second haan\b": "just a second hahn",
        r"\b[Jj]i\b": "jee",
        r"\b[Pp]riya\b": "Pree-yah",
        r"\b[Rr]ajesh\b": "Rah-jaysh",
        r"\b[Aa]nita\b": "Uh-nee-tha",
        r"\b[Ss]unita\b": "Suh-nee-tha",
        r"\b[Ii]ndiranagar\b": "In-theera-nugger",
        r"\b[Pp]urana\b": "poo-rah-nah",
        r"\b[Cc]hahiye\b": "cha-hee-yay",
        r"\b[Kk]arna\b": "kar-nah",
        r"\b[Cc]halega\b": "chuh-lay-gah",
    }
    async for chunk in text:
        if _use_phonetic_fallback:
            transformed = chunk
            for pattern, replacement in mappings.items():
                transformed = re.sub(pattern, replacement, transformed)
            yield transformed
        else:
            yield chunk


async def filter_code_artifacts(text):
    """TTS guard: never speak JSON, code, or markup that leaks from the LLM.

    Line-based state machine over the streamed text: a line whose first
    non-space character is {, [, <, or a backtick is dropped entirely
    (tool-call JSON / code fences), and anything from an inline '{"' to the
    end of that line is cut. Plain sentences stream through untouched.
    """
    mode = None  # None = start of line (undecided), "pass", "drop"
    pending_ws = ""
    pending_brace = False

    async for chunk in text:
        out = []
        for ch in chunk:
            if mode is None:
                if ch == "\n":
                    out.append(pending_ws + ch)
                    pending_ws = ""
                elif ch.isspace():
                    pending_ws += ch
                elif ch in "{[<`":
                    mode = "drop"
                    pending_ws = ""
                else:
                    mode = "pass"
                    out.append(pending_ws + ch)
                    pending_ws = ""
            elif mode == "pass":
                if pending_brace:
                    pending_brace = False
                    if ch == '"':
                        mode = "drop"  # inline '{"...' Ã¢â‚¬â€  cut the rest of the line
                        continue
                    out.append("{" + ("" if ch == "\n" else ch))
                    if ch == "\n":
                        out.append("\n")
                        mode = None
                    continue
                if ch == "{":
                    pending_brace = True
                elif ch == "\n":
                    out.append(ch)
                    mode = None
                else:
                    out.append(ch)
            else:  # drop
                if ch == "\n":
                    out.append(" ")
                    mode = None
        if out:
            yield "".join(out)

    if pending_brace:
        yield "{"


def clean_spoken_punctuation(text: str, language: str = "kannada") -> str:
    """TTS formatting guard: Strip characters that Indian TTS engines (e.g. Sarvam bulbul:v3) vocalize.
    Disallowed: / : ; * ** - — – _ # bullets.
    Allowed: . , ?
    """
    if not text:
        return ""
    # 1. Clean number slashes like 120/80 -> 120 80
    text = re.sub(r'(\d+)\s*/\s*(\d+)', r'\1 \2', text)
    # 2. Markdown bold/italics
    text = re.sub(r'\*{1,3}([^*]+)\*{1,3}', r'\1', text)
    text = text.replace('*', '')
    # 3. Colons and semicolons -> replace with comma
    text = re.sub(r'[:;]', ',', text)
    # 4. Word slashes -> replace with 'ಅಥವಾ' (Kannada) or 'या' (Hindi) or 'or' (English)
    lang_lower = (language or "").lower()
    or_word = " ಅಥವಾ " if "kannada" in lang_lower else (" या " if "hindi" in lang_lower else " or ")
    text = re.sub(r'(?<=\w)/(?=\w)', or_word, text)
    text = text.replace('/', ' ')
    # 5. Hyphens and dashes in numbers: e.g. 120-80 -> 120 80
    text = re.sub(r'(\d+)\s*[-—–]\s*(\d+)', r'\1 \2', text)
    # Standalone or spaced dashes -> comma
    text = re.sub(r'\s+[-—–]\s+', ', ', text)
    text = re.sub(r'[-—–_#]', ' ', text)
    # 6. Bullets
    text = re.sub(r'[\u2022\u25aa\u25ab]', ' ', text)
    # 7. Normalize multiple spaces / commas
    text = re.sub(r'\s*,\s*', ', ', text)
    text = re.sub(r'(,\s*){2,}', ', ', text)
    text = re.sub(r'[ \t]+', ' ', text)
    return text.strip()


async def filter_disallowed_punctuation(text):
    """TTS stream transform: filter disallowed punctuation characters so TTS does not vocalize their names."""
    async for chunk in text:
        if not chunk:
            continue
        cleaned = clean_spoken_punctuation(chunk)
        if cleaned:
            yield cleaned


async def medical_output_guard(text, language="kn"):
    """TTS guard: drug names and dosage phrasing must never be SPOKEN, even
    if the LLM generates them (a jailbreak that survived the input gate, or
    unprompted volunteering). The agent's legitimate vocabulary contains no
    drug or dosage words at all, so this can be aggressive.

    Streams with a held-back partial word: LLM token deltas can split words
    mid-stream ("paraceta" + "mol"), so only whitespace-complete text is
    scanned and emitted, with a short emitted-tail window for patterns that
    span a word boundary ("500 " then "mg"). On a hit, the rest of this
    reply is swallowed and replaced with the approved refusal IN THE CALL'S
    LANGUAGE (guardrails.script_for) - the drug name never reaches the
    synthesizer, and a Kannada call never switches to English mid-reply.
    """
    # Leading space: the refusal follows whatever was already spoken of this reply.
    refusal = " " + (guardrails.script_for("medical_advice", language) or guardrails.OUTPUT_REPLACEMENT)
    buffer = ""
    tail = ""  # last emitted chars, for cross-chunk pattern context
    flagged = False
    async for chunk in text:
        if flagged:
            continue  # swallow the remainder of a flagged reply
        buffer += chunk
        cut = max(buffer.rfind(" "), buffer.rfind("\n"))
        if cut == -1:
            continue  # nothing whitespace-complete yet - keep holding
        emit, buffer = buffer[: cut + 1], buffer[cut + 1:]
        if guardrails.output_flagged(tail + emit):
            flagged = True
            pipeline_event(
                "tts", "warn", "Guardrail: medical content blocked at output",
                "Generated text contained drug/dosage phrasing; replaced before TTS",
                event="guardrail_output_blocked",
            )
            yield refusal
            buffer = ""
            continue
        yield emit
        tail = (tail + emit)[-48:]
    if not flagged and buffer:
        if guardrails.output_flagged(tail + buffer):
            pipeline_event(
                "tts", "warn", "Guardrail: medical content blocked at output",
                "Generated text contained drug/dosage phrasing; replaced before TTS",
                event="guardrail_output_blocked",
            )
            yield refusal
        else:
            yield buffer


def medical_output_guard_for(language):
    """medical_output_guard bound to one call's language (a TTS transform
    takes only the text stream), like number_normalization_for()."""

    async def guard(text):
        async for piece in medical_output_guard(text, language):
            yield piece

    return guard


_TIME_RE = re.compile(r"(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?", re.IGNORECASE)


# NOTE: parse_time_to_24h, friendly_time, short_time, friendly_date and
# _slot_phrase lived here - appointment-time formatting for the clinic booking
# flow ("half past three in the afternoon"). Sakhi books nothing.
# NOTE: the SlotCache class and its `slot_cache = SlotCache()` module-level
# singleton lived here (~115 lines) - an in-memory mirror of the clinic's open
# appointment slots, refreshed on a 10-second background timer for the whole
# call. Another cross-call mutable global, and pure overhead once the booking
# tools were unregistered. See changed.md L7, oops.md §2.1.


class TurnLatencyAggregator:
    """Correlates per-provider metrics (EOU, LLM, TTS) by speech_id and emits
    ONE structured `turn_latency` event per conversational turn, so latency
    percentiles can be computed offline (scripts/latency_report.py) instead
    of eyeballing three separate log lines per turn.

    The individual metric events are unchanged - this only adds a summary.
    A turn is emitted when its first TTS metric arrives (= first audio for
    that reply) or evicted quietly after _MAX_PENDING turns to bound memory.
    """

    _MAX_PENDING = 32

    def __init__(self) -> None:
        self._turns: OrderedDict[str, dict] = OrderedDict()
        self.cancelled_generations = 0
        self.turns_emitted = 0

    def _turn(self, speech_id: str) -> dict:
        if speech_id not in self._turns:
            self._turns[speech_id] = {"speech_id": speech_id, "fallback_used": False, "cancelled_generation": False}
            while len(self._turns) > self._MAX_PENDING:
                self._turns.popitem(last=False)
        return self._turns[speech_id]

    def on_metric(self, m) -> None:
        metric_type = getattr(m, "type", "")
        speech_id = getattr(m, "speech_id", "") or ""
        if not speech_id:
            return
        turn = self._turn(speech_id)
        if getattr(m, "cancelled", False):
            turn["cancelled_generation"] = True
            self.cancelled_generations += 1
        if metric_type == "eou_metrics":
            turn["eou_delay_ms"] = round(getattr(m, "end_of_utterance_delay", 0) * 1000, 1)
            turn["stt_final_ms"] = round(getattr(m, "transcription_delay", 0) * 1000, 1)
        elif metric_type == "llm_metrics":
            turn["llm_ttft_ms"] = round(getattr(m, "ttft", 0) * 1000, 1)
            turn["llm_total_ms"] = round(getattr(m, "duration", 0) * 1000, 1)
        elif metric_type == "tts_metrics" and "tts_ttfa_ms" not in turn:
            turn["tts_ttfa_ms"] = round(getattr(m, "ttfb", 0) * 1000, 1)
            self._emit(speech_id, turn)

    def _emit(self, speech_id: str, turn: dict) -> None:
        self._turns.pop(speech_id, None)
        self.turns_emitted += 1
        eou = turn.get("eou_delay_ms") or 0.0
        ttft = turn.get("llm_ttft_ms") or 0.0
        ttfa = turn.get("tts_ttfa_ms") or 0.0
        # Composition estimate: end of user speech -> first playable audio.
        # response_path is "llm" when an LLM ran for this speech, otherwise the
        # reply came from say()/deterministic paths.
        first_audio_total = round(eou + ttft + ttfa, 1)
        pipeline_event(
            "turn", "ok", "Turn latency", "per-turn latency summary",
            event="turn_latency",
            speech_id=speech_id,
            stt_final_ms=turn.get("stt_final_ms"),
            eou_delay_ms=turn.get("eou_delay_ms"),
            llm_ttft_ms=turn.get("llm_ttft_ms"),
            llm_total_ms=turn.get("llm_total_ms"),
            tts_ttfa_ms=turn.get("tts_ttfa_ms"),
            first_audio_total_ms=first_audio_total,
            response_path="llm" if "llm_ttft_ms" in turn else "deterministic",
            cancelled_generation=turn.get("cancelled_generation", False),
            cancelled_generations_so_far=self.cancelled_generations,
        )


turn_latency = TurnLatencyAggregator()


_INVALID_PATIENT_NAME_TOKENS = {
    "dr", "doctor", "doc", "madam", "maam", "mam", "sir", "miss", "mrs", "ms",
    "booking", "book", "appointment", "follow", "followup", "follow-up", "new", "old",
    "phone", "number", "clinic", "patient", "name", "my", "mine", "no", "yes", "yeah", "haan", "ji",
}

_FEMALE_NAME_HINTS = {
    "angel", "anjali", "anju", "priya", "divya", "deepa", "deepti", "ritu", "neha", "meera",
    "anita", "sunita", "surbhi", "smitha", "swathi", "chaitra", "nivetha", "kavya", "simran",
    "shreya", "pooja", "shruti", "suhani", "kavitha", "rupali", "niharika", "tanya",
}
_MALE_NAME_HINTS = {
    "tilin", "vinayak", "rajesh", "rahul", "rohan", "amit", "aditya", "kabir", "varun",
    "sumit", "mohit", "rehan", "soham", "anand", "tarun", "vijay",
}
_FEMALE_TITLE_RE = re.compile(r"\b(mrs|ms|miss|ma'?am|madam|mother|wife|daughter|sister)\b", re.IGNORECASE)
_MALE_TITLE_RE = re.compile(r"\b(mr|sir|father|husband|son|brother)\b", re.IGNORECASE)


# NOTE: removed here - clean_patient_name, is_valid_patient_name,
# invalid_name_retry_message, infer_caller_profile, log_caller_profile,
# spoken_digits, extract_phone_candidate, and the BookingPrefetch class with its
# module-level singleton (~250 lines).
#
# All of it served the clinic's name-and-10-digit-phone booking flow. Two
# specific problems went with it: BookingPrefetch ran on EVERY interim STT
# transcript (several times a second while the caller speaks) to warm
# appointment lookups no registered tool could consume, and it was a mutable
# module-level global shared across every concurrent call on the worker.
# Name handling now lives in sakhi_domain.clean_person_name, scoped per call.
# See changed.md L7 and oops.md §2.1.


def extract_caller_phone_from_metadata(participant) -> str | None:
    metadata_values: list[str] = []
    metadata = getattr(participant, "metadata", None) if participant is not None else None
    if metadata:
        metadata_values.append(str(metadata))
        try:
            payload = json.loads(metadata)
            if isinstance(payload, dict):
                for key in (
                    "caller_phone",
                    "caller_id",
                    "phone",
                    "sip_from",
                    "sip.phoneNumber",
                    "sip_trunk_phone_number",
                ):
                    value = payload.get(key)
                    if value:
                        metadata_values.append(str(value))
        except Exception:
            pass

    attributes = getattr(participant, "attributes", None) if participant is not None else None
    if isinstance(attributes, dict):
        for key, value in attributes.items():
            lowered = str(key).lower()
            if any(token in lowered for token in ("phone", "caller", "sip")) and value:
                metadata_values.append(str(value))

    identity = getattr(participant, "identity", None) if participant is not None else None
    if identity:
        metadata_values.append(str(identity))

    for raw in metadata_values:
        phone = extract_phone_candidate(raw)
        if phone:
            return db_helper.normalize_phone(phone)
    return None


def parse_metadata_json(raw: object) -> dict:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        payload = json.loads(str(raw))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def voice_from_metadata(payload: dict) -> str | None:
    voice = str(payload.get("sarvam_speaker") or payload.get("voice") or "").strip().lower()
    return voice if voice in SARVAM_V3_SPEAKERS else None


def provider_and_voice_from_metadata(payload: dict) -> tuple[str | None, str | None]:
    """Resolve (tts_provider, voice_id) from dispatch/participant metadata.

    Returns (None, None) when nothing usable is present, NOT a hardcoded
    provider name. This used to default to "smallest" - a provider that
    predates Sakhi and was never wired into TTS_PROVIDERS (sarvam, gnani) -
    which meant every call without explicit metadata still returned a
    truthy-but-invalid provider string. `build_tts()`'s own
    `if not tts_provider: tts_provider = os.getenv("TTS_PROVIDER", ...)`
    check only runs on a falsy value, so that stale default silently defeated
    the .env-based provider choice on every call AND logged a spurious
    "Unknown TTS provider 'smallest'" warning each time. Returning None here
    lets build_tts's own env-driven default actually take effect.
    """
    raw_provider = str(payload.get("tts_provider") or "").strip().lower()
    provider = raw_provider if raw_provider in TTS_PROVIDERS else None

    raw_voice = str(payload.get("voice_id") or payload.get("sarvam_speaker") or payload.get("voice") or "").strip()
    if provider == "sarvam":
        # Validate against the full bulbul:v3 speaker set, not just the
        # shorter curated shortlist in voice_catalog.py (that list is a UI
        # convenience; every SARVAM_V3_SPEAKERS entry is a real usable voice).
        voice = raw_voice.lower()
        if voice not in SARVAM_V3_SPEAKERS:
            voice = None
    else:
        voice = raw_voice if voice_catalog_is_valid(provider, raw_voice) else None
    return provider, voice


def caller_phone_from_metadata_payload(payload: dict) -> str | None:
    for key in ("caller_phone", "caller_id", "phone", "sip_from", "sip.phoneNumber"):
        value = payload.get(key)
        if value:
            phone = extract_phone_candidate(str(value))
            if phone:
                return db_helper.normalize_phone(phone)
    return None


async def preload_user(caller_phone: str | None) -> dict:
    started = time.perf_counter()
    if not caller_phone:
        pipeline_event(
            "tools",
            "info",
            "Patient context preload skipped",
            "No SIP caller ID or phone metadata available",
            event="preload_user_skipped",
        )
        return {"caller_phone": None, "patient": None, "appointments": [], "history": []}

    normalized = db_helper.normalize_phone(caller_phone)
    try:
        context = await db_helper.get_patient_context_by_phone_async(normalized)
        if not context:
            context = {"patient": None, "appointments": [], "history": []}
        context["caller_phone"] = normalized
        pipeline_event(
            "tools",
            "ok",
            "Patient context preloaded",
            "Caller record fetched before first LLM turn",
            event="preload_user",
            phone_tail=normalized[-4:],
            patient_found=bool(context.get("patient")),
            appointments=len(context.get("appointments") or []),
            history=len(context.get("history") or []),
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        return context
    except Exception as exc:
        pipeline_event(
            "tools",
            "warn",
            "Patient context preload failed",
            str(exc),
            event="preload_user_failed",
            phone_tail=normalized[-4:],
            traceback=traceback.format_exc(),
        )
        return {"caller_phone": normalized, "patient": None, "appointments": [], "history": []}


def patient_context_prompt(preloaded: dict | None) -> str:
    if not preloaded:
        return "No caller-ID context was preloaded. Treat the caller normally and ask for name."
    phone = preloaded.get("caller_phone")
    patient = preloaded.get("patient")
    history = preloaded.get("history") or []
    appointments = preloaded.get("appointments") or []
    phone_line = f"Caller ID phone: {spoken_digits(phone)}." if phone else "No caller ID phone."
    if not patient:
        return f"{phone_line} No matching patient record. Do not mention this; continue fresh booking flow."
    last = history[0] if history else None
    next_appt = appointments[0] if appointments else None
    parts = [
        f"{phone_line} Registered caller appears to be {patient.get('name')}.",
        "Do not greet with the name until the caller confirms it.",
    ]
    if last:
        parts.append(
            "Last visit: "
            f"{friendly_date(last.get('appointment_date'))} with {last.get('doctor_name')} "
            f"at {friendly_time(last.get('appointment_time'))}."
        )
    if next_appt:
        parts.append(
            "Upcoming appointment: "
            f"ID {spoken_digits(next_appt.get('appointment_id'))}, "
            f"{friendly_date(next_appt.get('appointment_date'))} at {friendly_time(next_appt.get('appointment_time'))} "
            f"with {next_appt.get('doctor_name')}."
        )
    return " ".join(parts)


def summarize_call_from_transcripts(transcripts: list[dict], preloaded_user: dict | None, room_name: str | None) -> dict:
    final_text = " ".join(
        item["text"] for item in transcripts
        if item.get("role") == "user" and item.get("final") and item.get("text")
    ).strip()
    lowered = final_text.lower()
    negative_markers = ("angry", "upset", "complaint", "bad", "not working", "delay", "wrong", "cancel")
    follow_markers = ("call back", "callback", "complaint", "urgent", "emergency", "doctor call", "human", "staff")
    if not final_text:
        sentiment = "unknown"
        summary = "Call ended without a completed caller transcript."
    else:
        sentiment = "negative" if any(marker in lowered for marker in negative_markers) else "neutral"
        summary = final_text[:280]
    patient = (preloaded_user or {}).get("patient") or {}
    return {
        "room_name": room_name,
        "caller_phone": (preloaded_user or {}).get("caller_phone"),
        "patient_id": patient.get("patient_id"),
        "call_summary": summary,
        "user_sentiment": sentiment,
        "follow_up_required": any(marker in lowered for marker in follow_markers),
        "turn_count": len([item for item in transcripts if item.get("role") == "user" and item.get("final")]),
    }


async def start_call_recording(ctx: "JobContext") -> tuple[str | None, str | None]:
    """Room-composite (audio-only) egress of this call straight to S3.

    Runs on LiveKit Cloud's own egress workers, not on this instance - that's
    why it needs its own S3 credentials (RECORDING_AWS_ACCESS_KEY_ID/SECRET)
    rather than reusing the EC2 instance's IAM role, which egress can't see
    at all. That IAM user (sakhi-egress-uploader) is deliberately scoped to
    s3:PutObject/PutObjectAcl on recordings/* in the one recordings bucket
    only - see deploy/aws/README.md.

    Best-effort by design: a recording failure must never take down the
    call itself. RECORDING_ENABLED=false (or a missing bucket/credential)
    disables this cleanly - returns (None, None) and the call proceeds
    exactly as it did before this existed.
    """
    if not env_flag("RECORDING_ENABLED", True):
        return None, None
    bucket = os.getenv("RECORDING_S3_BUCKET", "")
    access_key = os.getenv("RECORDING_AWS_ACCESS_KEY_ID", "")
    secret_key = os.getenv("RECORDING_AWS_SECRET_ACCESS_KEY", "")
    region = os.getenv("RECORDING_S3_REGION", "ap-south-1")
    if not (bucket and access_key and secret_key):
        pipeline_event(
            "recording", "warn", "Recording skipped",
            "RECORDING_S3_BUCKET/RECORDING_AWS_ACCESS_KEY_ID/RECORDING_AWS_SECRET_ACCESS_KEY "
            "not fully configured - call proceeding without a recording",
            event="recording_not_configured",
        )
        return None, None
    room_name = getattr(ctx.room, "name", None) or "unknown-room"
    recording_key = f"recordings/{room_name}.ogg"
    try:
        from livekit import api as lk_api
        from livekit.protocol.egress import EncodedFileOutput, EncodedFileType, RoomCompositeEgressRequest, S3Upload

        lkapi = lk_api.LiveKitAPI(
            url=os.getenv("LIVEKIT_URL", ""),
            api_key=os.getenv("LIVEKIT_API_KEY", ""),
            api_secret=os.getenv("LIVEKIT_API_SECRET", ""),
        )
        try:
            info = await lkapi.egress.start_room_composite_egress(
                RoomCompositeEgressRequest(
                    room_name=room_name,
                    audio_only=True,
                    file_outputs=[
                        EncodedFileOutput(
                            file_type=EncodedFileType.OGG,
                            filepath=recording_key,
                            s3=S3Upload(
                                access_key=access_key,
                                secret=secret_key,
                                bucket=bucket,
                                region=region,
                            ),
                        )
                    ],
                )
            )
        finally:
            await lkapi.aclose()
        pipeline_event(
            "recording", "ok", "Recording started",
            f"Room-composite egress started for {room_name}",
            event="recording_started",
            egress_id=info.egress_id,
            recording_key=recording_key,
        )
        return info.egress_id, recording_key
    except Exception as exc:  # noqa: BLE001 - never let a recording failure break the call
        pipeline_event(
            "recording", "error", "Recording failed to start",
            f"start_room_composite_egress raised: {exc}",
            event="recording_start_failed",
        )
        return None, None


async def stop_call_recording(egress_id: str | None) -> str:
    """Stop egress at call end. Returns the recording_status to persist:
    'stopped' (egress told to finish - the file lands in S3 within
    seconds to a couple minutes after this, asynchronously) or 'none' if
    no recording was ever started. Best-effort, same reasoning as start."""
    if not egress_id:
        return "none"
    try:
        from livekit import api as lk_api
        from livekit.protocol.egress import StopEgressRequest

        lkapi = lk_api.LiveKitAPI(
            url=os.getenv("LIVEKIT_URL", ""),
            api_key=os.getenv("LIVEKIT_API_KEY", ""),
            api_secret=os.getenv("LIVEKIT_API_SECRET", ""),
        )
        try:
            await lkapi.egress.stop_egress(StopEgressRequest(egress_id=egress_id))
        finally:
            await lkapi.aclose()
        return "stopped"
    except Exception as exc:  # noqa: BLE001
        pipeline_event(
            "recording", "error", "Recording failed to stop",
            f"stop_egress raised: {exc}",
            event="recording_stop_failed",
            egress_id=egress_id,
        )
        return "stopped"  # egress usually finishes on its own even if the stop call itself errored


def build_sakhi_call_report(
    state: "SakhiCallState",
    *,
    duration_ms: float,
    recording_status: str = "none",
    recording_key: str | None = None,
) -> dict:
    """The clinically-real post-call report: risk, escalation, intake progress.

    Distinct from summarize_call_from_transcripts()'s sentiment/follow-up
    heuristic above, which predates the Sakhi pivot and carries no clinical
    fields at all - that one is kept only for its ops-log line, never
    persisted. This is the shape sakhi_sql_repository.save_call_report() and
    sakhi_schema.py's call_reports table actually expect.
    """
    return {
        "session_id": state.session_id,
        "patient_id": state.patient_id,
        "language": state.language.value,
        "risk_level": state.risk_level.value,
        "intake_answered": len(state.intake.answered),
        "intake_total": len(INTAKE_QUESTIONS),
        "escalated": state.escalated,
        "duration_ms": round(duration_ms, 2),
        "recording_status": recording_status,
        "recording_key": recording_key,
    }


async def save_post_call_report(report: dict, repo) -> None:
    started = time.perf_counter()
    try:
        # save_call_report only exists on SQLitePatientRepository, not on the
        # PatientRepository ABC (confirmed: sakhi_repository.py's ABC doesn't
        # declare it) - a JSON-mock-backed deployment has no such method, and
        # must not crash call teardown for lacking one.
        save = getattr(repo, "save_call_report", None)
        if save is None:
            pipeline_event(
                "tools", "warn", "Post-call report skipped",
                "Active repository has no save_call_report (JSON-mock backend); "
                "clinical report was not persisted",
                event="post_call_report_skipped",
            )
            return
        report_id = await save(report)
        pipeline_event(
            "tools",
            "ok",
            "Post-call report saved",
            "Structured call report persisted after disconnect",
            event="post_call_report_saved",
            report_id=report_id,
            risk_level=report.get("risk_level"),
            escalated=bool(report.get("escalated")),
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )
    except Exception as exc:
        pipeline_event(
            "tools",
            "error",
            "Post-call report failed",
            str(exc),
            event="post_call_report_failed",
            traceback=traceback.format_exc(),
        )


def env_list(name: str, defaults: list[str]) -> list[str]:
    raw = os.getenv(name)
    if not raw:
        return defaults
    values = [item.strip() for item in raw.split(",") if item.strip()]
    return values or defaults


def groq_api_keys() -> list[str]:
    keys: list[str] = []
    raw_multi = os.getenv("GROQ_API_KEYS")
    if raw_multi:
        keys.extend(item.strip() for item in raw_multi.split(",") if item.strip())
    for name in ("GROQ_API_KEY", "GROQ_API_KEY_1", "GROQ_API_KEY_2", "GROQ_API_KEY_3"):
        value = os.getenv(name)
        if value:
            keys.append(value.strip())

    deduped: list[str] = []
    seen: set[str] = set()
    for key in keys:
        if key and key not in seen:
            seen.add(key)
            deduped.append(key)
    return deduped


def openrouter_api_keys() -> list[str]:
    keys: list[str] = []
    raw_multi = os.getenv("OPENROUTER_API_KEYS")
    if raw_multi:
        keys.extend(item.strip() for item in raw_multi.split(",") if item.strip())
    for name in ("OPENROUTER_API_KEY", "OPENROUTER_API_KEY_1", "OPENROUTER_API_KEY_2"):
        value = os.getenv(name)
        if value:
            keys.append(value.strip())

    deduped: list[str] = []
    seen: set[str] = set()
    for key in keys:
        if key and key not in seen:
            seen.add(key)
            deduped.append(key)
    return deduped


def required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def env_diagnostics() -> dict[str, dict[str, object]]:
    names = [
        "SARVAM_API_KEY",
        "SIXTY_DB_API_KEY",
        "CARTESIA_API_KEY",
        "OPENAI_API_KEY",
        "GROQ_API_KEY",
        "GROQ_API_KEYS",
        "OPENROUTER_API_KEY",
        "OPENROUTER_API_KEYS",
        "ASSEMBLYAI_API_KEY",
        "DEEPGRAM_API_KEY",
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
    ]
    return {
        name: {
            "present": bool(os.getenv(name)),
            "length": len(os.getenv(name) or ""),
        }
        for name in names
    }


def sarvam_tts_model() -> str:
    """Which bulbul model to synthesise with.

    Replaces force_bulbul_v3(), which hard-returned "bulbul:v3" and ignored
    SARVAM_TTS_MODEL entirely. Measured against the live API (2026-08-14, same
    Kannada text):

        bulbul:v2 manisha   clause  438ms   sentence   779ms
        bulbul:v3 pooja     clause 1012ms   sentence  2655ms

    v3 is 2.3-3.4x slower, and on a full sentence it alone exceeds the whole
    audio-only latency budget. v2 is the pilot default; SARVAM_TTS_MODEL can
    still select v3 where voice quality matters more than response time.

    Note `bulbul:v1` is not selectable - the API rejects it outright:
    "model: Input should be 'bulbul:v2', 'bulbul:v3-beta' or 'bulbul:v3'".
    """
    model = os.getenv("SARVAM_TTS_MODEL", SARVAM_DEFAULT_MODEL).strip()
    valid = {"bulbul:v2", "bulbul:v3", "bulbul:v3-beta"}
    if model not in valid:
        logger.warning(
            "SARVAM_TTS_MODEL=%r is not accepted by the API; using %s. Valid: %s",
            model, SARVAM_DEFAULT_MODEL, ", ".join(sorted(valid)),
        )
        return SARVAM_DEFAULT_MODEL
    return model


# NOTE: _FillerHandle, delayed_filler, run_db_step, log_tool_failure and
# _harvest_identity lived here (~100 lines). They wrapped the clinic booking
# tools: a "let me check that for you" filler played while SQLite was queried,
# a retry/timing wrapper around those queries, and an identity harvester that
# copied name/phone out of booking tool arguments into CallState.
#
# _harvest_identity was already broken by this point - it called
# is_valid_patient_name, clean_patient_name and extract_phone_candidate, all of
# which were removed with the booking flow. It survived only because nothing
# called it any more. Identity capture now happens in ClinicalToolset, against
# SakhiCallState.


# NOTE: eleven clinic appointment-booking tools lived here - lookup_doctors,
# suggest_doctor, find_slots, fastest_appointment, lookup_booking_timings,
# lookup_appointments, lookup_patient_history, book_appointment,
# reschedule_appointment, cancel_appointment, register_patient (~370 lines).
#
# None of them were ever registered on the session; the tools= list only ever
# held the maternal set. They were dead weight that still pulled in db_helper,
# SlotCache and BookingPrefetch on every import. Sakhi's tools are in
# sakhi_tools.ClinicalToolset. See changed.md §0.1.


_ZERO_TTS_READY: dict[str, bool] = {}


def _zero_tts_ready(lang: str) -> bool:
    """Every prompt the zero-TTS loop can play is recorded for `lang`.

    All or nothing, decided once per language per worker: a call never runs
    half on recordings and half on a voice that suddenly differs. English
    has no recordings and keeps the LLM/TTS pipeline.
    """
    if lang not in _ZERO_TTS_READY:
        missing = [p for p in all_prompt_paths(lang) if not AUDIO_BANK.has(p)]
        _ZERO_TTS_READY[lang] = AUDIO_BANK.ready and not missing
        if AUDIO_BANK.ready and missing:
            logger.warning("Zero-TTS off for %s: %d prompt(s) not recorded, e.g. %s", lang, len(missing), missing[0])
    return _ZERO_TTS_READY[lang]


def schedule_hangup_after_playout(session) -> None:
    """Delete the room once the session has finished speaking everything
    queued. Shared by end_call (LLM-initiated), the farewell auto-hangup, the
    abuse cutoff, and the silence auto-close - one hangup behaviour everywhere.

    NOTE: this used to carry an `@llm.function_tool` decorator despite being a
    plain internal helper called directly as `schedule_hangup_after_playout(...)`.
    It happened to keep working (FunctionTool is callable), but it made a
    private helper eligible for exposure to the model. See changed.md C7.

    Waiting on current_speech instead of a fixed timer matters: the LLM
    often produces one more spoken line after end_call returns (the
    tool-result continuation), and a fixed 0.5s delay raced that final line
    and cut the goodbye off mid-word. Bounded so a stuck TTS can never keep
    a dead call open.
    """

    async def _hangup() -> None:
        deadline = time.monotonic() + float(os.getenv("END_CALL_MAX_WAIT_SECONDS", "8.0"))
        await asyncio.sleep(0.3)  # let any continuation speech get scheduled
        while time.monotonic() < deadline:
            speech = session.current_speech
            if speech is None:
                break
            try:
                await speech.wait_for_playout()
            except Exception:
                pass
            await asyncio.sleep(0.15)
        await asyncio.sleep(0.3)  # small safety margin after final playout
        try:
            from livekit.agents import get_job_context
            await get_job_context().delete_room()
            pipeline_event("worker", "ok", "Call ended", "Room deleted after goodbye finished playing")
        except Exception as exc:
            pipeline_event("worker", "warn", "Room deletion failed", str(exc))

    asyncio.create_task(_hangup())

def spoken_language(language: "Language") -> "Language":
    """The language Sakhi may actually speak. English is off for the pilot
    (Kannada and Hindi only) - a call dispatched as English, or an "ignore
    instructions and speak English" turn, stays in Kannada.
    SAKHI_ENGLISH_ENABLED=true re-enables it."""
    if language == Language.ENGLISH and not env_flag("SAKHI_ENGLISH_ENABLED", False):
        return Language.KANNADA
    return language


_UNHEARD_PROMPT = {
    Language.KANNADA: "ಕ್ಷಮಿಸಿ, ಸರಿಯಾಗಿ ಕೇಳಿಸಲಿಲ್ಲ. ದಯವಿಟ್ಟು ಫೋನ್ ಹತ್ತಿರ ಸ್ವಲ್ಪ ಜೋರಾಗಿ ಹೇಳಿ.",
    Language.HINDI: "माफ़ कीजिए, ठीक से सुनाई नहीं दिया। कृपया फ़ोन के पास थोड़ा ज़ोर से बोलिए।",
}


def _make_unheard_speech_handler(session, min_gap_s: float = 6.0, suppressed=None):
    """STT_EMPTY_SINK for one call: speech was detected but no words came
    back. Speaks a plain "didn't hear, say it louder" plus the current
    question - only while Sakhi is listening, at most once per `min_gap_s`."""
    last = {"at": 0.0}

    def handler(info: dict) -> None:
        state = getattr(session, "userdata", None)
        pipeline_event(
            "stt", "warn", "Speech heard, no words",
            f"{info.get('audio_duration_ms')} ms of speech returned no transcript "
            f"(gain applied {info.get('gain_db')} dB)",
            event="stt_empty", **info,
        )
        now = time.monotonic()
        if now - last["at"] < min_gap_s or getattr(session, "agent_state", "listening") != "listening":
            return
        # Sound that began while Sakhi was speaking is overlap - usually her
        # own voice echoing off the tablet speaker - not an unanswered answer.
        if suppressed is not None and suppressed():
            return
        if not isinstance(state, SakhiCallState):
            return
        text = _UNHEARD_PROMPT.get(state.language, _UNHEARD_PROMPT[Language.KANNADA])
        question = _arm_question_being_spoken(state) if questionnaire_open(state) else None
        if question is not None and has_localised_text(question, state.language):
            text = f"{text} {armed_question_text(question, state, state.language)}"
        last["at"] = now
        _safe_say(session, text)

    return handler


# A BP reading in the (canonicalised) turn text: "140/90", "140 by 90".
_BP_READING_RE = re.compile(r"(?<!\d)(\d{2,3})\s*(?:/|by|over|ಬೈ|बाई)\s*(\d{2,3})(?!\d)", re.IGNORECASE)

_REPLY_TOOLS = (
    "FIRST call the recording tool for every value she just stated (name and age -> "
    "record_patient_intake, months -> record_gestational_age, BP -> record_bp, "
    "symptoms -> record_symptoms, anything else -> record_anc_checkpoint). "
)
_REPLY_NO_QUESTION = (
    "Do NOT ask any question - the system asks the next question itself, word for word."
)


def _reply_format_instruction(answer_status: str | None) -> str:
    """The per-turn reply rule, chosen by what the CODE decided (2026-09-28).

    The old rule let the model choose between "a short acknowledgement" and
    "say you did not hear clearly" - so it said "sorry, not clear" about
    answers the checklist had just accepted, and "ok" about answers it had
    rejected. Now: accepted -> acknowledge, never claim not hearing;
    not answered -> say it was not caught (the system repeats the question);
    BP heard but not recorded yet -> record it.
    """
    if answer_status == "accepted":
        return ("# REPLY FORMAT: Her answer to the last question WAS recorded. "
                + _REPLY_TOOLS
                + "THEN reply with ONLY a short acknowledgement (under 8 words). Never "
                "say you did not hear or understand. " + _REPLY_NO_QUESTION)
    if answer_status == "value_to_record":
        return ("# REPLY FORMAT: She stated a blood pressure reading. Call record_bp with "
                "those two numbers now, THEN reply with ONLY a short acknowledgement (under "
                "8 words). " + _REPLY_NO_QUESTION)
    if answer_status == "not_answered":
        return ("# REPLY FORMAT: Her last reply did NOT answer the question that was asked. "
                "Do NOT say anything was recorded. " + _REPLY_TOOLS
                + "THEN say ONLY that you did not catch the answer (under 8 words). "
                + _REPLY_NO_QUESTION)
    return ("# REPLY FORMAT: " + _REPLY_TOOLS
            + "THEN reply with ONLY a short acknowledgement (under 8 words). "
            + _REPLY_NO_QUESTION)


def questionnaire_open(state) -> bool:
    """May a routine intake question be asked on this call right now?

    False once a danger sign / HIGH_RISK triage has locked the intake
    (SakhiCallState.lock_intake_on_danger_sign). Every code path that SPEAKS
    a questionnaire question checks this - 2026-09-28: after escalation the
    question lock, the "didn't hear" prompt and the guardrail scripts kept
    appending routine questions (iron tablets, fetal heart rate) between
    hospital instructions.
    """
    return not (isinstance(state, SakhiCallState) and state.intake_locked)


_ESCALATION_NOTICE = {
    Language.KANNADA: "ಅಕ್ಕಾ, ನಾನು ಈಗಲೇ ತಜ್ಞ ವೈದ್ಯರನ್ನು ಸಂಪರ್ಕಿಸುತ್ತಿದ್ದೇನೆ... ದಯವಿಟ್ಟು ಕರೆಯಲ್ಲೇ ಇರಿ.",
    Language.HINDI: "दीदी, मैं अभी विशेषज्ञ डॉक्टर से जोड़ रही हूँ... कृपया कॉल पर बनी रहिए।",
    Language.ENGLISH: "I am connecting a specialist for this mother right now... please stay on the line.",
}


def escalation_notice(language: "Language") -> str:
    """The forced-escalation line in the call's language (it used to be
    English on every call)."""
    return _ESCALATION_NOTICE.get(language, _ESCALATION_NOTICE[Language.KANNADA])


def _then_current_question(script: str, state) -> str:
    """`script` followed by the intake question she still owes an answer to
    (the armed one, else the next), verbatim in the call's language."""
    if not isinstance(state, SakhiCallState) or not questionnaire_open(state):
        return script
    question = _arm_question_being_spoken(state)
    if question is None or not has_localised_text(question, state.language):
        return script
    return f"{script} {question_text(question, state.language)}"


def _arm_question_being_spoken(state):
    """The question a script is about to append - ARMED, so her reply to it
    can close it (2026-09-28, live call 10:53: the medication script appended
    the scan question without arming it; her scan answer hit
    NO_ARMED_QUESTION, and the scan question was asked a second time)."""
    qid = state.intake.pending_id
    if qid:
        return QUESTIONS_BY_ID.get(qid)
    return state.intake.arm_next()


def end_call_refusal(state) -> str | None:
    """Why the model may not hang up yet, or None when it may.

    A real-LLM run (scripts/live_llm_scenarios.py, 2026-09-24) had the model
    call end_call on "yes, she takes them daily" with 15 questions still to
    go, and say "Goodbye." The ASHA worker ends a call early with the red
    button; the model only ends it once the checklist reaches its close (q17)
    or a specialist has taken over. SAKHI_END_CALL_GUARD=false disables.
    """
    if not env_flag("SAKHI_END_CALL_GUARD", True) or not isinstance(state, SakhiCallState):
        return None
    if state.escalated:
        return None
    nxt = state.intake.next_question()
    if nxt is None or nxt.id == "q17":
        return None
    pipeline_event(
        "worker", "warn", "End call refused",
        f"Model tried to end the call with {nxt.id} still unanswered",
        event="end_call_refused", question_id=nxt.id,
    )
    return (
        "Do not end the call - the screening is not finished. Do not say goodbye. "
        f"Continue with the next question: {question_text(nxt, state.language)}"
    )


@llm.function_tool
async def end_call(ctx: RunContext) -> str:
    """Ends the phone call. Call this ONLY after you have already spoken the full
    goodbye message and the caller has nothing more to ask."""
    refusal = end_call_refusal(getattr(ctx.session, "userdata", None))
    if refusal is not None:
        return refusal
    pipeline_event("worker", "info", "End call requested", "Agent is ending the call after goodbye")
    try:
        await ctx.wait_for_playout()  # let the goodbye finish playing first
    except Exception:
        pass
    schedule_hangup_after_playout(ctx.session)
    return "The call is ending now. Do not say anything more."


# NOTE: _ASSEMBLYAI_U3_PRO_MODELS, LockedSpeechStream and LockedAssemblyAISTT
# lived here - ~130 lines subclassing the AssemblyAI plugin to pin its
# endpointing parameters. Unreachable since build_stt() moved to Sarvam
# (changed.md C15). Recover from git history if AssemblyAI ever returns.

# Deepgram medical-glossary keyword priming (2026-08-30). `deepgram.STT`'s
# installed plugin DOES accept a `keyterm` parameter (the older `keyterms`
# name still works but is deprecated - confirmed via a live construction
# warning) - a prior comment on this function claiming Deepgram had no
# keyword-biasing option was stale for Deepgram specifically (still accurate
# for Sarvam, which has none at all). Keyterm prompting only exists on
# Deepgram's nova-3 tier, which is already the model this codebase uses.
#
# This only helps while Deepgram is PRIMARY - if the STT fallback chain
# fails over to Sarvam mid-call, glossary priming is lost with it, since
# SarvamSTT's constructor (sarvam_wrappers.py) has no equivalent parameter.
# An accepted, documented gap, not a silent one.
def _noise_filter():
    """BVCTelephony on a SIP call, BVC on WebRTC.

    The two models are trained for different input: BVCTelephony expects the
    narrowband, codec-compressed audio a PSTN/SIP leg delivers; BVC expects
    wideband WebRTC. Bidar calls arrive over SIP through LiveKit Cloud, so
    telephony is the right default; the browser console gets BVC via
    NOISE_CANCELLATION=bvc. Overridable because a field test may disagree.
    """
    mode = os.getenv("NOISE_CANCELLATION", "telephony").strip().lower()
    if mode in ("off", "none", "0", "false"):
        return None
    # BVC erases every voice that is not the close-talking primary speaker -
    # measured 2026-09-24 through the full production path
    # (scripts/e2e_call_probe.py): a speaker-played answer never reached VAD
    # (0/5), while close-talk speech passed (3/3). "off" keeps every voice.
    # NOT offered: noise_cancellation.NC() - it aborts every job process on
    # this 24 kHz room input ("NcSession::initSession: Input and output
    # sample rates must be equal"), i.e. a completely deaf agent.
    # See docs/STT_FRAGMENTATION_INCIDENT.md.
    if mode in ("bvc", "webrtc", "wideband"):
        return noise_cancellation.BVC()
    return noise_cancellation.BVCTelephony()


# Bidar district entity biasing (2026-09-02, trimmed 2026-09-04 - see
# DEEPGRAM_MEDICAL_KEYTERMS's docstring below for why). Taluk/facility words
# an ASHA worker says on every call - kept to the ones that matter most for
# referral routing, not the full original list, to leave keyterm budget for
# the clinical danger-sign terms.
DEEPGRAM_BIDAR_KEYTERMS = [
    "Bidar", "Chitguppa", "taluk hospital", "district hospital", "PHC",
]

# CRASHED A LIVE CALL 2026-09-04 - read before touching this list again.
# ------------------------------------------------------------------------
# The previous version of this list (6 hand-picked basics + the FULL output
# of kannada_symptom_terms(), 36 terms + the full 19-term Bidar list, 61
# terms total) made build_stt()'s real Deepgram WebSocket handshake fail
# outright on a live test call:
#
#   dg-error: 'Bad Request: Keyterm limit exceeded. The maximum number of
#   tokens across all keyterms is 500.'
#
# With VOICE_STT_FALLBACK_ENABLED=false (deliberate - see build_stt()), a
# failed STT connection has no fallback: the agent is completely deaf for
# the rest of that call, silence in response to everything the ASHA worker
# says. This is a WORSE outcome than sending a smaller, curated keyterm
# list - a boosted vocabulary that never connects boosts nothing.
#
# Confirmed empirically against the real Deepgram API (not guessed):
# Deepgram's "tokens" are NOT the space-split word count this list's size
# was previously eyeballed against - 61 items came out to only 84
# whitespace-delimited "words", nowhere near textually reaching 500 of
# anything, yet still rejected. Kannada script keyterms cost far more of
# whatever Deepgram's real budget unit is per character than English ones
# do (consistent with an English-centric subword tokenizer needing several
# tokens to represent one Kannada codepoint) - a live bisection against
# the actual API found the safe ceiling for THIS specific vocabulary sits
# around 20-22 items, not anywhere close to 61, and moves depending on
# which specific terms are included, not just how many.
#
# So this list is now DELIBERATELY short and PRIORITISED, not exhaustive:
# _SYMPTOM_PATTERNS in sakhi_domain.py was reordered (2026-09-04) so the
# Tier-1 danger signs - the ones sakhi_triage.py can escalate to HIGH_RISK
# on their own - come first, and kannada_symptom_terms() is sliced to a
# prefix confirmed live against the real Deepgram API to stay well under
# the failure boundary (12 of its 36 terms; the boundary found live was
# between 12 and 14). That prefix currently covers vaginal_bleeding and
# severe_abdominal_pain IN FULL plus most of convulsions - the highest-
# acuity, most time-critical signs - and does NOT include
# reduced_fetal_movement's Kannada phrasing, which is a real, known,
# accepted gap: detect_symptoms() still matches it whenever Deepgram's
# base model transcribes it correctly unaided, this list only stops
# helping it do so. Widening this list again requires re-testing against
# the real API (see the bisection method in this session's own history),
# not just eyeballing a bigger number - that is exactly the mistake that
# broke this call.
DEEPGRAM_MEDICAL_KEYTERMS = [
    "ರಕ್ತದೊತ್ತಡ", "ಗರ್ಭಿಣಿ", "ಆಶಾ",
] + kannada_symptom_terms()[:12] + DEEPGRAM_BIDAR_KEYTERMS


def build_stt(language: "Language | str" = Language.KANNADA, segment_vad=None) -> stt.STT:
    """Deepgram-primary streaming STT for Sakhi, with a Sarvam fallback.

    Two things were removed here rather than kept, from when this hard-
    returned Sarvam with no fallback at all:

    * `min_turn_silence` / `max_turn_silence` / `interruption_delay` were
      AssemblyAI endpointing parameters. They were still being threaded in from
      call metadata and env vars long after `build_stt` was switched off
      AssemblyAI, so nothing they were set to had any effect. Turn timing is
      owned by `build_turn_handling()` and `DynamicVADController`.
    * A `health_terms` vocabulary-bias list was built and then never passed to
      anything (changed.md C11). At the time, neither `SarvamSTT` nor
      Deepgram's plugin here was given a keyword-biasing parameter, so the
      honest fix was to drop the list rather than leave code that reads as
      if biasing were active. UPDATE 2026-08-30: Deepgram's installed plugin
      DOES accept `keyterms` (this docstring's earlier claim was stale for
      Deepgram specifically) - see `DEEPGRAM_MEDICAL_KEYTERMS` above and its
      own comment for why this only helps while Deepgram is primary. Sarvam
      genuinely still has no such parameter.

    UNLIKE TTS, THIS PREVIOUSLY HAD NO FALLBACK AT ALL: a single provider
    outage (e.g. Sarvam's account running out of credits, 2026-08-21) made
    the agent completely deaf on every call, with no degraded path - "not
    even listening" was a dead connection with nothing behind it, not a
    quality problem. `stt.FallbackAdapter` closes that gap the same way
    `tts.FallbackAdapter` already does for TTS. Vakyam AI (onboarded
    2026-08-30 for TTS) has no STT product at all - confirmed against its own
    API reference - so it is not and cannot be part of this chain.

    CORRECTION (2026-09-04): this docstring used to also claim "Gnani is
    TTS-only for the same reason" - wrong, and left uncorrected would mislead
    the next read. The installed livekit-plugins-gnani package exports a
    real `STT` class (`livekit.plugins.gnani.STT`, real-time WebSocket,
    `wss://api.vachana.ai/stt/v3/stream`) that was simply never used here.
    Confirmed live (2026-09-04): connects and authenticates successfully in
    all three call languages (en-IN, hi-IN, kn-IN) against the real API with
    a real key, not assumed from the SDK signature alone.
    """
    lang = language if isinstance(language, Language) else Language.parse(language)
    stt_primary = os.getenv("STT_PRIMARY", "deepgram").strip().lower()
    deepgram_key = os.getenv("DEEPGRAM_API_KEY")
    sarvam_key = os.getenv("SARVAM_API_KEY")
    smallest_key = os.getenv("SMALLEST_API_KEY")
    gnani_key = os.getenv("GNANI_API_KEY")

    def _deepgram() -> stt.STT:
        return deepgram.STT(
            model=os.getenv("DEEPGRAM_STT_MODEL", "nova-3"),
            language="en" if lang == Language.ENGLISH else lang.value,
            api_key=deepgram_key,
            keyterm=DEEPGRAM_MEDICAL_KEYTERMS,
        )

    def _sarvam() -> stt.STT:
        # 2026-09-08: saaras:v3 (Sarvam's code-mixed-Indic-speech model, vs.
        # saarika:v2.5's plain Kannada) confirmed live against the real API -
        # both models work with a fresh key, saaras:v3 is not the shipped
        # default here since a same-sentence comparison showed it dropping/
        # digit-converting a few words saarika:v2.5 got exactly right; wired
        # configurable so it can be A/B'd on real calls rather than swapped
        # on one offline sample. SARVAM_STT_MODE defaults to "transcribe",
        # the only value with a confirmed effect - see SarvamSTT's own note
        # on "codemix" before relying on it.
        #
        # SEGMENTATION (2026-09-25): by default the call is cut into
        # utterances by OUR Silero VAD and each one is sent whole to Sarvam's
        # REST API (livekit StreamAdapter + SarvamBatchSTT). On the streaming
        # socket Sarvam's own server VAD decided what counted as speech: a
        # real call had three ASHA bursts heard by LiveKit and only the last
        # transcribed, and q01 never closed. SARVAM_STT_SEGMENTATION=provider
        # returns to the streaming socket. See docs/STT_FRAGMENTATION_INCIDENT.md.
        if (
            segment_vad is not None
            and os.getenv("SARVAM_STT_SEGMENTATION", "livekit_vad").strip().lower() != "provider"
        ):
            return stt.StreamAdapter(
                stt=SarvamBatchSTT(
                    api_key=sarvam_key,
                    language_code=lang.sarvam_code,
                    model=os.getenv("SARVAM_STT_MODEL", "saaras:v3"),
                    mode=os.getenv("SARVAM_STT_MODE", "transcribe"),
                ),
                vad=segment_vad,
            )
        return SarvamSTT(
            api_key=sarvam_key,
            language_code=lang.sarvam_code,
            model=os.getenv("SARVAM_STT_MODEL", "saaras:v3"),
            mode=os.getenv("SARVAM_STT_MODE", "transcribe"),
        )

    def _smallest() -> stt.STT:
        return smallestai.STT(
            model=os.getenv("SMALLEST_STT_MODEL", "pulse"),
            language=os.getenv("SMALLEST_STT_LANGUAGE", "multi"),
            api_key=smallest_key,
            keywords=CLINICAL_KEYWORDS,
            eou_timeout_ms=int(os.getenv("SMALLEST_STT_EOU_TIMEOUT_MS", "450")),
        )

    def _gnani() -> stt.STT:
        return gnani.STT(
            api_key=gnani_key,
            language=lang.sarvam_code,   # kn-IN / hi-IN / en-IN, same shape Gnani wants
            sample_rate=16000,
        )

    if stt_primary == "gnani" and gnani_key:
        primary, primary_name = _gnani(), "gnani"
    elif stt_primary == "smallest" and smallest_key:
        primary, primary_name = _smallest(), "smallest"
    elif stt_primary == "sarvam" and sarvam_key:
        primary, primary_name = _sarvam(), "sarvam"
    elif deepgram_key:
        primary, primary_name = _deepgram(), "deepgram"
    else:
        primary, primary_name = _sarvam(), "sarvam"

    if not env_flag("VOICE_STT_FALLBACK_ENABLED", True):
        pipeline_event(
            "stt", "info", f"{primary_name.title()} STT provider",
            f"Streaming {primary_name} STT in {lang.display_name} (no fallback - disabled)",
            provider=primary_name, language=lang.sarvam_code,
        )
        return primary

    backup = None
    backup_name = None
    try:
        if primary_name == "deepgram" and sarvam_key:
            backup, backup_name = _sarvam(), "sarvam"
        elif primary_name == "sarvam" and deepgram_key:
            backup, backup_name = _deepgram(), "deepgram"
        # BUG FOUND 2026-09-09: no branch existed for primary_name=="gnani"
        # at all - confirmed live the same day, a real call hit "Gnani STT
        # WebSocket error: HTTP 429" with recoverable=True (retryable) but
        # nothing to fail over to, so the whole turn (and the caller's
        # ability to be heard at all) died with it. Deepgram is this
        # codebase's other proven-reliable STT path (no history of
        # inventing words, unlike Smallest AI's `pulse`) - same pairing
        # logic as the deepgram/sarvam branches above, just the one gnani
        # was missing.
        elif primary_name == "gnani" and deepgram_key:
            backup, backup_name = _deepgram(), "deepgram"
    except Exception:
        logger.warning("Could not build the STT fallback", exc_info=True)

    if backup is None:
        pipeline_event(
            "stt", "warn", "No STT fallback",
            f"{primary_name} has no backup configured; a provider outage means "
            "the agent cannot hear at all. Configure the other provider's API key.",
            primary=primary_name,
        )
        return primary

    pipeline_event(
        "stt", "ok", "STT fallback chain",
        f"{primary_name} -> {backup_name}",
        primary=primary_name, backup=backup_name, language=lang.sarvam_code,
    )
    return stt.FallbackAdapter([primary, backup])




# Tier plan for the Groq-only chain. Ordered fastest-acceptable-quality first,
# with model AND key-slot diversity so the two most likely failure modes are
# covered by different tiers:
#
#   - a per-key rate limit (Groq's on-demand tier caps tokens/minute) is
#     survived by moving to the other key slot;
#   - a single model being degraded or withdrawn is survived by moving to a
#     different model family.
#
# 70b first because clinical tool-calling accuracy matters more than the ~200ms
# the 8b model would save; 8b and gemma exist to keep the call alive, not to be
# equals.
# Verified live against this account on 2026-08-15, with tools attached:
#
#   llama-3.3-70b-versatile   TTFT ~521ms   tool-calling: yes
#   llama-3.1-8b-instant      TTFT ~185ms   tool-calling: yes
#   openai/gpt-oss-20b        TTFT ~707ms   tool-calling: NO
#   gemma2-9b-it              DECOMMISSIONED - HTTP 400 on every request
#
# gemma2-9b-it was the requested tier 4. It no longer exists ("The model
# `gemma2-9b-it` has been decommissioned"), so that tier would have failed 100%
# of the time - the opposite of the crash-elimination this change is for.
#
# gpt-oss-20b was the obvious replacement and was rejected on measurement: it is
# slower than the 70b primary AND did not emit a tool call for a blood-pressure
# statement. On an agent whose entire clinical function is tool calls, a tier
# that talks instead of calling record_bp is worse than no tier.
#
# So tier 4 is the 8b model on the second key. Two proven models across two key
# slots still covers both real failure modes - a per-key rate limit and a single
# model degrading - and every tier can actually drive the tools.
# REWRITTEN 2026-08-17: Groq decommissioned its entire Llama lineup.
# `llama-3.3-70b-versatile` and `llama-3.1-8b-instant` return HTTP 404 "model
# does not exist" - all four tiers failed in ~250ms and every turn died on a
# live call. Of the 13 models now listed, the only viable chat pair is gpt-oss
# (qwen3.6 leaks its `<think>` block into VISIBLE content, which would be
# spoken aloud - excluded).
#
# gpt-oss models are REASONING models - the qwen3 trap again. Measured on the
# real clinical prompt: default effort + 50-token cap burned every token on
# reasoning and returned ZERO visible characters. reasoning_effort="low" (set
# in build_llm) + a 200-token cap produced clean Kannada at ~550-640ms. Both
_GROQ_TIERS = (
    (os.getenv("GROQ_MODEL_T1", "openai/gpt-oss-120b"), 0),
    (os.getenv("GROQ_MODEL_T2", "openai/gpt-oss-120b"), 1),
    (os.getenv("GROQ_MODEL_T3", "openai/gpt-oss-20b"), 0),
    (os.getenv("GROQ_MODEL_T4", "openai/gpt-oss-20b"), 1),
)


def groq_model_for_question(question_id: str | None = None, stage: int | None = None) -> str:
    """Return model suited for question complexity:
    Simple intake (q01-q05): name, age, LMP, gravida/parity, address -> llama-3.1-8b-instant
    Clinical screening (q06-q24): BP, hemoglobin, weight, danger signs, ANC visits -> openai/gpt-oss-120b
    """
    if stage in (1, 2) or (question_id and question_id in ("q01", "q02", "q03", "q04", "q05")):
        return os.getenv("GROQ_INTAKE_MODEL", "llama-3.1-8b-instant")
    return os.getenv("GROQ_CLINICAL_MODEL", "openai/gpt-oss-120b")


def build_llm(
    temperature: float = 0.15,
    language: "Language | str" = Language.KANNADA,
) -> llm.LLM:
    """Multi-provider resilient LLM fallback chain.

    Supports OpenRouter, Groq, and OpenAI with cross-vendor redundancy so an
    outage, rate-limit, or model deprecation on one provider fails over to
    the next provider within 1-2 seconds with zero dead air.
    """
    openrouter_keys = openrouter_api_keys()
    groq_keys = groq_api_keys()
    openai_key = os.getenv("OPENAI_API_KEY")

    if not openrouter_keys and not groq_keys and not openai_key:
        raise RuntimeError(
            "No LLM API key configured. Set OPENROUTER_API_KEY, GROQ_API_KEY, or OPENAI_API_KEY."
        )

    openrouter_base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    openrouter_primary_model = os.getenv("OPENROUTER_LLM_MODEL", "meta-llama/llama-3.3-70b-instruct")
    openrouter_backup_model = os.getenv("OPENROUTER_BACKUP_MODEL", "meta-llama/llama-3.1-8b-instruct")

    groq_base_url = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
    max_tokens = int(os.getenv("LLM_MAX_COMPLETION_TOKENS", "150"))
    top_p = float(os.getenv("LLM_TOP_P", LLM_CONFIG["top_p"]))

    # ORDER AND TIMEOUT BUDGETS - both measured, both load-bearing.
    #
    # Measured TTFT on this account, 2026-08-17, real clinical prompt:
    #
    #   groq/gpt-oss-120b                    median  604ms   max 1113ms
    #   groq/gpt-oss-20b                     median  401ms   max  433ms
    #   openrouter/llama-3.3-70b-instruct    median 1842ms   max 2713ms
    #   openrouter/llama-3.1-8b-instruct     median 2200ms   max 2373ms
    #
    # The previous build put OpenRouter FIRST behind a single global
    # attempt_timeout of 1.8s. OpenRouter's MEDIAN is 1842ms - above that
    # timeout - so tier 1 was cancelled on essentially every turn, tier 2
    # (2200ms) was cancelled too, and only then did Groq answer. Every single
    # turn paid ~3.6s of fallback that could never succeed, to reach a provider
    # that responds in 604ms. That is the "fallback lags the system" report.
    #
    # Two fixes:
    #   1. Fastest proven provider first. Groq leads; OpenRouter is the
    #      cross-vendor backup it was meant to be, not the hot path.
    #   2. Per-GROUP timeouts instead of one global one. A single number cannot
    #      fit a 600ms provider and a 1900ms provider: set it for the fast one
    #      and the slow one can never answer; set it for the slow one and a
    #      hung fast tier stalls the call. Groups are nested FallbackAdapters,
    #      each with a budget sized from its own measurements.
    # LLM_PROVIDER_ORDER (2026-09-03): which vendor group goes first is
    # configurable, not hardcoded to Groq. Each block below still builds its
    # group with the same measured timeouts/models as before - only the
    # ASSEMBLY ORDER changes. Default preserves the existing, measured
    # Groq-first hot path; set LLM_PROVIDER_ORDER=openai,groq,openrouter to
    # make OpenAI primary with Groq/OpenRouter as fallback instead.
    _requested_order = [
        name.strip().lower()
        for name in os.getenv("LLM_PROVIDER_ORDER", "groq,openrouter,openai").split(",")
        if name.strip()
    ]

    groups: list[llm.LLM] = []
    described: list[str] = []
    _named_groups: dict[str, tuple[llm.LLM, str]] = {}   # name -> (group, description)

    def _group(members: list[llm.LLM], timeout: float) -> llm.LLM:
        if len(members) == 1:
            return members[0]
        return llm.FallbackAdapter(
            members, attempt_timeout=timeout, max_retry_per_llm=0, retry_interval=0.0,
        )

    # Groq - the measured hot path (see the timing table above). Budget
    # covers the 1113ms worst case with headroom while still cutting off a
    # genuinely hung tier fast.
    if groq_keys:
        groq_timeout = float(os.getenv("GROQ_ATTEMPT_TIMEOUT", "1.5"))
        members = []
        names = []
        for model, key_index in _GROQ_TIERS:
            key = groq_keys[key_index % len(groq_keys)]
            members.append(
                openai.LLM(
                    model=model,
                    api_key=key,
                    base_url=groq_base_url,
                    max_completion_tokens=max_tokens,
                    temperature=temperature,
                    top_p=top_p,
                    reasoning_effort="low",
                    max_retries=0,
                )
            )
            names.append(f"groq/{model}@k{(key_index % len(groq_keys)) + 1}")
        _named_groups["groq"] = (_group(members, groq_timeout), names)


    # OpenRouter - cross-vendor backup. Its budget is 4.0s, not 1.8s: at a
    # 1842ms median and 2713ms worst case, anything under ~3s guarantees it
    # is cancelled before it can speak, which is strictly worse than not
    # configuring it at all.
    if openrouter_keys:
        or_timeout = float(os.getenv("OPENROUTER_ATTEMPT_TIMEOUT", "4.0"))
        members = [
            openai.LLM(
                model=openrouter_primary_model,
                api_key=openrouter_keys[0],
                base_url=openrouter_base_url,
                max_completion_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                max_retries=0,
            )
        ]
        names = [f"openrouter/{openrouter_primary_model}"]
        if openrouter_backup_model and openrouter_backup_model != openrouter_primary_model:
            members.append(
                openai.LLM(
                    model=openrouter_backup_model,
                    api_key=openrouter_keys[1 % len(openrouter_keys)],
                    base_url=openrouter_base_url,
                    max_completion_tokens=max_tokens,
                    temperature=temperature,
                    top_p=top_p,
                    max_retries=0,
                )
            )
            names.append(f"openrouter/{openrouter_backup_model}")
        _named_groups["openrouter"] = (_group(members, or_timeout), names)

    # OpenAI. Built whenever a key is present and OpenAI is actually wanted -
    # either named in LLM_PROVIDER_ORDER (2026-09-03: this is how it becomes
    # PRIMARY rather than a last-resort safety net - set
    # LLM_PROVIDER_ORDER=openai,groq,openrouter), or via the older
    # VOICE_LLM_OPENAI_SAFETY_NET flag, or because it is the only provider
    # configured at all.
    if openai_key and (
        "openai" in _requested_order
        or env_flag("VOICE_LLM_OPENAI_SAFETY_NET", False)
        or not _named_groups
    ):
        openai_timeout = float(os.getenv("OPENAI_ATTEMPT_TIMEOUT", "3.0"))
        _named_groups["openai"] = (
            openai.LLM(
                model=os.getenv("OPENAI_LLM_MODEL", "gpt-4o-mini"),
                api_key=openai_key,
                max_completion_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                max_retries=0,
            ),
            ["openai/gpt-4o-mini"],
        )

    # Assemble in the requested order; anything built but not named in
    # LLM_PROVIDER_ORDER (a typo, or a provider added after the env var was
    # last set) is appended at the end rather than silently dropped.
    seen = set()
    for name in _requested_order:
        if name in _named_groups and name not in seen:
            group, names = _named_groups[name]
            groups.append(group)
            described.extend(names)
            seen.add(name)
    for name, (group, names) in _named_groups.items():
        if name not in seen:
            groups.append(group)
            described.extend(names)
            seen.add(name)

    providers = groups

    # "+"-joined in ASSEMBLY order, not a hardcoded guess - a log line that
    # says "groq" while OpenAI is actually primary is worse than no label.
    provider_name = "+".join(
        name for name in _requested_order if name in _named_groups
    ) or "none"

    pipeline_event(
        "llm", "ok", "LLM fallback chain",
        " -> ".join(described),
        provider=provider_name,
        tiers=len(providers),
        openrouter_keys=len(openrouter_keys),
        groq_keys=len(groq_keys),
        max_tokens=max_tokens,
        temperature=temperature,
    )

    if groq_keys and len(groq_keys) < 2 and not openrouter_keys:
        pipeline_event(
            "llm", "warn", "Single Groq key",
            "Only one Groq key is configured, so all four tiers share it - a "
            "per-key rate limit will take out the whole chain at once. Set "
            "GROQ_API_KEYS with two comma-separated keys or configure OPENROUTER_API_KEY.",
            key_slots=1,
        )

    if len(providers) == 1:
        return providers[0]

    # RACE, DON'T QUEUE (2026-09-04 rewrite): every assembled group starts
    # AT THE SAME TIME by default - not just the first two, with a third
    # tier serialised behind them.
    #
    # Previously this hedged ONLY groups[0] vs groups[1] (a 0.4s head start
    # for the primary before the backup joined), and any further group
    # (groups[2:], e.g. OpenRouter when three providers are configured) sat
    # in a plain serial FallbackAdapter OUTSIDE the hedge - it only got a
    # turn at all if the hedged pair failed outright. Confirmed live: on a
    # healthy call every group answers within ~1-1.5s of each other, so
    # "the fastest provider on THIS turn" was not always groups[0] or
    # groups[1] - a run where OpenRouter happened to be quickest still lost
    # to whichever of the first two finished first, because OpenRouter was
    # never in the race.
    #
    # LLM_HEDGE_DELAY now defaults to 0: every group is started immediately,
    # not after a delay, so "the group that produces a first token first
    # wins" is true for ALL configured providers on EVERY turn, not just as
    # a late-primary recovery mechanism. This is HedgedLLM nested across
    # however many groups were assembled (2 or 3 today) rather than a new
    # primitive - two groups still race in one HedgedLLM exactly as before,
    # a third is folded in as `HedgedLLM(HedgedLLM(a, b), c)`, which with
    # hedge_delay=0 starts all three at once: the inner race's "primary" is
    # itself already a two-way race, not a single slower provider blocking
    # the third from joining immediately.
    #
    # COST: every group is billed for a generation on every turn, not only
    # when a primary is late (this differs from the old design's rare-hedge
    # cost model). Deliberate given this account's measured group latencies
    # are all in the same 0.4-2s band with no single group cheap enough to
    # always answer well ahead of the others - a delayed hedge would rarely
    # fire, so this account gets none of "usually free" and all of "still
    # broken when it does fire". Set SAKHI_LLM_HEDGE=false to fall back to a
    # plain serial FallbackAdapter (first configured group tried first, no
    # racing, no duplicate billing) if the extra generation cost matters
    # more than shaving the slower groups' latency off every turn.
    # See llm_hedge.py for the algorithm and why racing is safe with tool
    # calls (the loser's output is discarded before the agent ever sees it,
    # so a hedged turn can never double-execute record_bp).
    if len(groups) >= 2 and env_flag("SAKHI_LLM_HEDGE", True):
        hedge_delay = float(os.getenv("LLM_HEDGE_DELAY", "0"))
        raced = groups[0]
        for group in groups[1:]:
            raced = HedgedLLM(
                raced,
                group,
                hedge_delay=hedge_delay,
                primary_timeout=float(os.getenv("LLM_GROUP_TIMEOUT", "30.0")),
                backup_timeout=float(os.getenv("LLM_GROUP_TIMEOUT", "30.0")),
            )
        pipeline_event(
            "llm", "ok", "LLM racing enabled",
            f"all {len(groups)} provider group(s) start "
            + ("simultaneously" if hedge_delay == 0 else f"within {hedge_delay}s of each other")
            + " every turn; whichever answers first wins",
            hedge_delay=hedge_delay, groups_raced=len(groups),
        )
        return raced

    # Racing disabled (SAKHI_LLM_HEDGE=false): plain serial fallback, first
    # configured group tried first. The OUTER adapter must not gate the
    # inner groups - each group already enforces a budget sized to its own
    # measured latency, and an outer timeout smaller than a group's internal
    # worst case would cancel that group mid-attempt, re-creating exactly
    # the bug the per-group budgets fix (a provider killed before it could
    # answer). So the outer budget is a backstop against a group hanging
    # entirely, not a per-attempt deadline.
    # 2026-09-09, explicit instruction (OpenAI as primary, no racing): retry
    # the PRIMARY provider a couple of times before actually failing over,
    # rather than the previous max_retry_per_llm=0 (one failure = instant
    # failover). "0" stays the default - the racing path above never needed
    # this (a race already tries every provider every turn), and changing
    # the shipped default for every existing single-provider/sequential
    # setup on this one request would be a bigger, unrequested change.
    return llm.FallbackAdapter(
        providers,
        attempt_timeout=float(os.getenv("LLM_GROUP_TIMEOUT", "30.0")),
        max_retry_per_llm=int(os.getenv("LLM_MAX_RETRY_PER_PROVIDER", "0")),
        retry_interval=float(os.getenv("LLM_RETRY_INTERVAL", "0.0")),
    )


# Validated against the live Sarvam API (2026-07-07): full bulbul:v3 speaker list.
SARVAM_V3_SPEAKERS = {
    "aditya", "ritu", "ashutosh", "priya", "neha", "rahul", "pooja", "rohan",
    "simran", "kavya", "amit", "dev", "ishita", "shreya", "ratan", "varun",
    "manan", "sumit", "roopa", "kabir", "aayan", "shubh", "advait", "anand",
    "tanya", "tarun", "sunny", "mani", "gokul", "vijay", "shruti", "suhani",
    "mohit", "kavitha", "rehan", "soham", "rupali", "niharika",
}


def _build_sarvam_tts(voice_id: str | None, pace_override: float | None = None, language: str = "en-IN") -> tts.TTS:
    diagnostics = env_diagnostics()
    pipeline_event(
        "tts", "info", "TTS env check",
        "Checking Sarvam credentials visible to worker process",
        sarvam=diagnostics["SARVAM_API_KEY"],
    )

    sarvam_model = sarvam_tts_model()
    # Voice validity is model-dependent and the two speaker sets do NOT overlap:
    # sending a v3 name like "pooja" to bulbul:v2 is a hard 400 from the API,
    # which on a live call means the whole turn is silent. resolve_sarvam_voice()
    # pairs them safely and remaps rather than failing.
    requested_speaker = (voice_id or os.getenv("SARVAM_SPEAKER") or "").strip().lower()
    sarvam_model, sarvam_speaker = resolve_sarvam_voice(sarvam_model, requested_speaker)
    if requested_speaker and requested_speaker != sarvam_speaker:
        pipeline_event(
            "tts", "warn", "Voice remapped",
            f"'{requested_speaker}' is not a {sarvam_model} voice; using '{sarvam_speaker}'",
            requested=requested_speaker, speaker=sarvam_speaker, model=sarvam_model,
        )
    elif requested_speaker:
        pipeline_event(
            "tts", "ok", "Voice selected",
            f"Sarvam voice: {sarvam_speaker}", speaker=sarvam_speaker, model=sarvam_model,
        )

    sarvam_language = language
    sarvam_base_url = os.getenv("SARVAM_BASE_URL", "https://api.sarvam.ai")
    # Natural, calm, slightly slower pace (0.92) designed for clear rural clinical comprehension
    sarvam_pace = pace_override if pace_override is not None else default_tts_pace()
    sarvam_min_buffer_size = int(os.getenv("SARVAM_MIN_BUFFER_SIZE", "20"))
    sarvam_max_chunk_length = int(os.getenv("SARVAM_MAX_CHUNK_LENGTH", "160"))
    pipeline_event(
        "tts",
        "info",
        "Sarvam TTS provider",
        f"Sarvam {sarvam_model}; streaming path reported on the next line",
        model=sarvam_model,
        speaker=sarvam_speaker,
        language=sarvam_language,
        base_url=sarvam_base_url,
        pace=sarvam_pace,
        min_buffer_size=sarvam_min_buffer_size,
        max_chunk_length=sarvam_max_chunk_length,
    )
    # All four tuned values below were previously computed, logged in the
    # pipeline event above as though they had been applied, and then NOT passed
    # to the constructor - so SarvamTTS silently used its own defaults for every
    # one of them (changed.md C18). Consequences: the TTS-speed slider and
    # SARVAM_PACE did nothing; SARVAM_PACE_GREETING did nothing, so the
    # "deliberately slower greeting" documented in entrypoint() never actually
    # played slower; SARVAM_BASE_URL could not be redirected; and
    # SARVAM_MIN_BUFFER_SIZE - which decides how much text buffers before the
    # first synthesis request leaves, i.e. time-to-first-audio - was not
    # tunable at all.
    provider = SarvamTTS(
        api_key=required_env("SARVAM_API_KEY"),
        model=sarvam_model,
        speaker=sarvam_speaker,
        target_language_code=sarvam_language,
        base_url=sarvam_base_url,
        pace=sarvam_pace,
        min_buffer_size=sarvam_min_buffer_size,
        max_chunk_length=sarvam_max_chunk_length,
        enable_preprocessing=env_flag("SARVAM_TTS_ENABLE_PREPROCESSING", True),
    )
    if provider.capabilities.streaming:
        pipeline_event("tts", "ok", "Streaming TTS path", "AgentSession will use token-by-token TTS.stream()", streaming=True)
    else:
        # Expected, and not a warning: Sarvam's WebSocket endpoint never returns
        # audio, so the provider declares itself non-streaming and AgentSession
        # routes through StreamAdapter + EagerClauseTokenizer instead - one REST
        # call per clause, first audio after ~440ms.
        pipeline_event(
            "tts", "ok", "Clause-buffered TTS path",
            "StreamAdapter + EagerClauseTokenizer will synthesise one clause at a time",
            streaming=False,
            min_chunk_chars=int(os.getenv("TTS_EAGER_MIN_CHUNK_CHARS", "20")),
        )
    return provider


def _build_rumik_tts(voice_id: str | None) -> tts.TTS:
    resolved_voice = voice_id if voice_id in RUMIK_VOICES else voice_catalog_default("rumik")
    voice_meta = RUMIK_VOICES.get(resolved_voice, {})
    pipeline_event(
        "tts", "info", "Rumik TTS provider",
        f"Rumik Silk {RUMIK_DEFAULT_MODEL} - no fallback chain, description-steered voice",
        model=RUMIK_DEFAULT_MODEL, voice_id=resolved_voice, voice_name=voice_meta.get("name"),
    )
    return RumikTTS(
        api_key=required_env("RUMIK_API_KEY"),
        description=voice_meta.get("description"),
        model=RUMIK_DEFAULT_MODEL,
    )


def _build_smallest_ai_tts(
    voice_id: str | None,
    *,
    speed_override: float | None = None,
    language: "Language" = Language.KANNADA,
) -> tts.TTS:
    """Smallest AI "lightning" streaming TTS, via the official plugin.

    Distinct from `_build_smallest_tts` below, which wraps this project's own
    older hand-rolled SmallestTTS client (smallest_wrappers.py) and predates
    the plugin. This one is the supported path.

    OPT-IN (TTS_PROVIDER=smallest_ai). Vakyam stays the default because it is
    what was actually benchmarked on this deployment - ~120ms median TTFB,
    measured live. A provider swap that has not been measured here is a
    hypothesis, not an optimisation.

    Two settings carry real weight and are both env-tunable, because §6 of
    the architecture brief is explicit that they must be re-measured per
    transport rather than copied:

      sample_rate - 24kHz matches the WebRTC pipeline. This is NOT the 16kHz
        a PSTN deployment would want: there is no 8kHz narrowband ceiling on
        a browser path, so downsampling buys nothing and costs fidelity. The
        number to watch is real-time factor, not "quality" - synthesis slower
        than the audio it produces (RTF > 1) is what trips the SDK's
        slow-generation cutoff and force-flushes mid-word, which is heard as
        the voice breaking up.
      max_buffer_flush_ms - the plugin default of 0 holds every byte until
        end-of-input, which starves a short reply of progressive audio.
    """
    return smallestai.TTS(
        api_key=required_env("SMALLEST_API_KEY"),
        model=os.getenv("SMALLEST_TTS_MODEL", "lightning_v3.1_pro"),
        voice_id=os.getenv("SMALLEST_TTS_VOICE", voice_id or "meher"),
        sample_rate=int(os.getenv("SMALLEST_TTS_SAMPLE_RATE", "24000")),
        speed=speed_override if speed_override is not None else default_tts_pace(),
        language=language.value if isinstance(language, Language) else str(language),
        max_buffer_flush_ms=int(os.getenv("SMALLEST_TTS_MAX_BUFFER_FLUSH_MS", "200")),
    )


def _build_smallest_tts(voice_id: str | None, speed_override: float | None = None) -> tts.TTS:
    resolved_voice = voice_id if voice_id in SMALLEST_VOICES else voice_catalog_default("smallest")
    voice_meta = SMALLEST_VOICES.get(resolved_voice, {})
    pipeline_event(
        "tts", "info", "Smallest.ai TTS provider",
        f"Smallest.ai {SMALLEST_DEFAULT_MODEL} - no fallback chain, sentence-level synthesis",
        model=SMALLEST_DEFAULT_MODEL, voice_id=resolved_voice, voice_name=voice_meta.get("name"),
    )
    # Rest-of-call speed defaults slightly over 1.0 - per-minute call cost
    # matters, and this is the speed used for every LLM turn after the
    # greeting. The greeting itself uses a separate, slower speed - see
    # _build_greeting_tts and SMALLEST_SPEED_GREETING.
    speed = speed_override if speed_override is not None else float(os.getenv("SMALLEST_SPEED", "1.05"))
    return SmallestTTS(
        api_key=required_env("SMALLEST_API_KEY"),
        voice_id=resolved_voice,
        model=SMALLEST_DEFAULT_MODEL,
        sample_rate=SMALLEST_SAMPLE_RATE,
        speed=speed,
    )


def _build_gemini_tts(voice_id: str | None) -> tts.TTS:
    resolved_voice = voice_id if voice_id in GEMINI_VOICES else voice_catalog_default("gemini")
    voice_meta = GEMINI_VOICES.get(resolved_voice, {})
    pipeline_event(
        "tts", "info", "Gemini TTS provider",
        f"Gemini 3.1 Flash TTS - no fallback chain, batch synthesis",
        model="gemini-3.1-flash-tts-preview", voice_id=resolved_voice, voice_name=voice_meta.get("name"),
    )
    return GeminiTTS(
        api_key=required_env("GEMINI_API_KEY"),
        voice_id=resolved_voice,
    )


def default_tts_pace() -> float:
    """The ONE speaking pace for the whole call, both providers, all turns.

    Previously four numbers claimed to be "the pace": 0.92 (Sarvam default),
    0.92 (Gnani default), 0.9 (greeting) and 1.08 (session metadata default).
    They were reached by different paths, so the greeting played ~20% slower
    than the conversation and a provider failover changed speed again. Same
    voice, different tempo, one sentence apart - which is exactly what "the
    voice is changing" sounds like to a listener.
    """
    return float(os.getenv("SARVAM_PACE", "1.0"))


# See _build_vakyam_tts for why these differ by language rather than being
# one voice everywhere. Ananya stays the Kannada default: it is the voice the
# 2026-08-30 benchmark measured at ~119ms TTFB with the tightest spread.
_VAKYAM_VOICE_BY_LANGUAGE = {
    Language.KANNADA: "Ananya",
    Language.HINDI: "Ananya",
    Language.ENGLISH: "Archana",   # clear, measured - see VAKYAM_KANNADA_VOICES
}


def _build_vakyam_tts(
    voice_id: str | None,
    *,
    speed_override: float | None = None,
    language: "Language" = Language.KANNADA,
) -> tts.TTS:
    """Vakyam AI TTS. See vakyam_wrappers.py for the latency benchmark and why."""
    # Per-language voice routing (2026-09-02). All 17 Vakyam voices speak all
    # 8 languages, so the choice is about REGISTER, not capability:
    #
    #   kn-IN -> a warm, unhurried voice that reads as an elder sister in a
    #            Bidar village, not a newsreader.
    #   en-IN -> a clear, even Indian-English voice; an English call is
    #            usually a supervisor or a trainee, where crispness matters
    #            more than warmth.
    #
    # An explicit voice_id from dispatch metadata still wins over both - the
    # frontend picker and a per-call override must not be silently ignored.
    if not voice_id:
        voice_id = os.getenv(
            f"VAKYAM_VOICE_{language.value.upper()}",
            _VAKYAM_VOICE_BY_LANGUAGE.get(language, VAKYAM_DEFAULT_VOICE),
        )
    voice = resolve_vakyam_voice(voice_id)
    provider = VakyamTTS(
        api_key=required_env("VAKYAM_API_KEY"),
        voice=voice,
        language=language.sarvam_code,   # kn-IN / hi-IN, same shape Vakyam wants
        speed=speed_override if speed_override is not None else default_tts_pace(),
    )
    pipeline_event(
        "tts", "ok", "Vakyam TTS provider",
        f"Vakyam {provider.model} voice {provider.voice} via realtime WebSocket, clause-buffered",
        provider="vakyam", model=provider.model, voice=provider.voice,
        language=language.sarvam_code, sample_rate=provider.sample_rate,
    )
    if voice_id and voice_id.strip().lower() != provider.voice.lower():
        pipeline_event(
            "tts", "warn", "Voice remapped",
            f"'{voice_id}' is not a Vakyam voice; using '{provider.voice}'",
            requested=voice_id, voice=provider.voice,
        )
    return provider


# Confirmed real and working live against api.vachana.ai/api/v1/tts/inference
# (2026-09-04) - not copied from docs or the plugin's own (partly wrong -
# "Shubhra" is in its GnaniTTSVoices type hint but the live API 400s on it)
# type hints. Kaveri is the fastest of the six benchmarked (median 1026.7ms
# on a real Kannada clinical sentence, three runs each) and the only one of
# the six also present in the official plugin's own curated voice list -
# see _build_gnani_tts()'s docstring for the full comparison.
GNANI_DEFAULT_VOICE = "Kaveri"
GNANI_KNOWN_FEMALE_VOICES = (
    "Kaveri", "Vidya", "Lavanya", "Radhika", "Madhuri", "Gauri",
    "Janaki", "Nayana", "Lalita",
)


def _resolve_gnani_voice(voice_id: str | None) -> str:
    """Pick a valid Timbre voice, tolerating case and unknown names.

    GNANI_KNOWN_FEMALE_VOICES is the confirmed-real subset of Gnani's actual
    voice catalog (retrieved 2026-09-04 by deliberately sending an invalid
    voice name to the live /tts/inference endpoint and reading the full
    catalog back out of its own 400 error body - not guessed, not copied
    from docs, which is how the previous gnani_wrappers.py list drifted:
    it named "Shubhra" as a real voice via the OFFICIAL plugin's own
    GnaniTTSVoices type hint, and the live API rejected it outright
    ("not in the Timbre speaker catalog"). An unrecognised name here falls
    back to Kaveri rather than being sent through and failing the call.
    """
    if not voice_id:
        return GNANI_DEFAULT_VOICE
    wanted = voice_id.strip()
    for known in GNANI_KNOWN_FEMALE_VOICES:
        if known.lower() == wanted.lower():
            return known
    logger.warning("Unknown Gnani voice %r; using %s", voice_id, GNANI_DEFAULT_VOICE)
    return GNANI_DEFAULT_VOICE


def _build_gnani_tts(
    voice_id: str | None,
    *,
    speed_override: float | None = None,
    language: "Language" = Language.KANNADA,
) -> tts.TTS:
    """Gnani Vachana TTS via the official livekit-plugins-gnani package.

    PROVIDER AND VOICE CHOICE (2026-09-04), NOT ASSUMED - MEASURED
    -----------------------------------------------------------------
    This deployment previously routed Gnani TTS through a hand-rolled
    wrapper (gnani_wrappers.py) built before this session confirmed the
    OFFICIAL plugin (livekit-plugins-gnani, already in requirements.txt)
    has real, working STT AND TTS support - the earlier "Gnani is TTS-only"
    note elsewhere in this file was wrong, corrected here rather than left
    to mislead the next read. The official plugin is used for both now, for
    the same reason every other provider in this file goes through its own
    livekit.plugins.* class rather than custom code: one maintained
    implementation instead of two to keep in sync.

    Voice: tested live against the real /tts/inference endpoint, not
    assumed - 6 candidate female voices (Kaveri, Vidya, Lavanya, Radhika,
    Madhuri, Gauri), 3 runs each, median time-to-first-byte on a real
    Kannada clinical sentence. All 6 clustered tightly (1027-1094ms),
    confirming latency here is model-bound rather than voice-bound, and
    Kaveri came out fastest (1026.7ms median) - it is also the only one of
    those six in the official plugin's own curated GnaniTTSVoices type,
    meaning it is the voice Gnani/the plugin maintainers actually vouch for
    rather than one merely accepted by the raw catalog. Re-confirmed working
    in all three call languages (kn-IN 1213ms, hi-IN 840ms, en-IN 906ms).

    synthesize_method="rest", not "websocket" - also measured, not assumed:
    websocket TTFB ran 1.6-1.8s across three live runs, SLOWER than REST's
    ~1.0s median for a short utterance, most likely because Gnani opens a
    fresh WebSocket handshake per synthesis call rather than reusing a
    connection. "Streaming" does not imply "lower latency" here; the
    measurement is what decided it.

    CRASHED A LIVE CALL 2026-09-04 - `synthesize_method="rest"` ALONE DOES
    NOT MAKE THIS PROVIDER USE REST. Read before touching this again.
    -----------------------------------------------------------------------
    A live call hit `Gnani TTS WebSocket error: server rejected WebSocket
    connection: HTTP 429` on a plain "Are you still there?" check-in -
    despite synthesize_method="rest" above. Traced into the installed SDK:
    gnani.TTS.__init__() hardcodes `capabilities=TTSCapabilities(streaming=
    True)` unconditionally - synthesize_method only selects which
    ChunkedStream `.synthesize()` uses; it does nothing for `.stream()`.
    `.stream()` returns `SynthesizeStream`, whose `_build_ws_url()` is
    hardcoded to `wss://api.vachana.ai/api/v1/tts` with NO synthesize_method
    check at all - confirmed by reading its source, not assumed. And
    `.stream()`, not `.synthesize()`, is what AgentSession actually calls:
    livekit.agents.voice.agent.py's tts_node checks
    `activity.tts.capabilities.streaming` and only wraps a provider in
    StreamAdapter (which drives `.synthesize()`, i.e. REST) when that flag
    is False. Gnani declares it True, so every real turn - not just
    streamed LLM replies but plain session.say() calls too - went straight
    through the always-websocket `.stream()` path regardless of what this
    function configured, which is also almost certainly why the earlier
    six-voice REST benchmark's numbers were never actually representative
    of a live call's real TTS path.

    Fixed below by overriding `._capabilities` on the constructed instance
    to `streaming=False` AFTER construction (capabilities is a plain
    instance attribute behind a read-only property - confirmed safe to
    reassign, not a frozen dataclass). That forces AgentSession to wrap this
    provider in StreamAdapter and drive it through `.synthesize()` -
    RESTChunkedStream - for real, on every call site, matching what
    synthesize_method="rest" above always looked like it already
    guaranteed. Re-verify with a live call (not just a unit test) if this
    is ever touched again: the config-looks-right-but-isn't gap here is
    exactly what let this ship the first time.

    No speed/pace control - checked against the plugin's own
    GnaniTTSOptions dataclass, which has no such field (unlike Vakyam/
    Sarvam's wrappers). speed_override is accepted for call-signature
    parity with every other _build_*_tts() function but is a deliberate
    no-op for this provider; silently accepting and dropping it is more
    honest than pretending to clamp a parameter the real API has no slot
    for.
    """
    voice = _resolve_gnani_voice(voice_id)
    provider = gnani.TTS(
        api_key=required_env("GNANI_API_KEY"),
        voice=voice,
        model="timbre-v2.5",
        language=language.sarvam_code,   # kn-IN / hi-IN / en-IN, same shape Gnani wants
        sample_rate=16000,
        synthesize_method="rest",
    )
    # See the docstring's "CRASHED A LIVE CALL" section: synthesize_method
    # alone never reaches AgentSession's own dispatch decision. This is what
    # actually forces every call site onto REST.
    provider._capabilities = tts.TTSCapabilities(streaming=False)
    pipeline_event(
        "tts", "ok", "Gnani TTS provider",
        f"Gnani {provider.model} voice {voice} via REST, clause-buffered",
        provider="gnani", model=provider.model, voice=voice,
        language=language.sarvam_code, sample_rate=16000,
    )
    if voice_id and voice_id.strip().lower() != voice.lower():
        pipeline_event(
            "tts", "warn", "Voice remapped",
            f"'{voice_id}' is not a Timbre voice; using '{voice}'",
            requested=voice_id, voice=voice,
        )
    return provider


def build_tts(
    tts_provider: str | None = None,
    voice_id: str | None = None,
    speed_override: float | None = None,
    language: "Language | str" = Language.KANNADA,
) -> tts.TTS:
    """Vakyam-primary streaming TTS for Sakhi, with Gnani/Sarvam as fallback.

    `speed_override` was previously accepted and then silently dropped - the
    dashboard's TTS-speed slider moved a value that never reached the provider
    (changed.md C12). It is now passed through as the provider's pace.

    Vakyam AI onboarded 2026-08-30 after a live latency benchmark against real
    Kannada clinical speech (see vakyam_wrappers.py's module docstring and
    logs/vakyam_benchmark.json): ~130ms median time-to-first-byte over its
    realtime WebSocket endpoint, vs 573ms (Sarvam) / 984ms (Gnani) on the same
    style of phrase - decisively faster, hence the new default. `tts_provider`
    is still accepted so dispatch metadata from the existing frontend keeps
    working; an unrecognised value logs a warning and falls through to Vakyam.
    """
    # The provider default lives in env, not in this signature. A hardcoded
    # default here silently overrode TTS_PROVIDER for every caller that did
    # not pass the argument explicitly.
    # 2026-09-08: hardcoded fallback "vakyam" -> "sarvam" - Vakyam is out of
    # credits (confirmed live: "account has 7 credits remaining but this
    # request requires 60"). TTS_PROVIDER in .env is the real, explicit
    # selection; this only matters if that env var is ever missing, but a
    # defensive default pointing at a known-broken provider defeats the
    # point of a defensive default.
    if not tts_provider:
        tts_provider = os.getenv("TTS_PROVIDER", "sarvam").strip().lower()

    lang = language if isinstance(language, Language) else Language.parse(language)
    requested = (tts_provider or "sarvam").strip().lower()

    # Sarvam bulbul:v2/manisha and Gnani timbre-v2.5/Janaki were previously
    # benchmarked as effectively tied (2026-08-17, p50 636ms / 655ms) - see
    # each wrapper's own module docstring for that history. Both are now kept
    # on as the fallback tier below Vakyam, not competing defaults.
    if requested == "gnani":
        primary = _build_gnani_tts(voice_id, speed_override=speed_override, language=lang)
    elif requested == "sarvam":
        primary = _build_sarvam_tts(
            voice_id,
            pace_override=speed_override,
            language=lang.sarvam_code,
        )
        # Repeated clinical prompts dominate a structured ANC call. Once a
        # clause has been synthesized, a disk hit removes the measured Sarvam
        # REST network TTFB entirely. CachedTTS preserves the provider's
        # ChunkedStream contract and performs disk reads off-loop; disable this
        # independently if cache behavior needs to be isolated in production.
        if env_flag("VOICE_TTS_AUDIO_CACHE_ENABLED", True):
            primary = CachedTTS(primary)
    elif requested == "smallest_ai":
        primary = _build_smallest_ai_tts(
            voice_id, speed_override=speed_override, language=lang
        )
    else:
        if requested not in {"", "vakyam"}:
            pipeline_event(
                "tts", "warn", "Unknown TTS provider",
                f"'{tts_provider}' is not a configured provider; using Vakyam",
                requested=tts_provider,
            )
        primary = _build_vakyam_tts(voice_id, speed_override=speed_override, language=lang)

    # Fallback chain - SAME-LANGUAGE ONLY.
    #
    # ROOT CAUSE OF "THE VOICE CHANGES MID-CONVERSATION":
    # TTS is clause-buffered, so every clause is its own synthesis request. The
    # old chain was [Kannada provider, OpenAI alloy]. When ONE clause was slow
    # and tripped the 3s ceiling, that clause alone came back in an ENGLISH
    # voice and the next clause returned to Kannada - the voice audibly
    # switching inside a single sentence. A cross-language fallback is simply
    # not safe in a per-clause pipeline.
    #
    # Three genuine Kannada providers now, so the fallback stays in language:
    # Vakyam -> Gnani -> Sarvam (both non-Vakyam providers chained, per
    # 2026-08-30 pilot direction - Vakyam has no track record on live calls
    # yet, so a single backup is not enough headroom). A failover still
    # changes timbre, but it stays Kannada, stays female, and stays
    # intelligible to an ASHA worker.
    #
    # OpenAI is retained ONLY as a last resort and OFF by default: an English
    # voice reading Kannada text is not a degraded experience, it is an
    # unusable one. Set VOICE_TTS_ENGLISH_LAST_RESORT=true to re-add it.
    if not env_flag("VOICE_TTS_FALLBACK_ENABLED", True):
        return primary

    # BUG FOUND 2026-09-04: this used to fall through to "sarvam" for ANY
    # primary that wasn't Vakyam or Gnani - which silently mis-tagged
    # Smallest AI (requested == "smallest_ai") as "sarvam" too. That name is
    # only used to pick a same-language BACKUP a few lines down, so a
    # Smallest-AI-primary call with GNANI_API_KEY set would silently chain in
    # a Gnani backup meant for a Sarvam primary - a provider swap mid-call
    # the moment one clause was slow enough to trip the FallbackAdapter,
    # which is exactly the "the voice changes" symptom this function's other
    # comment already documents for the Kannada/English case. Currently
    # dormant only because VOICE_TTS_FALLBACK_ENABLED=false in this
    # deployment's .env; fixed here so it stays correct if that ever flips.
    primary_name = (
        "vakyam" if isinstance(primary, VakyamTTS)
        else "gnani" if isinstance(primary, gnani.TTS)
        else "smallest_ai" if requested == "smallest_ai"
        else "sarvam"
    )
    primary_model = getattr(primary, "_model", None) or getattr(primary, "model", "?")
    # gnani.TTS (the official plugin) exposes no public .voice property,
    # unlike every other provider wrapper here - its voice lives on the
    # private _opts dataclass instead. Reached for logging accuracy only;
    # never used for control flow.
    primary_voice = (
        getattr(primary, "voice", None)
        or getattr(primary, "_speaker", None)
        or getattr(getattr(primary, "_opts", None), "voice", None)
        or "?"
    )

    chain = [primary]
    described = [f"{primary_name}/{primary_voice}"]

    # Cross-provider, same-language backups. When Vakyam is primary, chain
    # BOTH other providers in; when one of them is explicitly selected as
    # primary instead (tts_provider="gnani"/"sarvam"), keep the simpler
    # single-backup pairing this fallback chain has always used.
    try:
        if primary_name == "vakyam":
            if os.getenv("GNANI_API_KEY"):
                backup = _build_gnani_tts(
                    os.getenv("GNANI_VOICE", "Janaki"),
                    speed_override=speed_override,
                    language=lang,
                )
                chain.append(backup)
                described.append("gnani/Janaki")
            if os.getenv("SARVAM_API_KEY"):
                backup = _build_sarvam_tts(
                    os.getenv("SARVAM_SPEAKER", "manisha"),
                    pace_override=speed_override,
                    language=lang.sarvam_code,
                )
                chain.append(backup)
                described.append("sarvam/manisha")
        elif primary_name == "gnani" and os.getenv("SARVAM_API_KEY"):
            backup = _build_sarvam_tts(
                os.getenv("SARVAM_SPEAKER", "manisha"),
                pace_override=speed_override,
                language=lang.sarvam_code,
            )
            chain.append(backup)
            described.append("sarvam/manisha")
        elif primary_name == "sarvam" and os.getenv("GNANI_API_KEY"):
            backup = _build_gnani_tts(
                os.getenv("GNANI_VOICE", "Janaki"),
                speed_override=speed_override,
                language=lang,
            )
            chain.append(backup)
            described.append("gnani/Janaki")
    except Exception:
        logger.warning("Could not build the same-language TTS backup", exc_info=True)

    if env_flag("VOICE_TTS_ENGLISH_LAST_RESORT", False) and os.getenv("OPENAI_API_KEY"):
        try:
            chain.append(openai.TTS(
                model=os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts"),
                voice=os.getenv("OPENAI_TTS_VOICE", "alloy"),
                api_key=os.getenv("OPENAI_API_KEY"),
            ))
            described.append("openai/alloy (ENGLISH - last resort)")
        except Exception:
            logger.warning("Could not build the OpenAI TTS last resort", exc_info=True)

    if len(chain) == 1:
        pipeline_event(
            "tts", "warn", "No TTS fallback",
            f"{primary_name} has no same-language backup; a failure is dead air. "
            "Configure the other Indic provider's API key.",
            primary=primary_name, voice=primary_voice,
        )
        return primary

    pipeline_event(
        "tts", "ok", "TTS fallback chain (same-language)",
        " -> ".join(described),
        primary=primary_name, primary_model=primary_model, voice=primary_voice,
        chain=described, sample_rate=primary.sample_rate,
    )
    # Pin the chain to the primary's rate so a failover cannot change the
    # track's sample rate mid-call.
    return tts.FallbackAdapter(
        chain,
        sample_rate=primary.sample_rate,
        max_retry_per_tts=0,
    )


def _build_greeting_tts(
    provider: str,
    voice_id: str | None,
    language: "Language | str" = Language.KANNADA,
) -> tts.TTS | None:
    """A short-lived, slower-paced TTS instance used only for the opening greeting.

    THIS USED TO IGNORE `provider` AND ALWAYS RETURN SARVAM.

    The effect was invisible in the logs unless you read them closely, and it
    silently invalidated voice A/B testing: the session TTS was built correctly
    from the picker, but the GREETING - the only thing a caller hears before
    speaking - was always Sarvam. Selecting a Gnani voice produced
    "Voice remapped: 'nayana' is not a bulbul:v2 voice; using 'manisha'",
    because the greeting builder was handing a Gnani voice name to Sarvam.
    Anyone comparing voices by connecting and listening to the greeting was
    hearing the same Sarvam voice every time.
    """
    lang = language if isinstance(language, Language) else Language.parse(language)
    # Slower than the rest of the call: a caller's first impression is worth the
    # few tenths of a second. Both providers accept this range.
    # Greeting pace now MATCHES the conversation by default. It used to be 0.9
    # against a 1.08 session pace - the same voice ~20% slower, changing speed
    # one sentence into every call. Users reported this as "the voice changes at
    # the start". Override only if you accept that seam.
    slow_pace = float(os.getenv("SARVAM_PACE_GREETING", str(default_tts_pace())))

    normalized_provider = (provider or "").strip().lower()
    if normalized_provider == "gnani":
        return _build_gnani_tts(voice_id, speed_override=slow_pace, language=lang)
    if normalized_provider == "sarvam":
        return _build_sarvam_tts(
            voice_id,
            pace_override=slow_pace,
            language=lang.sarvam_code,
        )

    # Default (including "vakyam" and empty/unset): the same fix already
    # applied once for Gnani above - the greeting must follow whatever
    # provider is actually configured (TTS_PROVIDER, now Vakyam by default),
    # not silently fall through to Sarvam. That exact silent-fallthrough bug
    # made every A/B voice comparison hear Sarvam on the greeting regardless
    # of which provider the rest of the call used.
    return _build_vakyam_tts(voice_id, speed_override=slow_pace, language=lang)


def question_lock_enabled() -> bool:
    """Intake questions spoken verbatim by the code (sakhi_question_lock).
    SAKHI_QUESTION_LOCK=false returns the wording to the model."""
    return env_flag("SAKHI_QUESTION_LOCK", True) and env_flag("SAKHI_LEAN_INTAKE_MODE", True)


def preemptive_generation_enabled() -> bool:
    """Speculative replies before the turn is committed. Off unless opted in -
    see the preemptive_generation comment in build_turn_handling."""
    return env_flag("PREEMPTIVE_GENERATION", False)


def build_turn_handling(silence_threshold: float = 0.30) -> TurnHandlingOptions:
    # "vad": commit the turn at Silero end-of-speech once the final transcript
    # is in. "stt": additionally wait for the provider's own end-of-turn signal
    # - with Deepgram primary that signal (speech_final) was measured trailing
    # its final transcripts by ~500-600ms, pushing EOU p50 to ~1.1s while
    # transcript finals landed at 380-680ms. VAD mode removes that wait; the
    # deterministic incomplete-fragment guard (looks_incomplete) protects
    # against committing mid-sentence pauses. Set VOICE_TURN_DETECTION_MODE=stt
    # to restore the old behaviour.
    turn_detection = os.getenv("VOICE_TURN_DETECTION_MODE", "vad").strip().lower()
    if turn_detection not in {"vad", "stt", "manual"}:
        turn_detection = "vad"
    if multilingual_model is not None:
        turn_detection = multilingual_model()
    else:
        logger.warning("livekit-agents-turn-detector is not installed; falling back to VAD/STT turn detection.")

    # Paced for rural ASHA communication: allows unhurried turn-taking and thinking pauses.
    min_delay = float(silence_threshold)
    max_delay = min(float(os.getenv("MAX_ENDPOINTING_DELAY", "1.2")), 1.2)
    pipeline_event(
        "turn",
        "info",
        "Turn handling config",
        "Turn detection and endpointing configured",
        turn_detection=turn_detection if isinstance(turn_detection, str) else type(turn_detection).__name__,
        min_endpointing_delay=min_delay,
        max_endpointing_delay=max_delay,
        hard_max_turn_silence_s=1.2,
        preemptive_generation=preemptive_generation_enabled(),
        preemptive_tts=env_flag("PREEMPTIVE_TTS", True),
    )
    return TurnHandlingOptions(
        turn_detection=turn_detection,
        endpointing=EndpointingOptions(
            mode="dynamic",
            min_delay=min_delay,
            max_delay=max_delay,
        ),
        # Backchannels ("haan", "ಹೌದು", "hmm") must not cut Sakhi mid-sentence,
        # so a real interruption needs a couple of words behind it.
        #
        # The committed values were min_duration=5.0s / min_words=15, which do
        # not filter backchannels - they disable barge-in outright. An ASHA
        # worker could not interrupt for five seconds or fifteen words, which on
        # an urgent call is the wrong failure: she may be trying to correct a
        # misheard BP reading. Back to the values the comment above always
        # described (changed.md L4).
        interruption={
            # min_duration nudged 0.25 -> 0.35 (2026-09-04, env-overridable)
            # to absorb a stray cough/click without needing a full extra
            # word - min_words is DELIBERATELY kept at 1, not raised to 2 as
            # once re-proposed: the comment above and changed.md L4 already
            # document why a words-based floor is the wrong lever here (an
            # ASHA worker correcting a misheard BP reading may only get out
            # one word - "no" - before needing to be heard).
            "min_duration": float(os.getenv("MIN_INTERRUPTION_DURATION", "0.35")),
            "min_words": int(os.getenv("MIN_INTERRUPTION_WORDS", "1")),
            "resume_false_interruption": True,
            "false_interruption_timeout": float(os.getenv("FALSE_INTERRUPTION_TIMEOUT", "1.5")),
        },
        # OFF by default (2026-09-02). Speculative generation starts a reply
        # against a PARTIAL transcript; in noisy field telephony that partial
        # is frequently wrong - a horn, a bystander, a crying infant - so the
        # speculative turn collides with the real one and the ASHA worker
        # hears Sakhi talk over herself. Correctness beats the ~200ms it
        # would save on a clean line.
        #
        # 2026-09-24: the env default had drifted back to True, contradicting
        # the paragraph above, and it was the cause of the repeated-question
        # reports. The speculative reply runs llm_node BEFORE
        # on_user_turn_completed records her answer, so it is generated
        # against the still-armed question - and livekit reuses it whenever
        # the chat context is unchanged, which it is (intake state lives
        # outside the chat context). Live log 2026-09-22 12:12: q01 armed and
        # asked at transcript-final, answer recorded 2.5s later, q01 asked
        # again. Off by default; PREEMPTIVE_GENERATION=true to opt back in.
        preemptive_generation=PreemptiveGenerationOptions(
            enabled=preemptive_generation_enabled(),
            preemptive_tts=env_flag("PREEMPTIVE_TTS", True),
        ),
    )
def build_initial_context(
    preloaded_user: dict | None = None,
    language: "Language | str" = Language.KANNADA,
) -> llm.ChatContext:
    """Sakhi's system prompt.

    Rewritten from the clinic prompt, which still carried CLINIC_NAME, a
    seven-day appointment calendar, "the clinic is CLOSED on Sundays", and a
    Malayalam/"You are Gracy" block - none of it reachable on a maternal call,
    all of it paid for in prefill on every single generation (changed.md L2).

    Two deliberate omissions from the old prompt:

    * The `[VAD: FAST]` / `[VAD: SLOW]` tag protocol is gone. It forced extra
      output tokens onto the critical path and leaked into TTS whenever the
      strip transform missed one. Endpointing is decided by DynamicVADController
      from Sakhi's own outgoing text instead - no model involvement, no tokens.
    * The triage thresholds are gone. The model is told to *collect* readings and
      *speak* outcomes; TriageEngine decides what the outcome is. Restating
      "systolic >= 140" here would invite the model to second-guess the engine.
    """
    lang = language if isinstance(language, Language) else Language.parse(language)

    # Compressed to roughly a third of its previous size. Prefill is paid on
    # EVERY generation, so prompt length is a per-turn latency tax, not a
    # one-off cost.
    #
    # What was cut and why it is safe to cut:
    #   - The four-step WORKFLOW prose is now three tool names in order. The
    #     tools' own docstrings already describe when to call each one, and the
    #     model sees those on every request anyway - restating them in the
    #     prompt paid for the same information twice.
    #   - The TONE paragraph is gone. Each tool result carries an explicit
    #     `instruction` field written for that exact clinical situation, which
    #     steers delivery far more reliably than adjectives in a preamble.
    #   - "Never ask twice" is gone: SakhiCallState.prompt_summary() is injected
    #     fresh every turn and states what is already recorded.
    #
    # What is deliberately kept: identity, language + honorific, the "follow the
    # tool's instruction" contract, and the three hard safety rules. Those are
    # the lines that change model behaviour on a clinical call.
    initial_ctx = llm.ChatContext()
    lean_mode = env_flag("SAKHI_LEAN_INTAKE_MODE", True)
    if lean_mode:
        initial_ctx.add_message(
            role="system",
            content=system_prompt_for(
                lang,
                stage_desc="Demographics",
                stage=1,
            ),
        )
    else:
        initial_ctx.add_message(role="system", content=system_prompt_for(lang))

    if preloaded_user:
        initial_ctx.add_message(
            role="system",
            content="# CALLER CONTEXT\n" + patient_context_prompt(preloaded_user),
        )

    return initial_ctx


# Sakhi's scripted close. Kannada first (the Bidar pilot language), with the
# transliteration kept so the farewell detector below has an ASCII anchor too.
GOODBYE_TEXT = (
    "ಧನ್ಯವಾದಗಳು ಅಕ್ಕಾರ... ತಾಯಿಯನ್ನು ಚೆನ್ನಾಗಿ ನೋಡಿಕೊಳ್ಳಿ. ನಮಸ್ಕಾರ."
)

GOODBYE_TEXTS = {
    Language.KANNADA: "ಧನ್ಯವಾದಗಳು ಅಕ್ಕಾರ... ತಾಯಿಯನ್ನು ಚೆನ್ನಾಗಿ ನೋಡಿಕೊಳ್ಳಿ. ನಮಸ್ಕಾರ.",
    Language.HINDI: "धन्यवाद दीदी... माँ का ख़्याल रखिएगा। नमस्ते।",
    Language.ENGLISH: "Thank you Didi... Take good care of the mother. Goodbye.",
}


def goodbye_for(language: "Language | str" = Language.KANNADA) -> str:
    lang = language if isinstance(language, Language) else Language.parse(language)
    return GOODBYE_TEXTS.get(lang, GOODBYE_TEXTS[Language.KANNADA])


# Markers unique to the scripted farewell lines (prompt CLOSE + GOODBYE_TEXT).
# Spoken by Sakhi = the conversation is over; used to auto-end the call.
#
# THIS REGEX USED TO END CALLS ON THE GREETING.
#
# It matched "ನಮಸ್ಕಾರ" / "नमस्ते" / "namaste" - which in Kannada and Hindi mean
# BOTH hello and goodbye. Every greeting opens with that word, so Sakhi said
# hello, the farewell detector fired, and the room was deleted before the ASHA
# worker could say anything. That is the "frontend gets stopped when talking"
# report: it stopped the moment the greeting finished.
#
# It also matched bare "ಧನ್ಯವಾದಗಳು" / "धन्यवाद" / "thank you", which Sakhi says
# routinely mid-call ("Thank you Akkara, I have noted the reading") - so even
# without the greeting bug, an ordinary courteous turn could hang up the call.
#
# The fix is to anchor on the one clause that appears ONLY in the scripted
# goodbye and nowhere else in the protocol: "take good care of the mother".
# Greetings and thanks are no longer farewell evidence.
_FAREWELL_RE = re.compile(
    r"ತಾಯಿಯನ್ನು\s*ಚೆನ್ನಾಗಿ\s*ನೋಡಿಕೊಳ್ಳಿ"      # Kannada: "take good care of the mother"
    r"|माँ\s*का\s*ख़्याल\s*रखिएगा"              # Hindi, same clause
    r"|maa\s*ka\s*khayal\s*rakhiega"
    r"|taayiyannu\s*chennaagi\s*nodikolli"
    r"|take\s+good\s+care\s+of\s+the\s+mother"
    # q17, the questionnaire's closing statement (2026-09-25, Turn 30: the
    # call stayed open after it). Unique to the close, like the clause above.
    r"|ಎಲ್ಲಾ\s*ವಿವರಗಳನ್ನು\s*ಯಶಸ್ವಿಯಾಗಿ\s*ದಾಖಲಿಸಲಾಗಿದೆ"
    r"|सभी\s*विवरण\s*सफलतापूर्वक\s*दर्ज"
    r"|ಶುಭ\s*ದಿನ|ಮುಕ್ತಾಯ|visit\s+complete|call\s+complete",
    re.IGNORECASE,
)

# Thanks are NOT farewell evidence mid-call ("ಧನ್ಯವಾದಗಳು ಅಕ್ಕಾರ. ಈಗ
# ರಕ್ತದೊತ್ತಡ ಹೇಳಿ." = thank you, now tell me the BP). They end the call only
# once the questionnaire is complete - see _maybe_schedule_farewell_hangup.
_GRATITUDE_RE = re.compile(
    r"ಧನ್ಯವಾದ|धन्यवाद|शुक्रिया|thank\s+you", re.IGNORECASE
)

# Spoken when the LLM produces a completely empty turn. Localized per call language.
_RETRY_PROMPT = "ಕ್ಷಮಿಸಿ ಅಕ್ಕಾರ, ಸರಿಯಾಗಿ ಕೇಳಿಸಲಿಲ್ಲ... ಇನ್ನೊಮ್ಮೆ ಹೇಳುತ್ತೀರಾ?"

RETRY_PROMPTS = {
    Language.KANNADA: "ಕ್ಷಮಿಸಿ ಅಕ್ಕಾರ, ಸರಿಯಾಗಿ ಕೇಳಿಸಲಿಲ್ಲ... ಇನ್ನೊಮ್ಮೆ ಹೇಳುತ್ತೀರಾ?",
    Language.HINDI: "माफ़ कीजिए दीदी, ठीक से सुनाई नहीं दिया... क्या आप फिर से कह सकती हैं?",
    Language.ENGLISH: "Sorry Didi, I could not hear that clearly... Could you please repeat?",
}


def retry_prompt_for(language: "Language | str" = Language.KANNADA) -> str:
    lang = language if isinstance(language, Language) else Language.parse(language)
    return RETRY_PROMPTS.get(lang, RETRY_PROMPTS[Language.KANNADA])
# NOTE: the deterministic fast path lived here - looks_incomplete,
# extract_spoken_name, assemble_spelled_name, fast_path_reply and their regex
# tables (~145 lines). It answered a handful of turns without an LLM round trip,
# which was a real latency win *for the clinic booking script it was written
# for*: English name capture, "may I have your phone number", 10-digit echo-back
# confirmation, "what brings you in today".
#
# None of those turns exist in a Kannada maternal screening call, so on this
# product the patterns either never matched or matched the wrong thing. Removing
# it also removes a whole class of misfire on a clinical call. If a maternal
# fast path is wanted later (e.g. a bare "ಹೌದು"/"ಇಲ್ಲ" answer to the symptom
# questions), it belongs in sakhi_domain with tests, not in regex tables here.
# See changed.md C16.


# --- Eager clause-level TTS chunking -----------------------------------------
# livekit-agents' default tts_node wraps any non-natively-streaming TTS
# (Smallest.ai, Rumik, Gemini here - Sarvam already streams natively over its
# own WebSocket) in tokenize.blingfire.SentenceTokenizer, which only ever cuts
# text at a FULL sentence boundary (.!?). For a longer reply sentence, that
# means TTS waits for the whole sentence to finish generating before it can
# start synthesizing any of it - measured live, LLM generation runs
# ~100 tokens/s on Groq (see turn_latency logs), so a 15-20 word first
# sentence costs an extra ~120-180ms of pure waiting before the first TTS
# request is even sent, on top of the TTS request's own latency.
# This tokenizer additionally cuts at a clause boundary (comma/semicolon/
# colon/em-dash) once the buffered clause is long enough to sound natural on
# its own, so the first chunk reaches TTS sooner. Mirrors the min/max
# buffer-size knobs already tuned for Sarvam's native streaming
# (SARVAM_MIN_BUFFER_SIZE/SARVAM_MAX_CHUNK_LENGTH) so the same tuning applies
# uniformly. Trade-off: an independent TTS call per clause loses a small
# amount of cross-clause prosody compared to synthesizing the whole sentence
# at once - acceptable for a short clinic reply, verify by listening before
# assuming it's free.
# Clause boundaries for TTS chunking, with negative lookarounds for the cases
# that produce audibly wrong cuts on a clinical call:
#
#   (?<!\d) and (?!\d)  - "145.5", "1,20,000", "9:30" must not split. A BP
#                         reading cut in half mid-number is read as two numbers.
#   (?<!\bDr|Mr|Mrs)    - "Dr. Rao" must not split after the honorific.
#   (?<!\bmg|ml|min)    - "500 mg. twice" and "20 min." are unit abbreviations.
#   (?<!\bBP)           - "BP. 145" appears when STT punctuates an initialism.
#
# The Kannada danda (।) and Devanagari punctuation are included because Sarvam
# emits them in Indic output and they are genuine sentence ends.
#
# ಮತ್ತು ("and") / ಆದರೆ ("but") added 2026-08-30: real Kannada conjunction
# boundaries, not just punctuation - splitting there gets audio flowing
# sooner on a long compound sentence, the same reasoning that already
# applies to the lighter comma/semicolon split just below. Same trailing-
# whitespace requirement as every other alternative here, so this can't
# fire mid-word inside something that merely contains those characters.
_TTS_CLAUSE_CUT_RE = re.compile(
    r"(?<!\d)(?<!\bDr)(?<!\bMr)(?<!\bMrs)(?<!\bmg)(?<!\bml)(?<!\bmin)(?<!\bBP)"
    r"(?:[.!?…।॥][\"'”’]?\s+|[,;:]\s+|—\s+|ಮತ್ತು\s+|ಆದರೆ\s+)"
    r"(?!\d)"
)

# Never emit a fragment shorter than this. A 3-character chunk is its own TTS
# request with its own network round-trip and its own prosody contour, so it
# sounds clipped and costs more than it saves. Applies on top of the adaptive
# min/rest thresholds below.
TTS_MIN_SENTENCE_CHARS = int(os.getenv("TTS_MIN_SENTENCE_CHARS", "10"))


class EagerClauseSentenceStream(SentenceStream):
    def __init__(self, *, min_chunk_chars: int, max_chunk_chars: int,
                 rest_min_chunk_chars: int | None = None) -> None:
        super().__init__()
        self._buf = ""
        self._min_chunk_chars = min_chunk_chars
        self._max_chunk_chars = max_chunk_chars
        # Adaptive chunking: only the FIRST chunk uses the small eager
        # threshold - that's the one that determines time-to-first-audio.
        # Every later chunk waits for a bigger buffer, because each chunk is
        # an independent TTS synthesis and every boundary between two chunks
        # is an audible prosody seam ("voice breaking"). One small chunk +
        # few large ones keeps the latency win while roughly halving the
        # number of seams per reply versus cutting at every clause.
        self._rest_min_chunk_chars = rest_min_chunk_chars or min_chunk_chars
        self._emitted_first = False
        self._segment_id = shortuuid()

    @property
    def _active_min(self) -> int:
        base = self._min_chunk_chars if not self._emitted_first else self._rest_min_chunk_chars
        # Hard floor regardless of how the thresholds are tuned.
        return max(base, TTS_MIN_SENTENCE_CHARS)

    def _find_cut(self) -> int | None:
        buf = self._buf
        n = len(buf)
        if n == 0:
            return None
        if n >= self._max_chunk_chars:
            # Force a cut before the buffer grows unbounded (e.g. a long
            # run-on sentence with no punctuation at all). Cut at the last
            # word boundary so we never split a word in half.
            space = buf.rfind(" ", self._active_min, self._max_chunk_chars)
            return (space + 1) if space != -1 else self._max_chunk_chars
        for match in _TTS_CLAUSE_CUT_RE.finditer(buf):
            if match.end() >= self._active_min:
                return match.end()
        return None

    def push_text(self, text: str) -> None:
        self._check_not_closed()
        self._buf += text
        while True:
            cut = self._find_cut()
            if cut is None:
                return
            chunk, self._buf = self._buf[:cut].strip(), self._buf[cut:].lstrip()
            if chunk:
                self._event_ch.send_nowait(TokenData(token=chunk, segment_id=self._segment_id))
                self._emitted_first = True

    def flush(self) -> None:
        self._check_not_closed()
        remainder = self._buf.strip()
        if remainder:
            self._event_ch.send_nowait(TokenData(token=remainder, segment_id=self._segment_id))
        self._buf = ""
        self._segment_id = shortuuid()
        self._emitted_first = False

    def end_input(self) -> None:
        self.flush()
        self._event_ch.close()

    async def aclose(self) -> None:
        self._event_ch.close()


class EagerClauseTokenizer(SentenceTokenizer):
    """Drop-in tokenize.SentenceTokenizer for tts.StreamAdapter - see the
    module-level comment above for why this exists instead of the framework
    default (tokenize.blingfire.SentenceTokenizer)."""

    def __init__(self, *, min_chunk_chars: int = 35, max_chunk_chars: int = 160,
                 rest_min_chunk_chars: int = 90) -> None:
        self._min_chunk_chars = min_chunk_chars
        self._max_chunk_chars = max_chunk_chars
        self._rest_min_chunk_chars = rest_min_chunk_chars

    def tokenize(self, text: str, *, language: str | None = None) -> list[str]:
        stream = self.stream(language=language)
        stream.push_text(text)
        stream.end_input()
        chunks: list[str] = []
        # ChanEmpty is intentionally not exported by livekit-agents 1.6.10.
        # Drain by state instead of importing that private exception.
        while not stream._event_ch.empty():
            chunks.append(stream._event_ch.recv_nowait().token)
        return chunks

    def stream(self, *, language: str | None = None) -> SentenceStream:
        return EagerClauseSentenceStream(
            min_chunk_chars=self._min_chunk_chars,
            max_chunk_chars=self._max_chunk_chars,
            rest_min_chunk_chars=self._rest_min_chunk_chars,
        )


def _safe_say(session, text: str) -> None:
    """session.say() is synchronous and raises RuntimeError("AgentSession
    isn't running") immediately if the session has already died (observed
    live: WebRTC data channels can close unexpectedly without the room's
    connection_state reflecting it for 10+ seconds). The deterministic
    fast-path and FAQ cache call this instead of session.say() directly so a
    dead session fails one turn instead of raising out of
    on_user_turn_completed uncaught.
    """
    try:
        session.say(text, allow_interruptions=True)
    except Exception:
        pipeline_event(
            "turn", "warn", "Deterministic reply failed to speak",
            "session.say() raised - the AgentSession may have died",
            traceback=traceback.format_exc(),
        )


# The per-call ground-truth object is now the maternal SakhiCallState from
# sakhi_domain.py. The booking CallState that used to be defined here carried
# doctor / booking_confirmed / appointment_id / doctor_preference_asked, and
# llm_node injected its summary into EVERY generation - which kept steering
# Sakhi back toward collecting a doctor preference and confirming an
# appointment, directly against the maternal workflow. See changed.md C5.
CallState = SakhiCallState


# Agent utterances that put the caller under high cognitive load - thinking,
# recalling, or spelling - where a fast endpoint cuts them off mid-effort.
#
# Rewritten for the maternal workflow and for the languages the pilot actually
# runs in. The previous list was English clinic-booking triggers ("which
# doctor", "doctor in mind", "what brings you"), none of which Sakhi ever says -
# so on a Kannada call the slow window could never engage and every question,
# including "how many weeks pregnant is she?", got the fast endpoint.
#
# The cases that need a longer window are the ones where the ASHA worker has to
# recall, read off a monitor, or spell: BP readings, gestational age, names,
# villages, and symptom descriptions.
# Topic words that MIGHT need a long window - a reading, a name, a village, a
# gestational age. On their own they mean nothing: the entire workflow is about
# blood pressure, so "ರಕ್ತದೊತ್ತಡ" appears in almost every turn Sakhi speaks.
# Kannada and Hindi are agglutinative: "ಹೆಸರು" (name) becomes "ಹೆಸರನ್ನು" when
# inflected, so these are STEMS, not whole words. Matching the citation form
# alone silently missed the spelling prompt - the single turn that most needs a
# long window.
_VAD_SLOW_TOPIC_RE = re.compile(
    r"spell|letter by letter|name|village|week|gestational|old"
    r"|\bbp\b|blood pressure|reading|systolic|diastolic|number|symptom|describe|weight"
    r"|headache|vision|swelling"
    # Vitals as the recorded prompts actually say them (q06 "ಬಿಪಿ", q07 "ತೂಕ"):
    # she reads the cuff or the scale, so these need the slow window too.
    r"|ಹೆಸರ|ಊರ|ವಾರ|ರಕ್ತದೊತ್ತಡ|ಬಿಪಿ|ತೂಕ|ಲಕ್ಷಣ|ವಯಸ"    # stems + vitals
    r"|ನೋವು|ಮಂಜ|ಕಣ್ಣ|ಊತ|ಅಕ್ಷರ"                       # symptoms + "letter"
    r"|नाम|गाँव|गांव|सप्ताह|हफ़्ते|रक्तचाप|बीपी|वजन|लक्षण|उम्र"
    r"|सिरदर्द|दर्द|सूजन",
    re.IGNORECASE,
)

# ...so a topic word only earns the long window when Sakhi is ASKING. A
# statement ("ರಕ್ತದೊತ್ತಡ ದಾಖಲಿಸಿದೆ" - blood pressure recorded) requires no
# recall from the worker and must stay on the fast endpoint.
#
# THIS IS THE FIX FOR THE BIGGEST TURN-TAKING COST IN THE SYSTEM. Measured
# across live calls: 403 of 569 turns (71%) took the 1.0s slow window because
# the old pattern matched topic words alone. The adaptive VAD was not adaptive;
# it was stuck slow, adding ~0.7s to two turns in every three.
_VAD_QUESTION_RE = re.compile(
    r"\?"                                        # any script, explicit question
    r"|\b(?:what|which|how|when|who|whose|tell me|say again|repeat)\b"
    r"|ಎಷ್ಟು|ಯಾವ|ಹೇಗೆ|ಯಾರು|ಏನು"                 # Kannada interrogatives
    r"|ಇದೆಯೇ|ಇದೆಯಾ|ಹೇಳಿ|ಹೇಳಿರಿ"                  # ...and request forms
    r"|कितना|कितने|कौन|क्या|कैसे|बताइए|बताओ",
    re.IGNORECASE,
)


class _SlowTriggerMatcher:
    """Slow window only when Sakhi asks something that needs recall.

    Kept as an object with .search() so DynamicVADController's call site is
    unchanged - it still does `_VAD_SLOW_TRIGGER_RE.search(text)`.
    """

    @staticmethod
    def search(text: str):
        if not text:
            return None
        if not _VAD_QUESTION_RE.search(text):
            return None
        return _VAD_SLOW_TOPIC_RE.search(text)


_VAD_SLOW_TRIGGER_RE = _SlowTriggerMatcher()


class DynamicVADController:
    """Adaptive end-of-utterance timing, one instance per call.

    Toggles the session's endpointing min_delay between a fast default and a
    slower "the caller is thinking/spelling" value, based on what the agent
    just asked - lightweight regex on the agent's own outgoing text, no
    models (Render memory limits rule out a semantic classifier).

    Deliberate deviations from the naive approach:
    - The knob is the session's endpointing min_delay via the PUBLIC
      session.update_options(endpointing_opts=...) API - NOT the Silero VAD
      instance's min_silence_duration. The VAD object is shared across
      concurrent calls via proc.userdata; mutating it would change turn
      timing for every other live call on this worker.
    - Perceived silence before commit ~= VAD silence (0.2s) + min_delay, so
      the state values are calibrated to land at roughly 0.55s fast / 1.2s
      slow / 1.5s fragment-recovery total.
    - FAST reuses the MIN_ENDPOINTING_DELAY env default so enabling this
      feature changes nothing about the tuned baseline.
    """

    def __init__(self, session, pacing: "PacingTracker | None" = None) -> None:
        self._session = session
        self._state = "TRANSACTIONAL"
        self._delays = {
            "TRANSACTIONAL": float(os.getenv("MIN_ENDPOINTING_DELAY", "0.15")),
            "HIGH_COGNITIVE_LOAD": float(os.getenv("VAD_ENDPOINT_SLOW_S", "0.8")),
            "FRAGMENT_RECOVERY": float(os.getenv("VAD_ENDPOINT_FRAGMENT_S", "1.1")),
        }
        # Speaking-rate observer. The base wait still comes from what Sakhi
        # ASKED (that signal knows she will need thinking time before she
        # starts); this only nudges it by a bounded amount once it has enough
        # samples to be worth trusting. See sakhi_pacing.py.
        self._pacing = pacing

    def _delay_for(self, state: str) -> float:
        """Base wait for `state`, plus the worker's pacing offset."""
        base = self._delays[state]
        if self._pacing is None or not env_flag("SAKHI_ADAPTIVE_PACING", True):
            return base
        # Never let the nudge invert the ordering the states encode: a
        # fragment-recovery window must stay wider than a transactional one
        # however fast she talks.
        return max(0.20, min(2.5, base + self._pacing.offset_seconds()))

    @property
    def state(self) -> str:
        return self._state

    def _apply(self, new_state: str, reason: str, *, force: bool = False) -> None:
        if new_state == self._state and not force:
            return
        delay = self._delay_for(new_state)
        try:
            self._session.update_options(endpointing_opts={"min_delay": delay})
        except Exception:
            # A dead/closing session must never take down the turn that
            # triggered the change - endpointing just stays where it was.
            logger.warning("Adaptive VAD update failed; keeping previous endpointing", exc_info=True)
            return
        self._state = new_state
        pipeline_event(
            "turn", "info", "Adaptive VAD mode",
            reason if self._pacing is None else f"{reason} | {self._pacing.describe()}",
            event="vad_mode", state=new_state,
            threshold_ms=round(delay * 1000),
        )

    def observe_user_speech(self, transcript: str, speech_seconds: float) -> None:
        """Feed one completed utterance to the pacing tracker.

        Re-applies the CURRENT state so a newly-learned rate takes effect on
        the next turn rather than waiting for the state itself to change - a
        worker who speaks deliberately throughout would otherwise never
        benefit, because the state stays TRANSACTIONAL the whole call.
        """
        if self._pacing is None:
            return
        before = self._pacing.offset_seconds()
        self._pacing.observe(transcript, speech_seconds)
        if self._pacing.offset_seconds() != before:
            self._apply(self._state, "Speaking-rate estimate updated", force=True)

    def evaluate_agent_text(self, text: str) -> None:
        """Called with what the agent just said; sets the wait for the
        caller's NEXT turn."""
        if not text:
            return
        if _VAD_SLOW_TRIGGER_RE.search(text):
            self._apply("HIGH_COGNITIVE_LOAD", "Agent asked a high-cognitive-load question")
        else:
            self._apply("TRANSACTIONAL", "Agent asked a closed/transactional question")

    def fragment_recovery(self) -> None:
        """Extend the listen window because the caller was cut off mid-sentence.

        Triggered by the zero-TTS turn loop when an answer ends on a Kannada,
        Hindi or English connective (looks_incomplete); the fragment is held
        and joined to the continuation (SakhiAgent._hold_fragment).
        """
        self._apply("FRAGMENT_RECOVERY", "Incomplete fragment detected; extending the listen window")

    def on_user_turn(self) -> None:
        """A user turn arrived. Fragment recovery is one-shot: the extended
        window applied to this turn; drop back to the fast default (the
        agent's next reply re-evaluates anyway)."""
        if self._state == "FRAGMENT_RECOVERY":
            self._apply("TRANSACTIONAL", "Fragment window consumed; back to fast endpointing")

    def apply_tag(self, tag: str) -> None:
        """Set endpointing from an explicit [VAD: FAST|SLOW] tag in the reply.

        An explicit signal from the model beats inferring intent from its
        wording, so a tag overrides the regex heuristic for that turn.

        Note what this does NOT do: it never touches the Silero VAD instance,
        which is shared across every concurrent call on this worker via
        proc.userdata. The earlier tag implementation mutated
        `vad_provider.min_silence_duration` and reached into the private
        `session._turn_detector._min_endpointing_delay`, so one caller's reply
        retuned turn-taking for every other live call. This routes through the
        same per-call public API as the rest of the controller.
        """
        normalized = (tag or "").strip().upper()
        if normalized == "FAST":
            self._apply("TRANSACTIONAL", "Model tagged this turn [VAD: FAST]")
        elif normalized == "SLOW":
            self._apply("HIGH_COGNITIVE_LOAD", "Model tagged this turn [VAD: SLOW]")


# Matches the tag anywhere in the reply, tolerant of spacing and case - models
# drift on exact formatting and a missed tag would otherwise be spoken aloud.
_VAD_TAG_RE = re.compile(r"\[\s*VAD\s*:\s*(FAST|SLOW)\s*\]", re.IGNORECASE)

# Longest possible tag plus slack, so a tag split across streamed chunks is
# still caught before any of it reaches TTS.
_VAD_TAG_MAX = 20
_VAD_PARTIAL_RE = re.compile(r"\[[^\]]{0,18}$")

TAG_CLEANER = re.compile(r'\[.*?\]|\(.*?\)')

# The "Dot Anjali" bug, 2026-09-09: confirmed live on a real, current call -
# "Good morning, Anjali.Which month of pregnancy is she in?" and "Noted,
# Didi.When was her last menstrual period?" both actually reached the TTS
# synthesizer this way (Sarvam read the period as "dot"). This is a
# DIFFERENT bug than the sentence-boundary space-loss fix in tts_node()
# (2026-09-04): that one preserved a space _SENTENCE_END_RE's own match (or
# bridge_line_for()'s deliberate trailing space) had already captured before
# sanitize_text()'s .strip() ate it - it has nothing to add when the LLM's
# own raw generation never put a space there in the first place, which is
# what these two real examples are. Matches sentence-final punctuation
# immediately followed by a non-space, non-digit character (a real letter
# in any script - \w alone would also match digits and wrongly split a
# decimal like "3.5" into "3. 5") and inserts the missing space. Applied in
# sanitize_text() so every path that speaks text - streaming chunks, the
# final joined transcript, a future code path - gets this for free.
_MISSING_SENTENCE_SPACE_RE = re.compile(r'([.?!])([^\s\d.?!])')


def sanitize_text(text: str) -> str:
    """Strip debug tags like [VAD: FAST], disallowed TTS punctuation, and parenthetical annotations before TTS or speech output."""
    if not text:
        return ""
    cleaned = TAG_CLEANER.sub('', text).strip()
    cleaned = clean_spoken_punctuation(cleaned)
    return _MISSING_SENTENCE_SPACE_RE.sub(r'\1 \2', cleaned)


class VadTagTransform:
    """Strips [VAD: ...] tags from spoken text and routes them to the controller.

    Registered in `tts_text_transforms`, so it sits between the LLM and TTS and
    guarantees the tag is removed before synthesis. The old implementation put
    this stripping in a closure that ran only sometimes; a tag that slipped
    through was read aloud to the ASHA worker as "bracket vee ay dee".

    The controller is attached after `session.start()` (it needs a live
    session), so this holds a mutable reference rather than the controller
    itself.
    """

    def __init__(self) -> None:
        self.controller: "DynamicVADController | None" = None

    def _route(self, tag: str) -> None:
        if self.controller is None:
            return
        try:
            self.controller.apply_tag(tag)
        except Exception:
            logger.warning("Failed to apply VAD tag %r", tag, exc_info=True)

    async def __call__(self, text):
        held = ""
        async for chunk in text:
            if not chunk:
                continue
            held += chunk

            # Complete tags: route, then remove from the spoken stream.
            def _capture(match: re.Match) -> str:
                self._route(match.group(1))
                return ""

            held = _VAD_TAG_RE.sub(_capture, held)

            # Hold back a trailing fragment that could still become a tag.
            partial = _VAD_PARTIAL_RE.search(held)
            if partial and len(held) - partial.start() <= _VAD_TAG_MAX:
                safe, held = held[: partial.start()], held[partial.start():]
            else:
                safe, held = held, ""

            if safe:
                yield safe

        if held:
            # Stream ended mid-fragment: strip any complete tag and speak the
            # rest rather than swallowing real words.
            held = _VAD_TAG_RE.sub(_capture_final(self), held)
            if held:
                yield held


def _capture_final(transform: "VadTagTransform"):
    def _sub(match: re.Match) -> str:
        transform._route(match.group(1))
        return ""
    return _sub


# NOTE: _AFFIRMATIVE_RE / _NEGATIVE_RE lived here. They resolved a bare
# "yes"/"no" answer to the clinic's digit-by-digit phone-number confirmation
# without an LLM round trip. That confirmation step no longer exists, and both
# patterns were English/Hindi-transliterated only - they would not have matched
# "ಹೌದು" or "ಇಲ್ಲ" anyway.


# Backchannels and vocal tics. An ASHA worker saying "ಹೌದು" while Sakhi is
# mid-sentence is agreeing, not taking the floor - answering it restarts the
# turn and makes the agent feel jumpy.
#
# LiveKit's own interruption filter is duration- and word-count based
# (min_duration 0.6s / min_words 2), which cannot tell "ಹೌದು ಸರಿ" (two words of
# agreement) from a real interruption. This is the content-based layer on top.
_BACKCHANNEL_RE = re.compile(
    r"^\W*(?:"
    r"mm+|hm+|mhm+|mm[\s-]?hmm?|uh[\s-]?huh|uh|ah|oh|ok(?:ay)?|right|yeah|yep|yes|"
    r"ಹೌದು|ಆಯ್ತು|ಸರಿ|ಹೂಂ|ಅಹಾ|"
    r"हाँ|हां|जी|अच्छा|ठीक|हूँ"
    r")[\s,.!?]*$",
    re.IGNORECASE,
)

# Non-speech events some STT engines emit inline.
_VOCAL_TIC_RE = re.compile(
    r"^\W*(?:\[[^\]]*\]|\([^)]*\))\W*$"     # [cough], (sneezes), [laughs]
    r"|^\W*(?:cough|sneez\w*|laugh\w*|clears? throat|breath\w*)\W*$",
    re.IGNORECASE,
)

# Non-directed speech: the ASHA worker talking to someone else in the room, or
# asking Sakhi to wait. Not a turn, and answering it is actively wrong - she is
# mid-task and about to come back.
#
# Kept separate from backchannels because the correct response differs: a
# backchannel means "keep going", this means "pause". Both suppress the turn,
# but this one must not be mistaken for agreement in any future handling.
_NON_DIRECTED_RE = re.compile(
    r"^\W*(?:"
    r"(?:hold|wait|hang)\s*on(?:\s+a\s+(?:sec\w*|min\w*|moment))?"
    r"|(?:just|wait|hold)\s+a\s+(?:sec\w*|min\w*|moment)"
    r"|one\s+(?:sec\w*|min\w*|moment)"
    r"|give\s+me\s+a\s+(?:sec\w*|min\w*|moment)"
    r"|ಒಂದು\s*(?:ನಿಮಿಷ|ಕ್ಷಣ)(?:\s*ತಡೀರಿ|\s*ಇರಿ)?"      # one minute / wait
    r"|ಸ್ವಲ್ಪ\s*ಇರಿ|ತಡೀರಿ|ನಿಲ್ಲಿ"                        # wait / stop
    r"|एक\s*(?:मिनट|सेकंड|पल)(?:\s*रुको|\s*रुकिए)?"
    r"|रुको|रुकिए|ठहरो"
    r")[\s,.!?]*$",
    re.IGNORECASE,
)

# --- side-talk to the patient ---------------------------------------------
#
# An ASHA worker runs the call and the examination at the same time. She turns
# away constantly: "Suma, sit down", "ಉಸಿರು ತೆಗೆದುಕೊಳ್ಳಿ", "don't worry, it
# won't hurt". None of it is addressed to Sakhi, and answering it is worse than
# useless - it burns an LLM round trip, restarts TTS over the worker's voice,
# and can make Sakhi respond to a clinical instruction meant for a patient.
#
# This is enforced HERE rather than in the system prompt on purpose. A prompt
# rule cannot suppress a turn: the model still generates, the audio still
# plays, and the worker is still interrupted. Only StopResponse actually keeps
# Sakhi quiet, and only code can raise it.
#
# The gate is deliberately asymmetric. Suppressing a real question is far more
# damaging than answering one stray aside, so:
#   - any mention of Sakhi means the turn IS for us, whatever else it contains;
#   - a question is never suppressed;
#   - only second-person imperatives aimed at a patient's body or posture
#     count as evidence.
_ADDRESSED_TO_SAKHI_RE = re.compile(r"ಸಖಿ|सखी|\bsakhi\b", re.IGNORECASE)

_PATIENT_DIRECTED_RE = re.compile(
    # English
    r"\b(?:sit|lie|lay)\s+(?:down|back|here|still)\b"
    r"|\b(?:take|draw)\s+a\s+(?:deep\s+)?breath\b"
    r"|\bbreathe\s+(?:in|out|deeply|slowly)\b"
    r"|\b(?:don'?t|do\s+not)\s+(?:worry|move|be\s+afraid|be\s+scared)\b"
    r"|\b(?:come|move)\s+(?:here|closer)\b"
    r"|\b(?:give|show)\s+me\s+your\s+(?:hand|arm|finger)\b"
    r"|\broll\s+up\s+your\s+sleeve\b"
    r"|\b(?:stay|keep)\s+still\b"
    r"|\b(?:relax|hold\s+still)\b"
    # Kannada: sit / lie down / breathe / don't be afraid / give your hand
    r"|ಕುಳಿತುಕೊಳ್ಳಿ|ಕೂತ್ಕೊಳ್ಳಿ|ಮಲಗಿ|ಮಲಗಿಕೊಳ್ಳಿ"
    r"|ಉಸಿರು\s*ತೆಗೆದುಕೊಳ್ಳಿ|ಉಸಿರಾಡಿ"
    r"|ಹೆದರಬೇಡಿ|ಚಿಂತೆ\s*ಮಾಡಬೇಡಿ|ಅಲುಗಾಡಬೇಡಿ"
    r"|ಕೈ\s*ಕೊಡಿ|ಇಲ್ಲಿ\s*ಬನ್ನಿ|ಸುಮ್ಮನೆ\s*ಇರಿ"
    # Hindi
    r"|बैठ\s*जाओ|बैठिए|लेट\s*जाओ|लेटिए"
    r"|साँस\s*(?:लो|लीजिए)|गहरी\s*साँस"
    r"|डरो\s*मत|चिंता\s*मत\s*करो|हिलो\s*मत"
    r"|हाथ\s*दो|यहाँ\s*आओ",
    re.IGNORECASE,
)


def is_side_talk(text: str) -> bool:
    """True when the worker is talking to her patient, not to Sakhi.

    Sakhi must stay silent for these. Errs heavily toward answering: silence
    when the worker actually asked something is the worse failure on a clinical
    call, so ambiguity always resolves to "this was for me".
    """
    stripped = (text or "").strip()
    if not stripped:
        return False
    if _ADDRESSED_TO_SAKHI_RE.search(stripped):
        return False            # named us - it is our turn, whatever it says
    if "?" in stripped:
        return False            # a question is for the one who can answer it
    return bool(_PATIENT_DIRECTED_RE.search(stripped))


# --- "say that again" ------------------------------------------------------
#
# Answered deterministically by re-speaking the question that is already armed
# in the intake tracker, rather than by asking the LLM to recall what it said.
# A model asked to "repeat" paraphrases, and on a clinical call a paraphrased
# question is a different question - it invites a different answer and the
# checklist records it against the same slot.
_REPEAT_REQUEST_RE = re.compile(
    r"^\W*(?:"
    r"(?:can\s+you\s+|please\s+)?(?:say\s+(?:that\s+)?again|repeat(?:\s+(?:that|please|it))?)"
    r"|what(?:\s+was\s+that)?|pardon|sorry\s*\?*"
    r"|ಇನ್ನೊಮ್ಮೆ(?:\s*ಹೇಳಿ|\s*ಹೇಳು)?|ಮತ್ತೊಮ್ಮೆ\s*ಹೇಳಿ|ಮತ್ತೆ\s*ಹೇಳಿ|ಮತ್ತೆ\s*ಹೇಳು"
    r"|ಏನು\s*ಅಂದ್ರಿ|ಏನಂದ್ರಿ|ಕೇಳಿಸಲಿಲ್ಲ|ಸರಿಯಾಗಿ\s*ಕೇಳಿಸಲಿಲ್ಲ"
    r"|फिर\s*से\s*(?:कहो|कहिए|बोलो)|दोबारा\s*(?:कहिए|बोलिए)|क्या\s*कहा"
    r")[\s,.!?]*$",
    re.IGNORECASE,
)


def is_repeat_request(text: str) -> bool:
    """True when the worker asked Sakhi to say her last question again."""
    stripped = (text or "").strip()
    if not stripped or len(stripped) > 40:
        return False
    return bool(_REPEAT_REQUEST_RE.match(stripped))


# Above this, a short utterance is treated as real speech even if it looks like
# a backchannel - a deliberate, drawn-out "ಹೌದು" is an answer.
BACKCHANNEL_MAX_MS = int(os.getenv("BACKCHANNEL_MAX_MS", "400"))

# Holding phrases for the latent filler. Short on purpose: a long filler
# collides with the real reply arriving right behind it.
_LATENT_FILLER = {
    Language.KANNADA: "ಒಂದು ನಿಮಿಷ, ಪರಿಶೀಲಿಸುತ್ತಿದ್ದೇನೆ...",
    Language.HINDI: "एक सेकंड, मैं देख रही हूँ...",
    Language.ENGLISH: "One moment, I am checking...",
}


def is_backchannel(text: str, duration_ms: float | None = None) -> bool:
    """True when a turn is agreement noise or a vocal tic, not a contribution.

    `duration_ms` is honoured when the STT layer provides it: a long utterance
    is real speech whatever its wording. When it is unavailable - Sarvam's
    streaming events do not carry per-utterance duration - the decision falls
    back to wording plus length, which is why the patterns are anchored and
    allow no trailing content.
    """
    if not text or not text.strip():
        return False
    if duration_ms is not None and duration_ms > BACKCHANNEL_MAX_MS:
        return False
    stripped = text.strip()
    if len(stripped) > 30:          # too long to be a grunt in any script
        return False
    return bool(
        _BACKCHANNEL_RE.match(stripped)
        or _VOCAL_TIC_RE.match(stripped)
        or _NON_DIRECTED_RE.match(stripped)
    )


async def wait_for_remote_audio(room, timeout: float = 1.5) -> bool:
    """Block until the participant's audio track is actually subscribed.

    "The room is connected" and "audio is flowing both ways" are different
    events, and the gap between them varies per session - which is why a
    fixed sleep cannot cover it (raise it enough to be safe on a slow join
    and every fast join pays that cost for nothing). This waits on the real
    `track_subscribed` event instead.

    Returns True if audio was confirmed, False if the bounded timeout was
    reached. Deliberately non-fatal on timeout: a session that never reports
    subscription still gets its greeting, just without the guarantee. A
    greeting that never happens is a worse failure than a clipped one.

    In the common case - media already flowing by the time this is reached -
    it returns in well under a millisecond without ever awaiting the event,
    because the already-subscribed check runs first.
    """
    from livekit import rtc

    def _already_subscribed() -> bool:
        for participant in getattr(room, "remote_participants", {}).values():
            for publication in getattr(participant, "track_publications", {}).values():
                if (
                    getattr(publication, "kind", None) == rtc.TrackKind.KIND_AUDIO
                    and getattr(publication, "subscribed", False)
                ):
                    return True
        return False

    if _already_subscribed():
        return True

    subscribed = asyncio.Event()

    def _on_track_subscribed(track, publication, participant) -> None:
        if getattr(track, "kind", None) == rtc.TrackKind.KIND_AUDIO:
            subscribed.set()

    room.on("track_subscribed", _on_track_subscribed)
    try:
        await asyncio.wait_for(subscribed.wait(), timeout=timeout)
        return True
    except asyncio.TimeoutError:
        return False
    finally:
        try:
            room.off("track_subscribed", _on_track_subscribed)
        except Exception:
            pass


# Sentence boundaries for the pre-speech guard. Deliberately SENTENCE-level,
# not the clause-level cut _TTS_CLAUSE_CUT_RE uses for eager synthesis: a
# claim ("I've recorded her BP") is only judgeable once the whole assertion
# is present, and cutting at a comma would hand the guard half a claim with
# its negation still in the unread half. Includes the Devanagari danda.
_SENTENCE_END_RE = re.compile(r"[.!?।]+[\"')\]]*\s+|[.!?।]+[\"')\]]*$")

# Clause-level release for the pre-speech guard (2026-09-22). On production
# Sarvam REST turns, waiting for the first full sentence held a perfectly safe
# opening clause for roughly another 200ms even though EagerClauseTokenizer was
# already able to synthesize it. The guard still vets EVERY released piece;
# this changes only when a vetted piece may leave the buffer. Disable with
# SAKHI_PRESPEECH_GUARD_CLAUSE_LEVEL=false to restore the byte-identical
# sentence-boundary path above while comparing live TTFA.
_CLAUSE_BREAK_RE = re.compile(r"[,;:]\s+|[.!?…]\s+|[।॥]\s+")


# --- Pre-speech correctness guard (2026-09-03) ---------------------------
#
# Stops Sakhi narrating a clinical write that never happened ("I've recorded
# her blood pressure" when record_bp was never called, or its database write
# failed). The model does this without being asked to - it paraphrases the
# outcome it *would* report if the tool had run.
#
# TWO layers, because either alone is insufficient:
#
#   1. An explicit phrase list. Cheap, precise, and catches Sakhi's own
#      templated wording - but a paraphrasing model will always eventually
#      say it a new way, so this can never be the only layer.
#   2. A STRUCTURAL match: a past-tense completion verb landing near a
#      clinical entity, with no negation anywhere in the sentence. This is
#      the layer that catches the wording nobody predicted.
#
# Negation is checked across the WHOLE sentence rather than a window,
# because "I have not recorded her BP yet" and "I couldn't save the reading"
# must both pass through untouched - a guard that blocks honest admissions
# of failure would train the model toward silence about failures, which is
# worse than the problem it fixes.
_CLAIM_VERBS = (
    r"record\w*|sav\w*|logg\w*|not\w*ed|enter\w*|stor\w*|updat\w*|"
    r"add\w*|captur\w*|regist\w*|"
    r"ದಾಖಲ\w*|ಸೇರಿಸ\w*|"                       # Kannada: recorded / added
    r"दर्ज|सहेज\w*|रिकॉर्ड"                      # Hindi: recorded / saved
)
_CLAIM_ENTITIES = (
    r"bp|blood\s*pressure|reading|pressure|systolic|diastolic|"
    r"symptom\w*|sign\w*|vital\w*|chart|record|"
    r"ರಕ್ತದೊತ್ತಡ|ಲಕ್ಷಣ\w*|"                      # Kannada: BP / symptoms
    r"रक्तचाप|लक्षण"                              # Hindi: BP / symptoms
)
_NEGATION_RE = re.compile(
    r"\b(?:not|n't|never|unable|cannot|can't|couldn't|didn't|haven't|"
    r"hasn't|won't|failed|yet|before|will|going\s+to|let\s+me|about\s+to)\b"
    r"|ಇಲ್ಲ|ಮಾಡಿಲ್ಲ|नहीं|नही",
    re.IGNORECASE,
)
_CLAIM_STRUCTURAL_RE = re.compile(
    rf"\b(?:{_CLAIM_VERBS})\b(?:\W+\w+){{0,6}}?\W+\b(?:{_CLAIM_ENTITIES})\b"
    rf"|\b(?:{_CLAIM_ENTITIES})\b(?:\W+\w+){{0,6}}?\W+\b(?:{_CLAIM_VERBS})\b",
    re.IGNORECASE,
)
# Sakhi's own templated phrasings. Exact, cheap, checked first.
_CLAIM_PHRASES = (
    "i have recorded", "i've recorded", "i have saved", "i've saved",
    "i have logged", "i've logged", "i have noted", "i've noted",
    "that is recorded", "that's recorded", "it is recorded", "it's recorded",
    "has been recorded", "have been recorded", "has been saved",
)

# Which receipt authorises which claim. A sentence mentioning symptoms is
# checked against the symptoms receipt, one mentioning BP against the BP
# receipt; a sentence mentioning both needs both.
_BP_MENTION_RE = re.compile(
    r"\b(?:bp|blood\s*pressure|systolic|diastolic|reading|pressure)\b|ರಕ್ತದೊತ್ತಡ|रक्तचाप",
    re.IGNORECASE,
)
_SYMPTOM_MENTION_RE = re.compile(
    r"\b(?:symptom\w*|sign\w*|headache|vision|swelling|bleeding)\b|ಲಕ್ಷಣ|लक्षण",
    re.IGNORECASE,
)


def claims_completed_write(sentence: str) -> bool:
    """Does this sentence assert that a clinical write already happened?

    Pure and side-effect free so it can be tested directly against the exact
    real sentences that slipped through, rather than only against invented
    ones (see tests/test_pre_speech_guard.py).
    """
    if not sentence or not sentence.strip():
        return False
    text = sentence.strip()
    if _NEGATION_RE.search(text):
        return False                      # an honest "not yet" is not a claim
    lowered = text.casefold()
    if any(phrase in lowered for phrase in _CLAIM_PHRASES):
        return True
    return bool(_CLAIM_STRUCTURAL_RE.search(text))


def unbacked_claim_reason(sentence: str, state) -> str | None:
    """Name the receipt this sentence needs but does not have, or None.

    None means the sentence is safe to speak: either it claims nothing, or
    every write it claims actually succeeded this turn.
    """
    if state is None or not claims_completed_write(sentence):
        return None
    bp_ok = bool(getattr(state, "bp_record_succeeded", False))
    symptoms_ok = bool(getattr(state, "symptoms_record_succeeded", False))
    mentions_bp = bool(_BP_MENTION_RE.search(sentence))
    mentions_symptoms = bool(_SYMPTOM_MENTION_RE.search(sentence))

    if mentions_bp and not bp_ok:
        return "blood_pressure"
    if mentions_symptoms and not symptoms_ok:
        return "symptoms"

    # ANAPHORIC CLAIMS. "I have recorded it in her chart" names no entity at
    # all - and that is how a paraphrasing model most often phrases it, since
    # the entity was already named in the previous sentence. Routing by named
    # entity alone silently passes exactly the sentences most likely to be
    # generated, which is how this guard would have shipped looking like it
    # worked. With no entity to route on, the only safe reading is: a
    # completion claim made in a turn where NO write of any kind succeeded is
    # unbacked. If either receipt was earned this turn, the claim plausibly
    # refers to that write and is allowed through.
    if not mentions_bp and not mentions_symptoms and not (bp_ok or symptoms_ok):
        return "unspecified"
    return None


# The honest bridge spoken in place of a blocked claim. Deliberately says
# what is about to happen rather than apologising or going silent - the ASHA
# worker gets a coherent turn, and the next turn is told to actually call the
# tool (see SakhiCallState.unbacked_claim_blocked).
_BRIDGE_LINE = {
    "kn": "ಒಂದು ಕ್ಷಣ, ಅದನ್ನು ಈಗ ದಾಖಲಿಸುತ್ತೇನೆ.",
    "hi": "एक क्षण, मैं इसे अभी दर्ज करती हूँ.",
    "en": "One moment - let me put that into her chart now.",
}


def bridge_line_for(language) -> str:
    """The replacement sentence, in the language the call is actually in.

    Trailing space included: this is substituted mid-stream for a sentence
    that carried its own separator, and without it the bridge runs straight
    into the next sentence ("...ದಾಖಲಿಸುತ್ತೇನೆ.Is she taking iron?"), which
    the TTS clause tokenizer then reads as one run-on.
    """
    code = getattr(language, "value", None) or str(language or "en")
    return _BRIDGE_LINE.get(code[:2].lower(), _BRIDGE_LINE["en"]) + " "


# Question-final punctuation, per script. Kannada and Hindi both use the
# Latin "?" in practice for ASR output, but Devanagari's danda (।) and the
# Kannada full stop can end an interrogative too, so a question is detected on
# the mark OR on the interrogative particle the language actually uses.
_QUESTION_TAIL_RE = re.compile(
    r"(?:\?|\?\s*$"
    r"|(?:ಏನು|ಇದೆಯೇ|ಇದೆಯಾ|ಇದ್ಯಾ|ಹೇಗಿದೆ|ಎಷ್ಟು)\s*[।.!]?\s*$"   # Kannada
    r"|(?:क्या|कैसे|कितना|कितने|है\s*क्या)\s*[।.!]?\s*$"          # Hindi
    r")",
)


def ends_in_question(text: str | None) -> bool:
    """Did Sakhi's own last utterance ask something?

    Half of the assent exemption (see on_user_turn_completed). The other half
    is whether a FAST intake question is armed - this covers the case where
    Sakhi asked a clarifying or confirming question that is not itself one of
    the 17 numbered intake items, which the armed-question check alone would
    miss entirely.
    """
    if not text or not text.strip():
        return False
    return bool(_QUESTION_TAIL_RE.search(text.strip()))


def _answers_pending_question(state, text: str, last_agent_utterance: str | None) -> bool:
    """True when a short affirmative/negative is a real ANSWER, not filler.

    Deliberately narrow. It only exempts a turn from the backchannel filter
    when BOTH of these hold:

      1. the turn is one of the recognised one-word assents (sakhi_intake's
         own set - the same one that will accept it downstream), and
      2. something was actually asked: either a FAST (closed) intake question
         is armed right now, or Sakhi's last spoken line ended in a question.

    Both halves matter. Without (1) this would exempt any short utterance and
    defeat the backchannel filter entirely. Without (2) it would exempt "ಹೂಂ"
    spoken as pure acknowledgement mid-explanation, which is the exact noise
    the filter was built to drop.

    A SLOW (open) question deliberately does NOT exempt: "what brought her in
    today?" answered with a bare "yes" is not an answer, and treating it as
    one is the false-advance bug _is_substantive() already guards against on
    the other side.
    """
    if not text or not text.strip():
        return False
    if not is_assent(text):
        return False

    if state is not None:
        pending_id = getattr(getattr(state, "intake", None), "pending_id", None)
        if pending_id:
            question = QUESTIONS_BY_ID.get(pending_id)
            # A value question (months, a date, a reading) is decided here,
            # not by the fallback below: every Sakhi line ends in a question
            # now (question lock), so the fallback would exempt every "ಹೂಂ".
            if question is not None:
                return accepts_assent(question)

    return ends_in_question(last_agent_utterance)


def _is_deterministic_intake_answer(question, text: str) -> bool:
    """Whether an armed closed-question answer is safe to route without LLM.

    This is intentionally narrower than IntakeProgress.validate_answer(). A
    FAST question may accept a broad value for progress tracking, but bypassing
    clinical interpretation is allowed only when an existing deterministic
    parser resolves that value, or when the shared assent parser recognizes a
    yes/no answer. Unknown dates, weights, BP strings and descriptive replies
    continue to the unchanged LLM/tool path rather than being guessed here.
    """
    if question is None or question.vad.upper() != "FAST" or not text.strip():
        return False
    if is_assent(text):
        return accepts_assent(question)
    if question.id == "q02":
        return detect_gestational_age(text) is not None
    if question.id == "q04":
        return detect_pregnancy_number(text) is not None
    return False


# Dangling connectives: the caller was cut off mid-thought. Committing the turn
# here answers half a sentence, and on a clinical call the missing half is
# often the important one ("her BP is one forty five and ---").
#
# This restores the fragment guard that was removed with the clinic fast path.
# The old one matched English booking phrases ("my name is", "i want to"), so on
# a Kannada call it never fired and FRAGMENT_RECOVERY was unreachable.
_DANGLING_RE = re.compile(
    r"(?:^|\s)(?:"
    r"and|but|because|so|or|if|then|with|for|that|which|when|while"
    r"|ಮತ್ತು|ಆದರೆ|ಏಕೆಂದರೆ|ಅಥವಾ|ಆಗ|ಜೊತೆ"            # Kannada connectives
    r"|और|लेकिन|क्योंकि|या|तो|के\s*साथ"              # Hindi connectives
    r")[\s,]*$",
    re.IGNORECASE,
)


def looks_incomplete(text: str) -> bool:
    """True when an utterance ends on a connective, i.e. more is coming.

    Conservative on purpose: a false positive makes Sakhi wait in silence for a
    continuation that never arrives, which is worse than answering slightly
    early. So it requires a trailing connective AND a short-enough utterance
    that a cut-off is plausible.
    """
    if not text:
        return False
    stripped = text.strip().rstrip(".!?।")
    if not stripped or len(stripped) > 200:
        return False
    return bool(_DANGLING_RE.search(stripped))


def _approx_item_token_units(item) -> int:
    """Character units used by the hot-path token estimate for one item."""
    if getattr(item, "type", None) == "message":
        return len(item.text_content or "")
    return len(str(getattr(item, "arguments", "") or "")) + len(
        str(getattr(item, "output", "") or "")
    )


def _approx_context_tokens(chat_ctx) -> int:
    """Rough token count for a ChatContext.

    Deliberately an estimate, not tiktoken: this runs on the hot path before
    every generation, and a real tokenizer would cost more than the pruning
    saves. Kannada and Devanagari are far less token-dense than English, so the
    divisor is conservative (~3 chars/token) to avoid under-counting the very
    scripts that caused the bloat.
    """
    return sum(_approx_item_token_units(item) for item in chat_ctx.items) // 3


def _prune_conversation_incrementally(items, max_items: int, max_tokens: int):
    """Drop oldest items with the exact legacy estimate, without O(n²) copies.

    A long call previously copied the complete ChatContext and rescanned every
    surviving item after each pop. At 40 transcript/tool items that made the
    pre-generation pruning pass quadratic. We cache each item's character
    units once, subtract on popleft(), and retain the legacy final `// 3`
    rounding so the surviving items are byte-for-byte identical.
    """
    pending = deque(
        (item, _approx_item_token_units(item)) for item in items
    )
    total_units = sum(units for _, units in pending)
    while pending and (
        len(pending) > max_items or total_units // 3 > max_tokens
    ):
        _, removed_units = pending.popleft()
        total_units -= removed_units
    return [item for item, _ in pending]


def lean_stage_tools(toolset: "ClinicalToolset", stage: int, *, escalation_pending: bool = False) -> list:
    """The clinical tools the model may call while the intake is in `stage`.

    Must match what sakhi_intake's questions in that stage actually record
    (stage numbers are IntakeQuestion.stage):

      1 demographics        q01          record_patient_intake
      2 obstetric history   q02-q05,q21  record_gestational_age (+ checkpoints)
      3 vitals              q06-q08,q24,q25  record_bp (+ weight, pallor, FHR checkpoints)
      4 red-flag screening  q09-q12,q18-q20  record_symptoms (+ history, meds checkpoints)
      5+ scans, labs, birth preparedness     record_anc_checkpoint

    Fixed 2026-09-24, two ways:

    * Stages 3 and 4 had each other's tool (vitals got record_symptoms,
      danger signs got record_bp).
    * One tool per stage broke volunteered answers outright. "Suma, 22, seven
      months, BP 120 by 80" at stage 1 makes the model call record_bp, which
      was not in the request; Groq rejects the WHOLE request for that ("tool
      call validation failed ... not in request.tools"), every provider in the
      chain fails the same way, and the turn falls to the emergency path 4-6s
      later ("all LLMs are unavailable"). So every recording tool is always
      offered, the current stage's first - order is the only per-stage part.

    The specialist handoff is added whenever triage has one pending, because
    the URGENT instruction tells the model to call it. Stage 1 must never be
    empty: the September production build scoped it to zero tools, so the
    model could confirm a name it had no way to persist.
    """
    recording = [
        toolset.record_patient_intake,
        toolset.record_gestational_age,
        toolset.record_bp,
        toolset.record_symptoms,
        toolset.record_anc_checkpoint,
    ]
    first = {
        1: toolset.record_patient_intake,
        2: toolset.record_gestational_age,
        3: toolset.record_bp,
        4: toolset.record_symptoms,
    }.get(stage, toolset.record_anc_checkpoint)
    # By NAME: each `toolset.x` lookup is a new bound-method object, so an
    # identity check never matched and the first tool appeared twice
    # ("duplicate function name", the session never started).
    tools = [first, *(t for t in recording if _tool_name(t) != _tool_name(first))]
    # Always offered (its own gate refuses escalation triage did not ask
    # for). Keeping the SET constant for the whole call matters: when the set
    # changed inside llm_node, livekit executed the model's tool call against
    # the list captured before the change ("unknown AI function
    # record_patient_intake", 2026-09-25) and the turn ended in silence.
    tools.append(toolset.connect_smfmi_specialist)
    return tools


class SakhiAgent(Agent):
    """Sakhi's per-call agent.

    Replaces GracyAgent. The framework hooks it overrides are the same three
    (`on_user_turn_completed`, `llm_node`, `tts_node`) - that part of the old
    design was sound. What changed is everything they *did*: the booking fast
    path (name capture, 10-digit phone confirmation, doctor preference) and the
    clinic FAQ cache are gone, and a deterministic pre-eclampsia escalation net
    has taken their place.

    The one safety property this class guarantees:

        if the triage engine says a mother needs a specialist, a specialist is
        connected - whether or not the LLM remembers to call the tool.

    That was the gap behind changed.md C4. The escalation used to depend
    entirely on the model following prose instructions in the system prompt.
    """

    def __init__(
        self,
        *args,
        toolset: ClinicalToolset | None = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._toolset = toolset
        # Attached by entrypoint() right after session.start() (needs a live
        # session); None means adaptive VAD is off and endpointing stays fixed.
        self._vad_controller: DynamicVADController | None = None
        self._farewell_hangup_scheduled = False
        self._state_watch: dict = {}
        # A farewell before the ASHA worker has said ANYTHING is by definition
        # a false positive - there is no conversation to end yet. Independent
        # of the regex, so a future loosening of the pattern cannot resurrect
        # the "call ends on the greeting" failure.
        self._user_has_spoken = False
        # Sakhi's own last spoken line, kept so the turn gate can ask
        # "did I just ask something?" before deciding a one-word reply
        # is agreement noise. See _answers_pending_question().
        self._last_agent_utterance: str = ""
        self._escalation_in_flight = False
        # Final-turn idempotency (2026-09-22 incident).  LiveKit ChatMessage.id
        # is the stable utterance identity; text itself is not (an ASHA worker
        # can legitimately answer "illa" twice).  The timestamp remains only
        # for bounded eviction. Set SAKHI_FINAL_TURN_DEDUPE_ENABLED=false to
        # restore the SDK's unguarded behavior while diagnosing provider events.
        self._processed_turns: dict[str, float] = {}

    # --- helpers -------------------------------------------------------

    def _state(self) -> SakhiCallState | None:
        try:
            state = self.session.userdata
        except Exception:
            return None
        return state if isinstance(state, SakhiCallState) else None

    def _vad_eval(self, spoken_text: str) -> None:
        if self._vad_controller is not None:
            try:
                self._vad_controller.evaluate_agent_text(spoken_text)
            except Exception:
                logger.warning("Adaptive VAD evaluation failed", exc_info=True)

    @staticmethod
    def _turn_id(new_message, raw_text: str) -> str:
        """Stable finalized-utterance id, without putting transcript PII in it."""
        supplied = getattr(new_message, "id", None)
        if supplied:
            return str(supplied)
        # Compatibility fallback for older LiveKit/test ChatMessage objects.
        # Object identity distinguishes two legitimate equal replies while a
        # duplicated callback carrying the same message object remains stable.
        return f"legacy-{id(new_message):x}"

    def _capture_incident_fields(self, state: SakhiCallState, text: str) -> dict:
        """Extract the incident's structured fields before checklist closure.

        This is intentionally not a general NLP rewrite.  It reuses the
        existing domain detectors for demographics, gestational age and
        gravida, applies each value through SakhiCallState's non-overwrite
        mutators, and lets IntakeProgress.sync_from_state skip volunteered
        fields.  Disable with SAKHI_STRUCTURED_TURN_CAPTURE_ENABLED=false.
        """
        captured: dict[str, object] = {}
        if not env_flag("SAKHI_STRUCTURED_TURN_CAPTURE_ENABLED", True):
            return captured

        name, age = detect_patient_demographics(text)
        if name is not None or age is not None:
            before_name, before_age = state.name, state.age
            state.identify(name=name, age=age)
            if state.name != before_name:
                captured["name"] = "[REDACTED]"
            if state.age != before_age:
                captured["age"] = state.age
            if (
                self._toolset is not None
                and state.name
                and state.age
                and (state.name != before_name or state.age != before_age)
            ):
                self._toolset.persist_captured_demographics(state)

        # _capture_gestational_age() already ran immediately before this
        # helper. Report the value only when this utterance actually contains
        # one; do not attribute a value retained from an earlier turn.
        weeks = detect_gestational_age(text)
        if weeks is not None:
            state.identify(gestational_weeks=weeks)
            captured["gestational_weeks"] = state.gestational_weeks

        gravida = detect_pregnancy_number(text)
        if gravida is not None and "gravida" not in state.anc_checkpoints:
            state.record_anc_checkpoint(2, "gravida", str(gravida))
            captured["pregnancy_number"] = gravida

        return captured

    @staticmethod
    def _pending_answer_matches(
        state: SakhiCallState, pending_id: str | None, text: str, captured: dict
    ) -> bool:
        """Typed dispatch: may this turn be offered to the armed question?

        q01/q02/q04 have typed detectors and were the slots proven to
        cross-close in the September incident, so they are decided here.

        Every other question is decided by its typed schema in
        IntakeProgress.validate_answer(question_id=...) - quantitative,
        boolean or general (2026-09-25; Turn 6 "I am the network protocols
        right now" closed a question). That check runs inside record_answer(),
        which is also where the stall-escape lives: an irrelevant turn leaves
        the question armed (needs clarification) and is counted, so the
        question is re-asked at most STALL_LIMIT times. Rejecting it HERE
        would skip record_answer() entirely - no count, no escape, and the
        same question asked for the rest of the call.
        """
        if pending_id is None:
            return False
        if pending_id == "q01":
            return bool(state.name and state.age)
        if pending_id == "q02":
            if "gestational_weeks" in captured:
                return True
            # q02 explicitly also accepts LMP/EDD. Keep that existing route,
            # but require a date-shaped answer rather than any clinical fact.
            return bool(re.search(
                r"\b(?:\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?|"
                r"\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
                r")\b",
                text,
                re.IGNORECASE,
            ))
        if pending_id == "q04":
            return "pregnancy_number" in captured or "gravida" in state.anc_checkpoints
        if pending_id == "q06":
            # BP closes only when a reading is actually RECORDED (record_bp ->
            # sync_from_state), or on an explicit "not measured". 2026-09-28:
            # "140/90" closed q06 here before the LLM ran; the LLM then said
            # "BP was not clear" without recording it, and the question lock
            # appended the weight question. BP is never skipped silently.
            return bool(state.blood_pressure) or bool(
                IntakeProgress._NOT_AVAILABLE_RE.search(text or "")
            )
        if pending_id not in QUESTIONS_BY_ID:
            return False                      # not an intake question: nothing to close
        # Schema-validated in record_answer() -> validate_answer(question_id=...).
        return True

    def _route_deterministic_intake(
        self,
        state: SakhiCallState | None,
        armed_question_id: str | None,
        raw_turn_text: str,
    ) -> None:
        """Speak the next question when the armed closed answer is unambiguous.

        The normal tracker earlier in on_user_turn_completed remains the single
        writer of the answer. This hook runs only after side-talk, repeat,
        fragment, symptom, identity and guardrail gates have all completed.
        That ordering matters: the ~LLM-TTFT saving must never outrun a danger
        sign or medication-safety response. Set
        SAKHI_DETERMINISTIC_INTAKE_ENABLED=false to restore generation for
        every otherwise-safe intake turn.
        """
        if (
            not env_flag("SAKHI_DETERMINISTIC_INTAKE_ENABLED", True)
            or state is None
            or not questionnaire_open(state)
            or not armed_question_id
            or armed_question_id not in state.intake.answered
        ):
            return
        question = QUESTIONS_BY_ID.get(armed_question_id)
        if not _is_deterministic_intake_answer(question, raw_turn_text):
            return
        # Only when the next question has written wording in this call's
        # language - otherwise an English call hears the Kannada question
        # (live-LLM scenario "english_call", 2026-09-24); the LLM asks it.
        upcoming = state.intake.next_question()
        if upcoming is None or not has_localised_text(upcoming, state.language):
            return

        next_question = state.intake.arm_next()
        if next_question is None:
            return
        next_text = question_text(next_question, state.language.value)
        pipeline_event(
            "turn", "ok", "Intake answered deterministically",
            f"{armed_question_id} resolved without an LLM call; asking {next_question.id}",
            event="intake_answered_deterministically",
            question_id=armed_question_id,
            next_question_id=next_question.id,
        )
        _safe_say(self.session, next_text)
        self._vad_eval(f"[VAD: {next_question.vad}] {next_text}")
        raise StopResponse()

    def _maybe_schedule_farewell_hangup(self, spoken_text: str) -> None:
        """End the call when Sakhi speaks her scripted goodbye, even if the LLM
        skipped the end_call tool.

        The farewell markers are matched in Kannada, Hindi and English - the
        previous regex only knew the English clinic goodbye ("pranaam", "have a
        beautiful day"), so on a Kannada call this could never fire and the line
        stayed open until the silence timeout. See changed.md C9.
        """
        if self._farewell_hangup_scheduled:
            return
        if not self._user_has_spoken:
            pipeline_event(
                "worker", "warn", "Farewell ignored - no user turn yet",
                "Farewell wording detected before the ASHA worker spoke; "
                "treating it as a greeting, not a goodbye",
                event="farewell_suppressed",
            )
            return
        if not spoken_text:
            return
        state = self._state()
        intake = getattr(state, "intake", None)
        farewell = bool(_FAREWELL_RE.search(spoken_text))
        # COMPLETED: q17 is a closing STATEMENT, not a question - once Sakhi
        # has spoken it there is nothing to answer. Record it so the intake
        # reads complete (report, dashboard) and nothing re-arms after it.
        if farewell and intake is not None:
            upcoming = intake.next_question()
            if intake.pending_id == "q17" or (upcoming is not None and upcoming.id == "q17"):
                intake.mark_satisfied("q17", "(closing statement spoken)")
                if intake.pending_id == "q17":
                    intake.pending_id = None
        completed = bool(intake is not None and intake.complete)
        # After COMPLETED, a thank-you is the goodbye (Turn 30: "ಧನ್ಯವಾದಗಳು"
        # after q17 left the call open). Before it, thanks never end a call.
        if not (farewell or (completed and _GRATITUDE_RE.search(spoken_text))):
            return
        # A thank-you mid-screening is not a goodbye (same rule as end_call).
        if end_call_refusal(state) is not None:
            return
        self._farewell_hangup_scheduled = True
        pipeline_event(
            "worker", "ok", "Farewell spoken - auto-ending call",
            "Sakhi spoke her scripted goodbye; room will be deleted after playout",
            event="farewell_auto_hangup",
        )
        try:
            schedule_hangup_after_playout(self.session)
        except Exception:
            logger.warning("Farewell hangup scheduling failed", exc_info=True)

    # --- deterministic clinical safety net -----------------------------

    # --- latent acoustic filler ----------------------------------------

    def _start_latent_filler(self):
        """Speak a short holding phrase if the model has not answered in time.

        Returns a task to cancel, or None when disabled. Never raises: a filler
        that breaks a turn is far worse than a moment of silence.

        Delay increased to 1.2s so it fires AFTER the hedge delay (0.4s) and
        Groq's typical TTFT (~550ms). The filler now only triggers on genuine
        provider outages, not on normal variance.
        """
        if not env_flag("VOICE_LATENT_FILLER_ENABLED", True):
            return None

        delay = float(os.getenv("LATENT_FILLER_DELAY", "1.2"))
        state = self._state()
        language = state.language if state is not None else Language.KANNADA
        phrase = _LATENT_FILLER.get(language, _LATENT_FILLER[Language.KANNADA])

        async def _speak_later() -> None:
            try:
                await asyncio.sleep(delay)
                pipeline_event(
                    "tts", "info", "Latent filler",
                    f"No first token in {delay}s; speaking a holding phrase",
                    event="latent_filler", delay_s=delay,
                )
                _safe_say(self.session, phrase)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Latent filler failed", exc_info=True)

        try:
            return asyncio.create_task(_speak_later())
        except Exception:
            return None

    @staticmethod
    def _cancel_latent_filler(task) -> None:
        if task is not None and not task.done():
            task.cancel()

    def _capture_symptoms(self, state: SakhiCallState, text: str) -> None:
        """Merge symptoms heard in the caller's own words into call state.

        Re-runs triage immediately when a NEW symptom appears and a blood
        pressure is already on record, because that combination is exactly the
        MODERATE -> HIGH_RISK transition: raised BP alone is a 48-hour re-check,
        raised BP plus a danger symptom is a referral. Waiting for the model to
        call record_symptoms before re-assessing is what let the live call miss
        it entirely.
        """
        heard = detect_symptoms(text)
        if not heard.any_present:
            return

        before = state.symptoms
        merged = before.merged_with(heard)
        if merged.to_dict() == before.to_dict():
            return  # nothing new

        state.record_symptoms(heard)
        pipeline_event(
            "tools", "warn", "Symptom heard in speech",
            "Detected from the caller's own words: "
            + ", ".join(heard.present_names),
            event="symptom_autodetected",
            symptoms=list(state.symptoms.present_names),
        )

        # Tier-1 danger signs reach HIGH_RISK alone, with no blood pressure
        # needed at all - so the old "skip re-triage until BP exists" gate
        # below must not apply to them, or a bleeding-only report before BP
        # is ever taken would sit unassessed. Ordinary symptoms (headache/
        # blurry vision/swelling) keep the original gate: alone they can only
        # ever reach OBSERVATION, so skipping the re-triage call without BP
        # is still a safe no-op for them specifically.
        #
        # Reads heard.tier1_alone (sakhi_domain.SymptomSet, 2026-09-10)
        # rather than hand-listing fields here: this exact line used to list
        # only vaginal_bleeding/severe_abdominal_pain, silently missing
        # convulsions - a fits-only report with no BP and no other symptom
        # skipped re-triage entirely, an under-triage gap found while fixing
        # a different bug. See SymptomSet.tier1_alone's own docstring.
        danger_sign_alone = heard.tier1_alone
        if state.blood_pressure is None and not danger_sign_alone:
            return

        engine = getattr(self, "_triage", None) or TRIAGE
        assessment = engine.assess(
            state.blood_pressure, state.symptoms,
            gestational_weeks=state.gestational_weeks,
        )
        previous = state.risk_level
        state.record_assessment(assessment)
        if assessment.level is not previous:
            pipeline_event(
                "tools", "warn", f"Risk re-assessed: {assessment.level.value}",
                f"{previous.value} -> {assessment.level.value} after hearing "
                + ", ".join(heard.present_names),
                event="risk_reassessed",
                risk_level=assessment.level.value,
                requires_escalation=assessment.requires_escalation,
            )

        if assessment.level is RiskLevel.HIGH_RISK and not state.intake_locked:
            state.lock_intake_on_danger_sign(danger_sign_lock_reason(state))
            pipeline_event(
                "tools", "warn", "Intake locked - Tier-1 danger sign",
                f"reason={state.intake_lock_reason}; questionnaire stops here for this call",
                event="intake_locked_danger_sign",
                reason=state.intake_lock_reason,
            )

    def _capture_gestational_age(self, state: SakhiCallState, text: str) -> None:
        """Deterministic safety net for gestational age (2026-09-10).

        Mirrors _capture_symptoms() immediately above: the caller's own
        words ("five months", "ಐದು ತಿಂಗಳು") are scanned independently of
        whether the model correctly converts months to weeks before calling
        record_gestational_age - see sakhi_domain.detect_gestational_age's
        own docstring for the "trapped on q02" failure this closes.

        Deliberately called from the SAME point in on_user_turn_completed as
        _capture_symptoms - i.e. BEFORE and unconditional on the
        identity_pending gate a few lines below, which exists to stop a
        NAME-correction turn misfiling into an unrelated question
        (test_identity_gate.py), not to hold a plainly-stated clinical fact
        hostage until identity happens to resolve. Because
        IntakeProgress.sync_from_state() (called on every prompt_summary())
        auto-satisfies q02 the moment state.gestational_weeks is set,
        setting it here unblocks the checklist too, without touching or
        loosening the identity gate at all.
        """
        if state.gestational_weeks:
            return  # never overwrite a value already recorded
        weeks = detect_gestational_age(text)
        if weeks is None:
            return
        state.identify(gestational_weeks=weeks)
        pipeline_event(
            "tools", "ok", "Gestational age heard in speech",
            f"Detected {weeks} weeks from the caller's own words",
            event="gestational_age_autodetected", weeks=weeks,
        )

    def _escalation_pending(self) -> bool:
        state = self._state()
        return bool(
            state
            and state.assessment is not None
            and state.assessment.requires_escalation
            and not state.escalated
        )

    def _force_escalation(self) -> None:
        """Connect a specialist without waiting for the model to do it.

        Fires only after Sakhi has finished speaking a turn in which triage
        already returned HIGH_RISK and the model did not call the tool. The
        audit trail therefore records the escalation even on the turn the model
        got it wrong, which is the whole point: a red alert must not depend on
        an LLM remembering a prompt instruction.
        """
        state = self._state()
        if state is None or self._toolset is None or self._escalation_in_flight:
            return
        self._escalation_in_flight = True

        async def _run() -> None:
            try:
                reason = "; ".join(state.assessment.reasons) if state.assessment else "high-risk triage"
                pipeline_event(
                    "tools", "warn", "Deterministic escalation",
                    "Triage required escalation and the model did not call the tool; "
                    "connecting the specialist directly",
                    event="forced_escalation",
                    patient_id=state.patient_id,
                    risk_level=state.risk_level.value,
                )
                await self._toolset.connect_smfmi_specialist(
                    _ToolRunContext(self.session, state), reason
                )
                _safe_say(self.session, escalation_notice(state.language))
            except Exception:
                logger.warning("Forced escalation failed", exc_info=True)
            finally:
                self._escalation_in_flight = False

        asyncio.create_task(_run())

    # --- zero-TTS turn loop (2026-11-01) -------------------------------

    def _play_prompt(self, state: SakhiCallState, wav: str) -> bool:
        """Play a pre-recorded prompt on the call's audio track. The text
        goes to the transcript and chat context; no TTS synthesises it."""
        if not AUDIO_BANK.has(wav):
            return False
        self._zt_last_wav = wav
        txt = prompt_text(wav, state.language.value)
        # A recording never passes through the TTS hook that normally tells the
        # adaptive VAD what was asked: tell it here, so a BP or weight question
        # gets the slow window (she is reading the cuff) for the reply.
        self._vad_eval(txt)
        self.session.say(
            txt,
            audio=AUDIO_BANK.stream(wav), allow_interruptions=True, add_to_chat_ctx=True,
        )
        return True

    def _hold_fragment(self, state: SakhiCallState, qid: str, raw_speech: str, turn_id: str) -> None:
        """Keep a cut-off answer ("ಬಿಪಿ 130 ಮತ್ತು") for the next turn instead of
        answering half of it. If no continuation comes, it is processed alone
        after VOICE_FRAGMENT_WAIT_S, exactly as it would have been before."""
        self._drop_fragment()
        self._zt_fragment = (qid, raw_speech)

        def flush() -> None:
            held, self._zt_fragment, self._zt_fragment_timer = self._zt_fragment, None, None
            if held and state.intake.pending_id == held[0]:
                self._zero_tts_turn(state, held[1], turn_id, fragment_ok=False)

        try:
            self._zt_fragment_timer = asyncio.get_running_loop().call_later(
                float(os.getenv("VOICE_FRAGMENT_WAIT_S", "3.0")), flush)
        except RuntimeError:  # no running loop (a synchronous caller): nothing to wait on
            self._zt_fragment_timer = None

    def _drop_fragment(self) -> None:
        timer = getattr(self, "_zt_fragment_timer", None)
        if timer is not None:
            timer.cancel()
        self._zt_fragment = None
        self._zt_fragment_timer = None

    def _zero_tts_turn(self, state: SakhiCallState | None, raw_speech: str, turn_id: str, *, fragment_ok: bool = True) -> bool:
        """Handle one turn with slots and recordings only. True = handled.

        1. strip filler and extract every slot (sakhi_slot_engine.extract_all)
        2. feed the intake (IntakeProgress.ingest_slots): next question, or a
           micro-prompt for the one missing slot
        3. readings into call state; TRIAGE decides. HIGH_RISK halts the
           questionnaire, locks the intake and plays emergency_referral.wav
        4. play the WAV; persistence and escalation run behind it, so the
           first audio frame never waits on the database
        """
        if state is None or not raw_speech or not AUDIO_BANK.ready or state.awaiting_identity_box:
            return False
        lang = state.language.value
        if not _zero_tts_ready(lang):
            return False
        canon = canonicalise(raw_speech)
        if is_side_talk(canon):
            return True                      # she is talking to the mother: stay quiet
        if state.intake_locked:
            # Emergency already declared: the referral is the only thing to say.
            return self._play_prompt(state, localized(EMERGENCY_WAV, lang))
        intake = state.intake
        intake.sync_from_state(state)
        intake.sync_applicability(state)
        if intake.pending_id is None:
            intake.arm_next()                # the greeting asked the first open question
        qid = intake.pending_id
        if qid is None:
            return False                     # intake complete: the closing turn is the pipeline's
        if is_repeat_request(canon):
            last = getattr(self, "_zt_last_wav", None) or localized(
                f"assets/audio/prompts/kn/{qid}_full.wav", lang)
            return self._play_prompt(state, last)
        # Thinking noise ("ಹೂಂ", "uh") while she reads the cuff or the scale:
        # stay quiet, keep the question armed, and burn no stall attempt. A
        # real yes/no to a question that takes one is NOT noise
        # (_answers_pending_question), so a danger-sign "ಹೌದು" is never dropped.
        if is_backchannel(canon) and not _answers_pending_question(
                state, canon, getattr(self, "_last_agent_utterance", None)):
            pipeline_event(
                "turn", "info", "Zero-TTS backchannel ignored",
                f"{len(raw_speech.strip())} chars of thinking noise; question stays armed",
                event="backchannel_ignored", question_id=qid, turn_id=turn_id,
            )
            return True
        # The rest of a cut-off answer: join the held fragment and process both
        # halves together ("ಬಿಪಿ 130 ಮತ್ತು" + "90").
        held = getattr(self, "_zt_fragment", None)
        if held is not None:
            self._drop_fragment()
            if held[0] == qid:
                raw_speech = f"{held[1]} {raw_speech}"
                canon = canonicalise(raw_speech)
        # Cut off mid-thought (ends on "ಮತ್ತು" / "and" / "और"): wait for the rest
        # with the widened listen window instead of answering half a sentence.
        if (
            fragment_ok
            and env_flag("VOICE_FRAGMENT_RECOVERY_ENABLED", True)
            and len(raw_speech.split()) <= 6
            and looks_incomplete(raw_speech)
        ):
            if self._vad_controller is not None:
                self._vad_controller.fragment_recovery()
            self._hold_fragment(state, qid, raw_speech, turn_id)
            pipeline_event(
                "turn", "info", "Zero-TTS fragment recovery",
                f"{len(raw_speech.split())} words ending on a connective; waiting for the rest",
                event="fragment_recovery", question_id=qid, turn_id=turn_id,
            )
            return True

        started = time.perf_counter()
        slots = extract_all(raw_speech, expecting=qid)
        complete, wav = intake.ingest_slots(qid, slots, text=raw_speech, language=lang)

        # Readings into call state (the only inputs triage reads).
        v = intake.slot_values
        new = {}
        if v.get("name") or v.get("age"):
            state.identify(name=v.get("name"), age=v.get("age"))
        if v.get("gestational_weeks") and not state.gestational_weeks:
            state.identify(gestational_weeks=int(v["gestational_weeks"]))
            new["weeks"] = state.gestational_weeks
        if "systolic" in v and "diastolic" in v:
            reading = (int(v["systolic"]), int(v["diastolic"]))
            current = state.blood_pressure
            if current is None or (current.systolic, current.diastolic) != reading:
                try:
                    state.record_blood_pressure(BloodPressure(*reading))
                    new["bp"] = reading
                except BloodPressureError:
                    # Implausible ("90 over 140"): never recorded, asked again whole.
                    v.pop("systolic", None)
                    v.pop("diastolic", None)
                    intake.answered.pop("q06", None)
                    intake.pending_id = "q06"
                    complete, wav = False, localized("assets/audio/prompts/kn/q06_full.wav", lang)
        heard = detect_symptoms(canon, qid)
        if heard.any_present:
            state.record_symptoms(heard)
        if new.get("bp") or heard.any_present:
            engine = getattr(self, "_triage", None) or TRIAGE
            state.record_assessment(engine.assess(
                state.blood_pressure, state.symptoms, gestational_weeks=state.gestational_weeks,
            ))
        emergency = bool(state.assessment and state.assessment.requires_escalation)
        if emergency:
            state.lock_intake_on_danger_sign(danger_sign_lock_reason(state))
            wav = localized(EMERGENCY_WAV, lang)
        q01_done = qid == "q01" and complete and state.name and state.age

        played = bool(wav) and self._play_prompt(state, wav)
        if intake.complete and not emergency:
            self._farewell_hangup_scheduled = True
            try:
                schedule_hangup_after_playout(self.session)
            except Exception:
                logger.warning("Zero-TTS hangup scheduling failed", exc_info=True)
        pipeline_event(
            "turn", "warn" if emergency else "ok",
            "Zero-TTS emergency halt" if emergency else "Zero-TTS turn",
            f"{qid} -> {wav.rsplit('/', 1)[-1] if wav else 'done'}",
            event="zero_tts_turn", turn_id=turn_id, question_id=qid, complete=complete,
            slots=sorted(slots), wav=wav, played=played, emergency=emergency,
            decide_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        self._zero_tts_persist(state, new, heard, bool(q01_done), emergency)
        return True

    def _zero_tts_persist(self, state: SakhiCallState, new: dict, heard, q01_done: bool, emergency: bool) -> None:
        """The database writes the LLM's tool calls used to make, run behind
        the prompt already playing. Same tools, same repository, same audit
        events; a failure is logged and never reaches the call."""
        toolset = self._toolset
        if toolset is None or not (new or heard.any_present or q01_done or emergency):
            return
        ctx = _ToolRunContext(self.session, state)
        found = {f.name: True for f in dataclasses.fields(heard) if getattr(heard, f.name)}

        async def _run() -> None:
            try:
                if q01_done:
                    await toolset.record_patient_intake(ctx, state.name, state.age)
                if "weeks" in new:
                    await toolset.record_gestational_age(ctx, new["weeks"])
                if "bp" in new:
                    await toolset.record_bp(ctx, *new["bp"])
                if found:
                    await toolset.record_symptoms(ctx, **found)
                if emergency and not state.escalated and not self._escalation_in_flight:
                    self._escalation_in_flight = True
                    try:
                        reason = "; ".join(state.assessment.reasons) if state.assessment else "high-risk triage"
                        await toolset.connect_smfmi_specialist(ctx, reason)
                    finally:
                        self._escalation_in_flight = False
            except Exception:
                logger.warning("Zero-TTS persistence failed; call continues", exc_info=True)

        asyncio.create_task(_run())

    # --- framework hooks -----------------------------------------------

    async def on_user_turn_completed(self, turn_ctx, new_message) -> None:
        state = self._state()

        # Protection against duplicate finalized STT utterances. Business
        # logic lives only in this final-turn hook; interim transcript events
        # are telemetry-only in entrypoint().
        raw_speech = (new_message.text_content or "").strip()
        turn_id = self._turn_id(new_message, raw_speech)
        # TABLET_AUDIO_INGRESS: every committed turn, before any gate can drop
        # it. Duration is the last VAD speech burst (ChatMessage carries none).
        # Length only, never the words: this runs before guardrail redaction,
        # and a raw turn holds names, villages and vitals (DPDP Act s.8(5)).
        logger.info(
            "TABLET_AUDIO_INGRESS: chars=%d duration=%.2f turn_id=%s",
            len(raw_speech),
            float(
                getattr(new_message, "duration", None)
                or (getattr(self, "_state_watch", None) or {}).get("last_speech_seconds")
                or 0.0
            ),
            turn_id,
        )

        # [SAKHI_TURN_GATE] - one record per committed turn, at entry, showing
        # what every early-exit gate below WILL decide (evaluated on the same
        # canonicalised text those gates read). A turn that ends in
        # StopResponse() is then attributable to exactly one gate.
        # Read-only: nothing here changes the turn. SAKHI_TURN_GATE_DEBUG=false
        # turns it off.
        if env_flag("SAKHI_TURN_GATE_DEBUG", True):
            try:
                _gate_text = canonicalise(raw_speech) if raw_speech else ""
                _watch = getattr(self, "_state_watch", None) or {}
                _tic_re = globals().get("_VOCAL_TIC_RE")
                _gates = {
                    "is_side_talk": bool(_gate_text) and is_side_talk(_gate_text),
                    "is_repeat_request": bool(_gate_text) and is_repeat_request(_gate_text),
                    "answers_pending_question": _answers_pending_question(
                        state, _gate_text, self._last_agent_utterance
                    ),
                    "is_backchannel": bool(_gate_text) and is_backchannel(_gate_text),
                    # .match(), anchored - the same call is_backchannel() makes
                    "vocal_tic_re": (bool(_tic_re.match(_gate_text.strip())) if _tic_re is not None else None),
                    "looks_incomplete": bool(_gate_text) and looks_incomplete(_gate_text),
                }
                logger.debug("[SAKHI_TURN_GATE] chars=%d gates=%s", len(raw_speech), _gates)
                # No transcript, raw or canonical: this event is written to
                # pipeline_events.jsonl and streamed over SSE, unredacted.
                pipeline_event(
                    "turn", "info", "[SAKHI_TURN_GATE]",
                    f"{len(raw_speech)} chars; gates={[k for k, v in _gates.items() if v]}",
                    event="turn_gate_debug",
                    turn_id=turn_id,
                    raw_transcript_chars=len(raw_speech),
                    canonical_transcript_chars=len(_gate_text),
                    turn_duration_s=round(float(_watch.get("last_speech_seconds") or 0.0), 3),
                    pending_question_id=(state.intake.pending_id if state is not None else None),
                    **_gates,
                )
            except Exception:
                logger.warning("Turn gate debug failed; turn continues", exc_info=True)

        if raw_speech and env_flag("SAKHI_FINAL_TURN_DEDUPE_ENABLED", True):
            now = time.time()
            if turn_id in self._processed_turns:
                pipeline_event(
                    "turn", "warn", "Duplicate transcript ignored",
                    "A finalized utterance id was already committed; dropping duplicate callback",
                    event="duplicate_turn_ignored",
                    turn_id=turn_id,
                )
                raise StopResponse()
            self._processed_turns[turn_id] = now
            if len(self._processed_turns) > 100:
                self._processed_turns = {k: v for k, v in self._processed_turns.items() if now - v < 60}

        # Clear last turn's write receipts before anything can set new ones.
        # Without this the pre-speech guard would let a successful BP write
        # three turns ago authorise an unbacked claim made now - see
        # SakhiCallState.begin_turn().
        if state is not None:
            state.begin_turn()
        # What the CODE decided about this turn's answer; llm_node turns it
        # into the reply instruction so the model never decides on its own
        # that an answer was "not clear" (see _reply_format_instruction).
        self._turn_answer_status = None

        # COMPLETED: the goodbye is already playing and the room closes after
        # it. A courtesy turn ("ಧನ್ಯವಾದಗಳು") must not start another generated
        # reply (live call 2026-09-28 10:54:35 - one more "thank you" before
        # the hangup). A danger sign still gets the full pipeline.
        if (
            getattr(self, "_farewell_hangup_scheduled", False)
            and raw_speech
            and not detect_symptoms(canonicalise(raw_speech)).any_present
        ):
            pipeline_event(
                "turn", "info", "Turn after goodbye ignored",
                "Session COMPLETED; the goodbye is playing and the call is ending",
                event="post_farewell_turn_ignored", turn_id=turn_id,
            )
            raise StopResponse()

        # The ASHA worker is on the line and talking. Set before the
        # backchannel/fragment guards below, which return early: even a grunt
        # proves there is a conversation, which is all this flag claims.
        self._user_has_spoken = True

        if self._vad_controller is not None:
            self._vad_controller.on_user_turn()  # one-shot fragment window consumed

            # Learn how fast THIS worker speaks. Only the silence window is
            # affected - never TTS speed, never temperature. See sakhi_pacing.
            try:
                watch = getattr(self, "_state_watch", None) or {}
                seconds = float(watch.get("last_speech_seconds") or 0.0)
                if seconds > 0:
                    self._vad_controller.observe_user_speech(
                        new_message.text_content or "", seconds
                    )
                    watch["last_speech_seconds"] = 0.0
            except Exception:
                logger.warning("Pacing observation failed; turn continues", exc_info=True)

        # ZERO-TTS DETERMINISTIC TURN (2026-11-01). Slots, not a model, decide
        # what the answer meant and what comes next; a pre-recorded WAV says
        # it. No LLM call and no TTS call on this path. Falls through to the
        # full pipeline below whenever it cannot handle the turn (prompts not
        # recorded for this language, identity box up, English call).
        if env_flag("VOICE_ZERO_TTS_MODE", True) and self._zero_tts_turn(state, raw_speech, turn_id):
            raise StopResponse()

        # Backchannel / vocal-tic filter.
        #
        # A bare "ಹೌದು" or a cough is not a turn. Answering it costs an LLM
        # round trip, restarts TTS, and makes Sakhi feel jumpy to someone who
        # was only signalling that she is still listening. StopResponse ends
        # the turn without generating; `resume_false_interruption` (set in
        # build_turn_handling) resumes whatever Sakhi was saying.
        #
        # Deliberately BEFORE symptom detection and guardrails: a grunt carries
        # neither a symptom nor a safety concern, so nothing is lost by
        # returning early, and the two more expensive passes are skipped.
        # Tier-2 clinical canonicalisation, BEFORE any gate reads the text.
        #
        # Placed here on purpose: every consumer downstream - the assent
        # gate, deterministic symptom detection, the intake tracker, and the
        # LLM itself - then reads the same canonical string. Canonicalising
        # in only some of those places is how two of them end up disagreeing
        # about what the worker actually said. See clinical_terms.py for why
        # this tier is the load-bearing one and STT keyword boosting is not.
        raw_turn_text = new_message.text_content or ""
        if env_flag("SAKHI_CANONICALISE_TRANSCRIPT", True) and raw_turn_text.strip():
            canonical = canonicalise(raw_turn_text)
            if canonical != raw_turn_text:
                # Lengths only: this event is persisted and streamed before
                # guardrail redaction, and the words are the mother's.
                pipeline_event(
                    "stt", "info", "Transcript canonicalised",
                    f"{len(raw_turn_text.strip())} -> {len(canonical.strip())} chars",
                    event="transcript_canonicalised",
                )
                raw_turn_text = canonical
                # Rewrite the message the LLM will see too, so the model is
                # not reasoning over a different string than the gates were.
                try:
                    new_message.content = [canonical]
                except Exception:
                    logger.warning("Could not rewrite turn content post-canonicalisation",
                                   exc_info=True)

        # ANSWER GUARD (2026-09-25): a turn that carries clinical content is
        # never dropped as side-talk or a vocal tic. Tablet transcripts arrive
        # short and noisy; "ಹೂಂ, 28" or "BP 120 80" can look like chatter to a
        # wording-only gate, and dropping it is a StopResponse() with no reply.
        # Content = any digit (BP, age, weeks, after canonicalisation turns
        # number words into digits), a clinical keyword (English list from
        # clinical_terms + the Kannada/Hindi intake words), or an assent.
        _ANSWER_WORDS = (
            # Kannada intake vocabulary
            "ಹೆಸರು", "ವಯಸ್ಸು", "ವರ್ಷ", "ತಿಂಗಳು", "ವಾರ", "ಬಿಪಿ", "ತೂಕ", "ಕೆಜಿ", "ಗರ್ಭ",
            "ರಕ್ತ", "ನೋವು", "ಜ್ವರ", "ಊತ", "ತಲೆನೋವು", "ಸೆಳೆತ", "ಮಗು",
            # Hindi intake vocabulary
            "नाम", "उम्र", "साल", "महीने", "महीना", "हफ्ते", "बीपी", "वजन", "किलो", "गर्भ",
            "खून", "दर्द", "बुखार", "सूजन", "बच्चा",
        )
        # English/romanised words as STT writes them (matched lower-cased,
        # whole word, so "bp" never matches inside another word).
        _ANSWER_WORDS_LATIN_RE = re.compile(
            r"(?i)(?<![a-z])(?:bp|pressure|headache|fever|pain|bleeding|swelling|weeks?|months?|years?|kg)(?![a-z])"
        )
        # Whole-token assents only ("ha" must not match inside "hatti").
        _ASSENT_TOKEN_RE = re.compile(
            r"(?i)(?<![A-Za-z])(?:yes|yeah|ha|haa|haan|han|ok|okay)(?![A-Za-z])"
            r"|ಹೌದು|ಹೂಂ|ಹಾಂ|ಹೂ|हाँ|हां|जी"
        )

        def _carries_answer(text: str) -> bool:
            t = (text or "").strip()
            if not t:
                return False
            if re.search(r"\d", t):
                return True
            low = t.lower()
            if any(word in low for word, _weight in CLINICAL_KEYWORDS):
                return True
            if any(word in t for word in _ANSWER_WORDS):
                return True
            if _ANSWER_WORDS_LATIN_RE.search(t):
                return True
            return is_assent(t) or bool(_ASSENT_TOKEN_RE.search(t))

        carries_answer = _carries_answer(raw_turn_text)

        # The worker turned away to speak to her patient. Stay silent.
        # Before the guardrail gate: an instruction to a patient is not a
        # request to Sakhi, so it should not be classified, logged as a turn,
        # or answered with a script.
        if (
            env_flag("VOICE_SIDE_TALK_GATE_ENABLED", True)
            and is_side_talk(raw_turn_text)
            and not carries_answer
        ):
            pipeline_event(
                "turn", "info", "Side-talk ignored",
                f"{raw_turn_text.strip()[:48]!r} is addressed to the patient, not to Sakhi",
                event="side_talk_ignored",
            )
            raise StopResponse()

        # "Say that again" - re-speak the armed question verbatim rather than
        # letting the model paraphrase a clinical question into a different one.
        if is_repeat_request(raw_turn_text):
            armed = None
            if state is not None and state.intake.pending_id:
                armed = QUESTIONS_BY_ID.get(state.intake.pending_id)
            if armed is not None:
                pipeline_event(
                    "turn", "ok", "Repeat request",
                    f"Re-asking {armed.id} verbatim; no LLM round trip",
                    event="repeat_request", question_id=armed.id,
                )
                _safe_say(self.session, question_text(armed, state.language))
                self._vad_eval(f"[VAD: {armed.vad}]")
                raise StopResponse()
            # Nothing armed yet (e.g. during the greeting) - fall through and
            # let the model handle it rather than inventing a question.

        # THE ASSENT EXEMPTION (2026-09-03) - read this before touching the
        # backchannel gate below.
        #
        # `is_backchannel()` matches "yes", "yeah", "ok", "ಹೌದು", "ಹೂಂ",
        # "हाँ" - the exact words `sakhi_intake._ASSENTS` exists to accept as
        # the real answer to a closed clinical question. Because the gate ran
        # first and raised StopResponse(), the intake tracker never saw them:
        # asking "is she taking the iron tablets?" and being answered "ಹೂಂ"
        # discarded the answer, left the question armed, and Sakhi asked it
        # again. From the ASHA worker's side the agent simply had not heard
        # her - which is exactly what was reported from real calls.
        #
        # The distinction the gate was missing is context, not wording: the
        # SAME word is agreement noise after a statement and a real answer
        # after a question. So the exemption is scoped to precisely that -
        # a closed (FAST) question actually being armed right now, or Sakhi's
        # own last utterance having ended in a question. Nothing else about
        # the backchannel filter changes; a "ಹೂಂ" mid-narration is still
        # correctly swallowed.
        if _answers_pending_question(state, raw_turn_text, self._last_agent_utterance):
            pipeline_event(
                "turn", "ok", "Assent kept as an answer",
                f"{raw_turn_text.strip()[:40]!r} answers the armed question; "
                "not treated as backchannel",
                event="assent_exempted",
                question_id=(state.intake.pending_id if state is not None else None),
            )
        elif is_backchannel(raw_turn_text) and not carries_answer:
            # is_backchannel() is where _VOCAL_TIC_RE applies; the guard means
            # only content-free noise (a cough, "umm") is dropped here.
            pipeline_event(
                "turn", "info", "Backchannel ignored",
                f"{raw_turn_text.strip()[:40]!r} treated as agreement, not a turn",
                event="backchannel_ignored",
            )
            raise StopResponse()

        # Fragment recovery: the caller was cut off mid-thought. Extend the
        # listen window and stay silent instead of answering half a sentence -
        # on a clinical call the missing half is often the important one
        # ("her BP is one forty five and ---").
        #
        # This is what finally wires DynamicVADController.FRAGMENT_RECOVERY,
        # which has been unreachable since the English clinic fragment detector
        # was removed.
        #
        # Only for SHORT turns (<= 3 words): a longer turn that happens to end
        # on a connective has already said something worth answering, and
        # holding it for the extended fragment window reads as Sakhi not
        # responding at all.
        if (
            env_flag("VOICE_FRAGMENT_RECOVERY_ENABLED", True)
            and len(raw_turn_text.split()) <= 3
            and looks_incomplete(raw_turn_text)
        ):
            if self._vad_controller is not None:
                self._vad_controller.fragment_recovery()
            pipeline_event(
                "turn", "info", "Incomplete fragment",
                f"{raw_turn_text.strip()[-40:]!r} ends on a connective; waiting for the rest",
                event="fragment_recovery",
            )
            raise StopResponse()

        # Snapshot the armed item only after every early conversational gate.
        # record_answer() clears pending_id later, so this is the stable link
        # between the validated write and the post-guardrail latency router.
        deterministic_armed_question = (
            state.intake.pending_id if state is not None else None
        )

        # Deterministic symptom capture, before the LLM gets a say.
        #
        # Observed live: the worker reported a severe headache in her opening
        # sentence, the model then called record_symptoms(false, false, false),
        # and triage stayed MODERATE when it should have been HIGH_RISK. Symptom
        # capture must not depend on the model passing the right booleans.
        #
        # SymptomSet.merged_with() only ORs, so a later `false` from the model
        # cannot erase what the mother actually reported.
        if state is not None:
            try:
                self._capture_symptoms(state, new_message.text_content or "")
            except Exception:
                logger.warning("Symptom detection failed; turn continues", exc_info=True)

            # Deterministic gestational-age capture (2026-09-10), same
            # reasoning as symptom capture immediately above: must not
            # depend on the model correctly converting "five months" to
            # weeks=20 itself. See _capture_gestational_age's own docstring
            # for the "trapped on q02" failure this closes.
            try:
                self._capture_gestational_age(state, new_message.text_content or "")
            except Exception:
                logger.warning("Gestational age detection failed; turn continues", exc_info=True)

            # LANGUAGE LATCH (2026-09-02).
            #
            # The call language moves ONLY on an explicit request. Everything
            # else - code-switching ("BP", "tablet", "scan" inside a Kannada
            # sentence), an accent, a garbled transcript - leaves it alone.
            # This is the whole mechanism behind the hard constraint that an
            # English caller receives zero Kannada tokens: nothing in the
            # per-turn path can move the latch except the detector below.
            #
            # The switch is recorded in state and reported; the providers
            # themselves are NOT rebuilt mid-call. Swapping STT/TTS instances
            # under a live AgentSession is not something this pipeline
            # supports safely, and a half-swapped session is worse than a
            # consistent one - so the prompt-level latch carries the change
            # for the rest of THIS call, and the next call is dispatched in
            # the requested language.
            try:
                requested = detect_language_switch_request(new_message.text_content or "")
                if requested is not None and spoken_language(requested) != requested:
                    pipeline_event(
                        "turn", "info", "Language switch ignored",
                        f"{requested.display_name} is not enabled; staying in {state.language.display_name}",
                        event="language_switch_blocked", language=requested.value,
                    )
                    requested = None
                if requested is not None and state.switch_language(requested):
                    pipeline_event(
                        "turn", "warn", "Language switch requested",
                        f"ASHA worker explicitly asked for {requested.display_name}; "
                        "latch moved for the remainder of this call",
                        event="language_switched",
                        language=requested.value,
                        switches=state.language_switches,
                    )
            except Exception:
                logger.warning("Language-switch detection failed; turn continues", exc_info=True)

            # Close out the intake question that was armed for this turn.
            #
            # Placed AFTER the backchannel and fragment-recovery guards above,
            # which raise StopResponse: a grunt or a half-sentence must not tick
            # a clinical question off the checklist. Only a turn substantive
            # enough to be worth answering counts as an answer.
            #
            # BUT: while identity is still unresolved past q01, a turn is
            # answering the SPELLING/CLARIFICATION sub-dialogue, not whatever
            # numbered question happens to still be armed underneath it.
            # Confirmed live: "her name is Anjaneya" got heard as "Anjali" by
            # STT, the lookup failed, Sakhi asked her to spell it - and the
            # spelled-out reply ("Anjali A N J A L I") and a stray "it's her
            # name" both landed as the recorded answer to q03 ("when did her
            # symptoms first start"), because the intake tracker has no
            # concept of "we are mid-identity-resolution, nothing here counts
            # toward the questionnaire". That is the exact mechanism behind
            # "hallucinating... telling something else" - not the LLM
            # inventing facts, but the wrong fact landing in the wrong slot.
            # is_identified covers both the real-lookup and the Guest-Patient
            # ceiling-fallback paths (both go through state.identify()).
            #
            # awaiting_identity_box is the same class of gap, one layer newer:
            # while the frontend name/age text box is up, she's been told to
            # type it, not say anything - but nothing stopped a stray spoken
            # turn ("ok, typing now") from being recorded as the answer to
            # whatever question was still armed. Harmless functionally
            # (sync_from_state() re-satisfies q01 once the box resolves
            # regardless), but it leaves a bogus value as the permanent
            # audit-trail answer, since mark_satisfied() only sets a question
            # once and won't overwrite an existing entry.
            identity_pending = (
                state.intake.pending_id not in (None, "q01") and not state.is_identified
            ) or state.awaiting_identity_box
            _flow_transcript = (new_message.text_content or "").strip()
            # "COMPLETE", not "q01", once every question is closed: the old
            # fallback made post-q17 turns log as "q01 -> COMPLETE", which read
            # as the questionnaire restarting. (Nothing re-arms after the
            # close - arm_next() returns None.)
            _flow_next = state.intake.next_question()
            _flow_prev_q = state.intake.pending_id or (_flow_next.id if _flow_next else "COMPLETE")
            _pending_q_obj = QUESTIONS_BY_ID.get(_flow_prev_q)
            _vad = _pending_q_obj.vad if _pending_q_obj else "SLOW"
            _is_valid, _val_reason = state.intake.validate_answer(_flow_transcript, vad=_vad)

            try:
                # Extract typed facts first, including useful information the
                # caller volunteered before its question was asked. sync then
                # marks those exact questions satisfied; we do not increment
                # a question index blindly.
                captured_fields = self._capture_incident_fields(state, raw_turn_text)
                state.intake.sync_from_state(state)
                state.intake.sync_applicability(state)

                pending_was_satisfied = bool(
                    deterministic_armed_question
                    and deterministic_armed_question in state.intake.answered
                )
                matches_pending = self._pending_answer_matches(
                    state, deterministic_armed_question, raw_turn_text, captured_fields
                )
                closed = None
                if pending_was_satisfied:
                    closed = deterministic_armed_question
                    if state.intake.pending_id == deterministic_armed_question:
                        state.intake.pending_id = None
                elif not identity_pending and matches_pending:
                    closed = state.intake.record_answer(raw_turn_text)
                if closed:
                    pipeline_event(
                        "llm", "info", "Intake answer recorded",
                        f"{closed} answered ({len(state.intake.answered)}/{len(INTAKE_QUESTIONS)})",
                        event="intake_answered", question_id=closed,
                        answered=len(state.intake.answered),
                    )
                elif deterministic_armed_question in {"q01", "q02", "q04", "q06"} and not matches_pending:
                    pipeline_event(
                        "turn", "info", "Answer did not match armed field",
                        f"{deterministic_armed_question} remains armed; typed validation did not resolve it",
                        event="intake_answer_needs_clarification",
                        question_id=deterministic_armed_question,
                        captured_field_names=sorted(captured_fields),
                    )
                if deterministic_armed_question and not identity_pending:
                    if closed:
                        self._turn_answer_status = "accepted"
                    elif deterministic_armed_question == "q06" and _BP_READING_RE.search(raw_turn_text or ""):
                        self._turn_answer_status = "value_to_record"
                    else:
                        self._turn_answer_status = "not_answered"
                # Immediately sync facts auto-satisfied by tools or extractors.
                state.intake.sync_from_state(state)
                state.intake.sync_applicability(state)
            except Exception:
                logger.warning("Intake progress update failed; turn continues", exc_info=True)

            # [SAKHI_FLOW] per-turn structured trace (2026-09-22).
            #
            # One log line per turn that shows the complete pipeline from STT
            # transcript through state transition, so regressions like the
            # "yelu tingalu" stall are diagnosable from production logs without
            # requiring a re-run. Does NOT change any clinical behaviour.
            try:
                import hashlib as _hashlib
                _flow_next_q = state.intake.next_question()
                _next_qid = _flow_next_q.id if _flow_next_q else "COMPLETE"
                _flow_retry = state.intake._stall_counts.get(_flow_prev_q or "", 0)
                _raw_fingerprint = _hashlib.sha256(_flow_transcript.encode("utf-8")).hexdigest()[:12]
                pipeline_event(
                    "turn", "info", "[SAKHI_TURN]",
                    f"{_flow_prev_q} -> {_next_qid}; structured fields={sorted(captured_fields)}",
                    event="sakhi_flow",
                    session_id=state.session_id,
                    turn_id=turn_id,
                    raw_transcript_sha256=_raw_fingerprint,
                    raw_transcript_chars=len(_flow_transcript),
                    current_question_id=_flow_prev_q,
                    extracted_field_names=sorted(captured_fields),
                    extracted_values={k: v for k, v in captured_fields.items() if k != "name"},
                    validation_result=(
                        "NO_ARMED_QUESTION" if deterministic_armed_question is None
                        else "VALID" if matches_pending else "NEEDS_CLARIFICATION"
                    ),
                    validation_reason=_val_reason,
                    typed_validation_reason=(
                        state.intake.validate_answer(
                            raw_turn_text, vad=_vad, question_id=deterministic_armed_question
                        )[1] if deterministic_armed_question else None
                    ),
                    answer_status=getattr(self, "_turn_answer_status", None),
                    risk_level=(state.assessment.level.value if state.assessment else None),
                    intake_locked=state.intake_locked,
                    answer_saved=bool(closed),
                    retry_count=_flow_retry,
                    completed_question_ids=sorted(state.intake.answered.keys()),
                    next_question_id=(_flow_next_q.id if _flow_next_q else None),
                    identity_pending=identity_pending,
                )
            except Exception:
                logger.warning("Structured turn trace failed; turn continues", exc_info=True)


        # Safety guardrails run FIRST, on their own flag, never gated by a
        # latency toggle. A medical/emergency/jailbreak/abuse turn is answered
        # with a fixed approved script and StopResponse, so the LLM never
        # receives it and cannot hallucinate an answer to it.
        if not env_flag("VOICE_GUARDRAILS_ENABLED", True):
            return

        # Dual-layer gate. sanitize_input() redacts PII FIRST - before the text
        # is logged or sent to a US-hosted model - then classifies. It covers
        # what classify_turn alone did not: medication requests in Kannada and
        # Hindi (an English-only medication guard is decorative on an agent that
        # speaks only Kannada), dosage phrasing, and prompt injection.
        language = state.language if state is not None else Language.KANNADA
        try:
            decision = guardrails.GUARDRAILS.sanitize_input(
                new_message.text_content or "", language=language.value
            )
        except Exception:
            logger.warning("Guardrail gate failed; turn continues normally", exc_info=True)
            return

        # Overwrite the message the LLM will see with the redacted text, so an
        # identifier the caller spoke never reaches the model or the transcript.
        if decision.pii_kinds:
            try:
                new_message.content = [decision.text]
            except Exception:
                logger.warning("Could not replace message with redacted text", exc_info=True)
            pipeline_event(
                "llm", "warn", "PII redacted from turn",
                "Identifiers masked before the LLM and before logging: "
                + ", ".join(decision.pii_kinds),
                event="pii_redacted", kinds=list(decision.pii_kinds),
            )

        category = decision.category
        if category is None:
            self._route_deterministic_intake(
                state, deterministic_armed_question, raw_turn_text
            )
            return

        if category == "abuse":
            strikes = 1
            if state is not None:
                state.abuse_strikes += 1
                strikes = state.abuse_strikes
            pipeline_event(
                "llm", "warn", "Guardrail: abusive language",
                f"Strike {strikes} - {'warning issued' if strikes < 2 else 'ending the call'}",
                event="guardrail_triggered", category="abuse", strikes=strikes,
            )
            if strikes >= 2:
                _safe_say(self.session, guardrails.script_for("abuse_goodbye", language.value))
                schedule_hangup_after_playout(self.session)
            else:
                _safe_say(self.session, _then_current_question(
                    guardrails.script_for("abuse_warning", language.value), state))
            raise StopResponse()

        # Zero database tools run on this path: StopResponse aborts the turn
        # before the LLM is invoked, so no tool call can be generated.
        script = decision.script or guardrails.SCRIPTS.get(category, "")
        pipeline_event(
            "llm", "warn", f"Guardrail: {category}",
            "Turn answered with the approved script; the LLM never saw it",
            event="guardrail_triggered", category=category,
            response_path="deterministic", language=language.value,
        )
        if category in ("jailbreak", "medical_advice"):
            # Back to the checklist, not to a question of the script's own.
            # Emergency / self-harm scripts stand alone on purpose.
            script = _then_current_question(script, state)
        _safe_say(self.session, script)
        self._vad_eval(script)
        raise StopResponse()

    async def llm_node(self, chat_ctx, tools, model_settings):
        """Inject per-turn ground truth, then guard against empty completions."""
        # The clinical state is injected fresh on EVERY generation rather than
        # once at call start, so the model is told what has already been
        # collected instead of having to infer it from a long, sometimes-garbled
        # Kannada transcript. This is what stops the re-ask-the-BP bug.
        state = self._state()
        # Inactive unless an intake question is armed below - see
        # sakhi_question_lock for why the code, not the model, asks it.
        lock = QuestionLock(None)
        if state is not None:
            try:
                chat_ctx = chat_ctx.copy()

                # Sliding context window. Without it the transcript grows for
                # the whole call and every turn re-sends it, so prefill (and
                # therefore TTFT) climbs steadily the longer an ASHA worker
                # talks - exactly backwards for a screening call that gets more
                # urgent as it goes.
                #
                # ChatContext.truncate() is used rather than slicing items by
                # hand because it already drops a leading function_call or
                # function_call_output. Cutting mid tool-exchange leaves an
                # orphaned tool result whose originating call is gone, and the
                # OpenAI-compatible API rejects that outright - the turn dies.
                #
                # The budget is in ITEMS, not turns: one "turn" can be a user
                # message, an assistant message, a function_call and a
                # function_call_output. 12 items ~= the last 3 turns with tool
                # use, which is what the workflow needs (the mother's details
                # live in the state block below, not in the transcript).
                max_items = int(os.getenv("LLM_CONTEXT_MAX_ITEMS", "8"))
                max_tokens = int(os.getenv("LLM_CONTEXT_MAX_TOKENS", "500"))
                before_items = len(chat_ctx.items)
                before_tokens = _approx_context_tokens(chat_ctx)

                # Item count alone does not bound cost. A live call held 13
                # items and still sent ~2,400 tokens, because one Kannada STT
                # transcript item can be enormous. Tighten the item budget until
                # the token estimate fits, so prefill stays bounded no matter
                # how long the caller's sentences are.
                # THE SYSTEM PROMPT IS NOT PRUNABLE.
                #
                # `truncate` drops the OLDEST items, and the system prompt is
                # the oldest item there is. With a 700-token budget and a
                # ~2,300-token clinical prompt, the loop below could only ever
                # satisfy itself by deleting the prompt: a live call pruned
                # 6 items/~2,324 tok down to 5 items/~92 tok, and from that
                # turn on Sakhi had no persona, no Kannada instruction, no
                # intake flow control and no tool guidance. She was still
                # "working", just no longer Sakhi.
                #
                # So the budget applies to the CONVERSATION only. The prompt is
                # fixed, known, and the same on every turn - it is not what
                # makes context grow without bound; long STT transcripts are.
                system_items = [
                    item for item in chat_ctx.items
                    if getattr(item, "role", None) == "system"
                ]
                conversation = [
                    item for item in chat_ctx.items
                    if getattr(item, "role", None) != "system"
                ]

                if env_flag("SAKHI_INCREMENTAL_CONTEXT_PRUNING_ENABLED", True):
                    # Measured long-call contexts made the old copy+rescan loop
                    # O(n²) immediately before every LLM request. This helper
                    # computes each item's estimate once and subtracts as the
                    # deque advances; disable to restore the exact legacy loop.
                    conversation = _prune_conversation_incrementally(
                        conversation, max_items, max_tokens
                    )
                else:
                    def _conversation_tokens() -> int:
                        probe = chat_ctx.copy()
                        probe.items = list(conversation)
                        return _approx_context_tokens(probe)

                    while conversation and (
                        len(conversation) > max_items
                        or _conversation_tokens() > max_tokens
                    ):
                        conversation.pop(0)  # oldest turn first

                # Synchronize intake state with current call state before stage calculation
                state.intake.sync_from_state(state)
                state.intake.sync_applicability(state)

                if env_flag("SAKHI_LEAN_INTAKE_MODE", True) and system_items:
                    intake = getattr(state, "intake", None)
                    current_st = intake.current_stage() if intake and hasattr(intake, "current_stage") else 1
                    nxt_q = intake.next_question() if intake else None
                    nxt_text = question_text(nxt_q, state.language) if nxt_q else None
                    lean_prompt = system_prompt_for(
                        state.language,
                        stage=current_st,
                        stage_desc=stage_name(current_st),
                        stage_q=nxt_text,
                    )
                    first_sys = system_items[0]
                    if hasattr(first_sys, "content"):
                        first_sys.content = [lean_prompt]

                chat_ctx.items = [*system_items, *conversation]

                if len(chat_ctx.items) != before_items:
                    pipeline_event(
                        "llm", "info", "Context pruned",
                        f"{before_items} items/~{before_tokens} tok -> "
                        f"{len(chat_ctx.items)} items/~{_approx_context_tokens(chat_ctx)} tok",
                        event="context_pruned",
                        items_before=before_items, items_after=len(chat_ctx.items),
                        tokens_before=before_tokens,
                        tokens_after=_approx_context_tokens(chat_ctx),
                    )

                # Ground truth: always inject current state summary so the model
                # knows what is already recorded and never re-asks completed questions.
                chat_ctx.add_message(
                    role="system",
                    content=(
                        "# CURRENT CALL STATE (ground truth - never ask again for "
                        f"anything already recorded below)\n{state.prompt_summary()}"
                    ),
                )

                # Re-scope the tool surface to this phase of the call.
                if self._toolset is not None and env_flag("SAKHI_SCOPED_TOOLS", True):
                    try:
                        if env_flag("SAKHI_LEAN_INTAKE_MODE", True):
                            intake = getattr(state, "intake", None)
                            current_st = intake.current_stage() if intake and hasattr(intake, "current_stage") else 1
                            stage_tools = lean_stage_tools(
                                self._toolset, current_st,
                                escalation_pending=self._escalation_pending(),
                            )
                            # end_call stays offered (a tool missing from the
                            # request fails the whole request on Groq - see
                            # lean_stage_tools); it refuses by itself while
                            # questions remain - see end_call.
                            scoped = [*stage_tools, end_call]
                        else:
                            scoped = self._toolset.tools_for(state)
                            clinical = {_tool_name(t) for t in self._toolset.as_list()}
                            extras = [
                                tool for tool in (self.tools or [])
                                if _tool_name(tool) not in clinical
                            ]
                            scoped = [*scoped, *extras]
                        current = {_tool_name(t) for t in (self.tools or [])}
                        wanted = {_tool_name(t) for t in scoped}
                        if current != wanted:
                            await self.update_tools(scoped)
                            pipeline_event(
                                "tools", "info", "Tool surface re-scoped",
                                ", ".join(sorted(wanted)),
                                event="tools_scoped", tools=sorted(wanted),
                            )
                            # Should not happen in lean mode any more (the
                            # set is constant from construction). Do NOT
                            # swap `tools` for this generation: livekit runs
                            # tool calls against the list it captured, so
                            # the model would call tools that "don't exist".
                    except Exception:
                        # Never let scoping break a live turn - a wider surface
                        # is a lesser failure than a dead turn.
                        logger.warning("Tool re-scoping failed; keeping the current set",
                                       exc_info=True)
                # Arm the question we just told the model to ask, so the ASHA
                # worker's next turn can be attributed to it and the checklist
                # advances deterministically instead of the model re-deriving
                # its position from the transcript every turn.
                # Emergency lock: no routine question is armed or appended for
                # the rest of the call - _escalation_pending() alone turned
                # false again once the specialist was connected, and the
                # checklist resumed between hospital instructions.
                armed = state.intake.arm_next() if questionnaire_open(state) else None
                if armed is not None:
                    pipeline_event(
                        "llm", "info", "Intake question armed",
                        f"{armed.id} ({len(state.intake.answered)}/{len(INTAKE_QUESTIONS)}): {armed.text}",
                        event="intake_armed", question_id=armed.id,
                        answered=len(state.intake.answered),
                    )
                    if (
                        question_lock_enabled()
                        and not self._escalation_pending()
                        # Only where written wording exists (not English yet):
                        # the lock must never speak Kannada on another call.
                        and has_localised_text(armed, state.language)
                    ):
                        lock = QuestionLock(
                            armed_question_text(armed, state, state.language),
                            armed.vad,
                            # Every question, answered ones included: the model
                            # re-stating an answered question ("माता का नाम और
                            # उम्र बताइए" after the name was saved) must be
                            # dropped too. Safe for value read-backs because
                            # the match is measured against the QUESTION.
                            tuple(
                                question_text(q, state.language) for q in INTAKE_QUESTIONS
                                if q.id != armed.id
                            ),
                        )
                        chat_ctx.add_message(
                            role="system",
                            content=_reply_format_instruction(
                                getattr(self, "_turn_answer_status", None)
                            ),
                        )
                if self._escalation_pending():
                    # Deliberately blunt and placed last, so it is the most
                    # recent instruction the model sees.
                    chat_ctx.add_message(
                        role="system",
                        content=(
                            "# URGENT: triage has classified this mother HIGH_RISK. "
                            "Reassure the ASHA worker calmly and call "
                            "connect_smfmi_specialist in THIS turn. Do not ask "
                            "further screening questions first."
                        ),
                    )
            except Exception:
                pass  # never let state injection break a live turn

        # Latent acoustic filler.
        #
        # Measured TTFT here is ~0.4-0.6s, but the tail reaches 2.2s and a tool
        # round trip stacks on top. Silence in that gap reads as a dropped call
        # on a rural line, and the ASHA worker starts repeating herself - which
        # arrives as a fresh turn and makes it worse. The threshold sits above
        # p50 on purpose, so this fires on the tail only, not every turn.
        filler_task = self._start_latent_filler()

        text_length = 0
        tool_calls_seen = False
        try:
            async for chunk in Agent.default.llm_node(self, chat_ctx, tools, model_settings):
                # First sign of life cancels the filler - if it has not spoken yet
                # it never will, and the caller hears the real answer instead.
                self._cancel_latent_filler(filler_task)
                delta = getattr(chunk, "delta", None)
                content = getattr(delta, "content", None) if delta is not None else None
                if isinstance(chunk, str):
                    content = chunk
                if content:
                    text_length += len(content)
                if delta is not None and getattr(delta, "tool_calls", None):
                    tool_calls_seen = True
                if lock.active and not (delta is not None and getattr(delta, "tool_calls", None)):
                    # Text goes through the lock; tool-call chunks pass untouched.
                    for sentence in lock.feed(content or ""):
                        yield sentence
                    continue
                yield chunk
            if lock.active:
                for sentence in lock.finish(final=not tool_calls_seen):
                    text_length += len(sentence)
                    yield sentence
                if lock.dropped:
                    pipeline_event(
                        "llm", "warn", "Question lock",
                        f"{len(lock.dropped)} model sentence(s) replaced by the armed question",
                        event="question_lock_dropped",
                        question_id=state.intake.pending_id if state is not None else None,
                        dropped=lock.dropped[:3],
                    )
        except Exception as exc:
            self._cancel_latent_filler(filler_task)
            pipeline_event(
                "llm", "error", "LLM fallback chain error",
                f"Primary fallback adapter error ({type(exc).__name__}: {exc}); engaging emergency turn repair",
                event="llm_fallback_chain_failed",
                error_type=type(exc).__name__,
                error=str(exc),
            )

        # A tool-only completion is a VALID model response (the tool loop
        # continues this same logical turn) - it must never be treated as an
        # empty failure, and must never trigger the fallback.
        if text_length:
            # A normal post-tool continuation won the race. Discard the
            # deterministic backup so it cannot leak into a later turn and be
            # spoken twice.
            if self._toolset is not None:
                self._toolset.last_spoken_prompt = ""
            validated_label, validated_message = "LLM text ready for TTS", "Visible assistant content validated"
        elif tool_calls_seen:
            validated_label, validated_message = "Tool-only LLM completion", "Model returned tool calls; tool loop continues this turn"
        else:
            validated_label, validated_message = "Empty LLM completion", "No visible assistant content and no tool calls were produced"
        pipeline_event(
            "llm",
            "ok" if text_length or tool_calls_seen else "warn",
            validated_label,
            validated_message,
            event="llm_output_validated",
            text_present=bool(text_length),
            text_length=text_length,
            tool_calls_present=tool_calls_seen,
        )
        if text_length or tool_calls_seen:
            return

        # FIX THE SILENT TOOL COMPLETION LOOP:
        fallback_prompt = getattr(self._toolset, "last_spoken_prompt", "")
        if fallback_prompt:
            self._toolset.last_spoken_prompt = ""
            logger.info(f"[AGENT WATCHDOG] Forcing audio playback for tool response: {fallback_prompt}")
            pipeline_event(
                "tts", "info", "Watchdog speech in llm_node",
                f"[AGENT WATCHDOG] Forcing audio playback for tool response: {fallback_prompt}",
                event="agent_watchdog_speech",
                spoken_text=fallback_prompt,
            )
            yield fallback_prompt
            return

        # A successful-but-empty completion (or unhandled tier failure) cannot be retried by LiveKit's
        # provider adapter, so ask an emergency backup model explicitly for this turn.
        openrouter_keys = openrouter_api_keys()
        groq_keys = groq_api_keys()
        openai_key = os.getenv("OPENAI_API_KEY")

        fallback = None
        fallback_provider = "none"

        if openrouter_keys:
            fallback = openai.LLM(
                model=os.getenv("OPENROUTER_BACKUP_MODEL", "meta-llama/llama-3.1-8b-instruct"),
                api_key=openrouter_keys[0],
                base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
                max_completion_tokens=int(os.getenv("LLM_MAX_COMPLETION_TOKENS", "200")),
                temperature=float(os.getenv("LLM_TEMPERATURE", LLM_CONFIG["temperature"])),
                top_p=float(os.getenv("LLM_TOP_P", LLM_CONFIG["top_p"])),
                max_retries=0,
            )
            fallback_provider = "openrouter"
        elif groq_keys:
            fallback = openai.LLM(
                model=os.getenv("GROQ_REPAIR_MODEL", "openai/gpt-oss-20b"),
                api_key=groq_keys[0],
                base_url=os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
                max_completion_tokens=int(os.getenv("LLM_MAX_COMPLETION_TOKENS", "200")),
                temperature=float(os.getenv("LLM_TEMPERATURE", LLM_CONFIG["temperature"])),
                top_p=float(os.getenv("LLM_TOP_P", LLM_CONFIG["top_p"])),
                reasoning_effort="low",
                max_retries=0,
            )
            fallback_provider = "groq"
        elif openai_key:
            fallback = openai.LLM(
                model=os.getenv("OPENAI_LLM_MODEL", "gpt-4o-mini"),
                api_key=openai_key,
                max_completion_tokens=int(os.getenv("LLM_MAX_COMPLETION_TOKENS", "200")),
                temperature=float(os.getenv("LLM_TEMPERATURE", LLM_CONFIG["temperature"])),
                top_p=float(os.getenv("LLM_TOP_P", LLM_CONFIG["top_p"])),
                max_retries=0,
            )
            fallback_provider = "openai"

        if fallback is None:
            yield retry_prompt_for(self._state.language)
            return

        fallback_text_length = 0
        fallback_tool_calls = False
        tool_choice = model_settings.tool_choice if model_settings else NOT_GIVEN
        conn_options = self.session.conn_options.llm_conn_options
        try:
            async with fallback.chat(
                chat_ctx=chat_ctx,
                tools=tools,
                tool_choice=tool_choice,
                conn_options=conn_options,
            ) as stream:
                async for chunk in stream:
                    delta = getattr(chunk, "delta", None)
                    content = getattr(delta, "content", None) if delta is not None else None
                    if content:
                        fallback_text_length += len(content)
                    if delta is not None and getattr(delta, "tool_calls", None):
                        fallback_tool_calls = True
                    yield chunk
        except Exception as exc:
            pipeline_event(
                "llm", "error", "Emergency repair fallback failed",
                f"{fallback_provider} repair model failed after an empty primary completion",
                event="emergency_repair_failed",
                provider=fallback_provider, error_type=type(exc).__name__, error=str(exc),
            )
        finally:
            await fallback.aclose()

        pipeline_event(
            "llm",
            "ok" if fallback_text_length or fallback_tool_calls else "warn",
            "Emergency repair fallback completed",
            f"{fallback_provider} repair model produced a replacement completion" if fallback_text_length else f"{fallback_provider} repair model produced no visible replacement text",
            event="emergency_repair_completed",
            provider=fallback_provider,
            text_present=bool(fallback_text_length),
            text_length=fallback_text_length,
            tool_calls_present=fallback_tool_calls,
        )
        if not fallback_text_length and not fallback_tool_calls:
            yield _RETRY_PROMPT

    async def tts_node(self, text, model_settings):
        """Observe the LLM-to-TTS handoff without buffering the speech stream.

        For providers that don't stream natively, chunk at clause boundaries
        rather than only full sentences (see EagerClauseTokenizer). Sarvam
        streams natively and is left on the framework default.
        """
        async def observed_text():
            characters = 0
            logged = False
            full_text_parts: list[str] = []
            # Pre-speech correctness guard state. `pending` holds only the
            # CURRENT unfinished sentence - never the whole reply - so the
            # added latency is one sentence boundary, the same granularity
            # TTS already synthesises at. See claims_completed_write().
            guard_on = env_flag("SAKHI_PRESPEECH_GUARD_ENABLED", True)
            split_re = (
                _CLAUSE_BREAK_RE
                if env_flag("SAKHI_PRESPEECH_GUARD_CLAUSE_LEVEL", True)
                else _SENTENCE_END_RE
            )
            state = self._state()
            pending = ""

            def _vet(sentence: str) -> str:
                """Return the sentence to speak - the original, or a bridge."""
                reason = unbacked_claim_reason(sentence, state)
                if reason is None:
                    return sentence
                bridge = bridge_line_for(getattr(state, "language", None))
                # Record it for the NEXT turn's prompt, so the model is told
                # to actually call the tool instead of repeating the claim.
                try:
                    state.unbacked_claim_blocked = reason
                except Exception:
                    pass
                pipeline_event(
                    "tts", "warn", "Unbacked claim blocked",
                    f"{sentence.strip()[:70]!r} claimed a {reason} write that did "
                    "not happen this turn; replaced with an honest bridge",
                    event="unbacked_claim_blocked", claim_type=reason,
                )
                return bridge

            async for chunk in text:
                if chunk:
                    characters += len(chunk)
                    if not logged:
                        logged = True
                        pipeline_event(
                            "tts", "info", "TTS input received",
                            "Speakable assistant text reached TTS",
                            event="tts_input_received", characters_count=len(chunk),
                        )
                if not guard_on or state is None:
                    clean = sanitize_text(chunk) if chunk else ""
                    if clean:
                        full_text_parts.append(clean)
                        yield clean
                    continue
                # Accumulate to the selected sentence/clause boundary, vet,
                # release. Clause mode moves first audio ~200ms earlier on the
                # measured Sarvam path; the kill-switch keeps the old sentence
                # behavior available without weakening _vet().
                pending += chunk or ""
                while True:
                    match = split_re.search(pending)
                    if not match:
                        break
                    sentence, pending = pending[: match.end()], pending[match.end():]
                    # sanitize_text() ends in .strip(), which eats the
                    # trailing space _SENTENCE_END_RE's own match just
                    # captured (and the one bridge_line_for() deliberately
                    # appends). Each sentence is yielded and later joined
                    # with NO separator - lose that space here and the next
                    # sentence fuses onto this one with no gap at all before
                    # it ever reaches the synthesizer ("...now.Please tell
                    # me...", confirmed live 2026-09-04: one straight run-on
                    # word boundary in the actual TTS input text, not just
                    # the log line - see bridge_line_for()'s own docstring,
                    # which already names this exact failure mode).
                    vetted = sanitize_text(_vet(sentence))
                    if vetted and sentence.endswith((" ", "\n", "\t")):
                        vetted += " "
                    if vetted:
                        full_text_parts.append(vetted)
                        yield vetted

            if guard_on and state is not None and pending:
                tail = sanitize_text(_vet(pending) if pending.strip() else pending)
                if tail:
                    full_text_parts.append(tail)
                    yield tail
            if not logged:
                pipeline_event(
                    "tts", "warn", "TTS input empty",
                    "TTS node completed without speakable text",
                    event="tts_input_empty", characters_count=characters,
                )
            spoken = "".join(full_text_parts)
            # Record what Sakhi actually said.
            #
            # Only characters_count was logged before, so the transcript held
            # the ASHA worker's words but never Sakhi's. On a live call that
            # made it impossible to tell whether a piece of medical advice came
            # from the agent or from the caller - the exact question a clinical
            # audit has to answer. Truncated because a reply is 1-2 sentences.
            if spoken.strip():
                safe_spoken, _spoken_pii = guardrails.redact_pii(spoken.strip())
                pipeline_event(
                    "tts", "ok", "Sakhi said",
                    safe_spoken[:400],
                    event="agent_speech", characters_count=len(spoken),
                    pii_redacted=bool(_spoken_pii),
                )
            # Remember what Sakhi just said. The turn gate reads this to tell
            # a one-word ANSWER ("ಹೂಂ" after a question) from one-word
            # AGREEMENT NOISE ("ಹೂಂ" mid-explanation) - see
            # _answers_pending_question(). Stored before the guards below so
            # a failure in any of them cannot leave it stale.
            if spoken.strip():
                self._last_agent_utterance = spoken.strip()

            # Adaptive VAD: what Sakhi just said sets how long we wait for the
            # ASHA worker's NEXT turn (open/recall questions get a longer
            # window, closed questions stay fast).
            self._vad_eval(spoken)
            # Deterministic session end on a scripted farewell.
            self._maybe_schedule_farewell_hangup(spoken)
            # Deterministic clinical safety net: the turn is over and triage
            # still wants a specialist that the model never connected.
            if self._escalation_pending():
                self._force_escalation()

        activity = self._get_activity_or_raise()
        active_tts = activity.tts
        use_eager_chunking = (
            env_flag("VOICE_EAGER_TTS_CHUNKING_ENABLED", True)
            and active_tts is not None
            and not active_tts.capabilities.streaming
        )
        if not use_eager_chunking:
            async for frame in Agent.default.tts_node(self, observed_text(), model_settings):
                yield frame
            return

        wrapped_tts = tts.StreamAdapter(
            tts=active_tts,
            sentence_tokenizer=EagerClauseTokenizer(
                min_chunk_chars=int(os.getenv("TTS_EAGER_MIN_CHUNK_CHARS", "20")),
                max_chunk_chars=int(os.getenv("TTS_EAGER_MAX_CHUNK_CHARS", "160")),
                rest_min_chunk_chars=int(os.getenv("TTS_EAGER_REST_MIN_CHARS", "90")),
            ),
        )
        conn_options = self.session.conn_options.tts_conn_options
        async with wrapped_tts.stream(conn_options=conn_options) as stream:

            async def _forward_input() -> None:
                async for chunk in observed_text():
                    stream.push_text(chunk)
                stream.end_input()

            forward_task = asyncio.create_task(_forward_input())
            try:
                async for ev in stream:
                    yield ev.frame
            finally:
                await utils.aio.cancel_and_wait(forward_task)

    async def transcription_node(self, text, model_settings):
        """Clean debug tags like [VAD: FAST], [VAD: SLOW], and internal annotations
        before emitting to the transcription channel. LiveKit's default transcription_node
        yields raw LLM deltas directly to the room transcription stream, which would
        otherwise leak [VAD: FAST] into the frontend speech subtitle bubble.
        """
        held = ""
        async for chunk in text:
            if not chunk:
                continue
            held += str(chunk)

            # Strip complete [VAD: ...] tags and bracketed tokens
            held = _VAD_TAG_RE.sub("", held)
            held = TAG_CLEANER.sub("", held)

            # Hold back a trailing partial bracket that could complete in the next chunk
            partial = _VAD_PARTIAL_RE.search(held)
            if partial and len(held) - partial.start() <= _VAD_TAG_MAX:
                safe, held = held[: partial.start()], held[partial.start():]
            else:
                safe, held = held, ""

            if safe:
                yield safe

        if held:
            held = _VAD_TAG_RE.sub("", held)
            held = TAG_CLEANER.sub("", held)
            if held:
                yield held


class _ToolRunContext:
    """Minimal RunContext stand-in for calling a tool from agent code.

    The deterministic escalation path invokes ClinicalToolset directly rather
    than through the LLM's tool loop, and the tools only ever read `.userdata`
    and `.session` off the context.
    """

    __slots__ = ("session", "userdata")

    def __init__(self, session, userdata) -> None:
        self.session = session
        self.userdata = userdata


# Backwards-compatible alias: DEPLOY.md, tests and older scripts refer to the
# class by its former name.
GracyAgent = SakhiAgent


def _tool_name(tool) -> str:
    """Name of a tool in either shape LiveKit hands us.

    `ClinicalToolset.tools_for()` returns bound methods (`__name__`), while
    `Agent.tools` returns registered FunctionTool objects (`.info.name`).
    Reading only one of the two silently returns "" for the other, which is
    how the duplicate-tool outage happened.
    """
    info = getattr(tool, "info", None)
    name = getattr(info, "name", None) if info is not None else None
    return name or getattr(tool, "__name__", "") or repr(tool)


def build_repository() -> PatientRepository:
    """The single place storage is chosen.

    Defaults to SQLite. The JSON mock rewrites the entire file write-behind, so
    two concurrent calls each serialise their own copy of the whole dataset and
    the second silently overwrites the first - a lost blood-pressure reading
    with no error raised anywhere. It also has no crash durability: a reading
    taken moments before the process dies was never on disk.

    SAKHI_REPOSITORY=json restores the old mock for demos and offline work.
    """
    backend = os.getenv("SAKHI_REPOSITORY", "sqlite").strip().lower()

    if backend == "json":
        pipeline_event(
            "tools", "warn", "JSON repository selected",
            "mock_sixtydb.json has no transactions and loses concurrent writes; "
            "use SAKHI_REPOSITORY=sqlite for anything but a demo",
            backend="json",
        )
        return MockSixtyDBRepository(
            os.getenv("SIXTYDB_MOCK_PATH", "mock_sixtydb.json"),
            flush_delay=float(os.getenv("SIXTYDB_FLUSH_DELAY", "0.5")),
        )

    postgres_url = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL")
    if backend == "postgres" and not postgres_url:
        # SAKHI_REPOSITORY=postgres is the production pin (/etc/sakhi.env): a
        # lost DATABASE_URL must stop the worker, not quietly write clinical
        # records to a SQLite file on the instance's disk.
        pipeline_event(
            "tools", "error", "PostgreSQL repository misconfigured",
            "SAKHI_REPOSITORY=postgres but DATABASE_URL/POSTGRES_URL is not set",
            backend="postgres",
        )
        raise RuntimeError(
            "SAKHI_REPOSITORY=postgres requires DATABASE_URL (Supabase pooler "
            "connection string); refusing to fall back to SQLite"
        )
    if postgres_url:
        _dsn = urlparse(postgres_url)
        # Supabase Supavisor: 5432 = session mode, 6543 = transaction mode.
        # Both work - the repository disables prepared statements.
        _pool_mode = {5432: "session", 6543: "transaction"}.get(_dsn.port or 5432, "direct/custom")
        # PostgresPatientRepository targets the REAL clinical schema
        # (mothers/bp_records/symptom_records/teleconsults/call_reports via
        # sakhi_schema.schema_statements(POSTGRES)) - not a parallel schema.
        # See its class docstring in sakhi_sql_repository.py for the driver
        # choice (psycopg, not asyncpg). The dashboard reads through
        # sakhi_dashboard_store.get_store(), which selects its backend from
        # this same DATABASE_URL, so worker and dashboard always agree.
        pipeline_event(
            "tools", "ok", "PostgreSQL repository selected",
            "Connection-pooled Postgres storage active (mothers/bp_records/"
            "symptom_records/teleconsults/call_reports schema)",
            backend="postgres", db_host=_dsn.hostname, db_port=_dsn.port,
            pooler_mode=_pool_mode,
        )
        return PostgresPatientRepository(postgres_url)

    path = os.getenv("SAKHI_DB_PATH", "data/sakhi.sqlite3")
    pipeline_event(
        "tools", "ok", "SQLite repository selected",
        f"Durable storage at {path}; schema is Postgres-portable",
        backend="sqlite", path=path,
    )
    return SQLitePatientRepository(path)


def _build_vad() -> "silero.VAD":
    """The one place Silero VAD gets constructed - both call sites
    (prewarm_process, and the fallback if prewarm's userdata is missing) call
    this instead of duplicating the parameter list, so the two can no longer
    silently drift the way they were separately written before 2026-08-30.

    History on min_silence_duration (0.3 -> 0.2, measured live 2026-07-14):
    eou_delay was landing at a consistent ~1.0-1.16s across a real call even
    with MIN_ENDPOINTING_DELAY=0.35 - VAD's own silence-detection wait STACKS
    with the endpointing min_delay sequentially (silence detected -> THEN
    endpointing sleep starts), not a coincidence.

    Bumped again 2026-08-30 (0.2 -> 0.75 default) for outdoor rural
    deployment: 0.2s was cutting off natural pauses mid-sentence in spoken
    Kannada. This is a felt-experience tradeoff, not just a unit-testable
    one - verify with a real test call after deploying, not just this
    number in isolation.

    min_speech_duration (new, default 0.25s) ignores momentary vocal tics -
    previously left at the plugin's own default (0.05s), which is short
    enough to trigger on throat-clears and stray sounds.

    activation_threshold is left at the plugin's own default (0.5) rather
    than raised for "selectivity against bystander noise" as once proposed -
    raising it also risks missing a soft-spoken ASHA worker, a worse failure
    mode than an occasional false trigger (which the existing fragment-
    recovery/looks_incomplete guard already partially absorbs). Wired as
    configurable so it CAN be tuned from a field-observed problem without a
    code change, without shipping a speculative default change.
    """
    return silero.VAD.load(
        # 2026-09-25 (tablet field tests): 0.45 -> 0.22. This VAD now only
        # decides TURN timing - STT has its own segmentation VAD
        # (_build_stt_segmentation_vad, 0.6 s silence), so a shorter wait here
        # no longer fragments the words Sarvam receives; it only makes the
        # turn commit sooner. The 0.45 s wait stacked on top of the endpointing
        # delay. Env override still wins (VAD_MIN_SILENCE).
        min_silence_duration=float(os.getenv("VAD_MIN_SILENCE", "0.22")),
        min_speech_duration=float(os.getenv("VAD_MIN_SPEECH_DURATION", "0.15")),
        # 0.15 s pre-roll so an utterance-initial Indic vowel is not clipped.
        prefix_padding_duration=float(os.getenv("VAD_PREFIX_PADDING", "0.15")),
        # 0.30 for tablet mic distance (softer speech still activates).
        activation_threshold=float(os.getenv("VAD_ACTIVATION_THRESHOLD", "0.30")),
    )


def _build_stt_segmentation_vad() -> "silero.VAD":
    """Silero instance that cuts the call into utterances for Sarvam's batch
    STT (see build_stt). Separate from _build_vad() because the two jobs want
    different settings: turn-taking wants a tight 0.10 s pre-roll, while an
    utterance sent for transcription needs its first syllable intact (0.4 s
    pre-roll) and should not be split at a breath inside one answer.
    Same activation threshold, so both VADs agree on what counts as speech."""
    return silero.VAD.load(
        min_silence_duration=float(os.getenv("STT_SEGMENT_MIN_SILENCE", "0.6")),
        min_speech_duration=float(os.getenv("VAD_MIN_SPEECH_DURATION", "0.15")),
        prefix_padding_duration=float(os.getenv("STT_SEGMENT_PREFIX_PADDING", "0.4")),
        # Slightly MORE sensitive than the turn VAD (0.35): anything the turn
        # layer treats as speech must reach STT, or the turn is committed
        # with no words (seen with speaker-played audio, 2026-09-25).
        activation_threshold=float(os.getenv("STT_SEGMENT_ACTIVATION", "0.25")),
    )


def prewarm_process(proc: JobProcess) -> None:
    try:
        vad_started = time.perf_counter()
        proc.userdata["vad"] = _build_vad()
        proc.userdata["stt_vad"] = _build_stt_segmentation_vad()
        pipeline_event(
            "microphone",
            "ok",
            "VAD prewarm",
            "Silero VAD preloaded before calls",
            duration_ms=round((time.perf_counter() - vad_started) * 1000, 2),
        )
    except Exception as exc:
        pipeline_event("microphone", "error", "VAD prewarm failed", str(exc), error=exc)

    # Clinical backing services, loaded ONCE per worker process rather than per
    # call. The repository holds the whole patient set in memory, so a tool call
    # during a live turn costs a dict lookup instead of a synchronous JSON file
    # read on the event loop (changed.md L6).
    try:
        repo_started = time.perf_counter()
        repository = build_repository()
        asyncio.run(repository.load())
        proc.userdata["patient_repository"] = repository
        proc.userdata["triage_engine"] = TriageEngine(
            escalate_on_severe_bp_alone=env_flag("TRIAGE_ESCALATE_ON_SEVERE_BP", True),
        )
        pipeline_event(
            "tools", "ok", "SixtyDB prewarm",
            "Maternal patient repository loaded into memory",
            duration_ms=round((time.perf_counter() - repo_started) * 1000, 2),
        )
    except Exception as exc:
        # A worker with no repository can still take the call and screen the
        # mother - it just cannot persist. Failing the whole prewarm here would
        # be worse than degrading.
        pipeline_event(
            "tools", "error", "SixtyDB prewarm failed",
            "Clinical persistence unavailable; calls will run without saving records",
            error=exc, traceback=traceback.format_exc(),
        )
        proc.userdata.setdefault("patient_repository", MockSixtyDBRepository("mock_sixtydb.json"))
        proc.userdata.setdefault("triage_engine", TRIAGE)

    # Zero-TTS prompts into RAM once per worker, so a turn's reply is a dict
    # lookup (see audio_bank.py). An empty bank keeps the LLM/TTS turn loop.
    if env_flag("VOICE_ZERO_TTS_MODE", True):
        try:
            bank_started = time.perf_counter()
            count = AUDIO_BANK.preload()
            pipeline_event(
                "tts", "ok" if count else "warn", "Audio prompt bank",
                f"{count} prompt(s), {AUDIO_BANK.bytes_loaded // 1024} KB in RAM",
                duration_ms=round((time.perf_counter() - bank_started) * 1000, 2),
            )
        except Exception as exc:
            pipeline_event("tts", "error", "Audio prompt bank failed", str(exc), error=exc)

    # NOTE: the clinic FAQ semantic cache prewarm used to run here. It embedded
    # opening-hours/location/fees answers built from the appointment slot grid,
    # and SakhiAgent consulted it before the LLM on every non-fast-path turn -
    # an OpenAI embeddings round-trip in front of the model, for a FAQ set with
    # no maternal-triage content. Removed (changed.md L8).

    # --- Vendor connection prewarm (2026-09-03) ---------------------------
    #
    # Confirmed live on a real call: the FIRST LLM turn of a session measured
    # ttft=4.319s; the SECOND, on the same warm process, measured ttft=0.517s
    # for a near-identical token count (4073 vs 4179) - not a tokenization
    # cost, a cold DNS+TLS+auth handshake to Groq happening in the middle of
    # the caller's first turn. TTS showed the same shape smaller (ttfb 2.861s
    # -> 1.272s). Neither livekit-agents' own TTS.prewarm()/LLM hook nor the
    # Deepgram/openai-compatible plugins override it (checked directly - both
    # are a no-op `pass` in this installed version), so this pays that cost
    # here instead: one throwaway completion and one throwaway synthesis,
    # started as soon as the worker process comes up, absorbed entirely
    # before any call is ever dispatched to it - not on the caller's first
    # turn. Every downstream per-call build_llm()/_build_vakyam_tts() call
    # still constructs its own object (call-specific language/voice/
    # temperature), but they share this same process's underlying transport
    # (DNS cache, TLS session resumption, aiohttp connector pool via
    # livekit.agents.utils.http_context), so the handshake this pays for is
    # not repeated. Best-effort and bounded: a prewarm failure here must
    # never take the worker down, since the real per-call construction is
    # what actually needs to succeed.
    #
    # ONE CALL NOW WARMS EVERY LLM PROVIDER (2026-09-04): build_llm() races
    # every configured group simultaneously by default (LLM_HEDGE_DELAY=0 -
    # see build_llm()'s RACE, DON'T QUEUE section), so this single "hi"
    # completion below starts a real request against OpenAI, Groq, AND
    # OpenRouter concurrently, not just whichever one this call happens to
    # be routed to. Breaking after the winner's first chunk still cancels
    # the others, but by then each has already sent its request - which is
    # what establishes the DNS/TLS/auth handshake this prewarm exists to pay
    # for - so every configured provider's connection gets warmed by this
    # one call, not only the fastest one. If SAKHI_LLM_HEDGE=false (racing
    # disabled), this reverts to warming only whichever group is first in
    # LLM_PROVIDER_ORDER, same as before this change.
    try:
        llm_warm_started = time.perf_counter()

        async def _warm_llm() -> None:
            warm_llm = build_llm()
            try:
                chat_ctx = llm.ChatContext()
                chat_ctx.add_message(role="user", content="hi")
                stream = warm_llm.chat(chat_ctx=chat_ctx)
                try:
                    async for _ in stream:
                        break  # first chunk is enough to prove the round trip
                finally:
                    await stream.aclose()
            finally:
                aclose = getattr(warm_llm, "aclose", None)
                if aclose is not None:
                    await aclose()

        asyncio.run(_warm_llm())
        pipeline_event(
            "llm", "ok", "LLM prewarm",
            "Vendor connection warmed before calls",
            duration_ms=round((time.perf_counter() - llm_warm_started) * 1000, 2),
        )
    except Exception as exc:
        # Non-fatal by design: the real per-call build_llm() (agent.py's
        # entrypoint) still runs its own full construction regardless of
        # whether this warm-up succeeded - a slower first turn is a
        # degraded outcome, not a broken one.
        pipeline_event("llm", "warn", "LLM prewarm failed", str(exc), error=exc)

    try:
        tts_warm_started = time.perf_counter()
        # Read once, outside the closure, so both the construction choice
        # AND the log line below derive from the same value - confirmed
        # live that writing the provider name as a separate hardcoded
        # string in the pipeline_event call is exactly how a message
        # claiming "Vakyam" survived a switch to Smallest AI: the
        # CONSTRUCTION was already provider-aware, the LOG TEXT was not.
        tts_provider_name = os.getenv("TTS_PROVIDER", "sarvam").strip().lower()

        async def _warm_tts() -> None:
            # 2026-09-08: greeting-cache building used to live here, awaited
            # inline. That is genuinely wrong, not just slow: prewarm's own
            # asyncio.run() loop is short-lived and gates
            # `job runner initialized` / worker registration - three
            # synthesis calls (one per language), each retrying through
            # livekit-agents' own retry policy on any transient error,
            # turned "TTS prewarm" into a 24-SECOND step in one real run
            # (a Vakyam "insufficient_credits" error triggered the retries;
            # the delay itself would have happened on ANY transient error).
            # That is worker start-up latency, not per-call latency, but it
            # is real and this was the wrong place to spend it. Cache
            # building now happens from a real job's own long-lived event
            # loop instead (see the fire-and-forget task near the greeting
            # in entrypoint()) - never awaited, never on any critical path.
            # BUG FOUND 2026-09-08, live, right after switching TTS_PROVIDER
            # to "sarvam": this ternary never had a "sarvam" branch - it
            # fell through to Vakyam for ANY provider name that wasn't
            # smallest_ai or gnani. The "TTS prewarm - sarvam connection
            # warmed" log line right after this block reports the ENV value,
            # not what was actually built, so it kept claiming "sarvam" while
            # this line silently warmed (and, per _build_vakyam_tts's own
            # pipeline_event, logged) Vakyam - the exact "log text is not
            # provider-aware" bug this same function's docstring already
            # names and warns against, just reintroduced by an incomplete
            # provider switch instead of a hardcoded log string.
            warm_tts = (
                _build_smallest_ai_tts(None) if tts_provider_name == "smallest_ai"
                else _build_gnani_tts(None) if tts_provider_name == "gnani"
                else _build_sarvam_tts(None, language=Language.KANNADA.sarvam_code)
                if tts_provider_name == "sarvam"
                else _build_vakyam_tts(None)
            )
            try:
                stream = warm_tts.synthesize("hi")
                try:
                    async for _ in stream:
                        break
                finally:
                    await stream.aclose()
            finally:
                await warm_tts.aclose()

        # Confirmed live: the Smallest AI plugin's aiohttp session is
        # obtained through livekit.agents.utils.http_context, which raises
        # "Attempted to use an http session outside of a job context" unless
        # either a real job is running or this context manager is opened
        # explicitly - prewarm is neither, since it runs before any job
        # exists by definition. Vakyam's own wrapper manages its own
        # session and never needed this; wrapping unconditionally here is
        # still correct for it (an idle, no-op context) and keeps this one
        # code path correct for whichever provider TTS_PROVIDER names next.
        async def _warm_tts_in_job_context() -> None:
            from livekit.agents.utils import http_context

            async with http_context.open():
                await _warm_tts()

        asyncio.run(_warm_tts_in_job_context())
        pipeline_event(
            "tts", "ok", "TTS prewarm",
            f"{tts_provider_name} connection warmed before calls",
            duration_ms=round((time.perf_counter() - tts_warm_started) * 1000, 2),
            provider=tts_provider_name,
        )
    except Exception as exc:
        pipeline_event("tts", "warn", "TTS prewarm failed", str(exc), error=exc)

    # --- STT connection prewarm (2026-09-04) -------------------------------
    #
    # No STT warm-up existed before this - only VAD/LLM/TTS were prewarmed.
    # Gnani's STT is a real-time WebSocket (wss://api.vachana.ai/stt/v3/
    # stream), so the same cold DNS+TLS+auth-handshake cost documented above
    # for LLM/TTS applies here too: paid once, here, off the hot path,
    # instead of on the caller's first turn. Deepgram/SmallestAI are not
    # warmed here - this only fires for whichever provider STT_PRIMARY
    # actually names, matching how the TTS warm-up above only builds
    # whichever provider TTS_PROVIDER actually names rather than every
    # possible one.
    stt_primary_name = os.getenv("STT_PRIMARY", "deepgram").strip().lower()

    async def _warm_gnani_stt() -> None:
        warm_stt = gnani.STT(language="en-IN", sample_rate=16000)
        stream = warm_stt.stream()
        try:
            stream.end_input()
            async for _ in stream:
                break  # the initial "connected" ack is enough to prove the handshake
        finally:
            await stream.aclose()
        await warm_stt.aclose()

    async def _warm_sarvam_stt() -> None:
        # 2026-09-08: added when STT_PRIMARY switched to sarvam and this
        # block turned out to be Gnani-only, hardcoded - the exact same
        # class of "only the provider actually configured gets prewarmed"
        # bug already fixed once for TTS above (_warm_tts()'s missing
        # sarvam branch) the same day, just on the STT side instead.
        warm_stt = SarvamSTT(
            api_key=os.getenv("SARVAM_API_KEY"),
            language_code=Language.KANNADA.sarvam_code,
            model=os.getenv("SARVAM_STT_MODEL", "saaras:v3"),
            mode=os.getenv("SARVAM_STT_MODE", "transcribe"),
        )
        stream = warm_stt.stream()
        try:
            stream.end_input()
            async for _ in stream:
                break
        finally:
            await stream.aclose()

    if stt_primary_name == "gnani" and os.getenv("GNANI_API_KEY"):
        stt_warmer, stt_warm_label = _warm_gnani_stt, "Gnani"
    elif stt_primary_name == "sarvam" and os.getenv("SARVAM_API_KEY"):
        stt_warmer, stt_warm_label = _warm_sarvam_stt, "Sarvam"
    else:
        stt_warmer, stt_warm_label = None, None

    if stt_warmer is not None:
        try:
            stt_warm_started = time.perf_counter()

            async def _warm_stt_in_job_context() -> None:
                from livekit.agents.utils import http_context

                async with http_context.open():
                    await stt_warmer()

            asyncio.run(_warm_stt_in_job_context())
            pipeline_event(
                "stt", "ok", "STT prewarm",
                f"{stt_warm_label} connection warmed before calls",
                duration_ms=round((time.perf_counter() - stt_warm_started) * 1000, 2),
            )
        except Exception as exc:
            # Non-fatal by design, same reasoning as the LLM/TTS prewarms
            # above: the real per-call build_stt() still runs its own full
            # construction regardless of whether this warm-up succeeded.
            pipeline_event("stt", "warn", "STT prewarm failed", str(exc), error=exc)

    if KittenLocalTTS is None or not env_flag("KITTEN_TTS_ENABLED", False) or not env_flag("PREWARM_KITTEN_TTS", False):
        return
    try:
        kitten_tts = KittenLocalTTS(
            model_name=os.getenv("KITTEN_TTS_MODEL", "KittenML/kitten-tts-nano-0.8"),
            voice=os.getenv("KITTEN_TTS_VOICE", "Bella"),
            speed=float(os.getenv("KITTEN_TTS_SPEED", "1.0")),
            cache_dir=os.getenv("KITTEN_TTS_CACHE_DIR") or None,
            backend=os.getenv("KITTEN_TTS_BACKEND", "cpu") or None,
            clean_text=env_flag("KITTEN_TTS_CLEAN_TEXT", True),
        )
        started = time.perf_counter()
        kitten_tts.prewarm()
        proc.userdata["kitten_tts"] = kitten_tts
        pipeline_event(
            "tts",
            "ok",
            "KittenTTS worker prewarm",
            "Local KittenTTS model preloaded before calls",
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
            model=kitten_tts.model,
        )
    except Exception as exc:
        pipeline_event(
            "tts",
            "error",
            "KittenTTS worker prewarm failed",
            str(exc),
            error=exc,
            traceback=traceback.format_exc(),
        )
async def request_process(req: JobRequest) -> None:
    logger.info("Received job request for room %s (job_id=%s)", req.room.name, req.id)
    try:
        await req.accept()
        logger.info("Job request accepted successfully for room %s (job_id=%s)", req.room.name, req.id)
    except Exception as exc:
        logger.error("Failed to accept job request for room %s (job_id=%s):\n%s", req.room.name, req.id, traceback.format_exc())
        raise


# NOTE: the four stub maternal tools that used to live here (search_patient,
# record_bp, flag_high_risk_pregnancy, connect_smfmi_specialist) have been
# replaced by ClinicalToolset in sakhi_tools.py. Two of them were no-ops:
# record_bp persisted nothing and computed no risk, and flag_high_risk_pregnancy
# only wrote a log line. See changed.md C1-C3.


async def entrypoint(ctx: JobContext):
    # Attribute every pipeline event raised from here on - in this task and
    # every task it spawns - to this call (see sakhi_telemetry.CURRENT_SESSION).
    CURRENT_SESSION.set(_job_room_name(ctx))
    try:
        logger.info("Starting MyStree care coordinator agent session.")
        pipeline_event("worker", "info", "Entrypoint", "Starting MyStree care coordinator agent session")
        pipeline_event("worker", "info", "Env diagnostics", "Worker process environment visibility", env=env_diagnostics())
        livekit_url = os.getenv("LIVEKIT_URL", "")
        pipeline_event(
            "webrtc",
            "info",
            "LiveKit URL host",
            "LiveKit server host surfaced for region verification",
            event="livekit_url_host",
            host=urlparse(livekit_url).hostname,
            url_present=bool(livekit_url),
        )
        usage_collector = metrics.UsageCollector()
        # Connect to the room first so the WebRTC join overlaps provider construction.
        logger.info("Connecting to LiveKit room.")
        connect_started = time.perf_counter()
        pipeline_event("webrtc", "info", "Room connect", "Connecting worker to LiveKit room")
        await ctx.connect()
        # Call-duration clock for the post-call report (save_sakhi_call_report).
        # Starts here, not at process boot, so it measures the actual call, not
        # worker idle time before this job was dispatched.
        call_started = time.perf_counter()
        pipeline_event(
            "webrtc",
            "ok",
            "Room connected",
            "Worker connected to LiveKit room",
            duration_ms=round((time.perf_counter() - connect_started) * 1000, 2),
            room=getattr(ctx.room, "name", None),
        )

        # Room-composite egress to S3 (2026-09-02) - see start_call_recording()
        # for why this needs its own credentials. Best-effort: recording_key
        # stays None on any failure, and the call proceeds unaffected.
        recording_egress_id, recording_key = await start_call_recording(ctx)

        # The caller's UI can pick a TTS provider (Sarvam/Rumik/Smallest/Gemini)
        # and a voice within it. Prefer dispatch/job metadata because it
        # exists before the browser participant metadata is synced;
        # participant metadata is only a fallback. This prevents the worker
        # from racing ahead and locking TTS to the default provider/voice.
        job_metadata_payload = parse_metadata_json(getattr(ctx.job, "metadata", ""))
        requested_tts_provider, requested_voice = provider_and_voice_from_metadata(job_metadata_payload)
        participant = None
        caller_phone = caller_phone_from_metadata_payload(job_metadata_payload)
        voice_source = "dispatch" if requested_voice else "default"
        if requested_voice:
            pipeline_event(
                "tts",
                "ok",
                "Voice selected from dispatch",
                f"Using {requested_tts_provider} voice {requested_voice} from LiveKit dispatch metadata",
                provider=requested_tts_provider,
                speaker=requested_voice,
                source="dispatch",
            )
        try:
            remote_participants = getattr(ctx.room, "remote_participants", None)
            if isinstance(remote_participants, dict) and remote_participants:
                participant = next(iter(remote_participants.values()))
            else:
                participant = await asyncio.wait_for(
                    ctx.wait_for_participant(),
                    timeout=float(os.getenv("CALLER_METADATA_WAIT_SECONDS", "3.0")),
                )
            if participant.metadata:
                metadata_payload = parse_metadata_json(participant.metadata)
                participant_provider, participant_voice = provider_and_voice_from_metadata(metadata_payload)
                if participant_voice:
                    requested_tts_provider = participant_provider
                    requested_voice = participant_voice
                    voice_source = "participant"
                caller_phone = caller_phone or caller_phone_from_metadata_payload(metadata_payload)
            caller_phone = caller_phone or extract_caller_phone_from_metadata(participant)
            pipeline_event(
                "dispatch", "info", "Caller joined",
                f"participant={participant.identity} provider={requested_tts_provider} voice={requested_voice or 'default'}",
                voice_source=voice_source,
                caller_phone_tail=(db_helper.normalize_phone(caller_phone)[-4:] if caller_phone else None),
            )
        except Exception:
            pipeline_event(
                "dispatch",
                "info" if requested_voice else "warn",
                "Caller metadata skipped",
                (
                    f"Participant metadata was not ready; continuing with dispatch voice {requested_voice}"
                    if requested_voice
                    else "Starting immediately with default voice; metadata was not ready"
                ),
                voice_source=voice_source,
                provider=requested_tts_provider,
                speaker=requested_voice or os.getenv("SARVAM_SPEAKER", "ishita"),
            )
        
        # Merge job metadata payload and participant payload, participant overrides job
        combined_metadata = {}
        combined_metadata.update(job_metadata_payload)
        if participant and participant.metadata:
            try:
                combined_metadata.update(parse_metadata_json(participant.metadata))
            except Exception:
                pass
                
        # Extract sliders. Language is parsed into the Language enum once, here,
        # instead of being passed around as a bare string that each provider
        # builder re-interpreted differently (oops.md §2.3). Kannada is the
        # Bidar pilot default.
        call_language = spoken_language(Language.parse(
            combined_metadata.get("language"),
            default=Language.parse(os.getenv("SAKHI_DEFAULT_LANGUAGE", "kn")),
        ))
        call_temperature = float(
            combined_metadata.get("temperature", os.getenv("LLM_TEMPERATURE", LLM_CONFIG["temperature"]))
        )
        call_silence_threshold = float(combined_metadata.get("silence_threshold", os.getenv("MIN_ENDPOINTING_DELAY", "0.15")))
        call_tts_speed = float(combined_metadata.get("tts_speed", default_tts_pace()))
        # The three ASSEMBLYAI_* endpointing sliders that used to be read here
        # were passed to build_stt(), which stopped using them when STT moved to
        # Sarvam. Turn timing lives in build_turn_handling() + DynamicVADController.

        preload_task = asyncio.create_task(preload_user(caller_phone))
        pipeline_event("dispatch", "info", "Provider build", "Building STT, LLM, TTS, VAD, and turn detector")
        
        try:
            stt_segment_vad = ctx.proc.userdata.get("stt_vad") or _build_stt_segmentation_vad()
            stt_provider = build_stt(language=call_language, segment_vad=stt_segment_vad)
        except Exception:
            logger.error("Failed to build STT provider:\n%s", traceback.format_exc())
            raise

        try:
            vad_provider = ctx.proc.userdata.get("vad") or _build_vad()
        except Exception:
            logger.error("Failed to load VAD provider:\n%s", traceback.format_exc())
            raise

        try:
            llm_provider = build_llm(temperature=call_temperature, language=call_language)
        except Exception:
            logger.error("Failed to build LLM provider:\n%s", traceback.format_exc())
            raise

        try:
            tts_provider = build_tts(
                tts_provider=requested_tts_provider, 
                voice_id=requested_voice, 
                speed_override=call_tts_speed,
                language=call_language
            )
        except Exception:
            logger.error("Failed to build TTS provider:\n%s", traceback.format_exc())
            raise

        # NOTE: the `extract_vad_flags` transform that used to sit here is gone.
        # It parsed `[VAD: FAST]` / `[VAD: SLOW]` tags the LLM was prompted to
        # emit, then mutated `vad_provider.min_silence_duration` - the Silero
        # instance SHARED ACROSS EVERY CONCURRENT CALL on this worker via
        # proc.userdata - and reached into the private
        # `session._turn_detector._min_endpointing_delay`. So one caller's turn
        # retuned turn-taking for every other live call, and it fought
        # DynamicVADController for the same knob. DynamicVADController now owns
        # endpointing alone, through the public session.update_options() API and
        # scoped to one call. See changed.md C6 / L3.

        # Clinical tools are bound to the shared repository and triage engine.
        # Built per call so the toolset can emit pipeline events, but holding no
        # per-call mutable state itself - that lives in SakhiCallState.
        # Strips [VAD: FAST|SLOW] before TTS and routes it to the per-call
        # controller. Created here because tts_text_transforms is fixed at
        # session construction; its controller is attached after session.start().
        vad_tag_transform = VadTagTransform()

        clinical_tools = ClinicalToolset(
            ctx.proc.userdata["patient_repository"],
            ctx.proc.userdata["triage_engine"],
            event_sink=pipeline_event,
        )

        # Dashboard telemetry. Started here (the repository is known and the
        # call's event loop is running) and flushed from a shutdown callback,
        # which runs after the entrypoint's own final "Room disconnected" /
        # "Usage summary" events, so the tail of the call is never lost.
        call_session_id = getattr(ctx.room, "name", None) or _job_room_name(ctx)
        CURRENT_SESSION.set(call_session_id)
        telemetry = _telemetry_for(ctx.proc.userdata["patient_repository"])
        if telemetry is not None:
            async def _flush_telemetry() -> None:
                try:
                    await telemetry.flush()
                finally:
                    telemetry.forget_session(call_session_id)
            ctx.add_shutdown_callback(_flush_telemetry)

        lean_mode = env_flag("SAKHI_LEAN_INTAKE_MODE", True)
        # Lean mode relies on llm_node's per-turn re-scope to hand the model
        # its recording tools. With SAKHI_SCOPED_TOOLS=false that re-scope
        # never runs, so [end_call] alone would be the whole call's toolset
        # and nothing could ever be recorded - use the full set instead.
        # Lean mode: ONE constant tool set, registered on the Agent (not the
        # session) before the first turn. Two failures shaped this:
        # * listing tools on both session and agent -> "duplicate function
        #   name" (each bound-method lookup is a new object) and no session;
        # * changing the set inside llm_node -> livekit ran the model's tool
        #   call against the pre-change list ("unknown AI function
        #   record_patient_intake") and the turn ended in silence.
        lean_agent_tools = None
        if lean_mode and env_flag("SAKHI_SCOPED_TOOLS", True):
            initial_tools = []
            lean_agent_tools = [*lean_stage_tools(clinical_tools, 1), end_call]
        else:
            initial_tools = [*clinical_tools.as_list(), end_call]

        session = AgentSession(
            userdata=SakhiCallState(
                session_id=call_session_id,
                language=call_language,
            ),
            stt=stt_provider,
            vad=vad_provider,
            llm=llm_provider,
            tts=tts_provider,
            tools=initial_tools,
            turn_handling=build_turn_handling(silence_threshold=call_silence_threshold),
            # Keep the built-in markdown/emoji filters and add the JSON/code guard
            # so tool-call leakage from the LLM is never spoken aloud.
            # Order matters. The VAD tag is stripped FIRST so no later transform
            # can see it or speak it, and Kannada number expansion runs LAST so
            # it operates on the final spoken text rather than on something a
            # later filter might still rewrite.
            #
            # BUG FOUND 2026-09-09: kannada_number_normalization used to run
            # unconditionally here regardless of call_language - confirmed
            # live, a raw digit leaking into HINDI text got converted to
            # KANNADA number words mid-sentence ("रक्तचाप 150 है" ->
            # "रक्तचाप ನೂರ ಐವತ್ತು है"). number_normalization_for() picks the
            # real transform only for an actual Kannada call and a safe
            # passthrough otherwise - see sakhi_kannada.py's own note.
            tts_text_transforms=[
                vad_tag_transform,
                "filter_markdown",
                "filter_emoji",
                medical_output_guard_for(call_language),
                filter_code_artifacts,
                filter_disallowed_punctuation,
                indian_english_phonetic_normalization,
                number_normalization_for(call_language),
            ],
            max_tool_steps=int(os.getenv("MAX_TOOL_STEPS", "3")),
            conn_options=SessionConnectOptions(
                max_unrecoverable_errors=int(os.getenv("MAX_UNRECOVERABLE_ERRORS", "5"))
            ),
        )
        # --- frontend identity fallback: typed name/age via RPC -------------
        #
        # Armed by sakhi_tools.py's search_patient (lookup_attempts>=1 branch),
        # which sets state.awaiting_identity_box and withdraws the
        # search_patient tool so the model has nothing left to retry identity
        # through voice with. This is the other half: the frontend calls this
        # RPC method once the ASHA worker types the name+age on screen,
        # bypassing STT/LLM entirely for identity resolution from here on.
        #
        # No RPC/data-channel usage existed anywhere in this codebase before
        # this - both livekit-agents (Python) and livekit-client (browser)
        # already ship register_rpc_method/performRpc, just unused until now.
        #
        # Registered with a closure over `session`, not a snapshot of
        # `session.userdata` - each call reads session.userdata fresh, so it
        # always mutates the live call state, not a stale reference.
        async def _handle_submit_identity(rpc_data) -> str:
            from livekit.rtc import RpcError

            try:
                payload = json.loads(rpc_data.payload)
            except (json.JSONDecodeError, TypeError):
                raise RpcError(RpcError.ErrorCode.APPLICATION_ERROR, "invalid JSON payload")

            raw_name = str(payload.get("name") or "").strip()
            if not raw_name:
                raise RpcError(RpcError.ErrorCode.APPLICATION_ERROR, "name is required")

            # identify() silently drops an out-of-range age with no signal
            # back to the caller (sakhi_domain.py) - validated here instead,
            # so a bad value actually reaches the frontend as a rejection
            # rather than vanishing.
            try:
                age = int(payload.get("age"))
            except (TypeError, ValueError):
                raise RpcError(RpcError.ErrorCode.APPLICATION_ERROR, "age must be a number")
            if not (10 <= age <= 60):
                raise RpcError(RpcError.ErrorCode.APPLICATION_ERROR, "age must be between 10 and 60")

            state = session.userdata
            repo = ctx.proc.userdata["patient_repository"]

            # Mirrors search_patient/register_mother's find-then-create logic
            # (sakhi_tools.py). Called directly on the repository rather than
            # through the @llm.function_tool-wrapped tools - an RPC handler
            # runs outside RunContext, so it cannot call those. Small,
            # acknowledged duplication; extracting the shared logic is a
            # follow-up, not done here.
            try:
                matches = await repo.find_patients(name=raw_name)
            except Exception:
                logger.warning("Identity-fallback lookup failed", exc_info=True)
                matches = []

            if matches:
                record = matches[0]
            else:
                try:
                    record = await repo.create_patient(name=raw_name, age=age)
                except Exception:
                    logger.warning("Identity-fallback registration failed", exc_info=True)
                    raise RpcError(RpcError.ErrorCode.APPLICATION_ERROR, "could not save the record")

            state.identify(
                patient_id=record.patient_id, village=record.village,
                phone=record.phone, gestational_weeks=record.gestational_weeks,
            )
            # The typed name/age are authoritative - they are what she just
            # confirmed on screen - so this call sets them last, after
            # whatever the matched/created record carried.
            state.lock_identity_from_frontend(name=raw_name, age=age)

            pipeline_event(
                "tools", "ok", "Identity resolved via frontend",
                f"{state.name} ({state.patient_id}), age {state.age}",
                event="identity_resolved_via_frontend",
                patient_id=state.patient_id,
                # name/age as structured fields, same reasoning as the
                # "Patient found"/"Mother registered" events in
                # sakhi_tools.py (2026-08-30, SakhiHeader's mother-name
                # display) - this is the third and last place a name/age
                # can first become known during a call.
                name=state.name, age=state.age,
            )
            return "ok"

        ctx.room.local_participant.register_rpc_method("submit_identity", _handle_submit_identity)

        # --- one-tap field correction: typed BP/symptom fixes via RPC -------
        #
        # Module C (2026-08-30): the ASHA worker taps a value shown by Live
        # Form Sync (see the pipeline_event emissions inside
        # record_bp/record_symptoms/record_gestational_age in sakhi_tools.py)
        # and corrects it with a numeric keypad, bypassing another voice
        # round-trip - same RPC pattern as submit_identity above, same
        # closure-over-session reasoning.
        #
        # Field whitelist, mutator dispatch, and re-triage all live in
        # ClinicalToolset.apply_field_correction() (sakhi_tools.py) so they
        # are unit-testable without a LiveKit room and so this reuses
        # _retriage() instead of duplicating its lock/persist logic a third
        # time (record_bp and record_symptoms are the other two call sites).
        # This handler is just the RPC-transport translation on top of it.
        async def _handle_submit_correction(rpc_data) -> str:
            from livekit.rtc import RpcError

            try:
                payload = json.loads(rpc_data.payload)
            except (json.JSONDecodeError, TypeError):
                raise RpcError(RpcError.ErrorCode.APPLICATION_ERROR, "invalid JSON payload")

            field = str(payload.get("field") or "")
            state = session.userdata
            try:
                await clinical_tools.apply_field_correction(state, field, payload.get("value"))
            except ValueError as exc:
                raise RpcError(RpcError.ErrorCode.APPLICATION_ERROR, str(exc))
            return "ok"

        ctx.room.local_participant.register_rpc_method("submit_correction", _handle_submit_correction)

        # EOU commit is owned entirely by the semantic multilingual turn
        # detector configured in build_turn_handling() - no custom watchdog
        # timer forcing replies here. A stalled detector should surface as a
        # visible bug, not be silently papered over by a force-reply hack.
        turn_transcripts: list[dict] = []
        state_watch = {
            "agent": "initializing",
            "user": "listening",
            "last_user_activity": time.perf_counter(),
            "last_transcript": time.perf_counter(),
            "last_ping": 0.0,
            "unanswered_pings": 0,
            "closing": False,
        }

        @session.on("metrics_collected")
        def _on_metrics_collected(ev: MetricsCollectedEvent):
            metrics.log_metrics(ev.metrics)
            usage_collector.collect(ev.metrics)
            turn_latency.on_metric(ev.metrics)
            metric_type = getattr(ev.metrics, "type", "")

            # [SAKHI_AUDIO_INGEST] - Silero emits vad_metrics only while it is
            # running inference on REMOTE audio frames, so this is the direct
            # "are the tablet's frames reaching the worker?" signal
            # (user_input_transcribed fires on transcripts, not frames).
            # idle_time = seconds since VAD last detected speech.
            if metric_type == "vad_metrics":
                inferences = int(getattr(ev.metrics, "inference_count", 0) or 0)
                idle_s = float(getattr(ev.metrics, "idle_time", 0.0) or 0.0)
                if inferences and not state_watch.get("audio_ingest_logged"):
                    state_watch["audio_ingest_logged"] = True
                    pipeline_event(
                        "microphone", "ok", "[SAKHI_AUDIO_INGEST]",
                        "Remote audio frames are reaching VAD",
                        event="audio_ingest_started",
                        inference_count=inferences, idle_time_s=round(idle_s, 2),
                    )
                silent_after = float(os.getenv("AUDIO_INGEST_SILENT_WARN_S", "20"))
                if (
                    inferences
                    and idle_s >= silent_after
                    and state_watch.get("agent") == "listening"
                    and idle_s - state_watch.get("silent_warned_idle", -1e9) >= silent_after
                ):
                    state_watch["silent_warned_idle"] = idle_s
                    pipeline_event(
                        "microphone", "warn", "[SAKHI_AUDIO_INGEST]",
                        f"Frames arriving but no speech detected for {idle_s:.0f}s "
                        "(mic muted, wrong input device, or near-silent audio)",
                        event="audio_ingest_no_speech",
                        inference_count=inferences, idle_time_s=round(idle_s, 2),
                    )
                elif idle_s < silent_after:
                    state_watch["silent_warned_idle"] = -1e9

            stage_key, label = _metric_stage(metric_type)
            status = "warn" if getattr(ev.metrics, "cancelled", False) else "ok"
            metric_details = {"metrics": ev.metrics}
            if metric_type == "eou_metrics":
                metric_details.update(
                    {
                        "event": "eou_delay_ms",
                        "value": round(getattr(ev.metrics, "end_of_utterance_delay", 0) * 1000, 2),
                        "transcription_delay_ms": round(getattr(ev.metrics, "transcription_delay", 0) * 1000, 2),
                    }
                )
            elif metric_type == "llm_metrics":
                metric_details.update(
                    {
                        "event": "llm_ttft_ms",
                        "ttft_ms": round(getattr(ev.metrics, "ttft", 0) * 1000, 2),
                        "prompt_tokens": getattr(ev.metrics, "prompt_tokens", getattr(ev.metrics, "input_tokens", 0)),
                        "total_tokens": getattr(ev.metrics, "total_tokens", 0),
                    }
                )
                pipeline_event(
                    "llm",
                    "ok",
                    "Turn TTFT",
                    "LLM first token timing collected",
                    **{k: v for k, v in metric_details.items() if k != "metrics"},
                )
            elif metric_type == "tts_metrics":
                metric_details.update(
                    {
                        "event": "ttfa_ms",
                        "ttfa_ms": round(getattr(ev.metrics, "ttfb", 0) * 1000, 2),
                        "audio_duration_ms": round(getattr(ev.metrics, "audio_duration", 0) * 1000, 2),
                        "characters_count": getattr(ev.metrics, "characters_count", 0),
                    }
                )
                pipeline_event(
                    "tts",
                    "ok",
                    "Turn TTFA",
                    "Time to first audio collected",
                    **{k: v for k, v in metric_details.items() if k != "metrics"},
                )
            pipeline_event(
                stage_key,
                status,
                label,
                _metric_message(ev.metrics),
                **metric_details,
            )

            # Stream real-time telemetry metrics to stdout for native AWS CloudWatch ingestion
            if metric_type in ("llm_metrics", "tts_metrics", "eou_metrics"):
                cw_payload = {
                    "source": "sakhi.metrics",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "metric_type": metric_type,
                    "details": {k: v for k, v in metric_details.items() if k != "metrics"},
                }
                try:
                    import json
                    print(f"[CLOUDWATCH_METRICS] {json.dumps(cw_payload)}", file=sys.stdout, flush=True)
                except Exception:
                    pass

        @session.on("user_input_transcribed")
        def _on_user_input_transcribed(ev):
            raw_transcript = getattr(ev, "transcript", "")
            is_final = getattr(ev, "is_final", False)
            # Redact BEFORE anything is written. An Aadhaar or phone number
            # spoken by an ASHA worker must not land in logs/pipeline_events.jsonl
            # or in the post-call report - under DPDP, an identifier that is
            # never stored cannot leak from storage.
            transcript, _pii = guardrails.redact_pii(raw_transcript)
            if _pii:
                pipeline_event(
                    "stt", "warn", "PII redacted from transcript",
                    "Identifiers masked before logging: " + ", ".join(_pii),
                    event="pii_redacted_transcript", kinds=_pii,
                )
            state_watch["last_transcript"] = time.perf_counter()
            state_watch["unanswered_pings"] = 0  # the caller is still there
            state_watch["last_user_activity"] = time.perf_counter()
            pipeline_event(
                "stt",
                "ok" if is_final else "info",
                "Transcript final" if is_final else "Transcript interim",
                transcript or "<empty transcript>",
                is_final=is_final,
                speaker_id=getattr(ev, "speaker_id", None),
                language=getattr(ev, "language", None),
            )
            # `booking_prefetch.handle_transcript(...)` used to run here - on
            # EVERY interim transcript, several times a second while the caller
            # speaks - to warm the clinic's appointment lookups. It also mutated
            # a module-level global shared by every concurrent call. Removed
            # (changed.md L7, oops.md §2.1).
            turn_transcripts.append(
                {
                    "role": "user",
                    "text": transcript,
                    "final": bool(is_final),
                    "language": getattr(ev, "language", None),
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
            )
        @session.on("agent_state_changed")
        def _on_agent_state_changed(ev):
            new_state = getattr(ev, "new_state", "")
            state_watch["agent"] = str(new_state)
            status = "ok" if str(new_state) in {"speaking", "listening", "thinking"} else "info"
            pipeline_event(
                "dispatch",
                status,
                "Agent state",
                f"{getattr(ev, 'old_state', '')} -> {new_state}",
            )

        @session.on("user_state_changed")
        def _on_user_state_changed(ev):
            new_state = str(getattr(ev, "new_state", ""))
            # Per-utterance speech duration for the pacing tracker.
            #
            # The STT metrics' `audio=` field is CUMULATIVE session audio, not
            # the length of this utterance - using it would make every worker
            # look slower and slower as the call went on. The VAD's own
            # speaking/​listening transitions are the only honest source.
            now = time.perf_counter()
            if new_state == "speaking":
                state_watch["speech_started_at"] = now
                session.interrupt()
            elif state_watch.get("speech_started_at"):
                state_watch["last_speech_seconds"] = now - state_watch["speech_started_at"]
                state_watch["speech_started_at"] = None
            state_watch["user"] = new_state
            state_watch["last_user_activity"] = now
            pipeline_event(
                "microphone",
                "info",
                "User audio state",
                f"{getattr(ev, 'old_state', '')} -> {new_state}",
            )

        @session.on("speech_created")
        def _on_speech_created(ev):
            pipeline_event(
                "tts",
                "info",
                "Speech created",
                f"source={getattr(ev, 'source', '')} user_initiated={getattr(ev, 'user_initiated', False)}",
            )

        @session.on("function_tools_executed")
        def _on_function_tools_executed(ev):
            calls = []
            for call, output in ev.zipped():
                calls.append(
                    {
                        "name": getattr(call, "name", ""),
                        "call_id": getattr(call, "call_id", ""),
                        "output_present": output is not None,
                    }
                )
            pipeline_event(
                "tools",
                "ok",
                "Tool batch completed",
                f"{len(calls)} tool call(s) executed",
                calls=calls,
            )

        @session.on("error")
        def _on_session_error(ev):
            pipeline_event(
                "worker",
                "error",
                "Session error",
                str(getattr(ev, "error", "")),
                error=getattr(ev, "error", None),
                source=getattr(ev, "source", None),
            )

        # NOTE: the appointment slot-cache preload and its 10-second background
        # refresh loop used to run here, on every call. They served the clinic
        # booking tools, none of which are registered on a Sakhi session - so
        # this was a per-call SQLite read plus a repeating timer doing work no
        # tool could consume. Removed (changed.md L7). The maternal patient
        # repository is loaded once at process prewarm instead.

        agent = SakhiAgent(
            instructions=(
                "You are Sakhi, a maternal-health co-pilot for ASHA workers. "
                "Never call yourself an AI."
            ),
            chat_ctx=build_initial_context(
                preloaded_user=await preload_task,
                language=call_language,
            ),
            toolset=clinical_tools,
            **({"tools": lean_agent_tools} if lean_agent_tools is not None else {}),
        )
        if hasattr(agent, "fnc_ctx") and agent.fnc_ctx is not None:
            agent.fnc_ctx.session = session

        logger.info("Starting AgentSession with cascaded fallback providers.")
        session_started = time.perf_counter()
        pipeline_event("dispatch", "info", "Session start", "Starting AgentSession with fallback providers")
        # Reports what _noise_filter() actually returned - this line used to
        # say "BVC enabled" unconditionally, even with NOISE_CANCELLATION=off.
        _nc = _noise_filter()
        pipeline_event(
            "microphone",
            "info",
            "Noise cancellation enabled" if _nc is not None else "Noise cancellation off",
            (f"LiveKit {type(_nc).__name__} applied before STT and turn detection"
             if _nc is not None else "Raw microphone audio goes to VAD and STT (NOISE_CANCELLATION=off)"),
            event="noise_cancellation_enabled" if _nc is not None else "noise_cancellation_off",
            provider=type(_nc).__name__ if _nc is not None else "none",
        )
        # A WebRTC transport reconnect is not a new clinical conversation.
        # The September incident showed the greeting/q01 again after a brief
        # link interruption because LiveKit's default closes AgentSession on
        # participant disconnect, destroying its in-RAM SakhiCallState. Keep
        # the session alive for the bounded supervision grace below. Set
        # SAKHI_RECONNECT_GRACE_ENABLED=false for byte-for-byte old teardown.
        reconnect_grace_enabled = env_flag("SAKHI_RECONNECT_GRACE_ENABLED", True)
        await session.start(
            agent,
            room=ctx.room,
            room_input_options=RoomInputOptions(
                noise_cancellation=_noise_filter(),
                close_on_disconnect=not reconnect_grace_enabled,
            ),
        )

        # Speech detected, no words transcribed -> say so (never silence).
        # 2026-09-25 01:36: the ASHA worker spoke, our VAD heard it, Sarvam
        # returned "" for near-silent audio (-60 dBFS) and Sakhi said nothing
        # at all - from her side the agent simply stopped responding.
        _heard = {"final_at": 0.0, "speech_at": 0.0, "overlapped": False}
        _unheard = _make_unheard_speech_handler(
            session, suppressed=lambda: _heard["overlapped"]
        )
        _empty_grace_s = float(os.getenv("STT_EMPTY_GRACE_S", "1.5"))

        def _on_empty_segment(info: dict) -> None:
            # One empty STT segment is often only PART of her answer - the
            # rest arrives as a final a moment later (live call 2026-09-28
            # 10:52:30: "didn't hear" was spoken, then "ಎಲ್ಲಾ" arrived 3 s
            # later). Wait briefly and apologise only if no words came.
            reported_at = time.monotonic()

            def _check() -> None:
                if _heard["final_at"] < reported_at and _heard["speech_at"] < reported_at:
                    _unheard(info)

            try:
                asyncio.get_running_loop().call_later(_empty_grace_s, _check)
            except RuntimeError:
                _unheard(info)

        _sarvam_wrappers_module.STT_EMPTY_SINK = _on_empty_segment

        # Watchdog for every other way words can go missing (segmenter miss,
        # provider drop, fallback hiccup): the user spoke, no final transcript
        # followed within STT_SILENCE_WATCHDOG_S -> answer "didn't hear".
        _watchdog_s = float(os.getenv("STT_SILENCE_WATCHDOG_S", "3.0"))

        @session.on("user_input_transcribed")
        def _note_final(ev):
            if getattr(ev, "is_final", False) and (getattr(ev, "transcript", "") or "").strip():
                _heard["final_at"] = time.monotonic()

        async def _watch_for_words(spoke_at: float) -> None:
            await asyncio.sleep(_watchdog_s)
            # She paused and kept talking (speech_at moved on): her words are
            # still coming, it is not an unanswered answer. Live call
            # 2026-09-28 10:51:53: "didn't hear" was spoken one second before
            # the final transcript of her long answer arrived.
            if _heard["final_at"] < spoke_at and _heard["speech_at"] <= spoke_at:
                _unheard({"audio_duration_ms": None, "gain_db": None, "reason": "no_transcript_after_speech"})

        @session.on("user_state_changed")
        def _speech_watchdog(ev):
            new, old = getattr(ev, "new_state", None), getattr(ev, "old_state", None)
            if new == "speaking":
                _heard["speech_at"] = time.monotonic()
                _heard["overlapped"] = getattr(session, "agent_state", None) == "speaking"
            elif (
                old == "speaking"
                and env_flag("STT_SILENCE_WATCHDOG", True)
                and not _heard["overlapped"]
                # a cough or a click is not an unanswered answer
                and time.monotonic() - _heard["speech_at"] >= float(os.getenv("STT_WATCHDOG_MIN_SPEECH_S", "0.6"))
            ):
                asyncio.create_task(_watch_for_words(_heard["speech_at"]))

        if env_flag("VOICE_ADAPTIVE_VAD_ENABLED", True):
            # Needs a live session (update_options propagates to the running
            # activity), hence attached here rather than at agent construction.
            agent._vad_controller = DynamicVADController(
                session, pacing=PacingTracker(language=call_language.value)
            )
            agent._state_watch = state_watch
            # Same controller instance drives both signals: the explicit
            # [VAD: ...] tag (preferred) and the regex fallback in tts_node.
            vad_tag_transform.controller = agent._vad_controller

        # Avatar seam. Audio-only unless SAKHI_AVATAR names a backend, and a
        # backend that fails to start degrades to audio-only rather than ending
        # the call - see sakhi_avatar.py. Started after session.start() because
        # every backend needs a live session and a joined room.
        # The avatar MUST be fully attached before the first session.say().
        #
        # Starting it concurrently with the greeting was tried and it hangs the
        # call. The plugin's `replace_audio_tail(...)` swaps the agent's audio
        # sink, and swapping it while audio is already playing means the
        # greeting's playout never completes - `await session.say()` never
        # returns, entrypoint never reaches its supervision loop, and the call
        # sits there silently forever. Observed directly: "Greeting queued" with
        # no matching "Greeting accepted".
        #
        # So the ~6s Tavus join is paid up front, before anyone speaks. It is
        # the price of the avatar and it cannot be overlapped away at this
        # layer. The timeout is the safety valve: a slow or wedged Tavus must
        # cost a bounded delay and then fall back to audio-only, never a hung
        # call. Set SAKHI_AVATAR=none to skip the cost entirely.
        avatar_backend = build_avatar_backend(event_sink=pipeline_event)
        avatar_task = None
        try:
            await asyncio.wait_for(
                avatar_backend.start(session, ctx.room),
                timeout=float(os.getenv("AVATAR_START_TIMEOUT", "15.0")),
            )
        except asyncio.TimeoutError:
            pipeline_event(
                "playback", "warn", "Avatar start timed out",
                "Tavus did not attach in time; continuing audio-only",
                event="avatar_start_timeout", backend=avatar_backend.key,
            )
        except Exception:
            logger.warning("Avatar start failed; continuing audio-only", exc_info=True)

        pipeline_event(
            "dispatch",
            "ok",
            "Session started",
            "AgentSession started; noise cancellation and semantic turn detector active",
            duration_ms=round((time.perf_counter() - session_started) * 1000, 2),
        )

        # --- 600-second hard call ceiling (2026-09-02) ---------------------
        #
        # An ANC screening is a 5-10 minute protocol. Past that the call is
        # not screening any more - it is a stuck turn loop, a worker who put
        # the phone down, or an open line burning per-minute telephony and
        # STT spend on silence. Every one of those ends the same way: cap it.
        #
        # The ceiling PERSISTS FIRST and hangs up second. A call killed with
        # its state unwritten loses the BP and the red flags that were the
        # entire point of it - the recorded findings must survive the
        # timeout, which is why this awaits the report write before closing
        # the room rather than relying on the normal teardown path.
        async def _enforce_call_ceiling() -> None:
            limit = float(os.getenv("MAX_CALL_SECONDS", "600"))
            try:
                await asyncio.sleep(limit)
            except asyncio.CancelledError:
                return
            state = session.userdata
            pipeline_event(
                "worker", "warn", "Call ceiling reached",
                f"{limit:.0f}s hard limit hit - persisting state and closing the call",
                event="call_ceiling_reached",
                limit_s=limit,
                intake_done=len(state.intake.answered) if state else 0,
                stage=state.intake.current_stage() if state else None,
            )
            try:
                if state is not None:
                    report = build_sakhi_call_report(
                        state, duration_ms=limit * 1000.0,
                        recording_status="stopped" if recording_egress_id else "none",
                        recording_key=recording_key,
                    )
                    await save_post_call_report(
                        report, ctx.proc.userdata["patient_repository"]
                    )
                    if recording_egress_id:
                        try:
                            asyncio.create_task(stop_call_recording(recording_egress_id))
                        except Exception:
                            pass
            except Exception:
                logger.warning("Ceiling-triggered report write failed", exc_info=True)
            try:
                await session.aclose()
            except Exception:
                logger.warning("Ceiling-triggered session close failed", exc_info=True)

        ceiling_task = asyncio.create_task(_enforce_call_ceiling())

        async def _cancel_ceiling_task() -> None:
            # Must be an async function, not a plain lambda: confirmed live
            # that add_shutdown_callback's caller (job.py) unconditionally
            # does `await callback()` on whatever this returns - the
            # original `lambda: ceiling_task.cancel() if ... else None`
            # returned Task.cancel()'s own bool result, and awaiting a bare
            # bool raised "TypeError: object bool can't be used in 'await'
            # expression" on every single call's shutdown, logged as an
            # error even on a perfectly normal call end.
            if not ceiling_task.done():
                ceiling_task.cancel()

        ctx.add_shutdown_callback(_cancel_ceiling_task)

        greeting_started = time.perf_counter()
        greeting_text = greeting_for(call_language)
        active_voice = requested_voice or os.getenv("SARVAM_SPEAKER", "manisha")

        # 2026-09-08: this cache existed since (see load_cached_greeting's own
        # docstring) but was never actually called from here - built, never
        # wired. A live incident traced a 3,986ms greeting TTFA not to any
        # architectural cold-start problem (WebRTC media wait measured
        # 0.06ms the same call - not the bottleneck) but to a live TTS
        # round-trip hitting a transient provider error and retrying. Cached
        # playback removes that live round-trip entirely for the one line of
        # text guaranteed identical on every call.
        cached_greeting_frames = load_cached_greeting(active_voice, call_language)

        pipeline_event(
            "tts", "info", "Greeting queued", greeting_text,
            event="greeting_queued", provider=requested_tts_provider, voice=active_voice,
            cached=bool(cached_greeting_frames),
            language=call_language.value,
        )

        # Wait for the participant's audio to actually be subscribed before
        # speaking. "Session started" is not "media is flowing" - greeting on
        # the former clips the opening word or two on a slow join, and a
        # fixed sleep cannot cover a gap that varies per session. Bounded so
        # a client that never reports subscription is never held up.
        media_wait_started = time.perf_counter()
        audio_ready = await wait_for_remote_audio(
            ctx.room, timeout=float(os.getenv("GREETING_MEDIA_TIMEOUT", "1.5"))
        )
        pipeline_event(
            "webrtc", "ok" if audio_ready else "warn",
            "Remote audio ready" if audio_ready else "Remote audio wait timed out",
            "Participant audio subscribed; greeting is safe to speak" if audio_ready
            else "Greeting proceeding without confirmed inbound audio (bounded wait elapsed)",
            event="greeting_media_wait",
            audio_ready=audio_ready,
            duration_ms=round((time.perf_counter() - media_wait_started) * 1000, 2),
        )

        if cached_greeting_frames:
            async def _cached_audio_iter():
                for frame in cached_greeting_frames:
                    yield frame

            # allow_interruptions=False (2026-09-28): the greeting is ~4.5 s
            # and tells the worker what to say. It was being cut mid-sentence
            # at ~3 s by the first sound the mic picked up (Sakhi's own voice
            # echoing from the tablet speaker, a cough), leaving silence and
            # then a "didn't hear you" apology - heard as "the voice gets
            # stuck". Anything she says during it is still transcribed.
            await session.say(
                greeting_text, audio=_cached_audio_iter(), allow_interruptions=False
            )
        else:
            await session.say(greeting_text, allow_interruptions=False)
            # Fire-and-forget: fill the cache for THIS voice+language from a
            # real job's own long-lived event loop, so the NEXT call on this
            # worker gets the instant cached path - never awaited, so it can
            # never add latency to (or fail) the call actually in progress.
            # Deliberately not done in prewarm_process() any more - see
            # _warm_tts()'s own note on why that blocked worker registration.
            asyncio.create_task(
                ensure_greeting_cache(active_voice, tts_provider, call_language)
            )

        # Evaluate VAD on initial greeting
        agent._vad_eval(greeting_text)
        pipeline_event(
            "tts",
            "ok",
            "Greeting accepted",
            "Initial greeting sent to TTS",
            duration_ms=round((time.perf_counter() - greeting_started) * 1000, 2),
        )

        # NOTE: connection_state is an rtc.ConnectionState enum; comparing it to the
        # string "connected" was always False, so this loop used to exit immediately.
        from livekit import rtc

        # Fast session exit when caller disconnects or closes tab:
        # Prevents abandoned sessions from idling for 4+ minutes and hoarding 250MB RAM each.
        @ctx.room.on("participant_disconnected")
        def _on_participant_leave(p):
            logger.info("Remote participant disconnected: %s", getattr(p, "identity", "unknown"))
            if (
                not reconnect_grace_enabled
                and not getattr(ctx.room, "remote_participants", {})
            ):
                state_watch["closing"] = True

        empty_room_started: float | None = None
        while ctx.room.connection_state == rtc.ConnectionState.CONN_CONNECTED and not state_watch["closing"]:
            await asyncio.sleep(0.5)
            if not getattr(ctx.room, "remote_participants", {}):
                if empty_room_started is None:
                    empty_room_started = time.perf_counter()
                else:
                    reconnect_grace_s = (
                        float(os.getenv("SAKHI_RECONNECT_GRACE_SECONDS", "20.0"))
                        if reconnect_grace_enabled else 2.0
                    )
                    if (time.perf_counter() - empty_room_started) < reconnect_grace_s:
                        continue
                    logger.info(
                        "Room empty for %.1fs; reconnect grace expired. Ending session cleanly.",
                        reconnect_grace_s,
                    )
                    break
            else:
                empty_room_started = None
            now = time.perf_counter()
            silence_s = now - max(state_watch["last_user_activity"], state_watch["last_transcript"])
            ping_gap_s = now - state_watch["last_ping"]
            if (
                env_flag("ENABLE_LINE_LIVE_CHECK", True)
                and not state_watch["closing"]
                and state_watch["agent"] == "listening"
                and state_watch["user"] != "speaking"
                # A queued/active agent speech means we are not actually idle
                # even if the state string briefly reads "listening" - firing
                # here is how the check used to collide with agent audio.
                and session.current_speech is None
                and silence_s > float(os.getenv("LINE_LIVE_CHECK_SECONDS", "45.0"))
                and ping_gap_s > float(os.getenv("LINE_LIVE_CHECK_COOLDOWN_SECONDS", "60.0"))
            ):
                state_watch["last_ping"] = now
                state_watch["unanswered_pings"] += 1
                # Auto-close after repeated unanswered check-ins. Previously
                # this loop pinged "Are you still there?" every 30s forever -
                # a caller who walked away left a zombie call running until
                # something else killed it. Two unanswered checks (~45s+ of
                # total silence) is a finished conversation.
                if state_watch["unanswered_pings"] > int(os.getenv("LINE_LIVE_MAX_UNANSWERED", "3")):
                    state_watch["closing"] = True
                    pipeline_event(
                        "worker", "warn", "Silence auto-close",
                        "No response after repeated check-ins; ending the call",
                        event="silence_auto_close",
                        unanswered_pings=state_watch["unanswered_pings"],
                        silence_s=round(silence_s, 2),
                    )
                    try:
                        await session.say(
                            "It seems the line has gone quiet... I will end the call now. "
                            "Please call back anytime. " + GOODBYE_TEXT,
                            allow_interruptions=True,
                        )
                    except Exception:
                        pass  # dead session - the hangup below still runs
                    schedule_hangup_after_playout(session)
                    continue  # loop exits naturally when the room disconnects
                # Short on purpose - a long check-in phrase is more likely to
                # collide with a caller who starts speaking mid-playback.
                phrase = os.getenv("LINE_LIVE_CHECK_TEXT", "Are you still there?")
                pipeline_event(
                    "turn",
                    "warn",
                    "Line live check",
                    "Agent listening with sustained silence; sending short check-in",
                    event="line_live_check",
                    silence_s=round(silence_s, 2),
                    phrase=phrase,
                )
                try:
                    await session.say(phrase, allow_interruptions=True)
                except Exception as exc:
                    pipeline_event(
                        "turn",
                        "warn",
                        "Line live check failed",
                        "Unable to speak line-is-live prompt",
                        traceback=traceback.format_exc(),
                    )
                    # Observed live: the room's connection_state can keep
                    # reporting CONN_CONNECTED for 10+ seconds after the
                    # WebRTC data channels have already died (caller's
                    # network/tab dropped without a clean disconnect), which
                    # this loop's own exit condition never catches. Once the
                    # AgentSession itself has stopped, every subsequent
                    # session.say() anywhere in the call - fast path, FAQ
                    # cache, normal LLM replies - fails the same way, so the
                    # caller hears nothing for the rest of the "call" while
                    # the worker spins here every 30s forever. Treat this
                    # specific error as a hard signal to end the call now.
                    if "isn't running" in str(exc) or "is not running" in str(exc):
                        pipeline_event(
                            "webrtc", "error", "AgentSession died unexpectedly",
                            "Session stopped running while the room still reported connected; ending the call now",
                        )
                        break

        # Flush any buffered clinical writes before the process moves on. The
        # repository writes behind, so a call that ends immediately after a BP
        # or escalation must not lose the record.
        try:
            await ctx.proc.userdata["patient_repository"].flush()
        except Exception:
            logger.warning("Repository flush on call end failed", exc_info=True)

        try:
            # A join still in flight must be cancelled, or the Tavus
            # conversation it is about to create leaks a concurrency slot with
            # nobody on the call.
            if avatar_task is not None and not avatar_task.done():
                avatar_task.cancel()
            await avatar_backend.aclose()
        except Exception:
            logger.warning("Avatar close failed", exc_info=True)

        logger.info("Room disconnected, ending agent task.")
        pipeline_event("webrtc", "warn", "Room disconnected", "LiveKit room disconnected")
        logger.info("Session usage summary: %s", usage_collector.get_summary())
        pipeline_event("worker", "ok", "Usage summary", "Session ended", usage=usage_collector.get_summary())
        # Kept for its ops-log sentiment/follow-up line only - it predates the
        # Sakhi pivot and carries no clinical fields, so it is never persisted.
        # See build_sakhi_call_report() for the report that actually gets saved.
        legacy_summary = summarize_call_from_transcripts(
            turn_transcripts,
            await preload_task,
            getattr(ctx.room, "name", None),
        )
        pipeline_event(
            "worker", "info", "Call summary (heuristic)",
            legacy_summary.get("call_summary", ""),
            event="call_summary_heuristic",
            sentiment=legacy_summary.get("user_sentiment"),
            follow_up_required=legacy_summary.get("follow_up_required"),
        )
        # Persist the structured call report immediately so the visit summary
        # is available on the frontend without waiting for egress stop network calls.
        sakhi_report = build_sakhi_call_report(
            session.userdata,
            duration_ms=(time.perf_counter() - call_started) * 1000,
            recording_status="stopped" if recording_egress_id else "none",
            recording_key=recording_key,
        )
        await save_post_call_report(sakhi_report, ctx.proc.userdata["patient_repository"])

        # Stop egress in the background if recording was active
        if recording_egress_id:
            try:
                asyncio.create_task(stop_call_recording(recording_egress_id))
            except Exception:
                logger.warning("Background stop_call_recording task creation failed", exc_info=True)
    except Exception:
        pipeline_event("worker", "error", "Fatal entrypoint error", "Agent entrypoint crashed", traceback=traceback.format_exc())
        logger.error("Fatal error in agent entrypoint:\n%s", traceback.format_exc())
        raise


def _acquire_singleton_lock():
    """Refuse to start if another worker is already running on this machine.

    Duplicate workers repeatedly caused silent calls: LiveKit round-robins jobs
    across registered workers, so a second (often stale/broken) copy stole
    ~half the calls. Binding a localhost port is an OS-enforced mutex that
    releases automatically if the process dies.
    """
    import socket
    import sys

    port = int(os.getenv("WORKER_SINGLETON_PORT", "47821"))
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # NOTE: deliberately no SO_REUSEADDR — on Windows it would allow a second bind.
    try:
        sock.bind(("127.0.0.1", port))
        sock.listen(1)
        return sock  # held open for the process lifetime
    except OSError:
        msg = (
            f"ANOTHER agent worker is already running (singleton port {port} is taken). "
            "Refusing to start a duplicate - duplicates steal LiveKit jobs and cause silent calls. "
            "Stop the other worker first, or set WORKER_SINGLETON_PORT to run a second deliberately."
        )
        print(msg)
        pipeline_event("worker", "error", "Duplicate worker blocked", msg)
        sys.exit(1)


if __name__ == "__main__":
    _singleton_lock = _acquire_singleton_lock()
    try:
        # Default matches .env's LIVEKIT_AGENT_NAME - only reached if that
        # var is somehow unset. "mystree-care" (the pre-Sakhi ancestor
        # agent) is deliberately NOT the fallback here: a separate worker
        # under that name runs on this same LiveKit Cloud project, and a
        # silent fallback to it would make this worker race that one for
        # explicitly-dispatched jobs (see .env's LIVEKIT_AGENT_NAME comment,
        # 2026-09-02).
        agent_name = os.getenv("LIVEKIT_AGENT_NAME", "sakhi-maternal-care")
        if env_flag("ENABLE_STATUS_SERVER", True):
            from status_server import start_status_server

            start_status_server()
        pipeline_event("worker", "info", "Worker boot", "Starting LiveKit worker process")
        cli.run_app(
            WorkerOptions(
                entrypoint_fnc=entrypoint,
                prewarm_fnc=prewarm_process,
                request_fnc=request_process,
                agent_name=agent_name,
                num_idle_processes=int(os.getenv("LIVEKIT_NUM_IDLE_PROCESSES", "1")),
                # KittenTTS prewarm can take >10s on this machine; the framework
                # default (10s) was killing the job process before it could join
                # the room, which made Start Call silently do nothing.
                initialize_process_timeout=float(os.getenv("PROC_INIT_TIMEOUT", "60")),
            )
        )
    except (KeyboardInterrupt, SystemExit):
        logger.info("Received termination signal. Closing care coordinator agent.")
