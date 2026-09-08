"""End-to-end MOCK scenario harness for Sakhi, 2026-09-08 - no live
microphone, no live audio device, no real network calls to any STT/LLM/TTS
vendor. Every "latency" number below comes from the harness's own
instrumentation timing a mocked call, not a real API round-trip - that is
what "without using a live microphone or audio devices" actually buys you,
and this file says so rather than dressing mock numbers up as production
telemetry.

FOUR CORRECTIONS FROM THE ORIGINAL BRIEF, made against the real codebase
rather than silently working around it (same discipline as every other
brief handled this session):

1. "Pydantic schema field" (intake progression gate) - this codebase does
   NOT use Pydantic anywhere. The real, existing, equivalent mechanism is
   `IntakeProgress.record_answer()` (sakhi_intake.py): a question is only
   marked answered when it returns a question id, never on a bare filler.
   That is the deterministic, code-owned gate the brief is actually asking
   for - implemented with word-lists and regexes instead of a schema
   library, and already the thing exercised below.
2. `risk_level` values - the real enum (sakhi_domain.RiskLevel) is
   ROUTINE / OBSERVATION / MODERATE / HIGH_RISK. The brief's
   "OBSERVATION/CRITICAL" for scenario 1 and "LOW" for scenario 2 don't
   exist; corrected to HIGH_RISK and ROUTINE respectively (OBSERVATION
   itself IS real, just not the pairing the brief used for scenario 1).
3. Scenario 3's expected "MODERATE" is not reachable with the symptoms as
   specified. At the time this was written, TriageEngine's calibrated matrix
   (sakhi_triage.py) reached MODERATE only for hypertensive BP with NO
   symptoms; severe pedal edema (`swelling`) is itself a tracked symptom,
   so hypertensive+edema was HIGH_RISK and non-hypertensive+edema was
   OBSERVATION - no path to MODERATE once a real danger symptom is present.
   UPDATE (2026-09-10): per an explicit user decision made rewiring the
   intake onto a real ANC screening PDF, plain hypertensive-alone now ALSO
   reaches HIGH_RISK - RiskLevel.MODERATE is consequently unreachable by ANY
   combination, not just this one. IFA non-adherence is a checklist item
   (q08), not a triage input, in this codebase. Scenario 3 below asserts the
   real outcome (OBSERVATION, no BP given) and documents this explicitly
   rather than asserting a value the engine cannot produce - a test
   asserting the wrong answer to make a brief's literal wording pass would
   be worse than no test.
4. Scenario 4's "हेलो ठीक है जी" surfaced a REAL gap while writing this
   file (not from a prior live report): sakhi_intake.py's filler blocklist
   only had the ROMANIZED "theek hai" (added 2026-09-08, same day) - a real
   Hindi transcript renders in Devanagari, so "ठीक है"/"ठीक है जी" and the
   "हेलो" spelling were unguarded. Fixed in sakhi_intake.py as part of
   writing this test, per "fix it until it's fixed" - see the module's own
   comment there for the exact loop: found -> reproduced -> fixed ->
   reproduced again clean, below.

Run: python -m pytest tests/test_mock_scenarios.py -v -s
     (-s so the printed latency summary tables actually show)
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

import agent
from sakhi_domain import (
    BloodPressure,
    Language,
    RiskLevel,
    SakhiCallState,
    SymptomSet,
    detect_symptoms,
)
from sakhi_intake import INTAKE_QUESTIONS, IntakeProgress
from sakhi_sql_repository import SQLitePatientRepository
from sakhi_tools import ClinicalToolset
from sakhi_triage import TRIAGE


def _run(coro):
    return asyncio.run(coro)


def _ctx(state: SakhiCallState):
    """The exact RunContext stand-in this codebase's own tests already use
    (test_clinical_batch.py) - tool methods only ever read `ctx.userdata`."""
    return types.SimpleNamespace(userdata=state)


@pytest.fixture
def toolset():
    repo = _run(SQLitePatientRepository(Path(tempfile.mkdtemp()) / "t.sqlite3").load())
    return ClinicalToolset(repo, TRIAGE)


# --------------------------------------------------------------------------
# Mock latency instrumentation
#
# Simulates the four pipeline stages this codebase's own turn_latency event
# (agent.py) reports on a real call: STT finalization, LLM TTFT, TTS TTFB,
# and total TTFA. Each mock stage sleeps a small, fixed, deliberately-fast
# duration standing in for "a provider responded" - there is no network call
# here, so a <1000ms assertion on these numbers tests that THIS HARNESS's
# instrumentation and turn loop introduce no unexpected stall, not that any
# real Sarvam/Groq/Vakyam endpoint is fast. A real TTFA budget can only be
# verified against a live call (see scripts/latency_benchmark_20260904.py
# for that class of test, which this deliberately is not).
# --------------------------------------------------------------------------

class MockSTT:
    """Intercepts what would be an audio buffer and returns a canned
    transcript instead - the "STT mock" the brief asks for. `delay_s`
    stands in for a real provider's finalization latency."""

    def __init__(self, delay_s: float = 0.05):
        self.delay_s = delay_s

    async def transcribe(self, transcript: str) -> tuple[str, float]:
        t0 = time.perf_counter()
        await asyncio.sleep(self.delay_s)
        return transcript, (time.perf_counter() - t0) * 1000


class MockLLM:
    """Intercepts the point where a real turn would call the LLM and
    instead returns a pre-scripted single-generation reply combining the
    acknowledgment and next question - directly modelling "unified prompt
    generation... no double-turn tool calls", the thing to actually verify
    being that this is ONE call, not two."""

    def __init__(self, delay_s: float = 0.08):
        self.delay_s = delay_s
        self.calls = 0

    async def generate(self, ack_and_question: str) -> tuple[str, float]:
        self.calls += 1
        t0 = time.perf_counter()
        await asyncio.sleep(self.delay_s)
        return ack_and_question, (time.perf_counter() - t0) * 1000


class MockTTS:
    """Intercepts session.say()/streaming TTS - records TTFB, never touches
    a real audio device or speaker."""

    def __init__(self, delay_s: float = 0.06):
        self.delay_s = delay_s
        self.spoken: list[str] = []

    async def say(self, text: str) -> float:
        t0 = time.perf_counter()
        await asyncio.sleep(self.delay_s)
        self.spoken.append(text)
        return (time.perf_counter() - t0) * 1000


class TurnLatencyLog:
    """Collects one row per mocked turn, for the printed summary table the
    brief asks for - the terminal-facing equivalent of agent.py's own
    turn_latency pipeline event."""

    def __init__(self, scenario: str):
        self.scenario = scenario
        self.rows: list[dict] = []

    def record(self, turn_label: str, stt_ms: float, llm_ms: float, tts_ms: float) -> float:
        total = stt_ms + llm_ms + tts_ms
        self.rows.append({
            "turn": turn_label, "stt_ms": stt_ms, "llm_ttft_ms": llm_ms,
            "tts_ttfb_ms": tts_ms, "total_ttfa_ms": total,
        })
        return total

    def print_summary(self) -> None:
        print(f"\n=== {self.scenario} — turn latency summary (mocked, not live) ===")
        print(f"{'turn':<28}{'STT':>10}{'LLM TTFT':>12}{'TTS TTFB':>12}{'Total TTFA':>14}")
        for row in self.rows:
            print(
                f"{row['turn']:<28}{row['stt_ms']:>9.1f}m{row['llm_ttft_ms']:>11.1f}m"
                f"{row['tts_ttfb_ms']:>11.1f}m{row['total_ttfa_ms']:>13.1f}m"
            )


@contextmanager
def _mock_pipeline(scenario_name: str):
    """One STT/LLM/TTS mock set + latency log per scenario."""
    stt, llm, tts = MockSTT(), MockLLM(), MockTTS()
    log = TurnLatencyLog(scenario_name)
    try:
        yield stt, llm, tts, log
    finally:
        log.print_summary()


async def _run_turn(stt, llm, tts, log, *, turn_label, caller_text, agent_reply):
    """One mocked turn: STT "hears" caller_text, LLM produces ONE reply
    (ack + next question together), TTS speaks it. Returns (transcript,
    llm_call_count_delta) so callers can assert single-generation behaviour."""
    transcript, stt_ms = await stt.transcribe(caller_text)
    calls_before = llm.calls
    reply, llm_ms = await llm.generate(agent_reply)
    tts_ms = await tts.say(reply)
    total = log.record(turn_label, stt_ms, llm_ms, tts_ms)
    assert total < 1000, f"{turn_label}: mocked TTFA {total:.1f}ms exceeded the 1000ms budget"
    assert llm.calls == calls_before + 1, (
        f"{turn_label}: LLM called {llm.calls - calls_before} times for one turn - "
        "double-turn generation (ack as its own turn, then a second call for the "
        "question) is exactly the failure mode this assertion guards against"
    )
    return transcript


# ==========================================================================
# Scenario 1 (Kannada) - ASHA Manjula, pre-eclampsia pattern -> HIGH_RISK
# ==========================================================================

def test_scenario_1_kannada_preeclampsia_asha_manjula(toolset):
    async def go():
        with _mock_pipeline("Scenario 1: Kannada preeclampsia (ASHA Manjula)") as (stt, llm, tts, log):
            state = SakhiCallState(language=Language.KANNADA)

            await _run_turn(
                stt, llm, tts, log, turn_label="name+age",
                caller_text="ಮಂಜುಳಾ, ಇಪ್ಪತ್ತೆಂಟು ವರ್ಷ",
                agent_reply="ಸರಿ, ಬರೆದುಕೊಂಡೆ. ಈಗ ಎಷ್ಟು ತಿಂಗಳು ಗರ್ಭ?",
            )
            state.identify(name="Manjula", age=28)

            await _run_turn(
                stt, llm, tts, log, turn_label="gestational_age",
                caller_text="ಮೂವತ್ತೆರಡು ವಾರ",
                agent_reply="ಆಯ್ತು. ರಕ್ತದೊತ್ತಡ ಎಷ್ಟು?",
            )
            await toolset.record_gestational_age(_ctx(state), 32)
            assert state.gestational_weeks == 32

            await _run_turn(
                stt, llm, tts, log, turn_label="blood_pressure",
                caller_text="ನೂರೈವತ್ತು ಮೇಲೆ ತೊಂಬತ್ತೈದು",
                agent_reply="ಸರಿ, ಬರೆದುಕೊಂಡೆ. ತಲೆನೋವು ಅಥವಾ ಕಣ್ಣು ಮಂಜು ಇದೆಯ?",
            )
            result = json.loads(await toolset.record_bp(_ctx(state), 150, 95))
            assert result["persisted"] is False  # no patient_id yet - honestly reported, not claimed

            symptoms_heard = detect_symptoms("ವಿಪರೀತ ತಲೆನೋವು ಮತ್ತು ಕಣ್ಣು ಮಂಜು ಮಂಜು ಆಗ್ತಿದೆ")
            assert symptoms_heard.headache and symptoms_heard.blurry_vision
            await _run_turn(
                stt, llm, tts, log, turn_label="symptom_screen",
                caller_text="ವಿಪರೀತ ತಲೆನೋವು ಮತ್ತು ಕಣ್ಣು ಮಂಜು ಮಂಜು ಆಗ್ತಿದೆ",
                agent_reply="ಒಂದು ಕ್ಷಣ, ಅದನ್ನು ಈಗ ದಾಖಲಿಸುತ್ತೇನೆ. ತಜ್ಞರನ್ನು ಸಂಪರ್ಕಿಸುತ್ತೇನೆ.",
            )
            result = json.loads(await toolset.record_symptoms(
                _ctx(state), headache=True, blurry_vision=True
            ))

            assert state.risk_level is RiskLevel.HIGH_RISK, (
                f"BP 150/95 (hypertensive) + headache/blurry vision (symptomatic) "
                f"must reach HIGH_RISK per sakhi_triage.py's own matrix; got {state.risk_level}"
            )
            # Real key from ClinicalToolset._result() is "escalation_required",
            # not "requires_escalation" - caught running this test.
            assert result["escalation_required"] is True
            assert state.assessment.requires_escalation is True

    _run(go())


# ==========================================================================
# Scenario 2 (Kannada) - ASHA Rekha, routine ANC -> ROUTINE
# ==========================================================================

def test_scenario_2_kannada_routine_anc_asha_rekha(toolset):
    async def go():
        with _mock_pipeline("Scenario 2: Kannada routine ANC (ASHA Rekha)") as (stt, llm, tts, log):
            state = SakhiCallState(language=Language.KANNADA)

            await _run_turn(
                stt, llm, tts, log, turn_label="name+age",
                caller_text="ರೇಖಾ, ಇಪ್ಪತ್ತಾರು ವರ್ಷ",
                agent_reply="ಸರಿ. ಈಗ ಎಷ್ಟು ತಿಂಗಳು ಗರ್ಭ?",
            )
            state.identify(name="Rekha", age=26)

            await _run_turn(
                stt, llm, tts, log, turn_label="gestational_age",
                caller_text="ಇಪ್ಪತ್ತೆರಡು ವಾರ",
                agent_reply="ಆಯ್ತು. ರಕ್ತದೊತ್ತಡ ಎಷ್ಟು?",
            )
            await toolset.record_gestational_age(_ctx(state), 22)

            await _run_turn(
                stt, llm, tts, log, turn_label="blood_pressure",
                caller_text="ನೂರಹತ್ತು ಮೇಲೆ ಎಪ್ಪತ್ತು",
                agent_reply="ಸರಿ, ಬರೆದುಕೊಂಡೆ. ತೂಕ ಎಷ್ಟು?",
            )
            await toolset.record_bp(_ctx(state), 110, 70)

            # q03-q05, q21 (obstetric history: LMP, prior pregnancies/
            # deliveries, prior C-section/complication, conception mode) are
            # NOT auto-satisfied by any tool - unlike q01/q02/q06/q09, a real
            # call must actually ask them to reach q07/q08. Answered briefly
            # here for a genuinely clean, complete progression rather than
            # skipping ahead artificially. sync_from_state() first, or
            # arm_next() would arm q01 again (still technically "unanswered"
            # in the tracker until synced). q21 added 2026-09-10 (Antenatal
            # Quick Check rewrite) - it sits in Stage 2 after q05, so it must
            # be answered here too or arm_next() below would arm it instead
            # of q07.
            state.intake.sync_from_state(state)
            for qid, answer in (
                ("q03", "ಎರಡು ತಿಂಗಳ ಹಿಂದೆ"),
                ("q04", "ಇದೇ ಮೊದಲ ಗರ್ಭ"),
                ("q05", "ಇಲ್ಲ"),
                ("q21", "ಸಹಜವಾಗಿ ಆಯ್ತು"),
            ):
                nxt = state.intake.arm_next()
                assert nxt is not None and nxt.id == qid, f"expected {qid} armed, got {nxt}"
                assert state.intake.record_answer(answer) == qid

            await _run_turn(
                stt, llm, tts, log, turn_label="weight",
                caller_text="ಐವತ್ನಾಲ್ಕು ಕೆಜಿ",
                agent_reply="ಸರಿ. ಕಬ್ಬಿಣಾಂಶ ಗುಳಿಗೆ ತಿನ್ನುತ್ತಿದ್ದೀರಾ?",
            )
            # q01/q02/q06 are auto-satisfied by identify()/record_gestational_
            # age()/record_bp() above (IntakeProgress.sync_from_state) - arm_
            # next() is required before record_answer() can close anything
            # else (caught running this test: calling record_answer() with
            # nothing armed always returns None, silently).
            state.intake.sync_from_state(state)
            nxt = state.intake.arm_next()
            assert nxt is not None and nxt.id == "q07", f"expected weight (q07) armed next, got {nxt}"
            assert state.intake.record_answer("ಐವತ್ನಾಲ್ಕು ಕೆಜಿ") == "q07"

            await _run_turn(
                stt, llm, tts, log, turn_label="ifa_adherence",
                caller_text="ಹೌದು, ಪ್ರತಿದಿನ ತಿಂತಿದ್ದೀನಿ",
                agent_reply="ಒಳ್ಳೆಯದು. ತಲೆನೋವು ಅಥವಾ ಕಣ್ಣು ಮಂಜು ಇದೆಯ?",
            )
            nxt = state.intake.arm_next()
            assert nxt is not None and nxt.id == "q08", f"expected IFA (q08) armed next, got {nxt}"
            assert state.intake.record_answer("ಹೌದು, ಪ್ರತಿದಿನ ತಿಂತಿದ್ದೀನಿ") == "q08"

            await toolset.record_symptoms(_ctx(state))  # explicitly screened, all negative

            assert state.risk_level is RiskLevel.ROUTINE, (
                f"normal BP, no symptoms must stay ROUTINE; got {state.risk_level}"
            )
            assert state.assessment.requires_escalation is False
            # Clean progression through the questions actually exercised here:
            # name/age (q01), gestation (q02), BP (q06) and the symptom screen
            # (q09) auto-satisfy via sync_from_state(); weight (q07), IFA
            # (q08), and obstetric history/conception mode (q03-q05, q21)
            # answered by hand above. This scenario tests the vitals/
            # danger-sign path cleanly advancing, not a full 24-question call
            # - the remaining new CHECK/red-flag/history questions correctly
            # stay open.
            state.intake.sync_from_state(state)
            for qid in ("q01", "q02", "q03", "q04", "q05", "q06", "q07", "q08", "q09", "q21"):
                assert qid in state.intake.answered, f"{qid} should be answered by now"

    _run(go())


# ==========================================================================
# Scenario 3 (Hindi) - ASHA Sunita, swelling + dizziness, IFA stopped
# ==========================================================================

def test_scenario_3_hindi_swelling_ifa_drop_asha_sunita(toolset):
    """See module docstring correction #3: with no BP given and edema
    (a real tracked symptom) present, the correct outcome under this
    codebase's real, calibrated triage matrix is OBSERVATION, not MODERATE -
    RiskLevel.MODERATE is unreachable by any combination as of 2026-09-10
    (plain hypertensive-alone now also escalates to HIGH_RISK - see
    sakhi_triage.py's module docstring). IFA non-adherence is asserted as a
    recorded checklist fact, not a triage-affecting one - it genuinely is
    not one in sakhi_triage.py."""

    async def go():
        with _mock_pipeline("Scenario 3: Hindi swelling+IFA drop (ASHA Sunita)") as (stt, llm, tts, log):
            state = SakhiCallState(language=Language.HINDI)

            await _run_turn(
                stt, llm, tts, log, turn_label="name+age",
                caller_text="सुनीता के पास सुनिधि है, वो सत्ताईस साल की है",
                agent_reply="अच्छा दीदी, नोट कर लिया. अभी कितने महीने की गर्भवती है?",
            )
            state.identify(name="Sunidhi", age=27)

            await _run_turn(
                stt, llm, tts, log, turn_label="gestational_age",
                caller_text="अट्ठाईस हफ्ते",
                agent_reply="ठीक है. पैरों में सूजन है क्या?",
            )
            await toolset.record_gestational_age(_ctx(state), 28)

            symptoms_heard = detect_symptoms("पैरों में बहुत सूजन है और चक्कर भी आ रहे हैं")
            assert symptoms_heard.swelling, "edema must be detected from the caller's own words"
            await _run_turn(
                stt, llm, tts, log, turn_label="symptom_screen",
                caller_text="पैरों में बहुत सूजन है और चक्कर भी आ रहे हैं",
                agent_reply="एक क्षण, मैं इसे अभी दर्ज करती हूँ. आयरन की गोली ले रही हैं क्या?",
            )
            # dizziness folds into blurry_vision, same mapping detect_symptoms
            # already uses live - not a separate SymptomSet field.
            await toolset.record_symptoms(_ctx(state), swelling=True, blurry_vision=True)

            await _run_turn(
                stt, llm, tts, log, turn_label="ifa_adherence",
                caller_text="नहीं, गोली लेना बंद कर दिया",
                agent_reply="ठीक है, नोट कर लिया. डॉक्टर से इस बारे में बात करना ज़रूरी है.",
            )
            # IFA adherence is q08, reachable via arm_next() only once
            # q03-q07 and q21 (conception mode, added 2026-09-10) are
            # answered. This scenario is deliberately focused on the
            # danger-symptom path, not a full 24-question call (unlike
            # scenario 2, which does walk the whole sequence) - armed
            # directly here rather than padding with several unrelated
            # obstetric-history turns.
            state.intake.pending_id = "q08"
            ifa_answer = state.intake.record_answer("नहीं, गोली लेना बंद कर दिया")
            assert ifa_answer is not None, "a real, substantive answer must close the IFA question"

            assert state.risk_level is RiskLevel.OBSERVATION, (
                f"no BP recorded + symptomatic (edema) -> OBSERVATION under the real "
                f"matrix, not the brief's assumed MODERATE (unreachable with a real "
                f"danger symptom present - see module docstring correction #3); "
                f"got {state.risk_level}"
            )
            # The real, honest assertion this scenario CAN make: non-adherence
            # was recorded as a fact, even though it does not move risk_level.
            assert "गोली लेना बंद" in (state.intake.answered.get(ifa_answer) or "")

    _run(go())


# ==========================================================================
# Scenario 4 (Hindi) - ASHA Geeta, filler resilience + disconnect
# ==========================================================================

def test_scenario_4_hindi_filler_resilience_and_disconnect_asha_geeta(toolset):
    async def go():
        with _mock_pipeline("Scenario 4: Hindi filler resilience (ASHA Geeta)") as (stt, llm, tts, log):
            state = SakhiCallState(language=Language.HINDI)
            state.intake.pending_id = "q01"  # armed, as it would be at call start

            # "एक मिनट रुको" ("wait a minute") - a non-directed pause, caught
            # at the UPSTREAM layer (agent.is_backchannel), which raises
            # StopResponse() before the turn ever reaches intake at all -
            # tested at that real layer, not by calling record_answer()
            # directly and expecting it alone to guard against this phrase
            # (it doesn't, and isn't supposed to - two different real
            # defensive layers for two different kinds of filler).
            pause_text = "एक मिनट रुको"
            assert agent.is_backchannel(pause_text), (
                "a non-directed pause must be caught upstream, before intake sees it"
            )
            answered_before = dict(state.intake.answered)

            # "हेलो ठीक है जी" ("hello, it's fine ji") - real speech, not a
            # backchannel/pause, so it reaches record_answer() - THE layer
            # that must reject it. This is the gap found and fixed in
            # sakhi_intake.py while writing this test (see module docstring
            # correction #4): FOUND (Devanagari "ठीक है"/"हेलो" absent from
            # _NON_ANSWERS) -> FIXED (added) -> REPRODUCED CLEAN (asserted
            # here).
            filler_text = "हेलो ठीक है जी"
            assert not agent.is_backchannel(filler_text), (
                "sanity check: this phrase is real speech, not a pure backchannel - "
                "if this ever flips True the record_answer() assertion below "
                "would be vacuous"
            )
            closed = state.intake.record_answer(filler_text)
            assert closed is None, (
                f"filler {filler_text!r} incorrectly closed question {closed!r} - "
                "conversational fillers must never advance intake_answered"
            )
            assert state.intake.answered == answered_before, (
                "no empty/filler checkpoint was written for the pending question"
            )
            assert state.intake.pending_id == "q01", "the question must stay armed, unclosed"

            await _run_turn(
                stt, llm, tts, log, turn_label="filler_then_real_answer",
                caller_text="गीता, तीस साल",  # the REAL answer, right after the filler
                agent_reply="अच्छा दीदी, नोट कर लिया. अभी कितने हफ्ते की गर्भवती है?",
            )
            closed = state.intake.record_answer("गीता, तीस साल")
            assert closed == "q01", "the real answer, arriving right after the filler, must still land"
            state.identify(name="Geeta", age=30)

            # Background-noise turn: garbled STT, still real content-bearing
            # text (not empty) - must not raise, must not fabricate a value.
            # arm_next() required first (record_answer() only closes the
            # ARMED question, and q01 closing above cleared pending_id).
            nxt = state.intake.arm_next()
            assert nxt is not None and nxt.id == "q02"
            await _run_turn(
                stt, llm, tts, log, turn_label="noisy_gestational_age",
                caller_text="[noise] ... तीस हफ्ते [noise]",
                agent_reply="ठीक है. रक्तचाप कितना है?",
            )
            noisy_closed = state.intake.record_answer("[noise] ... तीस हफ्ते [noise]")
            assert noisy_closed is not None  # real digits survive the noise markers

            # --- Disconnect during the grace period ---
            #
            # A real call end (clean OR mid-call drop) always builds and
            # saves a post-call report from whatever state exists at that
            # moment - build_sakhi_call_report() (agent.py) is exactly that
            # code path, called here directly against a call cut short by a
            # disconnect rather than a clean farewell. The real historical
            # failure this guards against ("RuntimeError: AgentSession
            # isn't running", this session's own SmallestAI incident) came
            # from code that assumed the session was still alive during
            # teardown; build_sakhi_call_report() takes no session at all -
            # exactly why it is safe to call after a drop, and exactly what
            # this asserts by calling it here with no live session in
            # existence.
            report = agent.build_sakhi_call_report(
                state, duration_ms=4231.0, recording_status="none",
            )
            assert report["risk_level"] == state.risk_level.value
            assert report["intake_answered"] == len(state.intake.answered)
            assert report["escalated"] is False

    _run(go())


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v", "-s"]))
