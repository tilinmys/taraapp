import os
import sys
import asyncio
import base64
import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock

# Ensure project root is on PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

RUN_LIVE_TTS_TESTS = os.environ.get("RUN_LIVE_TTS_TESTS", "").strip() == "1"
if RUN_LIVE_TTS_TESTS:
    from dotenv import load_dotenv

    load_dotenv()
SARVAM_API_KEY = os.environ.get("SARVAM_API_KEY", "mock_key")


# ---------------------------------------------------------------------------
# HIDDEN TEST 1: Sarvam REST API Payload & PCM Unwrapping Contract
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sarvam_tts_payload_contract():
    """Verify that Sarvam TTS synthesizes audio and strips any WAV/RIFF headers,
    producing raw Int16 linear PCM audio frames required by LiveKit.
    """
    from sarvam_wrappers import SafeSarvamRESTTTS

    tts = SafeSarvamRESTTTS(api_key=SARVAM_API_KEY)

    test_kannada_phrase = (
        "ನಮಸ್ಕಾರ, ತಾಯಿಯ ಹೆಸರು ಮತ್ತು ವಯಸ್ಸು ಹೇಳಿ."
    )

    if SARVAM_API_KEY != "mock_key" and RUN_LIVE_TTS_TESTS:
        # Live integration assertion
        audio_result = await tts.synthesize(test_kannada_phrase)
        assert audio_result is not None, "TTS returned None"
        assert (
            audio_result.data is not None and len(audio_result.data) > 0
        ), "TTS returned empty audio data buffer"
        # Must NOT start with RIFF header (LiveKit AudioSource requires raw PCM16)
        assert not audio_result.data.startswith(
            b"RIFF"
        ), "CRITICAL: Audio data contains a RIFF/WAV header."
        assert (
            len(audio_result.data) % 2 == 0
        ), "PCM Int16 audio buffer must be byte-aligned (divisible by 2)."
    else:
        # Mock structural verification
        assert hasattr(
            tts, "synthesize"
        ), "SafeSarvamRESTTTS must implement a synthesize method."
        assert not tts.capabilities.streaming, (
            "REST TTS must remain non-streaming so AgentSession uses "
            "StreamAdapter instead of the hanging Sarvam WebSocket."
        )


# ---------------------------------------------------------------------------
# HIDDEN TEST 2: Unicode Kannada Punctuation Sanitization
# ---------------------------------------------------------------------------
def test_kannada_normalizer_hidden_edge_cases():
    """Verify that no Markdown, semicolons, colons, or math operators slip into the TTS engine."""
    from sakhi_normalizer import SakhiSpeechNormalizer

    dirty_inputs = [
        "ತಾಯಿ: Anjali; ವಯಸ್ಸು: 35. ಬಿಪಿ: 140/90**",
        "***ಮುಖ್ಯ ಸೂಚನೆ***: ತಾಯಿಗೆ ಜ್ವರ/ವಾಂತಿ ಇದೆಯೇ?",
        "ಹಂತ #1 - ಹೆಸರು - ದ್ಯಾವಮ್ಮ; ವಯಸ್ಸು_28",
    ]

    for dirty in dirty_inputs:
        cleaned = SakhiSpeechNormalizer.sanitize_for_tts(dirty)
        for forbidden in [
            "*",
            ";",
            ":",
            "#",
            "_",
            "**",
            "ಹಂತ #",
            "<",
            ">",
            "[",
            "]",
        ]:
            assert (
                forbidden not in cleaned
            ), f"Sanitizer leaked forbidden character '{forbidden}' in output: {cleaned}"
        # Slashes must be replaced with Kannada word 'ಅಥವಾ'
        if "/" in dirty:
            assert (
                "ಅಥವಾ" in cleaned
            ), f"Slash '/' was not converted to 'ಅಥವಾ' in {cleaned}"


# ---------------------------------------------------------------------------
# HIDDEN TEST 3: Tool Execution Dual-Dispatch (No-Silence Enforcement)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_tool_dual_dispatch_and_speech_safety():
    """Verify that when record_patient_intake runs:
    1. It persists to the DB repository.
    2. It immediately registers or dispatches a spoken response.
    3. It does NOT throw on null or string-casted inputs.
    """
    from sakhi_tools import SakhiIntakeTools

    mock_session = MagicMock()
    mock_session.say = AsyncMock()
    mock_repo = MagicMock()
    mock_repo.create_patient = AsyncMock()

    tools = SakhiIntakeTools(session=mock_session, repo=mock_repo)

    # Hidden edge-case: stringified numbers & leading/trailing whitespace
    result = await tools.record_patient_intake(name=" ದ್ಯಾವಮ್ಮ ", age=" 28 ")

    assert (
        "ದ್ಯಾವಮ್ಮ" in result
    ), "Tool return did not reflect back the cleaned patient name"
    # Verify session.say was triggered or an explicit non-empty directive is returned
    assert (
        mock_session.say.called or "ತಾಯಿ" in result
    ), "Tool completed in absolute silence! No audio was queued to the session."
    mock_repo.create_patient.assert_awaited_once_with(name="ದ್ಯಾವಮ್ಮ", age=28)
    assert not mock_session.say.called, (
        "A tool must not start a competing session.say() while LiveKit's active "
        "generation owns the speech handle."
    )

    invalid_result = await tools.record_patient_intake(name=None, age=None)
    assert invalid_result.strip(), "Invalid/null tool inputs must return a recovery prompt"


@pytest.mark.asyncio
async def test_clause_tokenizer_flushes_unpunctuated_tail():
    """A final LLM delta without sentence punctuation must still reach TTS."""
    from agent import EagerClauseTokenizer

    phrase = "ತಾಯಿಯ ಹೆಸರು ಮತ್ತು ವಯಸ್ಸು ಹೇಳಿ ಅಕ್ಕಾರ"
    chunks = EagerClauseTokenizer(min_chunk_chars=10).tokenize(phrase)
    assert chunks
    assert " ".join(chunks) == phrase
    assert chunks[-1].endswith("ಅಕ್ಕಾರ")


# ---------------------------------------------------------------------------
# HIDDEN TEST 4: OOP Hierarchy & State Machine Contract
# ---------------------------------------------------------------------------
def test_state_machine_and_entity_models():
    """Validate that intake domain models reject invalid clinical inputs (Type checking & range validation)."""
    from sakhi_tools import DemographicInput
    from pydantic import ValidationError

    # Valid case
    valid_data = DemographicInput(name="Ningamma", age=24)
    assert valid_data.name == "Ningamma"
    assert valid_data.age == 24

    # Invalid cases: Age out of biological bounds
    with pytest.raises(ValidationError):
        DemographicInput(name="Anjali", age=5)

    with pytest.raises(ValidationError):
        DemographicInput(name="Anjali", age=120)
