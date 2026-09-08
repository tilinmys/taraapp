"""The 7-stage ANC protocol, end to end (2026-09-02).

The four behaviours the Bidar pilot brief calls out by name, each locked
against the failure it exists to prevent:

  1. Stage 1 completes on name+age alone - no Thayi card, no RCH ID.
  2. An English caller receives ZERO Kannada tokens across consecutive turns.
  3. "BP 150/100" produces an immediate emergency transfer directive.
  4. Burst background noise does not become a false turn or a false answer.
"""

import asyncio
import json
import os
import tempfile
import types
from pathlib import Path

import pytest

import agent
from sakhi_domain import (
    BloodPressure,
    Language,
    RiskLevel,
    SakhiCallState,
    SymptomSet,
    detect_language_switch_request,
    detect_symptoms,
)
from sakhi_intake import ANC_STAGES, INTAKE_QUESTIONS, IntakeProgress
from sakhi_prompts import SAKHI_SYSTEM_PROMPT_EN, system_prompt_for
from sakhi_sql_repository import SQLitePatientRepository
from sakhi_tools import ClinicalToolset
from sakhi_triage import TRIAGE


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def toolset():
    repo = _run(SQLitePatientRepository(Path(tempfile.mkdtemp()) / "t.sqlite3").load())
    return ClinicalToolset(repo, TRIAGE)


def _ctx(state):
    return types.SimpleNamespace(userdata=state)


# --- 1. Stage 1 needs name and age ONLY ------------------------------------

def test_stage_one_completes_on_name_and_age_alone(toolset):
    """The most load-bearing simplification in the brief: an ASHA worker in a
    doorway has no card number, so requiring one is how a screening call dies
    before the blood pressure is ever taken."""
    async def go():
        state = SakhiCallState()
        assert state.intake.current_stage() == 1

        result = json.loads(
            await toolset.record_patient_intake(_ctx(state), name="Renukamma", age=24)
        )
        assert result["recorded"] is True

        state.intake.sync_from_state(state)
        assert state.intake.stage_complete(1), "name + age must finish Stage 1"
        assert state.intake.current_stage() == 2, "must transition cleanly to Stage 2"
        # Asserted on id, not an English `.text.startswith(...)`: this
        # file's own questionnaire is authored in Kannada script - the
        # literal English string this line checked for never matched and
        # this test has been silently broken since it was written.
        assert state.intake.next_question().id == "q02"
    _run(go())


def test_no_stage_asks_for_an_identity_document():
    banned = ("thayi", "rch", "aadhaar", "aadhar", "card number", "registration number")
    for q in INTAKE_QUESTIONS:
        low = q.text.lower()
        for term in banned:
            assert term not in low, f"{q.id} asks for an identity document: {q.text!r}"


def test_the_prompt_forbids_asking_for_a_card():
    for prompt in (system_prompt_for(Language.KANNADA), SAKHI_SYSTEM_PROMPT_EN):
        assert "NEVER ask for a Thayi card" in prompt


# --- 2. An English caller gets zero Kannada -------------------------------

def test_english_prompt_forbids_kannada_output():
    assert "ZERO Kannada words" in SAKHI_SYSTEM_PROMPT_EN
    assert "ZERO Kannada script" in SAKHI_SYSTEM_PROMPT_EN


def test_english_call_language_never_drifts_across_five_turns():
    """Five ordinary turns, including heavy code-switching, must leave the
    latch on English. Code-switching is normal rural speech, not a language
    change - a per-turn detector would flip on several of these."""
    state = SakhiCallState(language=Language.ENGLISH)
    turns = [
        "Her BP is one forty by ninety",
        "She took the tablet, the ANC scan is done",
        "The mother is from Basavakalyan, near the Anganwadi",
        "ಅಕ್ಕಾ said she has a headache",   # Kannada tokens inside her speech
        "We will send her to the Dodd-Arogya Kendra",
    ]
    for turn in turns:
        requested = detect_language_switch_request(turn)
        if requested is not None:
            state.switch_language(requested)
        assert state.language is Language.ENGLISH, f"latch drifted on: {turn!r}"
    assert state.language_switches == 0


def test_explicit_request_is_the_only_thing_that_switches_language():
    state = SakhiCallState(language=Language.ENGLISH)
    assert detect_language_switch_request("her BP is fine") is None

    requested = detect_language_switch_request("Kannada mathadi please")
    assert requested is Language.KANNADA
    assert state.switch_language(requested) is True
    assert state.language is Language.KANNADA
    assert state.language_switches == 1


# --- 3. BP 150/100 -> immediate emergency transfer -------------------------

def test_bp_150_over_100_triggers_immediate_transfer(toolset):
    async def go():
        state = SakhiCallState(patient_id="M-201")
        await toolset.record_bp(_ctx(state), 150, 100)

        assert state.blood_pressure == BloodPressure(150, 100)
        # 2026-09-10: hypertensive-alone is HIGH_RISK unconditionally now
        # (an explicit user decision - see sakhi_triage.py's module
        # docstring), so this is already the emergency level before any
        # symptom is even recorded, not a MODERATE-or-HIGH_RISK toss-up.
        assert state.assessment.level is RiskLevel.HIGH_RISK

        # Hypertensive + any danger sign is (still) the referral combination.
        await toolset.record_symptoms(_ctx(state), headache=True, blurry_vision=True)
        assert state.assessment.level is RiskLevel.HIGH_RISK
        assert state.intake_locked, "a referral must stop the questionnaire"

        check = json.loads(await toolset.check_pregnancy_risk_factors(_ctx(state)))
        assert check["emergency"] is True
        assert "immediate hospital transfer" in check["instruction"]
        assert "connect_smfmi_specialist" in check["instruction"]
    _run(go())


def test_severe_bp_alone_is_an_emergency(toolset):
    async def go():
        state = SakhiCallState(patient_id="M-201")
        await toolset.record_bp(_ctx(state), 165, 112)
        check = json.loads(await toolset.check_pregnancy_risk_factors(_ctx(state)))
        assert check["emergency"] is True
        assert check["trigger"] == "severe_bp"
    _run(go())


@pytest.mark.parametrize("phrase,expected", [
    ("ಫಿಟ್ಸ್ ಬಂತು", "convulsions"),
    ("she had a seizure", "convulsions"),
    ("ಕುಸು ಆಡ್ತಿಲ್ಲ", "reduced_fetal_movement"),
    ("baby not moving", "reduced_fetal_movement"),
    ("ನೀರು ಹೋಗ್ತಿದೆ", "severe_abdominal_pain"),
])
def test_every_obstetric_red_flag_is_detected(phrase, expected):
    assert getattr(detect_symptoms(phrase), expected) is True


def test_reduced_movement_is_third_trimester_only():
    """Same reported symptom, two gestations, two correct answers."""
    sym = SymptomSet(reduced_fetal_movement=True)
    assert TRIAGE.assess(None, sym, gestational_weeks=32).level is RiskLevel.HIGH_RISK
    assert TRIAGE.assess(None, sym, gestational_weeks=16).level is RiskLevel.OBSERVATION


# --- 4. Background noise must not become a turn or an answer ---------------

@pytest.mark.parametrize("burst", ["", ".", "uh", "hmm", "hello", "..."])
def test_noise_bursts_never_close_an_open_question(burst):
    """A SLOW (open) question must not be closed by a noise final. This is the
    "Sakhi skips questions very fast" bug: a half-heard fragment ticking off a
    question the worker never answered."""
    p = IntakeProgress()
    p.pending_id = "q17"          # SLOW: "anything else I should note?"
    assert p.record_answer(burst) is None
    assert p.pending_id == "q17", "a noise burst advanced the intake"


def test_vad_floor_is_tuned_for_one_syllable_assents(monkeypatch):
    """The acoustic half of the assent gate: a 0.25s floor sat above a spoken
    one-syllable answer, so those turns never reached STT at all."""
    monkeypatch.delenv("VAD_MIN_SPEECH_DURATION", raising=False)
    monkeypatch.delenv("VAD_PREFIX_PADDING", raising=False)
    assert float(os.getenv("VAD_MIN_SPEECH_DURATION", "0.12")) <= 0.15
    assert float(os.getenv("VAD_PREFIX_PADDING", "0.2")) >= 0.2
    assert agent._build_vad() is not None


@pytest.mark.parametrize("assent", ["ಹೂಂ", "ಇಲ್ಲ", "howdu", "haan", "yes", "no"])
def test_monosyllabic_assent_answers_a_yes_no_question(assent):
    """The semantic half. A one-syllable answer to a FAST question is a real
    answer - rejecting it made Sakhi re-ask a question she had been told."""
    p = IntakeProgress()
    p.pending_id = "q08"          # FAST: "is she taking the iron tablets?"
    assert p.record_answer(assent) == "q08"


def test_preemptive_generation_is_off_by_default(monkeypatch):
    monkeypatch.delenv("PREEMPTIVE_GENERATION", raising=False)
    assert agent.env_flag("PREEMPTIVE_GENERATION", False) is False


def test_noise_filter_defaults_to_the_telephony_model(monkeypatch):
    monkeypatch.delenv("NOISE_CANCELLATION", raising=False)
    assert agent._noise_filter() is not None
    monkeypatch.setenv("NOISE_CANCELLATION", "off")
    assert agent._noise_filter() is None


# --- protocol shape --------------------------------------------------------

def test_protocol_is_seven_stages_in_order():
    assert len(ANC_STAGES) == 7
    stages = [q.stage for q in INTAKE_QUESTIONS]
    assert stages == sorted(stages), "questions must be ordered by stage"
    assert set(stages) == set(range(1, 8)), "every stage must have questions"


def test_protocol_fits_a_ten_minute_call():
    """Ceiling raised from 20 to 25 on 2026-09-10, rewiring onto the
    "Antenatal Quick Check" PDF's fuller ASK/CHECK sections (24 questions
    total) - an explicit, disclosed growth for real clinical-completeness
    reasons, not unchecked accretion. Whether 24 questions still fits a
    5-10 minute call is a live-call timing question this count alone cannot
    answer - flagged for a real test call after deploy."""
    assert len(INTAKE_QUESTIONS) <= 25, (
        f"{len(INTAKE_QUESTIONS)} questions at one per turn will not fit a "
        "5-10 minute ANC visit"
    )


def test_bidar_entities_are_biased_into_stt():
    """2026-09-04: this list was cut from 19 place/facility terms to 5 after
    the FULL list (combined with the full danger-sign list below) made
    Deepgram reject the real WebSocket connection outright - "Keyterm limit
    exceeded. The maximum number of tokens across all keyterms is 500" -
    with STT fallback disabled, that is a completely deaf agent for the rest
    of the call. See DEEPGRAM_MEDICAL_KEYTERMS's own comment in agent.py for
    the live-API bisection that found the real safe ceiling. Only the terms
    that survived that cut are asserted here; the rest (Basavakalyan,
    Humnabad, Bhalki, Aurad, Kamalnagar, the given names, Anganwadi, etc.)
    are a deliberate, known, accepted gap now - re-adding them requires
    re-testing against the real Deepgram API, not just editing this
    assertion list back to what it used to say.
    """
    for term in ["Bidar", "Chitguppa", "taluk hospital", "district hospital", "PHC"]:
        assert term in agent.DEEPGRAM_MEDICAL_KEYTERMS, f"{term} not biased into STT"


def test_every_danger_sign_phrase_is_biased_into_stt():
    """2026-09-04 UPDATE: the version of this test that asserted "every
    Kannada phrase detect_symptoms() can match is in the STT boost list"
    described a real bug at the time (a hand-typed, stale second copy of
    sakhi_domain's _SYMPTOM_PATTERNS) but its FIX broke a live call the
    moment it shipped - the full derived list (36 terms) plus the full
    Bidar list (19 terms) exceeded Deepgram's real per-connection keyterm
    budget and the WebSocket handshake was rejected outright, leaving the
    agent deaf with no fallback for the whole call. "Complete" and
    "connects" turned out to be in tension, and a boost list that never
    connects boosts nothing.

    So DEEPGRAM_MEDICAL_KEYTERMS is now a TESTED-SAFE PREFIX, not the full
    set - _SYMPTOM_PATTERNS was reordered (Tier-1 signs first) so the prefix
    that fits is the highest-acuity one: vaginal_bleeding and
    severe_abdominal_pain are asserted here IN FULL because the prefix
    covers them completely; reduced_fetal_movement's Kannada phrasing is
    asserted ABSENT, on purpose - it did not make the cut, and
    detect_symptoms() still catches it whenever Deepgram's unaided base
    model transcribes it correctly, this list just no longer helps it do
    so. Widening the prefix again needs a fresh live-API bisection (see
    agent.py's comment for the method), not just changing this test.
    """
    full_gap_symptoms = agent.DEEPGRAM_MEDICAL_KEYTERMS

    for phrase in ("ರಕ್ತಸ್ರಾವ", "ಬ್ಲೀಡಿಂಗ್"):
        assert phrase in full_gap_symptoms, f"vaginal_bleeding phrase {phrase!r} must be boosted"
    for phrase in ("ಹೊಟ್ಟೆ ಕಲ್ಲು", "ಹೊಟ್ಟೆ ಗಟ್ಟಿ", "ಹೊಟ್ಟೆ ನೋವು ವಿಪರೀತ",
                   "ನೀರು ಹೋಗ್ತಿದೆ", "ನೀರು ಹೋಗುತ್ತಿದೆ", "ನೀರಿನ ಸ್ರಾವ"):
        assert phrase in full_gap_symptoms, f"severe_abdominal_pain phrase {phrase!r} must be boosted"

    # The known, accepted gap - not boosted, and this must stay true until a
    # fresh live-API bisection proves there is room to add it back.
    for phrase in ("ಕುಸು ಆಡ್ತಿಲ್ಲ", "ಕುಸು ಆಡುತ್ತಿಲ್ಲ", "ಮಗು ಆಡ್ತಿಲ್ಲ",
                   "ಚಲನೆ ಕಡಿಮೆ", "ಅಲುಗಾಡ್ತಿಲ್ಲ", "ಓಡಾಟ ಕಡಿಮೆ"):
        assert phrase not in full_gap_symptoms, (
            f"{phrase!r} (reduced_fetal_movement) is back in the list - if this is "
            "deliberate, it must have been re-verified live against the real "
            "Deepgram API (see agent.py's DEEPGRAM_MEDICAL_KEYTERMS comment), "
            "not just re-added"
        )

    # The real, hard constraint this whole incident was about: stay well
    # under the live-tested failure boundary (14 items failed; 12 passed
    # with margin in the bisection this list is built from).
    assert len(agent.DEEPGRAM_MEDICAL_KEYTERMS) <= 22, (
        "DEEPGRAM_MEDICAL_KEYTERMS grew past the live-tested-safe size again - "
        "re-run the bisection against the real Deepgram API before widening it, "
        "the same way this list broke a live call the last time it grew "
        "without that check"
    )


@pytest.mark.skipif(
    not (os.getenv("DEEPGRAM_API_KEY") and os.getenv("SAKHI_TEST_LIVE_DEEPGRAM")),
    reason="set DEEPGRAM_API_KEY and SAKHI_TEST_LIVE_DEEPGRAM=1 to run this against the real API",
)
def test_deepgram_keyterm_list_actually_connects_live():
    """The regression the size-cap test above cannot catch on its own: the
    2026-09-04 incident was a real WebSocket handshake rejected by Deepgram
    with the exact keyterm list build_stt() sends, not a theoretical token
    count. A future edit could stay under the 22-item cap and still pick
    heavier Kannada content that blows the same real budget - "small enough"
    was never really about the count, and the only trustworthy check is
    connecting for real. Off by default (network + API key + real request
    cost); opt in with SAKHI_TEST_LIVE_DEEPGRAM=1 to actually exercise this,
    e.g. before a pilot deployment or after touching this list again.
    """
    import urllib.parse

    import aiohttp

    async def _try_connect() -> tuple[bool, str]:
        params = [("model", "nova-3"), ("language", "en"),
                  ("sample_rate", "16000"), ("encoding", "linear16")]
        for kt in agent.DEEPGRAM_MEDICAL_KEYTERMS:
            params.append(("keyterm", kt))
        url = f"wss://api.deepgram.com/v1/listen?{urllib.parse.urlencode(params)}"
        headers = {"Authorization": f"Token {os.environ['DEEPGRAM_API_KEY']}"}
        async with aiohttp.ClientSession() as session:
            try:
                async with session.ws_connect(url, headers=headers):
                    return True, ""
            except Exception as exc:  # noqa: BLE001 - reporting the real API error is the point
                return False, str(exc)

    ok, err = _run(_try_connect())
    assert ok, f"the real DEEPGRAM_MEDICAL_KEYTERMS list failed to connect live: {err}"
