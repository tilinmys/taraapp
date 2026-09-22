import asyncio
import io
import wave

from livekit.agents import tts
from livekit.agents.types import APIConnectOptions, DEFAULT_API_CONNECT_OPTIONS

from tts_audio_cache import CachedTTS, _wait_for_cache_writes


PCM = b"\x01\x00\x02\x00" * 400


class _FakeProvider(tts.TTS):
    def __init__(self, *, pace=1.0):
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=22050,
            num_channels=1,
        )
        self._model = "bulbul:test"
        self._speaker = "manisha"
        self._target_language_code = "kn-IN"
        self._pace = pace
        self.calls = 0

    @property
    def model(self):
        return self._model

    @property
    def provider(self):
        return "fake"

    def synthesize(
        self,
        text,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ):
        self.calls += 1
        return _FakeStream(self, text, conn_options)


class _FakeStream(tts.ChunkedStream):
    def __init__(self, provider, text, conn_options):
        super().__init__(tts=provider, input_text=text, conn_options=conn_options)

    async def _run(self, output_emitter):
        output_emitter.initialize(
            request_id="fake-request",
            sample_rate=22050,
            num_channels=1,
            mime_type="audio/pcm",
        )
        output_emitter.push(PCM)


async def _collect(provider, text):
    stream = provider.synthesize(text)
    try:
        chunks = []
        async for event in stream:
            chunks.append(bytes(event.frame.data))
        return b"".join(chunks)
    finally:
        await stream.aclose()


def _write_wav(path, pcm=PCM):
    path.parent.mkdir(parents=True, exist_ok=True)
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(22050)
        wav_file.writeframes(pcm)
    path.write_bytes(output.getvalue())


def test_hit_returns_cached_audio_without_network(monkeypatch, tmp_path):
    monkeypatch.setenv("TTS_AUDIO_CACHE_DIR", str(tmp_path))
    inner = _FakeProvider()
    cached = CachedTTS(inner)
    _write_wav(tmp_path / f"{cached._cache_key('ನಮಸ್ಕಾರ')}.wav")

    assert asyncio.run(_collect(cached, "ನಮಸ್ಕಾರ")) == PCM
    assert inner.calls == 0


def test_miss_persists_then_second_call_hits(monkeypatch, tmp_path):
    monkeypatch.setenv("TTS_AUDIO_CACHE_DIR", str(tmp_path))
    inner = _FakeProvider()
    cached = CachedTTS(inner)

    assert asyncio.run(_collect(cached, "ಮುಂದಿನ ಪ್ರಶ್ನೆ")) == PCM
    asyncio.run(_wait_for_cache_writes())
    assert inner.calls == 1
    assert asyncio.run(_collect(cached, "ಮುಂದಿನ ಪ್ರಶ್ನೆ")) == PCM
    assert inner.calls == 1


def test_cache_key_changes_with_pace(monkeypatch, tmp_path):
    monkeypatch.setenv("TTS_AUDIO_CACHE_DIR", str(tmp_path))
    normal = CachedTTS(_FakeProvider(pace=1.0))
    slower = CachedTTS(_FakeProvider(pace=0.9))

    assert normal._cache_key("ಒಂದೇ ಪಠ್ಯ") != slower._cache_key("ಒಂದೇ ಪಠ್ಯ")


def test_build_tts_wraps_sarvam_before_fallback(monkeypatch):
    import agent

    inner = _FakeProvider()
    monkeypatch.setattr(agent, "_build_sarvam_tts", lambda *args, **kwargs: inner)
    monkeypatch.setenv("VOICE_TTS_AUDIO_CACHE_ENABLED", "true")
    monkeypatch.setenv("VOICE_TTS_FALLBACK_ENABLED", "false")

    assert isinstance(agent.build_tts(tts_provider="sarvam"), CachedTTS)


def test_cache_kill_switch_returns_unwrapped_provider(monkeypatch):
    import agent

    inner = _FakeProvider()
    monkeypatch.setattr(agent, "_build_sarvam_tts", lambda *args, **kwargs: inner)
    monkeypatch.setenv("VOICE_TTS_AUDIO_CACHE_ENABLED", "false")
    monkeypatch.setenv("VOICE_TTS_FALLBACK_ENABLED", "false")

    assert agent.build_tts(tts_provider="sarvam") is inner
