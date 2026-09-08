"""Deterministic pre-eclampsia triage.

The whole clinical purpose of this product used to live as prose inside a
system prompt, which meant the risk decision depended on the LLM remembering to
apply it (changed.md C4). This module makes the decision in code: pure, no I/O,
no network, fully unit-testable, and identical on every call regardless of which
model served the turn or how the sentence was phrased.

The LLM's job is to *collect* the readings and *speak* the outcome. It does not
get a vote on what the outcome is.

CALIBRATION CHANGE (2026-09-10) - two explicit user decisions
---------------------------------------------------------------
Rewiring the intake onto the "Antenatal Quick Check" PDF surfaced two places
where that form's own RED FLAGS list is stricter than this engine's existing,
previously-deliberate calibration. Both were put to the user directly, not
assumed, and both were answered "match the PDF literally":

1. **Plain hypertensive-alone (>=140/90, no symptoms)** used to stay MODERATE
   (a 48-hour re-check) - the PDF lists BP>=140/90 as its own automatic
   "REFER URGENTLY" red flag with no symptomatic qualifier. `_classify()` now
   returns HIGH_RISK for any hypertensive reading, symptomatic or not.
   **Mechanical consequence, disclosed rather than left implicit**:
   `RiskLevel.MODERATE` is now structurally unreachable - it was only ever
   produced by this one branch, and there is no other path to it below. The
   enum value stays defined (other code compares risk levels by ordering),
   but no live call will produce it.
2. **Fever and breathlessness**, reported alone with no other finding, now
   join vaginal bleeding / severe abdominal pain / convulsions as Tier-1-alone
   triggers (`danger_sign_alone` below) - the PDF's RED FLAGS list names both
   as automatic urgent-referral signs, same as the three pre-existing Tier-1
   signs. The safer, over-triage-averse alternative (require a second
   finding before either escalates alone) was offered and not chosen.

Everything else in this matrix - severe-range BP, gestation-conditional
reduced fetal movement, the new gestation-conditional preterm-contractions
rule below, pre-eclampsia-onset gestational caveats - is unchanged in kind,
only extended with the new fields.
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

# Reduced fetal movement only counts as a red flag from the third trimester.
# Before ~28 weeks a mother often has not established a reliable pattern of
# movement yet, so "the baby isn't moving much" is an expected answer rather
# than an alarming one - escalating on it would train ASHA workers to ignore
# the alert by the time it matters.
THIRD_TRIMESTER_WEEKS = 28

# Added 2026-09-10. Standard obstetric definition of preterm - labor before
# 37 completed weeks. Below this, reported contractions are a red flag
# ("suspected preterm labour" on the source PDF); at or above it, they are
# expected term labor and get birth-preparedness guidance instead.
PRETERM_WEEKS = 37


class TriageEngine:
    """Applies the Bidar pilot triage matrix.

    Stateless and cheap to construct; a module-level `TRIAGE` instance is
    provided for convenience. Kept as a class rather than a bare function so
    thresholds can be overridden per-deployment without touching call sites -
    the Bidar pilot may not be the only district.
    """

    def __init__(self, *, escalate_on_severe_bp_alone: bool = True) -> None:
        # Severe-range hypertension (>=160/110) is a referral on the reading
        # alone under standard obstetric practice; this flag existed to add
        # that rule on top of a matrix that otherwise only escalated high-BP
        # *with* symptoms (documented in changed.md).
        #
        # 2026-09-10: since plain hypertensive-alone (>=140/90) now ALSO
        # always escalates (see _classify()'s hypertensive branch, and this
        # module's docstring for why), and severe range is a subset of
        # hypertensive, setting this False no longer changes the outcome for
        # a severe reading - it still reaches HIGH_RISK via the hypertensive
        # branch below. The flag is left in place (constructing
        # TriageEngine(escalate_on_severe_bp_alone=False) still works and
        # still no-ops rather than errors), disclosed here rather than
        # silently made misleading.
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
        # Tier-1 danger signs (2026-08-30): active bleeding or a rigid/painful
        # abdomen are unambiguous obstetric emergencies on their own, the same
        # way severe BP already is - neither needs a second finding to
        # confirm it.
        # Convulsions added 2026-09-02: fits in pregnancy is eclampsia until
        # proved otherwise and needs no second finding to confirm it - the
        # same standing as bleeding and a rigid abdomen.
        # Fever and breathlessness added 2026-09-10, matching the "Antenatal
        # Quick Check" PDF's RED FLAGS list literally, per an explicit user
        # decision - see this module's docstring.
        #
        # Reads SymptomSet.tier1_alone (sakhi_domain.py) rather than
        # re-listing the fields here: this exact list used to be hand-copied
        # in TWO places, and the other copy (agent.py's _capture_symptoms)
        # had already drifted - missing `convulsions` entirely - by the time
        # this was found. One property, two call sites reading it, so it
        # cannot drift again.
        danger_sign_alone = symptoms.tier1_alone
        # Reduced fetal movement is gestation-conditional, so it is computed
        # separately rather than folded into danger_sign_alone above: the
        # SAME reported symptom is a red flag at 32 weeks and an unremarkable
        # answer at 16. If gestation is not known yet, it is treated as a red
        # flag - a screening tool must not under-triage on missing data.
        reduced_movement_flag = symptoms.reduced_fetal_movement and (
            gestational_weeks is None or gestational_weeks >= THIRD_TRIMESTER_WEEKS
        )
        # Preterm contractions (2026-09-10), the PDF's "suspected preterm
        # labour" red flag: the SAME reported contraction is expected term
        # labor at 39 weeks and a red flag at 30. Unknown gestation is
        # treated as flag-worthy, same under-triage-averse default as
        # reduced_movement_flag above.
        contractions_preterm_flag = symptoms.contractions and (
            gestational_weeks is None or gestational_weeks < PRETERM_WEEKS
        )

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
        if danger_sign_alone:
            reasons.append("Tier-1 danger sign present - HIGH_RISK regardless of blood pressure")
        if symptoms.reduced_fetal_movement:
            if reduced_movement_flag:
                weeks_txt = (
                    f"at {gestational_weeks} weeks" if gestational_weeks is not None
                    else "gestation unknown, treated as third trimester"
                )
                reasons.append(
                    f"Reduced fetal movement {weeks_txt} - third-trimester red flag"
                )
            else:
                reasons.append(
                    f"Reduced fetal movement reported at {gestational_weeks} weeks "
                    f"(under {THIRD_TRIMESTER_WEEKS}) - monitored, not escalated"
                )
        if symptoms.contractions:
            if contractions_preterm_flag:
                weeks_txt = (
                    f"at {gestational_weeks} weeks" if gestational_weeks is not None
                    else "gestation unknown, treated as possibly preterm"
                )
                reasons.append(
                    f"Contractions reported {weeks_txt} - suspected preterm labour red flag"
                )
            else:
                reasons.append(
                    f"Contractions reported at {gestational_weeks} weeks "
                    f"(term, >= {PRETERM_WEEKS}) - expected labour, not escalated"
                )

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
            danger_sign_alone=danger_sign_alone,
            reduced_movement_flag=reduced_movement_flag,
            contractions_preterm_flag=contractions_preterm_flag,
        )

        return RiskAssessment(
            level=level,
            reasons=tuple(reasons),
            recommended_action=_ACTIONS[level],
            blood_pressure=blood_pressure,
            symptoms=symptoms,
        )

    def _classify(
        self, *, hypertensive: bool, severe: bool, symptomatic: bool,
        danger_sign_alone: bool = False, reduced_movement_flag: bool = False,
        contractions_preterm_flag: bool = False,
    ) -> RiskLevel:
        """The matrix itself, isolated so it reads exactly like the diagram."""
        if danger_sign_alone:
            # Bleeding, a rigid/painful abdomen, convulsions, fever, or
            # breathlessness wins outright - checked before the BP branches
            # below, same precedence as severe BP.
            return RiskLevel.HIGH_RISK
        if reduced_movement_flag:
            # Third-trimester reduced movement is a referral on its own.
            return RiskLevel.HIGH_RISK
        if contractions_preterm_flag:
            # Suspected preterm labour is a referral on its own.
            return RiskLevel.HIGH_RISK
        if severe and self._escalate_on_severe_bp_alone:
            return RiskLevel.HIGH_RISK
        if hypertensive:
            # 2026-09-10: any hypertensive reading (>=140/90) is HIGH_RISK,
            # symptomatic or not - matches the source PDF's own RED FLAGS
            # list literally, per an explicit user decision (see this
            # module's docstring). This collapses what used to be a
            # HIGH_RISK/MODERATE split into one outcome; RiskLevel.MODERATE
            # is consequently unreachable from this branch (and there is no
            # other path to it below).
            return RiskLevel.HIGH_RISK
        # Normal BP + symptoms -> observe. Normal BP, no symptoms -> routine.
        return RiskLevel.OBSERVATION if symptomatic else RiskLevel.ROUTINE


TRIAGE = TriageEngine()
