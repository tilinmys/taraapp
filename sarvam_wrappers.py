import aiohttp
import asyncio
import base64
import inspect
import io
import json
import logging
import os
import uuid
import wave
from urllib.parse import urlencode, urlparse

import re

import websockets
from livekit import rtc
from livekit.agents import APIConnectionError, APIStatusError, stt, tts
from livekit.agents.types import APIConnectOptions, DEFAULT_API_CONNECT_OPTIONS
from livekit.agents.utils import AudioBuffer

logger = logging.getLogger("sarvam_wrappers")

# Server message types that mean "all audio for the flushed text has been sent".
_TTS_COMPLETION_TYPES = {"flush_complete", "flushed", "done", "complete", "end", "eos"}
_seen_unknown_types: set[str] = set()

# At least one letter or digit (any script) — the minimum Sarvam will accept.
_SPEAKABLE_RE = re.compile(r"[A-Za-z0-9\u0900-\u097F]", re.UNICODE)
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


def _normalize_tts_text(text: str, *, max_chars: int) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if not normalized or not _SPEAKABLE_RE.search(normalized):
        return ""
    return normalized[:max_chars].strip()


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


def _websocket_connect(uri: str, headers: dict[str, str], *, open_timeout: float | None = None):
    connect_params = inspect.signature(websockets.connect).parameters
    header_kw = "additional_headers" if "additional_headers" in connect_params else "extra_headers"
    kwargs = {header_kw: headers}
    if open_timeout is not None and "open_timeout" in connect_params:
        kwargs["open_timeout"] = open_timeout
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
        model: str = "saarika:v2.5",
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
            mode=self._mode,
        )

    async def _recognize_impl(
        self,
        buffer: AudioBuffer,
        *,
        language: str | None = None,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> stt.SpeechEvent:
        raise NotImplementedError("SarvamSTT only supports streaming STT.")


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
    ):
        super().__init__(stt=stt_instance, conn_options=conn_options)
        self._api_key = api_key
        self._model = model
        self._language_code = language_code
        self._base_url = base_url
        self._mode = mode

    async def _run(self) -> None:
        uri = _ws_url(
            self._base_url,
            "/speech-to-text/ws",
            {"language-code": self._language_code, "model": self._model},
        )
        headers = _ws_headers(self._api_key)
        resampler: rtc.AudioResampler | None = None
        resampler_rate: int | None = None
        pending = bytearray()

        try:
            logger.info("Connecting to Sarvam STT WebSocket: %s", _redact_url(uri))
            async with _websocket_connect(uri, headers) as websocket:
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
                    wav_bytes = _pcm16_to_wav_bytes(bytes(pending), sample_rate=16000, num_channels=1)
                    pending = bytearray()
                    audio_msg = {
                        "type": "audio",
                        "audio": {
                            "data": base64.b64encode(wav_bytes).decode("ascii"),
                            "encoding": "audio/wav",
                            "sample_rate": 16000,
                        },
                    }
                    await websocket.send(json.dumps(audio_msg))

                recv_task = asyncio.create_task(receive_loop())

                try:
                    async for item in self._input_ch:
                        if recv_task.done():
                            recv_task.result()

                        if isinstance(item, self._FlushSentinel):
                            await send_pending(force=True)
                            continue

                        frame = item
                        if resampler is None or resampler_rate != frame.sample_rate:
                            resampler_rate = frame.sample_rate
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
        min_buffer_size: int = 35,
        max_chunk_length: int = 160,
        output_audio_codec: str = "wav",
        enable_preprocessing: bool = False,
    ):
        model, speaker = resolve_sarvam_voice(model, speaker)
        super().__init__(
            # streaming=False on purpose.
            #
            # The WebSocket endpoint (wss://api.sarvam.ai/text-to-speech/ws) was
            # probed directly on 2026-08-14 and NEVER returned audio for either
            # model - it connects, accepts config/text/flush, then hangs until
            # the client gives up. That is the 11-second dead-air timeout.
            #
            # Declaring the provider non-streaming makes AgentSession route
            # through tts.StreamAdapter, which feeds the REST endpoint one
            # clause at a time using EagerClauseTokenizer (already wired in
            # SakhiAgent.tts_node, and only active for non-streaming providers).
            # The caller therefore hears the first clause after ~440ms instead of
            # waiting on a socket that never answers.
            capabilities=tts.TTSCapabilities(
                streaming=False,
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
        # After the final flush, how long to wait with no server message before
        # considering the segment fully synthesized. Live calls showed Sarvam can
        # pause >1s between synthesis batches on longer replies — 1.0s truncated
        # audible words (132 chars -> 3.8s audio), so keep this generous. The tail
        # wait overlaps playback of already-buffered audio, so callers never hear it.
        return float(os.getenv("SARVAM_TTS_IDLE_TIMEOUT", "3.0"))

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
    uri = _ws_url(tts_instance._base_url, "/text-to-speech/ws", {"model": tts_instance._model})
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

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        try:
            from livekit.agents import utils
            request_id = f"sarvam-tts-{utils.shortuuid()}"
            output_emitter.initialize(
                request_id=request_id,
                sample_rate=self._sarvam_tts.sample_rate,
                num_channels=1,
                mime_type="audio/pcm",
                stream=False,
            )

            payload = _sarvam_tts_rest_payload(self._sarvam_tts, self._input_text)
            headers = {
                "api-subscription-key": self._sarvam_tts._api_key,
                "Content-Type": "application/json"
            }
            # Hard 3s ceiling per clause. Measured clause synthesis is ~440ms on
            # bulbul:v2, so 3s is ~7x headroom while still failing fast enough
            # for the TTS FallbackAdapter to reach OpenAI before the caller
            # notices a gap. Without an explicit timeout aiohttp waits
            # indefinitely, which is how a slow synthesis became dead air.
            timeout = aiohttp.ClientTimeout(
                total=float(os.getenv("SARVAM_TTS_HTTP_TIMEOUT", "3.0"))
            )
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{self._sarvam_tts._base_url}/text-to-speech",
                    json=payload,
                    headers=headers
                ) as resp:
                    if resp.status != 200:
                        # Surface the body: Sarvam explains speaker/model
                        # mismatches here, and a silent failure is the worst
                        # possible outcome on a clinical call.
                        detail = (await resp.text())[:300]
                        raise APIStatusError(
                            f"Sarvam TTS {resp.status}: {detail}",
                            status_code=resp.status,
                        )
                    data = await resp.json()
                    audios = data.get("audios", [])

                    if not audios or not audios[0]:
                        # Raise rather than return: an empty body must trigger
                        # the fallback chain, not produce a silent turn.
                        raise APIConnectionError("Sarvam TTS returned no audio")

                # Decode Base64 WAV from Sarvam
                wav_bytes = base64.b64decode(audios[0])

                # Extract raw PCM frames from the WAV container
                with io.BytesIO(wav_bytes) as wav_io:
                    with wave.open(wav_io, 'rb') as wav_file:
                        actual_rate = wav_file.getframerate()
                        raw_pcm = wav_file.readframes(wav_file.getnframes())

                # The emitter was initialized with the declared sample rate. If
                # the API ever returns a different one the audio plays at the
                # wrong speed and pitch instead of failing visibly - so say so.
                if actual_rate != self._sarvam_tts.sample_rate:
                    logger.warning(
                        "Sarvam returned %dHz but the provider declares %dHz; "
                        "playback will be pitch-shifted. Update SARVAM_SAMPLE_RATE.",
                        actual_rate, self._sarvam_tts.sample_rate,
                    )

                # Push valid audio bytes to LiveKit's AudioEmitter
                output_emitter.push(raw_pcm)
                output_emitter.flush()

        except (APIConnectionError, APIStatusError):
            # Already the right type - let it reach the FallbackAdapter.
            raise
        except asyncio.TimeoutError as e:
            # The whole point of the 3s ceiling: convert a hang into a fast,
            # typed failure the fallback chain can act on.
            logger.warning("Sarvam TTS timed out; failing over")
            raise APIConnectionError("Sarvam TTS timed out") from e
        except Exception as e:
            # This previously logged and returned, swallowing the failure. The
            # emitter then produced no audio, AgentSession saw a "successful"
            # empty synthesis, and the fallback chain never fired - the caller
            # just got silence. Every failure must propagate.
            logger.error("Error in SarvamTTSChunkedStream._run", exc_info=True)
            raise _to_api_error("Sarvam TTS request failed", e) from e
