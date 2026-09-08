"""Skillies.ai TTS wrapper for LiveKit Agents.

Skillies.ai has no official livekit-plugins package, so this implements the
minimum `tts.TTS` surface directly against their REST API (POST /v1/text-to-speech).
It returns an audio/wav response which we stream to LiveKit.
"""

from __future__ import annotations

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

logger = logging.getLogger("skillies_wrappers")

DEFAULT_BASE_URL = "https://voice.skillies.ai/v1"
DEFAULT_SAMPLE_RATE = 24000
NUM_CHANNELS = 1


@dataclass
class _TTSOptions:
    voice_id: str
    sample_rate: int
    base_url: str
    api_key: str


class SkilliesTTS(tts.TTS):
    def __init__(
        self,
        *,
        api_key: NotGivenOr[str] = NOT_GIVEN,
        voice_id: str = "ehsan",
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        base_url: str = DEFAULT_BASE_URL,
        http_session: aiohttp.ClientSession | None = None,
    ) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=sample_rate,
            num_channels=NUM_CHANNELS,
        )
        # A live key was previously hardcoded here as a silent fallback when
        # SKILLIES_API_KEY was unset - a committed secret in plaintext, and
        # inconsistent with every other provider in this codebase (Smallest,
        # Rumik, Gemini all raise instead of silently using a baked-in key).
        # Removed; fails loudly like the others.
        key = api_key if is_given(api_key) else os.getenv("SKILLIES_API_KEY", "")
        if not key:
            raise ValueError(
                "Skillies.ai API key is required, either as argument or SKILLIES_API_KEY env var"
            )

        self._opts = _TTSOptions(
            voice_id=voice_id,
            sample_rate=sample_rate,
            base_url=base_url,
            api_key=key,
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
    def __init__(self, *, tts: SkilliesTTS, input_text: str, conn_options: APIConnectOptions) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._tts: SkilliesTTS = tts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        opts = self._tts._opts
        session = self._tts._ensure_session()
        
        payload = {
            "text": self.input_text,
            "voice_id": opts.voice_id
        }
        headers = {
            "Authorization": f"Bearer {opts.api_key}",
            "Content-Type": "application/json",
        }

        try:
            async with session.post(
                f"{opts.base_url}/text-to-speech",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(
                    total=30, sock_connect=self._conn_options.timeout
                ),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise APIStatusError(
                        message=f"Skillies.ai TTS error: {body[:300]}",
                        status_code=resp.status,
                        request_id=None,
                        body=body[:500],
                    )

                output_emitter.initialize(
                    request_id=resp.headers.get("x-request-id", ""),
                    sample_rate=opts.sample_rate,
                    num_channels=NUM_CHANNELS,
                    mime_type="audio/pcm", 
                )
                
                chunk_count = 0
                async for chunk in resp.content.iter_chunked(4096):
                    if chunk:
                        if chunk_count == 0 and chunk.startswith(b"RIFF"):
                            if len(chunk) > 44:
                                output_emitter.push(chunk[44:])
                        else:
                            output_emitter.push(chunk)
                        chunk_count += 1

            output_emitter.flush()

        except APIStatusError:
            raise
        except aiohttp.ClientConnectionError as e:
            raise APIConnectionError() from e
        except TimeoutError as e:
            raise APITimeoutError() from e
        except Exception as e:
            raise APIConnectionError(f"Skillies.ai TTS request failed: {e}") from e
