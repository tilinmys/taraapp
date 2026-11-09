"""Decode routing for the Indic STT service: pure functions, no torch.

Kept apart from app.py so the decisions are unit-tested without the model
(tests/test_stt_service_routing.py).

What the measurements behind these rules showed (bodhan-ai/indic-transcribe-flex):
  * mixed mode runs inverse text normalisation, and for Kannada / Hindi it
    corrupts spoken numbers ("ತೊಂಬತ್ತು" -> "9", "एक सौ तीस बाई अस्सी" ->
    "13580"). The app parses number WORDS itself (voice/nlu/normalize.ts), so
    kn / hi are decoded in NATIVE mode.
  * English is decoded in MIXED mode (Latin words, digits), which is what
    the app's English parser reads.
  * Language ID is weak for English (plain Indian English scored hi 0.63),
    so when the language was NOT given and the native decode comes out mostly
    in Latin script, it is decoded again as English.
"""

from __future__ import annotations

import re
import struct

ALLOWED_LANGS: tuple[str, ...] = ("kn", "hi", "en")
MODES = ("native", "mixed", "romanized")

#: Over this many seconds a single pass is refused by the model (45 s):
#: the chunked long-form path is used instead.
LONG_FORM_SECONDS = 40.0

_LATIN = re.compile(r"[A-Za-z]")
_INDIC = re.compile(r"[ऀ-ॿಀ-೿]")  # Devanagari, Kannada


def default_mode(lang: str) -> str:
    """One decode per answer: native for Kannada / Hindi (number words kept), mixed for English."""
    return "mixed" if lang == "en" else "native"


def resolve(lang: str | None, mode: str | None) -> tuple[str | None, str | None]:
    """Validate the request. Returns (lang or None for auto, mode or None for 'by language').

    Raises ValueError with a message the API returns as a 400.
    """
    lang = (lang or "").strip().lower() or None
    if lang in ("auto", "none"):
        lang = None
    if lang is not None and lang not in ALLOWED_LANGS:
        raise ValueError(f"lang must be one of {', '.join(ALLOWED_LANGS)} (or auto)")
    mode = (mode or "").strip().lower() or None
    if mode == "romanised":
        mode = "romanized"
    if mode is not None and mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}")
    return lang, mode


def latin_share(text: str) -> float:
    """Share of the letters that are Latin (0..1); 0 for no letters."""
    latin = len(_LATIN.findall(text))
    indic = len(_INDIC.findall(text))
    total = latin + indic
    return latin / total if total else 0.0


def mostly_latin_words(text: str) -> bool:
    """More than half of the words are written in Latin script (English that LID called kn / hi)."""
    words = [w for w in text.split() if re.search(r"[A-Za-zऀ-ॿಀ-೿]", w)]
    if not words:
        return False
    latin = sum(1 for w in words if latin_share(w) > 0.5)
    return latin / len(words) > 0.5


def should_redecode_english(requested: str | None, detected: str, text: str) -> bool:
    """Auto mode only: LID chose kn / hi but the words came out in Latin -> decode as English."""
    return requested is None and detected in ("kn", "hi") and mostly_latin_words(text)


def pcm16_mono_16k(data: bytes) -> bool:
    """Is this already a 16 kHz mono PCM16 WAV (so no conversion is needed)?"""
    if len(data) < 44 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        return False
    pos = 12
    while pos + 8 <= len(data):
        cid, size = data[pos : pos + 4], struct.unpack("<I", data[pos + 4 : pos + 8])[0]
        if cid == b"fmt " and pos + 8 + 16 <= len(data):
            fmt, channels, rate, _, _, bits = struct.unpack("<HHIIHH", data[pos + 8 : pos + 24])
            return fmt == 1 and channels == 1 and rate == 16000 and bits == 16
        pos += 8 + size + (size & 1)
    return False


def wav_pcm16_payload(data: bytes) -> bytes:
    """The sample bytes of a PCM16 WAV's data chunk."""
    pos = 12
    while pos + 8 <= len(data):
        cid, size = data[pos : pos + 4], struct.unpack("<I", data[pos + 4 : pos + 8])[0]
        if cid == b"data":
            return data[pos + 8 : pos + 8 + size]
        pos += 8 + size + (size & 1)
    raise ValueError("WAV has no data chunk")
