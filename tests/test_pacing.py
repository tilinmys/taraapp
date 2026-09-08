"""Speaking-rate adaptation — silence window only.

Adopted from a proposed "Adaptive Prosody Controller" after stripping the two
parts that would have caused harm:

  - per-turn TTS speed changes, which re-create the reported "the voice keeps
    changing" defect by design (and whose 0.80x tier is impossible anyway:
    Gnani Timbre clamps to 0.85-1.15)
  - per-turn LLM temperature, which changes word choice and cannot reach the
    TTS acoustic model's pitch contour

What survives is the genuinely useful half: wait longer for a worker who
speaks deliberately, less for one who is brisk.
"""

import os
from unittest.mock import MagicMock

import pytest

import agent
from sakhi_pacing import (
    FASTER_OFFSET,
    MIN_UTTERANCE_SECONDS,
    SLOWER_OFFSET,
    PacingTracker,
    words_per_minute,
)


# --- measurement -----------------------------------------------------------

def test_wpm_is_words_over_actual_speech_time():
    assert words_per_minute("one two three four five six", 3.0) == pytest.approx(120.0)


@pytest.mark.parametrize("transcript,seconds", [
    ("ಹೌದು", 0.4),                 # one word, far too short
    ("yes", 2.0),                  # single word carries no rate information
    ("", 3.0),                     # nothing said
    ("one two three", 0.0),        # no duration
    ("one two three", -1.0),       # nonsense duration
])
def test_unmeasurable_utterances_return_none(transcript, seconds):
    assert words_per_minute(transcript, seconds) is None


def test_short_utterances_are_ignored_rather_than_distorting_the_estimate():
    """A 0.4s "ಹೌದು" reads as a frantic 150 WPM. Reacting to that would make
    the wait time flap turn to turn."""
    assert MIN_UTTERANCE_SECONDS >= 0.5
    tracker = PacingTracker(language="kn")
    for _ in range(5):
        tracker.observe("ಹೌದು", 0.4)
    assert tracker.median_wpm is None


# --- it refuses to guess early ---------------------------------------------

def test_no_adjustment_until_enough_samples():
    """A call must not start by guessing how fast she talks."""
    tracker = PacingTracker(language="kn", min_samples=3)
    tracker.observe("ಅವಳ ಹೆಸರು ಸುಮಾ ಗೌಡ", 4.0)
    assert tracker.category() == "UNKNOWN"
    assert tracker.offset_seconds() == 0.0


# --- language-aware bands --------------------------------------------------

def test_kannada_band_is_lower_than_english():
    """Kannada is agglutinative - one word carries what English needs two or
    three for. English thresholds would mark every fluent Kannada speaker as
    'slow' and permanently widen her window."""
    kn_low, kn_high = PacingTracker(language="kn").band
    en_low, en_high = PacingTracker(language="en").band
    assert kn_low < en_low and kn_high < en_high


def test_a_fluent_kannada_speaker_is_not_mislabelled_slow():
    tracker = PacingTracker(language="kn")
    for _ in range(3):
        tracker.observe(" ".join(["ಪದ"] * 6), 4.0)      # 90 wpm
    assert tracker.category() == "STANDARD"
    assert tracker.offset_seconds() == 0.0


def test_the_same_rate_in_english_reads_as_deliberate():
    tracker = PacingTracker(language="en")
    for _ in range(3):
        tracker.observe(" ".join(["word"] * 6), 4.0)    # 90 wpm
    assert tracker.category() == "DELIBERATE"


# --- the adjustment itself -------------------------------------------------

def test_a_deliberate_speaker_gets_a_wider_window():
    tracker = PacingTracker(language="kn")
    for _ in range(3):
        tracker.observe(" ".join(["ಪದ"] * 3), 5.0)      # 36 wpm
    assert tracker.category() == "DELIBERATE"
    assert tracker.offset_seconds() == SLOWER_OFFSET


def test_a_brisk_speaker_gets_a_tighter_window():
    tracker = PacingTracker(language="kn")
    for _ in range(3):
        tracker.observe(" ".join(["ಪದ"] * 20), 5.0)     # 240 wpm
    assert tracker.category() == "BRISK"
    assert tracker.offset_seconds() == FASTER_OFFSET


def test_the_adjustment_stays_small():
    """It nudges a tuned baseline; it is not a second opinion about it."""
    assert 0 < SLOWER_OFFSET <= 0.4
    assert -0.25 <= FASTER_OFFSET < 0


def test_one_odd_turn_does_not_swing_the_estimate():
    """Median over a rolling window, not the last utterance."""
    tracker = PacingTracker(language="kn")
    for _ in range(4):
        tracker.observe(" ".join(["ಪದ"] * 6), 4.0)      # steady 90 wpm
    tracker.observe(" ".join(["ಪದ"] * 40), 4.0)         # one 600 wpm outlier
    assert tracker.category() == "STANDARD"


# --- composition with the existing state machine ---------------------------

def test_pacing_offsets_the_state_it_does_not_replace_it():
    """Question type still picks the base wait; pacing only nudges it."""
    tracker = PacingTracker(language="kn")
    for _ in range(3):
        tracker.observe(" ".join(["ಪದ"] * 3), 5.0)      # DELIBERATE
    controller = agent.DynamicVADController(MagicMock(), pacing=tracker)

    base_fast = controller._delays["TRANSACTIONAL"]
    base_slow = controller._delays["HIGH_COGNITIVE_LOAD"]

    assert controller._delay_for("TRANSACTIONAL") == pytest.approx(base_fast + SLOWER_OFFSET)
    assert controller._delay_for("HIGH_COGNITIVE_LOAD") == pytest.approx(base_slow + SLOWER_OFFSET)


def test_state_ordering_survives_any_pacing():
    """A fragment-recovery window must stay wider than a transactional one
    however fast she talks."""
    for words, seconds in ((3, 5.0), (20, 5.0), (6, 4.0)):
        tracker = PacingTracker(language="kn")
        for _ in range(3):
            tracker.observe(" ".join(["ಪದ"] * words), seconds)
        controller = agent.DynamicVADController(MagicMock(), pacing=tracker)
        assert controller._delay_for("TRANSACTIONAL") < controller._delay_for("FRAGMENT_RECOVERY")


def test_no_tracker_means_exactly_the_old_behaviour():
    controller = agent.DynamicVADController(MagicMock())
    for state, base in controller._delays.items():
        assert controller._delay_for(state) == base


def test_pacing_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("SAKHI_ADAPTIVE_PACING", "false")
    tracker = PacingTracker(language="kn")
    for _ in range(3):
        tracker.observe(" ".join(["ಪದ"] * 3), 5.0)
    controller = agent.DynamicVADController(MagicMock(), pacing=tracker)
    assert controller._delay_for("TRANSACTIONAL") == controller._delays["TRANSACTIONAL"]


def test_it_never_touches_tts_speed_or_temperature():
    """The two rejected knobs must not appear in the pacing module at all."""
    import sakhi_pacing

    source = open(sakhi_pacing.__file__, encoding="utf-8").read()
    body = source.split('"""', 2)[-1]          # skip the module docstring
    assert "temperature" not in body.lower()
    assert "tts_speed" not in body.lower()
