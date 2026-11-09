"""The Indic STT service's decode routing (stt_service/routing.py): no torch, no model."""

from __future__ import annotations

import struct

import pytest

from stt_service import routing


def wav(rate=16000, channels=1, bits=16, n=1600) -> bytes:
    data = b"\x01\x00" * n * channels
    block = channels * bits // 8
    fmt = struct.pack("<IHHIIHH", 16, 1, channels, rate, rate * block, block, bits)
    return b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt " + fmt + b"data" + struct.pack("<I", len(data)) + data


def test_one_decode_per_answer_native_for_kn_hi_mixed_for_en():
    assert routing.default_mode("kn") == "native"
    assert routing.default_mode("hi") == "native"  # number words kept: mixed ITN corrupts them
    assert routing.default_mode("en") == "mixed"


@pytest.mark.parametrize("lang", ["ta", "te", "ml", "xx"])
def test_languages_are_locked_to_kn_hi_en(lang):
    with pytest.raises(ValueError):
        routing.resolve(lang, None)


def test_resolve_accepts_auto_and_spellings():
    assert routing.resolve("KN", "") == ("kn", None)
    assert routing.resolve("auto", None) == (None, None)
    assert routing.resolve("", "romanised") == (None, "romanized")
    with pytest.raises(ValueError):
        routing.resolve("hi", "itnx")


def test_english_redecode_only_in_auto_mode_when_words_are_latin():
    latin = "I am twenty four years old"
    assert routing.should_redecode_english(None, "hi", latin)
    assert not routing.should_redecode_english("hi", "hi", latin)  # the app gave the language: trust it
    assert not routing.should_redecode_english(None, "kn", "ನನಗೆ ಇಪ್ಪತ್ನಾಲ್ಕು ವರ್ಷ")
    assert not routing.should_redecode_english(None, "hi", "मुझे BP की गोली मिली")  # code-mixed Hindi stays Hindi
    assert not routing.should_redecode_english(None, "en", latin)


def test_mostly_latin_words():
    assert routing.mostly_latin_words("yes I take iron tablets")
    assert not routing.mostly_latin_words("ಹೌದು iron ಮಾತ್ರೆ ತಗೊಳ್ತೀನಿ")
    assert not routing.mostly_latin_words("")


def test_wav_fast_path_only_for_16k_mono_pcm16():
    assert routing.pcm16_mono_16k(wav())
    assert not routing.pcm16_mono_16k(wav(rate=48000))
    assert not routing.pcm16_mono_16k(wav(channels=2))
    assert not routing.pcm16_mono_16k(b"ID3\x00 mp3 bytes")
    assert routing.wav_pcm16_payload(wav(n=10)) == b"\x01\x00" * 10
