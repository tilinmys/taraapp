"""Zero-TTS deterministic slot filling: extractors, intake, audio bank, agent turn."""

import asyncio
import time
from unittest.mock import MagicMock

import pytest

import agent
from audio_bank import AudioPromptBank
from sakhi_domain import Language, RiskLevel, SakhiCallState
from sakhi_intake import INTAKE_QUESTIONS, IntakeProgress
from sakhi_slot_engine import (
    EMERGENCY_WAV, PROMPT_TEXT, QUESTION_CONFIGS, all_prompt_paths, extract_all,
    extract_blood_pressure, extract_demographics, extract_gestation, extract_polarity,
    localized, prompt_text, strip_fillers,
)

P = "assets/audio/prompts"


# --- extractors -----------------------------------------------------------

@pytest.mark.parametrize("text", [
    "140 90", "140/90", "140 by 90", "one forty over ninety", "140 ಮೇಲೆ 90",
    "ನೂರ ನಲವತ್ತು ಮೇಲೆ ತೊಂಬತ್ತು", "एक सौ चालीस बटा नब्बे", "bp 140 over 90",
])
def test_blood_pressure_shapes(text):
    assert extract_blood_pressure(text) == (140, 90)


def test_blood_pressure_single_number_is_systolic_only():
    assert extract_blood_pressure("150") == (150, None)
    assert extract_blood_pressure("ಗೊತ್ತಿಲ್ಲ") == (None, None)
    assert extract_blood_pressure("90 140") == (None, None)   # swapped: never recorded


@pytest.mark.parametrize("text,weeks", [
    ("5 ತಿಂಗಳು", 20), ("ಐದು ತಿಂಗಳು", 20), ("five months", 20), ("28 weeks", 28),
    ("ಇಪ್ಪತ್ತೆಂಟು ವಾರ", 28), ("सात महीने", 28),
])
def test_gestation_months_become_weeks(text, weeks):
    assert extract_gestation(text) == weeks


@pytest.mark.parametrize("text,expected", [
    ("ಲಕ್ಷ್ಮಿ, 24 ವರ್ಷ", ("ಲಕ್ಷ್ಮಿ", 24)),
    ("ಅವಳ ಹೆಸರು ರೂಪಾ", ("ರೂಪಾ", None)),
    ("ಇಪ್ಪತ್ನಾಲ್ಕು ವರ್ಷ", (None, 24)),
    ("her name is Priya and she is twenty four years", ("Priya", 24)),
    ("सीता, उम्र पच्चीस साल", ("सीता", 25)),
])
def test_demographics(text, expected):
    assert extract_demographics(text) == expected


def test_age_out_of_range_is_not_an_age():
    assert extract_demographics("ಲಕ್ಷ್ಮಿ 70 ವರ್ಷ")[1] is None


@pytest.mark.parametrize("text,value", [
    ("ಹೌದು", True), ("ಹೌದ್ರಿ", True), ("ಹೂಂ", True), ("haan", True),
    ("ಇಲ್ಲ", False), ("ಇಲ್ರೀ", False), ("ಆಗಿಲ್ಲ", False), ("नहीं", False),
    ("ಹೌದು ಆದರೆ ಈಗ ಇಲ್ಲ", False), ("ಸ್ಕ್ಯಾನ್ ಮಾಡಿದೆ", None),
])
def test_polarity(text, value):
    assert extract_polarity(text) is value


def test_fillers_stripped():
    assert strip_fillers("um ಅಕ್ಕ uh 140 90 sister") == "140 90"


def test_unit_word_is_never_part_of_a_name():
    slots = extract_all("um ಅಕ್ಕ ಲಕ್ಷ್ಮಿ, ಏಳು ತಿಂಗಳು", expecting="q01")
    assert slots == {"name": "ಲಕ್ಷ್ಮಿ", "gestational_weeks": 28}


def test_bare_number_only_read_by_the_question_asked():
    assert extract_all("24", expecting="q01")["age"] == 24
    assert "age" not in extract_all("24", expecting="q08")
    assert extract_all("150", expecting="q06") == {"bp_single": 150}
    assert "systolic" not in extract_all("140 90", expecting="q07")      # no BP word: a weight question
    assert extract_all("ಬಿಪಿ 140 90", expecting="q07")["systolic"] == 140


# --- intake state machine ----------------------------------------------------

def _wav(name, lang="kn"):
    return f"{P}/{lang}/{name}.wav"


def test_partial_answer_asks_only_the_missing_slot():
    intake = IntakeProgress(pending_id="q01")
    assert intake.ingest_slots("q01", extract_all("ಲಕ್ಷ್ಮಿ", "q01"), text="ಲಕ್ಷ್ಮಿ") == (False, _wav("q01_ask_age"))
    ok, wav = intake.ingest_slots("q01", extract_all("24", "q01"), text="24")
    assert (ok, wav) == (True, _wav("q02_full"))
    assert intake.answered["q01"] == "ಲಕ್ಷ್ಮಿ, 24"
    assert intake.pending_id == "q02"


def test_age_first_asks_name():
    intake = IntakeProgress(pending_id="q01")
    assert intake.ingest_slots("q01", extract_all("24 ವರ್ಷ", "q01"), text="24 ವರ್ಷ")[1] == _wav("q01_ask_name")


def test_bp_split_across_two_turns():
    intake = IntakeProgress(pending_id="q06")
    assert intake.ingest_slots("q06", extract_all("140", "q06"), text="140") == (False, _wav("q06_ask_diastolic"))
    ok, _ = intake.ingest_slots("q06", extract_all("90", "q06"), text="90")
    assert ok and intake.answered["q06"] == "140/90"


def test_omnidirectional_prefill_skips_later_question():
    intake = IntakeProgress(pending_id="q01")
    text = "ಲಕ್ಷ್ಮಿ 24 ವರ್ಷ, ಏಳು ತಿಂಗಳು"
    ok, wav = intake.ingest_slots("q01", extract_all(text, "q01"), text=text)
    assert ok
    assert "q02" in intake.answered and "from earlier answer" in intake.answered["q02"]
    assert wav != _wav("q02_full")


def test_danger_question_is_never_skipped():
    """A yes/no question's slot is its own id, filled only when it is asked."""
    intake = IntakeProgress()
    for q in INTAKE_QUESTIONS:
        if q.id == "q09":
            break
        intake.mark_satisfied(q.id, "fixture")
    intake.pending_id = "q08"
    intake.ingest_slots("q08", extract_all("ಹೌದು", "q08"), text="ಹೌದು")
    assert "q09" not in intake.answered


def test_yes_no_question_closes_on_polarity():
    intake = IntakeProgress(pending_id="q08")
    ok, _ = intake.ingest_slots("q08", extract_all("ಹೌದ್ರಿ", "q08"), text="ಹೌದ್ರಿ")
    assert ok and intake.answered["q08"] == "yes"


def test_unclear_answer_stalls_then_closes_best_effort():
    intake = IntakeProgress(pending_id="q06")
    for _ in range(intake.STALL_LIMIT):
        ok, wav = intake.ingest_slots("q06", extract_all("ಏನು", "q06"), text="ಏನು")
        assert not ok and wav == _wav("q06_full")
    ok, _ = intake.ingest_slots("q06", extract_all("ಏನು", "q06"), text="ಏನು")
    assert ok and "best-effort" in intake.answered["q06"]


def test_hindi_prompts_come_from_the_hindi_folder():
    intake = IntakeProgress(pending_id="q01")
    assert intake.ingest_slots("q01", {"name": "सीता"}, text="सीता", language="hi")[1] == _wav("q01_ask_age", "hi")


def test_whole_intake_reaches_the_closing_line():
    intake = IntakeProgress()
    answers = {"q01": "ಲಕ್ಷ್ಮಿ 24 ವರ್ಷ", "q02": "7 ತಿಂಗಳು", "q04": "ಎರಡನೇ ಗರ್ಭ, ಒಂದು ಮಗು", "q06": "120 80",
               "q07": "55 ಕೆಜಿ", "q26": "ಹಿಮೋಗ್ಲೋಬಿನ್ 11, ಶುಗರ್ ನಾರ್ಮಲ್"}
    intake.arm_next()
    wav = None
    for _ in range(40):
        qid = intake.pending_id
        if qid is None:
            break
        text = answers.get(qid, "ಇಲ್ಲ")
        ok, wav = intake.ingest_slots(qid, extract_all(text, qid), text=text)
        assert ok, qid
    assert wav == _wav("q17_full") and intake.complete


# --- prompts and the audio bank ------------------------------------------

def test_every_prompt_has_words_and_a_recording():
    for lang in ("kn", "hi"):
        for path in all_prompt_paths(lang):
            assert prompt_text(path, lang), path
            assert (agent.Path(path)).exists(), f"{path} not recorded: run scripts/prerecord_prompts.py"
    assert set(PROMPT_TEXT["kn"]) == set(PROMPT_TEXT["hi"])


def test_every_question_has_a_config():
    assert set(QUESTION_CONFIGS) == {q.id for q in INTAKE_QUESTIONS}


@pytest.fixture(scope="module")
def bank():
    b = AudioPromptBank(P)
    assert b.preload() == 56
    return b


def test_bank_lookup_is_fast_and_frames_are_20ms(bank):
    started = time.perf_counter()
    assert bank.has(f"{P}/kn/q01_full.wav")
    assert (time.perf_counter() - started) * 1000 < 1
    frames = bank.get(f"{P}/kn/q01_full.wav")
    assert frames and frames[0].sample_rate == 16000 and frames[0].samples_per_channel == 320
    assert len(bank.get("kn/q01_full.wav")) == len(frames)       # relative key, same prompt
    assert abs(bank.duration_ms("kn/q01_full.wav") - len(frames) * 20) < 20
    assert bank.get("kn/never_recorded.wav") is None


def test_bank_memory_is_the_pcm_once(bank):
    # every prompt's PCM held once: frames are views, not copies
    assert bank.bytes_loaded < 8 * 1024 * 1024


def test_extraction_is_linear_in_transcript_length():
    def cost(n):
        text = " ".join(["ಲಕ್ಷ್ಮಿ ಹೌದು ಏಳು ತಿಂಗಳು"] * n)
        started = time.perf_counter()
        for _ in range(5):
            extract_all(text, "q01")
        return time.perf_counter() - started
    small, big = cost(50), cost(400)
    assert big / small < 8 * 3     # 8x the text, well under quadratic (64x)


# --- the agent's turn loop -------------------------------------------------

def _message(text):
    m = MagicMock()
    m.text_content = text
    m.content = [text]
    return m


@pytest.fixture
def zero_tts(monkeypatch, bank):
    monkeypatch.setenv("VOICE_ZERO_TTS_MODE", "true")
    monkeypatch.setattr(agent, "AUDIO_BANK", bank)
    monkeypatch.setattr(agent, "_ZERO_TTS_READY", {})

    def make(pending="q01"):
        state = SakhiCallState(language=Language.KANNADA)
        for q in INTAKE_QUESTIONS:
            if q.id == pending:
                break
            state.intake.mark_satisfied(q.id, "fixture")
        state.intake.pending_id = pending
        sakhi = agent.SakhiAgent(instructions="test", chat_ctx=agent.llm.ChatContext())
        session = MagicMock()
        session.userdata = state
        sakhi._activity = MagicMock(session=session)
        return sakhi, session, state
    return make


def _played(session):
    return session.say.call_args.kwargs, session.say.call_args.args[0]


@pytest.mark.asyncio
async def test_agent_partial_answer_plays_micro_prompt_without_llm(zero_tts):
    sakhi, session, state = zero_tts("q01")
    with pytest.raises(agent.StopResponse):
        await sakhi.on_user_turn_completed(MagicMock(items=[]), _message("ಲಕ್ಷ್ಮಿ"))
    kwargs, text = _played(session)
    assert "audio" in kwargs and text == PROMPT_TEXT["kn"]["q01_ask_age"]
    assert state.name == "ಲಕ್ಷ್ಮಿ" and state.intake.pending_id == "q01"


@pytest.mark.asyncio
async def test_agent_records_bp_and_moves_on(zero_tts):
    sakhi, session, state = zero_tts("q06")
    with pytest.raises(agent.StopResponse):
        await sakhi.on_user_turn_completed(MagicMock(items=[]), _message("120 ಮೇಲೆ 80"))
    assert str(state.blood_pressure) == "120/80"
    assert state.intake.pending_id == "q07"
    assert sakhi._zt_last_wav == f"{P}/kn/q07_full.wav"


@pytest.mark.asyncio
async def test_agent_severe_bp_halts_and_plays_emergency(zero_tts):
    speech = "ನೂರ ಅರವತ್ತು ಮೇಲೆ ನೂರ ಹತ್ತು"      # 160/110: severe range
    sakhi, session, state = zero_tts("q06")
    with pytest.raises(agent.StopResponse):
        await sakhi.on_user_turn_completed(MagicMock(items=[]), _message(speech))
    assert state.intake_locked and state.risk_level is RiskLevel.HIGH_RISK
    assert sakhi._zt_last_wav == EMERGENCY_WAV
    # locked: every later turn replays the referral, the questionnaire never resumes
    with pytest.raises(agent.StopResponse):
        await sakhi.on_user_turn_completed(MagicMock(items=[]), _message("ಹೌದು ಸರಿ"))
    assert sakhi._zt_last_wav == EMERGENCY_WAV and session.say.call_count == 2


@pytest.mark.parametrize("speech", ["ರಕ್ತಸ್ರಾವ ಆಗ್ತಿದೆ", "ಫಿಟ್ಸ್ ಬಂತು"])
@pytest.mark.asyncio
async def test_agent_tier1_sign_halts_on_any_question(zero_tts, speech):
    sakhi, session, state = zero_tts("q08")
    with pytest.raises(agent.StopResponse):
        await sakhi.on_user_turn_completed(MagicMock(items=[]), _message(speech))
    assert state.intake_locked
    assert session.say.call_args.args[0] == PROMPT_TEXT["kn"]["emergency_referral"]


@pytest.mark.asyncio
async def test_agent_flag_off_keeps_the_llm_path(zero_tts, monkeypatch):
    monkeypatch.setenv("VOICE_ZERO_TTS_MODE", "false")
    sakhi, session, state = zero_tts("q06")
    await sakhi.on_user_turn_completed(MagicMock(items=[]), _message("120 80"))
    assert getattr(sakhi, "_zt_last_wav", None) is None


@pytest.mark.asyncio
async def test_agent_english_call_keeps_the_llm_path(zero_tts):
    sakhi, session, state = zero_tts("q06")
    state.language = Language.ENGLISH
    await sakhi.on_user_turn_completed(MagicMock(items=[]), _message("120 80"))
    assert getattr(sakhi, "_zt_last_wav", None) is None


@pytest.mark.asyncio
async def test_agent_repeat_request_replays_last_prompt(zero_tts):
    sakhi, session, state = zero_tts("q01")
    with pytest.raises(agent.StopResponse):
        await sakhi.on_user_turn_completed(MagicMock(items=[]), _message("ಲಕ್ಷ್ಮಿ"))
    with pytest.raises(agent.StopResponse):
        await sakhi.on_user_turn_completed(MagicMock(items=[]), _message("ಇನ್ನೊಮ್ಮೆ ಹೇಳಿ"))
    assert session.say.call_count == 2
    assert session.say.call_args.args[0] == PROMPT_TEXT["kn"]["q01_ask_age"]


@pytest.mark.asyncio
async def test_agent_decision_is_under_100ms(zero_tts):
    # Warm the process first, as a live worker already is before any call: the
    # first pipeline event in a process starts the event-writer thread and
    # imports status_server (~20-50 ms, more on a busy machine). That is
    # start-up cost, not the per-turn decision this test measures.
    warm, _, warm_state = zero_tts("q06")
    with pytest.raises(agent.StopResponse):
        await warm.on_user_turn_completed(MagicMock(items=[]), _message("120 ಮೇಲೆ 80"))

    sakhi, session, state = zero_tts("q06")
    started = time.perf_counter()
    with pytest.raises(agent.StopResponse):
        await sakhi.on_user_turn_completed(MagicMock(items=[]), _message("140 ಮೇಲೆ 90 ಅಕ್ಕ"))
    assert (time.perf_counter() - started) * 1000 < 100


def test_localized():
    assert localized(EMERGENCY_WAV, "hi") == f"{P}/hi/emergency_referral.wav"


# --- thinking noise, fragments and the adaptive VAD window (2026-11-02) ------------

@pytest.mark.asyncio
async def test_agent_ignores_backchannel_during_vitals(zero_tts):
    sakhi, session, state = zero_tts("q06")
    # Murmuring "ಹೂಂ" while she reads the BP cuff: swallowed, nothing said, no stall used.
    for noise in ("ಹೂಂ", "hmm", "uh"):
        assert sakhi._zero_tts_turn(state, noise, "t-1") is True
    assert state.intake.pending_id == "q06"
    assert state.intake._stall_counts.get("q06", 0) == 0
    session.say.assert_not_called()


@pytest.mark.asyncio
async def test_a_yes_to_a_danger_sign_question_is_never_dropped_as_noise(zero_tts):
    # q09: severe headache / blurred vision / swelling - a yes/no question.
    sakhi, session, state = zero_tts("q09")
    with pytest.raises(agent.StopResponse):
        await sakhi.on_user_turn_completed(MagicMock(items=[]), _message("ಹೌದು"))
    assert state.intake.pending_id != "q09" or state.intake._stall_counts.get("q09", 0) == 0
    assert "q09" in state.intake.answered
    session.say.assert_called()


@pytest.mark.asyncio
async def test_play_prompt_evaluates_vad(zero_tts):
    sakhi, session, state = zero_tts("q06")
    sakhi._vad_controller = MagicMock()
    sakhi._play_prompt(state, f"{P}/kn/q06_full.wav")
    assert sakhi._vad_controller.evaluate_agent_text.called
    assert "ಬಿಪಿ" in sakhi._vad_controller.evaluate_agent_text.call_args[0][0]


@pytest.mark.parametrize("qid, slow", [("q06", True), ("q07", True), ("q11", False), ("q16", False)])
def test_vitals_prompts_get_the_slow_window_closed_questions_stay_fast(qid, slow):
    text = agent.prompt_text(f"{P}/kn/{qid}_full.wav", "kn")
    assert bool(agent._VAD_SLOW_TRIGGER_RE.search(text)) is slow
    # The same for the Hindi recordings of the vitals questions.
    if slow:
        assert agent._VAD_SLOW_TRIGGER_RE.search(agent.prompt_text(f"{P}/hi/{qid}_full.wav", "hi"))


def test_vitals_prompt_switches_the_controller_to_the_thinking_window(zero_tts):
    sakhi, session, state = zero_tts("q06")
    vad_session = MagicMock()
    sakhi._vad_controller = agent.DynamicVADController(vad_session)
    sakhi._play_prompt(state, f"{P}/kn/q06_full.wav")
    assert sakhi._vad_controller.state == "HIGH_COGNITIVE_LOAD"
    sakhi._play_prompt(state, f"{P}/kn/q11_full.wav")
    assert sakhi._vad_controller.state == "TRANSACTIONAL"


@pytest.mark.asyncio
async def test_cut_off_bp_is_held_and_joined_to_the_rest(zero_tts):
    sakhi, session, state = zero_tts("q06")
    sakhi._vad_controller = MagicMock()
    # "BP 140 and ..." - cut off: nothing recorded yet, the listen window widens.
    assert sakhi._zero_tts_turn(state, "ಬಿಪಿ 140 ಮತ್ತು", "t-1") is True
    sakhi._vad_controller.fragment_recovery.assert_called_once()
    session.say.assert_not_called()
    assert state.blood_pressure is None
    # The rest arrives: both halves are read together.
    with pytest.raises(agent.StopResponse):
        await sakhi.on_user_turn_completed(MagicMock(items=[]), _message("90"))
    assert (state.blood_pressure.systolic, state.blood_pressure.diastolic) == (140, 90)
    assert getattr(sakhi, "_zt_fragment", None) is None


@pytest.mark.asyncio
async def test_a_fragment_with_no_continuation_is_processed_alone(zero_tts, monkeypatch):
    monkeypatch.setenv("VOICE_FRAGMENT_WAIT_S", "0.05")
    sakhi, session, state = zero_tts("q06")
    assert sakhi._zero_tts_turn(state, "ಬಿಪಿ 140 ಮತ್ತು", "t-1") is True
    session.say.assert_not_called()
    await asyncio.sleep(0.2)
    # Processed as it always was: systolic kept, Sakhi asks for the missing number.
    session.say.assert_called_once()
    assert state.intake.slot_values.get("systolic") == 140
    assert getattr(sakhi, "_zt_fragment", None) is None


def test_a_long_answer_ending_in_a_connective_is_not_held(zero_tts):
    sakhi, session, state = zero_tts("q06")
    sakhi._vad_controller = MagicMock()
    sakhi._zero_tts_turn(state, "ಇಂದು ಬೆಳಿಗ್ಗೆ ಪರೀಕ್ಷೆ ಮಾಡಿದಾಗ ಬಿಪಿ 120 ಮೇಲೆ 80 ಬಂತು ಮತ್ತು", "t-1")
    sakhi._vad_controller.fragment_recovery.assert_not_called()
    assert (state.blood_pressure.systolic, state.blood_pressure.diastolic) == (120, 80)


@pytest.mark.parametrize("text", ["ಬಿಪಿ 140 ಮತ್ತು 90", "bp 140 and 90", "बीपी 140 और 90"])
def test_bp_said_with_and_is_one_reading(text):
    assert extract_all(text, "q06") == {"systolic": 140, "diastolic": 90}


def test_and_between_implausible_numbers_is_not_a_bp():
    # Diastolic above systolic, or out of range: never a reading.
    assert "diastolic" not in extract_all("ಬಿಪಿ 90 ಮತ್ತು 140", "q06")
    assert "diastolic" not in extract_all("ವಯಸ್ಸು 24 ಮತ್ತು 30 ವಾರ", "q01")


# --- conversational escape hatch (2026-11-03) ---------------------------------------

@pytest.mark.parametrize("doubt", [
    "ಬಿಪಿ ಯಾಕೆ ಮುಖ್ಯ?",          # why is BP important?
    "ಸ್ವಲ್ಪ ತಾಳಿ",               # wait a little
    "ಎರಡು ನಿಮಿಷ",                # two minutes
    "why is bp important",
    "बीपी क्यों जरूरी है",
])
def test_conversational_query_escapes_to_llm(zero_tts, doubt):
    sakhi, session, state = zero_tts("q06")
    # Not an answer: hand the turn to the full pipeline (LLM + input guardrails + TTS).
    assert sakhi._zero_tts_turn(state, doubt, "turn-doubt-1") is False
    # The question stays armed, no stall spent, no recording re-played.
    assert state.intake.pending_id == "q06"
    assert state.intake._stall_counts.get("q06", 0) == 0
    session.say.assert_not_called()


@pytest.mark.parametrize("danger", [
    "ಫಿಟ್ಸ್ ಬಂದಿದೆ ಏನು ಮಾಡಬೇಕು?",               # fits - what should we do?
    "ತುಂಬಾ ರಕ್ತಸ್ರಾವ ಆಗುತ್ತಿದೆ ಏನು ಮಾಡಲಿ?",       # heavy bleeding - what do I do?
    # Fever escalates in the triage engine, so a fever question is referred at
    # once too: a danger sign is never handed to the model to "answer".
    "ಅಕ್ಕ ಜ್ವರ ಇದೆ ಏನು ಮಾಡಬೇಕು?",
])
def test_danger_sign_during_query_halts_immediately(zero_tts, danger):
    sakhi, session, state = zero_tts("q06")
    assert sakhi._zero_tts_turn(state, danger, "turn-emergency-1") is True
    assert state.intake_locked is True
    assert state.risk_level == RiskLevel.HIGH_RISK
    assert sakhi._zt_last_wav == EMERGENCY_WAV


@pytest.mark.asyncio
async def test_slot_answer_with_question_intonation_does_not_escape(zero_tts):
    sakhi, session, state = zero_tts("q06")
    # A reading spoken with a questioning tone ("120 over 80?")
    with pytest.raises(agent.StopResponse):
        await sakhi.on_user_turn_completed(MagicMock(items=[]), _message("120 ಮೇಲೆ 80?"))
    assert str(state.blood_pressure) == "120/80"
    assert state.intake.pending_id == "q07"


@pytest.mark.parametrize("pending, answer", [
    ("q20", "ಕಬ್ಬಿಣ ಮಾತ್ರೆ ತೆಗೆದುಕೊಳ್ಳುತ್ತಿದ್ದಾಳೆ"),   # medication: "iron tablets" is an answer
    ("q20", "ಯಾವುದೂ ಇಲ್ಲ"),                        # "none" - not "ಯಾವ" (which)
    ("q19", "ಎಲ್ಲಾ ನಾರ್ಮಲ್"),                      # history: "all normal"
    ("q15", "ಡಾಕ್ಟರ್ ನರ್ಸ್ ಇದ್ದಾರೆ"),               # birth plan: "doctor and nurse are there"
])
def test_topic_words_in_an_answer_do_not_escape(zero_tts, pending, answer):
    sakhi, session, state = zero_tts(pending)
    assert sakhi._zero_tts_turn(state, answer, "turn-answer-1") is True
    assert pending in state.intake.answered
    session.say.assert_called_once()


def test_diversion_regex_is_linear():
    text = " ".join(["ಕಬ್ಬಿಣ ಮಾತ್ರೆ ತೆಗೆದುಕೊಳ್ಳುತ್ತಿದ್ದಾಳೆ"] * 400)
    started = time.perf_counter()
    for _ in range(20):
        agent._QUERY_OR_DIVERSION_RE.search(text)
    assert (time.perf_counter() - started) * 1000 < 200
