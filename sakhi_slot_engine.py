"""Deterministic slot filling for the zero-TTS voice turn loop.

WHY THIS MODULE EXISTS
----------------------
On the phone path every user turn used to go STT -> LLM -> TTS: the model
decided what the answer meant and what to say next, and a TTS provider
synthesised it. Both are network round trips on every turn, and the model's
reading of an answer is probabilistic.

The intake questions never change, so neither step is needed:

  - an answer is turned into SLOTS here, with plain regex and word tables
    (no model): name, age, gestational weeks, systolic, diastolic, a yes/no;
  - `sakhi_intake.IntakeProgress.ingest_slots()` decides from the slots alone
    whether the question is complete, which slot is still missing, or which
    later question an answer already covered;
  - `audio_bank.AUDIO_BANK` plays the matching pre-recorded WAV.

A partial answer gets a MICRO-PROMPT that asks only for what is missing
("Lakshmi" to "name and age?" -> "and her age?"), never the whole question
again.

Clinical decisions are NOT made here. Symptoms come from
`sakhi_domain.detect_symptoms` and the risk decision from
`sakhi_triage.TRIAGE`, the single source of truth; this module only extracts
the readings those two consume.

Every extractor is one linear pass over the transcript (O(n) in its length,
with fixed-size word tables) and allocates nothing that outlives the call.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Any, Dict, Optional, Tuple

PROMPT_DIR = "assets/audio/prompts"
EMERGENCY_WAV = f"{PROMPT_DIR}/kn/emergency_referral.wav"


@dataclasses.dataclass(frozen=True)
class SlotRequirement:
    """One value a question needs, and the micro-prompt that asks for it alone."""

    key: str
    prompt_wav: str


@dataclasses.dataclass(frozen=True)
class QuestionSlotConfig:
    """What a question needs before the call can move on."""

    question_id: str
    main_wav: str
    slots: Tuple[SlotRequirement, ...]
    stage: int


def _wav(name: str) -> str:
    return f"{PROMPT_DIR}/kn/{name}.wav"


def _single(qid: str, stage: int) -> QuestionSlotConfig:
    """A one-slot question: its own answer slot, re-asked with the full question."""
    return QuestionSlotConfig(qid, _wav(f"{qid}_full"), (SlotRequirement(qid, _wav(f"{qid}_full")),), stage)


def _build_configs() -> Dict[str, QuestionSlotConfig]:
    from sakhi_intake import INTAKE_QUESTIONS

    configs = {q.id: _single(q.id, q.stage) for q in INTAKE_QUESTIONS}
    configs["q01"] = QuestionSlotConfig(
        "q01", _wav("q01_full"),
        (SlotRequirement("name", _wav("q01_ask_name")), SlotRequirement("age", _wav("q01_ask_age"))),
        1,
    )
    configs["q02"] = QuestionSlotConfig(
        "q02", _wav("q02_full"), (SlotRequirement("gestational_weeks", _wav("q02_full")),), 2,
    )
    configs["q06"] = QuestionSlotConfig(
        "q06", _wav("q06_full"),
        (SlotRequirement("systolic", _wav("q06_full")), SlotRequirement("diastolic", _wav("q06_ask_diastolic"))),
        3,
    )
    configs["q07"] = QuestionSlotConfig(
        "q07", _wav("q07_full"), (SlotRequirement("weight_kg", _wav("q07_full")),), 3,
    )
    return configs


QUESTION_CONFIGS: Dict[str, QuestionSlotConfig] = _build_configs()

# The words of every prompt that is not a whole intake question: what
# scripts/prerecord_prompts.py records, and what the transcript shows when
# one plays. Hindi is a DRAFT, like the rest of the intake's Hindi text.
PROMPT_TEXT: Dict[str, Dict[str, str]] = {
    "kn": {
        "q01_ask_name": "ತಾಯಿಯ ಹೆಸರು ಏನು?",
        "q01_ask_age": "ತಾಯಿಯ ವಯಸ್ಸು ಎಷ್ಟು?",
        "q06_ask_diastolic": "ಬಿಪಿಯ ಕೆಳಗಿನ ಸಂಖ್ಯೆ ಎಷ್ಟು?",
        "emergency_referral": (
            "ಇದು ತುರ್ತು ಅಪಾಯದ ಲಕ್ಷಣ. ಪ್ರಶ್ನೆಗಳನ್ನು ಇಲ್ಲಿಗೆ ನಿಲ್ಲಿಸುತ್ತಿದ್ದೇನೆ. ತಕ್ಷಣ ನೂರಾ ಎಂಟು ಗಾಡಿಗೆ ಕರೆ ಮಾಡಿ, "
            "ತಾಯಿಯನ್ನು ಜಿಲ್ಲಾ ಆಸ್ಪತ್ರೆಗೆ ಕರೆದುಕೊಂಡು ಹೋಗಿ. ನಾನು ವೈದ್ಯರಿಗೆ ಈಗಲೇ ತಿಳಿಸುತ್ತಿದ್ದೇನೆ."
        ),
    },
    "hi": {
        "q01_ask_name": "माता का नाम क्या है?",
        "q01_ask_age": "माता की उम्र कितनी है?",
        "q06_ask_diastolic": "बीपी का नीचे वाला नंबर कितना है?",
        "emergency_referral": (
            "यह खतरे का लक्षण है। मैं सवाल यहीं रोक रही हूँ। तुरंत एक सौ आठ एम्बुलेंस बुलाइए "
            "और माता को ज़िला अस्पताल ले जाइए। मैं अभी डॉक्टर को बता रही हूँ।"
        ),
    },
}
MICRO_PROMPTS = tuple(PROMPT_TEXT["kn"])


def prompt_text(path: str, lang: str) -> str:
    """What a prompt WAV says, for the transcript (and the recorder)."""
    from sakhi_intake import QUESTIONS_BY_ID, question_text

    key = path.rsplit("/", 1)[-1].removesuffix(".wav")
    if key.endswith("_full") and key[:-5] in QUESTIONS_BY_ID:
        return question_text(QUESTIONS_BY_ID[key[:-5]], lang)
    return PROMPT_TEXT.get(lang, PROMPT_TEXT["kn"]).get(key, "")


def all_prompt_paths(lang: str) -> list[str]:
    """Every WAV the turn loop can play in this language."""
    paths = {localized(c.main_wav, lang) for c in QUESTION_CONFIGS.values()}
    paths |= {localized(r.prompt_wav, lang) for c in QUESTION_CONFIGS.values() for r in c.slots}
    paths |= {localized(_wav(k), lang) for k in MICRO_PROMPTS}
    return sorted(paths)


def localized(path: str, lang: str) -> str:
    """The same prompt in another language's folder ("kn" -> "hi")."""
    return path.replace(f"{PROMPT_DIR}/kn/", f"{PROMPT_DIR}/{lang}/", 1)


# --- normalisation -------------------------------------------------------

# Hesitations and forms of address carry nothing: "um ಅಕ್ಕ 140 90" is "140 90".
_FILLERS = frozenset({
    "uh", "uhh", "um", "umm", "hmm", "hm", "ah", "er", "erm", "sister", "akka", "madam", "maam",
    "ಅಕ್ಕ", "ಅಕ್ಕಾ", "ಮೇಡಂ", "ಹ್ಮ್", "ಅ", "ಆಂ",
    "दीदी", "बहन", "जी", "मैडम",
})
_WORD_RE = re.compile(r"[\wಀ-೿ऀ-ॿ']+", re.UNICODE)


def strip_fillers(text: str) -> str:
    """Drop hesitation and address words, keep everything else in order."""
    return " ".join(w for w in (text or "").split() if w.strip(".,!?").casefold() not in _FILLERS)


_EN_NUM = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
    "forty": 40, "fourty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
    "hundred": 100,
}
_HI_NUM = {
    "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाँच": 5, "छह": 6, "छः": 6, "सात": 7, "आठ": 8,
    "नौ": 9, "दस": 10, "ग्यारह": 11, "बारह": 12, "तेरह": 13, "चौदह": 14, "पंद्रह": 15, "सोलह": 16,
    "सत्रह": 17, "अठारह": 18, "उन्नीस": 19, "बीस": 20, "इक्कीस": 21, "बाईस": 22, "तेईस": 23,
    "चौबीस": 24, "पच्चीस": 25, "छब्बीस": 26, "सत्ताईस": 27, "अट्ठाईस": 28, "उनतीस": 29, "तीस": 30,
    "इकतीस": 31, "बत्तीस": 32, "तैंतीस": 33, "चौंतीस": 34, "पैंतीस": 35, "छत्तीस": 36, "सैंतीस": 37,
    "अड़तीस": 38, "उनतालीस": 39, "चालीस": 40, "पैंतालीस": 45, "पचास": 50, "पचपन": 55, "साठ": 60,
    "पैंसठ": 65, "सत्तर": 70, "पचहत्तर": 75, "अस्सी": 80, "पचासी": 85, "नब्बे": 90, "पचानवे": 95,
    "सौ": 100,
}
_NUM_WORDS = {**_EN_NUM, **_HI_NUM}
_TOKEN_RE = re.compile(r"\d+|[\wಀ-೿ऀ-ॿ]+|[/,.;:?!-]", re.UNICODE)


def _combine(values: list[int]) -> list[int]:
    """Spoken number words -> numbers: "one forty" 140, "hundred forty five" 145,
    "एक सौ चालीस" 140. One pass over a run of number words."""
    out: list[int] = []
    cur: Optional[int] = None
    for v in values:
        if cur is None:
            cur = v
        elif v == 100 and cur < 10:
            cur *= 100                                    # "one hundred"
        elif cur >= 100 and cur % 100 == 0 and v < 100:
            cur += v                                      # "hundred | forty"
        elif cur % 100 >= 20 and cur % 10 == 0 and v < 10:
            cur += v                                      # "forty | five"
        elif cur in (1, 2) and 20 <= v < 100 and v % 10 == 0:
            cur = cur * 100 + v                           # BP shorthand "one forty"
        else:
            out.append(cur)
            cur = v
    if cur is not None:
        out.append(cur)
    return out


def normalize_numbers(text: str) -> str:
    """Every spoken number as digits, in all three call languages.

    Kannada (with its fused sandhi forms, "ಇಪ್ಪತ್ತೈದು") goes through the
    repo's own normaliser; English and Hindi number words are folded here.
    """
    from clinical_terms import normalise_kannada_numbers

    working = normalise_kannada_numbers(text or "")
    parts: list[str] = []
    run: list[int] = []

    def flush() -> None:
        if run:
            parts.extend(str(n) for n in _combine(run))
            run.clear()

    for tok in _TOKEN_RE.findall(working):
        word = tok.casefold()
        if word in _NUM_WORDS:
            run.append(_NUM_WORDS[word])
            continue
        if word in ("and", "-") and run:
            continue                                      # "hundred and forty", "forty-five"
        flush()
        parts.append(tok)
    flush()
    return " ".join(parts)


def _numbers(normalized: str) -> list[Tuple[int, int]]:
    """(value, token index) of every number in an already-normalised text."""
    return [(int(t), i) for i, t in enumerate(normalized.split()) if t.isdigit()]


# --- extractors ----------------------------------------------------------

_AGE_MARKERS = ("ವಯಸ್ಸು", "ವಯಸ್", "ವರ್ಷ", "vayassu", "vayasu", "varsha", "age", "years", "year", "yrs",
                "saal", "साल", "वर्ष", "उम्र")
_NAME_STOP = frozenset({
    "ತಾಯಿಯ", "ತಾಯಿ", "ಹೆಸರು", "ಹೆಸರ", "ಅವರ", "ಅವಳ", "ಅವರು", "ಅವಳು", "ಇವರ", "ಇವಳ", "ನನ್ನ", "ಅಂತ", "ಅಂತಾ",
    "ಇದೆ", "ಆಗಿದೆ", "ವಯಸ್ಸು", "ವರ್ಷ", "ವರ್ಷದ", "ವರ್ಷಗಳು", "ಮತ್ತು", "ಅಮ್ಮ", "ಹೌದು", "ಇಲ್ಲ",
    "नाम", "माता", "मां", "का", "की", "के", "है", "हैं", "उम्र", "साल", "और", "उनका", "उनकी", "मेरा", "मेरी",
    "my", "her", "his", "the", "mother", "mothers", "name", "names", "is", "and", "age", "years", "year",
    "old", "she", "it", "yes", "no", "ok", "okay", "hello",
})


def extract_age(text: str) -> Optional[int]:
    """An age of 12-55: the number beside an age word, else a lone number in range."""
    norm = normalize_numbers(text)
    words = norm.split()
    nums = _numbers(norm)
    for value, i in nums:
        near = " ".join(words[max(0, i - 2): i + 3]).casefold()
        if 12 <= value <= 55 and any(m in near for m in _AGE_MARKERS):
            return value
    in_range = [v for v, _ in nums if 12 <= v <= 55]
    return in_range[0] if len(in_range) == 1 and len(nums) == 1 else None


def extract_name(text: str) -> Optional[str]:
    """The words of an answer that are a name (the rest are fillers and numbers)."""
    from sakhi_domain import clean_person_name, detect_patient_demographics

    name, _ = detect_patient_demographics(text)
    if name:
        return name
    tokens = _WORD_RE.findall(normalize_numbers(strip_fillers(text)))
    # A word right after a number is its unit ("7 ತಿಂಗಳು", "58 kg"), never a name.
    words = [w for i, w in enumerate(tokens)
             if not w.isdigit() and not (i and tokens[i - 1].isdigit())
             and w.casefold() not in _NAME_STOP and not any(m in w.casefold() for m in _AGE_MARKERS)]
    candidate = " ".join(words[:3])
    return clean_person_name(candidate) if candidate else None


def extract_demographics(text: str) -> Tuple[Optional[str], Optional[int]]:
    """(name, age 12-55). Either may be None: that is the missing slot."""
    age = extract_age(text)
    if age is None:
        from sakhi_domain import detect_patient_demographics

        _, age = detect_patient_demographics(text)
        age = age if age is not None and 12 <= age <= 55 else None
    return extract_name(text), age


# "140 90", "140/90", "140 by 90", "one forty over ninety", "140 ಮೇಲೆ 90",
# and "140 ಮತ್ತು 90" ("140 and 90" - also how a cut-off answer joined to its
# continuation reads). _plausible_bp still decides: both in range, sys > dia.
_BP_SEP = r"(?:/|-|by|over|upon|slash|on|and|ಮೇಲೆ|ಮ್ಯಾಲೆ|ಬೈ|ಬಾಯ್|ಓವರ್|ಮತ್ತು|बाई|बाय|बटा|ओवर|पर|और)?"
_BP_RE = re.compile(rf"(?<!\d)(\d{{2,3}})\s*{_BP_SEP}\s*(\d{{2,3}})(?!\d)", re.IGNORECASE)


def _plausible_bp(sys_: int, dia: int) -> bool:
    from sakhi_domain import BloodPressure

    return (BloodPressure.MIN_SYSTOLIC <= sys_ <= BloodPressure.MAX_SYSTOLIC
            and BloodPressure.MIN_DIASTOLIC <= dia <= BloodPressure.MAX_DIASTOLIC and sys_ > dia)


def extract_blood_pressure(text: str) -> Tuple[Optional[int], Optional[int]]:
    """(systolic, diastolic). A single reading of 70+ is the systolic alone,
    so the micro-prompt asks only for the diastolic."""
    norm = normalize_numbers(text)
    for m in _BP_RE.finditer(norm):
        sys_, dia = int(m.group(1)), int(m.group(2))
        if _plausible_bp(sys_, dia):
            return sys_, dia
    nums = [v for v, _ in _numbers(norm) if 30 <= v <= 300]
    if len(nums) == 1 and nums[0] >= 70:
        return nums[0], None
    return None, None


_MONTH_RE = re.compile(r"(?<!\d)(\d{1,2})\s*(?:ನೇ\s*)?(?:ತಿಂಗಳ\w*|months?|mahine|महीन\w*|माह)", re.IGNORECASE)
_WEEK_RE = re.compile(r"(?<!\d)(\d{1,2})\s*(?:ವಾರ\w*|weeks?|wks?|hafte|हफ्त\w*|सप्ताह)", re.IGNORECASE)


def extract_gestation(text: str) -> Optional[int]:
    """Weeks pregnant. Months become weeks at 4 per month (5 months -> 20),
    the conversion the rest of the pipeline already uses."""
    norm = normalize_numbers(text)
    week = _WEEK_RE.search(norm)
    if week and 4 <= int(week.group(1)) <= 42:
        return int(week.group(1))
    month = _MONTH_RE.search(norm)
    if month and 1 <= int(month.group(1)) <= 10:
        return int(month.group(1)) * 4
    from sakhi_domain import detect_gestational_age

    return detect_gestational_age(text)


_KG_RE = re.compile(r"(?<!\d)(\d{2,3})\s*(?:ಕೆಜಿ|ಕಿಲೋ\w*|kgs?|kilos?|किलो|केजी)", re.IGNORECASE)


def extract_weight(text: str) -> Optional[int]:
    norm = normalize_numbers(text)
    m = _KG_RE.search(norm)
    if m and 30 <= int(m.group(1)) <= 150:
        return int(m.group(1))
    return None


_AFFIRM = ("ಹೌದು", "ಹೌದ್ರಿ", "ಹೌದ್ರೀ", "ಹೌದ", "ಹೂಂ", "ಹೂ", "ಹಾಂ", "ಹಾ", "ಆಯ್ತು", "ಸರಿ",
           "haan", "han", "houdu", "haudu", "hu", "huu", "yes", "yeah", "yep",
           "हाँ", "हां", "जी हाँ", "जी हां")
_NEGATE = ("ಇಲ್ಲ", "ಇಲ್ಲಾ", "ಇಲ್ರೀ", "ಇಲ್ರಿ", "ಇಲ್ಲಪ್ಪ", "ಇಲ್ಲಮ್ಮ", "ಬೇಡ",
           "illa", "illla", "no", "nope", "nahi", "nahin", "नहीं", "नही", "ना")
# Word-final negation fused onto a verb: "ಆಗಿಲ್ಲ", "ಮಾಡಿಸಿಲ್ಲ", "ಬಂದಿಲ್ಲ".
_FUSED_NEG_RE = re.compile(r"[ಀ-೿]+ಲ್ಲ$")
_POLARITY = {w.casefold(): True for w in _AFFIRM} | {w.casefold(): False for w in _NEGATE}


def extract_polarity(text: str) -> Optional[bool]:
    """Yes (True), no (False) or neither (None). The LAST yes/no word wins:
    "ಹೌದು, ಆದರೆ ಈಗ ಇಲ್ಲ" (yes, but not now) is a no."""
    result: Optional[bool] = None
    for w in _WORD_RE.findall(text or ""):
        word = w.casefold()
        if word in _POLARITY:
            result = _POLARITY[word]
        elif _FUSED_NEG_RE.search(word):
            result = False
    return result


def extract_all(raw_text: str, expecting: Optional[str] = None) -> Dict[str, Any]:
    """Every slot one answer carries, for ANY question (omnidirectional).

    `expecting` is the pending question id. Only the question just asked may
    read a bare value: a lone "24" is an age to q01, a reading to nothing
    else, and a name is only looked for when q01 asked for one.
    """
    text = strip_fillers(raw_text)
    slots: Dict[str, Any] = {}
    if expecting == "q01":
        name, age = extract_demographics(text)
        if name:
            slots["name"] = name
        if age is not None:
            slots["age"] = age
    else:
        norm = normalize_numbers(text).casefold()
        if any(m in norm for m in _AGE_MARKERS):
            age = extract_age(text)
            if age is not None:
                slots["age"] = age
    weeks = extract_gestation(text)
    if weeks is None and expecting == "q02":
        nums = [v for v, _ in _numbers(normalize_numbers(text))]
        if len(nums) == 1:
            weeks = nums[0] * 4 if 1 <= nums[0] <= 10 else nums[0] if 11 <= nums[0] <= 42 else None
    if weeks is not None:
        slots["gestational_weeks"] = weeks
    if expecting == "q06" or re.search(r"bp|ಬಿಪಿ|ಬಿ\.ಪಿ|बीपी|pressure|ರಕ್ತದೊತ್ತಡ|रक्तचाप", text, re.IGNORECASE):
        sys_, dia = extract_blood_pressure(text)
        if sys_ is not None and dia is not None:
            slots["systolic"], slots["diastolic"] = sys_, dia
        elif expecting == "q06":
            # One number: systolic or diastolic depends on what is already
            # known, which only the intake has (ingest_slots decides).
            nums = [v for v, _ in _numbers(normalize_numbers(text)) if 30 <= v <= 300]
            if len(nums) == 1:
                slots["bp_single"] = nums[0]
    kg = extract_weight(text)
    if kg is None and expecting == "q07":
        nums = [v for v, _ in _numbers(normalize_numbers(text)) if 30 <= v <= 150]
        kg = nums[0] if len(nums) == 1 else None
    if kg is not None:
        slots["weight_kg"] = kg
    polarity = extract_polarity(text)
    if polarity is not None:
        slots["polarity"] = polarity
    return slots
