"""Question lock: the code asks the intake questions, the model only acknowledges.

Why this exists. The intake tracker (sakhi_intake.IntakeProgress) already
decides deterministically which question comes next, and arms it before each
generation. But the model was still the one saying the question, and a real
LLM run (scripts/live_llm_scenarios.py, 2026-09-24) showed what that costs:
with q08 "is she taking iron tablets?" armed, the model asked "does she have
pain, fever or bleeding?" - a question of its own, in the wrong stage, whose
answer was then filed under q08. That is the "hallucinates / asks something
else / asks again" behaviour ASHA workers reported.

What it does, per LLM pass, in O(length of the reply):

  1. The model's text is split into sentences as it streams.
  2. A sentence is dropped when it is a question (ends in ?), or when it
     reads like one of the checklist questions (character-trigram overlap) -
     i.e. the model trying to ask something itself, right or wrong.
  3. Everything else - the short acknowledgement, "I did not hear that
     clearly", a medicine refusal - passes through unchanged, one sentence at
     a time, so the first words still reach TTS as soon as they exist.
  4. When the pass ends without tool calls (the final pass of the turn), the
     armed question is appended verbatim, in the call's language, with its
     own [VAD: FAST|SLOW] tag.

So the order, wording and language of every question come from
INTAKE_QUESTIONS, never from sampling, and a question can only be repeated
when the tracker itself left it armed (an unclear answer, bounded by the
tracker's stall limit).

Pure and synchronous: agent.py feeds it chunks; tests drive it directly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Sentence ends: Latin . ! ? and the Devanagari danda. Kannada uses the Latin
# full stop. A decimal point ("37.5") is not an end - require a space/end after.
_BOUNDARY = re.compile(r"([.!?।])(\s+|$)")
_TAG = re.compile(r"\[\s*VAD\s*:\s*[A-Z]+\s*\]", re.I)
_SPACE = re.compile(r"\s+")
_CODE = re.compile(r"[{}\[\]]|\"\w+\"\s*:")

# A sentence containing this share of a checklist question's character
# trigrams is restating that question (e.g. the imperative "ತಾಯಿಯ ತೂಕ ಎಷ್ಟು
# ಕೆಜಿ ಇದೆ ಹೇಳಿ."). Measured against the QUESTION, not the sentence, so a
# short acknowledgement ("ಸರಿ.") that happens to share a word is never lost.
SIMILAR = 0.5
# An acknowledgement is short. Anything longer is the model narrating or
# lecturing; keep the first MAX_KEPT sentences only.
MAX_KEPT = 2


def _mostly_latin(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    return sum(1 for c in letters if c.isascii()) / len(letters) > 0.6


def _trigrams(text: str) -> set[str]:
    squashed = _SPACE.sub(" ", _TAG.sub("", text)).strip().lower()
    return {squashed[i:i + 3] for i in range(max(0, len(squashed) - 2))}


def similarity(a: str, b: str) -> float:
    """Share of `a`'s character trigrams also found in `b` (0..1). Script
    agnostic - works the same for Kannada, Devanagari and Latin text."""
    ta, tb = _trigrams(a), _trigrams(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta)


@dataclass
class QuestionLock:
    """One LLM pass. `question` is the armed question's text in the call's
    language (None = no lock this pass, pass text through); `vad` its pacing
    tag; `checklist` every question's text in that language."""

    question: str | None
    vad: str = "FAST"
    checklist: tuple[str, ...] = ()
    _buf: str = ""
    kept: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)

    @property
    def active(self) -> bool:
        return bool(self.question)

    def _judge(self, sentence: str) -> str | None:
        clean = _TAG.sub("", sentence).strip()
        if not clean:
            return None
        # A tool call written out as text ({"name": "...", "parameters": {}})
        # - seen from the real chain on a danger-sign turn. Never speech.
        if _CODE.search(clean):
            self.dropped.append(clean)
            return None
        # Kannada/Hindi call: an English sentence is never spoken. A Latin-
        # script name inside a Kannada sentence ("ಸರಿ, Priya Sharma, ...")
        # stays - only sentences that are mostly Latin letters are dropped.
        if self.question and not _mostly_latin(self.question) and _mostly_latin(clean):
            self.dropped.append(clean)
            return None
        if clean.endswith("?") or clean.endswith("?।"):
            self.dropped.append(clean)
            return None
        if any(similarity(q, clean) >= SIMILAR for q in (self.question, *self.checklist) if q):
            self.dropped.append(clean)
            return None
        if len(self.kept) >= MAX_KEPT:
            self.dropped.append(clean)
            return None
        self.kept.append(clean)
        return clean

    def feed(self, text: str) -> list[str]:
        """Model text in; the sentences that may be spoken now, out.

        `checklist` should hold the questions NOT yet answered: the
        acknowledgement of the one just answered naturally reuses its words.
        """
        if not self.active:
            return [text] if text else []
        self._buf += text
        out = []
        while True:
            match = _BOUNDARY.search(self._buf)
            if not match:
                break
            sentence, self._buf = self._buf[: match.end(1)], self._buf[match.end():]
            kept = self._judge(sentence)
            if kept:
                out.append(kept + " ")
        return out

    def finish(self, *, final: bool) -> list[str]:
        """End of the pass. `final` = no tool calls this pass, so this is the
        text the turn ends on and the armed question goes last."""
        if not self.active:
            return []
        out = []
        tail, self._buf = self._buf, ""
        kept = self._judge(tail) if tail.strip() else None
        if kept:
            # An unterminated acknowledgement would run straight into the
            # question for TTS ("ಮೊದಲನೆ ಗರ್ಭವತಿ ಕೊನೆಯ ಮುಟ್ಟಿನ...").
            if final and kept[-1] not in ".!?।,":
                kept += "."
            out.append(kept + " ")
        if final:
            out.append(f"{self.question} [VAD: {self.vad}]")
        return out
