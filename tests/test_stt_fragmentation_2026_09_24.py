"""Regression cover for the 2026-09-24 STT fragmentation incident.

Measured on production (scripts/stt_accuracy_probe.py): with Sarvam's
high_vad_sensitivity on, one spoken answer came back as 3-4 context-free
finals; "ಇಪ್ಪತ್ತೆಂಟು" was cut into "ಇಪ್ಪತ್ತು" + "28" and the age read 20.
See docs/STT_FRAGMENTATION_INCIDENT.md.
"""

import json
from urllib.parse import parse_qs

import pytest

import sarvam_wrappers as sw
from clinical_terms import canonicalise
from sakhi_domain import detect_patient_demographics


def _query(**env):
    return sw._sarvam_stt_query(model="saaras:v3", language_code="kn-IN",
                                mode="codemix", current_protocol=True)


def test_high_vad_sensitivity_is_off_by_default(monkeypatch):
    monkeypatch.delenv("SARVAM_STT_HIGH_VAD_SENSITIVITY", raising=False)
    assert _query()["high_vad_sensitivity"] == "false"


def test_high_vad_sensitivity_can_still_be_opted_into(monkeypatch):
    monkeypatch.setenv("SARVAM_STT_HIGH_VAD_SENSITIVITY", "true")
    assert _query()["high_vad_sensitivity"] == "true"


def test_the_fragmented_transcript_is_exactly_what_broke_q01():
    """Documents the failure mode: the four finals Sarvam returned with high
    sensitivity on, joined as the turn layer joins them, extract the WRONG
    age - while the single final it returns with sensitivity off is right.
    Extraction is not the thing to loosen; the segmentation is."""
    fragmented = " ".join(["ತಾಯಿಯ ಹೆಸರು", "Priya Sharma", "ವಯಸ್ಸು ಇಪ್ಪತ್ತು", "28 ವರ್ಷ"])
    whole = "ತಾಯಿಯ ಹೆಸರು Priya Sharma ವಯಸ್ಸು 28 ವರ್ಷ"
    assert detect_patient_demographics(canonicalise(fragmented))[1] != 28
    assert detect_patient_demographics(canonicalise(whole))[1] == 28


def test_debug_record_never_carries_transcript_text(monkeypatch, caplog):
    seen = []
    monkeypatch.setattr(sw, "STT_DEBUG_SINK", seen.append)
    sw._emit_stt_debug({"stt_provider": "sarvam", "transcript_length": 9,
                        "transcript_hash": "abc123", "is_final": True})
    assert seen and "transcript" not in {k for k in seen[0] if not k.startswith("transcript_")}
    assert "ಪ್ರಿಯಾ" not in caplog.text


def test_segment_audio_stats():
    seg = sw._SegmentAudio()
    tone = (b"\x00\x40" * 1600)          # 1600 samples of 16384 -> -6 dBFS
    seg.add(tone)
    assert seg.samples == 1600
    assert sw._dbfs(seg.sumsq, seg.samples) == pytest.approx(-6.0, abs=0.1)
    seg.reset()
    assert seg.samples == 0 and sw._dbfs(seg.sumsq, seg.samples) is None
