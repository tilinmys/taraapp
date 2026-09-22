from unittest.mock import MagicMock

import pytest

import agent
from sakhi_domain import Language, SakhiCallState
from sakhi_intake import INTAKE_QUESTIONS, QUESTIONS_BY_ID


def _agent_with_armed_q08():
    state = SakhiCallState(language=Language.KANNADA)
    state.identify(name="Anjali", age=24)
    for question in INTAKE_QUESTIONS:
        if question.id == "q08":
            break
        state.intake.mark_satisfied(question.id, "test fixture")
    state.intake.pending_id = "q08"

    sakhi = agent.SakhiAgent(instructions="test", chat_ctx=agent.llm.ChatContext())
    session = MagicMock()
    session.userdata = state
    sakhi._activity = MagicMock(session=session)
    return sakhi, session, state


def _message(text):
    message = MagicMock()
    message.text_content = text
    message.content = [text]
    return message


@pytest.mark.asyncio
async def test_affirmative_closed_answer_advances_and_stops_llm(monkeypatch):
    monkeypatch.setenv("SAKHI_DETERMINISTIC_INTAKE_ENABLED", "true")
    sakhi, session, state = _agent_with_armed_q08()

    with pytest.raises(agent.StopResponse):
        await sakhi.on_user_turn_completed(MagicMock(items=[]), _message("ಹೌದು"))

    assert state.intake.answered["q08"] == "ಹೌದು"
    assert state.intake.pending_id == "q24"
    session.say.assert_called_once()
    assert QUESTIONS_BY_ID["q24"].text in session.say.call_args.args[0]


@pytest.mark.asyncio
async def test_unknown_closed_answer_falls_through(monkeypatch):
    monkeypatch.setenv("SAKHI_DETERMINISTIC_INTAKE_ENABLED", "true")
    sakhi, session, state = _agent_with_armed_q08()

    await sakhi.on_user_turn_completed(
        MagicMock(items=[]), _message("ಬಹುಶಃ ನಂತರ ನೋಡೋಣ")
    )

    assert state.intake.pending_id is None  # existing tracker behavior
    session.say.assert_not_called()


@pytest.mark.asyncio
async def test_router_flag_off_restores_llm_fallthrough(monkeypatch):
    monkeypatch.setenv("SAKHI_DETERMINISTIC_INTAKE_ENABLED", "false")
    sakhi, session, state = _agent_with_armed_q08()

    await sakhi.on_user_turn_completed(MagicMock(items=[]), _message("ಹೌದು"))

    assert state.intake.answered["q08"] == "ಹೌದು"
    assert state.intake.pending_id is None
    session.say.assert_not_called()
