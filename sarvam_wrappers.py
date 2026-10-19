import aiohttp
import asyncio
import base64
import hashlib
import inspect
import io
import json
import logging
import os
import time
import uuid
import wave
from urllib.parse import urlencode, urlparse

import re

import websockets
from livekit import rtc
from livekit.agents import APIConnectionError, APIStatusError, stt, tts
from livekit.agents.types import APIConnectOptions, DEFAULT_API_CONNECT_OPTIONS
from livekit.agents.utils import AudioBuffer
from sakhi_normalizer import SakhiSpeechNormalizer

logger = logging.getLogger("sarvam_wrappers")

# Server message types that mean "all audio for the flushed text has been sent".
_TTS_COMPLETION_TYPES = {"flush_complete", "flushed", "done", "complete", "end", "eos"}
_seen_unknown_types: set[str] = set()

# At least one letter or digit (any script, including Kannada \u0C80-\u0CFF and Devanagari) — the minimum Sarvam will accept.
_SPEAKABLE_RE = re.compile(r"[A-Za-z0-9\u0900-\u0D7F]", re.UNICODE)
_SARVAM_V3_SPEAKERS = {
    "aditya", "ritu", "ashutosh", "priya", "neha", "rahul", "pooja", "rohan",
    "simran", "kavya", "amit", "dev", "ishita", "shreya", "ratan", "varun",
    "manan", "sumit", "roopa", "kabir", "aayan", "shubh", "advait", "anand",
    "tanya", "tarun", "sunny", "mani", "gokul", "vijay", "shruti", "suhani",
    "mohit", "kavitha", "rehan", "soham", "rupali", "niharika",
}


def _payload_shape(value):
    if isinstance(value, dict):
        return {str(k): _payload_shape(v) for k, v in value.items()}
    if isinstance(value, list):
        return ["list", len(value)]
    if isinstance(value, str):
        return "str"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if value is None:
        return "null"
    return type(value).__name__


def sanitize_kannada_tts(text: str) -> str:
    """Backward-compatible entry point for the shared speech normalizer."""
    return SakhiSpeechNormalizer.sanitize_for_tts(text)


def _normalize_tts_text(text: str, *, max_chars: int) -> str:
    sanitized = sanitize_kannada_tts(text)
    normalized = re.sub(r"\s+", " ", str(sanitized or "")).strip()
    if not normalized or not _SPEAKABLE_RE.search(normalized):
        return ""
    return normalized[:max_chars].strip()


class SarvamStreamingTTS:
    """High-reliability streaming WebSocket TTS client for Sarvam Bulbul:v3 with ping keep-alives and timeouts."""
    SARVAM_WS_URL = "wss://api.sarvam.ai/text-to-speech-websocket"

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "bulbul:v3",
        speaker: str = "shreya",
        target_language_code: str = "kn-IN",
        pace: float = 0.95,
        speech_sample_rate: int = 24000,
    ):
        self.api_key = api_key or os.getenv("SARVAM_API_KEY", "")
        self.model = model
        self.speaker = speaker
        self.target_language_code = target_language_code
        self.pace = pace
        self.speech_sample_rate = speech_sample_rate
        self.ws = None

    async def connect(self):
        headers = {
            "api-subscription-key": self.api_key,
            "x-api-key": self.api_key,
        }
        self.ws = await websockets.connect(
            self.SARVAM_WS_URL,
            extra_headers=headers,
            ping_interval=10,
            ping_timeout=5,
            open_timeout=5.0,
        )
        return self.ws

    async def send_text(self, text: str):
        clean_text = sanitize_kannada_tts(text)
        payload = {
            "inputs": [clean_text],
            "target_language_code": self.target_language_code,
            "speaker": self.speaker,
            "pace": self.pace,
            "speech_sample_rate": self.speech_sample_rate,
            "enable_preprocessing": True,
            "model": self.model,
        }
        if self.ws:
            await self.ws.send(json.dumps(payload))

    async def recv_audio(self, timeout: float = 3.0) -> bytes | None:
        if not self.ws:
            return None
        try:
            msg = await asyncio.wait_for(self.ws.recv(), timeout=timeout)
            data = json.loads(msg)
            if "audios" in data and data["audios"]:
                return base64.b64decode(data["audios"][0])
            if "audio" in data.get("data", {}):
                return base64.b64decode(data["data"]["audio"])
        except asyncio.TimeoutError:
            logger.warning("SarvamStreamingTTS recv timed out after %.1fs", timeout)
        except Exception as e:
            logger.error("SarvamStreamingTTS recv error: %s", e)
        return None

    async def close(self):
        if self.ws:
            await self.ws.close()
            self.ws = None


def _sarvam_tts_config_payload(tts_instance: "SarvamTTS") -> dict:
    speaker = str(tts_instance._speaker or "").strip().lower()
    if "v3" in tts_instance._model and speaker not in _SARVAM_V3_SPEAKERS:
        raise ValueError(f"Unsupported Sarvam bulbul:v3 speaker: {speaker!r}")
    
    lang = tts_instance._target_language_code.lower()
    if lang == "kannada":
        sarvam_lang = "kn-IN"
    elif lang == "hindi":
        sarvam_lang = "hi-IN"
    elif lang == "english":
        sarvam_lang = "en-IN"
    else:
        sarvam_lang = tts_instance._target_language_code

    data = {
        "target_language_code": sarvam_lang,
        "speaker": speaker,
        "pace": tts_instance._pace,
        "max_chunk_length": tts_instance._max_chunk_length,
        "output_audio_codec": tts_instance._output_audio_codec,
    }
    # Live probe on 2026-07-09: bulbul:v3 websocket rejects
    # min_buffer_size with 422 "Input parameters has to be a valid dictionary".
    # Keep the local buffering knob, but never send it unless explicitly enabled
    # for a future Sarvam API revision.
    if os.getenv("SARVAM_SEND_MIN_BUFFER_SIZE", "false").lower() in {"1", "true", "yes", "on"}:
        data["min_buffer_size"] = tts_instance._min_buffer_size
    return {"type": "config", "data": data}


def _sarvam_tts_rest_payload(tts_instance: "SarvamTTS", text: str) -> dict:
    """The actual REST /text-to-speech body SarvamTTSChunkedStream sends -
    extracted out of _run() (2026-09-08) so `enable_preprocessing` wiring
    could be unit-tested without mocking an HTTP call. Distinct from
    _sarvam_tts_config_payload above, which shapes a WEBSOCKET config
    message for the realtime endpoint this provider does not actually use
    (see SarvamTTS.__init__'s own note on why streaming=False)."""
    lang = tts_instance._target_language_code.lower()
    if lang == "kannada":
        sarvam_lang = "kn-IN"
    elif lang == "hindi":
        sarvam_lang = "hi-IN"
    elif lang == "english":
        sarvam_lang = "en-IN"
    else:
        sarvam_lang = tts_instance._target_language_code

    payload = {
        "inputs": [text],
        "target_language_code": sarvam_lang,
        "speaker": tts_instance._speaker,
        "model": tts_instance._model,
        "pace": tts_instance._pace,
    }
    if tts_instance._enable_preprocessing:
        payload["enable_preprocessing"] = True
    return payload


def _sarvam_tts_text_payload(text: str, *, max_chars: int) -> dict | None:
    normalized = _normalize_tts_text(text, max_chars=max_chars)
    if not normalized:
        return None
    payload = {"type": "text", "data": {"text": normalized}}
    if not isinstance(payload.get("data"), dict):
        raise ValueError("Sarvam text payload data must be a dictionary.")
    return payload


def _sarvam_tts_flush_payload() -> dict:
    return {"type": "flush", "data": {}}


def _redact_url(url: str) -> str:
    parsed = urlparse(url)
    return parsed._replace(query="...").geturl() if parsed.query else url


def _ws_url(base_url: str, path: str, query: dict[str, str]) -> str:
    base = base_url.rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base.removeprefix("https://")
    elif base.startswith("http://"):
        base = "ws://" + base.removeprefix("http://")
    return f"{base}{path}?{urlencode(query)}"


def _ws_headers(api_key: str) -> dict[str, str]:
    if not api_key:
        raise APIStatusError("SARVAM_API_KEY is missing.", status_code=401, retryable=False)
    return {
        "api-subscription-key": api_key,
        "x-api-key": api_key,
    }


def _websocket_connect(
    uri: str,
    headers: dict[str, str],
    *,
    open_timeout: float | None = None,
    ping_interval: float | None = 10,
    ping_timeout: float | None = 5,
):
    connect_params = inspect.signature(websockets.connect).parameters
    header_kw = "additional_headers" if "additional_headers" in connect_params else "extra_headers"
    kwargs = {header_kw: headers}
    if open_timeout is not None and "open_timeout" in connect_params:
        kwargs["open_timeout"] = open_timeout
    if "ping_interval" in connect_params:
        kwargs["ping_interval"] = ping_interval
    if "ping_timeout" in connect_params:
        kwargs["ping_timeout"] = ping_timeout
    return websockets.connect(uri, **kwargs)


def _to_api_error(message: str, exc: Exception) -> APIConnectionError:
    return APIConnectionError(f"{message}: {type(exc).__name__}: {exc}")


def _pcm16_to_wav_bytes(pcm: bytes, *, sample_rate: int, num_channels: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(num_channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)
    return buffer.getvalue()


def _env_bool(name: str, default: bool) -> bool:
    """Small local env parser (keeps this provider module independent of agent.py)."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _sarvam_stt_query(
    *,
    model: str,
    language_code: str,
    mode: str,
    current_protocol: bool,
) -> dict[str, str]:
    """Return the handshake query for the selected Sarvam STT protocol.

    Incident 2026-09-24: live browser audio reached Silero VAD, but Sarvam
    returned no transcript and raised no error, so LiveKit's fallback never
    activated.  The v3 endpoint now takes its configuration in the handshake;
    the old wrapper sent only language/model there and then sent a separate
    v2-era ``config`` message that the v3 schema does not accept.

    ``SARVAM_STT_CURRENT_PROTOCOL_ENABLED=false`` retains the exact legacy
    query for emergency rollback without reverting code.
    """
    query = {"language-code": language_code, "model": model}
    if not current_protocol:
        return query
    query.update(
        {
            "mode": mode,
            "sample_rate": "16000",
            "vad_signals": "true",
            # OFF by default (2026-09-24 incident, measured - see
            # docs/STT_FRAGMENTATION_INCIDENT.md). "true" makes Sarvam end a
            # segment at every micro-pause: one 3.6s answer came back as 4
            # context-free finals, "ಇಪ್ಪತ್ತೆಂಟು" was cut into "ಇಪ್ಪತ್ತು" + "28"
            # (age read as 20), and short fragments were hallucinated as
            # filler ("Yes Sir.", "Okay"). Clean speech: 0/10 correct with
            # it on, 10/10 with it off; speaker-to-mic 3/10 vs 10/10.
            "high_vad_sensitivity": "true"
            if _env_bool("SARVAM_STT_HIGH_VAD_SENSITIVITY", False)
            else "false",
            "flush_signal": "true"
            if _env_bool("SARVAM_STT_FLUSH_SIGNAL_ENABLED", True)
            else "false",
        }
    )
    return query


def _sarvam_stt_audio_message(pcm: bytes, *, current_protocol: bool) -> dict:
    """Build one STT audio message without ever exposing provider credentials.

    Current Saaras streaming consumes raw 16 kHz PCM bytes, but the production
    endpoint's message validator still requires the literal encoding label
    ``audio/wav`` (confirmed by its exact validation error on 2026-09-24 and
    mirrored by LiveKit's maintained Sarvam plugin).  The old implementation
    wrapped every 100 ms block in a fresh WAV file and added an undocumented
    outer ``type=audio`` field.  Keep that byte-for-byte legacy shape behind
    the protocol kill-switch, but default to the current plugin contract.
    """
    if current_protocol:
        encoded = base64.b64encode(pcm).decode("ascii")
        return {
            "audio": {
                "data": encoded,
                "encoding": "audio/wav",
                "sample_rate": 16000,
            }
        }

    wav_bytes = _pcm16_to_wav_bytes(pcm, sample_rate=16000, num_channels=1)
    return {
        "type": "audio",
        "audio": {
            "data": base64.b64encode(wav_bytes).decode("ascii"),
            "encoding": "audio/wav",
            "sample_rate": 16000,
        },
    }


def _sarvam_stt_flush_message() -> dict[str, str]:
    return {"type": "flush"}


# [SAKHI_STT_DEBUG] - one structured record per transcript, so a failed turn
# can be placed before STT (little or no audio reached it), inside STT
# (plenty of audio, wrong/fragmented text) or after it (right text, not
# extracted). agent.py points this at pipeline_event; None = log only.
# No transcript text leaves this module - only its length and a hash.
STT_DEBUG_SINK = None
# Called (with {audio_duration_ms, gain_db}) when our VAD detected speech
# but the provider returned no text. agent.py sets it per call.
STT_EMPTY_SINK = None


def _dbfs(sumsq: float, n: int) -> float | None:
    if n <= 0 or sumsq <= 0:
        return None
    import math
    return round(10 * math.log10(sumsq / n / (32768.0 ** 2)), 1)


class _SegmentAudio:
    """Audio actually sent to the provider since the previous final."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.samples = 0
        self.sumsq = 0.0
        self.peak = 0

    def add(self, pcm: bytes) -> None:
        import array
        a = array.array("h")
        a.frombytes(pcm[: len(pcm) // 2 * 2])
        if not a:
            return
        self.samples += len(a)
        self.sumsq += float(sum(x * x for x in a))
        self.peak = max(self.peak, max(abs(x) for x in a))


def _emit_stt_debug(record: dict) -> None:
    logger.info("[SAKHI_STT_DEBUG] %s", json.dumps(record, ensure_ascii=True))
    sink = STT_DEBUG_SINK
    if sink is not None:
        try:
            sink(record)
        except Exception:
            logger.debug("STT debug sink failed", exc_info=True)


def _strip_wav_header(audio_bytes: bytes) -> bytes:
    if audio_bytes.startswith(b"RIFF"):
        idx = audio_bytes.find(b"data")
        if idx != -1 and len(audio_bytes) > idx + 8:
            return audio_bytes[idx + 8:]
    return audio_bytes


class SarvamSTT(stt.STT):
    def __init__(
        self,
        api_key: str,
        model: str = "saaras:v3",
        language_code: str = "en-IN",
        base_url: str = "https://api.sarvam.ai",
        mode: str = "transcribe",
    ):
        super().__init__(
            capabilities=stt.STTCapabilities(
                streaming=True,
                interim_results=True,
            )
        )
        self._api_key = api_key
        self._model = model
        self._language_code = language_code
        self._base_url = base_url
        # 2026-09-08: "transcribe" is the only mode value confirmed against
        # the real API's own config schema in this codebase's history. A
        # "codemix" value was accepted (no rejection) in a live probe against
        # saaras:v3 - Sarvam's saaras family is itself built for code-mixed
        # Indic speech, so the model choice may matter more than this field -
        # but that acceptance is not the same as a documented, confirmed
        # effect, so it is wired as configurable rather than defaulted to.
        self._mode = mode
        # ON by default because Saaras v3/v4 use the current handshake/audio
        # contract.  This remains a per-process kill-switch so production can
        # revert to the pre-incident wire format without a code rollback.
        self._current_protocol = _env_bool("SARVAM_STT_CURRENT_PROTOCOL_ENABLED", True)

    _VALID_LANGS = {
        "en-IN", "hi-IN", "bn-IN", "gu-IN", "kn-IN", "ml-IN",
        "mr-IN", "od-IN", "pa-IN", "ta-IN", "te-IN", "unknown",
    }

    def _sanitize_language(self, language) -> str:
        """The agent framework passes hints like 'en' or NOT_GIVEN sentinels.
        Sarvam goes SILENT (no error, no transcripts) on codes it doesn't know,
        which made the agent deaf in live sessions - so anything that isn't a
        known Sarvam code falls back to the configured language."""
        if isinstance(language, str):
            norm = language.strip()
            if norm in self._VALID_LANGS:
                return norm
            low = norm.lower()
            if low in {"en", "en-us", "en-gb", "en-in"}:
                return "en-IN"
            if low in {"hi", "hi-in"}:
                return "hi-IN"
        return self._language_code

    def stream(
        self,
        *,
        language=None,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> "SarvamSpeechStream":
        lang = self._sanitize_language(language)
        return SarvamSpeechStream(
            self, self._api_key, self._model, lang, self._base_url, conn_options,
            mode=self._mode, current_protocol=self._current_protocol,
        )

    async def _recognize_impl(
        self,
        buffer: AudioBuffer,
        *,
        language: str | None = None,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> stt.SpeechEvent:
        raise NotImplementedError("SarvamSTT only supports streaming STT.")


class SarvamBatchSTT(stt.STT):
    """Sarvam's REST speech-to-text, one whole utterance per request.

    Used behind livekit's stt.StreamAdapter, which segments the call with
    the worker's own Silero VAD and hands each finished utterance here
    (agent.build_stt). Why not the streaming socket: there, SARVAM's
    server-side VAD decides what speech is. On a real call (2026-09-25,
    01:23) LiveKit's VAD heard three bursts from the ASHA worker and Sarvam
    transcribed only the last - the other two never produced a transcript,
    and the intake stalled on q01. One VAD (ours, the one the turn detector
    also uses) owning segmentation removes that disagreement entirely.

    Measured on production: the full q01 answer transcribed exactly, in
    0.25-0.33 s per utterance (the streaming final took 0.63-0.69 s).
    """

    _ENDPOINT = "/speech-to-text"

    def __init__(
        self,
        api_key: str,
        model: str = "saaras:v3",
        language_code: str = "kn-IN",
        base_url: str = "https://api.sarvam.ai",
        mode: str = "transcribe",
        timeout_s: float | None = None,
    ):
        super().__init__(capabilities=stt.STTCapabilities(streaming=False, interim_results=False))
        self._api_key = api_key
        self._model = model
        self._language_code = language_code if language_code in SarvamSTT._VALID_LANGS else "kn-IN"
        self._base_url = base_url.rstrip("/")
        self._mode = mode
        self._timeout = timeout_s or float(os.getenv("SARVAM_STT_REST_TIMEOUT", "6"))
        self._session: aiohttp.ClientSession | None = None

    @property
    def model(self) -> str:
        return self._model

    @property
    def provider(self) -> str:
        return "sarvam"

    def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def aclose(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()

    @staticmethod
    def _to_wav16k(buffer: AudioBuffer) -> tuple[bytes, int]:
        frame = rtc.combine_audio_frames(buffer) if isinstance(buffer, list) else buffer
        if frame.sample_rate != 16000 or frame.num_channels != 1:
            resampler = rtc.AudioResampler(
                input_rate=frame.sample_rate, output_rate=16000, num_channels=frame.num_channels,
            )
            parts = [bytes(f.data) for f in resampler.push(frame)] + [bytes(f.data) for f in resampler.flush()]
            pcm = b"".join(parts)
            if frame.num_channels > 1:            # keep channel 0
                import array
                a = array.array("h"); a.frombytes(pcm)
                pcm = a[::frame.num_channels].tobytes()
        else:
            pcm = bytes(frame.data)
        return _pcm16_to_wav_bytes(pcm, sample_rate=16000, num_channels=1), len(pcm) // 2

    @staticmethod
    def _normalise(pcm: bytes) -> tuple[bytes, float]:
        """Lift a quiet utterance to a -3 dBFS peak (at most +30 dB).

        A real call (2026-09-25 01:36) delivered a 2.5 s utterance at
        -60 dBFS RMS / -34 dBFS peak - near silence after the browser and
        noise filter - and Sarvam returned an empty transcript. Speech at
        that level is still intelligible once amplified; the gain cap keeps
        pure background hiss from being blown up into "speech".
        Returns (pcm, gain_db). Loud audio is never attenuated.
        """
        import array, math
        a = array.array("h"); a.frombytes(pcm[: len(pcm) // 2 * 2])
        if not a:
            return pcm, 0.0
        peak = max(abs(x) for x in a)
        if peak <= 0:
            return pcm, 0.0
        target = 32767 * 10 ** (-3 / 20)
        gain = min(target / peak, 10 ** (float(os.getenv("SARVAM_STT_MAX_GAIN_DB", "30")) / 20))
        if gain <= 1.05:
            return pcm, 0.0
        out = array.array("h", (max(-32768, min(32767, int(x * gain))) for x in a))
        return out.tobytes(), round(20 * math.log10(gain), 1)

    async def _recognize_impl(
        self,
        buffer: AudioBuffer,
        *,
        language: str | None = None,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> stt.SpeechEvent:
        # The framework passes hints like "en" or NOT_GIVEN; an unknown code
        # makes Sarvam silent, so anything unrecognised uses the call's own.
        lang = language.strip() if isinstance(language, str) and language.strip() in SarvamSTT._VALID_LANGS \
            else self._language_code
        wav, samples = self._to_wav16k(buffer)
        raw_pcm = wav[44:44 + samples * 2]
        gain_db = 0.0
        if _env_bool("SARVAM_STT_NORMALIZE", True):
            norm_pcm, gain_db = self._normalise(raw_pcm)
            if gain_db:
                wav = _pcm16_to_wav_bytes(norm_pcm, sample_rate=16000, num_channels=1)
        form = aiohttp.FormData()
        form.add_field("file", wav, filename="utterance.wav", content_type="audio/wav")
        form.add_field("model", self._model)
        form.add_field("language_code", lang)
        if self._mode:
            form.add_field("mode", self._mode)
        started = time.perf_counter()
        try:
            async with self._http().post(
                self._base_url + self._ENDPOINT, data=form,
                headers={"api-subscription-key": self._api_key},
                timeout=aiohttp.ClientTimeout(total=self._timeout),
            ) as resp:
                body = await resp.text()
                if resp.status != 200:
                    raise APIStatusError(
                        f"Sarvam STT {resp.status}", status_code=resp.status,
                        body=body[:300], retryable=resp.status >= 500 or resp.status == 429,
                    )
        except (APIStatusError, APIConnectionError):
            raise
        except asyncio.TimeoutError as e:
            raise APIConnectionError("Sarvam STT request timed out") from e
        except aiohttp.ClientError as e:
            raise APIConnectionError(f"Sarvam STT request failed: {e}") from e

        try:
            data = json.loads(body)
        except ValueError as e:
            raise APIConnectionError("Sarvam STT returned non-JSON") from e
        transcript = (data.get("transcript") or "").strip()

        if _env_bool("SAKHI_STT_DEBUG", True):
            import array
            a = array.array("h"); a.frombytes(raw_pcm)      # level BEFORE normalisation
            sumsq = float(sum(x * x for x in a)); peak = max((abs(x) for x in a), default=0)
            _emit_stt_debug({
                "stt_provider": "sarvam-batch",
                "stt_model": self._model,
                "stt_mode": self._mode,
                "stt_language": lang,
                "stt_utterance_id": data.get("request_id"),
                "is_final": True,
                "transcript_length": len(transcript),
                "transcript_hash": hashlib.sha256(transcript.encode("utf-8")).hexdigest()[:12],
                "stt_confidence": None,
                "audio_sample_rate": 16000,
                "audio_channels": 1,
                "audio_duration_ms": round(samples / 16.0, 1),
                "audio_rms_dbfs": _dbfs(sumsq, len(a)),
                "audio_peak_dbfs": _dbfs(float(peak) ** 2, 1),
                "stt_latency_ms": round((time.perf_counter() - started) * 1000, 1),
                "segmentation": "livekit_vad",
                "gain_db": gain_db,
            })

        # Speech was detected but nothing came back: tell the agent, so the
        # ASHA worker hears "I didn't catch that" instead of silence.
        if not transcript and samples >= 16000 * float(os.getenv("STT_EMPTY_MIN_SECONDS", "0.8")):
            sink = STT_EMPTY_SINK
            if sink is not None:
                try:
                    sink({"audio_duration_ms": round(samples / 16.0, 1), "gain_db": gain_db})
                except Exception:
                    logger.debug("STT empty sink failed", exc_info=True)

        return stt.SpeechEvent(
            type=stt.SpeechEventType.FINAL_TRANSCRIPT,
            request_id=data.get("request_id") or "",
            alternatives=[stt.SpeechData(text=transcript, language=lang, confidence=0.99)],
        )


class SarvamSpeechStream(stt.SpeechStream):
    # Batch mic audio to ~100ms chunks before shipping to Sarvam; sending every 10ms
    # frame as its own base64 WAV message floods the socket and hurts latency.
    _SEND_CHUNK_BYTES = 3200  # 100ms @ 16kHz mono s16le

    def __init__(
        self,
        stt_instance: SarvamSTT,
        api_key: str,
        model: str,
        language_code: str,
        base_url: str,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
        *,
        mode: str = "transcribe",
        current_protocol: bool = True,
    ):
        super().__init__(stt=stt_instance, conn_options=conn_options)
        self._api_key = api_key
        self._model = model
        self._language_code = language_code
        self._base_url = base_url
        self._mode = mode
        self._current_protocol = current_protocol

    async def _run(self) -> None:
        uri = _ws_url(
            self._base_url,
            "/speech-to-text/ws",
            _sarvam_stt_query(
                model=self._model,
                language_code=self._language_code,
                mode=self._mode,
                current_protocol=self._current_protocol,
            ),
        )
        headers = _ws_headers(self._api_key)
        resampler: rtc.AudioResampler | None = None
        resampler_rate: int | None = None
        pending = bytearray()
        segment = _SegmentAudio()
        debug_on = _env_bool("SAKHI_STT_DEBUG", True)
        stream_started = time.monotonic()
        input_format = {"rate": None, "channels": None}
        vad_marks = {"start": None, "end": None}
        utterance_no = 0

        try:
            logger.info("Connecting to Sarvam STT WebSocket: %s", _redact_url(uri))
            async with _websocket_connect(uri, headers) as websocket:
                # Saaras v3/v4 configure the stream entirely through query
                # parameters.  A separate config message belongs to the old
                # translate protocol and is silently ignored by the current
                # endpoint.  Only send it when the rollback switch is active.
                if not self._current_protocol:
                    config_msg = {
                        "type": "config",
                        "data": {
                            "model": self._model,
                            "mode": self._mode,
                            "language_code": self._language_code,
                        },
                    }
                    await websocket.send(json.dumps(config_msg))

                async def receive_loop():
                    nonlocal utterance_no
                    async for message in websocket:
                        resp = json.loads(message)
                        logger.debug("Sarvam STT received: %s", resp)

                        if resp.get("type") in {"error", "ERROR"} or "error" in resp:
                            raise APIStatusError(
                                "Sarvam STT rejected the stream.",
                                status_code=-1,
                                body=resp,
                                retryable=False,
                            )

                        # With vad_signals=true Sarvam marks the provider-side
                        # end of speech.  Flush immediately so short Kannada
                        # answers do not remain buffered forever.  This is the
                        # condition seen in the incident: local VAD ended the
                        # turn but the provider socket stayed open and silent.
                        data = resp.get("data") if isinstance(resp.get("data"), dict) else {}
                        signal = data.get("signal_type")
                        if signal == "START_SPEECH":
                            vad_marks["start"] = round(time.monotonic() - stream_started, 3)
                        elif signal == "END_SPEECH":
                            vad_marks["end"] = round(time.monotonic() - stream_started, 3)
                        if (
                            self._current_protocol
                            and resp.get("type") in {"event", "events"}
                            and data.get("signal_type") == "END_SPEECH"
                            and _env_bool("SARVAM_STT_FLUSH_SIGNAL_ENABLED", True)
                        ):
                            await websocket.send(json.dumps(_sarvam_stt_flush_message()))

                        transcript = ""
                        is_final = False
                        if "transcript" in resp:
                            transcript = resp["transcript"]
                            is_final = resp.get("is_final", False)
                        elif "data" in resp and isinstance(resp["data"], dict) and "transcript" in resp["data"]:
                            transcript = resp["data"]["transcript"]
                            # Verified against the live API (saarika:v2.5 ws): the server
                            # runs its own VAD and emits one type=="data" message per
                            # finished speech segment, with NO is_final flag. Treat those
                            # as FINAL — otherwise the agent only ever sees interims and
                            # the turn never completes.
                            is_final = bool(resp["data"].get("is_final", resp.get("type") == "data"))

                        if transcript and debug_on:
                            utterance_no += 1
                            _emit_stt_debug({
                                "stt_provider": "sarvam",
                                "stt_model": self._model,
                                "stt_mode": self._mode,
                                "stt_language": self._language_code,
                                "stt_utterance_id": data.get("request_id") or f"seg-{utterance_no}",
                                "is_final": bool(is_final),
                                "transcript_length": len(transcript),
                                "transcript_hash": hashlib.sha256(transcript.encode("utf-8")).hexdigest()[:12],
                                "stt_confidence": data.get("confidence"),
                                "audio_sample_rate": input_format["rate"],
                                "audio_channels": input_format["channels"],
                                "audio_duration_ms": round(segment.samples / 16.0, 1),
                                "audio_rms_dbfs": _dbfs(segment.sumsq, segment.samples),
                                "audio_peak_dbfs": _dbfs(float(segment.peak) ** 2, 1),
                                "vad_start_s": vad_marks["start"],
                                "vad_end_s": vad_marks["end"],
                                "high_vad_sensitivity": _env_bool("SARVAM_STT_HIGH_VAD_SENSITIVITY", False),
                            })
                            if is_final:
                                segment.reset()

                        if transcript:
                            event_type = (
                                stt.SpeechEventType.FINAL_TRANSCRIPT
                                if is_final
                                else stt.SpeechEventType.INTERIM_TRANSCRIPT
                            )
                            self._event_ch.send_nowait(
                                stt.SpeechEvent(
                                    type=event_type,
                                    alternatives=[
                                        stt.SpeechData(
                                            text=transcript,
                                            language=self._language_code,
                                            confidence=0.99,
                                        )
                                    ],
                                )
                            )

                async def send_pending(force: bool = False):
                    nonlocal pending
                    if not pending or (not force and len(pending) < self._SEND_CHUNK_BYTES):
                        return
                    pcm = bytes(pending)
                    pending = bytearray()
                    if debug_on:
                        segment.add(pcm)
                    audio_msg = _sarvam_stt_audio_message(
                        pcm, current_protocol=self._current_protocol
                    )
                    await websocket.send(json.dumps(audio_msg))

                recv_task = asyncio.create_task(receive_loop())

                try:
                    async for item in self._input_ch:
                        if recv_task.done():
                            recv_task.result()

                        if isinstance(item, self._FlushSentinel):
                            await send_pending(force=True)
                            if (
                                self._current_protocol
                                and _env_bool("SARVAM_STT_FLUSH_SIGNAL_ENABLED", True)
                            ):
                                await websocket.send(json.dumps(_sarvam_stt_flush_message()))
                            continue

                        frame = item
                        if resampler is None or resampler_rate != frame.sample_rate:
                            resampler_rate = frame.sample_rate
                            input_format["rate"] = frame.sample_rate
                            input_format["channels"] = frame.num_channels
                            resampler = rtc.AudioResampler(
                                input_rate=frame.sample_rate,
                                output_rate=16000,
                                num_channels=frame.num_channels,
                            )
                            logger.info(
                                "STT Resampler initialized: %sHz -> 16000Hz, channels: %s",
                                frame.sample_rate,
                                frame.num_channels,
                            )

                        for r_frame in resampler.push(frame):
                            pending.extend(bytes(r_frame.data))
                        await send_pending()
                    await send_pending(force=True)
                finally:
                    recv_task.cancel()
                    try:
                        await recv_task
                    except asyncio.CancelledError:
                        pass
        except (APIConnectionError, APIStatusError):
            raise
        except Exception as e:
            logger.error("Error in Sarvam STT stream _run", exc_info=True)
            raise _to_api_error("Sarvam STT stream failed", e) from e


# Speakers accepted by each model, read straight off the live API's own 400
# response (2026-08-14). They do NOT overlap: sending a v3 speaker to v2 is a
# hard 400, which is how a "voice" picked in the dashboard could silently kill
# all audio for a call.
SARVAM_V2_SPEAKERS = {
    "anushka", "abhilash", "manisha", "vidya", "arya",
    "karun", "hitesh", "aditya", "ritu",
}

# Measured against the live API, same Kannada text, 2026-08-14:
#
#   bulbul:v2 manisha   short clause  438ms   full sentence   779ms
#   bulbul:v2 vidya     short clause  451ms   full sentence   764ms
#   bulbul:v2 anushka   short clause  700ms   full sentence  1026ms
#   bulbul:v3 pooja     short clause 1012ms   full sentence  2655ms
#
# bulbul:v2 -> bulbul:v3 (2026-09-08): v2 is no longer a default choice, it
# is GONE - confirmed live, the API now returns a 400 "Model 'bulbul:v2' has
# been deprecated. Please use 'bulbul:v3' instead." The v3-is-2.3-3.4x-slower
# comment this constant used to justify v2 as the pilot default is now moot;
# there is no v2 to fall back to. `bulbul:v1` was never an option either -
# the API has always rejected it ("model: Input should be 'bulbul:v2',
# 'bulbul:v3-beta' or 'bulbul:v3'").
SARVAM_DEFAULT_MODEL = "bulbul:v3"
# manisha -> shreya (2026-09-08): manisha is a bulbul:v2 speaker: resolve_
# sarvam_voice() below would have silently remapped it to 'ishita' for v3
# anyway (a bulbul:v2 name has no meaning post-deprecation). shreya is a
# confirmed-live bulbul:v3 speaker (real 200 + audio bytes back, 2026-09-08).
SARVAM_DEFAULT_SPEAKER = "shreya"
# v2's OWN fallback, kept separate from SARVAM_DEFAULT_SPEAKER above: v2 is
# deprecated but resolve_sarvam_voice() below still has to pair an invalid
# v2-speaker request with SOMETHING, and that something must actually be a
# v2 voice - reusing the (now bulbul:v3) SARVAM_DEFAULT_SPEAKER there was a
# real bug caught by this file's own test (shreya has no meaning to v2's
# API and would have 400'd).
SARVAM_V2_DEFAULT_SPEAKER = "manisha"

# Both models return 22050 Hz WAV from the REST endpoint (verified by reading the
# RIFF header). The previous `24000 if "v3" in model` guess declared a rate the
# API never sends, so the emitter was told 24kHz while being fed 22.05kHz - every
# v3 reply played ~9% fast and pitched up, and frame durations were wrong.
SARVAM_SAMPLE_RATE = 22050


def resolve_sarvam_voice(model: str, speaker: str | None) -> tuple[str, str]:
    """Pair a model with a speaker it actually supports.

    Returns (model, speaker). A speaker belonging to the other model family is
    remapped rather than passed through, because the API answers 400 and the
    caller hears nothing at all.
    """
    model = (model or SARVAM_DEFAULT_MODEL).strip()
    requested = (speaker or "").strip().lower()

    if model.startswith("bulbul:v2"):
        if requested in SARVAM_V2_SPEAKERS:
            return model, requested
        if requested:
            logger.warning(
                "Speaker %r is not a bulbul:v2 voice; using %r. Valid: %s",
                requested, SARVAM_V2_DEFAULT_SPEAKER, ", ".join(sorted(SARVAM_V2_SPEAKERS)),
            )
        return model, SARVAM_V2_DEFAULT_SPEAKER

    # v3 / v3-beta
    if requested in _SARVAM_V3_SPEAKERS:
        return model, requested
    if requested:
        logger.warning("Speaker %r is not a %s voice; using 'ishita'", requested, model)
    return model, "ishita"


class SarvamTTS(tts.TTS):
    def __init__(
        self,
        api_key: str,
        model: str = SARVAM_DEFAULT_MODEL,
        speaker: str = SARVAM_DEFAULT_SPEAKER,
        target_language_code: str = "en-IN",
        base_url: str = "https://api.sarvam.ai",
        pace: float = 1.05,
        min_buffer_size: int = 20,
        max_chunk_length: int = 160,
        output_audio_codec: str = "wav",
        enable_preprocessing: bool = False,
    ):
        model, speaker = resolve_sarvam_voice(model, speaker)
        super().__init__(
            # streaming=True by default (2026-09-25). The old claim that the
            # WebSocket endpoint "hangs and never returns audio" is out of
            # date: re-tested on the production host through this class's own
            # stream(), 3/3 runs completed with first audio in 385-484 ms vs
            # ~1.6 s for REST .synthesize(). tts_audio_cache.CachedTTS now
            # mirrors this flag and passes stream() straight through.
            # SARVAM_TTS_STREAMING=false restores StreamAdapter +
            # EagerClauseTokenizer over REST, one clause at a time.
            capabilities=tts.TTSCapabilities(
                streaming=_env_bool("SARVAM_TTS_STREAMING", True),
            ),
            sample_rate=SARVAM_SAMPLE_RATE,
            num_channels=1,
        )
        self._api_key = api_key
        self._model = model
        self._speaker = speaker
        self._target_language_code = target_language_code
        self._base_url = base_url
        self._pace = pace
        self._min_buffer_size = min_buffer_size
        self._max_chunk_length = max_chunk_length
        self._output_audio_codec = output_audio_codec
        # enable_preprocessing (2026-09-08): a real, documented Sarvam TTS
        # request field (text normalization - expanding abbreviations/
        # numbers before synthesis, distinct from this codebase's own
        # sakhi_kannada.py digit-to-words transform, which runs before the
        # request is even built). Confirmed the API accepts it (real 200 +
        # audio back) but this has NOT been A/B'd against this pipeline's
        # own pre-normalized input the way the voice/model choices above
        # were - default False to avoid double-processing text this
        # pipeline may have already normalized, opt in explicitly.
        self._enable_preprocessing = enable_preprocessing

    @property
    def ws_open_timeout(self) -> float:
        return float(os.getenv("SARVAM_TTS_TIMEOUT", "5.0"))

    @property
    def idle_timeout(self) -> float:
        # Fallback only: the stream normally ends on Sarvam's "final" event
        # (send_completion_event). 0.25 s was shorter than the measured
        # 235-249 ms gap between clauses and cut the last clause off.
        return float(os.getenv("SARVAM_TTS_IDLE_TIMEOUT", "1.5"))

    def config_message(self) -> str:
        payload = _sarvam_tts_config_payload(self)
        logger.debug(
            "Sarvam TTS config payload shape=%s",
            _payload_shape(payload),
        )
        return json.dumps(payload)

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> "SarvamTTSChunkedStream":
        return SarvamTTSChunkedStream(tts_instance=self, text=text, conn_options=conn_options)

    def stream(
        self,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> "SarvamTTSSynthesizeStream":
        return SarvamTTSSynthesizeStream(tts_instance=self, conn_options=conn_options)


async def _run_sarvam_tts_ws(
    *,
    tts_instance: SarvamTTS,
    output_emitter: tts.AudioEmitter,
    input_aiter,
    conn_options: APIConnectOptions,
    mark_started,
) -> None:
    """Shared Sarvam TTS websocket session.

    `input_aiter` yields str tokens to synthesize and None to force a flush.
    Audio is pushed to `output_emitter` as raw PCM as soon as it arrives.
    Completes when the input ends AND the server has drained all audio
    (explicit completion message, socket close, or idle timeout after flush).
    """
    # send_completion_event=true: Sarvam then sends {"type": "event", "data":
    # {"event_type": "final"}} right after the last audio of each flush.
    # Without it no end marker arrives and the stream can only end on
    # silence - measured 2026-09-25, the gap between clauses (235-249 ms) is
    # as long as the old 0.25 s idle cut-off, which dropped the final clause
    # (the question) in 1 of 4 runs.
    uri = _ws_url(
        tts_instance._base_url,
        "/text-to-speech/ws",
        {"model": tts_instance._model, "send_completion_event": "true"},
    )
    headers = _ws_headers(tts_instance._api_key)
    open_timeout = min(float(getattr(conn_options, "timeout", 10.0) or 10.0), tts_instance.ws_open_timeout)
    recv_timeout = float(getattr(conn_options, "timeout", 10.0) or 10.0)
    idle_timeout = tts_instance.idle_timeout

    logger.info(
        "Connecting to Sarvam TTS WebSocket: %s speaker=%s language=%s pace=%s",
        _redact_url(uri),
        tts_instance._speaker,
        tts_instance._target_language_code,
        tts_instance._pace,
    )

    async with _websocket_connect(uri, headers, open_timeout=open_timeout) as websocket:
        await websocket.send(tts_instance.config_message())

        input_done = asyncio.Event()
        fatal_tts_error = asyncio.Event()
        last_payload_shape = {"type": "none"}
        # One "final" event arrives per flush; the stream is complete only once
        # every flush has been answered, not at the first final.
        flush_counts = {"sent": 0, "final": 0}

        async def send_loop() -> None:
            # Sarvam rejects text messages with no letters/digits with a fatal
            # "400: Text must contain at least one character from the allowed
            # languages" — so punctuation/whitespace-only tokens are buffered
            # until they can ride along with speakable text.
            buffer = ""
            pending_since_flush = False

            def should_send_buffer(force: bool = False) -> bool:
                stripped = buffer.strip()
                if not stripped or not _SPEAKABLE_RE.search(stripped):
                    return False
                return True

            async def send_buffer(force: bool = False) -> bool:
                nonlocal buffer, pending_since_flush, last_payload_shape
                if fatal_tts_error.is_set():
                    buffer = ""
                    return False
                if not should_send_buffer(force=force):
                    return False
                payload = _sarvam_tts_text_payload(
                    buffer,
                    max_chars=tts_instance._max_chunk_length,
                )
                if payload is None:
                    buffer = ""
                    return False
                mark_started()
                text = payload["data"]["text"]
                last_payload_shape = _payload_shape(payload)
                logger.debug(
                    "Sarvam TTS sending text payload shape=%s text_len=%s",
                    last_payload_shape,
                    len(text),
                )
                await websocket.send(json.dumps(payload))
                buffer = ""
                pending_since_flush = True
                return True

            try:
                async for item in input_aiter:
                    if fatal_tts_error.is_set():
                        buffer = ""
                        break
                    if item is None:
                        await send_buffer(force=True)
                        buffer = ""
                        if pending_since_flush:
                            payload = _sarvam_tts_flush_payload()
                            last_payload_shape = _payload_shape(payload)
                            logger.debug("Sarvam TTS sending flush payload shape=%s", last_payload_shape)
                            await websocket.send(json.dumps(payload))
                            flush_counts["sent"] += 1
                            pending_since_flush = False
                        continue
                    if not item:
                        continue
                    buffer += str(item)
                    await send_buffer(force=False)
                await send_buffer(force=True)
                if pending_since_flush:
                    payload = _sarvam_tts_flush_payload()
                    last_payload_shape = _payload_shape(payload)
                    logger.debug("Sarvam TTS sending flush payload shape=%s", last_payload_shape)
                    await websocket.send(json.dumps(payload))
                    flush_counts["sent"] += 1
            finally:
                input_done.set()

        async def recv_loop() -> None:
            received_audio = False
            while True:
                timeout = idle_timeout if (input_done.is_set() and received_audio) else recv_timeout
                try:
                    message = await asyncio.wait_for(websocket.recv(), timeout=timeout)
                except asyncio.TimeoutError:
                    if input_done.is_set():
                        if received_audio:
                            return  # server went idle after flush: segment complete
                        raise APIConnectionError("Sarvam TTS produced no audio before timing out.") from None
                    continue  # input still streaming in; keep waiting
                except websockets.exceptions.ConnectionClosedOK:
                    return
                except websockets.exceptions.ConnectionClosedError as exc:
                    if received_audio and input_done.is_set():
                        return
                    raise _to_api_error("Sarvam TTS websocket closed unexpectedly", exc) from exc

                resp = json.loads(message)
                rtype = str(resp.get("type", "")).lower()
                logger.debug("Sarvam TTS received type=%s", rtype)

                if rtype == "error" or "error" in resp:
                    data = resp.get("data") if isinstance(resp.get("data"), dict) else {}
                    code = int(data.get("code", -1) or -1)
                    message = str(data.get("message") or resp.get("error") or "Sarvam TTS error")
                    logger.warning(
                        "Sarvam TTS rejected stream code=%s message=%s last_payload_shape=%s",
                        code,
                        message,
                        last_payload_shape,
                    )
                    # Treat schema/validation failures as recoverable at the
                    # AgentSession level: skip this failed chunk and keep the call
                    # alive instead of raising a non-recoverable tts_error.
                    if code == 422:
                        fatal_tts_error.set()
                        return
                    raise APIStatusError(
                        "Sarvam TTS rejected the stream.",
                        status_code=code,
                        body={
                            "type": resp.get("type"),
                            "data": {
                                "code": code,
                                "message": message,
                                "request_id": data.get("request_id"),
                            },
                            "last_payload_shape": last_payload_shape,
                        },
                        retryable=True,
                    )

                if rtype == "audio":
                    audio_bytes = _strip_wav_header(base64.b64decode(resp["data"]["audio"]))
                    if audio_bytes:
                        received_audio = True
                        output_emitter.push(audio_bytes)
                elif rtype == "event":
                    data = resp.get("data") if isinstance(resp.get("data"), dict) else {}
                    if str(data.get("event_type", "")).lower() == "final":
                        flush_counts["final"] += 1
                        if input_done.is_set() and flush_counts["final"] >= flush_counts["sent"]:
                            return
                elif rtype in _TTS_COMPLETION_TYPES:
                    if input_done.is_set():
                        return
                else:
                    # Surface unrecognized server events once — if Sarvam sends an
                    # explicit synthesis-complete event we can key off it instead
                    # of the idle timeout.
                    if rtype not in _seen_unknown_types:
                        _seen_unknown_types.add(rtype)
                        logger.info("Sarvam TTS unrecognized message type: %s payload=%s", rtype, str(resp)[:300])

        send_task = asyncio.create_task(send_loop())
        recv_task = asyncio.create_task(recv_loop())
        try:
            done, pending = await asyncio.wait(
                {send_task, recv_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                task.result()
            if fatal_tts_error.is_set():
                return
            if input_done.is_set() and not recv_task.done():
                await recv_task
            elif recv_task.done() and not send_task.done():
                send_task.cancel()
                await asyncio.gather(send_task, return_exceptions=True)
            else:
                await asyncio.gather(send_task, recv_task)
        finally:
            for task in (send_task, recv_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(send_task, recv_task, return_exceptions=True)


class SarvamTTSSynthesizeStream(tts.SynthesizeStream):
    """Streaming TTS honoring the LiveKit SynthesizeStream contract.

    Text arrives via the base class `_input_ch` (fed by push_text/flush/end_input);
    do NOT override those methods — the agent framework relies on them.
    """

    def __init__(self, *, tts_instance: SarvamTTS, conn_options: APIConnectOptions):
        super().__init__(tts=tts_instance, conn_options=conn_options)
        self._sarvam_tts = tts_instance

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        request_id = f"sarvam-tts-{uuid.uuid4().hex[:12]}"
        output_emitter.initialize(
            request_id=request_id,
            sample_rate=self._sarvam_tts.sample_rate,
            num_channels=1,
            mime_type="audio/pcm",
            stream=True,
        )
        output_emitter.start_segment(segment_id=request_id)

        async def _input_aiter():
            async for item in self._input_ch:
                if isinstance(item, self._FlushSentinel):
                    yield None
                else:
                    yield item

        try:
            await _run_sarvam_tts_ws(
                tts_instance=self._sarvam_tts,
                output_emitter=output_emitter,
                input_aiter=_input_aiter(),
                conn_options=self._conn_options,
                mark_started=self._mark_started,
            )
            output_emitter.end_segment()
        except (APIConnectionError, APIStatusError):
            raise
        except Exception as e:
            logger.error("Error in Sarvam TTS stream _run", exc_info=True)
            raise _to_api_error("Sarvam TTS stream failed", e) from e


class SarvamTTSChunkedStream(tts.ChunkedStream):
    def __init__(self, *, tts_instance: SarvamTTS, text: str, conn_options: APIConnectOptions):
        super().__init__(tts=tts_instance, input_text=text, conn_options=conn_options)
        self._sarvam_tts = tts_instance

    def __await__(self):
        async def _collect():
            # Compatibility for component diagnostics that await synthesize()
            # directly. LiveKit itself consumes this object as an async
            # iterator, so this does not alter the framework contract.
            frames = []
            async for event in self:
                frame_data = event.frame.data
                frames.append(
                    frame_data.tobytes()
                    if hasattr(frame_data, "tobytes")
                    else bytes(frame_data)
                )
            if not frames:
                return None
            return type(
                "PCM16AudioResult",
                (),
                {
                    "data": b"".join(frames),
                    "sample_rate": self._sarvam_tts.sample_rate,
                    "num_channels": self._sarvam_tts.num_channels,
                },
            )()
        return _collect().__await__()

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        try:
            from livekit.agents import utils
            clean_input = sanitize_kannada_tts(self._input_text)
            if not clean_input or not _SPEAKABLE_RE.search(clean_input):
                raise APIConnectionError("Sarvam TTS received no speakable text")

            payload = _sarvam_tts_rest_payload(self._sarvam_tts, clean_input)
            headers = {
                "api-subscription-key": self._sarvam_tts._api_key,
                "Content-Type": "application/json",
            }
            url = f"{self._sarvam_tts._base_url}/text-to-speech"
            logger.info(f"[SARVAM TTS] Synthesizing: {clean_input}")

            timeout = aiohttp.ClientTimeout(
                total=float(os.getenv("SARVAM_TTS_HTTP_TIMEOUT", "3.0"))
            )
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, headers=headers, json=payload) as resp:
                    if resp.status != 200:
                        detail = (await resp.text())[:300]
                        logger.error("[SARVAM TTS ERROR] Status %s: %s", resp.status, detail)
                        raise APIStatusError(
                            f"Sarvam TTS {resp.status}: {detail}",
                            status_code=resp.status,
                        )
                    data = await resp.json()

            audios = data.get("audios", []) if isinstance(data, dict) else []
            if not audios or not audios[0]:
                raise APIConnectionError("Sarvam returned empty audio buffer")

            try:
                audio_bytes = base64.b64decode(audios[0], validate=True)
            except Exception as exc:
                raise APIConnectionError("Sarvam returned invalid base64 audio") from exc

            # Sarvam REST returns a WAV container. AudioEmitter requires only
            # the underlying signed 16-bit PCM frames; forwarding the RIFF
            # bytes makes the beginning of the buffer invalid audio.
            try:
                with io.BytesIO(audio_bytes) as wav_io:
                    with wave.open(wav_io, "rb") as wav_file:
                        actual_rate = wav_file.getframerate()
                        num_channels = wav_file.getnchannels()
                        sample_width = wav_file.getsampwidth()
                        compression = wav_file.getcomptype()
                        pcm_bytes = wav_file.readframes(wav_file.getnframes())
            except (EOFError, wave.Error) as exc:
                raise APIConnectionError("Sarvam returned malformed WAV audio") from exc

            expected_rate = self._sarvam_tts.sample_rate
            if actual_rate != expected_rate or num_channels != 1 or sample_width != 2:
                raise APIConnectionError(
                    "Sarvam audio format mismatch: "
                    f"expected PCM16 mono {expected_rate}Hz, got "
                    f"{sample_width * 8}-bit {num_channels}ch {actual_rate}Hz"
                )
            if compression != "NONE":
                raise APIConnectionError(
                    f"Sarvam returned unsupported WAV compression {compression}"
                )
            if not pcm_bytes or len(pcm_bytes) % 2:
                raise APIConnectionError("Sarvam returned empty or unaligned PCM16 audio")

            request_id = f"sarvam-tts-{utils.shortuuid()}"
            output_emitter.initialize(
                request_id=request_id,
                sample_rate=expected_rate,
                num_channels=1,
                mime_type="audio/pcm",
                stream=False,
            )

            logger.info(
                f"[SARVAM TTS] Synthesized {len(pcm_bytes)} PCM bytes successfully at {actual_rate}Hz"
            )

            output_emitter.push(pcm_bytes)
            output_emitter.flush()

        except (APIConnectionError, APIStatusError):
            raise
        except asyncio.TimeoutError as e:
            logger.warning("Sarvam TTS timed out; failing over")
            raise APIConnectionError("Sarvam TTS timed out") from e
        except Exception as e:
            logger.error("Error in SarvamTTSChunkedStream._run", exc_info=True)
            raise _to_api_error("Sarvam TTS request failed", e) from e


class SafeSarvamRESTTTS(SarvamTTS):
    """Direct, robust REST-based Sarvam TTS provider for LiveKit.
    Pushes valid linear PCM audio frames without deadlocking or WebSocket hanging.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "bulbul:v3",
        speaker: str = "shreya",
        target_language_code: str = "kn-IN",
        base_url: str = "https://api.sarvam.ai",
        pace: float = 0.95,
        **kwargs,
    ):
        key = api_key or os.getenv("SARVAM_API_KEY", "")
        super().__init__(
            api_key=key,
            model=model,
            speaker=speaker,
            target_language_code=target_language_code,
            base_url=base_url,
            pace=pace,
            **kwargs,
        )
        # REST-only by name and contract: never inherit SARVAM_TTS_STREAMING.
        self._capabilities = tts.TTSCapabilities(streaming=False)
