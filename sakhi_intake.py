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
from typing import Callable


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
    lang = str(language or "").strip().lower()
    if lang in ("hi", "hindi", "hin") and question.text_hi:
        return question.text_hi
    return question.text


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


# Declared in the order each stage's content reads most naturally; the actual
# call/prompt order is never taken from this declaration position - see
# INTAKE_QUESTIONS below, which sorts by `stage` so a question can be added
# near the content it belongs with without having to also relocate it inside
# the tuple by hand. New IDs (q18 and up) were chosen so no existing test or
# frontend reference to an existing id needed to change.
_RAW_QUESTIONS: tuple[IntakeQuestion, ...] = (
    # --- Stage 1: Basic Intake (North Kannada vernacular) ----------------
    # Name and age ONLY. No Thayi card number, no RCH ID, no Aadhaar, no
    # identity document of any kind: an ASHA worker standing in a doorway
    # does not have the card in her hand, and gating an ANC screening on a
    # number she has to go and find is how a call gets abandoned before the
    # BP is ever taken.
    IntakeQuestion("q01", SECTION_1,
        "ತಾಯಿಯ ಹೆಸರು ಮತ್ತು ವಯಸ್ಸು ಹೇಳಿ ಅಕ್ಕಾ", "SLOW", "identity", stage=1,
        text_hi="माता का नाम और उम्र बताइए दीदी"),

    # --- Stage 2: Obstetric History & Gestation (Bidari dialect) ----------
    IntakeQuestion("q02", SECTION_2,
        "ಈಗ ಯಾವಟು ತಿಂಗಳು ಗರ್ಭವಿದೆ?", "FAST", "gestational_weeks", stage=2,
        text_hi="अभी कितने महीने की गर्भवती है?"),
    IntakeQuestion("q03", SECTION_2,
        "ಕೊನೆಯ ಮತ್ತಿನ ದಿನ ಯಾವಾಗ?", "FAST", stage=2,
        text_hi="आखिरी माहवारी की तारीख कब थी?"),
    # REVISED (2026-09-10): the source PDF's header field is "Gravida/Para"
    # (times pregnant AND times delivered) as one combined item, not
    # pregnancy-count alone. Text updated to ask both in one question rather
    # than adding a second id for "para" - matches the form's own bundling
    # and keeps the questionnaire from growing by one more question for a
    # fact that's naturally answered in the same breath.
    #
    # Earlier DRAFT note (2026-09-09) on this id, kept for history: the
    # source text here had literal Latin "ide" where a Kannada word should be
    # ("ಗರ್ಭವide?") - not a rendering issue, confirmed byte-for-byte. That
    # reconstruction is superseded by this revision, not layered under it.
    IntakeQuestion("q04", SECTION_2,
        "ಮುಂಚೆ ಎಷ್ಟು ಸಲ ಗರ್ಭ ಆಗಿತ್ತು, ಎಷ್ಟು ಹೆರಿಗೆ ಆಗಿದೆ?", "FAST", stage=2,
        text_hi="पहले कितनी बार गर्भवती हुई थीं, कितनी बार प्रसव हुआ?"),
    # REVISED (2026-09-10): broadened from "prior C-section only" to also
    # cover "or another major pregnancy complication", matching the source
    # PDF's own header field ("Previous cesarean / major pregnancy
    # complication: Yes/No") rather than just the surgical-delivery half of it.
    IntakeQuestion("q05", SECTION_2,
        "ಮುಂಚೆ ಪ್ರಸವ ಆಪರೇಷನ್ ಆಗಿದೆಯ ಅಥವಾ ಬೇರೆ ಗಂಭೀರ ಸಮಸ್ಯೆ ಆಗಿತ್ತಾ?", "FAST", stage=2,
        text_hi="पहले ऑपरेशन से प्रसव हुआ था, या कोई और गंभीर समस्या हुई थी?"),
    # NEW (2026-09-10): the PDF's header also asks conception mode
    # (spontaneous / IVF) - a quick FAST yes/no, not gated on anything.
    IntakeQuestion("q21", SECTION_2,
        "ಗರ್ಭ ಸಹಜವಾಗಿ ಆಯ್ತೋ ಅಥವಾ ಟ್ರೀಟ್ಮೆಂಟ್ (ಐ.ವಿ.ಎಫ್) ತಗೊಂಡಿದ್ಯಾ?", "FAST", stage=2,
        text_hi="गर्भ सामान्य रूप से ठहरा या इलाज (आई.वी.एफ) से?"),

    # --- Stage 3: Vitals & Nutrition (Field-friendly phrasing) -----------
    IntakeQuestion("q06", SECTION_VITALS,
        "ರಕ್ತದೊತ್ತಡ ಎಷ್ಟು?", "FAST", "blood_pressure", stage=3,
        text_hi="रक्तचाप कितना है?"),
    IntakeQuestion("q07", SECTION_VITALS,
        "ಈಗ ತಾಯಿಯ ತೂಕ ಎಷ್ಟು?", "FAST", stage=3,
        text_hi="अभी माता का वजन कितना है?"),
    # DRAFT (2026-09-09): the source text had literal CYRILLIC characters
    # spliced into a Kannada word ("ತказыва") - confirmed byte-for-byte, not
    # a display artifact. Reconstructed to "ಕಬ್ಬಿಣಾಂಶ ಗುಳಿಗೆ ತಪ್ಪದೆ
    # ತಿನ್ನುತ್ತಿದ್ದಾಳಾ?" (is she taking the iron tablets without fail) -
    # needs native-speaker/clinician confirmation before treating as final.
    IntakeQuestion("q08", SECTION_VITALS,
        "ಕಬ್ಬಿಣಾಂಶ ಗುಳಿಗೆ ತಪ್ಪದೆ ತಿನ್ನುತ್ತಿದ್ದಾಳಾ?", "FAST", stage=3,
        text_hi="आयरन की गोली रोज बिना चूके खा रही है क्या?"),
    # NEW (2026-09-10): PDF CHECK item - pallor / edema. A provider-observed
    # sign, not a mother-reported danger symptom, so it is recorded via
    # record_anc_checkpoint (parameter="pallor_edema"), not a SymptomSet
    # field - see sakhi_tools.py.
    IntakeQuestion("q24", SECTION_VITALS,
        "ಕಣ್ಣು ಅಥವಾ ಮೈ ಬಿಳಿಚಿಕೊಂಡಂಗ ಕಾಣ್ತಿದ್ಯಾ, ಅಥವಾ ಕಾಲು ಊತ ಇದ್ಯಾ?", "FAST", stage=3,
        text_hi="आँख या शरीर पीला दिख रहा है, या पैर में सूजन है क्या?"),
    # NEW (2026-09-10): PDF CHECK item - fetal heart rate. Conditionally
    # applicable only from 20 weeks (_fhr_applicable) - the one question in
    # this rewrite that actually uses the new applicable_if mechanism.
    # Recorded via record_anc_checkpoint(parameter="fetal_heart_rate"), not a
    # dedicated tool - this is a recorded fact the triage engine does not
    # branch on.
    IntakeQuestion("q25", SECTION_VITALS,
        "ಕುಸುವಿನ ಎದೆಬಡಿತ ಪರೀಕ್ಷೆ ಮಾಡಿದ್ಯಾ, ಎಷ್ಟಿತ್ತು?", "FAST", stage=3,
        text_hi="बच्चे की धड़कन जांची क्या, कितनी थी?",
        applicable_if=_fhr_applicable),

    # --- Stage 4: High-Risk Red-Flag Screening (Pre-eclampsia focus) ------
    # These mirror the source PDF's own ASK section, in its order: bleeding/
    # leaking/pain/contractions; fetal movement; headache/vision/swelling/
    # upper-abdominal-pain; fever/urinary/breathless/palpitations/vomiting;
    # chronic disease history; medications/allergies. Convulsions (q11) is
    # kept even though the PDF's ASK section doesn't list it directly (its
    # own RED FLAGS list does) - dropping an existing, safety-critical Tier-1
    # screening question because the source form structures it differently
    # would be a regression, not fidelity to the form.
    #
    # REVISED (2026-09-10): was headache/blurred-vision only. Swelling was
    # previously only ever picked up by passive deterministic detection if
    # mentioned unprompted - never actually asked. Upper abdominal pain is
    # new (see sakhi_domain.SymptomSet.upper_abdominal_pain) - clinically
    # distinct from labor-type abdominal pain (q10), it is the epigastric-
    # pain signal associated with pre-eclampsia/HELLP.
    IntakeQuestion("q09", SECTION_REDFLAG,
        "ತಲೆನೋವು, ಕಣ್ಣು ಮಂಜು, ಊತ ಅಥವಾ ಹೊಟ್ಟೆಯ ಮೇಲ್ಭಾಗದಲ್ಲಿ ನೋವು ಇದೆಯ?",
        "FAST", "symptoms_screened", stage=4,
        text_hi="सिरदर्द, धुंधला दिखना, सूजन, या पेट के ऊपरी हिस्से में दर्द है क्या?"),
    # REVISED (2026-09-10): was bleeding/leaking-fluid only. Contractions
    # added (sakhi_domain.SymptomSet.contractions) and abdominal pain named
    # explicitly, matching the PDF's first ASK item exactly.
    IntakeQuestion("q10", SECTION_REDFLAG,
        "ರಕ್ತಸ್ರಾವ, ನೀರು ಹೋಗೋದು, ಹೊಟ್ಟೆ ನೋವು ಅಥವಾ ಸಂಕೋಚನ ಆಗ್ತಿದೆಯ?", "FAST", stage=4,
        text_hi="खून बहना, पानी जाना, पेट दर्द, या संकुचन हो रहा है क्या?"),
    IntakeQuestion("q11", SECTION_REDFLAG,
        "ಫಿಟ್ಸ್ ಅಥವಾ ಸೆಳೆತ ಆಗಿದೆಯ?", "FAST", stage=4,
        text_hi="दौरा या झटके आए हैं क्या?"),
    # REVISED (2026-09-10): rephrased to the PDF's direct "are movements
    # normal?" framing. Same field (reduced_fetal_movement); self-negating,
    # unchanged detection logic.
    IntakeQuestion("q12", SECTION_REDFLAG,
        "ಕುಸು ಆಟ ಸರಿಯಾಗಿ ಆಡ್ತಿದ್ಯಾ?", "FAST", stage=4,
        text_hi="बच्चे की हलचल ठीक से हो रही है क्या?"),
    # NEW (2026-09-10): bundles the PDF's 4th ASK item. Five new SymptomSet
    # fields - see sakhi_domain.py and sakhi_triage.py for how fever and
    # breathlessness (Tier-1 per the user's explicit choice) differ from the
    # other three (which contribute to the existing "symptomatic" bucket
    # only, same treatment as headache/blurry_vision today).
    IntakeQuestion("q18", SECTION_REDFLAG,
        "ಜ್ವರ, ಮೂತ್ರದ ತೊಂದರೆ, ಉಸಿರಾಟದ ತೊಂದರೆ, ಎದೆ ಢವಢವ ಅಥವಾ ವಿಪರೀತ ವಾಂತಿ ಆಗ್ತಿದೆಯ?",
        "FAST", stage=4,
        text_hi="बुखार, पेशाब में तकलीफ, सांस लेने में तकलीफ, धड़कन तेज होना, या बहुत ज्यादा उल्टी हो रही है क्या?"),
    # NEW (2026-09-10): known chronic disease history (PDF's 5th ASK item).
    # SLOW because a real answer names which condition(s), not yes/no.
    # Recorded via record_anc_checkpoint(parameter="chronic_disease") - a
    # history fact for the record, deliberately NOT wired into automated
    # triage in this pass (a real disease-specific risk-modifier ruleset is
    # a separate, larger clinical-calibration project - see sakhi_triage.py).
    IntakeQuestion("q19", SECTION_REDFLAG,
        "ಸಕ್ಕರೆ ಕಾಯಿಲೆ, ರಕ್ತದೊತ್ತಡ, ಥೈರಾಯ್ಡ್, ಫಿಟ್ಸ್ ಕಾಯಿಲೆ, ಹೃದಯ ಅಥವಾ ಬೇರೆ ದೀರ್ಘಕಾಲದ ಕಾಯಿಲೆ ಇದೆಯ?",
        "SLOW", stage=4,
        text_hi="शुगर, रक्तचाप, थायरॉइड, मिर्गी, दिल या कोई और पुरानी बीमारी है क्या?"),
    # NEW (2026-09-10): current medications/supplements/allergies (PDF's 6th
    # ASK item). Same treatment as q19 - recorded, not triage-wired.
    IntakeQuestion("q20", SECTION_REDFLAG,
        "ಈಗ ಯಾವುದಾದ್ರೂ ಔಷಧಿ, ಪೂರಕ ಗುಳಿಗೆ ತಗೊಳ್ತಿದ್ದಾಳಾ, ಅಥವಾ ಯಾವುದಾದ್ರೂ ಔಷಧಿಗೆ ಅಲರ್ಜಿ ಇದೆಯ?",
        "SLOW", stage=4,
        text_hi="अभी कोई दवा, सप्लीमेंट ले रही है, या किसी दवा से एलर्जी है क्या?"),

    # --- Stage 5: Scans & Tetanus (Local facility references) ------------
    IntakeQuestion("q13", SECTION_SCANS,
        "ಸ್ಕ್ಯಾನ್ ಮಾಡಿದೆಯ? (ಬಿ.ಆರ್.ಆಯ.ಎಂ.ಎಸ್/ತಾಲುಕ ಆಸ್ಪತ್ರೆ)", "FAST", stage=5,
        text_hi="स्कैन करवाया है क्या? (बी.आर.आई.एम.एस/तालुक अस्पताल में)"),
    IntakeQuestion("q14", SECTION_SCANS,
        "ಎರಡು ಧನುರ್ವಾತ ಚುಚ್ಚುಮದ್ದು ತೆಗೆದೆಯ?", "FAST", stage=5,
        text_hi="धनुर्वात के दोनों इंजेक्शन लगवाए हैं क्या?"),
    # NEW (2026-09-10): PDF CHECK item - review of latest Hb/blood-group/
    # glucose/ultrasound results. SLOW and phrased so "report not available
    # yet" is a complete, valid answer, not a failure - an ASHA worker in the
    # field frequently does not have the paper report in hand.
    IntakeQuestion("q26", SECTION_SCANS,
        "ಹಿಮೋಗ್ಲೋಬಿನ್, ಬ್ಲಡ್ ಗ್ರೂಪ್, ಸಕ್ಕರೆ ಪರೀಕ್ಷೆ ವರದಿ ಕೈಲಿದ್ಯಾ? ಇದ್ರೆ ಹೇಳಿ.", "SLOW", stage=5,
        text_hi="हीमोग्लोबिन, ब्लड ग्रुप, शुगर टेस्ट की रिपोर्ट हाथ में है क्या? हो तो बताइए।"),

    # --- Stage 6: Birth Preparedness (Bidar context) ---------------------
    # DRAFT (2026-09-09): "ನರ್ಸowę?" had a literal Polish-looking suffix
    # spliced in - confirmed byte-for-byte. Reconstructed to "ಪ್ರಸವಕ್ಕೆ
    # ಆಸ್ಪತ್ರೆಯಲ್ಲಿ ಡಾಕ್ಟರ್ ನರ್ಸ್ ಇದ್ದಾರಾ?" (for the delivery, is a doctor/
    # nurse available at the hospital) - needs native-speaker/clinician
    # confirmation before treating as final.
    IntakeQuestion("q15", SECTION_BIRTHPREP,
        "ಪ್ರಸವಕ್ಕೆ ಆಸ್ಪತ್ರೆಯಲ್ಲಿ ಡಾಕ್ಟರ್ ನರ್ಸ್ ಇದ್ದಾರಾ?", "SLOW", stage=6,
        text_hi="प्रसव के लिए अस्पताल में डॉक्टर नर्स मौजूद हैं क्या?"),
    # DRAFT (2026-09-09): "ಮಾಡbildung" had literal GERMAN spliced in -
    # confirmed byte-for-byte. Reconstructed to "ನೂರನೆಂಟು (108) ಗಾಡಿ ಕರೆ
    # ಮಾಡುವ ವ್ಯವಸ್ಥೆ ಇದೆಯ?" (is there an arrangement to call the 108
    # ambulance) - needs native-speaker/clinician confirmation.
    IntakeQuestion("q16", SECTION_BIRTHPREP,
        "ನೂರನೆಂಟು (108) ಗಾಡಿ ಕರೆ ಮಾಡುವ ವ್ಯವಸ್ಥೆ ಇದೆಯ?", "FAST", stage=6,
        text_hi="एक सौ आठ (108) गाड़ी बुलाने की व्यवस्था है क्या?"),

    # --- Stage 7: Confirmation & Close -----------------------------------
    # DRAFT (2026-09-09): "nàoದ್ದು" had a literal Vietnamese word spliced
    # in - confirmed byte-for-byte. Reconstructed to "ತಾಯಿ ಬಗ್ಗೆ ಇನ್ನೇನಾದ್ರೂ
    # ಹೇಳಬೇಕಾದ್ರು?" (anything else to say about the mother) - needs native-
    # speaker/clinician confirmation before treating as final.
    IntakeQuestion("q17", SECTION_CLOSE,
        "ತಾಯಿ ಬಗ್ಗೆ ಇನ್ನೇನಾದ್ರೂ ಹೇಳಬೇಕಾದ್ರು?", "SLOW", stage=7,
        text_hi="माता के बारे में और कुछ बताना है क्या?"),
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


def render_inventory(language: str = "kannada") -> str:
    """The prompt's question list, generated from the questionnaire itself.

    The prompt used to carry its own hand-typed copy of these lines. Two
    copies of a clinical protocol drift, and the one the model reads is not
    the one the code tracks. Grouped by ANC stage (2026-09-02) so the model
    reads the protocol in the same 7-stage shape the state block reports its
    position in.

    `language` (2026-09-10) selects which per-question text column is
    rendered - see `question_text()`. Defaults to Kannada so every existing
    call site that never passed a language keeps behaving exactly as before.
    """
    lines: list[str] = []
    current_stage = None
    for index, question in enumerate(INTAKE_QUESTIONS, start=1):
        if question.stage != current_stage:
            current_stage = question.stage
            lines.append(f"## {stage_name(current_stage)}")
        text = question_text(question, language)
        lines.append(f'{index}. "{text}" [VAD: {question.vad}]')
    return "\n".join(lines)


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
        if getattr(state, "blood_pressure", None):
            self.mark_satisfied("q06", str(state.blood_pressure))
        if getattr(state, "symptoms_screened", False):
            self.mark_satisfied("q09", "screened")

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

    def _is_assent(self, text: str) -> bool:
        """One-word yes/no, in any of the three call languages."""
        return is_assent(text)

    def _is_substantive(self, text: str, *, vad: str = "SLOW") -> bool:
        """Does this turn actually answer the question that was asked?

        Deliberately permissive - a wrong "yes" is recoverable, a question the
        worker never gets asked is not. It only rejects turns that carry no
        information *for the kind of question that was pending*.
        """
        stripped = text.strip().strip(".,!?ಣ।")
        if not stripped:
            return False
        # A FAST question is a closed question: one syllable answers it.
        if vad.upper() == "FAST" and self._is_assent(stripped):
            return True
        if len(stripped) < self.MIN_ANSWER_CHARS:
            return False
        if stripped.casefold() in self._NON_ANSWERS:
            return False
        if self._PRESENCE_CHECK_RE.match(stripped):
            return False
        # Strip filler words and require at least one real word of its own -
        # see _FILLER_WORDS above for why a whole-phrase blocklist alone
        # isn't enough. Words rather than characters, so a real short answer
        # ("sixth month") isn't penalised for being brief.
        words = re.findall(r"[\w']+", stripped, flags=re.UNICODE)
        content_words = [w for w in words if w.casefold() not in self._FILLER_WORDS]
        return len(content_words) > 0

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
        if self._is_substantive(text, vad=pending.vad if pending else "SLOW"):
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
