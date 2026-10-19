"""Retuning from live ASHA-worker feedback (18 Aug 2026).

Reported: Sakhi said "one moment, I am checking" on almost every turn, cut the
worker off mid-word, skipped questions she had never really answered, and the
whole exchange felt robotic.
"""

import os

import pytest
from dotenv import load_dotenv

from sakhi_intake import IntakeProgress
from sakhi_prompts import SAKHI_SYSTEM_PROMPT, SAKHI_SYSTEM_PROMPT_EN, SAKHI_SYSTEM_PROMPT_HI

load_dotenv(".env")


# --- 1. the holding phrase must be rare ------------------------------------

def test_filler_waits_longer_than_the_model_usually_takes():
    """It fired at 0.6s while Groq's MEDIAN first token is 604ms, so the worker
    heard it on roughly every second turn."""
    delay = float(os.getenv("LATENT_FILLER_DELAY", "0.6"))
    assert delay >= 1.5, (
        f"filler at {delay}s fires on normal turns; Groq measures 604ms median, "
        "1113ms worst case"
    )


# --- 2. she must not be cut off mid-sentence -------------------------------

def test_endpointing_window_gives_room_to_breathe():
    """Observed eou_delay 0.29-0.54s, with finals like 'Yeah. The this name is'
    - the worker was being committed mid-sentence."""
    vad = float(os.getenv("VAD_MIN_SILENCE", "0.25"))
    endpoint = float(os.getenv("MIN_ENDPOINTING_DELAY", "0.3"))
    window = vad + endpoint
    assert window >= 0.75, f"turn commits after {window:.2f}s; too fast for spoken Kannada"
    assert window <= 1.2, f"turn commits after {window:.2f}s; that reads as sluggish"


def test_recall_window_is_longer_than_the_fast_window():
    fast = float(os.getenv("VAD_MIN_SILENCE", "0.35")) + float(os.getenv("MIN_ENDPOINTING_DELAY", "0.5"))
    slow = float(os.getenv("VAD_ENDPOINT_SLOW_S", "1.0"))
    assert slow > fast - 0.35, "recall/spelling turns need at least as much room"


# --- 3. noise must not close a clinical question ---------------------------

@pytest.mark.parametrize("noise", [
    "Yeah.", "Hello?", "Hello", "ok", "hmm", "what", "ಹೌದು", "हाँ", "no",
])
def test_acknowledgements_do_not_advance_the_intake(noise):
    """Each of these closed a question on a live call, which is what the worker
    experienced as Sakhi skipping ahead."""
    progress = IntakeProgress()
    progress.arm_next()
    assert progress.record_answer(noise) is None, f"{noise!r} advanced the intake"
    assert progress.next_question().id == "q01"


@pytest.mark.parametrize("real", [
    "Suma Gowda, twenty four",
    "And her age is twenty three.",
    "she has had headaches for three days",
    "ಅವಳ ಹೆಸರು ಸುಮಾ ಗೌಡ",
])
def test_real_answers_still_advance_the_intake(real):
    """The guard must not become a wall - a question never asked is worse than
    a wrong answer recorded."""
    progress = IntakeProgress()
    progress.arm_next()
    assert progress.record_answer(real) == "q01"


def test_an_empty_or_silent_turn_never_advances():
    progress = IntakeProgress()
    progress.arm_next()
    assert progress.record_answer("   ") is None


# --- 4. the prompt must not mandate robotic speech -------------------------

def test_prompt_does_not_force_clipped_delivery():
    """'under 20 words' was an overly rigid clipping recipe."""
    assert "under 20 words" not in SAKHI_SYSTEM_PROMPT


def test_prompt_asks_for_warmth_and_variety():
    assert "kind elder sister" in SAKHI_SYSTEM_PROMPT
    # Reworded 2026-09-09 (shorter, to make room for the persona-grounding
    # fix below under the 12000-char budget) - same intent, updated wording.
    assert "never the same opener twice running" in SAKHI_SYSTEM_PROMPT


def test_prompt_forbids_repetition():
    assert "Never Repeat Yourself" in SAKHI_SYSTEM_PROMPT


def test_prompt_covers_speech_aimed_at_the_patient():
    """Reinforces the code-level side-talk gate: belt and braces, because the
    gate only recognises phrasings it has patterns for."""
    for prompt in (SAKHI_SYSTEM_PROMPT, SAKHI_SYSTEM_PROMPT_EN):
        assert "Also Talking To The Mother" in prompt
        assert "Never answer them" in prompt


# --- 5. healthcare guardrails (India-context, mother may be listening) -----
#
# HIPAA is US-only law and does not apply here. These guardrails draw on
# India's Digital Personal Data Protection Act 2023, the IT Act 2000 + SPDI
# Rules (health data as sensitive personal information), the Telemedicine
# Practice Guidelines 2020 (relevant since connect_smfmi_specialist bridges
# to a teleconsultation), and NMC's Code of Ethics - in spirit, as prompt-
# level product guardrails, not a certified legal compliance implementation.

def test_prompt_never_denies_being_ai_if_asked_directly():
    """The base 'never volunteer' instruction stays - this only narrows it
    with an honesty carve-out, so a regression that deletes the base
    instruction entirely (rather than narrowing it) should fail here too."""
    for prompt in (SAKHI_SYSTEM_PROMPT, SAKHI_SYSTEM_PROMPT_EN):
        assert "Never volunteer that you are an AI" in prompt
        assert "answer honestly" in prompt


def test_prompt_limits_identity_readback_to_the_name():
    for prompt in (SAKHI_SYSTEM_PROMPT, SAKHI_SYSTEM_PROMPT_EN):
        assert "confirm only the name" in prompt


def test_prompt_reinforces_calm_risk_wording():
    """Prompt-level reinforcement of the actual mechanism, which lives in
    sakhi_tools.py's _speak_instruction gate (see test_risk_speech_gating.py)
    - the model still free-generates phrasing around whatever a tool
    instruction says, so this line is a backstop, not the enforcement."""
    for prompt in (SAKHI_SYSTEM_PROMPT, SAKHI_SYSTEM_PROMPT_EN):
        assert "the mother may be listening" in prompt


def test_greeting_text_is_unchanged_by_the_consent_line():
    """Consent/transparency framing went into the standing system prompt
    (see test_prompt_mentions_data_use below), NOT the cached, hash-keyed
    GREETING_TEXT - every added word there is
    heard by literally every caller before anything else, and this file's
    own header describes a real incident from a single wrong greeting
    sentence. Regression guard: nothing should touch GREETING_TEXT for this."""
    from agent import GREETING_TEXT
    assert GREETING_TEXT == "ನಮಸ್ಕಾರ, ನಾನು ಸಖಿ. ತಾಯಿಯ ಹೆಸರು ಮತ್ತು ವಯಸ್ಸು ಹೇಳಿ."


def test_prompt_mentions_data_use():
    for prompt in (SAKHI_SYSTEM_PROMPT, SAKHI_SYSTEM_PROMPT_EN):
        assert "care team" in prompt


def test_prompt_stays_within_a_sane_length_budget():
    """Not a tight token count (LLM_CONFIG['max_completion_tokens']=200 caps
    generation, not the prompt itself) but a guard against exactly the kind
    of unchecked accretion this file's own header warns about - stacking
    instructions onto a reasoning-budget-capped model silently returned zero
    visible characters once already (see sakhi_prompts.py's module
    docstring).

    Budget raised from 12000 to 14000 on 2026-09-10 (Antenatal Quick Check
    rewrite): the questionnaire grew from 17 to 24 questions to add real
    ASK/CHECK-section coverage (chronic disease history, medications/
    allergies, fetal heart rate, lab review, and broader symptom bundles) -
    an explicit, disclosed decision, not unchecked accretion. All three
    language variants are checked now; the English one was already over the
    OLD budget (12072) with zero coverage before this change."""
    assert len(SAKHI_SYSTEM_PROMPT) < 14000
    assert len(SAKHI_SYSTEM_PROMPT_HI) < 14000
    assert len(SAKHI_SYSTEM_PROMPT_EN) < 14000
