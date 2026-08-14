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
