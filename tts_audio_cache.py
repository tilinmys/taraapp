"""Provider-agnostic disk cache for non-streaming LiveKit TTS providers.

The cache sits below StreamAdapter, so its input is exactly the clause sent to
the production REST provider and its output remains LiveKit SynthesizedAudio.
Disk reads run in a worker thread. Miss writes and LRU pruning run on one
bounded daemon queue: persistence is useful, but it must never extend TTFA.
"""

from __future__ import annotations

import asyncio
import atexit
import hashlib
import io
import json
import logging
import os
from pathlib import Path
import queue
import threading
import uuid
import wave

from livekit.agents import tts
from livekit.agents.types import APIConnectOptions, DEFAULT_API_CONNECT_OPTIONS


logger = logging.getLogger("tts_audio_cache")

_WRITE_QUEUE: "queue.Queue[tuple[Path, bytes, int, int, int] | None]" = queue.Queue(
    maxsize=max(1, int(os.getenv("TTS_AUDIO_CACHE_WRITE_QUEUE_MAX", "256")))
)
_WRITER: threading.Thread | None = None
_WRITER_LOCK = threading.Lock()


def _wav_bytes(pcm: bytes, sample_rate: int, num_channels: int) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(num_channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)
    return output.getvalue()


def _read_wav(path: Path) -> tuple[bytes, int, int]:
    """Read and validate cached PCM in a thread, touching mtime for LRU."""
    with wave.open(str(path), "rb") as wav_file:
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        sample_rate = wav_file.getframerate()
        pcm = wav_file.readframes(wav_file.getnframes())
    if sample_width != 2 or channels < 1 or not pcm or len(pcm) % 2:
        raise ValueError(f"invalid cached PCM WAV: {path}")
    os.utime(path, None)
    return pcm, sample_rate, channels


def _prune_lru(root: Path, max_entries: int) -> None:
    files = sorted(
        root.glob("*.wav"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for stale in files[max_entries:]:
        try:
            stale.unlink()
        except FileNotFoundError:
            pass


def _writer_loop() -> None:
    while True:
        item = _WRITE_QUEUE.get()
        try:
            if item is None:
                return
            path, pcm, sample_rate, num_channels, max_entries = item
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
            temporary.write_bytes(_wav_bytes(pcm, sample_rate, num_channels))
            os.replace(temporary, path)
            _prune_lru(path.parent, max_entries)
        except Exception:
            logger.warning("TTS cache writer failed", exc_info=True)
        finally:
            _WRITE_QUEUE.task_done()


def _ensure_writer() -> None:
    global _WRITER
    if _WRITER is not None and _WRITER.is_alive():
        return
    with _WRITER_LOCK:
        if _WRITER is not None and _WRITER.is_alive():
            return
        _WRITER = threading.Thread(
            target=_writer_loop,
            name="tts-audio-cache-writer",
            daemon=True,
        )
        _WRITER.start()


def _stop_writer() -> None:
    if _WRITER is None or not _WRITER.is_alive():
        return
    try:
        _WRITE_QUEUE.put_nowait(None)
    except queue.Full:
        return
    _WRITER.join(timeout=2.0)


atexit.register(_stop_writer)


async def _wait_for_cache_writes() -> None:
    """Test/support hook; production synthesis never waits for persistence."""
    await asyncio.to_thread(_WRITE_QUEUE.join)


class CachedTTS(tts.TTS):
    """Cache a non-streaming provider's PCM as WAV without changing its API."""

    def __init__(self, provider: tts.TTS) -> None:
        if provider.capabilities.streaming:
            raise ValueError("CachedTTS only wraps non-streaming TTS providers")
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=provider.sample_rate,
            num_channels=provider.num_channels,
        )
        self._provider = provider
        # LiveKit and this repository's provider diagnostics historically
        # inspect `_wrapped_tts` and selected vendor-private attributes. Keep
        # that introspection contract while the cache remains transparent to
        # synthesis; this does not alter fallback ordering or request data.
        self._wrapped_tts = provider
        self._cache_root = Path(
            os.getenv("TTS_AUDIO_CACHE_DIR", "assets/audio/tts_cache")
        )
        self._max_entries = max(
            1, int(os.getenv("TTS_AUDIO_CACHE_MAX_ENTRIES", "2000"))
        )

    def __getattr__(self, name: str):
        """Delegate provider metadata used by language/voice diagnostics."""
        provider = object.__getattribute__(self, "_provider")
        return getattr(provider, name)

    @property
    def model(self) -> str:
        return str(getattr(self._provider, "_model", None) or self._provider.model)

    @property
    def provider(self) -> str:
        return str(self._provider.provider)

    def _cache_key(self, text: str) -> str:
        identity = (
            text,
            self.model,
            str(
                getattr(self._provider, "_speaker", None)
                or getattr(self._provider, "voice", None)
                or ""
            ),
            str(getattr(self._provider, "_target_language_code", "")),
            str(getattr(self._provider, "_pace", "")),
            str(self.sample_rate),
        )
        encoded = json.dumps(
            identity, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> "_CachedChunkedStream":
        return _CachedChunkedStream(
            cache=self,
            text=text,
            conn_options=conn_options,
        )

    async def aclose(self) -> None:
        await self._provider.aclose()


class _CachedChunkedStream(tts.ChunkedStream):
    def __init__(
        self,
        *,
        cache: CachedTTS,
        text: str,
        conn_options: APIConnectOptions,
    ) -> None:
        super().__init__(tts=cache, input_text=text, conn_options=conn_options)
        self._cache = cache

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        key = self._cache._cache_key(self.input_text)
        path = self._cache._cache_root / f"{key}.wav"
        request_id = f"tts-cache-{key[:12]}"

        try:
            pcm, sample_rate, channels = await asyncio.to_thread(_read_wav, path)
        except (FileNotFoundError, EOFError, ValueError, wave.Error, OSError):
            pcm = b""
        else:
            output_emitter.initialize(
                request_id=request_id,
                sample_rate=sample_rate,
                num_channels=channels,
                mime_type="audio/pcm",
            )
            output_emitter.push(pcm)
            return

        # Misses stream straight through to the caller. PCM accumulation is an
        # in-memory bytes copy after each network frame; disk and LRU work are
        # deliberately queued only after the provider completes successfully.
        output_emitter.initialize(
            request_id=request_id,
            sample_rate=self._cache.sample_rate,
            num_channels=self._cache.num_channels,
            mime_type="audio/pcm",
        )
        chunks: list[bytes] = []
        inner = self._cache._provider.synthesize(
            self.input_text,
            conn_options=self._conn_options,
        )
        try:
            async for event in inner:
                data = bytes(event.frame.data)
                if data:
                    chunks.append(data)
                    output_emitter.push(data)
        finally:
            await inner.aclose()

        audio = b"".join(chunks)
        if audio:
            _ensure_writer()
            try:
                _WRITE_QUEUE.put_nowait(
                    (
                        path,
                        audio,
                        self._cache.sample_rate,
                        self._cache.num_channels,
                        self._cache._max_entries,
                    )
                )
            except queue.Full:
                logger.warning("TTS cache queue full; skipping persistence for %s", key)
