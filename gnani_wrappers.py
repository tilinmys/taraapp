"""Gnani.ai (Vachana / Timbre) TTS provider for LiveKit.

Second Kannada voice option alongside Sarvam, selectable per call so the pilot
can A/B them with real ASHA workers rather than guessing from a spec sheet.

WHICH API THIS TALKS TO
-----------------------
Gnani exposes several surfaces and only one of them is right here:

  * `api.vachana.ai/api/v1/tts/inference`  <- THIS ONE. Model `timbre-v2.5`,
    header `X-API-Key-ID`, returns binary WAV.
  * `api.vachana.ai/api/v1/tts/sse` with `vachana-voice-v3` - DEPRECATED. It
    authenticates and validates voice names, then fails every synthesis with
    `502 "We are facing technical difficulties"`. Reproduced across 6 voices,
    3 languages, 4 payload shapes and 5 retries with backoff. If you see that
    error, the model is the cause, not the network.
  * `api.inya.ai/platform/v1/...` - the Agent Builder platform, a different
    product with a different key namespace. A `vach_*` key gets 401 there.

WHY NON-STREAMING
-----------------
`/tts/inference` returns the whole WAV in one response, so the provider
declares `streaming=False` and AgentSession routes it through
`tts.StreamAdapter` + `EagerClauseTokenizer` - one request per clause, first
audio after roughly one clause of synthesis. Exactly the same shape as the
Sarvam REST path, so the two are directly comparable and swapping one for the
other changes nothing else in the pipeline.
"""

from __future__ import annotations

import io
import json
import logging
import os
import wave

import aiohttp
from livekit.agents import APIConnectionError, APIStatusError, tts
from livekit.agents.types import APIConnectOptions, DEFAULT_API_CONNECT_OPTIONS

logger = logging.getLogger("gnani_wrappers")

GNANI_BASE_URL = "https://api.vachana.ai"
GNANI_DEFAULT_MODEL = "timbre-v2.5"

# Matched to Sarvam's output rate so switching provider mid-deployment does not
# change the track's sample rate. 48000 is available and sounds no better over a
# rural voice call, while costing bandwidth an ASHA worker's connection may not
# have.
GNANI_SAMPLE_RATE = 22050

# The Timbre catalogue is 73 voices with no per-language mapping exposed by the
# API - `language` is a separate request field, so voices are multilingual.
# These are the female voices carrying Kannada/South-Indian names, i.e. the ones
# most likely to sound native rather than accented in kn-IN. Benchmarked
# 2026-08-17; see logs/voice_samples/.
GNANI_FEMALE_KANNADA_VOICES = {
    "Janaki":  {"name": "Janaki",  "gender": "female", "style": "warm, steady - fastest measured"},
    "Nayana":  {"name": "Nayana",  "gender": "female", "style": "soft, gentle"},
    "Lavanya": {"name": "Lavanya", "gender": "female", "style": "clear, measured"},
    "Vidya":   {"name": "Vidya",   "gender": "female", "style": "calm, professional"},
    "Lalita":  {"name": "Lalita",  "gender": "female", "style": "bright, friendly"},
    "Kaveri":  {"name": "Kaveri",  "gender": "female", "style": "even pace"},
}

GNANI_DEFAULT_VOICE = "Janaki"

# Gnani's edge blocks requests with a default Python user agent (403 Forbidden)
# while accepting the identical request from curl. Nothing about the payload -
# purely UA filtering, which cost an hour to find.
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"


def resolve_gnani_voice(voice: str | None) -> str:
    """Pick a valid Timbre voice, tolerating case and unknown names."""
    if not voice:
        return GNANI_DEFAULT_VOICE
    wanted = voice.strip().lower()
    for known in GNANI_FEMALE_KANNADA_VOICES:
        if known.lower() == wanted:
            return known
    logger.warning("Unknown Gnani voice %r; using %s", voice, GNANI_DEFAULT_VOICE)
    return GNANI_DEFAULT_VOICE


class GnaniTTS(tts.TTS):
    def __init__(
        self,
        api_key: str,
        voice: str = GNANI_DEFAULT_VOICE,
        language: str = "kn-IN",
        model: str = GNANI_DEFAULT_MODEL,
        base_url: str = GNANI_BASE_URL,
        speed: float = 1.0,
        sample_rate: int = GNANI_SAMPLE_RATE,
    ) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=sample_rate,
            num_channels=1,
        )
        self._api_key = api_key
        self._voice = resolve_gnani_voice(voice)
        self._language = language
        self._model = model
        self._base_url = base_url.rstrip("/")
        # Timbre accepts 0.85-1.15. Values outside that range are rejected, so
        # clamp rather than let a UI slider break every turn.
        self._speed = min(1.15, max(0.85, float(speed)))

    @property
    def voice(self) -> str:
        return self._voice

    @property
    def model(self) -> str:
        return self._model

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> "GnaniChunkedStream":
        return GnaniChunkedStream(tts_instance=self, text=text, conn_options=conn_options)


class GnaniChunkedStream(tts.ChunkedStream):
    def __init__(self, *, tts_instance: GnaniTTS, text: str, conn_options: APIConnectOptions):
        super().__init__(tts=tts_instance, input_text=text, conn_options=conn_options)
        self._gnani = tts_instance

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        from livekit.agents import utils

        output_emitter.initialize(
            request_id=f"gnani-tts-{utils.shortuuid()}",
            sample_rate=self._gnani.sample_rate,
            num_channels=1,
            mime_type="audio/pcm",
            stream=False,
        )

        payload = {
            "text": self._input_text,
            "voice": self._gnani._voice,
            "model": self._gnani._model,
            "language": self._gnani._language,
            "speed": self._gnani._speed,
            "audio_config": {
                "sample_rate": self._gnani.sample_rate,
                "num_channels": 1,
                "sample_width": 2,
                "encoding": "linear_pcm",
                "container": "wav",
            },
        }
        headers = {
            "X-API-Key-ID": self._gnani._api_key,
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": _UA,
        }

        # Same 3s ceiling as Sarvam. Measured p50 is ~655ms per clause, so this
        # is ~4.5x headroom while still failing fast enough for the TTS
        # FallbackAdapter to reach the next provider before the caller hears a
        # gap.
        timeout = aiohttp.ClientTimeout(
            total=float(os.getenv("GNANI_TTS_HTTP_TIMEOUT", "3.0"))
        )

        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{self._gnani._base_url}/api/v1/tts/inference",
                    json=payload,
                    headers=headers,
                ) as resp:
                    if resp.status != 200:
                        detail = (await resp.text())[:300]
                        raise APIStatusError(
                            f"Gnani TTS {resp.status}: {detail}",
                            status_code=resp.status,
                        )
                    audio = await resp.read()

            if not audio:
                raise APIConnectionError("Gnani TTS returned no audio")

            # Response is a WAV container; strip to raw PCM for the emitter.
            if audio[:4] == b"RIFF":
                with io.BytesIO(audio) as buf, wave.open(buf, "rb") as wav:
                    actual_rate = wav.getframerate()
                    pcm = wav.readframes(wav.getnframes())
                if actual_rate != self._gnani.sample_rate:
                    logger.warning(
                        "Gnani returned %dHz but provider declares %dHz; playback "
                        "will be pitch-shifted",
                        actual_rate, self._gnani.sample_rate,
                    )
            else:
                pcm = audio          # already raw

            output_emitter.push(pcm)
            output_emitter.flush()

        except (APIConnectionError, APIStatusError):
            raise
        except Exception as e:
            # Must propagate so the FallbackAdapter can reach the next provider.
            # Swallowing here would produce a silent turn.
            logger.error("Gnani TTS request failed", exc_info=True)
            raise APIConnectionError(f"Gnani TTS failed: {e}") from e
