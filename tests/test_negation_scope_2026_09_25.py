"""detect_symptoms negation scope (2026-09-25, Turn 22 false eclampsia)."""

import pytest

from sakhi_domain import detect_symptoms

TURN_22 = ("ಇಲ್ಲಕ್ಕ, ಇವು ಯಾವುದು ಇಲ್ಲ. ಮೊದಲಿನಿಂದಲೂ ಡಯಾಬಿಟ್, ಬಿಪಿ, ಥೈರಾಯ್ಡ್, "
           "ಫಿಟ್ಸ್ ಯಾವುದು ಡಯಾಗ್ನೋಸ್ ಆಗಿಲ್ಲ.")


def test_turn_22_never_diagnosed_is_not_convulsions():
    assert detect_symptoms(TURN_22).convulsions is False


@pytest.mark.parametrize("text", [
    "ಫಿಟ್ಸ್ ಬಂದಿಲ್ಲ",
    "ಫಿಟ್ಸ್ ಏನೂ ಕಾಣಿಸಿಲ್ಲ",
    "ಫಿಟ್ಸ್ ಇರಲಿಲ್ಲ",
    "no fits",
    "दौरे नहीं आए",
])
def test_denied_fits(text):
    assert detect_symptoms(text).convulsions is False


@pytest.mark.parametrize("text", [
    # A denial of ANOTHER symptom must never erase a real danger sign.
    "ತಲೆನೋವು ಇಲ್ಲ, ಆದರೆ ಫಿಟ್ಸ್ ಬಂತು",
    "ತಲೆನೋವು ಇಲ್ಲ. ಫಿಟ್ಸ್ ಬಂತು",
    "ಫಿಟ್ಸ್ ಬಂತು, ಆಕೆಗೆ ಹೊಟ್ಟೆ ನೋವು ಏನೂ ಇಲ್ಲ",
    "no headache but she had fits",
    "fits",
])
def test_real_fits_still_detected(text):
    assert detect_symptoms(text).convulsions is True


def test_hindi_headache_affirmed_fever_denied():
    s = detect_symptoms("सिरदर्द है लेकिन बुखार नहीं है")
    assert s.headache is True
    assert s.fever is False


def test_history_question_records_no_acute_sign_without_current_context():
    assert detect_symptoms("ಫಿಟ್ಸ್ ಇದೆ, ಮಾತ್ರೆ ತಗೋತಾರೆ", question_id="q19").convulsions is False
    assert detect_symptoms("ಫಿಟ್ಸ್ ಇದೆ, ಮಾತ್ರೆ ತಗೋತಾರೆ").convulsions is True  # screening question


def test_history_question_still_catches_current_event():
    assert detect_symptoms("ಫಿಟ್ಸ್ ಇದೆ, ಇವತ್ತು ಬೆಳಿಗ್ಗೆ ಫಿಟ್ಸ್ ಬಂತು", question_id="q19").convulsions is True
