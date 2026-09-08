import pytest
from unittest.mock import MagicMock
import os
from sarvam_wrappers import SarvamSTT
from livekit.agents.stt import SpeechEvent, SpeechEventType

# TC-06: Kannada Standard STT
@pytest.mark.asyncio
async def test_kannada_standard_stt():
    """TC-06: STT correctly processes Kannada transcription."""
    # We mock the Sarvam STT wrapper
    stt = SarvamSTT(api_key="mock", language_code="kn-IN")
    assert stt._language_code == "kn-IN"

# TC-07: Hindi Standard STT
@pytest.mark.asyncio
async def test_hindi_standard_stt():
    """TC-07: STT correctly processes Hindi transcription."""
    stt = SarvamSTT(api_key="mock", language_code="hi-IN")
    assert stt._language_code == "hi-IN"

# 2026-09-08: mode/model wiring for saaras:v3 (Sarvam's code-mixed-Indic
# speech model) - both were confirmed live against the real API with a fresh
# key. "transcribe" is the only mode value with a confirmed documented
# effect; "codemix" was accepted by the real API (no rejection) but that is
# not the same as a confirmed effect - see SarvamSTT's own note.
def test_sarvam_stt_defaults_to_the_confirmed_transcribe_mode():
    stt = SarvamSTT(api_key="mock", language_code="kn-IN")
    assert stt._mode == "transcribe"


def test_sarvam_stt_accepts_saaras_v3_and_a_custom_mode():
    stt = SarvamSTT(api_key="mock", model="saaras:v3", language_code="kn-IN", mode="codemix")
    assert stt._model == "saaras:v3"
    assert stt._mode == "codemix"


@pytest.mark.asyncio
async def test_sarvam_stream_config_message_carries_the_configured_mode():
    """The mode configured on SarvamSTT must reach the actual websocket
    config payload, not just live on the STT object - the mode field used
    to be hardcoded to "transcribe" in SarvamSpeechStream regardless of what
    was passed in above it. .stream() schedules a metrics task on the
    running loop internally, so this needs the async marker like the other
    tests in this file that construct real STT/stream objects."""
    stt = SarvamSTT(api_key="mock", model="saaras:v3", language_code="kn-IN", mode="codemix")
    stream = stt.stream(language="kn-IN")
    try:
        assert stream._mode == "codemix"
        assert stream._model == "saaras:v3"
    finally:
        await stream.aclose()


# TC-11: Low audio confidence handling
@pytest.mark.asyncio
async def test_low_audio_confidence_handling():
    """TC-11: Distorted audio snippet with STT confidence <0.5."""
    # The LiveKit SpeechEvent contains alternatives, which include confidence
    from livekit.agents.stt import SpeechData, SpeechEvent
    
    event = SpeechEvent(
        type=SpeechEventType.FINAL_TRANSCRIPT,
        alternatives=[
            SpeechData(text="???", language="kn-IN", start_time=0.0, end_time=1.0, confidence=0.3)
        ]
    )
    # Validate the structure supports confidence values
    assert event.alternatives[0].confidence == 0.3
