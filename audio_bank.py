"""Pre-recorded prompts held in RAM, ready to play with no TTS call.

The zero-TTS turn loop (agent.py, VOICE_ZERO_TTS_MODE) answers every turn
with one of a fixed set of WAVs: the intake questions, the micro-prompts that
ask for one missing slot, and the emergency referral. `preload()` reads them
all once per worker process (prewarm), so a turn only looks a path up in a
dict - no disk read, no decode, no network between the decision and the
first audio frame.

Memory: 16 kHz mono PCM16 is 32 KB per second; the whole bank (about 30
prompts per language, 3-8 s each) is a few MB, shared by every call the
worker runs. Only the PCM is held (one bytes object per prompt): the 20 ms
`rtc.AudioFrame`s are cut as it plays, as memoryview slices, not copies.
Pre-built frames cost ~3x the audio itself in object overhead (21 MB of
heap for 6 MB of PCM, measured).
"""

from __future__ import annotations

import logging
import wave
from pathlib import Path
from typing import AsyncIterator, Dict, Iterator, List, Optional, Tuple

logger = logging.getLogger("sakhi.audio_bank")

FRAME_MS = 20


class AudioPromptBank:
    def __init__(self, base_dir: str = "assets/audio/prompts") -> None:
        self.base_dir = Path(base_dir)
        # key -> (pcm, sample rate, channels)
        self._pcm: Dict[str, Tuple[bytes, int, int]] = {}
        self.bytes_loaded = 0

    @property
    def ready(self) -> bool:
        return bool(self._pcm)

    def _key(self, rel_path: str) -> str:
        """"kn/q01_full.wav", "assets/audio/prompts/kn/q01_full.wav" and a
        Windows path all name the same prompt."""
        key = str(rel_path).replace("\\", "/")
        base = self.base_dir.as_posix().rstrip("/") + "/"
        return key[len(base):] if key.startswith(base) else key

    def preload(self) -> int:
        """Load every WAV under base_dir. Returns how many. Safe to call twice."""
        if not self.base_dir.is_dir():
            logger.warning("audio bank: %s does not exist; zero-TTS mode stays off", self.base_dir)
            return 0
        loaded = 0
        for path in sorted(self.base_dir.rglob("*.wav")):
            key = self._key(path.relative_to(self.base_dir).as_posix())
            if key in self._pcm:
                continue
            try:
                with wave.open(str(path), "rb") as w:
                    if w.getsampwidth() != 2:
                        raise ValueError(f"{w.getsampwidth() * 8}-bit audio; PCM16 needed")
                    rate, channels = w.getframerate(), w.getnchannels()
                    pcm = w.readframes(w.getnframes())
            except Exception:
                logger.warning("audio bank: skipping unreadable %s", path, exc_info=True)
                continue
            self._pcm[key] = (pcm, rate, channels)
            self.bytes_loaded += len(pcm)
            loaded += 1
        logger.info("audio bank: %d prompt(s), %d KB in RAM", len(self._pcm), self.bytes_loaded // 1024)
        return loaded

    def has(self, rel_path: str) -> bool:
        return self._key(rel_path) in self._pcm

    def _cut(self, key: str) -> Iterator[object]:
        from livekit import rtc

        pcm, rate, channels = self._pcm[key]
        step = rate * FRAME_MS // 1000 * 2 * channels
        view = memoryview(pcm)
        for i in range(0, len(pcm), step):
            piece = view[i: i + step]
            yield rtc.AudioFrame(data=piece, sample_rate=rate, num_channels=channels,
                                 samples_per_channel=len(piece) // (2 * channels))

    def get(self, rel_path: str) -> Optional[List[object]]:
        """The prompt as 20 ms frames, or None when it was never recorded."""
        key = self._key(rel_path)
        return list(self._cut(key)) if key in self._pcm else None

    def duration_ms(self, rel_path: str) -> int:
        entry = self._pcm.get(self._key(rel_path))
        return len(entry[0]) * 1000 // (entry[1] * 2 * entry[2]) if entry else 0

    async def stream(self, rel_path: str) -> AsyncIterator[object]:
        """The prompt's frames, cut as they are consumed, for session.say(audio=...)."""
        key = self._key(rel_path)
        if key in self._pcm:
            for frame in self._cut(key):
                yield frame


AUDIO_BANK = AudioPromptBank()
