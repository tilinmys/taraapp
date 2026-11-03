"""DPDP Act s.8(5): a committed turn's words never reach a log line or a
pipeline event at ingress, before guardrail redaction runs.

The turn below carries a name, a village and a blood-pressure reading. The
ingress log, the turn-gate debug record and the canonicalisation event must
describe it by length only, and the Sarvam STT wrapper must log a message's
type, never its payload.
"""

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import agent
from sakhi_domain import Language, SakhiCallState

NAME = "ಲಕ್ಷ್ಮಿ"
VILLAGE = "ಕಮಠಾಣ"
TURN = f"ನನ್ನ ಹೆಸರು {NAME} ಊರು {VILLAGE} ಬಿಪಿ 148 ಬೈ 96"
SECRETS = (NAME, VILLAGE, "148")


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.messages: list[str] = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def _agent():
    state = SakhiCallState(language=Language.KANNADA)
    sakhi = agent.SakhiAgent(instructions="test", chat_ctx=agent.llm.ChatContext())
    session = MagicMock()
    session.userdata = state
    sakhi._activity = MagicMock(session=session)
    return sakhi


def _message(text):
    message = MagicMock()
    message.text_content = text
    message.content = [text]
    message.id = "turn-pii-1"
    return message


@pytest.mark.asyncio
async def test_ingress_logs_and_events_carry_no_transcript(monkeypatch):
    events = []
    monkeypatch.setattr(agent, "pipeline_event", lambda *a, **k: events.append((a, k)))
    monkeypatch.setenv("SAKHI_TURN_GATE_DEBUG", "true")
    capture = _Capture()
    log = logging.getLogger("agent")
    log.addHandler(capture)
    old_level = log.level
    log.setLevel(logging.DEBUG)
    try:
        try:
            await _agent().on_user_turn_completed(MagicMock(items=[]), _message(TURN))
        except agent.StopResponse:
            pass
    finally:
        log.removeHandler(capture)
        log.setLevel(old_level)

    ingress = [m for m in capture.messages if m.startswith("TABLET_AUDIO_INGRESS")]
    gate_logs = [m for m in capture.messages if m.startswith("[SAKHI_TURN_GATE]")]
    assert ingress and gate_logs, "the ingress and gate records must still be written"
    assert f"chars={len(TURN)}" in ingress[0]

    gate = [k for _, k in events if k.get("event") == "turn_gate_debug"]
    assert gate and gate[0]["raw_transcript_chars"] == len(TURN)
    assert "raw_transcript" not in gate[0] and "canonical_transcript" not in gate[0]

    for text in ingress + gate_logs:
        for secret in SECRETS:
            assert secret not in text, f"{secret!r} leaked into a log line: {text!r}"
    for args, details in events:
        if details.get("event") not in {"turn_gate_debug", "transcript_canonicalised"}:
            continue
        flat = json.dumps([args, details], ensure_ascii=False, default=str)
        for secret in SECRETS:
            assert secret not in flat, f"{secret!r} leaked into pipeline event {details.get('event')}"


def test_sarvam_stt_logs_the_message_type_only():
    source = Path(agent.__file__).with_name("sarvam_wrappers.py").read_text(encoding="utf-8")
    assert 'logger.debug("Sarvam STT received: %s", resp)' not in source
    assert 'logger.debug("Sarvam STT received response type: %s", resp.get("type", "unknown"))' in source
