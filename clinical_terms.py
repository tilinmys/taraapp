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

# Spoken number words, English + the two call languages, for readings dictated
# as words ("one twenty over eighty"). Only the forms that actually turn up in
# a BP reading - this is not a general number parser and is not trying to be.
_SPOKEN_NUMBERS: dict[str, str] = {
    "one twenty": "120", "one thirty": "130", "one forty": "140",
    "one fifty": "150", "one sixty": "160", "one seventy": "170",
    "one eighty": "180", "one ninety": "190", "two hundred": "200",
    "ninety": "90", "eighty": "80", "seventy": "70", "sixty": "60",
    "hundred": "100", "one ten": "110", "one hundred": "100",
}
_SPOKEN_NUMBER_RE = re.compile(
    r"\b(" + "|".join(sorted(_SPOKEN_NUMBERS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
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
    working = canonicalise_terms(working)
    working = normalise_blood_pressure(working)
    return working
