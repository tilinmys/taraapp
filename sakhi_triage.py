"""Deterministic pre-eclampsia triage.

The whole clinical purpose of this product used to live as prose inside a
system prompt, which meant the risk decision depended on the LLM remembering to
apply it (changed.md C4). This module makes the decision in code: pure, no I/O,
no network, fully unit-testable, and identical on every call regardless of which
model served the turn or how the sentence was phrased.

The LLM's job is to *collect* the readings and *speak* the outcome. It does not
get a vote on what the outcome is.
"""

from __future__ import annotations

from sakhi_domain import (
    BloodPressure,
    RiskAssessment,
    RiskLevel,
    SymptomSet,
)

# Spoken guidance attached to each outcome. Phrased as instructions to the ASHA
# worker (the operator), never as a diagnosis to the mother - Sakhi is a
# decision-support co-pilot, not a practitioner (target spec §5.2).
_ACTIONS = {
    RiskLevel.HIGH_RISK: (
        "Refer immediately to Bidar District Hospital and connect an SMFMI "
        "specialist now. Do not wait for a repeat reading."
    ),
    RiskLevel.MODERATE: (
        "Re-check blood pressure within 48 hours and log a warning. Advise the "
        "mother to report any headache, vision change, or swelling at once."
    ),
    RiskLevel.OBSERVATION: (
        "Log the symptoms for monitoring and re-check blood pressure at the "
        "next visit. Escalate sooner if symptoms worsen."
    ),
    RiskLevel.ROUTINE: (
        "Log a routine visit and confirm the next ANC appointment date."
    ),
}

# Pre-eclampsia is defined from 20 weeks' gestation. Before that, raised BP is
# more likely chronic hypertension - clinically a different pathway. The engine
# still escalates (a screening tool must not under-triage), but it says so in
# the reasons, so the reviewing clinician sees the distinction.
PRE_ECLAMPSIA_ONSET_WEEKS = 20


class TriageEngine:
    """Applies the Bidar pilot triage matrix.

    Stateless and cheap to construct; a module-level `TRIAGE` instance is
    provided for convenience. Kept as a class rather than a bare function so
    thresholds can be overridden per-deployment without touching call sites -
    the Bidar pilot may not be the only district.
    """

    def __init__(self, *, escalate_on_severe_bp_alone: bool = True) -> None:
        # Severe-range hypertension (>=160/110) is a referral on the reading
        # alone under standard obstetric practice. The matrix in the target
        # architecture only escalates high-BP *with* symptoms; this flag adds
        # the severe-range rule on top of it and is documented in changed.md.
        self._escalate_on_severe_bp_alone = escalate_on_severe_bp_alone

    def assess(
        self,
        blood_pressure: BloodPressure | None,
        symptoms: SymptomSet | None = None,
        *,
        gestational_weeks: int | None = None,
    ) -> RiskAssessment:
        """Return the risk decision for the readings collected so far.

        Both inputs are optional because triage runs incrementally during a
        call - BP often arrives before the symptom screen. A missing input is
        treated as "not yet known", never as "negative".
        """
        symptoms = symptoms or SymptomSet()
        reasons: list[str] = []

        hypertensive = bool(blood_pressure and blood_pressure.is_hypertensive)
        severe = bool(blood_pressure and blood_pressure.is_severe)
        symptomatic = symptoms.any_present

        if blood_pressure is None:
            reasons.append("Blood pressure not yet recorded")
        elif severe:
            reasons.append(f"Severe-range blood pressure {blood_pressure} mmHg (>=160/110)")
        elif hypertensive:
            reasons.append(f"Raised blood pressure {blood_pressure} mmHg (>=140/90)")
        else:
            reasons.append(f"Blood pressure {blood_pressure} mmHg is within normal range")

        if symptomatic:
            reasons.append("Danger symptoms reported: " + ", ".join(symptoms.present_names))
        elif symptoms is not None:
            reasons.append("No danger symptoms reported")

        if (
            gestational_weeks is not None
            and gestational_weeks < PRE_ECLAMPSIA_ONSET_WEEKS
            and (hypertensive or severe)
        ):
            reasons.append(
                f"At {gestational_weeks} weeks (under {PRE_ECLAMPSIA_ONSET_WEEKS}), "
                "raised BP may be chronic hypertension rather than pre-eclampsia - "
                "clinician review needed"
            )

        level = self._classify(
            hypertensive=hypertensive,
            severe=severe,
            symptomatic=symptomatic,
        )

        return RiskAssessment(
            level=level,
            reasons=tuple(reasons),
            recommended_action=_ACTIONS[level],
            blood_pressure=blood_pressure,
            symptoms=symptoms,
        )

    def _classify(self, *, hypertensive: bool, severe: bool, symptomatic: bool) -> RiskLevel:
        """The matrix itself, isolated so it reads exactly like the diagram."""
        if severe and self._escalate_on_severe_bp_alone:
            return RiskLevel.HIGH_RISK
        if hypertensive:
            # High BP + any danger symptom -> red alert.
            # High BP alone -> 48h re-check.
            return RiskLevel.HIGH_RISK if symptomatic else RiskLevel.MODERATE
        # Normal BP + symptoms -> observe. Normal BP, no symptoms -> routine.
        return RiskLevel.OBSERVATION if symptomatic else RiskLevel.ROUTINE


TRIAGE = TriageEngine()
