"""How fast this ASHA worker actually speaks, and how long to wait for her.

WHAT THIS DOES, AND DELIBERATELY DOES NOT
------------------------------------------
It adjusts ONE thing: how long Sakhi waits in silence before deciding the
worker has finished talking.

It does **not** touch TTS speed and it does **not** touch LLM temperature, both
of which were proposed alongside it and both of which were rejected on
evidence:

  - Varying TTS speed per turn re-creates the exact defect reported from the
    field - "the voice is getting changed in the middle way". That was traced
    to four inconsistent pace values and fixed by unifying them behind
    `default_tts_pace()`. Re-introducing per-turn speed changes is that bug,
    implemented on purpose. (Gnani Timbre also clamps to 0.85-1.15, so a
    proposed 0.80x tier would silently be 0.85 anyway.)
  - LLM temperature changes word choice. It does not reach the TTS acoustic
    model's pitch contour, so it cannot "stabilise prosody".

WHY NOT ABSOLUTE WPM THRESHOLDS
-------------------------------
The obvious design - "under 100 WPM is slow, over 160 is fast" - is calibrated
for English and is wrong here. Kannada is agglutinative: one word carries what
English needs two or three for, so a perfectly fluent Kannada speaker measures
far below 100 WPM and would be permanently classified "slow". Hindi sits
between the two.

So the bands are per-language, and the tracker compares the worker against the
band for the language she is actually speaking.

WHY A MEDIAN, NOT THE LAST TURN
-------------------------------
A single utterance is a terrible estimate. "ಹೌದು" spoken in 0.4s reads as a
frantic 150 WPM; a half-caught word reads as 20. Reacting to either would make
the wait time flap turn to turn, which is worse than a fixed window. The
tracker holds the last few samples, uses their median, and refuses to say
anything until it has seen `min_samples`.

WHY AN OFFSET, NOT A REPLACEMENT
--------------------------------
`DynamicVADController` already picks a base wait from what Sakhi just ASKED -
a spelling question gets a wider window than a yes/no question. That signal is
strictly better than speech rate, because it knows the worker will need time
*before* she starts speaking, whereas rate is only measurable afterwards. So
pacing returns a small bounded nudge that is added to whichever base the
controller chose; it never overrides it.
"""

from __future__ import annotations

import dataclasses
import statistics

# Words-per-minute band that counts as "normal", per language. Below the band
# she is deliberate or hesitant; above it she is brisk and transactional.
#
# Kannada and Hindi bands are lower than English because a word in those
# languages carries more meaning - the same thought takes fewer of them.
_BANDS = {
    "kn": (55.0, 105.0),
    "hi": (65.0, 120.0),
    "en": (100.0, 160.0),
}
_DEFAULT_BAND = _BANDS["kn"]

# How far pacing may move the window, in seconds. Deliberately small: this is
# a nudge on top of a tuned baseline, not a second opinion about it.
SLOWER_OFFSET = 0.30
FASTER_OFFSET = -0.15

# Utterances shorter than this carry no useful rate information - they are
# acknowledgements, not speech. Measuring "ಹೌದು" gives a wild WPM either way.
MIN_UTTERANCE_SECONDS = 0.8
MIN_UTTERANCE_WORDS = 2


def words_per_minute(transcript: str, speech_seconds: float) -> float | None:
    """Speaking rate for ONE utterance, or None when it cannot be measured.

    `speech_seconds` must be the duration of *this* utterance. Cumulative
    session audio (what the STT metrics report) would make every worker look
    slower and slower as the call went on.
    """
    if not transcript or speech_seconds <= 0:
        return None
    words = [w for w in transcript.split() if w.strip()]
    if len(words) < MIN_UTTERANCE_WORDS or speech_seconds < MIN_UTTERANCE_SECONDS:
        return None
    return (len(words) / speech_seconds) * 60.0


@dataclasses.dataclass
class PacingTracker:
    """Rolling estimate of one worker's speaking rate, for one call."""

    language: str = "kn"
    window: int = 5
    min_samples: int = 3
    samples: list[float] = dataclasses.field(default_factory=list)

    def observe(self, transcript: str, speech_seconds: float) -> float | None:
        """Record one utterance. Returns its WPM, or None if unmeasurable."""
        rate = words_per_minute(transcript, speech_seconds)
        if rate is None:
            return None
        self.samples.append(rate)
        if len(self.samples) > self.window:
            self.samples.pop(0)
        return rate

    @property
    def median_wpm(self) -> float | None:
        if len(self.samples) < self.min_samples:
            return None
        return statistics.median(self.samples)

    @property
    def band(self) -> tuple[float, float]:
        return _BANDS.get((self.language or "kn")[:2].lower(), _DEFAULT_BAND)

    def category(self) -> str:
        """DELIBERATE / STANDARD / BRISK, or UNKNOWN while still learning."""
        median = self.median_wpm
        if median is None:
            return "UNKNOWN"
        low, high = self.band
        if median < low:
            return "DELIBERATE"
        if median > high:
            return "BRISK"
        return "STANDARD"

    def offset_seconds(self) -> float:
        """Bounded adjustment to add to the controller's base wait.

        Zero until enough samples exist, so a call never starts by guessing.
        """
        category = self.category()
        if category == "DELIBERATE":
            return SLOWER_OFFSET
        if category == "BRISK":
            return FASTER_OFFSET
        return 0.0

    def describe(self) -> str:
        median = self.median_wpm
        if median is None:
            return f"pacing=learning ({len(self.samples)}/{self.min_samples} samples)"
        low, high = self.band
        return (
            f"pacing={self.category()} {median:.0f}wpm "
            f"(band {low:.0f}-{high:.0f} for {self.language}) "
            f"offset={self.offset_seconds():+.2f}s"
        )
