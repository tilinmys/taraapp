"""Kannada number expansion for TTS.

Sarvam's Kannada voices mispronounce raw ASCII digits - "145/95" comes out as
something an ASHA worker cannot act on, which on a blood-pressure reading is a
clinical problem, not a cosmetic one.

WHY THIS IS CODE AND NOT A PROMPT INSTRUCTION
---------------------------------------------
The obvious fix is to tell the model "always spell numbers in Kannada words".
That was tried and it is the wrong layer for two reasons:

1. It is not reliable. A model under a 120-token cap, mid tool-call, drops the
   rule intermittently - and the failure is silent and clinical.
2. It is expensive. A worked example table for every BP pair costs 300+ prompt
   tokens, paid on EVERY generation, to buy behaviour a lookup table gives for
   free.

So the prompt asks for it (cheap, one line) and this module guarantees it. If
the model already wrote Kannada words there are no digits left and this is a
no-op; if it emitted "145/95" this converts it. Belt and braces, with the code
holding the belt.

Covers 0-999, which spans every clinical value the agent handles: systolic and
diastolic pressure, age, gestational weeks, months, and pulse.
"""

from __future__ import annotations

import re

# 0-19 are irregular and must be listed.
_UNITS = {
    0: "ಸೊನ್ನೆ", 1: "ಒಂದು", 2: "ಎರಡು", 3: "ಮೂರು", 4: "ನಾಲ್ಕು",
    5: "ಐದು", 6: "ಆರು", 7: "ಏಳು", 8: "ಎಂಟು", 9: "ಒಂಬತ್ತು",
    10: "ಹತ್ತು", 11: "ಹನ್ನೊಂದು", 12: "ಹನ್ನೆರಡು", 13: "ಹದಿಮೂರು",
    14: "ಹದಿನಾಲ್ಕು", 15: "ಹದಿನೈದು", 16: "ಹದಿನಾರು", 17: "ಹದಿನೇಳು",
    18: "ಹದಿನೆಂಟು", 19: "ಹತ್ತೊಂಬತ್ತು",
}

_TENS = {
    2: "ಇಪ್ಪತ್ತು", 3: "ಮೂವತ್ತು", 4: "ನಲವತ್ತು", 5: "ಐವತ್ತು",
    6: "ಅರವತ್ತು", 7: "ಎಪ್ಪತ್ತು", 8: "ಎಂಬತ್ತು", 9: "ತೊಂಬತ್ತು",
}

# Sandhi: a tens word loses its final vowel sign and takes a bound form of the
# unit. ಇಪ್ಪತ್ತು + 4 -> ಇಪ್ಪತ್ತ + ನಾಲ್ಕು -> ಇಪ್ಪತ್ತನಾಲ್ಕು.
# This is why the units above cannot simply be concatenated.
_UNIT_SUFFIX = {
    1: "ೊಂದು", 2: "ೆರಡು", 3: "ಮೂರು", 4: "ನಾಲ್ಕು", 5: "ೈದು",
    6: "ಾರು", 7: "ೇಳು", 8: "ೆಂಟು", 9: "ೊಂಬತ್ತು",
}

_HUNDREDS = {
    1: ("ನೂರು", "ನೂರ"),        # (exact 100, bound form for 101-199)
    2: ("ಇನ್ನೂರು", "ಇನ್ನೂರ"),
    3: ("ಮುನ್ನೂರು", "ಮುನ್ನೂರ"),
    4: ("ನಾನ್ನೂರು", "ನಾನ್ನೂರ"),
    5: ("ಐನೂರು", "ಐನೂರ"),
    6: ("ಆರುನೂರು", "ಆರುನೂರ"),
    7: ("ಏಳುನೂರು", "ಏಳುನೂರ"),
    8: ("ಎಂಟುನೂರು", "ಎಂಟುನೂರ"),
    9: ("ಒಂಬೈನೂರು", "ಒಂಬೈನೂರ"),
}

# Spoken separator for a BP pair: "145/95" -> "... ಬೈ ...".
BY = "ಬೈ"


def _tens_stem(word: str) -> str:
    """Drop the trailing vowel sign so a unit suffix can attach."""
    return word[:-1] if word.endswith("ು") else word


def kannada_number(value: int) -> str:
    """Render 0-999 as Kannada words.

    Out-of-range values are returned as digits rather than raising - a TTS
    transform must never take down a live turn.
    """
    if not isinstance(value, int) or value < 0 or value > 999:
        return str(value)

    if value < 20:
        return _UNITS[value]

    if value < 100:
        tens, unit = divmod(value, 10)
        if unit == 0:
            return _TENS[tens]
        return _tens_stem(_TENS[tens]) + _UNIT_SUFFIX[unit]

    hundreds, remainder = divmod(value, 100)
    exact, bound = _HUNDREDS[hundreds]
    if remainder == 0:
        return exact
    return f"{bound} {kannada_number(remainder)}"


# A blood-pressure pair: 145/95, 145 / 95, 145-95.
_BP_RE = re.compile(r"(?<!\d)(\d{2,3})\s*[/\-]\s*(\d{2,3})(?!\d)")

# Any remaining standalone integer.
_NUM_RE = re.compile(r"(?<![\d\w])(\d{1,3})(?![\d\w])")


def expand_kannada_numbers(text: str) -> str:
    """Replace ASCII digits with Kannada words, BP pairs first.

    BP pairs are handled before bare numbers so "145/95" becomes one spoken
    phrase joined by ಬೈ, rather than two numbers separated by a slash the voice
    would read as punctuation.
    """
    if not text or not any(ch.isdigit() for ch in text):
        return text

    def _bp(match: re.Match) -> str:
        systolic, diastolic = int(match.group(1)), int(match.group(2))
        return f"{kannada_number(systolic)} {BY} {kannada_number(diastolic)}"

    def _num(match: re.Match) -> str:
        return kannada_number(int(match.group(1)))

    return _NUM_RE.sub(_num, _BP_RE.sub(_bp, text))


# A number can straddle a chunk boundary: "14" | "5/9" | "5". Emitting eagerly
# would expand "14" and "5" separately and speak nonsense. So the tail of the
# buffer is held back while it could still be part of a number, and released as
# soon as it cannot be.
#
# Bounded so a pathological stream cannot buffer without limit - "145/95" is 6
# characters, and no clinical value needs more than this.
_MAX_HELD = 12
_RISKY_TAIL_RE = re.compile(r"[\d\s/\-]+$")


async def kannada_number_normalization(text):
    """`tts_text_transforms` hook - streaming, not buffered.

    Buffering the whole reply before synthesising would hold TTS until the LLM
    finished, which costs far more latency than the expansion saves. This yields
    continuously and only withholds a short tail that might still grow into a
    number.
    """
    held = ""
    async for chunk in text:
        if not chunk:
            continue
        held += chunk

        match = _RISKY_TAIL_RE.search(held)
        if match and len(held) - match.start() <= _MAX_HELD:
            safe, held = held[: match.start()], held[match.start():]
        else:
            safe, held = held, ""

        if safe:
            yield expand_kannada_numbers(safe)

    if held:
        yield expand_kannada_numbers(held)
