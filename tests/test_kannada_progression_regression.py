"""Regression tests for the Kannada voice-agent questionnaire progression bug.

INCIDENT (2026-09-22)
---------------------
During a Kannada call with an ASHA worker, Sakhi repeatedly asked the
gestational-month question ("Idu yav tingal alli ide akka?") even after the
worker answered "Yelu tingalu, akka" (= 7 months) multiple times, then jumped
backward to ask for name and age again.

ROOT CAUSE (confirmed by code inspection + test script):
  detect_gestational_age("yelu tingalu") returned None because:
  1. _MONTH_WORD_TO_NUM lacked romanized Kannada number words
     ("yelu" not present; only Kannada-script "ಏಳು" was there)
  2. _MONTH_UNIT_WORDS lacked romanized Kannada unit words
     ("tingalu" not present; only Kannada-script "ತಿಂಗಳು" was there)

  Sarvam STT transcribes colloquial North Karnataka Kannada in Latin/romanized
  form, not Kannada script. With detect_gestational_age returning None,
  state.gestational_weeks was never set, sync_from_state() never auto-satisfied
  q02, and the prompt kept injecting q02 as NEXT QUESTION causing indefinite
  re-asking.

FIX (sakhi_domain.py 2026-09-22):
  Added romanized cardinals/ordinals to _MONTH_WORD_TO_NUM and
  "tingalu"/"tingal"/"tingala" to _MONTH_UNIT_WORDS.

These tests MUST pass before any deployment.
"""

import pytest

from sakhi_domain import SakhiCallState, detect_gestational_age
from sakhi_intake import IntakeProgress, QUESTIONS_BY_ID


# ---------------------------------------------------------------------------
# BUG 1 tests: detect_gestational_age romanized Kannada number words
# ---------------------------------------------------------------------------

class TestDetectGestationalAgeRomanized:
    """All romanized Kannada variants from the bug report must parse correctly."""

    def test_yelu_tingalu_lowercase(self):
        """Exact Sarvam STT output that caused the stall."""
        assert detect_gestational_age("yelu tingalu") == 28, (
            "yelu=7, tingalu=months → 7*4=28 weeks"
        )

    def test_yelu_tingalu_with_akka_suffix(self):
        """ASHA workers append 'akka' (elder sister) as a politeness marker."""
        assert detect_gestational_age("yelu tingalu, akka") == 28

    def test_yelu_tingalu_capitalized(self):
        """Sarvam may capitalize the first word of a sentence."""
        assert detect_gestational_age("Yelu tingalu, akka") == 28

    def test_digit_7_tingalu(self):
        """Digit + romanized unit word — another common STT output form."""
        assert detect_gestational_age("7 tingalu") == 28

    def test_digit_5_tingalu(self):
        assert detect_gestational_age("5 tingalu") == 20

    def test_eradu_tingalu(self):
        """'eradu' = 2 in romanized Kannada."""
        assert detect_gestational_age("eradu tingalu") == 8

    def test_aidu_tingalu(self):
        """'aidu' = 5."""
        assert detect_gestational_age("aidu tingalu") == 20

    def test_aaru_tingalu(self):
        """'aaru' = 6."""
        assert detect_gestational_age("aaru tingalu") == 24

    def test_entu_tingalu(self):
        """'entu' = 8."""
        assert detect_gestational_age("entu tingalu") == 32

    def test_tingal_variant_unit(self):
        """'tingal' (no trailing 'u') is also a valid romanization."""
        assert detect_gestational_age("yelu tingal") == 28

    def test_idu_yelu_tingalu_with_prefix(self):
        """With Kannada prefix 'idu' (it is / this is)."""
        assert detect_gestational_age("idu yelu tingalu") == 28

    # --- Kannada script forms must still work (regression guard) ----------

    def test_script_elu_tingalu(self):
        """Original script form must still work after the fix."""
        assert detect_gestational_age("ಏಳು ತಿಂಗಳು") == 28

    def test_script_aidu_tingalu(self):
        assert detect_gestational_age("ಐದು ತಿಂಗಳು") == 20

    def test_script_aaru_tingalu(self):
        assert detect_gestational_age("ಆರು ತಿಂಗಳು") == 24

    # --- English and Hindi must still work (regression guard) -----------

    def test_english_seven_months(self):
        assert detect_gestational_age("seven months") == 28

    def test_english_5_months(self):
        assert detect_gestational_age("5 months") == 20

    def test_hindi_saat_mahine(self):
        assert detect_gestational_age("सात महीने") == 28


# ---------------------------------------------------------------------------
# BUG 2 tests: eradaneya must NOT cause gestational-age false detection
# ---------------------------------------------------------------------------

class TestEradaneyaNotGestational:
    """'eradaneya garbha dharane' means '2nd pregnancy', not '2 months'.
    
    'eradaneya' is now in _MONTH_WORD_TO_NUM (as ordinal 2), but the sentence
    does NOT contain a month-unit word, so detect_gestational_age must still
    return None.
    """

    def test_eradaneya_garbha_dharane_returns_none(self):
        """Second-pregnancy statement must NOT parse as gestational weeks."""
        result = detect_gestational_age("Eradaneya garbha dharane")
        assert result is None, (
            f"Got {result!r}; 'eradaneya garbha dharane' is pregnancy count, not months"
        )

    def test_idu_eradaneya_garbha_dharane_returns_none(self):
        assert detect_gestational_age("idu eradaneya garbha dharane") is None


# ---------------------------------------------------------------------------
# Full conversation progression test
# ---------------------------------------------------------------------------

class TestPriyaSharmaConversation:
    """End-to-end reproduction of the exact failing call from the incident.

    Exact utterances from the 2026-09-22 Kannada ASHA call:
      Turn 1: "Priya Sharma, vayasu ippatentu varsha"  → q01 (name + age)
      Turn 2: "Eradaneya garbha dharane"               → q02 (gestational, via stall route)
      Turn 3: "Aayitu akka, hinde herige aagide"       → q03 (LMP / prev delivery)
      Turn 4: "Yelu tingalu, akka"                     → q04 (pregnancy count, 7 months)

    EXPECTED AFTER TURN 4:
      - q01, q02, q03, q04 all closed
      - state.gestational_weeks == 28  (from "yelu tingalu")
      - q02 marked satisfied via sync_from_state
      - next_question() is NOT q01, q02, q03, q04
      - pending_id is None (turn finished cleanly)
    """

    def _simulate_turn(self, p: IntakeProgress, state: SakhiCallState,
                       utterance: str, expected_close: str | None = None,
                       expected_gest: int | None = None) -> str | None:
        """Simulate one agent turn: arm → user speaks → record_answer → sync."""
        armed = p.arm_next()
        closed = p.record_answer(utterance)

        # Deterministic gestational capture (mirrors agent.py _capture_gestational_age)
        if not state.gestational_weeks:
            weeks = detect_gestational_age(utterance)
            if weeks is not None:
                state.identify(gestational_weeks=weeks)

        # sync_from_state auto-satisfies questions the state already answers
        p.sync_from_state(state)

        if expected_close is not None:
            assert closed == expected_close, (
                f"Expected to close {expected_close!r} but got {closed!r} "
                f"for utterance {utterance!r}"
            )
        if expected_gest is not None:
            assert state.gestational_weeks == expected_gest, (
                f"Expected gestational_weeks={expected_gest} but got "
                f"{state.gestational_weeks!r} after {utterance!r}"
            )
        return closed

    def test_q01_name_age_closes(self):
        """Name and age are captured and q01 is closed."""
        state = SakhiCallState()
        p = state.intake
        self._simulate_turn(p, state, "Priya Sharma, vayasu ippatentu varsha", "q01")
        assert "q01" in p.answered

    def test_q02_gestational_via_stall_and_then_sync(self):
        """Even when 'Eradaneya garbha dharane' doesn't carry gestational months,
        q02 closes after the turn (the record_answer substantive check passes).
        Then on Turn 4 gestational weeks are extracted from 'Yelu tingalu', and
        sync_from_state marks q02 satisfied.
        """
        state = SakhiCallState()
        p = state.intake

        # Turn 1
        state.identify(name="Priya Sharma", age=28)
        p.pending_id = "q01"
        p.record_answer("Priya Sharma, vayasu ippatentu varsha")
        p.sync_from_state(state)

        # Turn 2 — "Eradaneya garbha dharane" = 2nd pregnancy, NOT gestational months
        self._simulate_turn(p, state, "Eradaneya garbha dharane")
        # q02 closes (substantive answer recorded) but gestational_weeks still None
        assert "q02" in p.answered
        assert state.gestational_weeks is None

    def test_yelu_tingalu_sets_gestational_weeks_and_marks_q02(self):
        """KEY REGRESSION TEST.

        After 'Yelu tingalu, akka':
        1. detect_gestational_age returns 28
        2. state.gestational_weeks = 28
        3. sync_from_state marks q02 satisfied
        4. q02 is in p.answered
        5. next_question() is NOT q02
        """
        state = SakhiCallState()
        p = state.intake

        # Pre-populate turns 1-3 so we reach the state just before Turn 4
        state.identify(name="Priya Sharma", age=28)
        p.pending_id = "q01"
        p.record_answer("Priya Sharma, vayasu ippatentu varsha")
        p.sync_from_state(state)

        p.arm_next()
        p.record_answer("Eradaneya garbha dharane")
        p.sync_from_state(state)

        p.arm_next()
        p.record_answer("Aayitu akka, hinde herige aagide")
        p.sync_from_state(state)

        # Turn 4 — the critical utterance
        p.arm_next()
        p.record_answer("Yelu tingalu, akka")
        weeks = detect_gestational_age("Yelu tingalu, akka")

        assert weeks == 28, f"detect_gestational_age must return 28 for 'Yelu tingalu', got {weeks}"
        state.identify(gestational_weeks=weeks)
        p.sync_from_state(state)

        assert state.gestational_weeks == 28, "gestational_weeks must be 28 after turn 4"
        assert "q02" in p.answered, "q02 must be satisfied after gestational_weeks set"

        nxt = p.next_question()
        assert nxt is not None, "Intake should not be complete at this point"
        assert nxt.id not in ("q01", "q02", "q03", "q04"), (
            f"next_question must NOT be a question already answered; got {nxt.id!r}"
        )

    def test_q02_not_repeated_after_yelu_tingalu(self):
        """q02 must never appear in next_question() after gestational weeks are set."""
        state = SakhiCallState()
        p = state.intake

        state.identify(name="Priya Sharma", age=28, gestational_weeks=28)
        p.sync_from_state(state)

        questions_asked = []
        for _ in range(30):  # walk all remaining questions
            nxt = p.next_question()
            if nxt is None:
                break
            assert nxt.id != "q02", f"q02 must not surface again; surfaced at index {len(questions_asked)}"
            p.arm_next()
            p.record_answer("ಹೌದು, ಅಕ್ಕ")  # minimal substantive answer
            questions_asked.append(nxt.id)

    def test_name_age_not_reasked_after_sync(self):
        """q01 must not re-surface after name and age are set."""
        state = SakhiCallState()
        p = state.intake

        state.identify(name="Priya Sharma", age=28)
        p.sync_from_state(state)

        assert "q01" in p.answered, "q01 must be auto-satisfied when name+age are in state"
        nxt = p.next_question()
        assert nxt is None or nxt.id != "q01", (
            f"q01 must not be next question after name+age synced; got {nxt}"
        )

    def test_full_priya_sharma_conversation_no_regressions(self):
        """Reproduce the entire incident call. All 5 fields must be correct."""
        state = SakhiCallState()
        p = state.intake

        # -------- Turn 1: name + age ----------------------------------------
        state.identify(name="Priya Sharma", age=28)
        p.pending_id = "q01"
        closed1 = p.record_answer("Priya Sharma, vayasu ippatentu varsha")
        p.sync_from_state(state)

        assert closed1 == "q01", f"Turn 1 must close q01; got {closed1}"
        assert "q01" in p.answered

        # -------- Turn 2: 2nd pregnancy (q02 armed) -------------------------
        armed2 = p.arm_next()
        assert armed2 is not None and armed2.id == "q02", (
            f"Turn 2 must arm q02; armed {armed2 and armed2.id!r}"
        )
        closed2 = p.record_answer("Eradaneya garbha dharane")
        detect_gestational_age("Eradaneya garbha dharane")  # → None, no month set
        p.sync_from_state(state)
        assert closed2 == "q02"

        # -------- Turn 3: prev delivery (q03 armed) -------------------------
        armed3 = p.arm_next()
        assert armed3 is not None and armed3.id == "q03"
        closed3 = p.record_answer("Aayitu akka, hinde herige aagide")
        p.sync_from_state(state)
        assert closed3 == "q03"

        # -------- Turn 4: 7 months (q04 armed) — THE CRITICAL TURN ---------
        armed4 = p.arm_next()
        assert armed4 is not None and armed4.id == "q04"
        closed4 = p.record_answer("Yelu tingalu, akka")

        weeks4 = detect_gestational_age("Yelu tingalu, akka")
        assert weeks4 == 28, (
            f"REGRESSION: detect_gestational_age('Yelu tingalu, akka') = {weeks4!r}, "
            f"must be 28.  This is the root-cause bug."
        )
        state.identify(gestational_weeks=weeks4)
        p.sync_from_state(state)

        assert closed4 == "q04", f"q04 must close on 'Yelu tingalu'; got {closed4}"
        assert state.gestational_weeks == 28
        assert "q02" in p.answered  # auto-satisfied by sync after gestational set
        assert "q04" in p.answered

        # -------- Verify completed set is correct ---------------------------
        for qid in ("q01", "q02", "q03", "q04"):
            assert qid in p.answered, f"{qid} must be in answered dict; got {sorted(p.answered.keys())}"

        # -------- Verify no backward motion ---------------------------------
        nxt = p.next_question()
        assert nxt is not None, "Call should not be complete yet (many questions remain)"
        assert nxt.id not in ("q01", "q02", "q03", "q04"), (
            f"next_question must advance PAST answered questions; got {nxt.id!r}"
        )
        assert p.pending_id is None, (
            "pending_id must be cleared after record_answer; "
            f"stuck at {p.pending_id!r}"
        )


# ---------------------------------------------------------------------------
# Variant utterance tests (all from the bug report spec)
# ---------------------------------------------------------------------------

class TestKannadaVariantUtterances:
    """Every variant listed in the bug report must normalize correctly."""

    @pytest.mark.parametrize("text,expected_weeks", [
        ("yelu tingalu", 28),
        ("ಏಳು ತಿಂಗಳು", 28),
        ("7 tingalu", 28),
        ("Yelu tingalu, akka", 28),
        ("idu yelu tingalu", 28),
        ("yelu tingal", 28),
        ("aidu tingalu", 20),
        ("aaru tingalu", 24),
        ("eradu tingalu", 8),
    ])
    def test_gestational_age_detection(self, text, expected_weeks):
        assert detect_gestational_age(text) == expected_weeks, (
            f"detect_gestational_age({text!r}) must return {expected_weeks}"
        )

    @pytest.mark.parametrize("text", [
        "Eradaneya garbha dharane",
        "idu eradaneya garbha dharane",
        "ಎರಡನೇ ಗರ್ಭಧಾರಣೆ",
    ])
    def test_pregnancy_count_not_gestational(self, text):
        """Pregnancy-count phrases must NOT be mistaken for gestational months."""
        assert detect_gestational_age(text) is None, (
            f"detect_gestational_age({text!r}) must return None "
            f"(it's a pregnancy count, not months pregnant)"
        )


# ---------------------------------------------------------------------------
# Stall protection: stall-escape must advance, never reset to q01
# ---------------------------------------------------------------------------

class TestStallEscapeDoesNotResetToQ01:
    """STALL_LIMIT=2 means after 3 non-substantive turns the question is force-closed.
    The NEXT question must be q02 (or later), NEVER q01.
    """

    def test_stall_on_q02_advances_to_q03_not_q01(self):
        state = SakhiCallState()
        p = state.intake

        # q01 already answered
        state.identify(name="Test Mother", age=25)
        p.sync_from_state(state)
        assert "q01" in p.answered

        # arm q02
        armed = p.arm_next()
        assert armed is not None and armed.id == "q02"

        # Three non-substantive turns on q02 → stall-escape fires.
        # Empty strings hit the early-return guard (`if not text: return None`)
        # so they never increment the stall counter. Use short noise utterances
        # that reach _is_substantive() but fail it, triggering stall counting.
        # record_answer() STALL_LIMIT=2: the THIRD non-substantive call closes.
        p.record_answer("hmm")   # stall 1 (non-substantive, FAST question but 'hmm' in _NON_ANSWERS)
        p.record_answer("uh")    # stall 2
        result = p.record_answer("huh")  # stall 3 > STALL_LIMIT → force-close
        # After stall-escape: q02 closed, next must be q03 not q01
        assert "q02" in p.answered, (
            f"Stall-escape must close q02 after 3 stalls; answered={p.answered}"
        )
        nxt = p.next_question()
        assert nxt is not None
        assert nxt.id not in ("q01", "q02"), (
            f"After q02 stall-escape, next question must advance; got {nxt.id!r}"
        )
