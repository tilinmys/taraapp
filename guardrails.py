"""Deterministic safety guardrails for the MyStree voice agent.

Design principle: anything safety-critical must be deterministic, not
generated. A model that never receives a medical question cannot hallucinate
an answer to it (input gate), and a reply that is scanned before synthesis
cannot speak a drug name even if the model produced one (output gate). The
two layers fail independently - both would have to miss for medical content
to reach a caller's ears.

Deliberately dependency-free (regex only, no livekit imports) so it can be
unit-tested standalone and can never add latency beyond a few regex matches.

False-positive discipline: this clinic's LEGITIMATE flow includes symptom
words - the agent is supposed to ask "what brings you in today?" and route
"I have PCOS" to the right doctor. So the input gate triggers on
ADVICE-SEEKING patterns (what medicine / should I take / is it safe to
take / what dose), never on bare symptom mentions. "I have a fever, book me
with a doctor" must flow through untouched; "I have a fever, what medicine
should I take" must never reach the LLM.
"""

from __future__ import annotations

import dataclasses
import re

# --- Approved scripts (spoken VERBATIM - never let the LLM improvise a ------
# --- refusal; improvised refusals leak advice while refusing) ---------------

# Approved refusal scripts, one per category and language.
#
# These were English clinic-booking lines ("shall I find you a slot?", "I'm just
# the receptionist here"). Two failures at once, on the one path where failure
# matters most: they were the wrong LANGUAGE for a Kannada-speaking ASHA worker,
# and the wrong PRODUCT - they offered appointments Sakhi cannot book. A
# guardrail that fires and then says something incomprehensible has not
# protected anyone.
#
# Helpline numbers are spelled digit-by-digit in words. A voice reading "108"
# or "14416" as a cardinal number is not dictation an ASHA worker can act on.
_SCRIPTS_BY_LANG = {
    "medical_advice": {
        "kn": "ಕ್ಷಮಿಸಿ ಅಕ್ಕಾರ... ನಾನು ಔಷಧಿಗಳ ಬಗ್ಗೆ ಸಲಹೆ ನೀಡಲು ಸಾಧ್ಯವಿಲ್ಲ... "
              "ದಯವಿಟ್ಟು ತಕ್ಷಣ ವೈದ್ಯರನ್ನು ಅಥವಾ ಪಿ.ಎಚ್.ಸಿ ಯನ್ನು ಸಂಪರ್ಕಿಸಿ.",
        "hi": "माफ़ कीजिए दीदी... मैं दवा के बारे में सलाह नहीं दे सकती... "
              "कृपया तुरंत डॉक्टर या पी.एच.सी से संपर्क करें।",
        "en": "I am not able to advise on medicines... please contact the "
              "doctor or the PHC right away.",
    },
    "emergency": {
        "kn": "ಇದು ತುರ್ತು ಪರಿಸ್ಥಿತಿ ಅಕ್ಕಾರ... ತಕ್ಷಣ ತಾಯಿಯನ್ನು ಹತ್ತಿರದ ಆಸ್ಪತ್ರೆಗೆ "
              "ಕರೆದೊಯ್ಯಿರಿ... ಅಥವಾ ಒಂದು ಸೊನ್ನೆ ಎಂಟು ಗೆ ಕರೆ ಮಾಡಿ.",
        "hi": "यह आपात स्थिति है दीदी... माँ को तुरंत नज़दीकी अस्पताल ले जाइए... "
              "या एक शून्य आठ पर कॉल कीजिए।",
        "en": "This is an emergency... take the mother to the nearest hospital "
              "now, or call one zero eight.",
    },
    "self_harm": {
        "kn": "ನೀವು ಹೇಳಿದ್ದಕ್ಕೆ ಧನ್ಯವಾದ ಅಕ್ಕಾರ... ದಯವಿಟ್ಟು ಈಗಲೇ ಟೆಲಿ ಮನಸ್ ಸಹಾಯವಾಣಿಗೆ "
              "ಕರೆ ಮಾಡಿ... ಒಂದು ನಾಲ್ಕು ನಾಲ್ಕು ಒಂದು ಆರು... ಇದು ಉಚಿತ ಮತ್ತು ಯಾವಾಗಲೂ ಲಭ್ಯ.",
        "hi": "बताने के लिए धन्यवाद दीदी... कृपया अभी टेली मानस हेल्पलाइन पर कॉल करें... "
              "एक चार चार एक छह... यह निःशुल्क है और हमेशा उपलब्ध है।",
        "en": "Thank you for telling me... please call the Tele-MANAS helpline "
              "now on one four four one six. It is free and always open.",
    },
    # These two are statements on purpose (2026-09-24). They used to end in
    # their own question ("shall we talk about her blood pressure?"), which
    # jumped the checklist; agent.py now follows them with the armed intake
    # question, word for word.
    "jailbreak": {
        "kn": "ನಾನು ಸಖಿ, ತಾಯಿಯ ಆರೋಗ್ಯಕ್ಕಾಗಿ ಇದ್ದೇನೆ.",
        "hi": "मैं सखी हूँ, माँ के स्वास्थ्य के लिए।",
        "en": "I am Sakhi, here for the mother's health.",
    },
    "abuse_warning": {
        "kn": "ನಿಮ್ಮ ಕಷ್ಟ ಅರ್ಥವಾಗುತ್ತದೆ ಅಕ್ಕಾರ. ತಾಯಿಯ ಆರೋಗ್ಯಕ್ಕಾಗಿ ಮುಂದುವರಿಸೋಣ.",
        "hi": "मैं समझती हूँ दीदी। माँ के स्वास्थ्य के लिए आगे बढ़ते हैं।",
        "en": "I understand. Let us continue with the mother's health.",
    },
    "abuse_goodbye": {
        "kn": "ಕ್ಷಮಿಸಿ, ನಾನು ಈಗ ಕರೆಯನ್ನು ಮುಗಿಸಬೇಕು... ಯಾವಾಗ ಬೇಕಾದರೂ ಮತ್ತೆ ಕರೆ ಮಾಡಿ. ನಮಸ್ಕಾರ.",
        "hi": "माफ़ कीजिए, मुझे यह कॉल समाप्त करनी होगी... कभी भी दोबारा कॉल करें। नमस्ते।",
        "en": "I have to end this call now. Please call back anytime.",
    },
}


def script_for(category: str, language: str = "kn") -> str:
    """Approved script for a category in the caller's language.

    Falls back to Kannada (the pilot language) rather than English, and never
    raises - a guardrail that throws is worse than one that speaks the wrong
    dialect.
    """
    variants = _SCRIPTS_BY_LANG.get(category)
    if not variants:
        return ""
    lang = str(getattr(language, "value", language) or "kn").split("-")[0].lower()
    return variants.get(lang) or variants["kn"]


# Module-level names kept for the existing call sites and tests. Kannada, since
# that is what the Bidar pilot speaks.
MEDICAL_REFUSAL_SCRIPT = script_for("medical_advice")
EMERGENCY_SCRIPT = script_for("emergency")
SELF_HARM_SCRIPT = script_for("self_harm")
JAILBREAK_SCRIPT = script_for("jailbreak")
ABUSE_WARNING_SCRIPT = script_for("abuse_warning")
ABUSE_GOODBYE_SCRIPT = script_for("abuse_goodbye")

# --- Input gate patterns ------------------------------------------------------

# Emergencies outrank everything - flat refusal to a bleeding caller is a
# worse failure than the paracetamol case. Never triage severity; escalate.
_EMERGENCY_RE = re.compile(
    r"bleeding\s+(?:a\s+lot|heavily|badly|so\s+much)|heavy\s+bleeding"
    r"|severe\s+(?:pain|bleeding|cramp)|unbearable\s+pain|worst\s+pain"
    r"|collaps(?:ed|ing)|unconscious|fainted|fainting|passed\s+out"
    r"|chest\s+pain|can'?t\s+breathe|cannot\s+breathe|difficulty\s+breathing|not\s+breathing"
    r"|water\s+(?:has\s+|just\s+)?broke|labou?r\s+pain|having\s+(?:a\s+)?seizure",
    re.IGNORECASE,
)

_SELF_HARM_RE = re.compile(
    r"suicid|kill\s+myself|end\s+my\s+life|hurt\s+myself|harm\s+myself"
    r"|self[\s-]?harm|don'?t\s+want\s+to\s+live|no\s+reason\s+to\s+live"
    r"|want\s+to\s+die",
    re.IGNORECASE,
)

_JAILBREAK_RE = re.compile(
    r"ignore\s+(?:all\s+|your\s+|previous\s+|prior\s+|the\s+)*(?:instructions?|prompts?|rules?)"
    r"|system\s+prompt|your\s+instructions?\b|forget\s+(?:your|all|the)\s+(?:instructions?|rules?)"
    r"|pretend\s+(?:you'?re|you\s+are|to\s+be)|act\s+as\s+(?:a|an|the)\s+(?:doctor|nurse|physician|pharmacist)"
    r"|you\s+are\s+now\s+(?:a|an)\b|repeat\s+after\s+me|role[\s-]?play",
    re.IGNORECASE,
)

# Common Indian OTC/prescription drug names heard on clinic calls. A bare
# mention alone ("I'm on metformin") is information-sharing and does NOT
# trigger - it must co-occur with an advice-seeking verb.
_DRUG_WORDS_RE = re.compile(
    r"\b(?:paracetamol|dolo|crocin|calpol|ibuprofen|brufen|combiflam|meftal"
    r"|aspirin|disprin|azithromycin|azithral|amoxicillin|augmentin|antibiotics?"
    r"|metformin|insulin|cetirizine|allegra|pantoprazole|omeprazole|antacids?"
    r"|painkillers?|folic\s+acid|iron\s+tablets?)\b",
    re.IGNORECASE,
)
# Bare "take/stop/start/skip" are NOT advice cues (2026-09-28): "I take only
# iron tablets and calcium tablets" - the answer to the medicines question -
# was refused as a medication request. Only the modal/question forms ask.
_ADVICE_VERBS_RE = re.compile(
    r"\b(?:should\s+i|can\s+i|shall\s+i|safe\s+to|how\s+many|how\s+much"
    r"|what\s+dose|dosage|suggest|recommend|prescribe"
    r"|(?:should|can|shall|may)\s+(?:i|we|she|he|they)\s+"
    r"(?:take|stop|start|skip|increase|reduce|decrease|continue))\b",
    re.IGNORECASE,
)

# "should/can I take/use/stop/skip..." is only medical when a medicine-shaped
# object exists somewhere in the turn - "can I take my husband along" and
# "can I take the morning slot" are ordinary booking talk and must pass.
_TAKE_ADVICE_RE = re.compile(
    r"\b(?:should|can|shall)\s+i\s+(?:take|use|stop|start|skip|continue)\b", re.IGNORECASE
)
_MEDICINE_NOUNS_RE = re.compile(
    r"\b(?:medicines?|medications?|tablets?|pills?|doses?|dosage|syrups?"
    r"|antibiotics?|painkillers?|injections?|capsules?)\b",
    re.IGNORECASE,
)

_MEDICAL_ADVICE_PATTERNS = [
    # "what/which medicine should I..." / "suggest me a tablet..."
    # ("any" removed 2026-09-28: "not taking any other medicines" is a report.
    # The questioning uses of "any" are the two patterns right below.)
    re.compile(
        r"\b(?:what|which|suggest|recommend|give\s+me|tell\s+me\s+about)"
        r"[\w\s,']{0,30}\b(?:medicines?|medications?|tablets?|drugs?|antibiotics?"
        r"|syrups?|dose|dosage|painkillers?|remedy|remedies|injections?)\b",
        re.IGNORECASE,
    ),
    # "is there any medicine for ..." / "any tablet for the fever?"
    re.compile(
        r"\b(?:is|are)\s+there\s+any\b[\w\s,']{0,30}\b(?:medicines?|medications?|tablets?"
        r"|drugs?|syrups?|remedy|remedies|injections?)\b"
        r"|\bany\s+(?:medicines?|medications?|tablets?|drugs?|syrups?|remedy|remedies)"
        r"\s+(?:for|to)\b",
        re.IGNORECASE,
    ),
    # "is it safe/ok to take/use/eat/drink ..."
    re.compile(r"\bis\s+it\s+(?:safe|ok|okay|fine)\s+to\s+(?:take|use|eat|drink|have)\b", re.IGNORECASE),
    # "is this/that normal" - symptom triage disguised as a yes/no question
    re.compile(r"\bis\s+(?:this|that|it)\s+normal\b", re.IGNORECASE),
    # "home remedy for ..."
    re.compile(r"\bhome\s+remed(?:y|ies)\b", re.IGNORECASE),
    # "what should I do for the fever/pain..." (bounded to symptom objects)
    re.compile(
        r"\bwhat\s+(?:should|can|do)\s+i\s+do\s+(?:for|about)\s+(?:the\s+|my\s+|this\s+)?"
        r"(?:fever|pain|headache|cold|cough|vomit|nausea|acidity|cramps?|swelling|rash)",
        re.IGNORECASE,
    ),
    # test-result interpretation: "is my report normal/bad..."
    re.compile(r"\b(?:is|are)\s+(?:my|the|this|that)\s+(?:reports?|results?|scans?|readings?)\b", re.IGNORECASE),
]

_PROFANITY_RE = re.compile(
    r"\b(?:fuck(?:ing|er)?|bitch|bastard|asshole|motherfucker|dickhead"
    r"|chutiya|madarchod|bhenchod|behenchod|randi|kutti|kamini)\b",
    re.IGNORECASE,
)


# Negation directly before an emergency phrase, in the same clause (2026-09-28:
# "BP 120 by 80, no severe pain" played the emergency script after a normal
# reading). The clause cut keeps "No, she is bleeding heavily" an emergency.
_EMERGENCY_NEGATION_RE = re.compile(
    r"\b(?:no|not|never|without|denies|denied|nor|none)\b|n't\b", re.IGNORECASE
)
_CLAUSE_BREAK_RE = re.compile(r"[,.;:!?।]|\b(?:but|however)\b", re.IGNORECASE)


def _emergency_affirmed(text: str) -> bool:
    """True when at least one emergency phrase is stated, not denied."""
    for match in _EMERGENCY_RE.finditer(text):
        before = text[max(0, match.start() - 30):match.start()]
        breaks = list(_CLAUSE_BREAK_RE.finditer(before))
        if breaks:
            before = before[breaks[-1].end():]
        if not _EMERGENCY_NEGATION_RE.search(before):
            return True
    return False


def classify_turn(text: str) -> str | None:
    """Classify one caller turn. Returns one of:
    'emergency' | 'self_harm' | 'jailbreak' | 'medical_advice' | 'abuse' | None.

    Order matters: an emergency mentioned alongside anything else is still an
    emergency; self-harm outranks jailbreak/advice; abuse is checked last so
    a frustrated caller describing an emergency still gets the emergency
    path, not a warning.
    """
    t = (text or "").strip()
    if not t:
        return None
    if _emergency_affirmed(t):
        return "emergency"
    if _SELF_HARM_RE.search(t):
        return "self_harm"
    if _JAILBREAK_RE.search(t):
        return "jailbreak"
    if any(p.search(t) for p in _MEDICAL_ADVICE_PATTERNS):
        return "medical_advice"
    if _DRUG_WORDS_RE.search(t) and _ADVICE_VERBS_RE.search(t):
        return "medical_advice"
    if _TAKE_ADVICE_RE.search(t) and (_MEDICINE_NOUNS_RE.search(t) or _DRUG_WORDS_RE.search(t)):
        return "medical_advice"
    if _PROFANITY_RE.search(t):
        return "abuse"
    return None


SCRIPTS = {
    "emergency": EMERGENCY_SCRIPT,
    "self_harm": SELF_HARM_SCRIPT,
    "jailbreak": JAILBREAK_SCRIPT,
    "medical_advice": MEDICAL_REFUSAL_SCRIPT,
}


# --- Output gate (scan generated text BEFORE it reaches TTS) -----------------
# The agent's legitimate vocabulary contains no drug names and no dosage
# phrasing at all, so this can be aggressive with near-zero false-positive
# risk: if the model ever generates one (jailbreak that survived the input
# gate, or unprompted volunteering), the sentence is cut and replaced.

_OUTPUT_BLOCK_RE = re.compile(
    r"\b\d+\s*(?:mg|milligrams?|ml|millilitres?)\b"
    r"|\b(?:paracetamol|dolo|crocin|calpol|ibuprofen|brufen|combiflam|meftal"
    r"|aspirin|disprin|azithromycin|azithral|amoxicillin|augmentin"
    r"|metformin|cetirizine|allegra|pantoprazole|omeprazole"
    r"|take\s+(?:one|two|three|a|1|2|3)\s+(?:tablets?|pills?|capsules?|spoons?)"
    r"|twice\s+a\s+day\s+(?:before|after)\s+(?:food|meals?))\b",
    re.IGNORECASE,
)

# Last-resort fallback only: the stream guard speaks the call's own language
# via script_for("medical_advice", language). Kannada (the pilot language),
# and no offer to book anything - Sakhi books nothing (WHO: an assistant
# must never promise an action it cannot take).
OUTPUT_REPLACEMENT = (
    "ನಮ್ಮ ವೈದ್ಯರು ನಿಮಗೆ ಸೂಕ್ತ ಮಾರ್ಗದರ್ಶನ ನೀಡುತ್ತಾರೆ. "
    "ದಯವಿಟ್ಟು ಹತ್ತಿರದ ಪ್ರಾಥಮಿಕ ಆರೋಗ್ಯ ಕೇಂದ್ರಕ್ಕೆ ಭೇಟಿ ನೀಡಿ."
)


def output_flagged(text: str) -> bool:
    return bool(_OUTPUT_BLOCK_RE.search(text or ""))


# =============================================================================
# DPDP + multilingual safety layer
# =============================================================================
#
# Everything above this line was written for an English clinic receptionist and
# is kept intact. This section adds what a Kannada-first maternal-health agent
# operating under India's DPDP Act actually needs:
#
#   1. Medication detection in Kannada and Hindi. Measured before writing this:
#      "which tablet for headache?" was caught, "ಯಾವ ಮಾತ್ರೆ ಕೊಡಬೇಕು?" and
#      "कौन सी दवा दूं?" were NOT. On an agent that speaks only Kannada, an
#      English-only medication guard is close to decorative.
#   2. Dosage phrasing. The brief's own example, "give her 500mg paracetamol",
#      was missed by the existing patterns.
#   3. PII redaction before anything is logged or sent to a US-hosted LLM.
#
# All matching is precompiled regex over one short utterance - no model, no I/O.

# --- PII ---------------------------------------------------------------------
#
# Ordered most-specific first: an Aadhaar is 12 digits and would otherwise be
# partly consumed by the bank-account pattern.
#
# Clinical numbers are deliberately NOT matched. Blood pressure, gestational
# weeks and age are 1-3 digits and never reach these patterns, which all
# require 10+ digits. Redacting a BP reading would be a clinical failure
# dressed up as a privacy win.
_PII_PATTERNS = (
    # Aadhaar: 12 digits, usually spoken in 4-4-4 groups, never starts 0 or 1.
    ("aadhaar", re.compile(r"\b[2-9]\d{3}[ \-]?\d{4}[ \-]?\d{4}\b")),
    # PAN: five letters, four digits, one letter.
    ("pan", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b", re.IGNORECASE)),
    # Indian mobile: optional +91, then 10 digits starting 6-9.
    ("phone", re.compile(r"(?:\+?91[\-\s]?)?\b[6-9]\d{9}\b")),
    # Bank account: 11-18 digits. Deliberately last.
    ("bank_account", re.compile(r"\b\d{11,18}\b")),
)

_PII_MASK = {
    "aadhaar": "[AADHAAR REDACTED]",
    "pan": "[PAN REDACTED]",
    "phone": "[PHONE REDACTED]",
    "bank_account": "[ACCOUNT REDACTED]",
}


def redact_pii(text: str):
    """Mask identifiers in free text. Returns (masked_text, kinds_found).

    Used on two paths: before a transcript is written to
    logs/pipeline_events.jsonl, and before text reaches a post-call report.
    Under DPDP, an identifier that never lands in a log cannot leak from one.
    """
    if not text:
        return "", []
    found = []
    out = str(text)
    for kind, pattern in _PII_PATTERNS:
        if pattern.search(out):
            found.append(kind)
            out = pattern.sub(_PII_MASK[kind], out)
    return out, found


# --- Multilingual medication / dosage ---------------------------------------

_MEDICATION_INDIC_RE = re.compile(
    # Kannada: maatre (tablet), aushadha/aushadhi (medicine), gulige, injection
    r"ಮಾತ್ರೆ|ಔಷಧ|ಔಷಧಿ|ಗುಳಿಗೆ|ಚುಚ್ಚುಮದ್ದು|ಡೋಸ್"
    # Hindi: goli, dawa, dawai, khuraak, injection
    r"|गोली|दवा|दवाई|दवाइयाँ|खुराक|इंजेक्शन"
    # Transliterated forms Sarvam STT emits for spoken English
    r"|ಟ್ಯಾಬ್ಲೆಟ್|ಮೆಡಿಸಿನ್|ಇಂಜೆಕ್ಷನ್"
)

# A medicine NOUN alone is not a request (2026-09-25, Turns 14 & 25): "ಎರಡು
# ಡೋಸ್ ಪಡೆದಿದ್ದೇನೆ" / "ಎರಡು ಧನುರ್ವಾತ ಚುಚ್ಚುಮದ್ದು ಪಡೆದಿದ್ದೇನೆ" answer the Td
# question and were refused as medical advice. A Kannada/Hindi turn is a
# request only when the noun comes with an advice/question form.
# Deliberately broader than the modal verbs alone: "ಯಾವ ಮಾತ್ರೆ ಕೊಡಲಿ?"
# (which tablet shall I give?) must stay guarded.
_MEDICATION_ADVICE_VERBS_INDIC = re.compile(
    # Kannada, by grammatical ending rather than a word list:
    #   ...ಬಹುದಾ/ಬಹುದೇ  can (I/she) ...?      ...ಬೇಕಾ/ಬೇಕೇ/ಬೇಕು  should ...?
    #   ...ಲಾ/ಲೇ/ಲಿ after give/take/apply     shall I ...?
    #   ಕೊಡಿ/ಕೊಡು/ಹೇಳಿ/ತಿಳಿಸಿ as a whole word  give / tell (imperative)
    r"ಬಹುದ[ಾೇ]|ಬೇಕ[ಾೇು]"
    r"|(?:ಕೊಡ|ತಗೊಳ್ಳ|ತಗೋಳ್ಳ|ತೆಗೆದುಕೊಳ್ಳ|ಹಾಕ|ಮಾಡ)ಲ[ಾೇಿ]"
    r"|(?:ಕೊಡಿ|ಕೊಡು|ಹೇಳಿ|ತಿಳಿಸಿ)(?![ಀ-೿])"
    r"|ಸೂಕ್ತವೇ|ಸರಿಯಾ|ಯಾವ|ಎಷ್ಟು|ಸಲಹೆ"
    # Hindi: should give/take, can eat/give/take, shall I give, what should
    # I do, tell me, which, how much, advice
    r"|देना चाहिए|लेना चाहिए|खा सकते|खा सकती|दे सकते|दे सकती|ले सकते|ले सकती"
    r"|दूँ|दूं|क्या करूँ|क्या करूं|बताइए|बताइये|बताओ|बताएं|कौन सी|कौन सा"
    r"|कितनी|कितना|सलाह"
)

# Past/ongoing delivery - the dose was already received: a factual answer to
# an intake question (Td doses, iron/calcium tablets), not a request.
_MEDICATION_DISCLOSURE_INDIC = re.compile(
    r"ಪಡೆದಿದ್ದೇನೆ|ಪಡೆದಿದ್ದಾರೆ|ಪಡೆದಿದ್ದಾಳೆ|ತಗೊಂಡಿದ್ದೇನೆ|ತಗೊಂಡಿದ್ದಾರೆ|ತಗೊಂಡಿದ್ದಾಳೆ"
    r"|ಹಾಕಿಸಿಕೊಂಡಿದ್ದೇನೆ|ಹಾಕಿಸಿಕೊಂಡಿದ್ದಾರೆ|ಹಾಕಿಸಿಕೊಂಡಿದ್ದಾಳೆ|ಹೋಗಿದೆ"
    r"|ले लिया|ले ली|लगवा लिया|लगवाया|लगवाए|मिल गया|मिल गई"
)


def indic_medication_advice_requested(text: str) -> bool:
    """Kannada/Hindi: a medicine noun AND an advice/question form.

    A pure disclosure ("ಎರಡು ಡೋಸ್ ಪಡೆದಿದ್ದೇನೆ") is never a request. A turn
    with a disclosure AND a question ("ಎರಡು ಡೋಸ್ ಪಡೆದಿದ್ದೇನೆ, ಮೂರನೆಯದು
    ಕೊಡಬೇಕಾ?") is still a request - the question outranks the disclosure.
    """
    if not text or not _MEDICATION_INDIC_RE.search(text):
        return False
    for match in _MEDICATION_ADVICE_VERBS_INDIC.finditer(text):
        if match.group().startswith(_WEAK_INDIC_ADVICE_CUES):
            # "which/how much" followed by a negation in the same clause is a
            # report: "ಬೇರೆ ಯಾವ ಔಷಧಿಯೂ ಇಲ್ಲ" = no OTHER medicine (2026-09-28).
            after = re.split(r"[,.;!?।]", text[match.end():match.end() + 40], maxsplit=1)[0]
            if _INDIC_NEGATION_RE.search(after):
                continue
        return True
    return False


# Question words that only ask when not negated ("ಬೇರೆ ಯಾವ ಔಷಧಿಯೂ ಇಲ್ಲ" is the
# ANSWER "no other medicine"); modal forms (ಕೊಡಬೇಕಾ, ಕೊಡಲಿ, दूँ) always ask.
_WEAK_INDIC_ADVICE_CUES = ("ಯಾವ", "ಎಷ್ಟು", "कौन", "कितन")
_INDIC_NEGATION_RE = re.compile(r"ಇಲ್ಲ|ನಹಿ|नहीं|नही")


def indic_medication_disclosure(text: str) -> bool:
    """A Kannada/Hindi report that a dose was already received."""
    return bool(
        text
        and _MEDICATION_INDIC_RE.search(text)
        and _MEDICATION_DISCLOSURE_INDIC.search(text)
        and not _MEDICATION_ADVICE_VERBS_INDIC.search(text)
    )

# A number next to a dose unit, in any of the three scripts.
_DOSAGE_RE = re.compile(
    r"\b\d+\s*(?:mg|ml|mcg|gm|grams?|tablets?|pills?|doses?)\b"
    r"|\d+\s*(?:ಎಂಜಿ|ಮಿಗ್ರಾ|ಮಾತ್ರೆ|ಗುಳಿಗೆ)"
    r"|\d+\s*(?:मिलीग्राम|एमजी|गोली|खुराक)",
    re.IGNORECASE,
)

# Named drugs. Split into two groups because the clinical stakes differ.
#
# HIGH-RISK / PREGNANCY-CONTRAINDICATED: abortifacients, uterotonics and known
# teratogens. Naming a dose for any of these to an ASHA worker could end a
# pregnancy or cause a foetal injury, so they are listed exhaustively rather
# than relying on the generic "medicine" nouns to catch them.
_HIGH_RISK_DRUGS_RE = re.compile(
    r"\b(?:"
    r"misoprostol|mifepristone|mifeprex|cytotec|methotrexate|"      # abortifacients
    r"oxytocin|pitocin|ergometrine|methylergometrine|"              # uterotonics
    r"warfarin|isotretinoin|valproate|valproic|thalidomide|"        # teratogens
    r"tetracycline|doxycycline|fluconazole|"
    r"lisinopril|enalapril|ramipril|losartan|"                      # ACE / ARB
    r"atenolol|statins?|atorvastatin|simvastatin"
    r")\b",
    re.IGNORECASE,
)

_DRUG_NAMES_RE = re.compile(
    r"\b(?:metformin|labetalol|nifedipine|paracetamol|acetaminophen|aspirin|"
    r"ibuprofen|methyldopa|amoxicillin|azithromycin|insulin|"
    r"dolo|crocin|combiflam|meftal|calpol|brufen|disprin)\b"
    r"|" + _HIGH_RISK_DRUGS_RE.pattern,
    re.IGNORECASE,
)

# Drug names as Sarvam STT renders spoken English into Kannada/Devanagari.
# An agent that only matches Latin spellings is blind to exactly the input it
# actually receives on a Kannada call.
_TRANSLITERATED_DRUGS_RE = re.compile(
    r"ಮೆಟ್‌?ಫಾರ್ಮಿನ್|ಮೆಟ್ಫಾರ್ಮಿನ್"          # metformin
    r"|ಮಡಿಕೇಶನ್|ಮೆಡಿಕೇಶನ್|ಮೆಡಿಸಿನ್"              # medication / medicine
    r"|ಪ್ಯಾರಸಿಟಮಾಲ್|ಪ್ಯಾರಾಸಿಟಮಾಲ್"                # paracetamol
    r"|ಆಸ್ಪಿರಿನ್|ಐಬುಪ್ರೊಫೇನ್|ಇನ್ಸುಲಿನ್"            # aspirin / ibuprofen / insulin
    r"|ಮಿಸೊಪ್ರೊಸ್ಟಾಲ್|ಮೈಫೆಪ್ರಿಸ್ಟೋನ್"             # misoprostol / mifepristone
    r"|मेटफॉर्मिन|पैरासिटामोल|एस्पिरिन|इंसुलिन"
    r"|मिसोप्रोस्टोल|मिफेप्रिस्टोन|मेडिसिन"
)


# English/roman medication nouns. Deliberately NOT sufficient on their own.
#
# Intake question 23 asks "What medications or vitamins is she currently
# taking?", so the ASHA worker's ANSWER will contain exactly these words. A
# bare noun match would block the reply to Sakhi's own question and stall the
# screening - the guard has to tell a REQUEST FOR ADVICE apart from a REPORT
# OF FACT.
_MED_NOUN_RE = re.compile(
    r"\b(?:medicines?|medication|meds|tablets?|pills?|capsules?|syrup|"
    r"injections?|drugs?|dose|dosage|prescription)\b",
    re.IGNORECASE,
)

# Asking for guidance: imperative, permission-seeking or advisory.
_MED_REQUEST_RE = re.compile(
    r"\b(?:prescribe|recommend|suggest|advise|advice)\b"
    r"|\b(?:should|shall|can|may|could)\s+(?:i|we|she|he|they)\b"
    r"|\b(?:give|start|stop|increase|decrease|change|take)\s+(?:her|him|them|any|some|a|the)?\s*"
    r"(?:medicines?|medication|meds|tablets?|pills?|capsules?|syrup|injections?|drugs?|dose|dosage)\b"
    r"|\bwhat\s+(?:medicines?|medication|tablets?|pills?|drugs?|dose|dosage)\b"
    r"|\bwhich\s+(?:medicines?|medication|tablets?|pills?|drugs?)\b"
    r"|\bhow\s+(?:much|many)\b",
    re.IGNORECASE,
)

# Reporting what she is already on - a legitimate answer to intake q23.
_MED_REPORT_RE = re.compile(
    r"\b(?:is|she'?s|he'?s|they'?re)\s+(?:currently\s+|already\s+)?(?:on|taking|using)\b"
    r"|\b(?:has|have)\s+been\s+(?:taking|using|on)\b"
    r"|\b(?:takes|taking|using)\s+(?:iron|folic|calcium|vitamin)",
    re.IGNORECASE,
)


def english_medication_request(text: str) -> bool:
    """True only when the turn ASKS for medication guidance in English.

    "she is taking iron tablets" is an answer to intake q23 and must pass.
    "should I give her medicine" is a request for a prescription and must not.
    """
    if not text or not _MED_NOUN_RE.search(text):
        return False
    if _MED_REPORT_RE.search(text) and not _MED_REQUEST_RE.search(text):
        return False
    return bool(_MED_REQUEST_RE.search(text))


def medication_requested(text: str) -> bool:
    """True when a turn asks about medicine, in any supported language."""
    if not text:
        return False
    return bool(
        indic_medication_advice_requested(text)
        # "ದಿನಕ್ಕೆ 1 ಮಾತ್ರೆ ತಗೊಂಡಿದ್ದಾರೆ" (takes 1 tablet a day) is the iron
        # answer, not a dose question. Named drugs and English stay guarded
        # below regardless.
        or (_DOSAGE_RE.search(text) and not indic_medication_disclosure(text))
        or _DRUG_NAMES_RE.search(text)
        or _TRANSLITERATED_DRUGS_RE.search(text)
        # English requests were reaching the LLM entirely unguarded: there was
        # no roman-script medicine pattern at all, so "should I give her
        # medicine for BP" and "prescribe medicine" passed straight through and
        # the only thing standing between them and a prescription was a prompt
        # rule - which is exactly the protection this module exists to replace.
        or english_medication_request(text)
    )


# --- SQL / database injection ------------------------------------------------
#
# The Sakhi path holds no SQL at all - SixtyDB's mock is a JSON document store,
# and db_helper.py (the legacy clinic SQLite) was audited and is fully
# parameterised: every query uses `?` placeholders with a tuple, with no
# f-strings, concatenation or .format() anywhere near a statement.
#
# So this is defence in depth, not a patch for a live hole. It exists because an
# ASHA worker never says "UNION SELECT", so the presence of SQL structure in a
# transcript means someone is probing the system, and that is worth refusing and
# logging even when it could not have worked.
#
# Structure is required, not bare keywords: "select" and "union" are ordinary
# English words, and blocking a caller for saying "please select the morning
# slot" would be its own failure.
_SQL_INJECTION_RE = re.compile(
    r"\bselect\b[\s\S]{0,80}?\bfrom\b"
    r"|\bunion\b\s+(?:all\s+)?\bselect\b"
    r"|\binsert\b\s+\binto\b"
    r"|\bdelete\b\s+\bfrom\b"
    r"|\bdrop\b\s+(?:table|database)\b"
    r"|\bupdate\b[\s\S]{0,60}?\bset\b"
    r"|\btruncate\b\s+\btable\b"
    r"|'\s*or\s*'?1'?\s*=\s*'?1"          # ' OR '1'='1
    r"|\bor\b\s+1\s*=\s*1\b"
    r"|;\s*(?:drop|delete|insert|update|select)\b"
    r"|--\s*$|/\*|\bxp_cmdshell\b|\bsleep\s*\(",
    re.IGNORECASE,
)


def sql_injection_attempt(text: str) -> bool:
    return bool(text and _SQL_INJECTION_RE.search(text))


# --- Prompt injection --------------------------------------------------------

_INJECTION_RE = re.compile(
    r"ignore\s+(?:all\s+|the\s+)?(?:previous|prior|above|earlier)\s+"
    r"(?:instructions?|prompts?|rules?)"
    r"|disregard\s+(?:your|all|the)\s+(?:instructions?|rules?|guidelines?)"
    r"|forget\s+(?:everything|your\s+instructions?|the\s+rules?)"
    r"|you\s+are\s+now\s+(?:a|an)\b"
    r"|(?:system|developer)\s*(?:prompt|message)\s*[:=]"
    r"|act\s+as\s+(?:a|an)\s+(?:doctor|physician|pharmacist)"
    r"|pretend\s+(?:you\s+are|to\s+be)"
    r"|reveal\s+(?:your|the)\s+(?:prompt|instructions?|system)"
    r"|repeat\s+(?:your|the)\s+(?:prompt|instructions?)"
    # Role/authority escalation - the caller claiming privileged status to
    # unlock behaviour the guardrails would otherwise refuse.
    r"|developer\s+mode|debug\s+mode|god\s+mode|admin\s+mode"
    r"|override\s+(?:the\s+)?(?:system|safety|rules?|guardrails?)"
    r"|system\s+administrator|i\s+am\s+(?:the\s+)?(?:admin|developer|engineer)"
    r"|\bjailbreak\b|\bDAN\s+mode\b|unrestricted\s+mode"
    r"|bypass\s+(?:the\s+)?(?:safety|filter|guardrails?|restrictions?)"
    r"|enable\s+(?:developer|debug|admin)\b"
    r"|ನಿಮ್ಮ\s*ಸೂಚನೆಗಳನ್ನು\s*ಮರೆ"
    r"|अपने\s*निर्देश\s*भूल",
    re.IGNORECASE,
)


def injection_attempt(text: str) -> bool:
    return bool(text and _INJECTION_RE.search(text))


# --- Combined API ------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class InputDecision:
    """Outcome of the pre-LLM gate."""

    text: str                    # PII-redacted; safe to log and to send on
    category: str = None         # None means the turn may proceed
    script: str = ""             # approved reply when blocked
    pii_kinds: tuple = ()

    @property
    def blocked(self) -> bool:
        return self.category is not None


@dataclasses.dataclass(frozen=True)
class OutputDecision:
    """Outcome of the post-LLM gate."""

    text: str
    blocked: bool = False
    reason: str = ""


class SakhiGuardrails:
    """Dual-layer local guardrails. No model, no network, no I/O.

    Stateless and cheap enough to run on every turn. The performance budget is
    asserted in tests/test_sakhi_guardrails.py rather than claimed here.
    """

    def sanitize_input(self, text: str, language: str = "kn") -> InputDecision:
        """Pre-LLM gate.

        Order is deliberate:

        1. **Redact PII first.** Whatever happens next - blocked or allowed -
           the text gets logged and may be sent to a US-hosted model. An Aadhaar
           number must not survive either path, including on turns that are
           otherwise perfectly safe.
        2. **classify_turn next**, because it owns emergency and self-harm and
           those outrank everything. A flat refusal to a bleeding mother is a
           worse failure than answering a paracetamol question; severity is
           escalated here, never triaged.
        3. Medication and injection checks last, on the redacted text.
        """
        if not text:
            return InputDecision(text="")

        clean, pii = redact_pii(text)
        found = tuple(pii)

        category = classify_turn(clean)

        if category is None:
            if medication_requested(clean):
                category = "medical_advice"
            elif injection_attempt(clean) or sql_injection_attempt(clean):
                # SQL probing is grouped with prompt injection: both are
                # attempts to manipulate the system rather than clinical
                # questions, and both get the same "I am Sakhi, here for the
                # mother's health" redirect.
                category = "jailbreak"

        if category is None:
            return InputDecision(text=clean, pii_kinds=found)

        return InputDecision(
            text=clean,
            category=category,
            script=script_for(category, language) or SCRIPTS.get(category, ""),
            pii_kinds=found,
        )

    def validate_output(self, text: str, language: str = "kn") -> OutputDecision:
        """Post-LLM gate: nothing unsafe reaches TTS.

        Catches what the input gate cannot - the model volunteering a drug name
        or a dose that nobody asked for.
        """
        if not text:
            return OutputDecision(text="")

        if (
            output_flagged(text)
            or _DRUG_NAMES_RE.search(text)
            or _TRANSLITERATED_DRUGS_RE.search(text)
            or _DOSAGE_RE.search(text)
        ):
            return OutputDecision(
                text=script_for("medical_advice", language),
                blocked=True,
                reason="medication or dosage in generated text",
            )

        # A model echoing an identifier back is still a disclosure.
        clean, pii = redact_pii(text)
        if pii:
            return OutputDecision(
                text=clean, blocked=False, reason="pii_redacted:" + ",".join(pii)
            )

        return OutputDecision(text=text)


GUARDRAILS = SakhiGuardrails()
