"""The clinical intake questionnaire, as DATA rather than as prose.

WHY THIS MODULE EXISTS
----------------------
The 24-question intake lived only inside the system prompt, as a numbered list
the model was asked to follow. Nothing tracked it. `SakhiCallState` carried 16
fields and `prompt_summary()` reported exactly three facts (mother, bp,
symptoms), so roughly twenty answers had nowhere to be stored and no
completion marker.

That is the whole mechanism behind the two reported symptoms:

  - **Re-asking and skipping.** With no record of what had been asked, the
    model re-derived its position in the list from the raw transcript every
    turn. A garbled STT turn, a context prune, or an out-of-order answer and it
    lost its place - so it asked question 7 twice and never asked 11.

  - **Hallucination.** Asked to produce "the next question" with no state to
    ground it, a low-temperature model will still produce *something*. Absent
    a checklist it invents plausible-sounding clinical questions, and on a
    pre-eclampsia screening call an invented question is an invented finding.

The fix is to stop asking the model to remember, and make the questionnaire
authoritative in code:

  - `INTAKE_QUESTIONS` is the single source of truth. `sakhi_prompts.py`
    RENDERS the prompt inventory from it, so the prompt and the tracker can
    never disagree - previously they were two hand-maintained copies.
  - `IntakeProgress` records what has been answered and computes the next
    question deterministically.
  - The exact next question is injected into every generation, so the model is
    never guessing where it is.

AUTO-SATISFACTION
-----------------
Some questions are already answered by data the pipeline captures on its own -
name and age from `search_patient`, gestational weeks from registration, BP
from `record_bp`, danger symptoms from deterministic detection. Those carry a
`satisfied_by` slot and are marked answered without being asked, so Sakhi does
not interrogate an ASHA worker for something the record already holds.

BILINGUAL TEXT AND CONDITIONAL QUESTIONS (2026-09-10)
------------------------------------------------------
Rewired onto "Antenatal Quick Check - Primary Care Provider" (a rapid ANC
screening form the user supplied), which added two things the original
7-stage protocol didn't need yet:

  - **Real Hindi text.** Until now `SAKHI_SYSTEM_PROMPT_HI` rendered this same
    Kannada-only `text` field - an ASHA worker who picked Hindi was reading
    (or hearing) Kannada question wording. `text_hi` is optional per question;
    `question_text()` falls back to the Kannada `text` for any question that
    doesn't have one yet, the same safe-fallback shape `system_prompt_for()`
    already uses for an unrecognized language.
  - **Conditional applicability - the one real "edge" this graph needed.**
    Everything before this was a flat, unconditional sequence. Exactly one new
    question (fetal heart rate) only makes clinical sense from ~20 weeks;
    asking it at 10 weeks is not "strict," it's nonsensical. `applicable_if`
    is a plain predicate over `SakhiCallState`, not a generic graph engine -
    the least amount of machinery that turns "skip this one, in this
    condition" from a prompt-obeyed-probabilistically suggestion into
    something the code enforces. See `IntakeProgress.sync_applicability()`.

The revised/added questions mirror the source PDF's own ASK section bundling
(bleeding/leaking/pain/contractions; fetal movement; headache/vision/swelling/
upper-abdominal-pain; fever/urinary/breathless/palpitations/vomiting; chronic
disease history; medications/allergies) and CHECK section (BP, weight,
pallor/edema, fetal heart rate, lab/scan review). Two clinical-calibration
choices in this rewrite were put to the user directly, not assumed - see
`sakhi_triage.py`'s module docstring for both. Kannada text for the reworded/
new questions follows the same North Karnataka rural register as the existing
lines. Hindi text is a first-pass draft in the same warm, simple-spoken-Hindi
register `sakhi_prompts.py`'s Hindi latch rule already asks for - like this
file's own earlier corrupted-question reconstructions, it is marked DRAFT and
needs a native Hindi speaker/clinician to confirm the phrasing before it is
treated as final.
"""

from __future__ import annotations

import dataclasses
import re
from typing import Any, Callable, Dict, Tuple


@dataclasses.dataclass(frozen=True)
class IntakeQuestion:
    """One question. Frozen: the questionnaire is not editable at runtime."""

    id: str
    section: str
    text: str
    vad: str  # FAST for yes/no, dates, numbers; SLOW for open description
    # Name of a SakhiCallState-derived fact that answers this without asking.
    satisfied_by: str | None = None
    # Which of the 7 ANC stages this belongs to (1-7). The stage is the unit
    # the protocol advances through; the question is the unit a turn asks.
    stage: int = 1
    # Hindi wording (2026-09-10). Optional so every pre-existing question
    # keeps working with no text_hi at all - question_text() falls back to
    # the Kannada `text` above. DRAFT for every question that has one below;
    # see this module's docstring.
    text_hi: str | None = None
    # A predicate over SakhiCallState deciding whether this question applies
    # at all this call. None means "always applies" - the default for every
    # question except the one that genuinely needs gating (fetal heart rate,
    # gestational-age-conditional). Kept as a plain function, not a class or
    # a second data structure, so most questions never have to think about it.
    applicable_if: Callable[..., bool] | None = None


def question_text(question: "IntakeQuestion", language: str = "kannada") -> str:
    """The question's text in the requested language.

    Falls back to the Kannada `text` field for Hindi/English when a
    per-language variant hasn't been written yet, the same shape
    `sakhi_prompts.system_prompt_for()` already uses for an unrecognized
    language - a missing translation degrades to "still asks the right
    clinical question", never to a blank or a crash.
    """
    # Accepts the Language enum too. str(Language.HINDI) is "Language.HINDI",
    # which matched nothing, so every caller passing state.language (the
    # per-turn prompt, the tools) handed Hindi calls the Kannada question.
    lang = str(getattr(language, "value", language) or "").strip().lower()
    if lang in ("hi", "hindi", "hin") and question.text_hi:
        return question.text_hi
    return question.text


_Q01_MISSING = {
    # (missing field) -> (kannada, hindi)
    "name": ("ಸರಿ. ತಾಯಿಯ ಹೆಸರು ಏನು?", "ठीक है। माँ का नाम क्या है?"),
    "age": ("ಸರಿ. ತಾಯಿಯ ವಯಸ್ಸು ಎಷ್ಟು?", "ठीक है। माँ की उम्र कितनी है?"),
}


def armed_question_text(question: "IntakeQuestion", state, language) -> str:
    """What to say for the armed question, given what is already known.

    q01 asks for name AND age. When one of the two has already been heard
    (a real call, 2026-09-25: the transcript carried only "... 28 ವರ್ಷ"),
    repeating the whole question tells the ASHA worker she was not heard at
    all. Ask only for the part that is missing. Every other question - and
    q01 with nothing known yet - is spoken exactly as written.
    """
    if question.id == "q01" and state is not None:
        has_name = bool(getattr(state, "name", None))
        has_age = bool(getattr(state, "age", None))
        missing = "name" if has_age and not has_name else "age" if has_name and not has_age else None
        lang = str(getattr(language, "value", language) or "").strip().lower()
        if missing and lang in ("kn", "kannada", "hi", "hindi", "hin"):
            kn, hi = _Q01_MISSING[missing]
            return hi if lang.startswith("hi") else kn
    return question_text(question, language)


def question_for_model(question: "IntakeQuestion", language) -> str:
    """The next question as handed to the MODEL in a tool result: the
    written wording where it exists, otherwise the Kannada meaning with an
    explicit instruction to say it in the call's language - an English call
    was hearing the Kannada text read out verbatim."""
    if has_localised_text(question, language):
        return question_text(question, language)
    lang = str(getattr(language, "value", language) or "").strip().lower()
    name = {"en": "English", "english": "English", "hi": "Hindi", "hindi": "Hindi"}.get(lang, "the call language")
    return f"(say this in {name}, never in Kannada) {question.text}"


def has_localised_text(question: "IntakeQuestion", language) -> bool:
    """Is there written wording for this question in this language? Kannada
    always; Hindi where text_hi exists; English not yet (the model phrases
    English questions from the Kannada meaning)."""
    lang = str(getattr(language, "value", language) or "").strip().lower()
    if lang in ("kn", "kannada"):
        return True
    if lang in ("hi", "hindi", "hin"):
        return bool(question.text_hi)
    return False


# The 7-stage ANC field protocol (2026-09-02). Replaces the previous
# 26-question general clinical history, which was a hospital intake form
# rather than a field ANC visit: at strictly one question per turn it could
# not finish inside the 5-10 minute call this is built for, and it collected
# nothing about IFA tablets, Td vaccination, weight, or birth preparedness -
# the four things an ASHA worker's ANC visit actually exists to check.
#
# Question IDs stay on the qNN scheme rather than becoming s1q1/s2q1: the
# identity gate (agent.py), IntakeProgress.sync_from_state, and the frontend
# all address questions by id, and renaming the addressing scheme to express
# something the new `stage` field already carries would be churn with a
# migration cost and no gain.
ANC_STAGES: tuple[str, ...] = (
    "Stage 1: Basic Intake",
    "Stage 2: Obstetric History & Gestation",
    "Stage 3: Vitals & Nutrition",
    "Stage 4: High-Risk Red-Flag Screening",
    "Stage 5: Scans & Tetanus (Td)",
    "Stage 6: Birth Preparedness",
    "Stage 7: Confirmation & Close",
)


def stage_name(stage: int) -> str:
    """1-indexed stage label, tolerant of an out-of-range value."""
    if 1 <= stage <= len(ANC_STAGES):
        return ANC_STAGES[stage - 1]
    return f"Stage {stage}"


SECTION_1 = ANC_STAGES[0]
SECTION_2 = ANC_STAGES[1]
# Pre-eclampsia screening is this agent's clinical purpose, so BP and the
# danger signs are part of the ordered flow - not a separate track that jumped
# the queue. Deterministic symptom detection still runs on EVERY turn
# independently of this ordering, and triage still escalates the moment a
# severe reading is reported - so a mother in danger is never gated behind
# the questionnaire.
SECTION_VITALS = ANC_STAGES[2]
SECTION_REDFLAG = ANC_STAGES[3]
SECTION_SCANS = ANC_STAGES[4]
SECTION_BIRTHPREP = ANC_STAGES[5]
SECTION_CLOSE = ANC_STAGES[6]


def _fhr_applicable(state) -> bool:
    """Fetal heart rate only makes clinical sense from ~20 weeks.

    Unknown gestation is treated as applicable (ask it) rather than skipped -
    the same under-triage-averse default `sakhi_triage.py` already uses for
    reduced fetal movement and preterm contractions: a screening tool should
    never silently skip a check because a fact it needs wasn't given yet.
    """
    weeks = getattr(state, "gestational_weeks", None)
    return weeks is None or weeks >= 20


def _fetal_movement_applicable(state) -> bool:
    """Fetal movements only apply from 24 weeks gestation.
    Skip if gestational age is under 24 weeks.
    Unknown gestation is treated as applicable (ask it).
    """
    weeks = getattr(state, "gestational_weeks", None)
    return weeks is None or weeks >= 24


# Declared in the order each stage's content reads most naturally; the actual
# call/prompt order is never taken from this declaration position - see
# INTAKE_QUESTIONS below, which sorts by `stage` so a question can be added
# near the content it belongs with without having to also relocate it inside
# the tuple by hand.
_RAW_QUESTIONS: tuple[IntakeQuestion, ...] = (
    # --- Block 1 / Stage 1 & 2: Demographics & Pregnancy History ---------
    IntakeQuestion("q01", SECTION_1,
        "ತಾಯಿಯ ಹೆಸರು ಮತ್ತು ವಯಸ್ಸು ಎಷ್ಟು?", "SLOW", "identity", stage=1,
        text_hi="माता का नाम और उम्र बताइए दीदी"),

    IntakeQuestion("q02", SECTION_2,
        "ಕೊನೆಯ ಮುಟ್ಟಿನ ದಿನಾಂಕ ಅಥವಾ ಹೆರಿಗೆಯ ಅಂದಾಜು ದಿನಾಂಕ ಯಾವಾಗ? ಈಗ ಎಷ್ಟು ತಿಂಗಳು ಅಥವಾ ವಾರಗಳು?",
        "FAST", "gestational_weeks", stage=2,
        text_hi="आखिरी माहवारी की तारीख या प्रसव की संभावित तारीख कब है? अभी कितने महीने या हफ्ते हैं?"),
    # Only when q02's answer gave no gestational age. q02 already asks
    # "LMP or EDD, and how many months or weeks"; asking "when was the last
    # period?" straight after "six months" is heard as the same question
    # twice (live-LLM scenarios, 2026-09-24).
    IntakeQuestion("q03", SECTION_2,
        "ಕೊನೆಯ ಮುಟ್ಟಿನ ದಿನಾಂಕ ಯಾವಾಗ?", "FAST", stage=2,
        text_hi="आखिरी माहवारी की तारीख कब थी?",
        applicable_if=lambda state: not getattr(state, "gestational_weeks", None)),
    IntakeQuestion("q04", SECTION_2,
        "ಇದು ಎಷ್ಟನೇ ಗರ್ಭಧಾರಣೆ? ಈ ಮೊದಲು ಎಷ್ಟು ಮಕ್ಕಳು ಜನಿಸಿದ್ದಾರೆ?", "FAST", stage=2,
        text_hi="यह कौन सा गर्भ है? पहले कितने बच्चे जन्मे हैं?"),
    IntakeQuestion("q05", SECTION_2,
        "ಹಿಂದಿನ ಹೆರಿಗೆಗಳಲ್ಲಿ ಸಿ-ಸೆಕ್ಷನ್, ಅಧಿಕ ರಕ್ತಸ್ರಾವ, ಅಥವಾ ಬೇರೆ ತೊಂದರೆಗಳು ಆಗಿದ್ದವಾ?", "FAST", stage=2,
        text_hi="पहले के प्रसव में सी-सेक्शन, अधिक रक्तस्राव, या कोई और समस्या हुई थी?"),
    IntakeQuestion("q21", SECTION_2,
        "ಇದು ನೈಸರ್ಗಿಕ ಗರ್ಭಧಾರಣೆಯಾ ಅಥವಾ IVF ಚಿಕಿತ್ಸೆ ಪಡೆದಿದ್ದಾರಾ?", "FAST", stage=2,
        text_hi="यह प्राकृतिक गर्भ है या आई.वी.एफ इलाज करवाया था?"),

    # --- Block 4 / Stage 3: Clinical Vitals & Labs (CHECK) ---------------
    IntakeQuestion("q06", SECTION_VITALS,
        "ತಾಯಿಯ ಇಂದಿನ ಬಿಪಿ ಎಷ್ಟು ದಾಖಲಾಗಿದೆ?", "FAST", "blood_pressure", stage=3,
        text_hi="आज का रक्तचाप कितना दर्ज हुआ है?"),
    IntakeQuestion("q07", SECTION_VITALS,
        "ತಾಯಿಯ ತೂಕ ಎಷ್ಟು ಕೆಜಿ ಇದೆ?", "FAST", stage=3,
        text_hi="माता का वजन कितने किलो है?"),
    IntakeQuestion("q08", SECTION_VITALS,
        "ಕಬ್ಬಿಣಾಂಶ ಗುಳಿಗೆ ತಪ್ಪದೆ ತಿನ್ನುತ್ತಿದ್ದಾಳಾ?", "FAST", stage=3,
        text_hi="आयरन की गोली रोज बिना चूके खा रही है क्या?"),
    IntakeQuestion("q24", SECTION_VITALS,
        "ಕಣ್ಣು ಅಥವಾ ಉಗುರುಗಳಲ್ಲಿ ರಕ್ತಹೀನತೆ (ಬಿಳುಪು), ಅಥವಾ ಪಾದಗಳಲ್ಲಿ ಊತ ಕಂಡುಬಂದಿದೆಯಾ?", "FAST", stage=3,
        text_hi="आँख या नाखूनों में खून की कमी, या पैरों में सूजन दिखी है क्या?"),
    IntakeQuestion("q25", SECTION_VITALS,
        "ಮಗುವಿನ ಹೃದಯ ಬಡಿತ (FHR) ಪರೀಕ್ಷೆ ಮಾಡಲಾಗಿದೆಯಾ? ಎಷ್ಟಿದೆ?", "FAST", stage=3,
        text_hi="बच्चे की धड़कन (एफ.एच.आर) जांची क्या, कितनी है?",
        applicable_if=_fhr_applicable),

    # --- Block 2 & 3 / Stage 4: Symptom Screen & Medical History ---------
    IntakeQuestion("q09", SECTION_REDFLAG,
        "ತೀವ್ರ ತಲೆನೋವು, ಕಣ್ಣು ಮಂಜಾಗುವುದು, ಅಥವಾ ಮುಖ ಕೈಕಾಲುಗಳಲ್ಲಿ ಊತ ಇದೆಯಾ?",
        "FAST", "symptoms_screened", stage=4,
        text_hi="तेज सिरदर्द, धुंधला दिखना, या चेहरे-हाथ-पैरों में सूजन है क्या?"),
    IntakeQuestion("q10", SECTION_REDFLAG,
        "ಈಗ ತಾಯಿಗೆ ಹೊಟ್ಟೆ ನೋವು, ರಕ್ತಸ್ರಾವ, ಅಥವಾ ನೀರಿನಂಶ ಹೋಗುವ ತೊಂದರೆ ಇದೆಯಾ?", "FAST", stage=4,
        text_hi="अभी पेट दर्द, रक्तस्राव, या पानी जाने की तकलीफ है क्या?"),
    IntakeQuestion("q11", SECTION_REDFLAG,
        "ಫಿಟ್ಸ್ ಅಥವಾ ಸೆಳೆತ ಆಗಿದೆಯ?", "FAST", stage=4,
        text_hi="दौरा या झटके आए हैं क्या?"),
    IntakeQuestion("q12", SECTION_REDFLAG,
        "ಮಗುವಿನ ಚಲನೆ ಸರಿಯಾಗಿ ಗೊತ್ತಾಗುತ್ತಿದೆಯಾ?", "FAST", stage=4,
        text_hi="बच्चे की हलचल ठीक से महसूस हो रही है क्या?",
        applicable_if=_fetal_movement_applicable),
    IntakeQuestion("q18", SECTION_REDFLAG,
        "ಜ್ವರ, ತೀವ್ರ ವಾಂತಿ, ಅಥವಾ ಉಸಿರಾಟದ ತೊಂದರೆ ಏನಾದರೂ ಇದೆಯಾ?",
        "FAST", stage=4,
        text_hi="बुखार, तेज उल्टी, या सांस लेने में कोई तकलीफ है क्या?"),
    IntakeQuestion("q19", SECTION_REDFLAG,
        "ತಾಯಿಗೆ ಮೊದಲಿನಿಂದ ಶುಗರ್, ಬಿಪಿ, ಥೈರಾಯ್ಡ್, ಅಥವಾ ಫಿಟ್ಸ್ ತೊಂದರೆ ಇದೆಯಾ?",
        "SLOW", stage=4,
        text_hi="माता को पहले से शुगर, बीपी, थायरॉइड, या मिर्गी की समस्या है क्या?"),
    IntakeQuestion("q20", SECTION_REDFLAG,
        "ಈಗ ಯಾವುದಾದರೂ ಮಾತ್ರೆಗಳನ್ನು ತೆಗೆದುಕೊಳ್ಳುತ್ತಿದ್ದಾರಾ? ಯಾವುದಾದರೂ ಔಷಧಿಯಿಂದ ಅಲರ್ಜಿ ಇದೆಯಾ?",
        "SLOW", stage=4,
        text_hi="अभी कोई दवाइयां ले रही हैं? किसी दवाई से एलर्जी है क्या?"),

    # --- Stage 5: Scans & Labs -------------------------------------------
    IntakeQuestion("q13", SECTION_SCANS,
        "ಸ್ಕ್ಯಾನ್ ಮಾಡಿದೆಯ? (ಬಿ.ಆರ್.ಐ.ಎಂ.ಎಸ್ ಅಥವಾ ತಾಲೂಕು ಆಸ್ಪತ್ರೆ)", "FAST", stage=5,
        text_hi="स्कैन करवाया है क्या? (बी.आर.आई.एम.एस या तालुक अस्पताल में)"),
    IntakeQuestion("q14", SECTION_SCANS,
        "ಎರಡು ಧನುರ್ವಾತ ಚುಚ್ಚುಮದ್ದು ತೆಗೆದೆಯ?", "FAST", stage=5,
        text_hi="धनुर्वात के दोनों इंजेक्शन लगवाए हैं क्या?"),
    IntakeQuestion("q26", SECTION_SCANS,
        "ಇತ್ತೀಚಿನ ರಕ್ತ ಪರೀಕ್ಷೆಯಲ್ಲಿ ಹಿಮೋಗ್ಲೋಬಿನ್ (Hb) ಮತ್ತು ಶುಗರ್ ವರದಿ ಎಷ್ಟಿದೆ?", "SLOW", stage=5,
        text_hi="हाल की जांच में हीमोग्लोबिन और शुगर की रिपोर्ट कितनी है?"),

    # --- Stage 6: Birth Preparedness -------------------------------------
    IntakeQuestion("q15", SECTION_BIRTHPREP,
        "ಪ್ರಸವಕ್ಕೆ ಆಸ್ಪತ್ರೆಯಲ್ಲಿ ಡಾಕ್ಟರ್ ನರ್ಸ್ ಇದ್ದಾರಾ?", "SLOW", stage=6,
        text_hi="प्रसव के लिए अस्पताल में डॉक्टर नर्स मौजूद हैं क्या?"),
    IntakeQuestion("q16", SECTION_BIRTHPREP,
        "ನೂರನೆಂಟು (108) ಗಾಡಿ ಕರೆ ಮಾಡುವ ವ್ಯವಸ್ಥೆ ಇದೆಯ?", "FAST", stage=6,
        text_hi="एक सौ आठ (108) गाड़ी बुलाने की व्यवस्था है क्या?"),

    # --- Block 5 / Stage 7: Closing & Summary ----------------------------
    IntakeQuestion("q17", SECTION_CLOSE,
        "ಧನ್ಯವಾದಗಳು. ಎಲ್ಲಾ ವಿವರಗಳನ್ನು ಯಶಸ್ವಿಯಾಗಿ ದಾಖಲಿಸಲಾಗಿದೆ. ನಾನು ಈ ಮಾಹಿತಿಯನ್ನು ಪ್ರಾಥಮಿಕ ಆರೋಗ್ಯ ಕೇಂದ್ರದ ವೈದ್ಯರಿಗೆ ಕಳುಹಿಸುತ್ತಿದ್ದೇನೆ.", "SLOW", stage=7,
        text_hi="धन्यवाद। सभी विवरण सफलतापूर्वक दर्ज कर लिए गए हैं। मैं यह जानकारी डॉक्टर को भेज रही हूँ।"),
)

# Runtime order is sorted by stage, NOT by declaration position above -
# Python's sort is stable, so declaration order is preserved within a stage.
# This guarantees the tuple-order-equals-flow-order invariant
# next_question()/render_inventory()/the "stages sorted, monotonic" test
# already rely on, rather than depending on every future edit remembering to
# insert new questions in exactly the right physical spot in this file.
INTAKE_QUESTIONS: tuple[IntakeQuestion, ...] = tuple(
    sorted(_RAW_QUESTIONS, key=lambda q: q.stage)
)

QUESTIONS_BY_ID = {q.id: q for q in INTAKE_QUESTIONS}

assert len(QUESTIONS_BY_ID) == len(INTAKE_QUESTIONS), "duplicate intake question id"


def render_inventory(language: str = "kannada", stage: int | None = None) -> str:
    """The prompt's question list, generated from the questionnaire itself.

    If stage is specified (1-7), renders only the questions for that active stage,
    reducing token count from ~1,200 tokens to <150 tokens.
    """
    lines: list[str] = []
    current_stage = None
    questions = [q for q in INTAKE_QUESTIONS if stage is None or q.stage == stage]
    for index, question in enumerate(questions, start=1):
        if question.stage != current_stage:
            current_stage = question.stage
            lines.append(f"## {stage_name(current_stage)}")
        text = question_text(question, language)
        lines.append(f'{index}. "{text}" [VAD: {question.vad}]')
    return "\n".join(lines)


# FAST questions that ask for a VALUE (name, months, a date, a count, a
# reading) rather than yes/no. FAST only says "short answer expected"; a bare
# "ಹೂಂ" / "yes" is not a date. Live-LLM scenario 2026-09-24: "ಹೂಂ" closed q03
# (last-period date) and the checklist moved on with nothing recorded.
VALUE_QUESTION_IDS = frozenset({"q01", "q02", "q03", "q04", "q06", "q07", "q26"})


def accepts_assent(question: "IntakeQuestion | None") -> bool:
    """Can a one-word yes/no answer this question?"""
    return (
        question is not None
        and question.vad.upper() == "FAST"
        and question.id not in VALUE_QUESTION_IDS
    )


def is_assent(text: str) -> bool:
    """One-word yes/no, in any of the three call languages.

    Public because agent.py's turn gate needs the same answer this module's
    own intake tracker needs, and needed it EARLIER in the turn than the
    tracker runs. `is_backchannel()` matches the same words ("yes", "ok",
    "ಹೌದು", "हाँ") and used to swallow them with StopResponse() before the
    intake tracker was ever reached - so a real one-word answer to a closed
    clinical question was discarded as agreement noise. The gate now asks
    this function first; see agent.py's `_answers_pending_question()`.

    Kept as a module-level function rather than a method so the turn gate
    doesn't have to construct or reach into an IntakeProgress to ask.
    """
    return text.strip().strip(".,!?ಣ।").casefold() in IntakeProgress._ASSENTS


@dataclasses.dataclass(frozen=True)
class _SlotView:
    """What `applicable_if` predicates read, taken from the slots: a weeks
    value heard this turn gates q03/q12/q25 before call state has it."""

    gestational_weeks: int | None


@dataclasses.dataclass
class IntakeProgress:
    """Which intake questions are done, and what comes next.

    Mutable by design - it is per-call scratch state owned by SakhiCallState,
    not a value object.
    """

    answered: dict[str, str] = dataclasses.field(default_factory=dict)
    # The question Sakhi was told to ask on the previous turn. The ASHA
    # worker's next substantive turn is recorded as its answer.
    pending_id: str | None = None
    # STALL-ESCAPE (2026-09-10). Consecutive times the currently-pending
    # question's answer failed _is_substantive(). Keyed by question id so a
    # question that later comes back around (it can't, in this design, but
    # keying by id rather than a single counter costs nothing and stays
    # correct if that ever changes) doesn't inherit a stale count from a
    # different question. See record_answer() for the escape itself.
    _stall_counts: dict[str, int] = dataclasses.field(default_factory=dict)
    # ZERO-TTS SLOT FILLING (2026-11-01). Every value an answer carried
    # (name, age, gestational_weeks, systolic, diastolic, weight_kg, and a
    # question's own yes/no keyed by its id), from ANY answer of the call.
    # See ingest_slots() and sakhi_slot_engine.py.
    slot_values: Dict[str, Any] = dataclasses.field(default_factory=dict)

    # Two consecutive real-but-unclear replies to the SAME question is the
    # ceiling: the third non-empty reply closes it regardless, best-effort.
    # Without this, a genuinely poor STT transcript on one question can leave
    # it armed for the rest of the call - which is what "asks the same
    # question over and over" actually is from the ASHA worker's side: not
    # the model choosing to repeat itself, but nothing ever being allowed to
    # close the question it's stuck on.
    STALL_LIMIT = 2

    # --- mutators ------------------------------------------------------

    def mark_satisfied(self, question_id: str, answer: str = "(from record)") -> None:
        """Mark a question answered without having asked it."""
        if question_id in QUESTIONS_BY_ID:
            self.answered.setdefault(question_id, answer)

    def sync_from_state(self, state) -> None:
        """Tick off questions the pipeline already answered on its own.

        Prevents Sakhi asking for a name that `search_patient` returned, or a
        gestational age captured at registration.
        """
        if getattr(state, "name", None) and getattr(state, "age", None):
            self.mark_satisfied("q01", f"{state.name}, {state.age}")
        # Vitals are ticked off by the TOOLS that record them, not by the model
        # claiming it asked. record_bp() and deterministic symptom detection are
        # the only things that can satisfy these.
        if getattr(state, "gestational_weeks", None):
            self.mark_satisfied("q02", f"~{state.gestational_weeks} weeks")
        checkpoints = getattr(state, "anc_checkpoints", {}) or {}
        if "gravida" in checkpoints:
            self.mark_satisfied("q04", f"ಗರ್ಭಧಾರಣೆ {checkpoints['gravida']}")
        if getattr(state, "blood_pressure", None):
            self.mark_satisfied("q06", str(state.blood_pressure))
        if getattr(state, "symptoms_screened", False):
            self.mark_satisfied("q09", "screened")
        # The same facts as slots, so ingest_slots() skips what the record holds.
        bp = getattr(state, "blood_pressure", None)
        for key, value in (("name", getattr(state, "name", None)), ("age", getattr(state, "age", None)),
                           ("gestational_weeks", getattr(state, "gestational_weeks", None)),
                           ("systolic", getattr(bp, "systolic", None)), ("diastolic", getattr(bp, "diastolic", None))):
            if value:
                self.slot_values.setdefault(key, value)

    def sync_applicability(self, state) -> None:
        """Skip any unanswered question whose `applicable_if` says no.

        Called alongside sync_from_state() on every prompt_summary() build.
        Marks the question satisfied with a distinct "(not applicable)"
        answer rather than removing it from the count, so the audit trail
        shows WHY it was skipped instead of a silent gap - and so a later
        turn where the gating fact changes (e.g. gestational_weeks becomes
        known) can still re-evaluate anything not yet marked.
        """
        for question in INTAKE_QUESTIONS:
            if question.id in self.answered:
                continue
            if question.applicable_if is not None and not question.applicable_if(state):
                self.mark_satisfied(question.id, "(not applicable)")

    # A turn shorter than this is treated as noise, not as a clinical answer.
    # Live calls produced finals like "Yeah.", "Hello?" and "Mother's meeting"
    # from a half-heard Kannada name - each of which closed a question and
    # advanced the intake, which is what the ASHA worker experienced as Sakhi
    # "skipping to the next question very fast".
    #
    # This floor applies to SLOW (open-description) questions only - see
    # _is_substantive and the assent gate below.
    MIN_ANSWER_CHARS = 4

    # Turns that are pure acknowledgement or confusion. They are real speech,
    # so the backchannel filter upstream lets them through, but they answer
    # nothing that was ASKED OPENLY.
    _NON_ANSWERS = {
        "yeah", "yes", "ok", "okay", "hello", "hi", "hmm", "huh", "what",
        "sorry", "pardon", "no", "haan", "ha", "houdu", "illa",
        "ಹೌದು", "ಇಲ್ಲ", "ಹಲೋ", "ಏನು", "ಹಾಂ",
        "हाँ", "नहीं", "हलो", "क्या",
        # Romanized Hindi "it's fine/okay" (2026-09-08) - same acknowledgement
        # role as "ok"/"yeah" above, just untransliterated. Confirmed live
        # that neither this nor is_backchannel()'s Devanagari-only "ठीक"
        # pattern caught the Latin spelling a caller actually spoke.
        "theek hai", "theek hai ji", "thik hai", "theek", "thik",
        # Devanagari script (2026-09-08, found writing a mock-scenario test,
        # not from a live report - but the gap is real): the fix above only
        # covered the ROMANIZED spelling. A real Sarvam/Deepgram transcript
        # of Hindi speech renders in Devanagari, so "ठीक है"/"ठीक है जी" -
        # the actual script form of the exact same phrase - was still
        # unguarded, and "हेलो" (this spelling, vs. the "हलो" already above)
        # was too. Includes the compound "hello, it's fine ji" phrasing
        # verbatim, since _NON_ANSWERS/_ASSENTS only match a whole phrase and
        # there is no Hindi equivalent of _FILLER_WORDS' per-word stripping
        # to catch a multi-word filler generically.
        "ठीक है", "ठीक है जी", "हेलो", "हेलो ठीक है", "हेलो ठीक है जी",
    }

    # MONOSYLLABIC ASSENT GATE (2026-09-02).
    #
    # A rural North Karnataka ASHA worker answers a yes/no question with one
    # syllable - "ಹೂಂ" (huu), "ಆಂ" (aah), "ಇಲ್ಲ" (illa) - and nothing else.
    # Those turns were being thrown away by _NON_ANSWERS above, so "is she
    # taking the iron tablets?" answered with "ಹೂಂ" left the question still
    # pending and Sakhi asked it again. From her side the agent simply had
    # not heard her.
    #
    # The fix is NOT to delete the non-answer filter - it exists because a
    # bare "yeah" closing an OPEN question ("what brought her in today?") is
    # exactly the false-advance bug that filter was added for. The two rules
    # live at different layers and both are right:
    #
    #   FAST question (yes/no, number, date)  -> "ಹೂಂ" IS the answer.
    #   SLOW question (open description)      -> "ಹೂಂ" is a backchannel.
    #
    # so the filter is now scoped by the pending question's own VAD class.
    _ASSENTS = {
        # Kannada, spoken and transliterated
        "huu", "hu", "hoo", "hun", "aah", "aa", "ah", "an", "howdu", "haudu",
        "illa", "ille", "sari", "aytu", "aayitu", "ayitu",
        "ಹೂಂ", "ಹೂ", "ಆಂ", "ಹೌದು", "ಇಲ್ಲ", "ಸರಿ", "ಆಯ್ತು",
        # Hindi
        "haan", "ha", "nahi", "nahin", "हाँ", "हां", "नहीं",
        "theek hai", "theek hai ji", "thik hai", "theek", "thik",
        "ठीक है", "ठीक है जी",
        # English
        "yes", "yeah", "no", "nope", "yep", "done", "taken", "ok", "okay",
    }

    # WORD-LEVEL FILLER STRIPPING (2026-09-03).
    #
    # _NON_ANSWERS above only ever caught an EXACT whole-phrase match - "Yeah.",
    # "Hello?", a bare disfluency with nothing else in the turn. Confirmed live
    # that this misses the much more common case: a disfluent PHRASE that is
    # long enough to clear MIN_ANSWER_CHARS while still carrying zero clinical
    # content - "She's a yeah." (13 chars, not a whole-phrase match against
    # _NON_ANSWERS) closed "which month of pregnancy is she in now?" and the
    # real answer that followed one second later ("She's in sixth month.") had
    # nowhere to land, since the question was already marked done. That is the
    # same failure this file's own record_answer() docstring already names as
    # "hallucinating... telling something else" - not the LLM inventing facts,
    # the wrong (or no) fact silently closing the wrong slot.
    #
    # Rather than add "she's a yeah" as one more exact phrase to a blocklist
    # that will always be one utterance behind the next disfluency, this strips
    # common filler WORDS (pronouns+copulas, articles, hesitation sounds) from
    # the turn first and judges substance on what is left - the same
    # "deliberately permissive" spirit as before (a real short answer like "six
    # months" or "one forty by ninety" survives stripping untouched), just
    # applied per-word instead of per-whole-phrase.
    _FILLER_WORDS = {
        "she's", "shes", "he's", "hes", "it's", "its", "that's", "thats",
        "i'm", "im", "we're", "were", "a", "an", "the", "is", "was", "are",
        "um", "uh", "uhh", "umm", "hmm", "well", "so", "just", "like",
        "actually", "basically", "kind", "sort", "of", "to", "and", "but",
    } | _NON_ANSWERS

    # CONNECTIVITY/PRESENCE CHECKS (2026-09-08).
    #
    # "hello are you there", "can you hear me" - real speech, and long enough
    # to clear MIN_ANSWER_CHARS, but none of "hello"/"you"/"there" are in
    # _NON_ANSWERS (an exact-phrase set) or _FILLER_WORDS, so the word-strip
    # fallback below found real-looking content words and wrongly closed
    # whichever question was pending. Confirmed against a live report - this
    # is the same "wrong fact silently closes the wrong slot" failure the
    # module docstring already names, just a phrase the existing blocklists
    # didn't anticipate. A regex, not one more fixed phrase, because a check
    # like this comes in many wordings ("are you there?", "you there",
    # "hello, can you hear me") that a set would always be one behind.
    _PRESENCE_CHECK_RE = re.compile(
        r"^(?:hello[,.\s]*)?(?:are\s+you\s+there|can\s+you\s+hear\s+me|"
        r"you\s+there|is\s+(?:this|anyone)\s+(?:on|there))\s*\??$",
        re.IGNORECASE,
    )

    # --- Typed answer schemas (2026-09-25) ------------------------------
    # Turn 6: "I am the network protocols right now" closed a clinical
    # question. Each question type now needs evidence of an answer; a reject
    # still feeds record_answer()'s stall-escape, so a question is re-asked
    # at most STALL_LIMIT times - never forever.
    #
    # q01/q02/q04 are owned by typed detectors (agent._pending_answer_matches).
    _TYPED_DETECTOR_IDS = frozenset({"q01", "q02", "q04"})
    # BP and weight: need a number or an explicit "not measured / not known".
    # (q03 dates and q26 lab reports arrive in too many shapes - "since
    # Monday", "report not come yet" - and stay on the general rule.)
    _QUANTITATIVE_IDS = frozenset({"q06", "q07"})

    # Indic address/filler words - stripped only AFTER polarity is checked,
    # because "ಇಲ್ಲ ಅಕ್ಕ" (no, sister) is the commonest real answer there is.
    _INDIC_FILLER_WORDS = frozenset({
        "ಅಕ್ಕ", "ಅಕ್ಕಾ", "ಅಂದರೆ", "ಅದೇ", "ಹ್ಮ್", "ಹೂಂ", "ಹೌದು", "ಇಲ್ಲ",
        "ನಮಸ್ಕಾರ", "ನಮಸ್ತೆ", "ಹಲೋ", "नमस्ते", "नमस्कार", "दीदी", "जी",
        # Bare "ಎಲ್ಲಾ" (all) is how STT has written a spoken "ಇಲ್ಲ" (no) - live
        # call 2026-09-28, closing two danger-screening questions (q10, q18)
        # on an answer that means nothing. Alone it is re-asked; "ಎಲ್ಲಾ ಸರಿ"
        # (all fine) still answers through the polarity check.
        "ಎಲ್ಲಾ", "ಎಲ್ಲ",
    })
    # Words, not \w runs: \w stops at Kannada/Devanagari vowel signs, so
    # "ಅಕ್ಕ" came out as fragments and never matched a filler.
    _WORD_RE = re.compile(r"[\w'ಀ-೿ऀ-ॿ]+", re.UNICODE)

    # Yes/no evidence: the words, and Kannada/Hindi finite-verb endings
    # ("ತಿನ್ನುತ್ತಿದ್ದಾಳೆ", "ಮಾಡಿಸಿದ್ದಾರೆ", "ಆಗಿಲ್ಲ", "हुआ है").
    _POLARITY_RE = re.compile(
        r"ಹೌದು|ಇಲ್ಲ|ಹೂಂ|ಹಾಂ|ಹೂ(?![ಀ-೿])|ಇದೆ|ಇತ್ತು|ಆಗಿದೆ|ಆಗಿತ್ತು|ಆಯ್ತು|ಆಯಿತು|ಸರಿ"
        r"|(?:ದ್ದಾರೆ|ದ್ದಾಳೆ|ದ್ದೇನೆ|ತ್ತಾರೆ|ತ್ತಾಳೆ|ತ್ತಿದೆ|ತಿದ್ದಾಳೆ|ತಿದ್ದಾರೆ|ಿಲ್ಲ|ಿದೆ|ಿತ್ತು|ಾಳೆ|ಾರೆ|ತು)(?![ಀ-೿])"
        r"|हाँ|हां|जी|नहीं|नही|है|हैं|था|थी|हुआ|हुई|चुकी|चुका"
        r"|(?<![a-z])(?:yes|no|not|done|normal|never)(?![a-z])",
        re.IGNORECASE,
    )
    _NUMBER_RE = re.compile(
        r"\d"
        r"|(?<![ಀ-೿])(?:ಒಂದು|ಎರಡು|ಮೂರು|ನಾಲ್ಕು|ಐದು|ಆರು|ಏಳು|ಎಂಟು|ಒಂಬತ್ತು|ಹತ್ತ|ಇಪ್ಪತ್"
        # tens as stems: spoken "ಐವತ್ನಾಲ್ಕು" (54) drops the ತ್ತ of "ಐವತ್ತು"
        r"|ಮೂವತ್|ನಲವತ್|ನಲ್ವತ್|ಐವತ್|ಅರವತ್|ಎಪ್ಪತ್|ಎಂಬತ್|ತೊಂಬತ್|ನೂರ|ಇನ್ನೂರ)"
        # a unit is itself a reading: "ಐವತ್ನಾಲ್ಕು ಕೆಜಿ", "ಸುಮಾರು ಕಿಲೋ"
        r"|ಕೆಜಿ|ಕಿಲೋ|किलो|केजी|(?<![a-z])kgs?(?![a-z])"
        r"|ಜನವರಿ|ಫೆಬ್ರವರಿ|ಮಾರ್ಚ್|ಏಪ್ರಿಲ್|ಜೂನ್|ಜುಲೈ|ಆಗಸ್ಟ್|ಸೆಪ್ಟೆಂಬರ್|ಅಕ್ಟೋಬರ್|ನವೆಂಬರ್|ಡಿಸೆಂಬರ್|ತಾರೀಖು"
        r"|(?<![ऀ-ॿ])(?:एक|दो|तीन|चार|पांच|पाँच|छह|सात|आठ|नौ|दस|बीस|तीस|चालीस|पचास|साठ|सत्तर|अस्सी|नब्बे|सौ)(?![ऀ-ॿ])"
        r"|(?<![a-z])(?:one|two|three|four|five|six|seven|eight|nine|ten|twenty|thirty|forty|fifty|hundred"
        r"|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)",
        re.IGNORECASE,
    )
    # "BP not taken", "report not come yet", "don't know" are complete answers.
    _NOT_AVAILABLE_RE = re.compile(
        r"ಗೊತ್ತಿಲ್ಲ|ಮಾಡಿಲ್ಲ|ಮಾಡಿಸಿಲ್ಲ|ಬಂದಿಲ್ಲ|ಆಗಿಲ್ಲ|ನೋಡಿಲ್ಲ|ಇನ್ನೂ"
        r"|पता नहीं|नहीं पता|नहीं हुआ|नहीं किया|अभी नहीं|नहीं आई|नहीं आया"
        r"|not (?:done|taken|known|available|yet)|don'?t know|unknown",
        re.IGNORECASE,
    )
    # Clinical English that may legitimately arrive in Latin script.
    _ENGLISH_DOMAIN_RE = re.compile(
        r"(?<![a-z])(?:yes|no|not|none|normal|done|never|bp|pressure|sugar|diabetes|thyroid|fits|"
        r"scan|report|tablets?|iron|calcium|folic|td|tt|injection|dose|vaccine|bleeding|pain|"
        r"headache|fever|swelling|vomiting|baby|movement|weeks?|months?|kg|hb|hospital|"
        r"doctor|nurse|ivf|natural|ambulance|108|allergy|medicine|since|ago|last|"
        r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
        # romanised Kannada/Hindi, as a transliterating STT writes it
        r"houdu|haudu|illa|illa|aayitu|aaytu|aagide|ide|beku|herige|garbha|tingalu|"
        r"vara|maatre|gulige|novu|jwara|haan|nahi|nahin|hai|bacha|dard|bukhar)(?![a-z])",
        re.IGNORECASE,
    )

    def _is_assent(self, text: str) -> bool:
        """One-word yes/no, in any of the three call languages."""
        return is_assent(text)

    def _echoes_question(self, text: str, question_id: str | None) -> bool:
        """The answer reuses a word of the question ("ತಾಲೂಕು ಆಸ್ಪತ್ರೆಯಲ್ಲಿ" to
        "scan at BRIMS or taluk hospital?", "ನೈಸರ್ಗಿಕ" to "natural or IVF?")."""
        question = QUESTIONS_BY_ID.get(question_id or "")
        if question is None:
            return False
        stems = {w[:5] for w in self._WORD_RE.findall(question.text) if len(w) >= 4}
        return any(w[:5] in stems for w in self._WORD_RE.findall(text) if len(w) >= 4)

    def _mentions_symptom(self, text: str) -> bool:
        from sakhi_domain import detect_symptoms   # sakhi_domain imports this module
        try:
            return detect_symptoms(text).any_present
        except Exception:
            return False

    def validate_answer(self, text: str, *, vad: str = "SLOW", question_id: str | None = None) -> tuple[bool, str]:
        """Does this turn actually answer the question that was asked?

        Returns (is_valid: bool, reason: str). Deliberately permissive - a wrong
        "yes" is recoverable, a question the worker never gets asked is not. It only
        rejects turns that carry no information for the kind of question that was pending.
        """
        stripped = text.strip().strip(".,!?ಣ।")
        if not stripped:
            return False, "empty_or_whitespace"
        # A bare reading ("140", "58") is a complete answer, however short.
        if question_id is not None and re.fullmatch(r"\d{2,3}(?:\s*[/ ]\s*\d{2,3})?", stripped):
            return True, "valid_number"
        # A FAST yes/no question: one syllable answers it. Not a value one.
        if self._is_assent(stripped):
            if question_id in VALUE_QUESTION_IDS:
                return False, "assent_to_value_question"
            if vad.upper() == "FAST":
                return True, "valid_assent"
        if len(stripped) < self.MIN_ANSWER_CHARS:
            return False, f"too_short (length {len(stripped)} < {self.MIN_ANSWER_CHARS})"
        if stripped.casefold() in self._NON_ANSWERS:
            return False, f"non_answer_phrase ({stripped.casefold()!r})"
        if self._PRESENCE_CHECK_RE.match(stripped):
            return False, "presence_check_phrase"

        typed = question_id is not None and question_id not in self._TYPED_DETECTOR_IDS
        has_indic = bool(re.search(r"[ಀ-೿ऀ-ॿ]", stripped))
        # Out of domain: Latin-only text with no number and no clinical word
        # on a Kannada/Hindi call ("I am the network protocols right now").
        if typed and not has_indic and not re.search(r"\d", stripped) \
                and not self._ENGLISH_DOMAIN_RE.search(stripped):
            return False, "out_of_domain"

        if typed and question_id in self._QUANTITATIVE_IDS:
            if self._NUMBER_RE.search(stripped) or self._NOT_AVAILABLE_RE.search(stripped):
                return True, "valid_quantity"
            return False, "quantitative_without_value"

        # Direct evidence of an answer: yes/no, a number, a symptom, or the
        # question's own words ("ತಾಲೂಕು ಆಸ್ಪತ್ರೆಯಲ್ಲಿ" to the scan question).
        # Checked BEFORE filler stripping - "ಇಲ್ಲ ಅಕ್ಕ" is all "filler" words.
        if typed and (self._POLARITY_RE.search(stripped) or re.search(r"\d", stripped)
                      or self._mentions_symptom(stripped)
                      or self._echoes_question(stripped, question_id)):
            return True, "valid_answer_evidence"

        # Strip filler words and require at least one real word of its own -
        # see _FILLER_WORDS above for why a whole-phrase blocklist alone
        # isn't enough. Words rather than characters, so a real short answer
        # ("sixth month") isn't penalised for being brief.
        words = self._WORD_RE.findall(stripped)
        fillers = self._FILLER_WORDS | self._INDIC_FILLER_WORDS if typed else self._FILLER_WORDS
        content_words = [w for w in words if w.casefold() not in fillers]
        if not content_words:
            return False, "only_filler_words"
        return True, "valid_content"

    def _is_substantive(self, text: str, *, vad: str = "SLOW", question_id: str | None = None) -> bool:
        """Does this turn actually answer the question that was asked?"""
        valid, _ = self.validate_answer(text, vad=vad, question_id=question_id)
        return valid

    def record_answer(self, text: str) -> str | None:
        """Attribute this user turn to the question that was pending.

        Returns the question id that was closed, or None. Only the PENDING
        question can be closed, so an unrelated remark cannot silently tick off
        a question that was never asked - and only a turn carrying actual
        content closes anything, so a misheard fragment no longer advances the
        clinical intake past a question the worker never answered.

        STALL-ESCAPE (2026-09-10): if this is the third or later consecutive
        non-empty reply that still fails the substantive check for the SAME
        pending question, it closes anyway, best-effort, rather than leaving
        the question armed indefinitely - see STALL_LIMIT above.
        """
        text = (text or "").strip()
        if not text or self.pending_id is None:
            return None
        pending_id = self.pending_id
        pending = QUESTIONS_BY_ID.get(pending_id)
        if self._is_substantive(text, vad=pending.vad if pending else "SLOW", question_id=pending_id):
            self._stall_counts.pop(pending_id, None)
            closed = pending_id
            self.answered[closed] = text[:120]
            self.pending_id = None
            return closed

        # Not substantive by the normal rule. Count it toward the stall
        # ceiling for THIS question only - a real turn was heard, it just
        # didn't look like an answer, which is exactly the "unclear STT"
        # case the escape exists for.
        stalls = self._stall_counts.get(pending_id, 0) + 1
        self._stall_counts[pending_id] = stalls
        if stalls > self.STALL_LIMIT:
            closed = pending_id
            self.answered[closed] = (text[:110] + " (unclear, best-effort)")
            self.pending_id = None
            self._stall_counts.pop(pending_id, None)
            return closed
        return None

    # --- zero-TTS slot filling (2026-11-01) -----------------------------

    def _absorb(self, question_id: str, extracted: Dict[str, Any], text: str) -> None:
        """Merge one answer's slots into the call's slots (omnidirectional:
        "ಏಳು ತಿಂಗಳು" said to the name question fills q02). The question just
        asked may overwrite its own values; anything else only fills a blank.
        A question's own yes/no is keyed by its id and filled only when it
        was the one asked, so a danger question is always asked, never
        skipped."""
        from sakhi_slot_engine import QUESTION_CONFIGS

        config = QUESTION_CONFIGS.get(question_id)
        owned = {r.key for r in config.slots} if config else set()
        sys_, dia = extracted.get("systolic"), extracted.get("diastolic")
        if sys_ is not None and dia is not None and ("systolic" in owned or "systolic" not in self.slot_values):
            # A reading is taken whole: never one half from each of two answers.
            self.slot_values["systolic"], self.slot_values["diastolic"] = sys_, dia
        single = extracted.get("bp_single")
        if single is not None and "systolic" in owned:
            known = self.slot_values.get("systolic")
            if known is not None and "diastolic" not in self.slot_values and single < known:
                self.slot_values["diastolic"] = single     # answer to "and the lower number?"
            elif single >= 70:
                self.slot_values["systolic"] = single
                self.slot_values.pop("diastolic", None)
        for key in ("name", "age", "gestational_weeks", "weight_kg"):
            if key in extracted and (key in owned or key not in self.slot_values):
                self.slot_values[key] = extracted[key]
        if question_id in owned and question_id not in self.slot_values                 and self._answers_itself(question_id, extracted, text):
            self.slot_values[question_id] = extracted.get("polarity", text.strip()[:120])

    def _answers_itself(self, question_id: str, extracted: Dict[str, Any], text: str) -> bool:
        """Does this turn fill a one-slot question's own answer slot?"""
        question = QUESTIONS_BY_ID.get(question_id)
        if question is None or not text.strip():
            return False
        valid, _ = self.validate_answer(text, vad=question.vad, question_id=question_id)
        if question_id in VALUE_QUESTION_IDS:
            return valid
        return "polarity" in extracted or valid

    def _slot_summary(self, question_id: str, fallback: str) -> str:
        v = self.slot_values
        if question_id == "q01":
            return f"{v.get('name')}, {v.get('age')}"
        if question_id == "q02":
            return f"~{v.get('gestational_weeks')} weeks"
        if question_id == "q06":
            return f"{v.get('systolic')}/{v.get('diastolic')}"
        if question_id == "q07":
            return f"{v.get('weight_kg')} kg"
        own = v.get(question_id)
        if isinstance(own, bool):
            return "yes" if own else "no"
        return str(own if own is not None else fallback)[:120]

    def ingest_slots(
        self, question_id: str, extracted_slots: Dict[str, Any], *, text: str = "", language: str = "kn",
    ) -> Tuple[bool, str]:
        """One answer in, the next prompt out. No model, no network.

        Returns (True, next question's main WAV) when `question_id` is now
        complete, or (False, the micro-prompt WAV for its first missing slot)
        - "and her age?", not the whole question again. Later questions this
        answer (or any earlier one) already filled are closed and skipped.
        After STALL_LIMIT re-asks the question closes best-effort, the same
        ceiling record_answer() applies. (True, "") means the intake is done.
        """
        from sakhi_slot_engine import QUESTION_CONFIGS, localized

        self._absorb(question_id, extracted_slots, text)
        config = QUESTION_CONFIGS.get(question_id)
        missing = [r for r in config.slots if r.key not in self.slot_values] if config else []
        if missing and question_id not in self.answered:
            stalls = self._stall_counts.get(question_id, 0) + 1
            self._stall_counts[question_id] = stalls
            if stalls <= self.STALL_LIMIT:
                self.pending_id = question_id
                return False, localized(missing[0].prompt_wav, language)
            self.answered[question_id] = f"{(text or '')[:110]} (unclear, best-effort)"
        elif question_id not in self.answered:
            self.answered[question_id] = self._slot_summary(question_id, text)
        self._stall_counts.pop(question_id, None)
        self.pending_id = None
        return True, self._advance(language)

    def _advance(self, language: str) -> str:
        """Arm the next question that still needs asking; its WAV, or "" when done."""
        from sakhi_slot_engine import QUESTION_CONFIGS, localized

        while (nxt := self.next_question()) is not None:
            config = QUESTION_CONFIGS[nxt.id]
            if nxt.id == "q17":
                # The closing line is said, not asked: nothing to wait for.
                self.mark_satisfied("q17", "(closing statement spoken)")
                return localized(config.main_wav, language)
            if nxt.applicable_if is not None and not nxt.applicable_if(
                    _SlotView(self.slot_values.get("gestational_weeks"))):
                self.mark_satisfied(nxt.id, "(not applicable)")
                continue
            if all(r.key in self.slot_values for r in config.slots):
                self.mark_satisfied(nxt.id, f"{self._slot_summary(nxt.id, '')} (from earlier answer)")
                continue
            self.pending_id = nxt.id
            return localized(config.main_wav, language)
        return ""

    # --- queries -------------------------------------------------------

    def arm_next(self) -> IntakeQuestion | None:
        """Mark the next question as the one being asked THIS turn.

        Called at injection time so the ASHA worker's reply can be attributed
        to it. Without arming, `record_answer` has nothing to close and the
        checklist never advances.
        """
        nxt = self.next_question()
        if nxt is not None and nxt.id != self.pending_id:
            # A different question just became pending - the stall count on
            # whatever was pending before (if anything) does not carry over.
            self._stall_counts.pop(self.pending_id or "", None)
        self.pending_id = nxt.id if nxt else None
        return nxt

    def next_question(self) -> IntakeQuestion | None:
        for question in INTAKE_QUESTIONS:
            if question.id not in self.answered:
                return question
        return None

    @property
    def complete(self) -> bool:
        return self.next_question() is None

    # --- 7-stage protocol position -------------------------------------

    def current_stage(self) -> int:
        """Which of the 7 ANC stages the call is standing in right now.

        Derived from the next unanswered question rather than stored: a
        separate stage counter would be a second source of truth that can
        drift out of step with `answered` (the exact failure this module was
        written to remove for questions). Returns len(ANC_STAGES) once every
        question is done, since the call is then in the closing stage.
        """
        nxt = self.next_question()
        return nxt.stage if nxt else len(ANC_STAGES)

    def stage_progress(self) -> tuple[int, int]:
        """(answered, total) within the CURRENT stage only."""
        stage = self.current_stage()
        in_stage = [q for q in INTAKE_QUESTIONS if q.stage == stage]
        done = sum(1 for q in in_stage if q.id in self.answered)
        return done, len(in_stage)

    def stage_complete(self, stage: int) -> bool:
        """Has every question in this stage been answered or auto-satisfied?"""
        return all(
            q.id in self.answered
            for q in INTAKE_QUESTIONS
            if q.stage == stage
        )

    def summary(self) -> str:
        """The block injected into every generation.

        Deliberately compact - it is re-sent on every turn, so each word is
        prefill latency. It carries only what the model cannot reconstruct:
        how far along we are, and the EXACT next question. The full inventory
        is already in the system prompt; repeating it here would double the
        per-turn cost to say nothing new.
        """
        done = len(self.answered)
        total = len(INTAKE_QUESTIONS)
        nxt = self.next_question()
        if nxt is None:
            return (
                f"intake={done}/{total} COMPLETE - Stage 7 reached. Ask no further "
                "intake questions; confirm what was recorded in one short sentence "
                "and close the call warmly."
            )
        stage_done, stage_total = self.stage_progress()
        return (
            f"intake={done}/{total} done | {stage_name(nxt.stage)} "
            f"({stage_done}/{stage_total} in this stage). "
            f"NEXT QUESTION (ask THIS one, verbatim in meaning, and nothing else): "
            f'"{nxt.text}" [VAD: {nxt.vad}]'
        )
