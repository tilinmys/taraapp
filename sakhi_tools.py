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

import json
import logging
from typing import Any, Callable

from livekit.agents import RunContext, llm

from sakhi_domain import (
    BloodPressure,
    BloodPressureError,
    RiskLevel,
    SakhiCallState,
    SymptomSet,
)
from sakhi_repository import (
    BIDAR_VILLAGES,
    PatientRepository,
    correct_proper_noun,
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
    ) -> None:
        self._repo = repository
        self._triage = triage
        self._event = event_sink

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

    @staticmethod
    def _speak_instruction(assessment) -> str:
        """What the model should say, derived from the decided risk level.

        Phrased as guidance to the ASHA worker. Sakhi is a decision-support
        co-pilot, never a practitioner issuing a diagnosis (target spec §5.2).
        """
        if assessment.level is RiskLevel.HIGH_RISK:
            return (
                "SAY: calmly tell the ASHA worker this reading needs urgent attention, "
                "reassure her that the mother will be looked after, and that you are "
                "connecting a specialist right now. Speak slowly and stay calm. "
                "THEN call connect_smfmi_specialist immediately."
            )
        if assessment.level is RiskLevel.MODERATE:
            return (
                "SAY: the reading is above the safe range but there are no danger "
                "symptoms yet. Ask her to re-check the blood pressure within 48 hours "
                "and to call back at once if headache, vision changes, or swelling appear."
            )
        if assessment.level is RiskLevel.OBSERVATION:
            return (
                "SAY: the blood pressure is normal, but note the symptoms she reported "
                "and ask her to watch them. Re-check at the next visit."
            )
        return (
            "SAY: reassure her that the reading and the screening are both normal, "
            "then confirm the next ANC visit date."
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
            "instruction": self._speak_instruction(assessment),
        }
        if not persisted:
            payload["instruction"] = (
                "DO NOT tell her the reading has been saved - it has not. "
                "Say you still need the mother's name to file it, ask for her "
                "name, and call search_patient or register_mother. "
                + self._speak_instruction(assessment)
            )
        payload.update(extra)
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
                return json.dumps({
                    "found": False,
                    "attempt": state.lookup_attempts,
                    "instruction": (
                        "DO NOT ask for the name the same way again - it will be "
                        "misheard the same way. Ask her to spell it letter by "
                        "letter, separated by commas, and give this example: "
                        "\"ದಯವಿಟ್ಟು ಹೆಸರನ್ನು ಅಕ್ಷರ-ಅಕ್ಷರವಾಗಿ ಹೇಳಿ... "
                        "ಉದಾಹರಣೆಗೆ: S, U, M, A\". "
                        "If she confirms the mother is new, ask for age, village "
                        "and weeks pregnant, then call register_mother."
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
                    patient_id=best.patient_id, risk_level=best.risk_status.value)

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
                    event="patient_registered", patient_id=record.patient_id)

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

        self._event("tools", "warn" if assessment.requires_escalation else "ok",
                    f"BP {bp} recorded", assessment.recommended_action,
                    event="bp_recorded", systolic=bp.systolic, diastolic=bp.diastolic,
                    risk_level=assessment.level.value)

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
    ) -> str:
        """Record the three danger symptoms and re-assess risk. True only for symptoms she reports."""
        state = self._state(ctx)
        symptoms = SymptomSet(
            headache=bool(headache),
            blurry_vision=bool(blurry_vision),
            swelling=bool(swelling),
        )
        state.record_symptoms(symptoms)

        if state.patient_id:
            try:
                await self._repo.record_symptoms(state.patient_id, symptoms)
            except Exception:
                logger.warning("Symptom persistence failed; call continues", exc_info=True)

        assessment, persisted = await self._retriage(state)
        self._event("tools", "warn" if assessment.requires_escalation else "ok",
                    "Symptoms screened",
                    ", ".join(state.symptoms.present_names) or "none reported",
                    event="symptoms_recorded", risk_level=assessment.level.value)

        return self._result(
            state, assessment,
            persisted=persisted or bool(state.patient_id),
            recorded=True,
            symptoms_reported=list(state.symptoms.present_names) or ["none"],
        )

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
                patient_id, reason, state.assessment
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

    def as_list(self) -> list:
        """Bound tools, in the order the workflow uses them.

        Returned as bound methods so LiveKit resolves `self` automatically while
        keeping the generated schema free of it.
        """
        return [
            self.search_patient,
            self.register_mother,
            self.record_bp,
            self.record_symptoms,
            self.connect_smfmi_specialist,
        ]
