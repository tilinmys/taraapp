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
    """The three pre-eclampsia danger symptoms screened on every visit.

    Keyword-only construction is deliberate: the previous signature was
    `record_symptoms(pid, headache, blurry_vision, swelling)`, three positional
    bools that a caller can transpose without any error surfacing.
    """

    headache: bool = False
    blurry_vision: bool = False
    swelling: bool = False

    def __post_init__(self) -> None:
        for field in dataclasses.fields(self):
            if not isinstance(getattr(self, field.name), bool):
                raise TypeError(f"Symptom '{field.name}' must be a bool.")

    @property
    def any_present(self) -> bool:
        return self.headache or self.blurry_vision or self.swelling

    @property
    def present_names(self) -> tuple[str, ...]:
        names = {
            "headache": "severe headache",
            "blurry_vision": "blurry vision",
            "swelling": "facial or hand swelling",
        }
        return tuple(label for field, label in names.items() if getattr(self, field))

    def merged_with(self, other: "SymptomSet") -> "SymptomSet":
        """Symptoms accumulate across a call - a later 'no swelling' must not
        erase an earlier 'yes headache'."""
        return SymptomSet(
            headache=self.headache or other.headache,
            blurry_vision=self.blurry_vision or other.blurry_vision,
            swelling=self.swelling or other.swelling,
        )

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
    "headache": (
        "ತಲೆನೋವು", "ತಲೆ ನೋವು", "ತಲೆನೋವಿ", "ಹೆಡ್ ಏಕ್", "ಹೆಡ್‌ಏಕ್",
        "सिरदर्द", "सर दर्द", "सिर में दर्द",
        "headache", "head ache", "head-ache",
    ),
    "blurry_vision": (
        "ಕಣ್ಣು ಮಂಜು", "ಮಂಜಾಗು", "ಮಂಜು", "ದೃಷ್ಟಿ ಮಂದ", "ಕಣ್ಣು ಮಂದ",
        "धुंधला", "धुंधली", "दृष्टि",
        "blurry", "blurred", "blurry vision", "double vision",
    ),
    "swelling": (
        "ಊತ", "ಬಾವು", "ಊದಿಕೊಂಡ", "ಊದಿದೆ",
        "सूजन", "सूजा",
        "swelling", "swollen", "puffiness",
    ),
}

# Negation markers. Kannada and Hindi place negation AFTER the noun
# ("ತಲೆನೋವು ಇಲ್ಲ" = headache not-present), English places it before
# ("no headache"), so the window is checked on both sides.
_NEGATIONS = (
    "ಇಲ್ಲ", "ಇಲ್ಲವೇ", "ಇರಲಿಲ್ಲ",
    "नहीं", "नही",
    " no ", " not ", " without ", "denies", "negative",
)

_NEGATION_WINDOW = 28


def detect_symptoms(text: str) -> SymptomSet:
    """Extract danger symptoms from what the ASHA worker actually said.

    Negation-aware: "ತಲೆನೋವು ಇಲ್ಲ" (no headache) and "no swelling" must NOT
    register the symptom, or every screening question would flag itself the
    moment it was answered "no".

    Never raises - a detector that crashes a turn is worse than one that misses.
    """
    if not text:
        return SymptomSet()

    haystack = f" {re.sub(r's+', ' ', str(text)).lower()} "
    found: dict[str, bool] = {}

    for field, patterns in _SYMPTOM_PATTERNS.items():
        present = False
        for pattern in patterns:
            start = haystack.find(pattern.lower())
            if start == -1:
                continue
            window = haystack[
                max(0, start - _NEGATION_WINDOW):
                start + len(pattern) + _NEGATION_WINDOW
            ]
            if any(neg.lower() in window for neg in _NEGATIONS):
                continue          # explicitly denied - do not record
            present = True
            break
        found[field] = present

    return SymptomSet(**found)


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
    ) -> None:
        """Record whatever identity fields we just learned.

        Only ever fills blanks or upgrades - a later garbled STT turn must not
        wipe a name that was already captured and confirmed.
        """
        if patient_id:
            self.patient_id = patient_id
        cleaned = clean_person_name(name)
        if cleaned:
            self.name = cleaned
            self.name_confirmed = True
        if village:
            self.village = str(village).strip() or None
        if phone:
            digits = re.sub(r"\D", "", str(phone))
            if len(digits) >= 10:
                self.phone = digits[-10:]
        if isinstance(age, int) and 10 <= age <= 60:
            self.age = age
        if isinstance(gestational_weeks, int) and 1 <= gestational_weeks <= 45:
            self.gestational_weeks = gestational_weeks

    def record_blood_pressure(self, bp: BloodPressure) -> None:
        self.blood_pressure = bp

    def record_symptoms(self, symptoms: SymptomSet) -> None:
        self.symptoms = self.symptoms.merged_with(symptoms)
        self.symptoms_screened = True

    def record_assessment(self, assessment: RiskAssessment) -> None:
        self.assessment = assessment

    def mark_escalated(self) -> None:
        self.escalated = True

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
        parts.append(self.intake.summary())

        return "; ".join(parts)
