"""Vakyam AI TTS provider for LiveKit.

Onboarded 2026-08-30 after a live latency benchmark against real Kannada
clinical speech (the same style of phrase used for the Sarvam/Gnani
benchmark: a spoken BP reading). Results, all median time-to-first-byte over
the realtime WebSocket endpoint:

    Vakyam Ananya   ~130ms   (128.8 / 129.8 / 131.0 / 132.1ms - 4 runs, ~3ms
                              spread - the tightest, most reproducible of
                              every voice tested, chosen for that reliability
                              over any single voice's best-case sample)
    Sarvam manisha   573ms   (prior benchmark, see sakhi_prompts.py history)
    Gnani Kaveri     984ms   (prior benchmark)

Full data: logs/vakyam_benchmark.json. All 17 preset voices support every one
of Vakyam's 8 languages (en-IN, hi-IN, ta-IN, te-IN, kn-IN, mr-IN, gu-IN,
bn-IN) - this is one multilingual roster, not per-language distinct voices.
Ananya kept as the one default across languages, for the same reason Sakhi
already speaks with one voice regardless of provider (see agent.py/build_tts).

TTS ONLY. Vakyam has no speech-to-text product - confirmed directly against
docs.vakyam.ai/api-reference/speech ("No STT/transcription endpoint
documented"). Nothing about this file changes STT provider selection.

WHY A CHUNKEDSTREAM WRAPPER AROUND A WEBSOCKET, NOT A TRUE STREAMING TTS
-------------------------------------------------------------------------
Vakyam's realtime endpoint is a genuine low-latency streaming protocol
(config -> text -> binary PCM frames -> end_of_utterance), and a
`tts.SynthesizeStream` implementation that forwarded each binary frame to the
output emitter as it arrived would shave a further amount of the ~600-900ms
total-synthesis time off perceived latency for a long clause. That is a real
future improvement, not done here: `tts.ChunkedStream` (connect once per
clause, collect every frame, push the complete PCM buffer) is the exact
pattern already proven in this codebase for Gnani and Sarvam-REST, integrates
with `tts.StreamAdapter` + `EagerClauseTokenizer` with zero new wiring, and
the TTFB win over the OLD providers is already decisive at the connection
level (~130ms to first byte vs 573-984ms) regardless of whether the
remaining bytes are forwarded incrementally or in one push.
"""

from __future__ import annotations

import json
import logging
import os

from livekit.agents import APIConnectionError, APIStatusError, tts
from livekit.agents.types import APIConnectOptions, DEFAULT_API_CONNECT_OPTIONS

logger = logging.getLogger("vakyam_wrappers")

VAKYAM_WS_URL = "wss://api.vakyam.ai/v1/tts/websocket"
VAKYAM_MODEL = "raaga-v1"

# 24000, not Sarvam/Gnani's 22050 - matches what was benchmarked and is one
# of Vakyam's four supported rates (8000/16000/24000/48000). LiveKit resamples
# at the mixer regardless, so this does not have to match the other providers'
# rate the way GNANI_SAMPLE_RATE's own comment discusses.
VAKYAM_SAMPLE_RATE = 24000

VAKYAM_KANNADA_VOICES = {
    # All 17 names below support kn-IN (confirmed live via
    # GET /v1/voices?group_by=language - this is one shared multilingual
    # roster, not a per-language voice list). Only the female voices are
    # listed here: Sakhi's persona is consistently a warm female "elder
    # sister" voice across every existing provider (Sarvam manisha, Gnani
    # Kaveri) - offering the male half of the roster here would invite a
    # persona change this integration was never asked to make.
    "Ananya":  {"name": "Ananya",  "gender": "female", "style": "warm, steady - fastest and most consistent measured"},
    "Archana": {"name": "Archana", "gender": "female", "style": "clear, measured"},
    "Bhamini": {"name": "Bhamini", "gender": "female", "style": "bright, friendly"},
    "Kavya":   {"name": "Kavya",   "gender": "female", "style": "even pace"},
    "Meena":   {"name": "Meena",   "gender": "female", "style": "calm, professional"},
    "Neha":    {"name": "Neha",    "gender": "female", "style": "soft, gentle"},
    "Nisha":   {"name": "Nisha",   "gender": "female", "style": "clear, measured"},
    "Priya":   {"name": "Priya",   "gender": "female", "style": "warm, clear"},
    "Vidya":   {"name": "Vidya",   "gender": "female", "style": "calm, measured"},
}

# See the module docstring's benchmark table.
VAKYAM_DEFAULT_VOICE = "Ananya"


def resolve_vakyam_voice(voice: str | None) -> str:
    """Pick a valid Vakyam voice, tolerating case and unknown names."""
    if not voice:
        return VAKYAM_DEFAULT_VOICE
    wanted = voice.strip().lower()
    for known in VAKYAM_KANNADA_VOICES:
        if known.lower() == wanted:
            return known
    logger.warning("Unknown Vakyam voice %r; using %s", voice, VAKYAM_DEFAULT_VOICE)
    return VAKYAM_DEFAULT_VOICE


class VakyamTTS(tts.TTS):
    def __init__(
        self,
        api_key: str,
        voice: str = VAKYAM_DEFAULT_VOICE,
        language: str = "kn-IN",
        model: str = VAKYAM_MODEL,
        speed: float = 1.0,
        sample_rate: int = VAKYAM_SAMPLE_RATE,
    ) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=sample_rate,
            num_channels=1,
        )
        self._api_key = api_key
        self._voice = resolve_vakyam_voice(voice)
        self._language = language
        self._model = model
        # Documented range is 0.5-2.0; clamp rather than let an out-of-range
        # value fail every turn against the live API.
        self._speed = min(2.0, max(0.5, float(speed)))

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
    ) -> "VakyamChunkedStream":
        return VakyamChunkedStream(tts_instance=self, text=text, conn_options=conn_options)


class VakyamChunkedStream(tts.ChunkedStream):
    def __init__(self, *, tts_instance: VakyamTTS, text: str, conn_options: APIConnectOptions):
        super().__init__(tts=tts_instance, input_text=text, conn_options=conn_options)
        self._vakyam = tts_instance

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        import asyncio

        import websockets
        from livekit.agents import utils

        output_emitter.initialize(
            request_id=f"vakyam-tts-{utils.shortuuid()}",
            sample_rate=self._vakyam.sample_rate,
            num_channels=1,
            mime_type="audio/pcm",
            stream=False,
        )

        # Same reasoning as Gnani's HTTP timeout: measured connect+TTFB is
        # ~130ms and full clause synthesis ~750-900ms (logs/vakyam_benchmark.json),
        # so this is generous headroom while still failing fast enough for the
        # TTS FallbackAdapter to reach the next provider before the caller
        # hears a gap.
        timeout = float(os.getenv("VAKYAM_TTS_WS_TIMEOUT", "4.0"))

        async def _synthesize() -> list[bytes]:
            async with websockets.connect(
                VAKYAM_WS_URL,
                additional_headers={"Authorization": f"Bearer {self._vakyam._api_key}"},
                open_timeout=timeout,
            ) as ws:
                await ws.recv()  # {"type": "connected", ...}
                await ws.send(json.dumps({
                    "type": "config",
                    "model_id": self._vakyam._model,
                    "voice": self._vakyam._voice,
                    "language": self._vakyam._language,
                    "output_format": "pcm",
                    "sample_rate": self._vakyam.sample_rate,
                    "speed": self._vakyam._speed,
                }))
                await ws.recv()  # {"type": "configured", ...}

                await ws.send(json.dumps({"type": "text", "text": self._input_text}))

                pcm_chunks: list[bytes] = []
                while True:
                    msg = await ws.recv()
                    if isinstance(msg, (bytes, bytearray)):
                        pcm_chunks.append(bytes(msg))
                        continue
                    data = json.loads(msg)
                    mtype = data.get("type")
                    if mtype == "end_of_utterance":
                        break
                    if mtype == "error":
                        # Confirmed live 2026-09-02 and again 2026-09-08: a
                        # real Vakyam error payload's "code" field is not
                        # guaranteed to be numeric (a string error slug, e.g.
                        # "invalid_request", not an HTTP status). Passed
                        # straight through, livekit-agents' own
                        # APIStatusError.__init__ does `400 <= status_code
                        # < 500` UNCONDITIONALLY (no None-check - confirmed
                        # by reading the installed SDK source, and the hard
                        # way: passing status_code=None here still TypeErrors,
                        # just against NoneType instead of str). Its own
                        # signature documents `status_code: int = -1` as the
                        # sentinel for "unknown/non-HTTP status" - that is the
                        # only value this can safely pass when Vakyam's code
                        # isn't a real HTTP status, never None and never a
                        # fabricated 4xx/5xx guess.
                        raw_code = (
                            data.get("error", {}).get("code")
                            if isinstance(data.get("error"), dict) else None
                        )
                        status_code = raw_code if isinstance(raw_code, int) else -1
                        raise APIStatusError(
                            f"Vakyam TTS error: {data.get('error')}",
                            status_code=status_code,
                        )
                return pcm_chunks

        try:
            # asyncio.wait_for, not asyncio.timeout - this runs on Python
            # 3.10, which does not have the latter (learned the hard way
            # elsewhere in this project's own diagnostic scripts).
            pcm_chunks = await asyncio.wait_for(_synthesize(), timeout=timeout)

            if not pcm_chunks:
                raise APIConnectionError("Vakyam TTS returned no audio")

            output_emitter.push(b"".join(pcm_chunks))
            output_emitter.flush()

        except (APIConnectionError, APIStatusError):
            raise
        except Exception as e:
            # Must propagate so the FallbackAdapter can reach the next
            # provider. Swallowing here would produce a silent turn.
            logger.error("Vakyam TTS request failed", exc_info=True)
            raise APIConnectionError(f"Vakyam TTS failed: {e}") from e
