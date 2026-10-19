"""Sakhi maternal-health domain model.

Value objects and per-call state for the ASHA co-pilot. Deliberately
dependency-free (stdlib only) so the clinical model can be unit-tested without
LiveKit, without network, and without a database - the same discipline
`guardrails.py` already follows in this codebase.

Why this module exists: the pre-refactor code carried the product's entire
clinical subject matter as loose primitives - risk as the magic string "HIGH" in
one place and "HIGH_RISK" in another, blood pressure as two ints threaded
through five call sites, symptoms as three positional bools. See oops.md §2.3.
"""

from __future__ import annotations

import dataclasses
import enum
import re
from typing import Iterable

from sakhi_intake import IntakeProgress


# Spelled-out language names, because the dashboard's picker sends
# "kannada"/"hindi" rather than BCP-47 codes (frontend/src/app/page.tsx sets
# `language` from the <select> value). Matching codes alone meant "hindi" fell
# through to the default and silently produced a KANNADA agent - the ASHA
# worker picked Hindi and Sakhi answered in Kannada.
#
# Module-level rather than a class attribute on the Enum: a plain dict in an
# Enum body is a member-name candidate, which is exactly the kind of subtlety
# that should not sit under a language selector.
_LANGUAGE_ALIASES = {
    "kannada": "kn", "kan": "kn",
    "hindi": "hi", "hin": "hi",
    "english": "en", "eng": "en", "indian english": "en",
}


class Language(enum.Enum):
    """Canonical call language.

    The pre-refactor code passed language around as a bare `str` that meant
    "en", "kn", "hi", or "kn-IN" depending on which function you were in, so
    `build_stt()` had to re-derive the Sarvam code with a `startswith` check.
    One type now carries the code for every provider that needs it.
    """

    KANNADA = "kn"
    HINDI = "hi"
    ENGLISH = "en"

    @classmethod
    def parse(cls, value: object, default: "Language" = None) -> "Language":
        """Accept anything the metadata/UI/provider layers might hand us.

        Handles enum members, BCP-47 codes ("hi-IN"), bare codes ("hi"), and
        spelled-out names ("hindi").

        Never raises: an unknown value falls back rather than killing a live
        call. Sarvam goes silent (no error, no transcripts) on language codes
        it does not know, which previously made the agent deaf mid-session, so
        the safe default matters.
        """
        if isinstance(value, cls):
            return value
        fallback = default if default is not None else cls.KANNADA
        if not isinstance(value, str):
            return fallback
        norm = value.strip().lower().replace("_", "-")
        if not norm:
            return fallback

        code = _LANGUAGE_ALIASES.get(norm)
        if code is None:
            base = norm.split("-", 1)[0]
            code = _LANGUAGE_ALIASES.get(base, base)

        for member in cls:
            if code == member.value:
                return member
        return fallback

    @property
    def sarvam_code(self) -> str:
        """BCP-47 code accepted by Sarvam STT and TTS."""
        return f"{self.value}-IN"

    @property
    def honorific(self) -> str:
        """How Sakhi addresses the ASHA worker, per the Bidar pilot protocol."""
        return {
            Language.KANNADA: "ಅಕ್ಕಾರ",
            Language.HINDI: "दीदी",
            Language.ENGLISH: "Akkara",
        }[self]

    @property
    def display_name(self) -> str:
        return {
            Language.KANNADA: "Kannada",
            Language.HINDI: "Hindi",
            Language.ENGLISH: "Indian English",
        }[self]


class RiskLevel(enum.Enum):
    """Pre-eclampsia triage outcome.

    Ordered by escalation so call sites can compare (`>=`) instead of matching
    strings. `_order` exists because Enum members cannot be compared by value
    when the values are display strings.
    """

    ROUTINE = "ROUTINE"
    OBSERVATION = "OBSERVATION"
    MODERATE = "MODERATE"
    HIGH_RISK = "HIGH_RISK"

    @property
    def _order(self) -> int:
        return {
            RiskLevel.ROUTINE: 0,
            RiskLevel.OBSERVATION: 1,
            RiskLevel.MODERATE: 2,
            RiskLevel.HIGH_RISK: 3,
        }[self]

    def __lt__(self, other: "RiskLevel") -> bool:
        if not isinstance(other, RiskLevel):
            return NotImplemented
        return self._order < other._order

    def __le__(self, other: "RiskLevel") -> bool:
        if not isinstance(other, RiskLevel):
            return NotImplemented
        return self._order <= other._order

    @property
    def requires_escalation(self) -> bool:
        """Only HIGH_RISK auto-connects a specialist. MODERATE is a re-check."""
        return self is RiskLevel.HIGH_RISK

    @property
    def indicator(self) -> str:
        """Dashboard glyph, matching the triage matrix in ARCHITECTURE.md."""
        return {
            RiskLevel.ROUTINE: "🟢",
            RiskLevel.OBSERVATION: "🟡",
            RiskLevel.MODERATE: "🟡",
            RiskLevel.HIGH_RISK: "🔴",
        }[self]


class BloodPressureError(ValueError):
    """A BP reading that cannot be clinically valid.

    Raised rather than silently clamped: an STT mishearing "one forty over
    ninety" as "one forty over nine hundred" must surface as a re-ask, not be
    quietly recorded against a mother's record.
    """


@dataclasses.dataclass(frozen=True)
class BloodPressure:
    """One BP reading in mmHg.

    Thresholds follow the Bidar pilot protocol / ACOG: hypertension at
    >=140 systolic OR >=90 diastolic; severe range at >=160/>=110.
    """

    systolic: int
    diastolic: int

    # Physiologically possible bounds - anything outside is a transcription
    # error, not a reading.
    MIN_SYSTOLIC = 50
    MAX_SYSTOLIC = 300
    MIN_DIASTOLIC = 30
    MAX_DIASTOLIC = 200

    HYPERTENSION_SYSTOLIC = 140
    HYPERTENSION_DIASTOLIC = 90
    SEVERE_SYSTOLIC = 160
    SEVERE_DIASTOLIC = 110

    def __post_init__(self) -> None:
        if not isinstance(self.systolic, int) or not isinstance(self.diastolic, int):
            raise BloodPressureError("Blood pressure must be given as whole numbers.")
        if not (self.MIN_SYSTOLIC <= self.systolic <= self.MAX_SYSTOLIC):
            raise BloodPressureError(
                f"Systolic {self.systolic} is outside the plausible range "
                f"{self.MIN_SYSTOLIC}-{self.MAX_SYSTOLIC} mmHg."
            )
        if not (self.MIN_DIASTOLIC <= self.diastolic <= self.MAX_DIASTOLIC):
            raise BloodPressureError(
                f"Diastolic {self.diastolic} is outside the plausible range "
                f"{self.MIN_DIASTOLIC}-{self.MAX_DIASTOLIC} mmHg."
            )
        if self.systolic <= self.diastolic:
            raise BloodPressureError(
                f"Systolic ({self.systolic}) must be higher than diastolic "
                f"({self.diastolic}); the two numbers were probably swapped."
            )

    @property
    def is_hypertensive(self) -> bool:
        return (
            self.systolic >= self.HYPERTENSION_SYSTOLIC
            or self.diastolic >= self.HYPERTENSION_DIASTOLIC
        )

    @property
    def is_severe(self) -> bool:
        """Severe-range hypertension - escalates on the BP reading alone."""
        return (
            self.systolic >= self.SEVERE_SYSTOLIC
            or self.diastolic >= self.SEVERE_DIASTOLIC
        )

    def __str__(self) -> str:
        return f"{self.systolic}/{self.diastolic}"


@dataclasses.dataclass(frozen=True)
class SymptomSet:
    """The obstetric danger symptoms screened on every visit.

    Keyword-only construction is deliberate: the previous signature was
    `record_symptoms(pid, headache, blurry_vision, swelling)`, three positional
    bools that a caller can transpose without any error surfacing.

    `vaginal_bleeding` and `severe_abdominal_pain` were added 2026-08-30 as
    Tier-1 danger signs distinct from the original three pre-eclampsia signs -
    either one alone is an obstetric emergency regardless of blood pressure,
    so `TriageEngine` treats them as HIGH_RISK-alone triggers (see
    sakhi_triage.py), the same way severe BP already was.

    `convulsions` and `reduced_fetal_movement` were added 2026-09-02 to close
    the last two gaps against the ANC red-flag list:

    * convulsions/fits in pregnancy is eclampsia until proved otherwise - the
      single most time-critical obstetric emergency there is, and it was the
      one red flag with no field at all.
    * reduced fetal movement is only a red flag in the THIRD TRIMESTER (>=28
      weeks); before that a mother has often not begun feeling movement
      reliably, so a "no" is expected rather than alarming. That gestational
      condition lives in TriageEngine, not here - this field records only
      what was reported.
    """

    headache: bool = False
    blurry_vision: bool = False
    swelling: bool = False
    vaginal_bleeding: bool = False
    severe_abdominal_pain: bool = False
    convulsions: bool = False
    reduced_fetal_movement: bool = False

    # Added 2026-09-10, rewiring the intake onto the "Antenatal Quick Check"
    # PDF's ASK section. `fever` and `breathlessness` are Tier-1-alone danger
    # signs (sakhi_triage.py) per an explicit user decision - the source form
    # lists both on its own RED FLAGS list, and the user chose to match that
    # literally rather than requiring a second finding first. `contractions`
    # is gestation-conditional the same way `reduced_fetal_movement` already
    # is (a red flag only if preterm - see sakhi_triage.py). The remaining
    # four (`upper_abdominal_pain`, `urinary_symptoms`, `palpitations`,
    # `severe_vomiting`) fold into the existing "symptomatic" bucket, the
    # same treatment `headache`/`blurry_vision` already get - they do not
    # escalate alone, but they do make a hypertensive reading symptomatic.
    contractions: bool = False
    upper_abdominal_pain: bool = False
    fever: bool = False
    urinary_symptoms: bool = False
    breathlessness: bool = False
    palpitations: bool = False
    severe_vomiting: bool = False

    def __post_init__(self) -> None:
        for field in dataclasses.fields(self):
            if not isinstance(getattr(self, field.name), bool):
                raise TypeError(f"Symptom '{field.name}' must be a bool.")

    @property
    def any_present(self) -> bool:
        return any(getattr(self, f.name) for f in dataclasses.fields(self))

    @property
    def tier1_alone(self) -> bool:
        """Signs capable of reaching HIGH_RISK on their own, no second
        finding needed (sakhi_triage.py's danger_sign_alone).

        2026-09-10: extracted here as the single source of truth after
        finding TWO independent hand-copies of this exact list had drifted:
        sakhi_triage.py's own `danger_sign_alone` (which the Antenatal Quick
        Check rewrite correctly extended with fever/breathlessness) and a
        SEPARATE, older copy in agent.py's `_capture_symptoms()` that was
        missing `convulsions` entirely (a fits-only report, with no BP and no
        other symptom, silently skipped re-triage) and would have gone stale
        again the moment fever/breathlessness were added there too. Both
        call sites now read this property instead of re-listing the fields.
        """
        return (
            self.vaginal_bleeding
            or self.severe_abdominal_pain
            or self.convulsions
            or self.fever
            or self.breathlessness
        )

    @property
    def present_names(self) -> tuple[str, ...]:
        names = {
            "headache": "severe headache",
            "blurry_vision": "blurry vision",
            "swelling": "facial or hand swelling",
            "vaginal_bleeding": "vaginal bleeding",
            "severe_abdominal_pain": "severe abdominal pain or leaking fluid",
            "convulsions": "convulsions or fits",
            "reduced_fetal_movement": "reduced fetal movement",
            "contractions": "contractions",
            "upper_abdominal_pain": "upper abdominal pain",
            "fever": "fever",
            "urinary_symptoms": "urinary symptoms",
            "breathlessness": "breathlessness",
            "palpitations": "palpitations",
            "severe_vomiting": "severe vomiting",
        }
        return tuple(label for field, label in names.items() if getattr(self, field))

    def merged_with(self, other: "SymptomSet") -> "SymptomSet":
        """Symptoms accumulate across a call - a later 'no swelling' must not
        erase an earlier 'yes headache'."""
        return SymptomSet(**{
            f.name: getattr(self, f.name) or getattr(other, f.name)
            for f in dataclasses.fields(self)
        })

    def to_dict(self) -> dict[str, bool]:
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class RiskAssessment:
    """The output of TriageEngine - a decision plus the reasons behind it.

    `reasons` is carried so the audit trail records *why* a mother was flagged,
    which is what a clinical reviewer needs, and so the spoken reply can name
    the finding instead of asserting a bare risk level.
    """

    level: RiskLevel
    reasons: tuple[str, ...]
    recommended_action: str
    blood_pressure: BloodPressure | None = None
    symptoms: SymptomSet | None = None

    @property
    def requires_escalation(self) -> bool:
        return self.level.requires_escalation

    def to_dict(self) -> dict:
        return {
            "risk_level": self.level.value,
            "reasons": list(self.reasons),
            "recommended_action": self.recommended_action,
            "blood_pressure": str(self.blood_pressure) if self.blood_pressure else None,
            "symptoms": self.symptoms.to_dict() if self.symptoms else None,
            "requires_escalation": self.requires_escalation,
        }


# --------------------------------------------------------------------------
# Deterministic symptom detection
# --------------------------------------------------------------------------
#
# WHY THIS EXISTS
# ---------------
# Observed on a live test call: the ASHA worker said the mother had a severe
# headache in her opening sentence. The model then called
# record_symptoms(false, false, false), triage stayed MODERATE instead of
# HIGH_RISK, and no specialist was indicated. That is under-triage - the most
# dangerous direction this system can fail in - and it happened silently.
#
# So symptom capture no longer depends on the model passing the right booleans.
# The user's own words are scanned here, and because SymptomSet.merged_with()
# only ever ORs, a model that later reports `false` cannot erase a symptom the
# mother actually reported.
#
# Transliterated forms are included on purpose: Sarvam STT renders spoken
# English into Kannada script ("ಹೆಡ್ ಏಕ್" for "head ache"), so matching only
# native-script or only Latin spellings misses half of real speech.
_SYMPTOM_PATTERNS = {
    # Tier-1 danger signs FIRST (2026-09-04 reorder) - these are the fields
    # capable of reaching HIGH_RISK on their own (see sakhi_triage.py), so
    # when kannada_symptom_terms() below feeds a Deepgram keyterm list that
    # turns out to be too large for Deepgram's own per-request budget (see
    # that function's and DEEPGRAM_MEDICAL_KEYTERMS's docstrings for the
    # real incident this fixes), the terms silently dropped by truncation
    # must be the moderate-risk ones, never a Tier-1 sign. detect_symptoms()
    # below iterates this dict and sets each field independently with no
    # cross-field priority logic, so this reorder changes nothing about
    # which symptoms get detected or how - confirmed by reading it, not
    # assumed.
    "vaginal_bleeding": (
        "ರಕ್ತಸ್ರಾವ", "ಬ್ಲೀಡಿಂಗ್",
        "रक्तस्राव",
        "bleeding",
    ),
    "severe_abdominal_pain": (
        "ಹೊಟ್ಟೆ ಕಲ್ಲು", "ಹೊಟ್ಟೆ ಗಟ್ಟಿ", "ಹೊಟ್ಟೆ ನೋವು ವಿಪರೀತ",
        "पेट में तेज दर्द",
        "severe abdominal pain", "stomach hard",
        # Leaking watery fluid per vaginum - PROM. Grouped here rather than
        # given its own field because the clinical action is identical
        # (immediate referral) and the ASHA worker reports it in the same
        # breath as abdominal pain; a separate field would split one
        # decision across two flags without changing what happens next.
        "ನೀರು ಹೋಗ್ತಿದೆ", "ನೀರು ಹೋಗುತ್ತಿದೆ", "ನೀರಿನ ಸ್ರಾವ",
        "पानी जा रहा", "पानी निकल",
        "water leaking", "leaking fluid", "fluid leaking", "water breaking",
    ),
    # Red flags added 2026-09-02 - see SymptomSet's docstring.
    "convulsions": (
        # "fits", "shivering-and-falling", "unconscious shaking" is how
        # eclampsia gets described in the field, not the clinical word.
        "ಫಿಟ್ಸ್", "ಫಿಟ್", "ಸೆಳೆತ", "ಮೂರ್ಛೆ", "ಕೈಕಾಲು ಸೆಳೆದು", "ನಡುಕ ಬಂದು",
        "दौरा", "दौरे", "मिर्गी", "झटके",
        "convulsion", "convulsions", "fits", "seizure", "seizures",
    ),
    # Added 2026-09-10 (Antenatal Quick Check rewrite) - Tier-1-alone per an
    # explicit user decision (sakhi_triage.py), so listed here alongside the
    # other Tier-1 signs above, ahead of the moderate-risk fields below, for
    # the same STT-keyterm-truncation-safety reason given at the top of this
    # dict.
    "fever": (
        "ಜ್ವರ", "ಮೈ ಬಿಸಿ", "ಮೈ ಕಾಯ್ತಿದೆ",
        "बुखार", "ज्वर",
        "fever", "high temperature", "temperature",
    ),
    "breathlessness": (
        "ಉಸಿರಾಟದ ತೊಂದರೆ", "ಉಸಿರು ಕಟ್ಟಿದಂಗೆ", "ಉಸಿರಾಡಕ್ ಆಗ್ತಿಲ್ಲ",
        "सांस लेने में तकलीफ", "सांस फूलना", "साँस फूलना",
        "breathless", "breathlessness", "shortness of breath",
        "difficulty breathing", "trouble breathing",
    ),
    "reduced_fetal_movement": (
        # "Kusu aado-aata" - literally "the baby's playing" - is the
        # vernacular for fetal movement in North Karnataka. The patterns
        # below deliberately match the REDUCED/ABSENT phrasing only; plain
        # "ಕುಸು ಆಡ್ತಿದೆ" (baby is moving) must NOT set this flag, and the
        # negation window in detect_symptoms() is what keeps "no reduced
        # movement" from tripping it either.
        "ಕುಸು ಆಡ್ತಿಲ್ಲ", "ಕುಸು ಆಡುತ್ತಿಲ್ಲ", "ಮಗು ಆಡ್ತಿಲ್ಲ", "ಚಲನೆ ಕಡಿಮೆ",
        "ಅಲುಗಾಡ್ತಿಲ್ಲ", "ಓಡಾಟ ಕಡಿಮೆ",
        "बच्चा हिल नहीं", "हलचल कम",
        "not moving", "no movement", "reduced movement", "less movement",
        "baby not moving", "movements decreased", "decreased movement",
    ),
    # Added 2026-09-10 - gestation-conditional the same way reduced fetal
    # movement above is: a red flag only if suspected preterm (see
    # sakhi_triage.py). Recorded here regardless of gestation; the
    # gestational condition lives in TriageEngine, not here.
    "contractions": (
        "ಸಂಕೋಚನ", "ಹೊಟ್ಟೆ ಬಿಗಿತ", "ನೋವು ಬಂದು ಹೋಗ್ತಿದೆ",
        "संकुचन", "दर्द उठ रहा",
        "contraction", "contractions", "labour pain", "labor pain",
    ),
    # Moderate-risk fields - do not escalate alone, but do make a
    # hypertensive reading "symptomatic" (see sakhi_triage.py; note that as
    # of 2026-09-10 plain hypertensive-alone escalates to HIGH_RISK too, per
    # an explicit user decision, so the symptomatic/non-symptomatic
    # distinction now mainly affects the OBSERVATION-vs-ROUTINE split when BP
    # is normal). Kept below the Tier-1 signs above, not ahead of them.
    "headache": (
        "ತಲೆನೋವು", "ತಲೆ ನೋವು", "ತಲೆನೋವಿ", "ಹೆಡ್ ಏಕ್", "ಹೆಡ್‌ಏಕ್",
        "सिरदर्द", "सर दर्द", "सिर में दर्द",
        "headache", "head ache", "head-ache",
    ),
    "blurry_vision": (
        "ಕಣ್ಣು ಮಂಜು", "ಮಂಜಾಗು", "ಮಂಜು", "ದೃಷ್ಟಿ ಮಂದ", "ಕಣ್ಣು ಮಂದ",
        # Regional idiom additions (2026-08-30): "eyes going dark" and
        # "dizziness/head-spinning" are how this is actually described in
        # North Karnataka Kannada, not clinical vocabulary - dizziness folds
        # into this same field rather than getting its own, matching how an
        # ASHA worker would report it as one complaint.
        "ಕಣ್ಣು ಕತ್ತಲೆ", "ತಲೆಸುತ್ತು",
        "धुंधला", "धुंधली", "दृष्टि",
        "blurry", "blurred", "blurry vision", "double vision",
    ),
    "swelling": (
        "ಊತ", "ಬಾವು", "ಊದಿಕೊಂಡ", "ಊದಿದೆ",
        "सूजन", "सूजा",
        "swelling", "swollen", "puffiness",
    ),
    # Added 2026-09-10 - distinct from severe_abdominal_pain (labor/rupture-
    # type pain, Tier-1) above: epigastric/upper-abdominal pain is the
    # pre-eclampsia/HELLP-syndrome signal, clinically meaningful especially
    # alongside headache/blurry_vision/raised BP, but not a Tier-1-alone
    # trigger on its own in this pass.
    "upper_abdominal_pain": (
        "ಹೊಟ್ಟೆಯ ಮೇಲ್ಭಾಗದಲ್ಲಿ ನೋವು", "ಎದೆಯ ಕೆಳಗೆ ನೋವು",
        "पेट के ऊपरी हिस्से में दर्द", "ऊपरी पेट दर्द",
        "upper abdominal pain", "epigastric pain",
    ),
    "urinary_symptoms": (
        "ಮೂತ್ರದ ತೊಂದರೆ", "ಉರಿ ಮೂತ್ರ", "ಮೂತ್ರ ಉರಿ",
        "पेशाब में तकलीफ", "पेशाब में जलन",
        "urinary", "burning urination", "painful urination",
    ),
    "palpitations": (
        "ಎದೆ ಢವಢವ", "ಎದೆ ಬಡಿತ ಜಾಸ್ತಿ",
        "धड़कन तेज", "दिल तेज़ धड़कना",
        "palpitation", "palpitations", "heart racing", "racing heart",
    ),
    "severe_vomiting": (
        "ವಿಪರೀತ ವಾಂತಿ", "ಸತತ ವಾಂತಿ",
        "बहुत ज्यादा उल्टी", "बार बार उल्टी",
        "severe vomiting", "vomiting a lot", "constant vomiting",
    ),
}


def kannada_symptom_terms() -> list[str]:
    """Kannada-script phrases from _SYMPTOM_PATTERNS, flattened and deduped.

    Public so STT keyword-boosting (agent.py's DEEPGRAM_MEDICAL_KEYTERMS,
    Smallest AI's `keywords` via clinical_terms.py) can derive its danger-
    sign vocabulary from the SAME patterns detect_symptoms() actually
    matches against, rather than a hand-typed second copy that silently
    goes stale the next time a pattern is added here and nobody remembers
    to update the STT list too - the exact class of bug already found and
    fixed once this session (agent.py's clinical-tool-name set drifting
    against ClinicalToolset.as_list()).

    English/Hindi variants are deliberately excluded: this feeds a
    Kannada-primary STT keyterm list, and keyword-boosting works best with
    a bounded, curated set - every language variant of every phrase would
    roughly triple the list for no boosting benefit on the languages this
    specific list isn't for.
    """
    seen: set[str] = set()
    terms: list[str] = []
    for phrases in _SYMPTOM_PATTERNS.values():
        for phrase in phrases:
            if phrase in seen:
                continue
            if any("ಀ" <= ch <= "೿" for ch in phrase):
                seen.add(phrase)
                terms.append(phrase)
    return terms


# --------------------------------------------------------------------------
# Tier-1 danger-sign first-aid advisory (2026-08-30)
# --------------------------------------------------------------------------
# DRAFT CONTENT - NEEDS CLINICAL REVIEW BEFORE REAL DEPLOYMENT. Same caveat
# this codebase already applies to translated prompt text elsewhere (see
# sakhi_prompts.py's own header: "Centralised so the prompt can be reviewed
# by a Kannada speaker and a clinician"). This is basic, widely-documented
# first-aid guidance (lie on the left side, do not travel alone, do not eat
# or drink, get to a facility now) - not a substitute for a clinician's
# sign-off on the exact wording an ASHA worker will actually hear.
#
# Keyed by the reason string set on SakhiCallState.intake_lock_reason.
# "severe_bp"/"symptomatic_bp" cover the two HIGH_RISK paths that already
# existed before this change (severe BP alone, or hypertensive+symptomatic) -
# included here so every HIGH_RISK lock has a matching advisory line, not
# just the two new Tier-1 signs.
def danger_sign_lock_reason(state: "SakhiCallState") -> str:
    """Which Tier-1 sign to blame in the lock/escalation record.

    Shared by both places a HIGH_RISK assessment can be produced -
    agent.py's deterministic re-triage on raw speech (_capture_symptoms) and
    sakhi_tools.py's tool-driven re-triage (_retriage, called from
    record_bp/record_symptoms) - so a call reaching HIGH_RISK via either
    path gets the same reason string. Precedence mirrors
    TriageEngine._classify()'s own branch order (danger-sign-alone checked
    before severe BP, before hypertensive+symptomatic): if more than one is
    true at once, the reason recorded is the one that actually decided the
    risk level.
    """
    # Convulsions first: in pregnancy this is eclampsia until proved
    # otherwise, and it is the most time-critical thing on the list. If a
    # mother is fitting AND bleeding, "convulsions" is the reason the
    # Medical Officer needs to read first.
    if state.symptoms.convulsions:
        return "convulsions"
    if state.symptoms.vaginal_bleeding:
        return "vaginal_bleeding"
    if state.symptoms.severe_abdominal_pain:
        return "severe_abdominal_pain"
    # Added 2026-09-10: fever and breathlessness are Tier-1-alone per an
    # explicit user decision (sakhi_triage.py). Checked here, after the
    # three original Tier-1 signs and before severe BP, mirroring
    # _classify()'s own branch order (danger_sign_alone checked before the
    # BP branches) - if more than one is true, the reason recorded is
    # whichever a clinician would want to read first.
    if state.symptoms.fever:
        return "fever"
    if state.symptoms.breathlessness:
        return "breathlessness"
    if state.blood_pressure and state.blood_pressure.is_severe:
        return "severe_bp"
    if state.symptoms.reduced_fetal_movement:
        return "reduced_fetal_movement"
    # Added 2026-09-10: preterm contractions (see sakhi_triage.py's
    # contractions_preterm_flag) - checked last among the named signs.
    if state.symptoms.contractions:
        return "contractions"
    return "symptomatic_bp"


DANGER_SIGN_ADVISORY: dict[str, str] = {
    "vaginal_bleeding": (
        "ಅಕ್ಕಾ, ಆಕೆಯನ್ನು ಎಡಮಗ್ಗುಲಿಗೆ ಮಲಗಿಸಿ. ಒಬ್ಬಳೇ ಪ್ರಯಾಣ ಮಾಡಬೇಡಿ, ಈಗಲೇ "
        "ಆಸ್ಪತ್ರೆಗೆ ಕರೆದುಕೊಂಡು ಹೋಗಿ."
    ),
    "severe_abdominal_pain": (
        "ಅಕ್ಕಾ, ಆಕೆಗೆ ಏನೂ ತಿನ್ನಲು ಅಥವಾ ಕುಡಿಯಲು ಕೊಡಬೇಡಿ. ಆಕೆಯನ್ನು ಶಾಂತವಾಗಿ "
        "ಮಲಗಿಸಿ ಈಗಲೇ ಆಸ್ಪತ್ರೆಗೆ ಕರೆದುಕೊಂಡು ಹೋಗಿ."
    ),
    "severe_bp": (
        "ಅಕ್ಕಾ, ಆಕೆಯನ್ನು ಶಾಂತವಾಗಿ, ಎಡಮಗ್ಗುಲಿಗೆ ಮಲಗಿಸಿ, ಪ್ರಕಾಶಮಾನ ಬೆಳಕು ಮತ್ತು "
        "ಗದ್ದಲದಿಂದ ದೂರವಿಡಿ. ಈಗಲೇ ಆಸ್ಪತ್ರೆಗೆ ಕರೆದುಕೊಂಡು ಹೋಗಿ."
    ),
    "symptomatic_bp": (
        "ಅಕ್ಕಾ, ಆಕೆಯನ್ನು ಶಾಂತವಾಗಿ, ಎಡಮಗ್ಗುಲಿಗೆ ಮಲಗಿಸಿ. ಈಗಲೇ ಆಸ್ಪತ್ರೆಗೆ "
        "ಕರೆದುಕೊಂಡು ಹೋಗಿ."
    ),
    # Added 2026-09-02 with the convulsions / reduced-fetal-movement red
    # flags. Eclampsia first aid is position + airway + do NOT restrain, and
    # explicitly NOT "put something in her mouth", which is the folk response
    # this line has to displace rather than leave unaddressed.
    "convulsions": (
        "ಅಕ್ಕಾ, ಆಕೆಯನ್ನು ಎಡಮಗ್ಗುಲಿಗೆ ಮಲಗಿಸಿ, ಸುತ್ತಲಿನ ವಸ್ತುಗಳನ್ನು ಸರಿಸಿ. "
        "ಬಾಯಿಗೆ ಏನನ್ನೂ ಹಾಕಬೇಡಿ, ಆಕೆಯನ್ನು ಹಿಡಿದು ತಡೆಯಬೇಡಿ. ಈಗಲೇ ನೂರಎಂಟಕ್ಕೆ ಕರೆ ಮಾಡಿ."
    ),
    "reduced_fetal_movement": (
        "ಅಕ್ಕಾ, ಆಕೆಯನ್ನು ಎಡಮಗ್ಗುಲಿಗೆ ಮಲಗಿಸಿ ಒಂದು ಗಂಟೆ ಮಗುವಿನ ಆಟ ಎಣಿಸಲು ಹೇಳಿ. "
        "ಆಟ ಕಡಿಮೆ ಇದ್ದರೆ ತಡ ಮಾಡಬೇಡಿ, ಈಗಲೇ ಆಸ್ಪತ್ರೆಗೆ ಕರೆದುಕೊಂಡು ಹೋಗಿ."
    ),
    # Added 2026-09-10 with the fever/breathlessness/contractions red flags -
    # DRAFT CONTENT, same caveat as every entry above.
    "fever": (
        "ಅಕ್ಕಾ, ಆಕೆಗೆ ತಣ್ಣನೆ ಬಟ್ಟೆಯಿಂದ ಮೈ ಒರೆಸಿ, ಸಾಕಷ್ಟು ನೀರು ಕುಡಿಸಿ. "
        "ಈಗಲೇ ಆಸ್ಪತ್ರೆಗೆ ಕರೆದುಕೊಂಡು ಹೋಗಿ."
    ),
    "breathlessness": (
        "ಅಕ್ಕಾ, ಆಕೆಯನ್ನು ನೆಟ್ಟಗೆ ಕೂರಿಸಿ, ಗಾಳಿ ಆಡುವಂತೆ ಮಾಡಿ. ಶಾಂತವಾಗಿರಿ, "
        "ಈಗಲೇ ಆಸ್ಪತ್ರೆಗೆ ಕರೆದುಕೊಂಡು ಹೋಗಿ."
    ),
    "contractions": (
        "ಅಕ್ಕಾ, ನೋವು ಎಷ್ಟು ಹೊತ್ತಿಗೊಮ್ಮೆ ಬರುತ್ತಿದೆ ಗಮನಿಸಿ. ಆಕೆಯನ್ನು ಒಬ್ಬಳೇ "
        "ಬಿಡಬೇಡಿ, ಈಗಲೇ ಆಸ್ಪತ್ರೆಗೆ ಕರೆದುಕೊಂಡು ಹೋಗಿ - ಸಮಯಕ್ಕಿಂತ ಮುಂಚಿನ ಹೆರಿಗೆ ಆಗಿರಬಹುದು."
    ),
}


# Negation markers. Kannada and Hindi place negation AFTER the noun
# ("ತಲೆನೋವು ಇಲ್ಲ" = headache not-present), English places it before
# ("no headache"), so the window is checked on both sides.
#
# Kannada negative VERB forms carry the negation as a vowel sign + ಲ್ಲ
# ("ಆಗಿಲ್ಲ" = has-not-happened), which never contains the free-standing
# "ಇಲ್ಲ" - so each needs its own entry. Turn 22 (2026-09-25): "... ಫಿಟ್ಸ್
# ಯಾವುದು ಡಯಾಗ್ನೋಸ್ ಆಗಿಲ್ಲ" (fits: never diagnosed) flagged convulsions.
_NEGATIONS = (
    "ಇಲ್ಲ", "ಇಲ್ಲವೇ", "ಇರಲಿಲ್ಲ",
    "ಆಗಿಲ್ಲ", "ಕಾಣಿಸಿಲ್ಲ", "ಬಂದಿಲ್ಲ", "ತೊಂದರೆ ಇಲ್ಲ", "ಏನೂ ಇಲ್ಲ",
    "नहीं", "नही",
    " no ", " not ", " without ", "denies", "negative",
)

# 28 -> 65 (2026-09-25) for longer sentences, made safe by clipping: the
# window never crosses a sentence end or a contrast word, so "ತಲೆನೋವು ಇಲ್ಲ,
# ಆದರೆ ಫಿಟ್ಸ್ ಬಂತು" (no headache, BUT fits) still records the fits. A wider
# unclipped window would let one denied symptom erase a real danger sign -
# under-triage, the worse failure on this call.
_NEGATION_WINDOW = 65

_CLAUSE_BREAK_RE = re.compile(
    r"[.?!।\n]|(?<![a-z])(?:but|however)(?![a-z])"
    r"|ಆದರೆ|ಆದ್ರೆ|ಆದರೂ|ಆದ್ರೂ|लेकिन|मगर|परंतु|किंतु"
)

# Indic "it happened / it is there" right after the symptom: the symptom is
# AFFIRMED, and a negation later in the same window belongs to a later noun
# ("ಫಿಟ್ಸ್ ಬಂತು, ಹೊಟ್ಟೆ ನೋವು ಏನೂ ಇಲ್ಲ" = fits came, no stomach pain).
_AFFIRMATIONS = (
    "ಬಂತು", "ಬಂದಿದೆ", "ಬಂದಿತ್ತು", "ಬರ್ತಿದೆ", "ಬರುತ್ತಿದೆ", "ಆಯ್ತು", "ಆಗಿದೆ",
    "ಆಗ್ತಿದೆ", "ಆಗುತ್ತಿದೆ", "ಇದೆ", "ಇತ್ತು",
    "हुआ", "हुई", "हो रहा", "हो रही", "है", "था", "थी",
)

# Past-history intake questions (chronic disease, current medicines). A
# disclosure there - "ಫಿಟ್ಸ್ ಇದೆ, ಮಾತ್ರೆ ತಗೋತಾರೆ" (known epilepsy, on
# tablets) - is history, not an acute danger sign, unless the worker also
# says it is happening NOW.
_HISTORY_QUESTION_IDS = frozenset({"q19", "q20"})
_CURRENT_MARKERS = (
    "ಈಗ", "ಇವತ್ತು", "ಇಂದು", "ನಿನ್ನೆ", "ಇದೀಗ",
    "अभी", "आज", "कल", "इस समय",
    "now", "today", "yesterday", "currently",
)

# Fields whose PATTERNS ARE THEMSELVES NEGATIVE PHRASES.
#
# The negation window above exists because most patterns are positive symptom
# words - "headache" near "no" means the symptom was denied. `reduced_fetal_
# movement` inverts that: its patterns already read "not moving" / "ಆಡ್ತಿಲ್ಲ"
# (is-not-playing), so the generic rule saw the "not" that IS the symptom and
# suppressed the match. Caught live 2026-09-02 - "baby not moving" detected
# nothing, silently dropping a third-trimester red flag.
#
# Deliberately a per-field exemption, not a change to the negation rule
# itself: "no fits" must still suppress `convulsions`, and it does.
_SELF_NEGATING_FIELDS = frozenset({"reduced_fetal_movement"})


def _find_first(text: str, needles) -> int:
    hits = [i for i in (text.find(n.lower()) for n in needles) if i != -1]
    return min(hits) if hits else -1


def _is_negated(haystack: str, start: int, end: int) -> bool:
    """Negation check for one pattern match at haystack[start:end].

    Looks _NEGATION_WINDOW characters each side, clipped at the nearest
    clause break. After the match, whichever comes first wins: an
    affirmation ("ಬಂತು", "है") means present, a negation means denied.
    Before the match, any negation (English "no fits", or an introductory
    Kannada/Hindi denial in the same clause) means denied.
    """
    before = haystack[max(0, start - _NEGATION_WINDOW):start]
    breaks = list(_CLAUSE_BREAK_RE.finditer(before))
    if breaks:
        before = before[breaks[-1].end():]
    after = haystack[end:end + _NEGATION_WINDOW]
    cut = _CLAUSE_BREAK_RE.search(after)
    if cut:
        after = after[:cut.start()]

    negation_at = _find_first(after, _NEGATIONS)
    affirmed_at = _find_first(after, _AFFIRMATIONS)
    if affirmed_at != -1 and (negation_at == -1 or affirmed_at < negation_at):
        return False
    if negation_at != -1:
        return True
    return any(neg.lower() in before for neg in _NEGATIONS)


def detect_symptoms(text: str, question_id: str | None = None) -> SymptomSet:
    """Extract danger symptoms from what the ASHA worker actually said.

    Negation-aware: "ತಲೆನೋವು ಇಲ್ಲ" (no headache) and "no swelling" must NOT
    register the symptom, or every screening question would flag itself the
    moment it was answered "no".

    `question_id` is the armed intake question. On a past-history question
    (_HISTORY_QUESTION_IDS) a symptom word is only recorded when the answer
    also says it is happening now (_CURRENT_MARKERS); otherwise it is
    history, not an acute danger sign. None keeps the old behaviour.

    Never raises - a detector that crashes a turn is worse than one that misses.
    """
    if not text:
        return SymptomSet()

    # r"\s+", NOT r"s+". The missing backslash (found 2026-09-02) made this
    # strip every letter "s" from the transcript rather than collapse
    # whitespace, so EVERY English pattern containing an "s" had silently
    # never matched: "swelling", "swollen", "severe abdominal pain",
    # "stomach hard", "seizure", "convulsions", "fits" - and "denies" in the
    # negation list. Kannada and Hindi patterns were unaffected (no ASCII
    # "s"), which is why the detector looked like it worked.
    collapsed = re.sub(r"\s+", " ", str(text)).lower()
    haystack = f" {collapsed} "
    if question_id in _HISTORY_QUESTION_IDS and not any(
        marker.lower() in haystack for marker in _CURRENT_MARKERS
    ):
        return SymptomSet()       # history disclosure, not a current sign
    found: dict[str, bool] = {}

    for field, patterns in _SYMPTOM_PATTERNS.items():
        present = False
        for pattern in patterns:
            start = haystack.find(pattern.lower())
            if start == -1:
                continue
            if field not in _SELF_NEGATING_FIELDS and _is_negated(
                haystack, start, start + len(pattern)
            ):
                continue          # explicitly denied - do not record
            present = True
            break
        found[field] = present

    return SymptomSet(**found)


# --------------------------------------------------------------------------
# Deterministic gestational-age detection (2026-09-10)
# --------------------------------------------------------------------------
#
# WHY THIS EXISTS
# ---------------
# q02 ("how many months pregnant?") is a FAST question - IntakeProgress
# already closes it on any substantive reply, including "five months" or
# "ಐದು ತಿಂಗಳು" (confirmed directly: _is_substantive() has no trouble with
# either). The intake CHECKLIST does not get stuck on q02 by itself.
#
# What CAN get stuck: state.gestational_weeks - the typed clinical fact,
# separate from the checklist - only gets set when something calls
# record_gestational_age() with the right number, and that has always
# depended entirely on the model correctly converting "five months" to
# weeks=20 in its own head before calling the tool. If it doesn't (a raw
# months value passed instead of weeks, a skipped tool call, a slow/failed
# identity-resolution turn that the awaiting-identity gate in
# on_user_turn_completed correctly refuses to let a stray voice turn answer
# for OTHER questions - see test_identity_gate.py), gestational_weeks stays
# None, next_question() still returns q02 forever, and Sakhi re-asks it -
# which is what "trapped on q02" actually looks like from the outside.
#
# The fix follows the exact pattern already proven for danger signs:
# detect_symptoms() scans the caller's own words as a safety net independent
# of whether the model calls record_symptoms correctly. detect_gestational_
# age() does the same for months-of-pregnancy phrasing, and
# SakhiAgent._capture_gestational_age() (agent.py) calls it unconditionally,
# same call site as _capture_symptoms - NOT gated by the identity_pending
# check, because that gate exists to stop a NAME-correction turn misfiling
# into an unrelated question (a real, reproduced, tested bug - see
# test_identity_gate.py), not to hold a plainly-stated clinical fact hostage
# until identity happens to resolve.
_MONTH_WORD_TO_NUM: dict[str, int] = {
    # English cardinals
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9,
    # English ordinals, word and digit-suffix forms
    "first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3,
    "fourth": 4, "4th": 4, "fifth": 5, "5th": 5, "sixth": 6, "6th": 6,
    "seventh": 7, "7th": 7, "eighth": 8, "8th": 8, "ninth": 9, "9th": 9,
    # Kannada cardinals (the register q02 is actually asked in)
    "ಒಂದು": 1, "ಎರಡು": 2, "ಮೂರು": 3, "ನಾಲ್ಕು": 4, "ಐದು": 5,
    "ಆರು": 6, "ಏಳು": 7, "ಎಂಟು": 8, "ಒಂಬತ್ತು": 9,
    # Romanized Kannada cardinals — Sarvam STT colloquial output (2026-09-22).
    #
    # Sarvam STT transcribes spoken North Karnataka Kannada into Latin script
    # when the caller uses colloquial pronunciation: "yelu" not "ಏಳು",
    # "tingalu" not "ತಿಂಗಳು". Without these entries the whole family of
    # utterances — "yelu tingalu akka", "7 tingalu", "eradu tingalu" — returns
    # None from detect_gestational_age(), leaving state.gestational_weeks unset
    # and q02 never auto-satisfied, which causes Sakhi to repeatedly rephrase
    # the same gestational-month question instead of advancing. See
    # test_kannada_progression_regression.py for the exact failing call.
    "ondu": 1, "eradu": 2, "erdu": 2, "mooru": 3, "naalku": 4, "naaku": 4,
    "aidu": 5, "aidu": 5, "aaru": 6, "yelu": 7, "yelu": 7,
    "entu": 8, "ombattu": 9, "ombhatu": 9,
    # Romanized Kannada ordinals heard in ASHA calls
    # ("eradaneya garbha dharane" = second pregnancy, but "eradaneya tingalu"
    # would mean second month - both need the same numeric mapping here).
    "eradaneya": 2, "eradu ne": 2, "moorneya": 3, "naalkaneya": 4,
    "aidaneya": 5, "aaraneya": 6, "yellaneya": 7, "yel aneya": 7,
    "entaneya": 8, "ombattaneya": 9,
    # Hindi cardinals
    "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाँच": 5,
    "छह": 6, "छः": 6, "सात": 7, "आठ": 8, "नौ": 9,
    # Hindi ordinals
    "पहला": 1, "पहले": 1, "दूसरा": 2, "दूसरे": 2, "तीसरा": 3, "तीसरे": 3,
    "चौथा": 4, "चौथे": 4, "पांचवां": 5, "पांचवें": 5, "छठा": 6, "छठे": 6,
    "सातवां": 7, "सातवें": 7, "आठवां": 8, "आठवें": 8, "नौवां": 9, "नौवें": 9,
}

# Month-unit words to search for. The number is then read from the text
# immediately BEFORE whichever one is found, rather than matched in one
# combined \b-bounded regex: Python's `re` treats a trailing Kannada/Hindi
# combining vowel sign (e.g. the "ು" in "ಐದು", the "ा"/"ं" in "पांच") as \W,
# not \w - so BOTH a \b anchored right after such a word, AND \w-based word
# tokenization (re.findall(r"[\w']+", ...)), silently drop or misplace that
# trailing mark. Confirmed directly: detect_gestational_age("ಐದು ತಿಂಗಳು")
# returned None with either approach, tokenization included, until this was
# found and switched to the plain-substring technique below. This is the
# same reason detect_symptoms() above matches its Kannada/Hindi patterns via
# plain substring search rather than a word-boundary regex - established
# precedent in this file, not a new workaround.
_MONTH_UNIT_WORDS = (
    "months", "month",
    "ತಿಂಗಳು", "ತಿಂಗಳ",                    # Kannada script
    "tingalu", "tingal", "tingala",         # romanized Kannada (Sarvam STT, 2026-09-22)
    "महीने", "महीना", "माह",               # Hindi script
)
# \b is safe here: digits and the st/nd/rd/th suffixes are pure ASCII, which
# Python's re module has always classified as \w correctly.
_DIGIT_MONTH_RE = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\b", re.IGNORECASE)
# Same word-splitting IntakeProgress._is_substantive() uses (sakhi_intake.py)
# - correct and sufficient for the ASCII English number words, since English
# letters have no combining marks for \w to mishandle.
_WORD_RE = re.compile(r"[\w']+", re.UNICODE)
# Kannada/Hindi number words, longest first so a longer word is never
# shadowed by a shorter one that happens to be a substring of it.
_INDIC_MONTH_WORDS = sorted(
    ((w, n) for w, n in _MONTH_WORD_TO_NUM.items() if not w.isascii()),
    key=lambda pair: len(pair[0]), reverse=True,
)


def detect_gestational_age(text: str) -> int | None:
    """How many weeks pregnant, from a plain-language months mention.

    Returns weeks (month * 4, the same conversion this codebase's own prompt
    already asks the model to do - "third month" means gestational_weeks=12,
    sakhi_prompts.py), or None if nothing matched. Never raises, matching
    detect_symptoms()'s own contract - a detector that crashes a turn is
    worse than one that misses.
    """
    if not text:
        return None
    haystack = str(text).lower()  # a no-op on Kannada/Hindi text - they have no case
    for unit in _MONTH_UNIT_WORDS:
        idx = haystack.find(unit.lower())
        if idx == -1:
            continue
        before = haystack[max(0, idx - 20):idx]

        month = None
        # The number NEAREST the unit. search() took the first number in the
        # 20-char window, so "22 ವರ್ಷ, 7 ತಿಂಗಳು" read 22 (her age), failed the
        # 1-9 check, and the stated seven months was dropped - Sakhi then
        # asked for it again (live-LLM scenario, 2026-09-24).
        digit_matches = list(_DIGIT_MONTH_RE.finditer(before))
        digit_match = digit_matches[-1] if digit_matches else None
        if digit_match:
            month = int(digit_match.group(1))
        else:
            tokens = _WORD_RE.findall(before)
            if tokens:
                month = _MONTH_WORD_TO_NUM.get(tokens[-1])
            if month is None:
                for word, num in _INDIC_MONTH_WORDS:
                    if word in before:
                        month = num
                        break

        if month is not None and 1 <= month <= 9:
            return month * 4

    # Weeks said directly ("28 weeks", "28 ವಾರ", "28 हफ्ते") - q02 asks
    # "how many months OR weeks", and an answer in weeks was never heard.
    for unit in _WEEK_UNIT_WORDS:
        idx = haystack.find(unit)
        if idx == -1:
            continue
        digit_matches = list(_DIGIT_MONTH_RE.finditer(haystack[max(0, idx - 12):idx]))
        if digit_matches:
            weeks = int(digit_matches[-1].group(1))
            if 4 <= weeks <= 42:
                return weeks
    return None


_WEEK_UNIT_WORDS = ("weeks", "week", "ವಾರಗಳು", "ವಾರ", "हफ्ते", "हफ़्ते", "सप्ताह")


# --------------------------------------------------------------------------
# Language latching (2026-09-02)
# --------------------------------------------------------------------------
# The call language is decided ONCE, at dispatch, and then latched. It changes
# only when the ASHA worker explicitly asks for a different one.
#
# Why latching rather than per-turn detection: a code-switched turn is normal
# rural speech, not a language change. An ASHA worker speaking Kannada will
# still say "BP", "tablet", "scan", "delivery" in English, and an ASHA worker
# speaking English will still say "ಅಕ್ಕಾ" or a Kannada village name. A
# detector that re-decides every turn flips the agent's language mid-call on
# those tokens - which is exactly the failure the hard constraint forbids:
# an English caller must receive zero Kannada tokens.
#
# So the ONLY thing that moves the latch is an explicit demand, matched
# below. Everything else - accent, code-switching, a garbled transcript -
# leaves it where it is.
_LANGUAGE_SWITCH_REQUESTS: dict[str, tuple[str, ...]] = {
    "kn": (
        # "speak in Kannada" as an ASHA worker actually says it
        "kannada mathadi", "kannadali mathadi", "kannada matadi",
        "kannada please", "speak kannada", "speak in kannada",
        "talk in kannada", "kannada bekku", "kannada la",
        "ಕನ್ನಡ ಮಾತಾಡಿ", "ಕನ್ನಡದಲ್ಲಿ ಮಾತಾಡಿ", "ಕನ್ನಡ ಬೇಕು", "ಕನ್ನಡದಲ್ಲಿ ಹೇಳಿ",
    ),
    "en": (
        "speak in english", "speak english", "english please",
        "talk in english", "english mathadi", "english bekku",
        "in english please", "say it in english",
        "ಇಂಗ್ಲಿಷ್ ಮಾತಾಡಿ", "ಇಂಗ್ಲಿಷ್ ಬೇಕು", "ಇಂಗ್ಲಿಷ್‌ನಲ್ಲಿ ಹೇಳಿ",
    ),
    "hi": (
        "speak in hindi", "speak hindi", "hindi please", "talk in hindi",
        "hindi mein bolo", "hindi me boliye", "हिंदी में बोलो", "हिंदी बोलिए",
    ),
}


def detect_language_switch_request(text: str) -> "Language | None":
    """An EXPLICIT demand for a different language, or None.

    Returns None for everything else, including turns that merely contain
    words from another language - see the latching rationale above. A `None`
    return means "keep the latch where it is", never "unknown, guess".
    """
    if not text:
        return None
    # Computed outside the f-string: Python 3.10 rejects a backslash inside
    # an f-string expression, and this project runs on 3.10.
    collapsed = re.sub(r"\s+", " ", str(text)).lower().strip()
    haystack = f" {collapsed} "
    for code, phrases in _LANGUAGE_SWITCH_REQUESTS.items():
        for phrase in phrases:
            if phrase.lower() in haystack:
                return Language.parse(code)
    return None


# A name that is really an STT artefact ("umm", "hello", a bare digit string).
_NAME_NOISE_RE = re.compile(r"^(?:u+m+|a+h+|h+m+|hello|hi|namaste|yes|no|ok(?:ay)?)$", re.IGNORECASE)


def clean_person_name(raw: str | None) -> str | None:
    """Normalise a spoken name; return None if it is not plausibly a name.

    Kept permissive on script - Kannada and Devanagari names must pass - so the
    check is on shape and noise words rather than an allowlist of characters.
    """
    if not raw:
        return None
    name = re.sub(r"\s+", " ", str(raw)).strip(" .,-")
    if not name or len(name) > 60:
        return None
    if _NAME_NOISE_RE.match(name):
        return None
    if not any(ch.isalpha() for ch in name):
        return None
    return name


_NAME_PATTERNS = [
    re.compile(
        r"(?:ತಾಯಿಯ?\s*ಹೆಸರು|ಹೆಸರು|taayiya?\s*hesaru|hesaru|maa\s*ka\s*naam|mother(?:'s)?\s*name(?:\s*is)?|her\s+name\s+is)\s*[:=\-]?\s*"
        r"([A-Za-z\u0C80-\u0CFF\s]+?)"
        r"(?:[,.\n]|\s+(?:ಅವರ|avara|ಅವರಿಗೆ|avarige|ವಯಸ್ಸು|vayass?u|age|umar|ವರ್ಷ|varsha|years)|\s*$)",
        re.IGNORECASE,
    ),
    # Sarvam commonly omits the introductory "hesaru/name is" token but
    # preserves the age marker: "Priya Sharma, avara vayasu ippatentu
    # varsha".  The comma/age boundary makes this materially safer than
    # treating the first arbitrary words of every utterance as a name.  This
    # exact shape was the 2026-09-22 incident transcript.
    re.compile(
        r"^\s*([A-Za-z\u0C80-\u0CFF][A-Za-z\u0C80-\u0CFF .'-]{1,58}?)\s*,?\s*"
        r"(?:ಅವರ\s+|ಅವರಿಗೆ\s+|avara\s+|avarige\s+)?"
        r"(?:ವಯಸ್ಸು|vayass?u|age|umar|उम्र)\b",
        re.IGNORECASE,
    ),
]

_AGE_PATTERNS = [
    re.compile(
        r"(?:ವಯಸ್ಸು|vayass?u|age|umar|उम्र)\s*[:=\-]?\s*(\d{1,2})",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(\d{1,2})\s*(?:ವರ್ಷ|varsha|varshagalu|years?|saal|साल)\b",
        re.IGNORECASE,
    ),
    # Explicit name introductions often end "her name is X, 24" without an
    # age unit. This comma-delimited form is evaluated only inside the
    # demographics extractor and still passes the 10-60 biological bound.
    re.compile(r",\s*(\d{2})\b", re.IGNORECASE),
]


def detect_patient_demographics(text: str) -> tuple[str | None, int | None]:
    """Deterministically extract patient name and age from conversational speech.

    Returns (name, age). Only returns non-None for plausible values (name cleaned,
    age 10-60). Never raises.
    """
    if not text:
        return None, None

    from clinical_terms import clean_currency_symbols, normalise_kannada_numbers
    working = clean_currency_symbols(str(text))
    working = normalise_kannada_numbers(working)

    extracted_name = None
    for pattern in _NAME_PATTERNS:
        match = pattern.search(working)
        if match:
            candidate = match.group(1).strip()
            cleaned = clean_person_name(candidate)
            if cleaned and len(cleaned.split()) <= 4:
                extracted_name = cleaned
                break

    extracted_age = None
    for pattern in _AGE_PATTERNS:
        match = pattern.search(working)
        if match:
            try:
                age_val = int(match.group(1))
                if 10 <= age_val <= 60:
                    extracted_age = age_val
                    break
            except (ValueError, TypeError):
                pass

    return extracted_name, extracted_age


_PREGNANCY_ORDINAL_MAP = {
    "ಮೊದಲನೆಯ": 1, "ಮೊದಲನೇ": 1, "ಮೊದಲನೆ": 1, "ಒಂದನೇ": 1, "ಮೊದಲ": 1,
    "modalaneya": 1, "modalane": 1, "modala": 1, "first": 1,
    "ಎರಡನೆಯ": 2, "ಎರಡನೇ": 2, "eradaneya": 2, "eradane": 2, "second": 2,
    "ಮೂರನೆಯ": 3, "ಮೂರನೇ": 3, "mooraneya": 3, "moorane": 3, "third": 3,
    "ನಾಲ್ಕನೆಯ": 4, "ನಾಲ್ಕನೇ": 4, "naalkaneya": 4, "naalkane": 4, "fourth": 4,
    "ಐದನೆಯ": 5, "ಐದನೇ": 5, "aidaneya": 5, "aidane": 5, "fifth": 5,
    # Hindi (2026-09-25: "दूसरा गर्भ है" was not understood and q04 repeated)
    "पहला": 1, "पहली": 1, "दूसरा": 2, "दूसरी": 2, "तीसरा": 3, "तीसरी": 3,
    "चौथा": 4, "चौथी": 4, "पांचवां": 5, "पाँचवाँ": 5, "पांचवीं": 5,
}

# Ordinals that are also the stem of unrelated words: matched only as a whole
# word. "ಮೊದಲ ಹೆರಿಗೆ" (first delivery) = 1, but "ಮೊದಲು"/"ಮೊದಲೇ" mean
# before/already and "ಮೊದಲಿನಿಂದಲೂ" from-the-start - none of them is a count.
# "ಮೊದಲೇ ಹೆರಿಗೆ ಆಗಿದೆ" (already delivered before) is deliberately NOT 1.
_WHOLE_WORD_ORDINALS = frozenset({"ಮೊದಲ"})
_KANNADA_LETTER = r"ಀ-೿"

# Pregnancy nouns: an ordinal OR a plain number next to one is the gravida
# ("2 ಗರ್ಭ" / "ಎರಡನೇ ಗರ್ಭ" = second pregnancy). ಗೃಹ್ಮಧಾರಣೆ is how Sarvam STT
# has written ಗರ್ಭಧಾರಣೆ.
_PREGNANCY_KEYWORDS = (
    r"(?:ಗರ್ಭಧಾರಣೆ|ಗೃಹ್ಮಧಾರಣೆ|ಗರ್ಭಾವಸ್ಥೆ|ಗರ್ಭ|garbhadharane|garbha\s*dharane|garbha"
    r"|pregnancy|gravida|गर्भ|गर्भावस्था|प्रेगनेंसी)"
)

# Delivery / child nouns: only an ORDINAL next to one is the gravida
# ("ಎರಡನೇ ಹೆರಿಗೆ" = this is her 2nd delivery). A plain number counts past
# deliveries or living children - "2 ಹೆರಿಗೆ ಆಗಿದೆ", "2 ಮಗು" - which is the
# PARITY (the gravida would be 3), so it is never returned as the gravida.
_DELIVERY_KEYWORDS = (
    r"(?:ಹೆರಿಗೆ|ಹೇರಿಗೆ|ಪ್ರಸವ|ಮಗು|delivery|herige|बच्चा|डिलीवरी|प्रसव)"
)

_ORDINAL_CONTEXT_RE = re.compile(
    rf"{_PREGNANCY_KEYWORDS}|{_DELIVERY_KEYWORDS}", re.IGNORECASE
)

_PREGNANCY_PATTERNS = [
    re.compile(
        # No trailing \b: after a Kannada vowel sign ("ಗರ್ಭಧಾರಣೆ") \b never
        # matches, so "2 ಗರ್ಭಧಾರಣೆ" used to return None.
        rf"\b(\d{{1,2}})(?:st|nd|rd|th|ನೇ|ನೆಯ)?\s*{_PREGNANCY_KEYWORDS}(?![A-Za-z])",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:gravida|G)\s*[:=\-]?\s*(\d{1,2})\b",
        re.IGNORECASE,
    ),
]


def detect_pregnancy_number(text: str) -> int | None:
    """Extract pregnancy count (gravida) from speech (e.g. 'eradaneya garbha dharane' -> 2).

    Returns 1-10 or None. Never raises.
    """
    if not text:
        return None
    raw = str(text).strip()
    low = raw.lower()

    for word, num in _PREGNANCY_ORDINAL_MAP.items():
        if word not in low:
            continue
        if word in _WHOLE_WORD_ORDINALS:
            match = re.search(
                rf"(?<![{_KANNADA_LETTER}]){re.escape(word)}(?![{_KANNADA_LETTER}])", low
            )
            if not match:
                continue
            idx = match.start()
        else:
            idx = low.find(word)
        window = low[max(0, idx - 10):min(len(low), idx + len(word) + 25)]
        if _ORDINAL_CONTEXT_RE.search(window):
            return num

    for pattern in _PREGNANCY_PATTERNS:
        match = pattern.search(raw)
        if match:
            try:
                num = int(match.group(1))
                if 1 <= num <= 10:
                    return num
            except (ValueError, TypeError):
                pass

    return None


@dataclasses.dataclass
class SakhiCallState:
    """Ground truth for one call, owned by AgentSession.userdata.

    Replaces the clinic booking `CallState` (name/phone/doctor/appointment_id),
    whose summary was injected into every generation and kept steering Sakhi
    back toward asking for a doctor preference. See changed.md C5.

    Mutation goes through methods, not by writing fields from call sites, so
    the risk level can never drift out of sync with the readings that produced
    it - that was the failure mode of the old design, where `record_bp()`
    computed a risk it then threw away.
    """

    session_id: str | None = None
    language: Language = Language.KANNADA

    # Identity
    patient_id: str | None = None
    name: str | None = None
    name_confirmed: bool = False
    village: str | None = None
    phone: str | None = None

    # Clinical
    age: int | None = None
    gestational_weeks: int | None = None
    blood_pressure: BloodPressure | None = None
    symptoms: SymptomSet = dataclasses.field(default_factory=SymptomSet)
    symptoms_screened: bool = False
    assessment: RiskAssessment | None = None

    # Call control
    escalated: bool = False
    abuse_strikes: int = 0
    # Failed patient lookups this call. Drives the spelling fallback: name
    # collection is the dominant bottleneck in production voice agents, and an
    # agent that re-asks the same way twice traps the worker in a loop. One
    # miss is enough to switch strategy.
    lookup_attempts: int = 0

    # True only while the frontend name/age text box is being shown, after a
    # first failed voice lookup. Gates search_patient out of tools_for() so
    # the model has no tool call left to retry identity through voice while
    # it's up, and gates on_user_turn_completed's recording, so a stray
    # spoken turn during this window ("ok, typing now") never gets misfiled
    # as the answer to whatever question happens to be armed. Cleared the
    # instant lock_identity_from_frontend() runs.
    awaiting_identity_box: bool = False

    # True once identity was resolved via the frontend text box rather than
    # voice. Distinct from is_identified (which is already true the moment a
    # name is confirmed by any path) - this specifically marks "don't offer
    # voice-based identity resolution again", so a later correction still
    # goes through search_patient normally if the worker chooses to use it.
    identity_locked: bool = False

    # True once a Tier-1 danger sign (vaginal bleeding, severe abdominal
    # pain/leaking fluid, or any other path to HIGH_RISK) has fired this
    # call. Set once, via lock_intake_on_danger_sign(), and never cleared for
    # the rest of the call - a mother whose bleeding stops being mentioned a
    # few turns later is not "no longer an emergency", the questionnaire
    # should not silently resume. prompt_summary() reads this to replace the
    # normal NEXT QUESTION line with the locked-intake instruction instead of
    # continuing the 26-question inventory. See agent.py's deterministic
    # re-triage path (_capture_symptoms) for where this actually gets set.
    intake_locked: bool = False
    # Which sign triggered the lock, for the escalation audit record
    # (log_teleconsult's trigger_sign field) - "vaginal_bleeding",
    # "severe_abdominal_pain", or "severe_bp"/"symptomatic_bp" for the
    # pre-existing HIGH_RISK paths.
    intake_lock_reason: str | None = None

    # --- Per-TURN write receipts (2026-09-03) ----------------------------
    #
    # Each of these means exactly one thing: "the database write behind this
    # tool actually succeeded, during the turn now being spoken." They exist
    # so the pre-speech guard in agent.py's tts_node can refuse to let Sakhi
    # say "I've recorded her blood pressure" when no such write happened -
    # the model narrating a successful outcome it never delivered.
    #
    # SINGLE-PURPOSE ON PURPOSE, and this is the whole design constraint:
    # they must never be reused to mean anything else, and must never be
    # OR'd with a looser signal. A flag that also means "a patient exists"
    # or "the call is ending" will eventually be read for the wrong meaning
    # and the guard silently stops working on every turn where the other
    # meaning happens to be true - which is how this class of guard has
    # failed before. Note record_symptoms()'s own `persisted` return value
    # is deliberately NOT what sets these: it is OR'd with bool(patient_id)
    # for the model's benefit, which is exactly the looser signal this must
    # not inherit.
    #
    # Reset at the START of every turn by begin_turn(), so a success three
    # turns ago cannot authorise a claim made now.
    bp_record_succeeded: bool = False
    symptoms_record_succeeded: bool = False
    # Set by the guard when it blocks a sentence, read by the next turn's
    # prompt so the model is told to actually call the tool rather than
    # repeating the same unbacked claim.
    unbacked_claim_blocked: str | None = None

    # How many times the ASHA worker explicitly asked for a different
    # language this call. Latching means this is normally 0; a non-zero value
    # in the call report is the signal that the dispatched language was wrong
    # for this worker, which is a routing problem worth seeing.
    language_switches: int = 0

    # Stage-tagged ANC answers that carry no triage logic - weight, IFA
    # adherence, scan done, Td doses, delivery place, 108 awareness.
    anc_checkpoints: dict = dataclasses.field(default_factory=dict)

    # Deterministic position in the 24-question clinical intake.
    #
    # Before this existed, the questionnaire lived only in the system prompt
    # and nothing tracked it: 16 state fields for 24 questions, and a
    # prompt_summary() that reported three facts. The model re-derived its
    # place in the list from the transcript every turn, so it re-asked, it
    # skipped, and - with no checklist to ground "the next question" - it
    # invented questions outright. See sakhi_intake.py.
    intake: IntakeProgress = dataclasses.field(default_factory=IntakeProgress)

    # --- mutators ------------------------------------------------------

    def identify(
        self,
        *,
        patient_id: str | None = None,
        name: str | None = None,
        village: str | None = None,
        phone: str | None = None,
        age: int | None = None,
        gestational_weeks: int | None = None,
        force: bool = False,
    ) -> None:
        """Record whatever identity fields we just learned.

        Only ever fills blanks or upgrades - a later garbled STT turn must not
        wipe a name that was already captured and confirmed.
        """
        if patient_id:
            self.patient_id = patient_id
        cleaned = clean_person_name(name)
        if cleaned:
            if not self.name or not self.name_confirmed or force:
                self.name = cleaned
                self.name_confirmed = True
        if village:
            if not self.village or force:
                self.village = str(village).strip() or None
        if phone:
            digits = re.sub(r"\D", "", str(phone))
            if len(digits) >= 10 and (not self.phone or force):
                self.phone = digits[-10:]
        if isinstance(age, int) and 10 <= age <= 60:
            if not self.age or force:
                self.age = age
        if isinstance(gestational_weeks, int) and 1 <= gestational_weeks <= 45:
            if not self.gestational_weeks or force:
                self.gestational_weeks = gestational_weeks

    def lock_identity_from_frontend(self, *, name: str, age: int | None) -> None:
        """Identity resolved via the frontend text box, bypassing voice/STT.

        Goes through identify() so name-cleaning and name_confirmed stay
        consistent with every other identity path, then additionally clears
        awaiting_identity_box and sets identity_locked so the call never
        re-offers the voice fallback for the rest of the session.
        """
        self.identify(name=name, age=age)
        self.identity_locked = True
        self.awaiting_identity_box = False

    def record_blood_pressure(self, bp: BloodPressure) -> None:
        self.blood_pressure = bp

    def record_symptoms(self, symptoms: SymptomSet) -> None:
        self.symptoms = self.symptoms.merged_with(symptoms)
        self.symptoms_screened = True

    def record_assessment(self, assessment: RiskAssessment) -> None:
        self.assessment = assessment

    def mark_escalated(self) -> None:
        self.escalated = True

    def record_anc_checkpoint(self, stage: int, parameter: str, value: str) -> None:
        """Store one non-clinical-decision ANC answer (weight, IFA, Td, 108...).

        Deliberately a flat dict rather than a field per parameter: these are
        recorded facts that no code branches on, and adding six more typed
        fields to this dataclass for values only the report and the dashboard
        ever read would be schema churn for nothing. Anything the triage
        engine DOES branch on (BP, symptoms, gestation) has a real typed
        field and does not come through here.
        """
        key = str(parameter or "").strip().lower().replace(" ", "_")
        if not key:
            return
        self.anc_checkpoints[key] = {"stage": int(stage), "value": str(value)[:120]}

    def switch_language(self, requested: "Language") -> bool:
        """Move the latched call language. Returns True if it actually moved.

        The ONLY sanctioned way `language` changes after dispatch. Everything
        else in the pipeline reads this field and must never write it - see
        detect_language_switch_request() for why per-turn re-detection is
        forbidden. Idempotent: asking for the language already in force is a
        no-op, so an ASHA worker repeating "Kannada mathadi" does not churn
        the STT/TTS providers rebuilt from it.
        """
        if requested is self.language:
            return False
        self.language = requested
        self.language_switches += 1
        return True

    def lock_intake_on_danger_sign(self, reason: str) -> None:
        """A Tier-1 danger sign reached HIGH_RISK - stop the questionnaire.

        Idempotent: only the FIRST trigger's reason is kept, since that is
        the one the ASHA worker actually heard the advisory for. A second,
        different Tier-1 sign firing later in the same call does not
        overwrite it - it is still one locked call, not a re-lock.
        """
        if self.intake_locked:
            return
        self.intake_locked = True
        self.intake_lock_reason = reason

    def begin_turn(self) -> None:
        """Clear the per-turn write receipts. Call once per user turn.

        Without this the flags are per-CALL, not per-turn, and a successful
        BP write on turn 3 would authorise an unbacked "I've recorded that"
        on turn 9. The guard is only meaningful if the receipt expires with
        the turn that earned it.

        Deliberately does NOT clear `unbacked_claim_blocked`: that one is
        written by the guard at the END of a turn precisely so the NEXT
        turn's prompt can read it. agent.py clears it once it has been shown
        to the model.
        """
        self.bp_record_succeeded = False
        self.symptoms_record_succeeded = False

    # --- projections ---------------------------------------------------

    @property
    def risk_level(self) -> RiskLevel:
        return self.assessment.level if self.assessment else RiskLevel.ROUTINE

    @property
    def is_identified(self) -> bool:
        return bool(self.patient_id or (self.name and self.name_confirmed))

    def prompt_summary(self) -> str:
        """Compact ground-truth block injected into each generation.

        Kept short on purpose - this string is re-sent on every LLM call, so
        every word is prefill latency paid per turn (changed.md L2).
        """
        parts: list[str] = []

        if self.name:
            who = self.name
            if self.patient_id:
                who += f" (id {self.patient_id})"
            if self.village:
                who += f", {self.village}"
            parts.append(f"mother={who}")
        else:
            parts.append("mother=UNKNOWN")

        if self.gestational_weeks:
            parts.append(f"gestational_weeks={self.gestational_weeks}")

        # STATUS ONLY - never an instruction to ask.
        #
        # These lines used to read "bp=NOT TAKEN - ask for it" and
        # "symptoms=NOT SCREENED - ask the three danger questions". They were
        # injected on EVERY turn and appeared BEFORE the intake line, so on turn
        # one the model read an explicit order to ask for blood pressure and
        # obeyed it - jumping straight past the intake flow. Exactly one thing
        # is allowed to say what to ask next, and that is the intake line
        # appended at the end of this summary.
        if self.blood_pressure:
            parts.append(f"bp={self.blood_pressure} (recorded - do NOT ask again)")
        else:
            parts.append("bp=not yet recorded")

        if self.symptoms_screened:
            present = ", ".join(self.symptoms.present_names) or "none reported"
            parts.append(f"symptoms=screened ({present}) - do NOT re-screen")
        else:
            parts.append("symptoms=not yet screened")

        if self.assessment:
            parts.append(f"risk={self.assessment.level.value}")
        if self.escalated:
            parts.append("specialist=ALREADY CONNECTED - do not connect again")

        # Intake position goes LAST so the next question is the final thing
        # the model reads before generating - the strongest position in the
        # context for an instruction we need followed exactly.
        self.intake.sync_from_state(self)
        # Skip any question whose applicable_if predicate now says no (e.g.
        # fetal heart rate before 20 weeks) - see sakhi_intake.py. Run every
        # turn, alongside sync_from_state, so a gating fact learned mid-call
        # (gestational_weeks becoming known) is picked up immediately.
        self.intake.sync_applicability(self)
        if self.intake_locked:
            # A Tier-1 danger sign fired (see lock_intake_on_danger_sign()) -
            # replace the normal NEXT QUESTION line entirely. The 26-question
            # inventory is not resumed for the rest of this call: a mother
            # who is bleeding is not owed the tetanus-immunization question.
            advisory = DANGER_SIGN_ADVISORY.get(self.intake_lock_reason or "", "")
            parts.append(
                "INTAKE LOCKED - a Tier-1 danger sign was detected. Do not ask "
                "any further questionnaire items. Deliver this first-aid "
                f"advisory once, calmly, then confirm the specialist "
                f"connection: \"{advisory}\""
            )
        else:
            parts.append(self.intake.summary())

        return "; ".join(parts)


# --------------------------------------------------------------------------
# DOCUMENT & PLAN mapping (2026-09-10)
# --------------------------------------------------------------------------
# The source "Antenatal Quick Check" PDF closes with a DOCUMENT & PLAN block:
# risk status, next follow-up, investigations due, referral, provider
# signature. This assembles the equivalent from state Sakhi already holds -
# no new capture, no new UI in this pass (that is a separate, larger piece of
# work if the earlier-planned end-of-visit export is wanted too). "Provider
# signature" has no equivalent on a voice call and is omitted rather than
# faked.
_EDD_GESTATION_WEEKS = 40


def build_visit_record(state: "SakhiCallState") -> dict:
    """Map recorded call state onto the PDF's DOCUMENT & PLAN fields.

    EDD is computed from `gestational_weeks` alone (today's date plus the
    weeks remaining to 40), not parsed from the free-text LMP answer (q03) -
    q03 is recorded as raw spoken text with no date parser behind it, and a
    standard gestational-age estimate is the same arithmetic a provider would
    do by hand from the same number. LMP itself is still available separately
    via `state.intake.answered.get("q03")` for anyone who wants the raw
    answer, not just the derived date.
    """
    import datetime as _dt

    assessment = state.assessment
    edd = None
    if state.gestational_weeks:
        weeks_remaining = _EDD_GESTATION_WEEKS - state.gestational_weeks
        edd = (_dt.date.today() + _dt.timedelta(weeks=weeks_remaining)).isoformat()

    investigations_due = [
        qid for qid in ("q13", "q14", "q26")
        if qid not in state.intake.answered
    ]

    return {
        "risk_status": assessment.level.value if assessment else "not yet assessed",
        "follow_up": assessment.recommended_action if assessment else None,
        "investigations_due": investigations_due,
        "referral_required": bool(state.escalated),
        "referral_to": "Bidar District Hospital" if state.escalated else None,
        "estimated_due_date": edd,
        "gestational_weeks": state.gestational_weeks,
        "anc_checkpoints": dict(state.anc_checkpoints),
    }
