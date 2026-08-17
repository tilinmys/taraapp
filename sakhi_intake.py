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
"""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class IntakeQuestion:
    """One question. Frozen: the questionnaire is not editable at runtime."""

    id: str
    section: str
    text: str
    vad: str  # FAST for yes/no, dates, numbers; SLOW for open description
    # Name of a SakhiCallState-derived fact that answers this without asking.
    satisfied_by: str | None = None


SECTION_1 = "Patient Particulars & Chief Complaint"
# Pre-eclampsia screening is this agent's clinical purpose, so BP and the three
# danger signs are part of the ordered flow - not a separate track that jumped
# the queue.
#
# They sit immediately after patient particulars: late enough that Sakhi has
# identified the mother first (the ASHA worker's stated priority: "first
# collect the details"), early enough that a pre-eclamptic mother is not 20
# questions away from being caught.
#
# Deterministic symptom detection still runs on EVERY turn independently of
# this ordering, and triage still escalates the moment a severe reading is
# reported - so a mother in danger is never gated behind the questionnaire.
SECTION_VITALS = "Vital Signs & Danger Signs"
SECTION_2 = "History of Present Pregnancy"
SECTION_3 = "Past Obstetric History"
SECTION_4 = "Past Medical & Surgical History"
SECTION_5 = "Medications & Social History"


INTAKE_QUESTIONS: tuple[IntakeQuestion, ...] = (
    # Section 1
    IntakeQuestion("q01", SECTION_1, "What is her full name and age?", "SLOW", "identity"),
    IntakeQuestion("q02", SECTION_1, "What brought her to the clinic today?", "SLOW"),
    IntakeQuestion("q03", SECTION_1, "When did her symptoms first start?", "FAST"),
    # Vitals - the pre-eclampsia core
    IntakeQuestion("q04", SECTION_VITALS, "What is her blood pressure reading?", "FAST", "blood_pressure"),
    IntakeQuestion("q05", SECTION_VITALS, "Does she have headache, blurred vision, or swelling?", "FAST", "symptoms_screened"),
    # Section 2
    IntakeQuestion("q06", SECTION_2, "What was the first day of her last menstrual period?", "FAST", "gestational_weeks"),
    IntakeQuestion("q07", SECTION_2, "Does she know her estimated due date?", "FAST"),
    IntakeQuestion("q08", SECTION_2, "Has she had any nausea, vomiting, or vaginal bleeding?", "FAST"),
    IntakeQuestion("q09", SECTION_2, "Has she started feeling the baby move yet?", "FAST"),
    IntakeQuestion("q10", SECTION_2, "How often is the baby moving now?", "SLOW"),
    IntakeQuestion("q11", SECTION_2, "Has she had any fluid leaking or abdominal pain?", "FAST"),
    IntakeQuestion("q12", SECTION_2, "What scans or blood tests has she had so far?", "SLOW"),
    # Section 3
    IntakeQuestion("q13", SECTION_3, "How many times has she been pregnant before?", "FAST"),
    IntakeQuestion("q14", SECTION_3, "Did any of those pregnancies end in a miscarriage or termination?", "FAST"),
    IntakeQuestion("q15", SECTION_3, "What was the gestational age at each previous delivery?", "FAST"),
    IntakeQuestion("q16", SECTION_3, "Were those deliveries vaginal or by C-section?", "FAST"),
    IntakeQuestion("q17", SECTION_3, "Did she have complications like high blood pressure?", "FAST"),
    IntakeQuestion("q18", SECTION_3, "What were the birth weights of her previous babies?", "FAST"),
    # Section 4
    IntakeQuestion("q19", SECTION_4, "Does she have pre-existing conditions like diabetes?", "FAST"),
    IntakeQuestion("q20", SECTION_4, "Has she ever had high blood pressure or kidney disease?", "FAST"),
    IntakeQuestion("q21", SECTION_4, "Has she ever had surgery on her uterus?", "FAST"),
    IntakeQuestion("q22", SECTION_4, "Are her vaccinations up to date, including rubella?", "FAST"),
    # Section 5
    IntakeQuestion("q23", SECTION_5, "What medications or vitamins is she currently taking?", "SLOW"),
    IntakeQuestion("q24", SECTION_5, "Does she have any allergies to medications?", "FAST"),
    IntakeQuestion("q25", SECTION_5, "Does she smoke, drink alcohol, or use recreational drugs?", "FAST"),
    IntakeQuestion("q26", SECTION_5, "Does she feel safe and supported at home?", "FAST"),
)

QUESTIONS_BY_ID = {q.id: q for q in INTAKE_QUESTIONS}

assert len(QUESTIONS_BY_ID) == len(INTAKE_QUESTIONS), "duplicate intake question id"


def render_inventory() -> str:
    """The prompt's question list, generated from the questionnaire itself.

    The prompt used to carry its own hand-typed copy of these 24 lines. Two
    copies of a clinical protocol drift, and the one the model reads is not
    the one the code tracks.
    """
    lines: list[str] = []
    current_section = None
    for index, question in enumerate(INTAKE_QUESTIONS, start=1):
        if question.section != current_section:
            current_section = question.section
            lines.append(f"## {current_section}")
        lines.append(f'{index}. "{question.text} [VAD: {question.vad}]"')
    return "\n".join(lines)


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
        if getattr(state, "blood_pressure", None):
            self.mark_satisfied("q04", str(state.blood_pressure))
        if getattr(state, "symptoms_screened", False):
            self.mark_satisfied("q05", "screened")
        if getattr(state, "gestational_weeks", None):
            self.mark_satisfied("q06", f"~{state.gestational_weeks} weeks")

    def record_answer(self, text: str) -> str | None:
        """Attribute this user turn to the question that was pending.

        Returns the question id that was closed, or None. Only the PENDING
        question can be closed, so an unrelated remark cannot silently tick off
        a question that was never asked.
        """
        text = (text or "").strip()
        if not text or self.pending_id is None:
            return None
        closed = self.pending_id
        self.answered[closed] = text[:120]
        self.pending_id = None
        return closed

    # --- queries -------------------------------------------------------

    def arm_next(self) -> IntakeQuestion | None:
        """Mark the next question as the one being asked THIS turn.

        Called at injection time so the ASHA worker's reply can be attributed
        to it. Without arming, `record_answer` has nothing to close and the
        checklist never advances.
        """
        nxt = self.next_question()
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
                f"intake={done}/{total} COMPLETE - ask no further intake questions; "
                "summarise and close"
            )
        return (
            f"intake={done}/{total} done. "
            f"NEXT QUESTION (ask THIS one, verbatim in meaning, and nothing else): "
            f'"{nxt.text}" [VAD: {nxt.vad}]'
        )
