"""Patient persistence behind one interface.

Two problems in the pre-refactor code motivated this module:

1. The clinical tools called `read_db()` / `write_db()` - free functions doing a
   full synchronous JSON file read *and* write - directly on the asyncio event
   loop, inside a live call. Every tool call stalled audio (changed.md L6).
2. There was no interface at all, so swapping the mock for real SixtyDB meant
   rewriting the body of every tool (oops.md §2.2).

`PatientRepository` is the seam. `MockSixtyDBRepository` keeps the whole dataset
in memory and flushes to disk write-behind, off the event loop, so a tool call
costs a dict lookup instead of a file read.
"""

from __future__ import annotations

import abc
import asyncio
import dataclasses
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sakhi_domain import BloodPressure, RiskAssessment, RiskLevel, SymptomSet

logger = logging.getLogger("sakhi.repository")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Kannada / Devanagari -> Latin, enough to make names comparable across scripts.
#
# WHY: seeded records hold "Suma Gowda" in Latin, while Sarvam STT returns
# "ಸುಮಾ ಗೌಡ" in Kannada script. A plain substring match scores zero, so on a
# live call EVERY existing mother missed lookup and the model registered a
# duplicate - it created "Suman Goud" alongside the real "Suma Gowda".
#
# This is transliteration for MATCHING ONLY. It is lossy and is never shown to
# anyone or written back to a record.
_TRANSLIT = {
    # Kannada vowels
    "ಅ": "a", "ಆ": "aa", "ಇ": "i", "ಈ": "ii", "ಉ": "u", "ಊ": "uu",
    "ಎ": "e", "ಏ": "ee", "ಐ": "ai", "ಒ": "o", "ಓ": "oo", "ಔ": "au",
    # Kannada consonants
    "ಕ": "k", "ಖ": "kh", "ಗ": "g", "ಘ": "gh", "ಚ": "ch", "ಛ": "chh",
    "ಜ": "j", "ಝ": "jh", "ಟ": "t", "ಠ": "th", "ಡ": "d", "ಢ": "dh",
    "ಣ": "n", "ತ": "t", "ಥ": "th", "ದ": "d", "ಧ": "dh", "ನ": "n",
    "ಪ": "p", "ಫ": "ph", "ಬ": "b", "ಭ": "bh", "ಮ": "m", "ಯ": "y",
    "ರ": "r", "ಲ": "l", "ವ": "v", "ಶ": "sh", "ಷ": "sh", "ಸ": "s",
    "ಹ": "h", "ಳ": "l",
    # Devanagari
    "अ": "a", "आ": "aa", "इ": "i", "ई": "ii", "उ": "u", "ऊ": "uu",
    "ए": "e", "ऐ": "ai", "ओ": "o", "औ": "au",
    "क": "k", "ख": "kh", "ग": "g", "घ": "gh", "च": "ch", "ज": "j",
    "ट": "t", "ठ": "th", "ड": "d", "ण": "n", "त": "t", "थ": "th",
    "द": "d", "ध": "dh", "न": "n", "प": "p", "फ": "ph", "ब": "b",
    "भ": "bh", "म": "m", "य": "y", "र": "r", "ल": "l", "व": "v",
    "श": "sh", "ष": "sh", "स": "s", "ह": "h",
}


def match_key(name: str | None) -> str:
    """Fold a name to a script-independent, vowel-light comparison key.

    Vowels are dropped after transliteration because they are exactly what
    varies between spellings of the same Indian name ("Gowda"/"Gouda"/"Goud"),
    and because Indic vowel signs are separate codepoints this function does not
    map. Consonant skeletons are far more stable.
    """
    if not name:
        return ""
    out = []
    for ch in str(name).strip().lower():
        if ch in _TRANSLIT:
            out.append(_TRANSLIT[ch])
        elif ch.isalpha() and ch.isascii():
            out.append(ch)
        # everything else (vowel signs, virama, spaces, punctuation) is dropped
    key = "".join(out)
    return re.sub(r"[aeiou]", "", key)


def name_similarity(a: str | None, b: str | None) -> float:
    """0..1 similarity between two names, across scripts."""
    from difflib import SequenceMatcher

    ka, kb = match_key(a), match_key(b)
    if not ka or not kb:
        return 0.0
    if ka == kb:
        return 1.0
    return SequenceMatcher(None, ka, kb).ratio()


# Villages in the Bidar pilot catchment. Proper nouns are where ASR degrades
# worst, and these are the ones an ASHA worker says on every call.
BIDAR_VILLAGES = (
    "Aurad", "Kamalnagar", "Bhalki", "Basavakalyan", "Humnabad",
    "Chitguppa", "Hulsoor", "Santpur", "Janwada", "Manhalli",
    "Nirna", "Kamthana", "Mannaekhelli", "Halhalli", "Chincholi",
)


def export_stt_biasing_keywords(patients: "list[PatientRecord] | None" = None) -> list[str]:
    """Vocabulary of proper nouns the STT layer is likely to mangle.

    IMPORTANT - what this can and cannot do:

    `SarvamSTT` exposes **no keyword-biasing parameter**. An earlier version of
    agent.py built exactly such a list and passed it nowhere; it was removed as
    dead code (changed.md C11). Reinstating that would repeat the mistake.

    So this list is not sent to the recogniser. It is the reference vocabulary
    for `correct_proper_noun()`, which snaps a mangled transcript back to a
    known name AFTER recognition. Same failure addressed, by the only mechanism
    the provider actually supports.

    If Sarvam adds keyword boosting, this is the function to feed it.
    """
    vocabulary = list(BIDAR_VILLAGES)
    for record in patients or []:
        if record.name:
            vocabulary.append(record.name)
        if record.village:
            vocabulary.append(record.village)
    # Stable, de-duplicated.
    return sorted(set(vocabulary))


def looks_spelled(text: str) -> bool:
    """True when the worker is spelling a name out rather than saying it.

    ASR renders a spelled name in several shapes depending on how she paces it:
    "S, U, M, A" / "S. U. M. A." / "s u m a" / "ಸ, ು, ಮ, ಾ". They all share one
    property - almost every token is a single character.
    """
    tokens = [tok for tok in re.split(r"[\s,.\-]+", (text or "").strip()) if tok]
    if len(tokens) < 3:
        return False
    singles = sum(1 for tok in tokens if len(tok) == 1)
    return singles >= max(3, int(len(tokens) * 0.7))


def assemble_spelled_name(text: str) -> str | None:
    """Reconstruct a name from spelled-out letters, or None if not spelled.

    RESTORED. This existed for the clinic script and was deleted with the rest
    of the English fast path, which left the spelling fallback a dead end: the
    tools asked the ASHA worker to spell the name, she spelled it, and
    `search_patient` then looked up the literal string "S, U, M, A" and found
    nothing. She had done exactly what was asked and got the same failure.

    Only the separators are removed - no transliteration, no case forcing -
    because `match_key()` downstream already handles script and vowel-drop
    variation, and doing it twice would mangle Kannada letters.
    """
    if not looks_spelled(text):
        return None
    letters = [tok for tok in re.split(r"[\s,.\-]+", text.strip()) if tok]
    assembled = "".join(letters)
    return assembled or None


def spell_out(name: str) -> str:
    """Format a name for reading BACK to the worker, one letter at a time.

    "Priya" -> "P, R, I, Y, A". The commas are load-bearing: the TTS layer is
    clause-buffered and treats punctuation as a pause, so this is what makes
    the letters land separately instead of as a slurred word.
    """
    # Uppercased so the letters are unambiguous when read back. `upper()` is a
    # no-op for Kannada and Devanagari, which have no case, so this is safe for
    # a name in any of the three scripts.
    letters = [ch.upper() for ch in (name or "").strip() if not ch.isspace()]
    return ", ".join(letters)


def correct_proper_noun(
    heard: str | None,
    vocabulary: "list[str]",
    cutoff: float = 0.75,
) -> "str | None":
    """Snap a misheard proper noun to the closest known one, or leave it alone.

    Uses the same cross-script key as patient matching, so "ಕಮಲನಗರ" resolves to
    "Kamalnagar". Returns the corrected term, or the original when nothing
    clears the cutoff - guessing a village is worse than admitting uncertainty.
    """
    if not heard or not vocabulary:
        return heard
    best, best_score = heard, cutoff
    for candidate in vocabulary:
        score = name_similarity(heard, candidate)
        if score > best_score:
            best, best_score = candidate, score
    return best


def normalize_phone(value: str | None) -> str | None:
    """Reduce any spoken/stored phone shape to its last 10 digits.

    The seeded mock records store phones digit-separated ("9-8-7-6-..."), while
    STT yields runs of digits and callers give numbers with spaces or a +91
    prefix. All three must match the same patient.
    """
    if not value:
        return None
    digits = re.sub(r"\D", "", str(value))
    return digits[-10:] if len(digits) >= 10 else (digits or None)


@dataclasses.dataclass
class PatientRecord:
    """A mother's record as the agent sees it."""

    patient_id: str
    name: str
    age: int | None = None
    village: str | None = None
    phone: str | None = None
    gestational_weeks: int | None = None
    risk_status: RiskLevel = RiskLevel.ROUTINE
    symptoms: SymptomSet = dataclasses.field(default_factory=SymptomSet)
    history: list[dict[str, Any]] = dataclasses.field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "PatientRecord":
        """Tolerant of the seeded JSON, which predates this model.

        Legacy records use "NORMAL"/"HIGH" for risk where the domain model uses
        ROUTINE/HIGH_RISK, and may omit fields entirely.
        """
        legacy_risk = str(raw.get("risk_status") or "").upper()
        risk_map = {
            "NORMAL": RiskLevel.ROUTINE,
            "ROUTINE": RiskLevel.ROUTINE,
            "OBSERVATION": RiskLevel.OBSERVATION,
            "MODERATE": RiskLevel.MODERATE,
            "HIGH": RiskLevel.HIGH_RISK,
            "HIGH_RISK": RiskLevel.HIGH_RISK,
        }
        raw_symptoms = raw.get("symptoms") or {}
        return cls(
            patient_id=str(raw.get("patient_id", "")),
            name=str(raw.get("name", "")),
            age=raw.get("age"),
            village=raw.get("village"),
            phone=normalize_phone(raw.get("phone")),
            gestational_weeks=raw.get("gestational_weeks"),
            risk_status=risk_map.get(legacy_risk, RiskLevel.ROUTINE),
            symptoms=SymptomSet(
                headache=bool(raw_symptoms.get("headache", False)),
                blurry_vision=bool(raw_symptoms.get("blurry_vision", False)),
                swelling=bool(raw_symptoms.get("swelling", False)),
            ),
            history=list(raw.get("history") or []),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "patient_id": self.patient_id,
            "name": self.name,
            "age": self.age,
            "village": self.village,
            "phone": self.phone,
            "gestational_weeks": self.gestational_weeks,
            "risk_status": self.risk_status.value,
            "symptoms": self.symptoms.to_dict(),
            "history": self.history,
        }

    def spoken_summary(self) -> str:
        """One line the LLM can read back without re-deriving anything."""
        bits = [self.name]
        if self.age:
            bits.append(f"{self.age} years")
        if self.village:
            bits.append(self.village)
        if self.gestational_weeks:
            bits.append(f"{self.gestational_weeks} weeks pregnant")
        if self.risk_status is not RiskLevel.ROUTINE:
            bits.append(f"previously flagged {self.risk_status.value}")
        return ", ".join(str(b) for b in bits if b)


class PatientRepository(abc.ABC):
    """The persistence seam.

    Every method is async because the real SixtyDB backend is an HTTP API. The
    mock honours the same contract so swapping implementations changes no call
    site - that is the entire point of this class existing.
    """

    @abc.abstractmethod
    async def find_patients(
        self, *, name: str | None = None, phone: str | None = None
    ) -> list[PatientRecord]:
        """Search by name substring or exact normalised phone."""

    @abc.abstractmethod
    async def get_patient(self, patient_id: str) -> PatientRecord | None:
        ...

    @abc.abstractmethod
    async def create_patient(
        self,
        *,
        name: str,
        age: int | None = None,
        gestational_weeks: int | None = None,
        village: str | None = None,
        phone: str | None = None,
    ) -> PatientRecord:
        ...

    @abc.abstractmethod
    async def record_blood_pressure(
        self, patient_id: str, bp: BloodPressure, assessment: RiskAssessment
    ) -> None:
        ...

    @abc.abstractmethod
    async def record_symptoms(self, patient_id: str, symptoms: SymptomSet) -> None:
        ...

    @abc.abstractmethod
    async def log_teleconsult(
        self,
        patient_id: str,
        reason: str,
        assessment: RiskAssessment | None = None,
        *,
        trigger_sign: str | None = None,
    ) -> str:
        """Append an immutable audit entry; return the channel/log id.

        `trigger_sign` (added 2026-08-30) records which Tier-1 danger sign
        actually forced the escalation - "vaginal_bleeding",
        "severe_abdominal_pain", "severe_bp", or "symptomatic_bp" - so the
        log-only escalation record (this is a DB write, never a real phone
        call or SMS - see sakhi_tools.py's connect_smfmi_specialist) lets a
        reviewer distinguish which emergency actually happened, not just
        that "some" HIGH_RISK escalation did.
        """

    async def close(self) -> None:
        """Flush anything buffered. Safe to call more than once."""
        return None

    async def record_telemetry(
        self,
        events: list[dict[str, Any]],
        turns: list[dict[str, Any]],
        health: list[dict[str, Any]],
    ) -> None:
        """Persist one batch of pipeline events, conversation turns and
        component-health upserts (see sakhi_telemetry.TelemetryWriter).

        Not abstract: a backend with no telemetry tables (the JSON demo mock)
        simply discards them, and the JSONL pipeline log remains the record.
        The SQL repositories override this with a single batched write.
        """
        return None


class MockSixtyDBRepository(PatientRepository):
    """In-memory SixtyDB stand-in with write-behind persistence.

    Read path: pure dict/list access, no I/O, so a tool call adds no measurable
    latency to a turn.

    Write path: mutate memory, mark dirty, and let a single debounced background
    task do the `json.dump` inside `asyncio.to_thread`. A burst of BP + symptoms
    + teleconsult writes in one turn therefore costs one file write, off-loop,
    after the caller has already heard the reply.
    """

    def __init__(
        self,
        path: str | Path = "mock_sixtydb.json",
        *,
        flush_delay: float = 0.5,
    ) -> None:
        self._path = Path(path)
        self._flush_delay = flush_delay
        # Tuned to accept cross-script and spelling variants of the same name
        # while refusing genuinely different mothers. Raise it if unrelated
        # names start matching; lower it if duplicates reappear.
        self._name_match_threshold = float(os.getenv("SIXTYDB_NAME_MATCH_THRESHOLD", "0.75"))
        self._data: dict[str, Any] = {"patients": [], "bp_records": [], "teleconsult_logs": []}
        self._lock = asyncio.Lock()
        self._dirty = False
        self._flush_task: asyncio.Task | None = None
        self._loaded = False

    # --- lifecycle -----------------------------------------------------

    async def load(self) -> "MockSixtyDBRepository":
        """Read the seed file once, off the event loop. Idempotent."""
        if self._loaded:
            return self
        try:
            raw = await asyncio.to_thread(self._read_file)
        except Exception:
            logger.warning("Could not read %s; starting empty", self._path, exc_info=True)
            raw = None
        if raw:
            # Defensive: a hand-edited seed file may omit collections.
            for key in ("patients", "bp_records", "teleconsult_logs"):
                raw.setdefault(key, [])
            self._data = raw
        self._loaded = True
        logger.info(
            "Mock SixtyDB loaded: %d patients, %d BP records",
            len(self._data["patients"]),
            len(self._data["bp_records"]),
        )
        return self

    def _read_file(self) -> dict[str, Any] | None:
        if not self._path.exists():
            return None
        with self._path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def _write_file(self, snapshot: dict[str, Any]) -> None:
        # Write to a temp file and replace, so a crash mid-write cannot leave a
        # truncated JSON file that fails to load on the next call.
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(snapshot, handle, indent=2, ensure_ascii=False)
        tmp.replace(self._path)

    def _mark_dirty(self) -> None:
        self._dirty = True
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = asyncio.create_task(self._flush_later())

    async def _flush_later(self) -> None:
        try:
            await asyncio.sleep(self._flush_delay)
            await self.flush()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Deferred SixtyDB flush failed", exc_info=True)

    async def flush(self) -> None:
        if not self._dirty:
            return
        async with self._lock:
            snapshot = json.loads(json.dumps(self._data))  # cheap deep copy
            self._dirty = False
        try:
            await asyncio.to_thread(self._write_file, snapshot)
        except Exception:
            self._dirty = True  # keep it queued for the next attempt
            logger.warning("SixtyDB write failed; data retained in memory", exc_info=True)

    async def close(self) -> None:
        if self._flush_task is not None and not self._flush_task.done():
            self._flush_task.cancel()
        await self.flush()

    # --- queries -------------------------------------------------------

    async def find_patients(
        self, *, name: str | None = None, phone: str | None = None
    ) -> list[PatientRecord]:
        search_name = (name or "").strip().lower() or None
        search_phone = normalize_phone(phone)
        if not search_name and not search_phone:
            return []

        matches: list[PatientRecord] = []
        near: list[tuple[float, PatientRecord]] = []

        for raw in self._data["patients"]:
            record = PatientRecord.from_dict(raw)

            if search_phone and record.phone and record.phone == search_phone:
                matches.append(record)
                continue
            if not search_name:
                continue

            # Same-script substring match first - cheap and exact.
            if search_name in record.name.lower():
                matches.append(record)
                continue

            # Cross-script / spelling-variant match. This is what makes a
            # Kannada-script name from STT find a Latin-script stored record,
            # and what stops "Suman Goud" being registered as a new mother
            # alongside the existing "Suma Gowda".
            score = name_similarity(search_name, record.name)
            if score >= self._name_match_threshold:
                near.append((score, record))

        if matches:
            return matches

        near.sort(key=lambda pair: pair[0], reverse=True)
        if near:
            logger.info(
                "Fuzzy name match: %r -> %r (score %.2f)",
                name, near[0][1].name, near[0][0],
            )
        return [record for _, record in near]

    async def get_patient(self, patient_id: str) -> PatientRecord | None:
        raw = self._raw_patient(patient_id)
        return PatientRecord.from_dict(raw) if raw else None

    def _raw_patient(self, patient_id: str) -> dict[str, Any] | None:
        for raw in self._data["patients"]:
            if str(raw.get("patient_id")) == str(patient_id):
                return raw
        return None

    # --- mutations -----------------------------------------------------

    async def create_patient(
        self,
        *,
        name: str,
        age: int | None = None,
        gestational_weeks: int | None = None,
        village: str | None = None,
        phone: str | None = None,
    ) -> PatientRecord:
        async with self._lock:
            # Monotonic id derived from the highest existing number, not from
            # len(patients) - the old scheme (`M-{len+201}`) reissued an id
            # already in use as soon as any record was removed.
            highest = 200
            for raw in self._data["patients"]:
                match = re.search(r"(\d+)$", str(raw.get("patient_id", "")))
                if match:
                    highest = max(highest, int(match.group(1)))
            record = PatientRecord(
                patient_id=f"M-{highest + 1}",
                name=name,
                age=age,
                village=village,
                phone=normalize_phone(phone),
                gestational_weeks=gestational_weeks,
            )
            self._data["patients"].append(record.to_dict())
            self._mark_dirty()
        logger.info("Registered mother %s (%s)", record.name, record.patient_id)
        return record

    async def record_blood_pressure(
        self, patient_id: str, bp: BloodPressure, assessment: RiskAssessment
    ) -> None:
        async with self._lock:
            self._data["bp_records"].append(
                {
                    "patient_id": patient_id,
                    "recorded_at": _now_iso(),
                    "systolic": bp.systolic,
                    "diastolic": bp.diastolic,
                    "risk_level": assessment.level.value,
                    "reasons": list(assessment.reasons),
                }
            )
            raw = self._raw_patient(patient_id)
            if raw is not None:
                raw.setdefault("history", []).append(
                    {
                        "date": _now_iso(),
                        "bp": str(bp),
                        "risk_level": assessment.level.value,
                    }
                )
                # Risk status only ever ratchets upward within a call - a later
                # normal reading must not silently clear a red alert already
                # raised and acted on.
                current = PatientRecord.from_dict(raw).risk_status
                if current < assessment.level:
                    raw["risk_status"] = assessment.level.value
            self._mark_dirty()

    async def record_symptoms(self, patient_id: str, symptoms: SymptomSet) -> None:
        async with self._lock:
            raw = self._raw_patient(patient_id)
            if raw is None:
                return
            existing = PatientRecord.from_dict(raw).symptoms
            raw["symptoms"] = existing.merged_with(symptoms).to_dict()
            self._mark_dirty()

    async def log_teleconsult(
        self,
        patient_id: str,
        reason: str,
        assessment: RiskAssessment | None = None,
        *,
        trigger_sign: str | None = None,
    ) -> str:
        log_id = f"CONN-{int(time.time() * 1000)}"
        async with self._lock:
            self._data["teleconsult_logs"].append(
                {
                    "log_id": log_id,
                    "patient_id": patient_id,
                    "reason": reason,
                    "risk_level": assessment.level.value if assessment else None,
                    "reasons": list(assessment.reasons) if assessment else [],
                    "trigger_sign": trigger_sign,
                    "timestamp": _now_iso(),
                    "sip_status": "CONNECTED_MOCK",
                }
            )
            self._mark_dirty()
        logger.warning("SMFMI teleconsult logged for %s: %s", patient_id, reason)
        return log_id
