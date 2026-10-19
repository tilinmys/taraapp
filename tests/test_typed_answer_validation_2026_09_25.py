"""Typed answer validation (2026-09-25, Turn 6 out-of-domain answer)."""

import pytest

from agent import SakhiAgent
from sakhi_domain import SakhiCallState
from sakhi_intake import QUESTIONS_BY_ID, IntakeProgress


def _valid(qid, text):
    q = QUESTIONS_BY_ID[qid]
    return IntakeProgress().validate_answer(text, vad=q.vad, question_id=qid)[0]


@pytest.mark.parametrize("qid", ["q05", "q09", "q13", "q19", "q20", "q26"])
def test_turn_6_out_of_domain_text_does_not_close_a_question(qid):
    assert _valid(qid, "I am the network protocols right now") is False


@pytest.mark.parametrize("qid,text", [
    ("q06", "ಹೌದು ಅಕ್ಕ"),        # BP question needs a reading
    ("q07", "ಹೌದು ಅಕ್ಕ"),
    ("q11", "ಅಕ್ಕ ಅಕ್ಕ"),        # address words only
    ("q18", "ನಮಸ್ಕಾರ ಅಕ್ಕ"),     # a greeting is not an answer
])
def test_content_free_or_wrong_type_is_rejected(qid, text):
    assert _valid(qid, text) is False


@pytest.mark.parametrize("qid,text", [
    ("q09", "ಇಲ್ಲ ಅಕ್ಕ"),                    # the commonest real answer
    ("q08", "ಹೌದು, ತಪ್ಪದೆ ತಿನ್ನುತ್ತಿದ್ದಾಳೆ"),
    ("q14", "ಎರಡು ಡೋಸ್ ಪಡೆದಿದ್ದೇನೆ"),
    ("q13", "ತಾಲೂಕು ಆಸ್ಪತ್ರೆಯಲ್ಲಿ"),
    ("q21", "ಸಹಜವಾಗಿ ಆಯ್ತು"),
    ("q21", "ನೈಸರ್ಗಿಕ"),
    ("q10", "ಸ್ವಲ್ಪ ಹೊಟ್ಟೆ ನೋವು"),
    ("q25", "140"),
    ("q06", "120 80"),
    ("q06", "ಬಿಪಿ ಮಾಡಿಲ್ಲ"),                  # not measured is a complete answer
    ("q07", "ಐವತ್ನಾಲ್ಕು ಕೆಜಿ"),
    ("q26", "ರಿಪೋರ್ಟ್ ಇನ್ನೂ ಬಂದಿಲ್ಲ"),
    ("q03", "ಕಳೆದ ತಿಂಗಳು"),
    ("q03", "she has had a headache since Monday"),
    ("q03", "Aayitu akka, hinde herige aagide"),   # romanised Kannada
    ("q19", "ಇಲ್ಲ ಅಕ್ಕ"),
    ("q16", "yes"),
])
def test_real_answers_still_close(qid, text):
    assert _valid(qid, text) is True


def test_irrelevant_input_is_reasked_at_most_stall_limit_times():
    p = IntakeProgress()
    p.pending_id = "q05"
    results = [p.record_answer("I am the network protocols right now")
               for _ in range(IntakeProgress.STALL_LIMIT + 1)]
    assert results[:-1] == [None] * IntakeProgress.STALL_LIMIT
    assert results[-1] == "q05"
    assert p.answered["q05"].endswith("(unclear, best-effort)")


def test_pending_answer_matches_dispatch():
    state = SakhiCallState()
    assert SakhiAgent._pending_answer_matches(state, None, "x", {}) is False
    assert SakhiAgent._pending_answer_matches(state, "q99", "x", {}) is False
    assert SakhiAgent._pending_answer_matches(state, "q04", "ಹೌದು", {}) is False
    # schema-validated questions are handed to record_answer()
    assert SakhiAgent._pending_answer_matches(state, "q09", "ಇಲ್ಲ", {}) is True
