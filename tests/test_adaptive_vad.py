"""Tests for the adaptive VAD state machine (agent.DynamicVADController).

The controller toggles the session's endpointing min_delay between fast /
slow / fragment-recovery based on what the agent just asked - via the PUBLIC
session.update_options(endpointing_opts=...) API, never by mutating the
shared Silero VAD instance (which is shared across concurrent calls via
proc.userdata and would contaminate other live calls).

Standalone runnable: python tests/test_adaptive_vad.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, condition: bool) -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"[PASS] {label}")
    else:
        FAIL += 1
        print(f"[FAIL] {label}")


# --- state machine transitions ------------------------------------------------
session = MagicMock()
c = agent.DynamicVADController(session)
check("starts TRANSACTIONAL", c.state == "TRANSACTIONAL")

c.evaluate_agent_text("May I please have your name?")
check("name question -> HIGH_COGNITIVE_LOAD", c.state == "HIGH_COGNITIVE_LOAD")
check(
    "slow delay applied via public API",
    session.update_options.call_args.kwargs["endpointing_opts"]["min_delay"] == 1.0,
)

c.evaluate_agent_text("Is ten thirty tomorrow okay for you?")
check("closed question -> TRANSACTIONAL", c.state == "TRANSACTIONAL")
# Compare against the controller's own configured fast value, not a literal.
# This previously hardcoded 0.35 and broke the moment MIN_ENDPOINTING_DELAY was
# retuned to 0.3 - it was asserting the deployment's env, not the code.
check(
    "fast delay restored",
    session.update_options.call_args.kwargs["endpointing_opts"]["min_delay"]
    == c._delays["TRANSACTIONAL"],
)

c.fragment_recovery()
check("fragment -> FRAGMENT_RECOVERY", c.state == "FRAGMENT_RECOVERY")
check(
    "fragment delay applied",
    session.update_options.call_args.kwargs["endpointing_opts"]["min_delay"] == 1.3,
)

c.on_user_turn()
check("fragment window is one-shot", c.state == "TRANSACTIONAL")

n = session.update_options.call_count
c.evaluate_agent_text("okay, booked for tomorrow")
check("idempotent: no redundant update on same state", session.update_options.call_count == n)

# --- failure safety -------------------------------------------------------------
bad = MagicMock()
bad.update_options.side_effect = RuntimeError("session closed")
c2 = agent.DynamicVADController(bad)
c2.evaluate_agent_text("could you spell your name?")
check("dead session fails safe, state unchanged", c2.state == "TRANSACTIONAL")

# --- slow trigger coverage -------------------------------------------------------
# Maternal-workflow triggers. The clinic phrasings that used to be tested here
# ("what brings you in today", "do you have a specific doctor in mind") are gone
# with the booking flow - Sakhi never says them. The cases that need a long
# window now are readings, gestational age, names and villages, in all three
# languages the pilot runs in.
for t in [
    "Could you spell that for me?",
    "letter by letter please",
    "What is her blood pressure reading?",
    "How many weeks pregnant is she?",
    "please describe the symptoms",
    "ತಾಯಿಯ ರಕ್ತದೊತ್ತಡ ಎಷ್ಟು?",      # Kannada: what is her blood pressure
    "ಎಷ್ಟು ವಾರ ಗರ್ಭಿಣಿ?",             # Kannada: how many weeks pregnant
    "उनका नाम क्या है?",               # Hindi: what is her name
]:
    c3 = agent.DynamicVADController(MagicMock())
    c3.evaluate_agent_text(t)
    check(f"slow trigger: {t!r}", c3.state == "HIGH_COGNITIVE_LOAD")

for t in [
    "Is that right?",
    "I have recorded that.",
    "ಸರಿ, ಧನ್ಯವಾದಗಳು.",               # Kannada: alright, thank you
]:
    c4 = agent.DynamicVADController(MagicMock())
    c4.evaluate_agent_text("your name please")  # push to slow first
    c4.evaluate_agent_text(t)
    check(f"fast trigger: {t!r}", c4.state == "TRANSACTIONAL")


# --- full hook integration --------------------------------------------------------
async def hook_tests() -> None:
    sakhi = agent.SakhiAgent(instructions="t", chat_ctx=agent.llm.ChatContext())
    mock_session = MagicMock()
    mock_session.userdata = agent.SakhiCallState()
    sakhi._activity = MagicMock(session=mock_session)
    vc = agent.DynamicVADController(MagicMock())
    sakhi._vad_controller = vc

    # FRAGMENT_RECOVERY is no longer reachable from a user turn: its only
    # trigger was the fast path's English `looks_incomplete` detector, removed
    # with the booking flow. The state and its timing still work when driven
    # directly, which is what a future Kannada fragment detector would do.
    vc.fragment_recovery()
    check("fragment_recovery() still engages the long window",
          vc.state == "FRAGMENT_RECOVERY")

    # An ordinary user turn consumes the one-shot window.
    msg = MagicMock()
    msg.text_content = "ಅವಳ ರಕ್ತದೊತ್ತಡ ನೂರ ಇಪ್ಪತ್ತು ಎಂಬತ್ತು"   # "her BP is 120 over 80"
    tc = MagicMock()
    tc.items = [MagicMock(role="assistant", text_content="ತಾಯಿಯ ರಕ್ತದೊತ್ತಡ ಎಷ್ಟು?")]
    try:
        await sakhi.on_user_turn_completed(tc, msg)
    except agent.StopResponse:
        pass
    check("user turn consumes the fragment window", vc.state == "TRANSACTIONAL")

    # A guardrail turn must still be answered deterministically and stop the LLM.
    stopped = False
    msg2 = MagicMock()
    msg2.text_content = "which tablet should she take for the headache?"
    try:
        await sakhi.on_user_turn_completed(MagicMock(items=[]), msg2)
    except agent.StopResponse:
        stopped = True
    check("medical-advice turn is stopped before the LLM", stopped)


asyncio.run(hook_tests())

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
