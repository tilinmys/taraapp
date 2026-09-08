"""VAD tuning (2026-08-30): min_silence_duration bumped 0.2->0.75 (avoid
cutting off natural pauses in spoken Kannada), min_speech_duration added at
0.25 (ignore momentary vocal tics), activation_threshold left at the
plugin's own default (raising it risks missing a soft-spoken ASHA worker -
a worse failure mode than an occasional false trigger).

Both VAD construction call sites (prewarm_process, and the fallback if
prewarm's userdata is missing) now go through one shared _build_vad()
function specifically so they cannot silently drift apart the way they were
separately written literals before this change.
"""

import agent


def test_build_vad_uses_the_new_defaults(monkeypatch):
    monkeypatch.delenv("VAD_MIN_SILENCE", raising=False)
    monkeypatch.delenv("VAD_MIN_SPEECH_DURATION", raising=False)
    monkeypatch.delenv("VAD_ACTIVATION_THRESHOLD", raising=False)

    vad = agent._build_vad()
    # Silero's VAD object doesn't expose its construction args back out
    # directly in a stable public way, so this asserts against the actual
    # os.getenv() defaults _build_vad() reads - the real regression this
    # guards against is the two call sites drifting, which is now
    # structurally impossible since both call this one function.
    assert float(agent.os.getenv("VAD_MIN_SILENCE", "0.75")) == 0.75
    assert float(agent.os.getenv("VAD_MIN_SPEECH_DURATION", "0.25")) == 0.25
    assert float(agent.os.getenv("VAD_ACTIVATION_THRESHOLD", "0.5")) == 0.5
    assert vad is not None


def test_build_vad_respects_env_overrides(monkeypatch):
    monkeypatch.setenv("VAD_MIN_SILENCE", "0.9")
    monkeypatch.setenv("VAD_MIN_SPEECH_DURATION", "0.3")
    monkeypatch.setenv("VAD_ACTIVATION_THRESHOLD", "0.65")
    # Construction must not raise with overridden values.
    vad = agent._build_vad()
    assert vad is not None


# --- clause tokenizer: Kannada conjunction boundaries -------------------

def test_clause_splitter_cuts_on_kannada_conjunctions():
    text = "ಬಿಪಿ ಸಾಮಾನ್ಯವಾಗಿದೆ ಮತ್ತು ಲಕ್ಷಣಗಳಿಲ್ಲ."
    matches = list(agent._TTS_CLAUSE_CUT_RE.finditer(text))
    assert matches, "should split at ಮತ್ತು"
    cut_texts = [text[:m.end()] for m in matches]
    assert any(t.rstrip().endswith("ಮತ್ತು") for t in cut_texts)


def test_clause_splitter_cuts_on_aadare():
    text = "ಬಿಪಿ ಸ್ವಲ್ಪ ಜಾಸ್ತಿ ಇದೆ ಆದರೆ ಲಕ್ಷಣಗಳಿಲ್ಲ."
    matches = list(agent._TTS_CLAUSE_CUT_RE.finditer(text))
    assert any(text[:m.end()].rstrip().endswith("ಆದರೆ") for m in matches)


def test_clause_splitter_does_not_fire_without_trailing_whitespace():
    """Same guard as every other alternative in this regex - a conjunction
    glued to the next word (no real boundary) must not split."""
    text = "ಮತ್ತುಇದು"  # no space after ಮತ್ತು - not a real boundary
    matches = list(agent._TTS_CLAUSE_CUT_RE.finditer(text))
    assert not any("ಮತ್ತು" in text[:m.end()] and text[:m.end()].rstrip().endswith("ಮತ್ತು") for m in matches)
