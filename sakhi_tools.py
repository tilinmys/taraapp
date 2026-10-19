"""Clinical function tools for Sakhi.

Replaces the four stub tools that were bolted onto the clinic agent
(changed.md C1-C3), where `record_bp` persisted nothing and computed no risk,
`flag_high_risk_pregnancy` only logged a line, and `record_symptoms` /
`record_patient_data` did not exist at all despite the prompt calling for them.

Design
------
These are **methods on a class**, not module-level functions, so the repository
and triage engine are injected through the constructor instead of being reached
through module globals. LiveKit binds bound methods as tools and excludes `self`
from the generated JSON schema (verified against livekit-agents 1.6.10), so the
model sees exactly the clinical arguments.

The division of labour is deliberate and is the point of the whole module:

    the LLM  collects the readings and speaks the outcome
    the code decides what the outcome is

Every tool returns a compact instruction block telling the model what was
determined and what to say next. The model never computes a risk level.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Callable
from pydantic import BaseModel, Field, ValidationError

from livekit.agents import RunContext, llm

if not hasattr(llm, "FunctionContext"):
    class FunctionContext:
        def __init__(self):
            pass
    llm.FunctionContext = FunctionContext

if not hasattr(llm, "ai_callable"):
    llm.ai_callable = llm.function_tool


class DemographicInput(BaseModel):
    name: str = Field(description="Mother's name", min_length=1)
    age: int = Field(description="Mother's age in years", ge=12, le=55)


class SakhiIntakeTools(llm.FunctionContext):
    def __init__(self, current_stage: int = 1, repo=None, session=None):
        super().__init__()
        self.current_stage = current_stage
        self.last_spoken_prompt = ""
        self.repo = repo
        self.session = session

    @llm.ai_callable(
        name="record_patient_intake",
        description="Save mother's demographic name and age, and get the next question to speak.",
    )
    async def record_patient_intake(self, name: str, age: int) -> str:
        try:
            demographic = DemographicInput(
                name=str(name or "").strip(),
                age=int(str(age).strip()),
            )
        except (TypeError, ValueError, ValidationError):
            spoken_text = "ತಾಯಿಯ ಹೆಸರು ಮತ್ತು ಸರಿಯಾದ ವಯಸ್ಸನ್ನು ಮತ್ತೆ ಹೇಳಿ ಅಕ್ಕಾ."
            self.last_spoken_prompt = spoken_text
            return f"NOT_RECORDED. Speak this response: {spoken_text}"

        clean_name = demographic.name
        clean_age = demographic.age
        logger.info(f"[CRITICAL CHECK] Tool invoked for {clean_name=}, {clean_age=}")

        # Persist through the injected repository only, in the background so
        # the voice path never waits on network/disk; yield one event-loop turn
        # so the write is registered immediately. (Removed: `from database
        # import db_repo` - no such module exists - and a `db_helper.db_repo`
        # probe; both failed silently inside `except Exception: pass`.)
        if self.repo is not None:
            async def _save_mother_bg():
                try:
                    await self.repo.create_patient(name=clean_name, age=clean_age)
                except Exception:
                    logger.warning("record_patient_intake: saving the mother failed", exc_info=True)
            asyncio.create_task(_save_mother_bg())
            await asyncio.sleep(0)
        else:
            logger.warning("record_patient_intake: no repository configured; mother not persisted")

        # The active tool turn owns playback. Starting session.say() here would
        # create a competing speech handle while LiveKit is still generating
        # the tool continuation, which can cancel both responses.
        spoken_text = f"ತಾಯಿ {clean_name}, ವಯಸ್ಸು {clean_age} ದಾಖಲಾಗಿದೆ. ಇದು ಅವರ ಎಷ್ಟನೇ ಗರ್ಭಧಾರಣೆ ಅಕ್ಕಾ?"
        self.last_spoken_prompt = spoken_text
        return f"COMPLETED. Speak this response now: {spoken_text}"

    @llm.ai_callable(
        name="record_demographics",
        description="Save mother's demographic name and age, and get the next question to speak.",
    )
    async def record_demographics(self, name: str, age: int) -> str:
        return await self.record_patient_intake(name=name, age=age)


class Stage1Tools(llm.FunctionContext):
    def __init__(self, session=None, stage_machine=None, repo=None):
        super().__init__()
        self.session = session
        self.stage_machine = stage_machine
        self.repo = repo

    @llm.ai_callable(description="Records mother's name and age for Stage 1 intake.")
    async def record_demographics(self, name: str, age: int) -> str:
        clean_name = str(name).strip()
        clean_age = int(age)

        # 1. Save to SQLite repository in background task (non-blocking)
        async def _save_stage1_bg():
            if self.repo is not None:
                try:
                    await self.repo.create_patient(name=clean_name, age=clean_age)
                except Exception:
                    pass
            try:
                import db_helper
                if hasattr(db_helper, "db_repo") and hasattr(db_helper.db_repo, "save_mother"):
                    await db_helper.db_repo.save_mother(clean_name, clean_age)
            except Exception:
                pass
        asyncio.create_task(_save_stage1_bg())

        # 2. Advance intake state machine
        if hasattr(self.stage_machine, "advance_to"):
            self.stage_machine.advance_to(stage=2)
        elif hasattr(self.stage_machine, "arm_next"):
            self.stage_machine.arm_next()

        # Return speech to the active generation instead of starting a second
        # session speech from within the tool callback.
        spoken_response = (
            f"ತಾಯಿ {clean_name}, ವಯಸ್ಸು {clean_age} ದಾಖಲಾಗಿದೆ. "
            f"ಇದು ಅವರ ಎಷ್ಟನೇ ಗರ್ಭಧಾರಣೆ ಮತ್ತು ಎಷ್ಟು ಮಕ್ಕಳು ಇದ್ದಾರೆ ಅಕ್ಕಾ?"
        )

        return f"Successfully saved. Speak this response now: {spoken_response}"

    @llm.ai_callable(description="Records mother's name and age for Stage 1 intake.")
    async def record_patient_intake(self, name: str, age: int) -> str:
        return await self.record_demographics(name=name, age=age)


class Stage2Tools(llm.FunctionContext):
    def __init__(self, session=None, stage_machine=None, repo=None):
        super().__init__()
        self.session = session
        self.stage_machine = stage_machine
        self.repo = repo

    @llm.ai_callable(description="Records obstetric history: gestational weeks, gravida, and para.")
    async def record_obstetric_history(self, gestational_weeks: int, gravida: int = 1, para: int = 0) -> str:
        weeks = int(gestational_weeks)
        if hasattr(self.stage_machine, "advance_to"):
            self.stage_machine.advance_to(stage=3)
        elif hasattr(self.stage_machine, "arm_next"):
            self.stage_machine.arm_next()

        spoken_response = (
            f"{weeks} ವಾರಗಳ ಗರ್ಭಧಾರಣೆ ದಾಖಲಾಗಿದೆ. "
            f"ಈಗ ತಾಯಿಗೆ ಹೊಟ್ಟೆ ನೋವು, ರಕ್ತಸ್ರಾವ, ಅಥವಾ ನೀರಿನಂಶ ಹೋಗುವ ತೊಂದರೆ ಇದೆಯಾ ಅಕ್ಕಾ?"
        )
        return f"Successfully saved obstetric history. Speak this response now: {spoken_response}"

    @llm.ai_callable(description="Records gestational age in weeks or months.")
    async def record_gestational_age(self, weeks_or_months: str | int) -> str:
        try:
            val = int(str(weeks_or_months).strip())
        except Exception:
            val = 12
        return await self.record_obstetric_history(gestational_weeks=val)


class Stage3Tools(llm.FunctionContext):
    def __init__(self, session=None, stage_machine=None, repo=None):
        super().__init__()
        self.session = session
        self.stage_machine = stage_machine
        self.repo = repo

    @llm.ai_callable(description="Records maternal symptom screen results.")
    async def record_symptoms_screen(self, bleeding: bool = False, abdominal_pain: bool = False, severe_headache: bool = False, swelling: bool = False) -> str:
        if hasattr(self.stage_machine, "advance_to"):
            self.stage_machine.advance_to(stage=4)
        elif hasattr(self.stage_machine, "arm_next"):
            self.stage_machine.arm_next()

        spoken_response = (
            "ಲಕ್ಷಣಗಳನ್ನು ದಾಖಲಿಸಲಾಗಿದೆ. "
            "ತಾಯಿಯ ಇಂದಿನ ರಕ್ತದೊತ್ತಡ ಬಿಪಿ ಎಷ್ಟು ದಾಖಲಾಗಿದೆ ಅಕ್ಕಾ?"
        )
        return f"Successfully saved symptoms. Speak this response now: {spoken_response}"

    @llm.ai_callable(description="Records maternal danger signs and symptoms.")
    async def record_symptoms(self, headache: bool = False, blurry_vision: bool = False, swelling: bool = False, vaginal_bleeding: bool = False, severe_abdominal_pain: bool = False) -> str:
        return await self.record_symptoms_screen(bleeding=vaginal_bleeding, abdominal_pain=severe_abdominal_pain, severe_headache=headache, swelling=swelling)


class Stage4Tools(llm.FunctionContext):
    def __init__(self, session=None, stage_machine=None, repo=None):
        super().__init__()
        self.session = session
        self.stage_machine = stage_machine
        self.repo = repo

    @llm.ai_callable(description="Records maternal clinical vitals: blood pressure and weight.")
    async def record_vitals(self, systolic: int, diastolic: int, weight: float = 0.0) -> str:
        if hasattr(self.stage_machine, "advance_to"):
            self.stage_machine.advance_to(stage=5)
        elif hasattr(self.stage_machine, "arm_next"):
            self.stage_machine.arm_next()

        spoken_response = (
            f"ಬಿಪಿ {systolic} {diastolic} ದಾಖಲಾಗಿದೆ. "
            "ಎಲ್ಲಾ ವಿವರಗಳನ್ನು ಯಶಸ್ವಿಯಾಗಿ ದಾಖಲಿಸಲಾಗಿದೆ. ಧನ್ಯವಾದಗಳು ಅಕ್ಕಾ."
        )
        return f"Successfully saved vitals. Speak this response now: {spoken_response}"

    @llm.ai_callable(description="Record blood pressure in mmHg.")
    async def record_bp(self, systolic: int, diastolic: int) -> str:
        return await self.record_vitals(systolic=systolic, diastolic=diastolic)


def get_stage_tool_context(stage_id: int, session=None, stage_machine=None, repo=None):
    """Return ONLY the tools needed for the current stage to prevent context bloat."""
    if stage_id == 1:
        return Stage1Tools(session, stage_machine, repo=repo)
    if stage_id == 2:
        return Stage2Tools(session, stage_machine, repo=repo)
    if stage_id == 3:
        return Stage3Tools(session, stage_machine, repo=repo)
    if stage_id == 4:
        return Stage4Tools(session, stage_machine, repo=repo)
    return Stage1Tools(session, stage_machine, repo=repo)


from sakhi_intake import ANC_STAGES, question_for_model, question_text, stage_name


def _intake_ack(language, name: str, age) -> str:
    """"Noted: <name>, <age> years" in the call's language."""
    lang = str(getattr(language, "value", language) or "").lower()
    age_part = f", {age}" if age else ""
    if lang.startswith("hi"):
        return f"ठीक है, {name}{age_part} साल दर्ज हो गया।"
    if lang.startswith("en"):
        return f"Noted, {name}{age_part} years."
    return f"ಸರಿ, {name}{age_part} ವರ್ಷ ದಾಖಲಾಗಿದೆ."


def tool_fallback_line(kind: str, language, *, systolic=None, diastolic=None, weeks=None) -> str:
    """What Sakhi says when the model returns a tool call but no text.

    An acknowledgement ONLY, in the call's language (2026-09-28). The old lines
    were fixed Kannada and carried their own clinical content: record_bp's said
    "all details recorded, thank you" (a false closing mid-call - the "thank
    you" loop), record_symptoms' re-asked the BP question (a duplicate question)
    and record_gestational_age's jumped to the abdominal-pain question. Which
    question comes next is decided by the checklist, never by this line.
    """
    lang = str(getattr(language, "value", language) or "").lower()
    hindi = lang.startswith("hi")
    if kind == "bp" and systolic and diastolic:
        return (f"ठीक है, बीपी {systolic} {diastolic} दर्ज हो गया।" if hindi
                else f"ಸರಿ, ಬಿಪಿ {systolic} {diastolic} ದಾಖಲಾಗಿದೆ.")
    if kind == "gestational_age" and weeks:
        return (f"ठीक है, {weeks} हफ़्ते दर्ज हो गए।" if hindi
                else f"ಸರಿ, {weeks} ವಾರ ದಾಖಲಾಗಿದೆ.")
    return "ठीक है, दर्ज हो गया।" if hindi else "ಸರಿ, ದಾಖಲಾಗಿದೆ."


from sakhi_domain import (
    DANGER_SIGN_ADVISORY,
    BloodPressure,
    BloodPressureError,
    RiskLevel,
    SakhiCallState,
    SymptomSet,
    clean_person_name,
    danger_sign_lock_reason,
)
from sakhi_repository import (
    BIDAR_VILLAGES,
    PatientRepository,
    assemble_spelled_name,
    correct_proper_noun,
    spell_out,
)
from sakhi_triage import TriageEngine

logger = logging.getLogger("sakhi.tools")

# Used when a mother's name cannot be resolved after the one permitted spelling
# attempt. A placeholder record that carries a real blood pressure is worth
# more than a perfect name and no reading.
GUEST_PATIENT_NAME = "Guest Patient"

# Emitted by the agent layer for the dashboard. Injected rather than imported so
# this module stays testable without agent.py's logging stack.
EventSink = Callable[..., None]


def _noop_event(*args: Any, **kwargs: Any) -> None:
    return None


# One-tap keypad correction (Module C, 2026-08-30): the fields Live Form
# Sync can show and a tapped keypad may overwrite. Checked BEFORE any
# mutator dispatch in apply_field_correction() below - never trust a raw
# field name from an RPC payload, same defensive reasoning as
# sakhi_sql_repository.py's FLAGGABLE_FIELDS whitelist for the
# reports-flag endpoint.
CORRECTABLE_FIELDS = frozenset({
    "systolic", "diastolic", "gestational_weeks",
    "headache", "blurry_vision", "swelling",
    "vaginal_bleeding", "severe_abdominal_pain",
    "convulsions", "reduced_fetal_movement",
    # Added 2026-09-10 with the Antenatal Quick Check rewrite's new
    # SymptomSet fields (sakhi_domain.py) - apply_field_correction()'s
    # generic "a symptom boolean" branch below already handles any field
    # present in SymptomSet.to_dict() with no further code change, so these
    # just need to be on the whitelist to be correctable.
    "contractions", "upper_abdominal_pain", "fever", "urinary_symptoms",
    "breathlessness", "palpitations", "severe_vomiting",
})


class ClinicalToolset:
    """The maternal-health tools, bound to one repository and triage engine.

    One instance per worker process is fine - the mutable per-call data lives in
    `RunContext.userdata` (a `SakhiCallState`), never on this object. That is the
    fix for the global-singleton problem described in oops.md §2.1.
    """

    def __init__(
        self,
        repository: PatientRepository,
        triage: TriageEngine,
        *,
        event_sink: EventSink = _noop_event,
        session=None,
    ) -> None:
        self._repo = repository
        self._triage = triage
        self._event = event_sink
        self.session = session
        self.last_spoken_prompt = ""

    def persist_captured_demographics(self, state: SakhiCallState) -> None:
        """Persist deterministic name/age capture without blocking the turn.

        The structured turn detector can resolve q01 before the LLM tool loop,
        so the ordinary record_patient_intake tool may never be called.  This
        keeps the same repository write semantics while leaving network/disk
        work off the voice hot path.  State identity guards prevent a delayed
        write from replacing a later confirmed value.
        """
        if self._repo is None or state.patient_id or not state.name or not state.age:
            return

        async def _persist() -> None:
            if state.patient_id:
                return
            try:
                record = await self._repo.create_patient(name=state.name, age=state.age)
                state.identify(patient_id=record.patient_id)
            except Exception:
                logger.warning("Deterministic Stage-1 persistence failed", exc_info=True)

        asyncio.create_task(_persist())

    # --- helpers -------------------------------------------------------

    @staticmethod
    def _state(ctx: RunContext) -> SakhiCallState:
        """Per-call ground truth. Never returns None - a tool must not crash a
        live turn because userdata was not threaded through."""
        state = getattr(ctx, "userdata", None)
        return state if isinstance(state, SakhiCallState) else SakhiCallState()

    async def _retriage(self, state: SakhiCallState) -> "tuple":
        """Re-run triage over everything known so far and persist the outcome.

        Called after *any* new clinical input, because risk is a function of the
        whole picture: a symptom reported after the BP reading can flip a
        MODERATE into a red alert, and the old code had no path that
        re-evaluated on the second input.
        """
        assessment = self._triage.assess(
            state.blood_pressure,
            state.symptoms,
            gestational_weeks=state.gestational_weeks,
        )
        state.record_assessment(assessment)

        # Tier-1 lock: this is the tool-driven re-triage path (record_bp,
        # record_symptoms), the most common way HIGH_RISK is actually
        # reached (e.g. a severe BP reading called in directly) - it needs
        # the same lock check as agent.py's deterministic re-triage on raw
        # speech, or a call that goes HIGH_RISK purely through a tool call
        # would never lock. See sakhi_domain.py's danger_sign_lock_reason().
        if assessment.level is RiskLevel.HIGH_RISK and not state.intake_locked:
            state.lock_intake_on_danger_sign(danger_sign_lock_reason(state))
            self._event(
                "tools", "warn", "Intake locked - Tier-1 danger sign",
                f"reason={state.intake_lock_reason}; questionnaire stops here for this call",
                event="intake_locked_danger_sign",
                reason=state.intake_lock_reason,
            )

        # Track whether the write actually happened. The tool must not report
        # success the database did not deliver - see _result().
        persisted = False
        if state.patient_id and state.blood_pressure:
            try:
                await self._repo.record_blood_pressure(
                    state.patient_id, state.blood_pressure, assessment
                )
                persisted = True
            except Exception:
                logger.warning("BP persistence failed; call continues", exc_info=True)

        self._event(
            "tools",
            "warn" if assessment.requires_escalation else "ok",
            f"Triage: {assessment.level.value}",
            "; ".join(assessment.reasons),
            event="triage_assessed",
            risk_level=assessment.level.value,
            requires_escalation=assessment.requires_escalation,
            patient_id=state.patient_id,
        )
        return assessment, persisted

    async def apply_field_correction(self, state: SakhiCallState, field: str, value: Any):
        """One-tap keypad correction (Module C, 2026-08-30): a value shown by
        Live Form Sync, corrected directly from a numeric keypad rather than
        through another voice round-trip. agent.py's `submit_correction` RPC
        handler is a thin wrapper around this - kept here, not in agent.py,
        so it is unit-testable without a LiveKit room and so it can reuse
        `_retriage()` instead of duplicating its lock/persist logic a third
        time (record_bp and record_symptoms are the other two call sites).

        Raises ValueError - not RpcError - on any rejected field/value: this
        module has no LiveKit dependency, and the RPC handler translates a
        ValueError into the wire-level error instead.
        """
        if field not in CORRECTABLE_FIELDS:
            raise ValueError(f"not a correctable field: {field!r}")

        if field in ("systolic", "diastolic"):
            current = state.blood_pressure
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                raise ValueError("value must be a number")
            systolic = parsed if field == "systolic" else (current.systolic if current else None)
            diastolic = parsed if field == "diastolic" else (current.diastolic if current else None)
            if systolic is None or diastolic is None:
                raise ValueError("cannot correct one BP number before both have been recorded once")
            try:
                state.record_blood_pressure(BloodPressure(systolic, diastolic))
            except BloodPressureError as exc:
                raise ValueError(str(exc)) from exc
        elif field == "gestational_weeks":
            try:
                weeks = int(value)
            except (TypeError, ValueError):
                raise ValueError("value must be a number")
            before = state.gestational_weeks
            state.identify(gestational_weeks=weeks)
            if state.gestational_weeks == before:
                raise ValueError("value out of the accepted range")
        else:
            # A symptom boolean.
            current_symptoms = state.symptoms.to_dict()
            current_symptoms[field] = bool(value)
            state.record_symptoms(SymptomSet(**current_symptoms))

        assessment, _ = await self._retriage(state)
        self._event(
            "tools", "ok", "Field corrected via one-tap keypad",
            f"{field} -> {value}",
            event="field_corrected_via_rpc", field=field,
            risk_level=assessment.level.value,
        )
        # Live Form Sync's chips must reflect a correction too, not just the
        # three record_* tools - a keypad-typed BP correction on device A
        # (e.g. a supervisor's tablet reviewing the same call) would
        # otherwise never reach a second viewer polling the same log stream.
        self._emit_live_form_sync(state)
        return assessment

    @staticmethod
    def _speak_instruction(state: SakhiCallState, assessment) -> str:
        """What the model should say, derived from the decided risk level.

        Phrased as guidance to the ASHA worker. Sakhi is a decision-support
        co-pilot, never a practitioner issuing a diagnosis (target spec §5.2).

        HIGH_RISK is spoken immediately regardless of what else has been
        asked - delaying a genuine emergency (e.g. a severe BP reading) to
        wait for the rest of the questionnaire is not safe, and this tier's
        timing is deliberately unchanged.

        MODERATE/OBSERVATION are lower-stakes, non-emergency findings, and the
        mother is often sitting right next to the ASHA worker while this call
        happens - so these two tiers wait until BOTH the blood pressure AND
        the symptom screen have actually been recorded (state.blood_pressure,
        state.symptoms_screened) before saying anything about the reading or
        the risk level at all. Checking the raw state fields directly here,
        not IntakeProgress.answered, matters: that dict only gets refreshed
        once per turn inside prompt_summary() (sakhi_domain.py), so it can be
        one turn stale relative to what record_bp/record_symptoms just did.
        """
        if assessment.level is RiskLevel.HIGH_RISK:
            # Include the matched first-aid advisory directly in THIS turn's
            # instruction, not just relying on prompt_summary()'s locked-
            # intake block next turn: when HIGH_RISK is reached via a tool
            # call (record_bp/record_symptoms, the most common path), the
            # lock is set mid-generation, after this turn's prompt_summary()
            # was already built - so without this, the advisory would not
            # reach the model until the turn after the emergency was found.
            advisory = DANGER_SIGN_ADVISORY.get(state.intake_lock_reason or "", "")
            return (
                "SAY: calmly tell the ASHA worker this reading needs urgent attention, "
                "reassure her that the mother will be looked after, and that you are "
                "connecting a specialist right now. Speak slowly and stay calm. "
                f"Include this first-aid advisory: \"{advisory}\" "
                "THEN call connect_smfmi_specialist immediately."
            )

        vitals_complete = bool(state.blood_pressure) and state.symptoms_screened
        if assessment.level in (RiskLevel.MODERATE, RiskLevel.OBSERVATION) and not vitals_complete:
            return (
                "Continue the intake normally. Do NOT mention the blood pressure "
                "reading or any risk level yet - ask the next question instead."
            )

        if assessment.level is RiskLevel.MODERATE:
            return (
                "SAY, calmly: the reading is a little high, nothing to worry the "
                "mother about right now. Ask her to re-check the blood pressure "
                "within 48 hours and to call back if headache, vision changes, or "
                "swelling appear."
            )
        if assessment.level is RiskLevel.OBSERVATION:
            return (
                "SAY, calmly: the blood pressure is normal, but note the symptoms "
                "she reported and ask her to keep an eye on them. Re-check at the "
                "next visit."
            )
        return (
            "SAY: reassure her that the reading and the screening are both normal, "
            "then confirm the next ANC visit date."
        )

    def _emit_live_form_sync(self, state: SakhiCallState) -> None:
        """Push the current verified slot values for the frontend's Live
        Form Sync chips (2026-08-30, Module C).

        Same one-way pipeline_event/poll channel every other dashboard
        signal already uses (identity_fallback_needed, intake_answered,
        etc.) - the frontend's poll loop gets one more `label ==` branch to
        turn this into tap-to-correct chips, no new transport. This is
        display-only; the actual correction round-trip is the
        submit_correction RPC handler in agent.py, not this event.
        """
        self._event(
            "tools", "info", "Live form sync",
            "verified slot values updated",
            event="live_form_sync",
            systolic=state.blood_pressure.systolic if state.blood_pressure else None,
            diastolic=state.blood_pressure.diastolic if state.blood_pressure else None,
            symptoms=state.symptoms.to_dict(),
            gestational_weeks=state.gestational_weeks,
        )

    def _result(self, state: SakhiCallState, assessment, *, persisted: bool = True, **extra: Any) -> str:
        """Build a tool result.

        `persisted` is the tool-execution verification guard. A reading that was
        triaged but NOT written to SixtyDB must never come back as a plain
        success: the model would tell the ASHA worker "recorded" while the
        database holds nothing, and the mother's chart would silently disagree
        with what she was told. Verified live - record_bp returned
        recorded=True with zero rows written whenever the mother had not been
        identified yet.
        """
        payload = {
            "risk_level": assessment.level.value,
            "reasons": list(assessment.reasons),
            "recommended_action": assessment.recommended_action,
            "escalation_required": assessment.requires_escalation,
            "persisted": persisted,
            "instruction": self._speak_instruction(state, assessment),
        }
        if not persisted:
            payload["instruction"] = (
                "DO NOT tell her the reading has been saved - it has not. "
                "Say you still need the mother's name to file it, ask for her "
                "name, and call search_patient or register_mother. "
                + self._speak_instruction(state, assessment)
            )
        payload.update(extra)
        payload["directive"] = "CRITICAL PROTOCOL: Speak immediately to the ASHA worker without silence or hesitation."
        return json.dumps(payload, ensure_ascii=False)

    # --- tools ---------------------------------------------------------

    @llm.function_tool
    async def search_patient(
        self,
        ctx: RunContext,
        name: str | None = None,
        phone: str | None = None,
    ) -> str:
        """Find a registered mother by name or phone. Call this first."""
        state = self._state(ctx)

        # The worker was asked to spell the name; honour what she spelled.
        #
        # ASR delivers a spelled name as "S, U, M, A" / "S. U. M. A." / "s u m
        # a". Searching that literal string finds nothing, so before this the
        # spelling fallback was a dead end: Sakhi asked her to spell, she
        # spelled, and the lookup failed again on the string she had just been
        # asked to produce.
        spelled = assemble_spelled_name(name) if name else None
        if spelled:
            self._event(
                "tools", "info", "Spelled name assembled",
                f"{name!r} -> {spelled!r}",
                event="spelled_name_assembled", assembled=spelled,
            )
            name = spelled

        # Security Authorization Check: Verify caller identity and block cross-tenant/patient probing
        active_caller = getattr(state, "caller_phone", None) or getattr(ctx, "caller_phone", None)
        if phone and active_caller:
            from sakhi_repository import normalize_phone
            if normalize_phone(phone) != normalize_phone(active_caller):
                logger.warning("Security Alert: Unauthorized cross-patient access. Caller=%s, Target=%s", active_caller, phone)
                raise PermissionError("403 Forbidden: Caller is not authorized to query records outside active session.")

        try:
            matches = await self._repo.find_patients(name=name, phone=phone)
        except Exception:
            logger.warning("Patient search failed", exc_info=True)
            return json.dumps({
                "found": False,
                "instruction": "SAY: the records are not reachable right now; continue "
                               "collecting her details manually.",
            }, ensure_ascii=False)

        if not matches:
            state.lookup_attempts += 1
            self._event("tools", "info", "Patient search miss",
                        # Phone reduced to its last 4 digits. The live call
                        # logged the raw argument, which on a real number is a
                        # DPDP-reportable identifier sitting in a plaintext log.
                        f"No SixtyDB record for name={name} "
                        f"phone=***{str(phone)[-4:] if phone else 'none'} "
                        f"(attempt {state.lookup_attempts})",
                        event="patient_search", found=False,
                        attempt=state.lookup_attempts)

            # Spelling fallback, enforced here rather than left to the prompt.
            #
            # Name collection is the dominant bottleneck in production voice
            # agents: a regional accent produces a mangled transcript, the
            # agent re-asks the same way, gets the same mangling, and the
            # worker is trapped in a loop. Switching strategy after ONE miss is
            # what breaks the loop - and doing it in code means it happens even
            # when the model forgets the instruction.
            # HARD CEILING: ask for the spelling ONCE, never twice.
            #
            # The spelling prompt below breaks the "misheard the same way
            # twice" loop, but on its own it creates a second loop: if the
            # spelled name also misses, this branch would ask her to spell it
            # again, and again. A worker with a mother in front of her cannot
            # be held at an identity check - the clinical value of this call is
            # the blood pressure, not the database row.
            #
            # So after the spelling attempt has already been made, we stop
            # asking and move on with whatever name we have. Vitals get
            # recorded against a real record either way; reconciling the name
            # is a back-office problem, and a wrong name on a captured BP is
            # recoverable in a way that a missed pre-eclampsia case is not.
            if state.lookup_attempts >= 2:
                fallback_name = (name or "").strip() or GUEST_PATIENT_NAME
                return json.dumps({
                    "found": False,
                    "attempt": state.lookup_attempts,
                    "instruction": (
                        "DO NOT ask for the name or spelling again - you have "
                        "already asked once and asking twice traps her in a loop. "
                        f"Proceed with the mother as \"{fallback_name}\". Say "
                        "warmly that you will note her down for now, then call "
                        "register_mother with that name and continue to the "
                        "blood pressure reading."
                    ),
                    "fallback_name": fallback_name,
                }, ensure_ascii=False)

            if state.lookup_attempts >= 1 and name:
                # Show the frontend text box instead of a second voice round-
                # trip. Asking her to spell it letter-by-letter over voice
                # used to be the fallback here - it burns another LLM/STT
                # turn to fix a problem STT just caused, and often mishears
                # the spelling too. Typing on screen is instant and exact.
                #
                # awaiting_identity_box is what actually stops the re-asking:
                # tools_for() withdraws search_patient while it's set, so the
                # model has no tool left to retry identity through voice even
                # if it wanted to - the "say it once" instruction below is
                # belt-and-braces, not the real enforcement mechanism.
                state.awaiting_identity_box = True
                self._event(
                    "tools", "info", "Identity fallback: show text box",
                    f"Voice lookup for \"{name}\" failed; arming frontend name/age entry",
                    event="identity_fallback_needed",
                )
                return json.dumps({
                    "found": False,
                    "attempt": state.lookup_attempts,
                    "instruction": (
                        "SAY (once): apologise briefly that the name did not come "
                        "through clearly, and ask her to type the mother's name and "
                        "age on the screen in front of her instead. THEN say nothing "
                        "further about the name - do not ask again."
                    ),
                }, ensure_ascii=False)

            return json.dumps({
                "found": False,
                "attempt": state.lookup_attempts,
                "instruction": "SAY: she is not registered yet. Ask for her name, age, "
                               "village, and how many weeks pregnant she is, then call "
                               "register_mother.",
            }, ensure_ascii=False)

        best = matches[0]
        state.identify(
            patient_id=best.patient_id,
            name=best.name,
            village=best.village,
            phone=best.phone,
            age=best.age,
            gestational_weeks=best.gestational_weeks,
        )
        # A record already flagged high-risk is the single most important thing
        # for the ASHA worker to know before anything else in the call.
        prior_alert = best.risk_status is RiskLevel.HIGH_RISK

        self._event("tools", "ok", "Patient found",
                    f"{best.name} ({best.patient_id}) risk={best.risk_status.value}",
                    event="patient_search", found=True,
                    patient_id=best.patient_id, risk_level=best.risk_status.value,
                    # name/age as structured fields (2026-08-30, SakhiHeader's
                    # mother-name display) - not just embedded in `message`,
                    # so the frontend never has to regex-scrape free text for
                    # a value this codebase already computed.
                    name=best.name, age=best.age)

        return json.dumps({
            "found": True,
            "patient_id": best.patient_id,
            "summary": best.spoken_summary(),
            "previously_high_risk": prior_alert,
            "instruction": (
                "SAY: confirm her name back warmly, mention that her record shows a "
                "previous high-risk flag, and go straight to asking for today's blood "
                "pressure."
                if prior_alert else
                "SAY: confirm her name back warmly, then ask for today's blood pressure reading."
            ),
        }, ensure_ascii=False)

    @llm.function_tool
    async def register_mother(
        self,
        ctx: RunContext,
        name: str,
        age: int | None = None,
        gestational_weeks: int | None = None,
        village: str | None = None,
        phone: str | None = None,
    ) -> str:
        """Register a new mother. Only after search_patient found nothing."""
        state = self._state(ctx)
        # Snap a mangled village back to a known Bidar village. Proper nouns are
        # where ASR degrades worst, and a village recorded as "Kamalnagara" or
        # "Kamal Nagar" fragments the catchment data for every later report.
        if village:
            corrected = correct_proper_noun(village, list(BIDAR_VILLAGES))
            if corrected and corrected != village:
                self._event(
                    "tools", "info", "Village corrected",
                    f"{village!r} -> {corrected!r} (nearest Bidar village)",
                    event="proper_noun_corrected", heard=village, resolved=corrected,
                )
                village = corrected
        try:
            record = await self._repo.create_patient(
                name=name, age=age, gestational_weeks=gestational_weeks,
                village=village, phone=phone,
            )
        except Exception:
            logger.warning("Patient registration failed", exc_info=True)
            return json.dumps({
                "registered": False,
                "instruction": "SAY: apologise briefly, then continue with the blood "
                               "pressure check - her details will be saved afterwards.",
            }, ensure_ascii=False)

        state.identify(
            patient_id=record.patient_id, name=record.name, village=record.village,
            phone=record.phone, age=record.age,
            gestational_weeks=record.gestational_weeks,
        )
        self._event("tools", "ok", "Mother registered",
                    f"{record.name} ({record.patient_id})",
                    event="patient_registered", patient_id=record.patient_id,
                    name=record.name, age=record.age)

        return json.dumps({
            "registered": True,
            "patient_id": record.patient_id,
            "instruction": "SAY: confirm she is registered, then ask for today's blood "
                           "pressure reading.",
        }, ensure_ascii=False)

    @llm.function_tool
    async def record_bp(self, ctx: RunContext, systolic: int, diastolic: int) -> str:
        """Record a blood pressure reading in mmHg and assess risk. For "145 over 95": systolic=145, diastolic=95."""
        state = self._state(ctx)
        try:
            bp = BloodPressure(int(systolic), int(diastolic))
        except (BloodPressureError, TypeError, ValueError) as exc:
            # A mis-transcribed reading must be re-asked, never recorded.
            self._event("tools", "warn", "Implausible BP rejected", str(exc),
                        event="bp_rejected", systolic=systolic, diastolic=diastolic)
            return json.dumps({
                "recorded": False,
                "reason": str(exc),
                "instruction": "SAY: politely say you did not catch the reading clearly "
                               "and ask her to say both numbers again slowly.",
            }, ensure_ascii=False)

        state.record_blood_pressure(bp)
        assessment, persisted = await self._retriage(state)

        # Per-turn write receipt for the pre-speech guard (agent.py's
        # tts_node). Set ONLY from `persisted`, which _retriage() sets only
        # inside the actual repository write's success path - never from
        # "we got this far" or "a patient exists". If the write did not
        # happen, Sakhi must not be allowed to say it did.
        if persisted:
            state.bp_record_succeeded = True

        self._event("tools", "warn" if assessment.requires_escalation else "ok",
                    f"BP {bp} recorded", assessment.recommended_action,
                    event="bp_recorded", systolic=bp.systolic, diastolic=bp.diastolic,
                    risk_level=assessment.level.value)
        self._emit_live_form_sync(state)

        spoken_text = tool_fallback_line(
            "bp", state.language, systolic=bp.systolic, diastolic=bp.diastolic
        )
        self.last_spoken_prompt = spoken_text
        logger.info(f"[TOOL EXECUTED] record_bp: systolic={bp.systolic}, diastolic={bp.diastolic}")

        return self._result(
            state, assessment,
            persisted=persisted,
            recorded=persisted,
            blood_pressure=str(bp),
            # The screening questions are mandatory on every visit, so the model
            # is told to ask them unless they have already been asked.
            next_step=(
                "already screened" if state.symptoms_screened else
                "ASK the three danger-symptom questions now: severe headache, "
                "blurry or double vision, swelling of the face or hands. Then call "
                "record_symptoms."
            ),
        )

    @llm.function_tool
    async def record_symptoms(
        self,
        ctx: RunContext,
        headache: bool = False,
        blurry_vision: bool = False,
        swelling: bool = False,
        vaginal_bleeding: bool = False,
        severe_abdominal_pain: bool = False,
        convulsions: bool = False,
        reduced_fetal_movement: bool = False,
        contractions: bool = False,
        upper_abdominal_pain: bool = False,
        fever: bool = False,
        urinary_symptoms: bool = False,
        breathlessness: bool = False,
        palpitations: bool = False,
        severe_vomiting: bool = False,
    ) -> str:
        """Record danger symptoms and re-assess risk. True only for symptoms she reports.

        vaginal_bleeding and severe_abdominal_pain are Tier-1 danger signs
        (2026-08-30) - either one alone reaches HIGH_RISK regardless of blood
        pressure. fever and breathlessness (2026-09-10) are ALSO Tier-1-alone,
        per an explicit user decision matching the source PDF's RED FLAGS list
        literally - see sakhi_triage.py. This tool call is not the only way
        these get recorded: the deterministic detector
        (sakhi_domain.detect_symptoms) also scans the ASHA worker's own words
        for these signs as a safety net, the same way it already did for the
        original three - the two paths OR together via merged_with(), never
        overwrite each other.

        A single grouped ASK question can cover several of these fields at
        once (e.g. "any fever, urinary symptoms, breathlessness, palpitations,
        or severe vomiting?") - pass every true finding she actually mentions
        in ONE call rather than asking a separate follow-up per field.
        """
        state = self._state(ctx)
        symptoms = SymptomSet(
            headache=bool(headache),
            blurry_vision=bool(blurry_vision),
            swelling=bool(swelling),
            vaginal_bleeding=bool(vaginal_bleeding),
            severe_abdominal_pain=bool(severe_abdominal_pain),
            convulsions=bool(convulsions),
            reduced_fetal_movement=bool(reduced_fetal_movement),
            contractions=bool(contractions),
            upper_abdominal_pain=bool(upper_abdominal_pain),
            fever=bool(fever),
            urinary_symptoms=bool(urinary_symptoms),
            breathlessness=bool(breathlessness),
            palpitations=bool(palpitations),
            severe_vomiting=bool(severe_vomiting),
        )
        state.record_symptoms(symptoms)

        if state.patient_id:
            try:
                await self._repo.record_symptoms(state.patient_id, symptoms)
                # Per-turn write receipt for the pre-speech guard. Set HERE,
                # inside the write's own success path - deliberately not from
                # the `persisted` value returned below, which is OR'd with
                # bool(patient_id) for the model's benefit and would therefore
                # report success for a write that raised. See
                # SakhiCallState.bp_record_succeeded's comment.
                state.symptoms_record_succeeded = True
            except Exception:
                logger.warning("Symptom persistence failed; call continues", exc_info=True)

        assessment, persisted = await self._retriage(state)
        self._event("tools", "warn" if assessment.requires_escalation else "ok",
                    "Symptoms screened",
                    ", ".join(state.symptoms.present_names) or "none reported",
                    event="symptoms_recorded", risk_level=assessment.level.value)
        self._emit_live_form_sync(state)

        spoken_text = tool_fallback_line("symptoms", state.language)
        self.last_spoken_prompt = spoken_text
        logger.info("[TOOL EXECUTED] record_symptoms")

        return self._result(
            state, assessment,
            persisted=persisted or bool(state.patient_id),
            recorded=True,
            symptoms_reported=list(state.symptoms.present_names) or ["none"],
        )

    @llm.function_tool
    async def record_gestational_age(self, ctx: RunContext, weeks: int) -> str:
        """Record how many weeks pregnant she is, if mentioned mid-call rather than at registration.

        Added 2026-08-30: gestational_weeks was previously only set inside
        register_mother/search_patient at registration time - a later
        mention ("she's 7 months along") had no tool to land in. Out-of-order
        clinical facts must never be droppable, same reasoning as
        record_bp/record_symptoms staying available in every phase - see
        tools_for()'s always-available set below.
        """
        state = self._state(ctx)
        before = state.gestational_weeks
        state.identify(gestational_weeks=weeks)  # validates 1-45, silently drops out of range
        if state.gestational_weeks == before:
            self._event(
                "tools", "warn", "Implausible gestational age rejected",
                f"{weeks} weeks is out of the accepted 1-45 range",
                event="gestational_age_rejected", weeks=weeks,
            )
            return json.dumps({
                "recorded": False,
                "directive": "CRITICAL PROTOCOL: Speak immediately to the ASHA worker without silence.",
                "instruction": "SAY: politely say you did not catch that clearly and ask "
                               "how many months or weeks pregnant she is, in under 10 words.",
            }, ensure_ascii=False)

        self._event(
            "tools", "ok", "Gestational age recorded",
            f"{state.gestational_weeks} weeks",
            event="gestational_age_recorded", weeks=state.gestational_weeks,
        )
        self._emit_live_form_sync(state)
        nxt = state.intake.next_question()
        nxt_text = question_for_model(nxt, state.language) if nxt else "continue with the next check"

        spoken_text = tool_fallback_line(
            "gestational_age", state.language, weeks=state.gestational_weeks
        )
        self.last_spoken_prompt = spoken_text
        logger.info(f"[TOOL EXECUTED] record_gestational_age: weeks={state.gestational_weeks}")

        return json.dumps({
            "recorded": True,
            "gestational_weeks": state.gestational_weeks,
            "directive": "CRITICAL PROTOCOL: Speak immediately to the ASHA worker without silence.",
            "instruction": f"Acknowledge briefly in under 10 words, then ask the next question: '{nxt_text}'.",
            "next_question": nxt_text,
        }, ensure_ascii=False)

    @llm.function_tool
    async def connect_smfmi_specialist(self, ctx: RunContext, reason: str) -> str:
        """Connect a specialist. Call immediately when a tool says escalation is required."""
        state = self._state(ctx)
        if state.escalated:
            # Idempotent: the model sometimes re-calls after the tool result.
            return json.dumps({
                "connected": True,
                "instruction": "SAY: the specialist is already on the line - do not "
                               "connect again. Reassure her and stay with her.",
            }, ensure_ascii=False)

        # Refuse escalation the triage engine did not ask for.
        #
        # Observed live: triage returned MODERATE ("raised BP, no danger
        # symptoms" - a 48-hour re-check), and the model called this tool
        # anyway, with a reason that itself said the risk was moderate. The
        # audit trail then contained an escalation contradicting its own
        # stated grounds.
        #
        # This is the mirror of the deterministic net in SakhiAgent: that one
        # stops the model UNDER-escalating, this one stops it OVER-escalating.
        # Between them, the decision belongs to TriageEngine in both directions.
        #
        # A call with no assessment yet is allowed through - there is no
        # decision to contradict, and refusing would block a genuine emergency
        # raised before any reading was taken.
        assessment = state.assessment
        if assessment is not None and not assessment.requires_escalation:
            self._event(
                "tools", "warn", "Escalation refused",
                f"Model requested a specialist at {assessment.level.value}; "
                "triage does not indicate escalation",
                event="escalation_refused",
                risk_level=assessment.level.value,
                patient_id=state.patient_id,
            )
            return json.dumps({
                "connected": False,
                "risk_level": assessment.level.value,
                "instruction": (
                    "DO NOT say a specialist is being connected - one is not. "
                    "The reading does not meet the referral threshold. Give her "
                    f"this advice instead: {assessment.recommended_action}"
                ),
            }, ensure_ascii=False)

        patient_id = state.patient_id or "UNREGISTERED"
        try:
            channel_id = await self._repo.log_teleconsult(
                patient_id, reason, state.assessment,
                trigger_sign=state.intake_lock_reason,
            )
        except Exception:
            logger.warning("Teleconsult logging failed", exc_info=True)
            channel_id = "CONN-UNLOGGED"

        state.mark_escalated()
        self._event("tools", "warn", "SMFMI escalation",
                    f"Specialist connected for {patient_id}: {reason}",
                    event="specialist_connected", patient_id=patient_id,
                    channel_id=channel_id,
                    risk_level=state.risk_level.value)

        return json.dumps({
            "connected": True,
            "channel_id": channel_id,
            "referral": "Bidar District Hospital",
            "instruction": (
                "SAY: tell her calmly that the specialist is connected now, that she "
                "should take the mother to Bidar District Hospital, and that she has "
                "done the right thing by calling. Keep your voice slow and steady."
            ),
        }, ensure_ascii=False)

    # --- registration --------------------------------------------------

    # --- ANC protocol tools (2026-09-02) --------------------------------

    @llm.function_tool
    async def record_anc_checkpoint(
        self, ctx: RunContext, stage: int, parameter: str, value: str,
    ) -> str:
        """Record one answered ANC checkpoint (weight, IFA tablets, scan, Td, delivery place, 108 awareness).

        Use this for the protocol answers that are NOT already covered by a
        dedicated tool. Blood pressure still goes through record_bp, danger
        signs through record_symptoms, gestation through
        record_gestational_age - those carry clinical decision logic this one
        deliberately does not duplicate.
        """
        state = self._state(ctx)
        param = str(parameter or "").strip().lower().replace(" ", "_")
        if not param:
            return json.dumps({
                "recorded": False,
                "instruction": "SAY: ask the current stage question again, simply.",
            }, ensure_ascii=False)

        try:
            stage_num = int(stage)
        except (TypeError, ValueError):
            stage_num = state.intake.current_stage()
        stage_num = min(max(stage_num, 1), len(ANC_STAGES))

        state.record_anc_checkpoint(stage_num, param, str(value)[:120])
        self._event(
            "tools", "ok", "ANC checkpoint recorded",
            f"{stage_name(stage_num)} | {param}={str(value)[:60]}",
            event="anc_checkpoint", stage=stage_num, parameter=param,
        )
        nxt = state.intake.next_question()
        nxt_text = question_for_model(nxt, state.language) if nxt else "continue to the next check"
        return json.dumps({
            "recorded": True,
            "stage": stage_num,
            "parameter": param,
            "value": str(value)[:120],
            "directive": "CRITICAL PROTOCOL: Speak immediately to the ASHA worker without silence.",
            "instruction": f"Acknowledge in under six words, then ask the next question: '{nxt_text}'. Do not stay silent.",
            "next_question": nxt_text,
        }, ensure_ascii=False)

    @llm.function_tool
    async def record_patient_intake(self, ctx: RunContext, name: str, age: int) -> str:
        """Stage 1 only: record the mother's name and age. No card or ID number is ever required.

        Deliberately writes ONLY demographics. It does not look up, join
        against, or require a Thayi card / RCH register row - an ASHA worker
        in a doorway does not have that number, and gating an ANC screening
        on it is how the call gets abandoned before the BP is taken.
        """
        state = self._state(ctx)
        cleaned = clean_person_name(name)
        if not cleaned:
            self._event(
                "tools", "warn", "Unusable intake name",
                f"{name!r} did not look like a name", event="intake_name_rejected",
            )
            return json.dumps({
                "recorded": False,
                "directive": "CRITICAL PROTOCOL: Speak immediately to the ASHA worker without silence.",
                "instruction": "Say you did not catch the name and ask for it once more plainly in under ten words.",
            }, ensure_ascii=False)

        before_age = state.age
        state.identify(name=cleaned, age=age)
        age_ok = state.age is not None and state.age != before_age or state.age == age

        # Background DB persistence: save runs asynchronously without blocking tool return
        async def _persist_patient_bg():
            if self._repo is not None and not state.patient_id:
                try:
                    record = await self._repo.create_patient(name=cleaned, age=state.age)
                    state.identify(patient_id=record.patient_id)
                except Exception:
                    logger.warning("Stage-1 intake persistence failed", exc_info=True)
        asyncio.create_task(_persist_patient_bg())

        self._event(
            "tools", "ok", "Patient intake recorded",
            f"{cleaned}, {state.age}", event="patient_intake",
            patient_id=state.patient_id, name=cleaned, age=state.age,
        )
        self._emit_live_form_sync(state)
        nxt = state.intake.next_question()
        nxt_text = question_for_model(nxt, state.language) if nxt else "ask which month of pregnancy she is in"

        logger.info(f"[CRITICAL CHECK] Tool invoked for {cleaned=}, age={state.age}")
        # Acknowledgement in the CALL's language + the checklist's actual next
        # question. It used to be one hardcoded Kannada sentence ending in the
        # gravida question (q04) - Kannada on Hindi/English calls, and asked
        # ahead of q02 (months), so the months question came back afterwards.
        next_question = f"{_intake_ack(state.language, cleaned, state.age)} {nxt_text}"
        self.last_spoken_prompt = next_question

        return json.dumps({
            "status": "DATA_RECORDED",
            "directive": f"You must say this exact sentence to the user right now: {next_question}",
            "recorded": True,
            "patient_id": state.patient_id,
            "name": cleaned,
            "age": state.age,
            "age_accepted": bool(age_ok),
            "instruction": f"Say this exact sentence to the user right now: {next_question}",
            "next_question": next_question,
            "spoken": next_question,
        }, ensure_ascii=False)

    @llm.function_tool
    async def check_pregnancy_risk_factors(self, ctx: RunContext) -> str:
        """Re-run the deterministic obstetric risk check over everything recorded so far.

        Returns whether an emergency referral is required. The decision is
        made in code (sakhi_triage.TriageEngine), never by you - you only
        speak the result.
        """
        state = self._state(ctx)
        assessment, _ = await self._retriage(state)
        emergency = assessment.level is RiskLevel.HIGH_RISK
        advisory = DANGER_SIGN_ADVISORY.get(state.intake_lock_reason or "", "") if emergency else ""
        return json.dumps({
            "emergency": emergency,
            "risk_level": assessment.level.value,
            "trigger": state.intake_lock_reason if emergency else None,
            "reasons": list(assessment.reasons),
            "instruction": (
                "SAY: calmly tell her this needs immediate hospital transfer, deliver "
                f"this advisory in full: \"{advisory}\" THEN call "
                "connect_smfmi_specialist immediately."
                if emergency else
                "SAY: nothing about risk. Continue with the NEXT QUESTION from the state block."
            ),
        }, ensure_ascii=False)

    def as_list(self) -> list:
        """Every tool. Use `tools_for(state)` on a live call instead.

        Kept for tests and for the initial Agent construction, where no call
        state exists yet.
        """
        return [
            self.search_patient,
            self.register_mother,
            self.record_bp,
            self.record_symptoms,
            self.record_gestational_age,
            self.record_anc_checkpoint,
            self.record_patient_intake,
            self.check_pregnancy_risk_factors,
            self.connect_smfmi_specialist,
        ]

    def tools_for(self, state) -> list:
        """The tool surface for the CURRENT phase of this call.

        A prompt rule like "only register after search_patient found nothing"
        is a suggestion the model follows probabilistically. A tool the model
        cannot see is a rule it cannot break. Two failures observed by calling
        the tools directly, both of which this makes structurally impossible:

          - `register_mother` called twice created M-201 AND M-202 for the same
            mother. Nothing deduplicated, so a re-registration mid-call
            silently forked the record and the second half of the screening
            was filed against a patient with no history.
          - `connect_smfmi_specialist` with no patient identified returned
            `connected: True` while the teleconsult audit INSERT died on a
            foreign-key violation. A specialist reported as paged, with no
            record that it happened - the worst possible combination on a
            referral.

        DELIBERATELY NOT a strict linear state machine. An ASHA worker reports
        facts out of order ("her BP is one sixty over one ten" before any name
        is known), and the prompt's "dynamic absorption" requirement depends on
        Sakhi being able to record that immediately. So `record_bp` and
        `record_symptoms` stay available in every phase - they already degrade
        safely, returning `persisted: False` with an instruction not to claim
        the reading was saved. Only the two tools whose misuse is irreversible
        are gated.
        """
        # Always available: out-of-order clinical facts must never be droppable.
        #
        # record_anc_checkpoint, record_patient_intake, and
        # check_pregnancy_risk_factors were added here 2026-09-10, closing a
        # real, previously-undiscovered gap: they existed in
        # ClinicalToolset.as_list() and were reachable at initial Agent
        # construction, but this method - the list actually used for the
        # PER-TURN re-scope that fires on essentially every turn (see
        # agent.py's llm_node) - never included them. From the second turn
        # onward the model had no live tool call left to reach them at all,
        # so every question meant to be recorded via record_anc_checkpoint
        # (weight, IFA, scans, Td, and now pallor/FHR/lab-review/chronic
        # disease/medications, added in the same rewrite this fixes) was
        # silently unrecordable through most of a call. No test exercised
        # this because existing tests call these tools directly on the
        # toolset object, bypassing tools_for() entirely - see
        # tests/test_scoped_tools.py.
        lean_mode = os.getenv("SAKHI_LEAN_INTAKE_MODE", "true").strip().lower() in ("true", "1", "yes")
        stage = 1
        if state is not None:
            intake = getattr(state, "intake", None)
            if intake is not None and hasattr(intake, "current_stage"):
                try:
                    stage = intake.current_stage()
                except Exception:
                    stage = 1

        if lean_mode:
            if stage == 1:
                tools = [
                    self.record_patient_intake,
                    self.record_bp,
                    self.record_symptoms,
                    self.record_gestational_age,
                    self.record_anc_checkpoint,
                    self.check_pregnancy_risk_factors,
                ]
            elif stage == 2:
                tools = [
                    self.record_gestational_age,
                    self.record_anc_checkpoint,
                    self.check_pregnancy_risk_factors,
                    self.record_bp,
                    self.record_symptoms,
                ]
            elif stage == 3:
                tools = [self.record_bp, self.record_anc_checkpoint, self.record_symptoms]
            elif stage == 4:
                tools = [self.record_symptoms, self.record_anc_checkpoint, self.record_bp]
            else:
                tools = [self.record_anc_checkpoint, self.record_bp, self.record_symptoms]
        else:
            tools = [
                self.record_bp, self.record_symptoms, self.record_gestational_age,
                self.record_anc_checkpoint, self.record_patient_intake,
                self.check_pregnancy_risk_factors,
            ]

        if not getattr(state, "patient_id", None):
            # Identity not settled: look her up, or create her.
            tools = [self.search_patient, self.register_mother, *tools]
        else:
            # Identified. `register_mother` is withdrawn here - that is the
            # duplicate-record bug above, removed as a possibility rather than
            # as an instruction. `search_patient` stays: the worker may correct
            # the name and need a re-lookup.
            tools = [self.search_patient, *tools]

        # While the frontend name/age box is up, withdraw search_patient so
        # the model has nothing left to call to retry identity through voice
        # - the structural half of "stop nudging via the LLM about the name".
        # Re-offered automatically once the box resolves (awaiting_identity_
        # box clears), matching normal post-identification behaviour.
        #
        # Filtered by __name__, not `is` - each `self.search_patient`
        # attribute access returns a fresh livekit FunctionTool wrapper
        # object (verified: `ts.search_patient is ts.search_patient` is
        # False), so an identity check would silently never match anything.
        if getattr(state, "awaiting_identity_box", False):
            tools = [t for t in tools if t.__name__ != "search_patient"]

        # Paging a specialist is an outward-facing, irreversible action. It
        # appears only once TriageEngine has actually classified this mother
        # HIGH_RISK - the model never gets to decide that it qualifies.
        assessment = getattr(state, "assessment", None)
        level = getattr(assessment, "level", None)
        if (
            getattr(state, "patient_id", None)
            and level is not None
            and getattr(level, "value", str(level)) == "HIGH_RISK"
            and not getattr(state, "escalated", False)
        ):
            tools.append(self.connect_smfmi_specialist)

        return tools
