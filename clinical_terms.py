"""Domain terminology: STT keyword boosting and post-ASR canonicalisation.

Two tiers, deliberately separate because they fail differently:

  Tier 1 - CLINICAL_KEYWORDS is passed to the STT vendor as a biasing hint.
    It only ever *raises the odds* of a correct transcription. It cannot be
    relied on: a vendor may ignore it, cap the list, or drop it on a
    failover to a provider with no such parameter (this is exactly what
    happens today when Deepgram is swapped out - see build_stt()).

  Tier 2 - canonicalise() runs on the transcript AFTER the vendor returns,
    inside this process, and therefore always runs. It is the tier that is
    allowed to be load-bearing.

The split matters: anything whose correctness a clinical decision depends on
belongs in Tier 2. Tier 1 is an optimisation, not a guarantee.

Everything here is O(1)-per-token or a single pre-compiled regex pass. This
runs on the hot path of every user turn, before the turn gate, so it must not
add measurable latency.
"""

from __future__ import annotations

import re
import unicodedata

# --------------------------------------------------------------------------
# Tier 1: STT keyword boosting
# --------------------------------------------------------------------------
#
# Weights follow the Smallest AI "pulse" convention: a float multiplier, where
# ~2.0 is a firm nudge and ~3.0 is a strong one. Reserved the highest weights
# for terms where a miss is clinically consequential rather than merely
# untidy - a mangled "preeclampsia" can lose an emergency; a mangled
# "anganwadi" only loses a place name.
#
# Kept deliberately short. A biasing list that contains everything biases
# toward nothing, and every vendor caps it somewhere.

CLINICAL_KEYWORDS: list[tuple[str, float]] = [
    # Hypertensive disorders of pregnancy - the highest-stakes misses.
    ("preeclampsia", 3.0),
    ("pre-eclampsia", 3.0),
    ("eclampsia", 3.0),
    ("proteinuria", 3.0),
    ("hypertension", 2.5),
    # Danger signs Sakhi screens for by protocol.
    ("convulsions", 3.0),
    ("seizure", 3.0),
    ("blurry vision", 2.5),
    ("severe headache", 2.5),
    ("vaginal bleeding", 3.0),
    ("reduced fetal movement", 3.0),
    ("edema", 2.0),
    ("oedema", 2.0),
    ("swelling", 2.0),
    # Routine ANC vocabulary.
    ("gestational diabetes", 2.5),
    ("anemia", 2.0),
    ("anaemia", 2.0),
    ("haemoglobin", 2.0),
    ("hemoglobin", 2.0),
    ("trimester", 2.0),
    ("tetanus", 2.0),
    ("ultrasound", 2.0),
    ("blood pressure", 2.5),
    ("systolic", 2.0),
    ("diastolic", 2.0),
    ("iron tablets", 2.0),
    ("folic acid", 2.0),
    # Regional / programme entities. Lower weight: a miss here is a cosmetic
    # transcription problem, not a clinical one.
    ("anganwadi", 1.8),
    ("ASHA", 1.8),
    ("Bidar", 1.8),
    ("Basavakalyan", 1.5),
    ("Humnabad", 1.5),
    ("Bhalki", 1.5),
    ("Aurad", 1.5),
    # 2026-09-04: Chitguppa and Kamalnagar are the two remaining real
    # taluk/town names in Bidar district this list had missed; "taluk
    # hospital"/"district hospital"/"PHC" are facility terms the system
    # prompt itself already names (sakhi_prompts.py: "Nearby facilities by
    # name: BRIMS Bidar, the taluk hospital, the Anganwadi") but that had no
    # STT boosting on either provider until now - kept in step with the
    # matching addition to agent.py's DEEPGRAM_BIDAR_KEYTERMS.
    ("Chitguppa", 1.5),
    ("Kamalnagar", 1.5),
    ("taluk hospital", 1.6),
    ("district hospital", 1.6),
    ("PHC", 1.6),
    ("BRIMS", 1.6),
]


# --------------------------------------------------------------------------
# Tier 2: post-ASR canonicalisation
# --------------------------------------------------------------------------

# Blood pressure. ASR renders a spoken reading half a dozen ways and none of
# them are the "120/80" the rest of the system parses. Digits are handled
# here; spoken number words go through _SPOKEN_NUMBERS first.
#
# Bounded to plausible obstetric ranges (systolic 60-260, diastolic 30-200)
# so an unrelated pair of numbers in the sentence ("she has 2 over 3 children")
# is not silently rewritten into a vital sign. A reading outside those bounds
# is left alone for record_bp()'s own BloodPressureError path to reject
# loudly, rather than being quietly normalised into something plausible.
_BP_SEPARATORS = r"(?:/|\s+(?:over|by|upon|bai|ಬೈ|ಮೇಲೆ|बाई|पर)\s+)"
_BP_RE = re.compile(
    rf"\b(?P<sys>\d{{2,3}})\s*{_BP_SEPARATORS}\s*(?P<dia>\d{{2,3}})\b"
    rf"(?P<tail>\s*(?:mm\s*hg|mmhg|millimet\w*)?)",
    re.IGNORECASE,
)

# Spoken number words, English + Kannada + Hindi, for readings dictated
# as words ("one twenty over eighty", "ippatentu varsha", etc.).
_SPOKEN_NUMBERS: dict[str, str] = {
    # English BP readings
    "one twenty": "120", "one thirty": "130", "one forty": "140",
    "one fifty": "150", "one sixty": "160", "one seventy": "170",
    "one eighty": "180", "one ninety": "190", "two hundred": "200",
    "ninety": "90", "eighty": "80", "seventy": "70", "sixty": "60",
    "hundred": "100", "one ten": "110", "one hundred": "100",
    # Demographic ages seen in English/code-mixed intake. Longest-first regex
    # construction below keeps these from being split into their components.
    "twenty five": "25", "twenty six": "26", "twenty seven": "27",
    "twenty eight": "28", "twenty nine": "29", "twenty four": "24",
}

# Kannada cardinal and colloquial numbers (cardinals 0-60 and common multiples)
# in both Kannada script and Latin/romanized transliteration.
KANNADA_NUMBER_WORDS: dict[str, str] = {
    # 0 - 9
    "ಸೊನ್ನೆ": "0", "sonne": "0", "zero": "0",
    "ಒಂದು": "1", "ondu": "1",
    "ಎರಡು": "2", "eradu": "2",
    "ಮೂರು": "3", "mooru": "3", "muru": "3",
    "ನಾಲ್ಕು": "4", "naalku": "4", "nalku": "4",
    "ಐದು": "5", "aidu": "5",
    "ಆರು": "6", "aaru": "6",
    "ಏಳು": "7", "yelu": "7", "elu": "7",
    "ಎಂಟು": "8", "entu": "8",
    "ಒಂಬತ್ತು": "9", "ombattu": "9",
    # 10 - 19
    "ಹತ್ತು": "10", "hattu": "10",
    "ಹನ್ನೊಂದು": "11", "hannondu": "11",
    "ಹನ್ನೆರಡು": "12", "hanneradu": "12",
    "ಹದಿಮೂರು": "13", "hadimooru": "13",
    "ಹದಿನಾಲ್ಕು": "14", "hadinaalku": "14",
    "ಹದಿನೈದು": "15", "hadinaidu": "15",
    "ಹದಿನಾರು": "16", "hadinaaru": "16",
    "ಹದಿನೇಳು": "17", "hadinelu": "17",
    "ಹದಿನೆಂಟು": "18", "hadinentu": "18",
    "ಹತ್ತೊಂಬತ್ತು": "19", "hattombattu": "19",
    # 20 - 29
    "ಇಪ್ಪತ್ತು": "20", "ippattu": "20",
    "ಇಪ್ಪತ್ತೊಂದು": "21", "ippattondu": "21",
    "ಇಪ್ಪತ್ತೆರಡು": "22", "ippatteradu": "22",
    "ಇಪ್ಪತ್ಮೂರು": "23", "ಇಪ್ಪತ್ತಮೂರು": "23", "ippammooru": "23", "ippattmooru": "23",
    "ಇಪ್ಪತ್ನಾಲ್ಕು": "24", "ಇಪ್ಪತ್ತನಾಲ್ಕು": "24", "ippattnaalku": "24", "ippattnalku": "24",
    "ಇಪ್ಪತ್ತೈದು": "25", "ippattaidu": "25",
    "ಇಪ್ಪತ್ತಾರು": "26", "ippattaaru": "26",
    "ಇಪ್ಪತ್ತೇಳು": "27", "ippattelu": "27",
    "ಇಪ್ಪತ್ತೆಂಟು": "28", "ಇಪ್ಪತೆಂಟು": "28", "ಇಪ್ಪತ್ತು ಎಂಟು": "28",
    "ippatentu": "28", "ippattentu": "28", "ippattu entu": "28",
    "ಇಪ್ಪತ್ತೊಂಬತ್ತು": "29", "ippattombattu": "29",
    # 30 - 39
    "ಮೂವತ್ತು": "30", "moovattu": "30",
    "ಮೂವತ್ತೊಂದು": "31", "moovattondu": "31",
    "ಮೂವತ್ತೆರಡು": "32", "moovatteradu": "32",
    "ಮೂವತ್ಮೂರು": "33", "moovattmooru": "33",
    "ಮೂವತ್ನಾಲ್ಕು": "34", "moovattnaalku": "34",
    "ಮೂವತ್ತೈದು": "35", "moovattaidu": "35",
    "ಮೂವತ್ತಾರು": "36", "moovattaaru": "36",
    "ಮೂವತ್ತೇಳು": "37", "moovattelu": "37",
    "ಮೂವತ್ತೆಂಟು": "38", "moovattentu": "38",
    "ಮೂವತ್ತೊಂಬತ್ತು": "39", "moovattombattu": "39",
    # 40 - 49
    "ನಲವತ್ತು": "40", "nalavattu": "40",
    "ನಲವತ್ತೊಂದು": "41", "nalavattondu": "41",
    "ನಲವತ್ತೆರಡು": "42", "nalavatteradu": "42",
    "ನಲವತ್ಮೂರು": "43", "nalavattmooru": "43",
    "ನಲವತ್ನಾಲ್ಕು": "44", "nalavattnaalku": "44",
    "ನಲವತ್ತೈದು": "45", "nalavattaidu": "45",
    # 50 - 60
    "ಐವತ್ತು": "50", "aivattu": "50",
    "ಅರವತ್ತು": "60", "aravattu": "60",
    "ಎಪ್ಪತ್ತು": "70", "eppattu": "70",
    "ಎಂಬತ್ತು": "80", "embattu": "80",
    "ತೊಂಬತ್ತು": "90", "tombattu": "90",
    "ನೂರು": "100", "nooru": "100",
}

# Kannada ordinals (e.g. "eradaneya" -> 2nd pregnancy)
KANNADA_ORDINAL_WORDS: dict[str, str] = {
    "ಮೊದಲನೆಯ": "1", "ಮೊದಲನೇ": "1", "modalaneya": "1", "modalane": "1", "modala": "1",
    "ಎರಡನೆಯ": "2", "ಎರಡನೇ": "2", "eradaneya": "2", "eradane": "2",
    "ಮೂರನೆಯ": "3", "ಮೂರನೇ": "3", "mooraneya": "3", "moorane": "3",
    "ನಾಲ್ಕನೆಯ": "4", "ನಾಲ್ಕನೇ": "4", "naalkaneya": "4", "naalkane": "4",
    "ಐದನೆಯ": "5", "ಐದನೇ": "5", "aidaneya": "5", "aidane": "5",
}

def _kannada_tens_compounds() -> dict[str, str]:
    """41-99 as single joined words ("ಐವತ್ತೆರಡು" 52, "ಎಂಬತ್ತೈದು" 85).

    Generated from each tens word rather than typed: weights (52 kg) and
    diastolic readings (85, 95) live in exactly this range, and the typed
    table above stops at the round tens from 50 up. A missing compound is
    left as words, which is harmless; the danger is a PARTIAL conversion,
    handled by _combine_digit_runs below.
    """
    tens = {"ನಲವತ್ತು": 40, "ಐವತ್ತು": 50, "ಅರವತ್ತು": 60, "ಎಪ್ಪತ್ತು": 70, "ಎಂಬತ್ತು": 80, "ತೊಂಬತ್ತು": 90}
    joined = {"ೊಂದು": 1, "ೆರಡು": 2, "ೈದು": 5, "ಾರು": 6, "ೇಳು": 7, "ೆಂಟು": 8, "ೊಂಬತ್ತು": 9}
    out: dict[str, str] = {}
    for word, value in tens.items():
        stem = word[:-1]              # drop the final vowel sign: ಎಪ್ಪತ್ತು -> ಎಪ್ಪತ್ತ
        short = word[:-2]             # ಎಪ್ಪತ್ತು -> ಎಪ್ಪತ್ (for ಮೂರು / ನಾಲ್ಕು)
        for suffix, unit in joined.items():
            out.setdefault(stem + suffix, str(value + unit))
        for base, unit in (("ಮೂರು", 3), ("ನಾಲ್ಕು", 4)):
            out.setdefault(short + base, str(value + unit))
            out.setdefault(stem + base, str(value + unit))
    return out


_ALL_KANNADA_NUMBERS = {
    **_kannada_tens_compounds(),
    **KANNADA_NUMBER_WORDS,
    # "ನೂರ" is how "hundred" is actually said before a remainder
    # ("ನೂರ ಇಪ್ಪತ್ತು" = 120); only "ನೂರು" was listed, so the remainder was
    # converted alone and "ನೂರ ಇಪ್ಪತ್ತು ಬೈ ಎಂಬತ್ತು" reached the model and
    # record_bp as "ನೂರ 20 ಬೈ 80".
    "ನೂರ": "100", "ನೂರಾ": "100", "noora": "100",
    "ಇನ್ನೂರು": "200", "innooru": "200",
}

# "hundred" followed by a remainder, recombined after word conversion:
#   "1 100 70" / "100 70" -> 170     (ಒಂದು ನೂರ ಎಪ್ಪತ್ತು / ನೂರ ಎಪ್ಪತ್ತು)
# Deliberately NOT generalised to "70 5" -> 75: in "…ಬೈ ತೊಂಬತ್ತು ಐದು ತಿಂಗಳು"
# (by 90, five months) that would silently turn two facts into one wrong one.
_HUNDREDS_RUN_RE = re.compile(r"(?<!\d)(?:1\s+)?100\s+(\d{1,2})(?!\d)")


def _combine_digit_runs(text: str) -> str:
    return _HUNDREDS_RUN_RE.sub(lambda m: str(100 + int(m.group(1))), text)

_SPOKEN_NUMBER_RE = re.compile(
    r"\b(" + "|".join(sorted(_SPOKEN_NUMBERS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)

_KANNADA_NUMBERS_RE = re.compile(
    r"(?<![A-Za-z0-9\u0C80-\u0CFF])("
    + "|".join(re.escape(k) for k in sorted(_ALL_KANNADA_NUMBERS, key=len, reverse=True))
    + r")(?![A-Za-z0-9\u0C80-\u0CFF])",
    re.IGNORECASE | re.UNICODE,
)


# Phonetic corruptions -> canonical clinical term.
#
# Every entry here came from a real mangled transcript, not from imagining how
# a word might be misheard. The value is what the rest of the system expects
# to see; the keys are what the vendor actually returned.
#
# Matched on word boundaries against the casefolded transcript, so an entry
# can never fire inside a longer word.
CLINICAL_CANONICAL_MAP: dict[str, str] = {
    # preeclampsia - the single most consequential term to get right.
    "pre eclampsia": "preeclampsia",
    "pre-eclampsia": "preeclampsia",
    "pre a clampsia": "preeclampsia",
    "pre clampsia": "preeclampsia",
    "preclampsia": "preeclampsia",
    "pre lamp sia": "preeclampsia",
    "free eclampsia": "preeclampsia",
    "brie clampsia": "preeclampsia",
    # proteinuria
    "protein urea": "proteinuria",
    "protein uria": "proteinuria",
    "proteen uria": "proteinuria",
    "protein in urine": "proteinuria",
    # gestational diabetes
    "gestational diabetics": "gestational diabetes",
    "gestation diabetes": "gestational diabetes",
    "just national diabetes": "gestational diabetes",
    "gestational dia betes": "gestational diabetes",
    # oedema / swelling
    "adema": "edema",
    "edima": "edema",
    "a dema": "edema",
    "identify ma": "edema",
    # anaemia / haemoglobin
    "anemiya": "anemia",
    "aniemia": "anemia",
    "hemo globin": "hemoglobin",
    "haemo globin": "hemoglobin",
    "hb count": "hemoglobin",
    # eclampsia / convulsions
    "a clampsia": "eclampsia",
    "convulsion": "convulsions",
    "convolutions": "convulsions",
    "conversions": "convulsions",
    # programme + regional entities. "Anganwadi" is mangled constantly and
    # was, in a real call, transcribed as a patient's NAME - which is how a
    # mother ended up registered as "Anganwadi, 45".
    "angan wadi": "anganwadi",
    "angan vadi": "anganwadi",
    "aangan wadi": "anganwadi",
    "anganwari": "anganwadi",
    "angarwadi": "anganwadi",
    "asha worker": "ASHA worker",
    "aasha worker": "ASHA worker",
    "beedar": "Bidar",
    "bidder": "Bidar",
    "basava kalyan": "Basavakalyan",
    "human abad": "Humnabad",
    "humna bad": "Humnabad",
    "bhalki": "Bhalki",
    "balki": "Bhalki",
    # Tetanus / immunisation
    "t d injection": "Td injection",
    "tetnus": "tetanus",
    "tetanous": "tetanus",
}

_CANONICAL_RE = re.compile(
    r"\b(" + "|".join(
        re.escape(k) for k in sorted(CLINICAL_CANONICAL_MAP, key=len, reverse=True)
    ) + r")\b",
    re.IGNORECASE,
)


def normalise_blood_pressure(text: str) -> str:
    """Rewrite any spoken BP reading into `<sys>/<dia> mmHg`.

    "120 over 80", "120 by 80", "one forty over ninety", "150/100" all become
    the one form record_bp()'s caller can parse. Readings outside plausible
    obstetric bounds are deliberately left untouched - see _BP_RE's comment.
    """
    if not text:
        return text

    # Spoken words -> digits first, so "one forty over ninety" reaches _BP_RE
    # as "140 over 90".
    def _digitise(match: re.Match) -> str:
        return _SPOKEN_NUMBERS[match.group(1).casefold()]

    working = _SPOKEN_NUMBER_RE.sub(_digitise, text)

    def _rewrite(match: re.Match) -> str:
        systolic, diastolic = int(match.group("sys")), int(match.group("dia"))
        if not (60 <= systolic <= 260 and 30 <= diastolic <= 200):
            return match.group(0)          # not a vital sign; leave it alone
        if diastolic >= systolic:
            return match.group(0)          # physiologically impossible pairing
        return f"{systolic}/{diastolic} mmHg"

    return _BP_RE.sub(_rewrite, working)


def canonicalise_terms(text: str) -> str:
    """Map known phonetic corruptions onto their canonical clinical forms."""
    if not text:
        return text

    def _replace(match: re.Match) -> str:
        return CLINICAL_CANONICAL_MAP[match.group(1).casefold()]

    return _CANONICAL_RE.sub(_replace, text)


def clean_currency_symbols(text: str) -> str:
    """Strip stray currency formatting (₹, Rs., etc.) produced by code-mix ASR models.

    In a maternal healthcare questionnaire, numbers represent age, gestational weeks,
    BP readings, or pregnancy counts - never rupee amounts. Stray currency symbols
    corrupt downstream extraction.
    """
    if not text:
        return text
    cleaned = re.sub(r"[₹\u20B9]|(?:\b(?:rs|inr)\b\.?)", "", text, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def normalise_kannada_numbers(text: str) -> str:
    """Normalize spoken Kannada number words (script and romanized) to Arabic digits."""
    if not text:
        return text

    def _rep(match: re.Match) -> str:
        word = match.group(0).casefold()
        return _ALL_KANNADA_NUMBERS.get(word, match.group(0))

    return _combine_digit_runs(_KANNADA_NUMBERS_RE.sub(_rep, text))


def canonicalise(text: str) -> str:
    """The single entry point: run every Tier-2 pass over one transcript.

    Called on the raw transcript before the turn gate sees it, so every
    downstream consumer - the backchannel/assent gate, deterministic symptom
    detection, the intake tracker, and the LLM - all see the same canonical
    text. Running it in only some of those places is how two of them end up
    disagreeing about what the worker said.

    Unicode is NFC-normalised first: Kannada and Devanagari both have
    multiple byte sequences for the same visible string, and a dictionary
    lookup against the wrong one silently misses.
    """
    if not text or not text.strip():
        return text
    working = unicodedata.normalize("NFC", text)
    working = clean_currency_symbols(working)
    working = canonicalise_terms(working)
    working = normalise_kannada_numbers(working)
    working = normalise_blood_pressure(working)
    return working
