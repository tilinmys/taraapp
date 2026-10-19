"""Question lock (sakhi_question_lock): the model acknowledges, the code asks.

Each case below is a reply shape seen from the real LLM chain in
scripts/live_llm_scenarios.py, or the failure it would cause.
"""

import pytest

from sakhi_intake import INTAKE_QUESTIONS, QUESTIONS_BY_ID, question_text
from sakhi_question_lock import QuestionLock, similarity


def _q(qid, lang="kannada"):
    return question_text(QUESTIONS_BY_ID[qid], lang)


def _unanswered_after(qid, lang="kannada"):
    ids = [q.id for q in INTAKE_QUESTIONS]
    return tuple(question_text(q, lang) for q in INTAKE_QUESTIONS[ids.index(qid) + 1:])


def _run(lock, chunks, final=True):
    out = []
    for c in chunks:
        out += lock.feed(c)
    out += lock.finish(final=final)
    return "".join(out)


def test_invented_question_is_replaced_by_the_armed_one():
    """Live run: q08 (iron tablets) armed, the model asked about pain/fever."""
    lock = QuestionLock(_q("q08"), "FAST", _unanswered_after("q08"))
    spoken = _run(lock, ["ಸರಿ, ತೂಕ ದಾಖಲಾಗಿದೆ. ", "ತಾಯಿಗೆ ಹೊಟ್ಟೆ ನೋವು, ಜ್ವರ, ", "ಅಥವಾ ರಕ್ತಸ್ರಾವ ಇದೆಯಾ?"])
    assert "ಹೊಟ್ಟೆ ನೋವು" not in spoken
    assert spoken.startswith("ಸರಿ, ತೂಕ ದಾಖಲಾಗಿದೆ.")
    assert spoken.endswith(f"{_q('q08')} [VAD: FAST]")
    assert lock.dropped == ["ತಾಯಿಗೆ ಹೊಟ್ಟೆ ನೋವು, ಜ್ವರ, ಅಥವಾ ರಕ್ತಸ್ರಾವ ಇದೆಯಾ?"]


def test_model_asking_the_right_question_is_not_asked_twice():
    lock = QuestionLock(_q("q07"), "FAST", _unanswered_after("q07"))
    spoken = _run(lock, ["ಬಿಪಿ ದಾಖಲಾಗಿದೆ. ", _q("q07")])
    assert spoken.count("ತೂಕ") == 1


def test_imperative_paraphrase_of_a_checklist_question_is_dropped():
    lock = QuestionLock(_q("q07"), "FAST", _unanswered_after("q07"))
    spoken = _run(lock, ["ಸರಿ. ತಾಯಿಯ ತೂಕ ಎಷ್ಟು ಕೆಜಿ ಇದೆ ಹೇಳಿ."])
    assert spoken.count("ತೂಕ") == 1


def test_acknowledgement_reading_back_the_value_is_kept():
    """Hearing '110 70' read back is how she catches a mishearing."""
    lock = QuestionLock(_q("q07"), "FAST", _unanswered_after("q07"))
    spoken = _run(lock, ["ಬಿಪಿ 110 ಮತ್ತು 70 ಎಂದು ದಾಖಲಿಸಿದೆ."])
    assert spoken.startswith("ಬಿಪಿ 110 ಮತ್ತು 70 ಎಂದು ದಾಖಲಿಸಿದೆ.")


def test_unclear_answer_reasks_the_same_question_verbatim():
    lock = QuestionLock(_q("q02"), "FAST", _unanswered_after("q02"))
    spoken = _run(lock, ["ಕ್ಷಮಿಸಿ ಅಕ್ಕಾರ, ಸ್ಪಷ್ಟವಾಗಿ ಕೇಳಿಸಲಿಲ್ಲ. ", "ಈಗ ಎಷ್ಟು ತಿಂಗಳು?"])
    assert spoken == f"ಕ್ಷಮಿಸಿ ಅಕ್ಕಾರ, ಸ್ಪಷ್ಟವಾಗಿ ಕೇಳಿಸಲಿಲ್ಲ. {_q('q02')} [VAD: FAST]"


def test_tool_call_pass_does_not_append_the_question():
    """A pass that ends in a tool call is followed by another pass; asking in
    both would say the question twice."""
    lock = QuestionLock(_q("q06"), "FAST", _unanswered_after("q06"))
    assert _run(lock, ["ಸರಿ."], final=False) == "ಸರಿ. "


def test_long_narration_is_capped():
    lock = QuestionLock(_q("q09"), "FAST", _unanswered_after("q09"))
    spoken = _run(lock, ["ಸರಿ. ", "ಒಳ್ಳೆಯದು. ", "ಬಿಪಿ ಸಾಮಾನ್ಯವಾಗಿದೆ. ", "ಚಿಂತೆ ಬೇಡ. "])
    assert spoken == f"ಸರಿ. ಒಳ್ಳೆಯದು. {_q('q09')} [VAD: FAST]"


def test_vad_tag_from_the_model_is_stripped_and_the_questions_own_is_used():
    lock = QuestionLock(_q("q19"), "SLOW", _unanswered_after("q19"))
    spoken = _run(lock, ["ಸರಿ. [VAD: FAST]"])
    assert spoken == f"ಸರಿ. {_q('q19')} [VAD: SLOW]"


def test_streaming_emits_each_sentence_as_soon_as_it_ends():
    lock = QuestionLock(_q("q07"), "FAST", _unanswered_after("q07"))
    assert lock.feed("ಸರಿ, ಬಿಪಿ") == []
    assert lock.feed(" ದಾಖಲಾಗಿದೆ. ಮುಂ") == ["ಸರಿ, ಬಿಪಿ ದಾಖಲಾಗಿದೆ. "]


def test_decimal_point_is_not_a_sentence_end():
    lock = QuestionLock(_q("q26"), "SLOW", _unanswered_after("q26"))
    spoken = _run(lock, ["ಹಿಮೋಗ್ಲೋಬಿನ್ 10.5 ದಾಖಲಾಗಿದೆ."])
    assert spoken.startswith("ಹಿಮೋಗ್ಲೋಬಿನ್ 10.5 ದಾಖಲಾಗಿದೆ.")


def test_hindi_call_gets_the_hindi_question():
    lock = QuestionLock(_q("q07", "hindi"), "FAST", _unanswered_after("q07", "hindi"))
    spoken = _run(lock, ["ठीक है, बीपी दर्ज हो गया।"])
    assert spoken.endswith(f"{_q('q07', 'hindi')} [VAD: FAST]")
    assert "ठीक है, बीपी दर्ज हो गया।" in spoken


def test_unterminated_acknowledgement_gets_a_full_stop_before_the_question():
    lock = QuestionLock(_q("q02"), "FAST", _unanswered_after("q02"))
    assert _run(lock, ["ಮೊದಲನೆ ಗರ್ಭವತಿ"]) == f"ಮೊದಲನೆ ಗರ್ಭವತಿ. {_q('q02')} [VAD: FAST]"


def test_inactive_lock_passes_text_through_untouched():
    lock = QuestionLock(None)
    assert _run(lock, ["anything? ", "at all."]) == "anything? at all."


@pytest.mark.parametrize("qid", [q.id for q in INTAKE_QUESTIONS])
def test_every_question_is_recognised_as_itself(qid):
    """If a checklist question stopped matching its own text, the lock
    would let a model-asked copy through and the question would be said twice."""
    assert similarity(_q(qid), _q(qid)) == 1.0


def test_filter_cost_is_linear():
    import time
    lock = QuestionLock(_q("q09"), "FAST", _unanswered_after("q01"))
    chunk = "ಸರಿ ಅಕ್ಕಾರ " * 20 + ". "
    started = time.perf_counter()
    for _ in range(200):
        lock.feed(chunk)
    lock.finish(final=True)
    # 200 sentences, each checked against every open question: ~2.5 ms each
    # on a loaded laptop. The bound catches quadratic blow-up, not noise
    # (0.5 s flaked at 0.507 s under load).
    assert time.perf_counter() - started < 2.0
