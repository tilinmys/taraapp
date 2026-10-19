"""Medication guardrail: a dose already received is an answer, not a request."""

import pytest

from guardrails import SakhiGuardrails, medication_requested

G = SakhiGuardrails()


@pytest.mark.parametrize("text", [
    "ಎರಡು ಡೋಸ್ ಪಡೆದಿದ್ದೇನೆ",                       # Turn 14
    "ಎರಡು ಧನುರ್ವಾತ ಚುಚ್ಚುಮದ್ದು ಪಡೆದಿದ್ದೇನೆ",          # Turn 25
    "2 ಡೋಸ್ ಪಡೆದಿದ್ದಾರೆ",
    "ದಿನಕ್ಕೆ 1 ಮಾತ್ರೆ ತಗೊಂಡಿದ್ದಾರೆ",               # iron answer with a number
    "ಇಂಜೆಕ್ಷನ್ ಹಾಕಿಸಿಕೊಂಡಿದ್ದಾರೆ",
    "ಹೌದು, ಕಬ್ಬಿಣದ ಮಾತ್ರೆ ತಗೋತಿದ್ದಾರೆ",
    "दो इंजेक्शन लगवा लिया",
])
def test_factual_dose_answers_are_not_refused(text):
    assert medication_requested(text) is False
    decision = G.sanitize_input(text, language="kn")
    assert decision.category != "medical_advice"


@pytest.mark.parametrize("text", [
    "ಜ್ವರಕ್ಕೆ ಯಾವ ಮಾತ್ರೆ ಕೊಡಲಿ?",
    "ಎರಡು ಡೋಸ್ ಪಡೆದಿದ್ದೇನೆ, ಮೂರನೆಯದು ಕೊಡಬೇಕಾ?",    # question outranks disclosure
    "ಮಾತ್ರೆ ತೆಗೆದುಕೊಳ್ಳಬಹುದಾ?",
    "ಇಂಜೆಕ್ಷನ್ ಹಾಕಬೇಕಾ?",
    "ಈ ಔಷಧಿ ಸೂಕ್ತವೇ?",
    "कौन सी दवा दूँ?",
    "बुखार के लिए कितनी गोली देनी चाहिए? दवा बताइए",
    "misoprostol ತಗೊಂಡಿದ್ದಾರೆ",                    # named high-risk drug: always guarded
    "500 mg paracetamol",                         # English dose: unchanged
])
def test_real_medication_requests_stay_guarded(text):
    assert medication_requested(text) is True
