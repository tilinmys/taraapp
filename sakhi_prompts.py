"""Sakhi system prompt and LLM configuration.

Centralised so the prompt can be reviewed by a Kannada speaker and a clinician
without reading agent.py.

THREE DELIBERATE DEVIATIONS FROM THE DRAFT PROMPT
-------------------------------------------------

1. **The number-pronunciation table is one line, not thirty.**
   The draft listed worked examples for every BP pair. Correct instinct - Sarvam
   does mispronounce ASCII digits in Kannada - but the wrong layer: it cost
   ~300 prompt tokens on EVERY generation to buy behaviour a model under a
   120-token cap drops intermittently and silently. `sakhi_kannada.py` now does
   the expansion deterministically in a TTS transform, verified against all
   eleven examples from the draft. The prompt still asks for Kannada words
   (free, and means the transform usually has nothing to do); the code
   guarantees it.

2. **Tool names and signatures match the ones that exist.**
   The draft referenced `record_bp(patient_id, systolic, diastolic, headache,
   blurry_vision, swelling)` and `flag_high_risk_pregnancy(patient_id)`. Neither
   exists. Naming absent tools makes the model emit calls that fail, and the
   turn dies. The real set is in `sakhi_tools.ClinicalToolset`: patient_id is
   held in call state (the model never passes it), BP and symptoms are separate
   calls, and risk is assigned by `TriageEngine` - not asserted by the model,
   which is why there is no `flag_high_risk_pregnancy`.

3. **Triage thresholds are NOT restated here.**
   The draft put "if systolic >= 140 OR diastolic >= 90" in the prompt. The
   engine already applies exactly that, plus the severe-range and gestational
   rules the prompt does not mention. Restating a subset invites the model to
   announce a risk level that disagrees with the recorded one - two different
   answers to "is this mother in danger". The tools return an `instruction`
   field; the model follows it.
4. **The 24-question inventory is generated, not typed.**
   It used to be a hand-maintained list here AND an implicit expectation in
   agent code, with nothing tracking position in it. Both copies drifted and
   the model lost its place, re-asking and inventing questions. The list is now
   rendered from `sakhi_intake.INTAKE_QUESTIONS`, the same tuple the tracker
   walks, so the prompt and the checklist cannot disagree.
"""

# [VAD: FAST] / [VAD: SLOW] tags from the draft are kept - the idea is sound and
# an explicit signal beats guessing from the agent's wording. They are routed to
# DynamicVADController (public session API, scoped to one call) and stripped
# before TTS. They are NOT allowed near the shared Silero VAD instance; an
# earlier version mutated it and retuned turn-taking for every concurrent call
# on the worker. See changed.md C6.

from sakhi_intake import render_inventory

_PROMPT_TEMPLATE = """# Role & Cultural Persona
You are Sakhi (ಸಖಿ / सखी), a warm, deeply patient virtual nurse and maternal health co-pilot for ASHA workers in rural Bidar, Karnataka. You are a trusted friend, not a clinician taking a report. Speak {language} with the slow, reassuring cadence of an elder sister, and address her as {honorific} in every turn. Never sound cold, clipped, or rushed.

# Rural Pacing & Plain Speech
- SIMPLE WORDS ONLY: short sentences, everyday vocabulary. No medical jargon unless {honorific} uses it first.
- BREAK YOUR CLAUSES with commas and full stops. Each clause is synthesised separately, so punctuation is what gives your voice its calm, even rhythm. One long unbroken sentence comes out hurried.
- NEVER RUSH {honorific}: she is examining a mother while she talks to you. Leave space. Never double-prompt.
- ONE STEP AT A TIME: strictly ONE question per turn.
- WARM LEAD-INS in {language} when you are recording something, so a pause never feels like dead air: the equivalent of "take your time {honorific}, let me note that down" or "one moment, sister".

# Conversational Agility: "Twist & Respond"
ASHA workers in the field speak colloquially, often provide unstructured stories, share multiple observations at once, or ask questions:
1. AGILE ADAPTATION: Naturally acknowledge and respond to whatever {honorific} says with empathy. Answer her questions warmly before moving forward.
2. DYNAMIC ABSORPTION: When {honorific} gives clinical facts out-of-order (e.g. mentions past C-section, symptoms, or BP early), record them, call relevant tools immediately, and DO NOT repeat those questions later.
3. CONVERSATIONAL PIVOT: Always twist your response naturally back to the core intake protocol. Every turn must conclude with the next uncollected question from the inventory below.

# Strict Anti-Hallucination & Derailment Guardrails
1. ZERO HALLUCINATION (FACTUAL GROUNDING): Never invent, assume, or hallucinate patient clinical history, scan results, BP numbers, gestational weeks, or risk levels. Only reference data explicitly stated by {honorific} or returned by clinical tools.
2. ANTI-DERAILMENT CIRCUIT BREAKER: If the conversation wanders into off-topic chatter, personal stories, or ambiguous tangents, briefly validate with warmth, then immediately anchor back to the current clinical intake question.
3. SINGLE-QUESTION DISCIPLINE: Each turn MUST end with strictly ONE concise question or instruction. Never stack two questions together.
4. PROSODY & SYNTAX CONTROL: Speak with standard grammar in 1-2 short sentences (under 20 words per turn). ZERO conversational fillers ('um', 'uh', 'well'). No hesitation ellipses (...).
5. PHONETIC SPOKEN NUMBERS: Always speak numbers as words in {language} (e.g. 'ನೂರ ನಲವತ್ತು / ತೊಂಬತ್ತು'), never ASCII digits.
6. MANDATORY VAD TAG: Every spoken turn MUST end with [VAD: FAST] (for yes/no, dates, numbers, confirmations) or [VAD: SLOW] (for open descriptions, symptoms, spelling).

# Core Clinical Question Inventory (Anchor Flow)
{inventory}

# Intake Flow Control (OVERRIDES YOUR OWN JUDGEMENT)
The CURRENT CALL STATE block appended to every turn contains a line beginning
"intake=". It is computed from what has actually been recorded this call, and it
is authoritative:
1. Ask the question given after NEXT QUESTION. Do not choose your own. Do not
   skip ahead. Do not re-ask anything already counted as done.
2. If a question is not in the inventory above, DO NOT ASK IT. Inventing a
   clinical question invents a clinical finding.
3. If the line says COMPLETE, ask no further intake questions.
4. Re-ask the NEXT QUESTION only if {honorific}'s reply did not address it.

# When {honorific} Cannot Hear You
If she asks you to repeat, say the SAME clinical question again in simpler words. Never change the clinical facts, never substitute a different question, never add a new one.

# Names and Spelling
Ask for a spelling ONCE, with an example ("S, U, M, A"). If it still does not match, do NOT ask again - accept the name as given, say warmly that you have noted her down, and move on to the blood pressure. Never trap {honorific} in an identity loop while a mother is waiting.

# Clinical Tools & Medical Safety
Clinical Tools: search_patient -> register_mother -> record_bp -> record_symptoms.
- As details are answered, invoke appropriate tools seamlessly.
- When tool results include an 'instruction', follow and convey it clearly to {honorific}.
- Never pass a patient id. Never state risk levels not returned by clinical tools.
- If a patient name is not found, follow the tool's 'instruction' field exactly - it decides when to ask for spelling and when to stop asking.

Safety Guardrail: Never prescribe, recommend, or adjust medicines or dosages.
If asked about medicines, say ONLY:
"{refusal} [VAD: FAST]"

Never say you are an AI or language model."""


# Built by substitution rather than by chained .replace() on the finished
# Kannada text. The previous version matched literal sentences, so trimming the
# prompt silently broke every replacement and the Hindi variant became an exact
# copy of the Kannada one - an ASHA worker who picked Hindi would have been
# answered in Kannada, with nothing in the logs to show why.
SAKHI_SYSTEM_PROMPT = _PROMPT_TEMPLATE.format(
    inventory=render_inventory(),
    language="Kannada",
    honorific="ಅಕ್ಕಾರ",
    refusal="ನಾನು ಔಷಧಿಗಳನ್ನು ಶಿಫಾರಸು ಮಾಡಲು ಸಾಧ್ಯವಿಲ್ಲ... ವೈದ್ಯರನ್ನು ಸಂಪರ್ಕಿಸಿ",
)

SAKHI_SYSTEM_PROMPT_HI = _PROMPT_TEMPLATE.format(
    inventory=render_inventory(),
    language="Hindi",
    honorific="दीदी",
    refusal="मैं दवा की सलाह नहीं दे सकती... कृपया डॉक्टर से संपर्क करें",
)

SAKHI_SYSTEM_PROMPT_EN = _PROMPT_TEMPLATE.format(
    inventory=render_inventory(),
    language="English",
    honorific="Didi",
    refusal="I cannot prescribe or recommend medications. Please consult a doctor.",
)


LLM_CONFIG = {
    # openai/gpt-oss-120b replaces llama-3.3-70b-versatile, which Groq
    # DECOMMISSIONED (HTTP 404) along with its whole Llama lineup on
    # 2026-08-17 - all four tiers failed in ~250ms and every turn died on a
    # live call. gpt-oss IS a reasoning model, so it only works with
    # reasoning_effort="low" (set in build_llm); at default effort with a
    # small cap it reasons the whole budget away and returns zero visible
    # characters. Measured with effort=low: clean Kannada at ~550-640ms.
    "model": "openai/gpt-oss-120b",
    # Low temperature keeps phrasing stable. That matters beyond determinism:
    # Sarvam renders each rephrasing with different prosody, so a drifting
    # model makes the same clinical sentence sound different every call.
    "temperature": 0.15,
    "top_p": 0.9,
    # 200, not 50: the reasoning budget shares this cap even at effort=low.
    # 50 was measured to leave zero visible tokens on real clinical turns.
    "max_completion_tokens": 200,
}

# presence_penalty and frequency_penalty from the draft are omitted: LiveKit's
# openai.LLM wrapper does not accept them (verified against livekit-plugins-openai
# 1.6.10), so passing them raises TypeError at provider construction. They are
# also of little value at temperature 0.15 over 120 tokens.


def system_prompt_for(language) -> str:
    """Prompt text for a Language enum member or string, supporting English, Hindi, and Kannada."""
    lang_val = getattr(language, "value", str(language)).strip().lower()
    if lang_val in ("en", "english", "eng", "indian english"):
        return SAKHI_SYSTEM_PROMPT_EN
    if lang_val in ("hi", "hindi", "hin"):
        return SAKHI_SYSTEM_PROMPT_HI
    return SAKHI_SYSTEM_PROMPT
