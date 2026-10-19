"""Text normalization at the final boundary before speech synthesis.

The LLM and tool layer may return Markdown or compact clinical notation.  TTS
must receive words, not formatting characters or ambiguous slash notation.
Keeping this rule in one small module prevents the REST and fallback TTS paths
from drifting apart.
"""

from __future__ import annotations

import re


class SakhiSpeechNormalizer:
    """Convert generated text into safe, speakable Kannada/Indic prose."""

    _UNSPOKEN_MARKUP = re.compile(r"[*;:#_`~<>\[\]{}()\\|^+=%]")
    _DASHES = re.compile(r"[-\u2010-\u2015]")

    @staticmethod
    def sanitize_for_tts(text: object) -> str:
        if text is None:
            return ""

        cleaned = str(text)
        # A slash is semantic in clinical text (for example BP 140/90).  Do
        # not merely delete it and accidentally join the two values.
        cleaned = cleaned.replace("/", " ಅಥವಾ ")
        cleaned = SakhiSpeechNormalizer._UNSPOKEN_MARKUP.sub(" ", cleaned)
        cleaned = SakhiSpeechNormalizer._DASHES.sub(" ", cleaned)
        return re.sub(r"\s+", " ", cleaned).strip()


__all__ = ["SakhiSpeechNormalizer"]
