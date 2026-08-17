"""Kannada number expansion tests.

This sits directly in the clinical speech path: if a blood-pressure reading is
mispronounced, an ASHA worker acts on the wrong number. Every example from the
original prompt specification is pinned here.

Run:  python -m pytest tests/test_sakhi_kannada.py -q
"""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sakhi_kannada import (  # noqa: E402
    expand_kannada_numbers,
    kannada_number,
    kannada_number_normalization,
)


# --------------------------------------------------------------------------
# The specification's own worked examples
# --------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (120, "ನೂರ ಇಪ್ಪತ್ತು"),
    (140, "ನೂರ ನಲವತ್ತು"),
    (145, "ನೂರ ನಲವತ್ತೈದು"),
    (150, "ನೂರ ಐವತ್ತು"),
    (95, "ತೊಂಬತ್ತೈದು"),
    (90, "ತೊಂಬತ್ತು"),
    (80, "ಎಂಬತ್ತು"),
    (100, "ನೂರು"),
    (24, "ಇಪ್ಪತ್ತನಾಲ್ಕು"),
    (28, "ಇಪ್ಪತ್ತೆಂಟು"),
    (7, "ಏಳು"),
])
def test_matches_the_specified_pronunciations(value, expected):
    assert kannada_number(value) == expected


# --------------------------------------------------------------------------
# Sandhi — a tens word takes a bound unit form, it is not simple concatenation
# --------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (21, "ಇಪ್ಪತ್ತೊಂದು"),
    (22, "ಇಪ್ಪತ್ತೆರಡು"),
    (25, "ಇಪ್ಪತ್ತೈದು"),
    (26, "ಇಪ್ಪತ್ತಾರು"),
    (29, "ಇಪ್ಪತ್ತೊಂಬತ್ತು"),
    (45, "ನಲವತ್ತೈದು"),
    (99, "ತೊಂಬತ್ತೊಂಬತ್ತು"),
])
def test_tens_unit_sandhi(value, expected):
    assert kannada_number(value) == expected


@pytest.mark.parametrize("value", list(range(0, 300)))
def test_no_digit_survives_anywhere_in_range(value):
    """The whole point: nothing numeric may reach the voice."""
    assert not any(ch.isdigit() for ch in kannada_number(value))


def test_out_of_range_degrades_instead_of_raising():
    """A TTS transform must never take down a live turn."""
    assert kannada_number(1000) == "1000"
    assert kannada_number(-5) == "-5"
    assert kannada_number("x") == "x"


# --------------------------------------------------------------------------
# Blood-pressure pairs
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("145/95", "ನೂರ ನಲವತ್ತೈದು ಬೈ ತೊಂಬತ್ತೈದು"),
    ("120/80", "ನೂರ ಇಪ್ಪತ್ತು ಬೈ ಎಂಬತ್ತು"),
    ("150 / 100", "ನೂರ ಐವತ್ತು ಬೈ ನೂರು"),
    ("140-90", "ನೂರ ನಲವತ್ತು ಬೈ ತೊಂಬತ್ತು"),
])
def test_bp_pairs_become_one_spoken_phrase(text, expected):
    """A slash left in place is read as punctuation, not as 'by'."""
    assert expand_kannada_numbers(text) == expected


def test_expansion_preserves_surrounding_kannada():
    out = expand_kannada_numbers("ರಕ್ತದೊತ್ತಡ 145/95 ಇದೆ.")
    assert out.startswith("ರಕ್ತದೊತ್ತಡ ") and out.endswith(" ಇದೆ.")
    assert not any(ch.isdigit() for ch in out)


def test_text_without_numbers_is_untouched():
    original = "ನಮಸ್ಕಾರ ಅಕ್ಕಾರ, ಹೇಗಿದ್ದೀರಿ?"
    assert expand_kannada_numbers(original) is original or expand_kannada_numbers(original) == original


# --------------------------------------------------------------------------
# Streaming transform
# --------------------------------------------------------------------------

def _run(chunks):
    async def go():
        async def src():
            for c in chunks:
                yield c
        return "".join([c async for c in kannada_number_normalization(src())])
    return asyncio.run(go())


@pytest.mark.parametrize("chunks", [
    ["ಬಿಪಿ ", "145", "/", "95", " ಇದೆ."],
    ["ಬಿ", "ಪಿ 1", "20/8", "0"],           # number split mid-digit
    ["1", "4", "5", "/", "9", "5"],         # one character at a time
    ["ವಯಸ್ಸು 24 ವರ್ಷ"],
])
def test_no_digits_reach_tts_however_the_stream_is_chunked(chunks):
    out = _run(chunks)
    assert not any(ch.isdigit() for ch in out), f"digits leaked: {out!r}"


def test_split_number_is_not_expanded_as_two_numbers():
    """'14' + '5' must become one hundred forty-five, not fourteen then five."""
    assert _run(["14", "5"]) == kannada_number(145)


def test_streaming_matches_whole_string_expansion():
    text = "ತಾಯಿಯ ಬಿಪಿ 145/95, ವಯಸ್ಸು 24, 28 ವಾರ."
    assert _run(list(text)) == expand_kannada_numbers(text)
