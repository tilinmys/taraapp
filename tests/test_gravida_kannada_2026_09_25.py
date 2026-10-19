"""detect_pregnancy_number: Kannada script, STT variants, gravida vs parity."""

import pytest

from sakhi_domain import detect_pregnancy_number


@pytest.mark.parametrize("text,expected", [
    ("2 ಗರ್ಭಧಾರಣೆ", 2),                 # trailing \b after a vowel sign used to fail
    ("ಎರಡನೇ ಹೇರಿಗೆ", 2),
    ("ಇದು ಮೊದಲ ಹೆರಿಗೆ", 1),
    ("ಮೊದಲನೆ ಪ್ರಸವ", 1),
    ("ಒಂದನೇ ಗರ್ಭ", 1),
    ("ಮೂರನೇ ಗೃಹ್ಮಧಾರಣೆ", 3),             # STT spelling of ಗರ್ಭಧಾರಣೆ
    ("ನಾಲ್ಕನೇ ಗರ್ಭಾವಸ್ಥೆ", 4),
    ("ಮೊದಲು ಒಂದು ಹೆರಿಗೆ, ಈಗ ಎರಡನೇ ಗರ್ಭ", 2),  # "before" is not "first"
    ("eradaneya garbha dharane", 2),
    ("दूसरा बच्चा", 2),
    ("2 गर्भ", 2),
])
def test_gravida_extracted(text, expected):
    assert detect_pregnancy_number(text) == expected


@pytest.mark.parametrize("text", [
    "ಹೌದು, ಹೇರಿಗೆಯಾಗಿದೆ",     # yes, delivered before - no count given
    "ಮೊದಲೇ ಹೇರಿಗೆ ಆಗಿದೆ",     # ಮೊದಲೇ = already, not "first"
    "2 ಹೆರಿಗೆ ಆಗಿದೆ",          # two past deliveries = parity, gravida would be 3
    "2 ಮಗು",                  # two children = parity
    "2 बच्चा",
])
def test_no_invented_gravida(text):
    assert detect_pregnancy_number(text) is None
