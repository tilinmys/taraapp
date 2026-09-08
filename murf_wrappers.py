"""Murf.ai TTS wrapper for LiveKit Agents. EVALUATED, NOT PROMOTED - see below.

REST only (POST /v1/speech/generate, encodeAsBase64=true). Murf also has a
"ultra-low latency" WebSocket streaming endpoint with a dedicated India
region (wss://in.api.murf.ai/v1/speech/stream-input), which is the path that
would actually make Murf latency-competitive. It is NOT implemented here: the
India endpoint rejects every request with

    1008 (policy violation) Gen2 Model is not available in in.api.murf.ai.
    Use Falcon model here or call api.murf.ai/v1/speech/stream for using Gen2

and no combination of a `model`/`modelVersion` field inside `voice_config`
(the only two plausible field names given the REST API's own schema) changes
that outcome - tried both, both rejected identically. This may be an
account/plan gate rather than a request-shape problem; resolving it needs
Murf's own support, not more guessing at an undocumented protocol. Until then
this wrapper reports REST latency only, which is real and directly measured,
but is NOT Murf's best case.

BENCHMARK (2026-08-21, interleaved against production, 4 rounds, real Sakhi
greeting text, https://api.murf.ai/v1/speech/generate):

    provider              voice              native?   ttfb median   max
    sarvam kn-IN           manisha            yes         573ms      744ms
    gnani  kn-IN           Kaveri             yes         984ms     1708ms
    murf   kn-IN           bn-IN-anwesha      NO          2282ms    2725ms
    sarvam hi-IN           manisha            yes          629ms      770ms
    murf   hi-IN           hi-IN-shweta       yes         2002ms    2463ms

Murf's REST path is 2-4x slower than current production in both languages.

WHY THIS IS NOT WIRED IN, EVEN IF THE WEBSOCKET GAP CLOSES THE LATENCY
------------------------------------------------------------------------
Murf's voice catalog (162 voices, queried live via GET /v1/speech/voices) has
NO voice whose PRIMARY locale is kn-IN. Every voice that claims kn-IN support
does so via `supportedLocales` cross-lingual transfer from a different native
locale (the benchmarked candidate, bn-IN-anwesha, is a Bengali-native voice
model steered into Kannada). Kannada is this project's PRIMARY language. A
non-native model reading Kannada is exactly the failure mode Sarvam and Gnani
were chosen to avoid in the first place - this is a quality risk independent
of latency, and latency alone cannot fix it.

Hindi, by contrast, has genuine native voices (hi-IN-shweta, hi-IN-ayushi,
both female) - Murf is a legitimate candidate for Hindi specifically if a
future need is Hindi-only.

LATENCY-RELEVANT PARAMETERS MURF'S API DOCUMENTS
----------------------------------------------------
- `modelVersion: GEN2` (REST default) - "more natural/high-quality than
  earlier models," but the only model the REST endpoint and (per the error
  above) the global-not-India websocket accept.
- `min_buffer_size` (40-160 chars) / `max_buffer_delay_in_ms` (0-1000) -
  server-side text-buffering knobs for the streaming path, analogous to this
  project's own EagerClauseTokenizer min_chunk_chars. Not exercised here
  since the streaming path never connected.
- `sampleRate`: 8000/24000/44100/48000. 24000 used throughout, matching every
  other provider in this codebase.
"""

from __future__ import annotations

import base64
import logging
import os
from dataclasses import dataclass

import aiohttp
from livekit.agents import (
    APIConnectionError,
    APIConnectOptions,
    APIStatusError,
    APITimeoutError,
    tts,
)
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN, NotGivenOr
from livekit.agents.utils import is_given

logger = logging.getLogger("murf_wrappers")

DEFAULT_BASE_URL = "https://api.murf.ai/v1"
DEFAULT_SAMPLE_RATE = 24000
NUM_CHANNELS = 1


@dataclass
class _TTSOptions:
    voice_id: str
    locale: str
    sample_rate: int
    base_url: str
    api_key: str


class MurfTTS(tts.TTS):
    def __init__(
        self,
        *,
        api_key: NotGivenOr[str] = NOT_GIVEN,
        voice_id: str = "hi-IN-shweta",
        locale: str = "hi-IN",
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        base_url: str = DEFAULT_BASE_URL,
        http_session: aiohttp.ClientSession | None = None,
    ) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=sample_rate,
            num_channels=NUM_CHANNELS,
        )
        key = api_key if is_given(api_key) else os.getenv("MURF_API_KEY", "")
        if not key:
            raise ValueError(
                "Murf.ai API key is required, either as argument or MURF_API_KEY env var"
            )
        self._opts = _TTSOptions(
            voice_id=voice_id, locale=locale, sample_rate=sample_rate,
            base_url=base_url, api_key=key,
        )
        self._owns_session = http_session is None
        self._session = http_session

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self._owns_session = True
        return self._session

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> "_ChunkedStream":
        return _ChunkedStream(tts=self, input_text=text, conn_options=conn_options)

    async def aclose(self) -> None:
        if self._owns_session and self._session is not None and not self._session.closed:
            await self._session.close()


class _ChunkedStream(tts.ChunkedStream):
    def __init__(self, *, tts: MurfTTS, input_text: str, conn_options: APIConnectOptions) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._tts: MurfTTS = tts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        opts = self._tts._opts
        session = self._tts._ensure_session()
        payload = {
            "text": self.input_text,
            "voiceId": opts.voice_id,
            "locale": opts.locale,
            "format": "WAV",
            "sampleRate": opts.sample_rate,
            "encodeAsBase64": True,
            "modelVersion": "GEN2",
        }
        headers = {"api-key": opts.api_key, "Content-Type": "application/json"}

        try:
            async with session.post(
                f"{opts.base_url}/speech/generate",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=20, sock_connect=self._conn_options.timeout),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise APIStatusError(
                        message=f"Murf TTS error: {body[:300]}",
                        status_code=resp.status,
                        request_id=None,
                        body=body[:500],
                    )
                data = await resp.json()
                audio_b64 = data.get("encodedAudio")
                if not audio_b64:
                    raise APIConnectionError("Murf response had no encodedAudio field")

                # Real WAV (RIFF header) - strip the standard 44-byte header,
                # same pattern already used for Skillies' response.
                raw = base64.b64decode(audio_b64)
                if raw[:4] == b"RIFF":
                    raw = raw[44:]

                from livekit.agents import utils
                output_emitter.initialize(
                    # audioLengthInSeconds is a float - passing it directly
                    # here failed LiveKit's TTSMetrics pydantic validation
                    # (request_id must be str), caught by actually running
                    # this end to end rather than assuming the wrapper worked
                    # once it imported cleanly.
                    request_id=f"murf-tts-{utils.shortuuid()}",
                    sample_rate=opts.sample_rate,
                    num_channels=NUM_CHANNELS,
                    mime_type="audio/pcm",
                )
                output_emitter.push(raw)

            output_emitter.flush()

        except APIStatusError:
            raise
        except aiohttp.ClientConnectionError as e:
            raise APIConnectionError() from e
        except TimeoutError as e:
            raise APITimeoutError() from e
        except Exception as e:
            raise APIConnectionError(f"Murf TTS request failed: {e}") from e
