"""Shared TTS voice/provider catalog - the single source of truth for both
agent.py (LiveKit worker) and frontend/local_server.py (token/dispatch API).

Deliberately dependency-free (no livekit imports) so local_server.py can
import it without pulling in the full agent runtime. Voice IDs here were
verified live against each provider's API on 2026-07-10 - see
scripts/tts_benchmark.py for the latency data behind the curation choices.

Every entry is hand-picked for a calm, professional, Indian-accented
clinic-receptionist tone (MyStree Clinic, Indiranagar). Smallest.ai exposes
hundreds of Indian voices; dumping all of them into a dropdown would be
unusable and unvetted, so only voices that tested well for tone + reliability
are listed. Rumik has no fixed voice catalog at all - see the RUMIK_VOICES
comment below for how that provider's "voices" are constructed. Gemini's
voices are Google's standard prebuilt presets (no Indian-accent tagging
upstream); gemini_wrappers.py steers them toward en-IN via speechConfig's
languageCode on every request. Four providers total: sarvam, rumik,
smallest, gemini - see PROVIDERS below. Add more only after benchmarking
them.
"""

from __future__ import annotations

# --- Sarvam Bulbul V3 ---------------------------------------------------
# Kept here too (mirrors agent.py's SARVAM_V3_SPEAKERS) so the UI/catalog
# endpoint has one place to read from. agent.py remains the source of truth
# for what it actually accepts; this list is for display/selection only.
# bulbul:v2 speakers. These are the ones the pilot actually uses - v2 is
# 2.3-3.4x faster than v3 (438ms vs 1012ms per clause, measured 2026-08-14) and
# is the configured default.
#
# The two model families share NO speakers: sending a v3 name like "pooja" to
# bulbul:v2 is a hard 400 from the API. Listing v3 voices as the catalog default
# is what produced the "Voice remapped" warning on every call.
SARVAM_V2_VOICES = {
    "manisha": {"name": "Manisha", "gender": "female", "style": "warm, clear - default for Kannada and Hindi"},
    "vidya": {"name": "Vidya", "gender": "female", "style": "calm, measured"},
    "anushka": {"name": "Anushka", "gender": "female", "style": "bright, friendly"},
    "arya": {"name": "Arya", "gender": "female", "style": "soft, reassuring"},
    "ritu": {"name": "Ritu", "gender": "female", "style": "steady, professional"},
    "abhilash": {"name": "Abhilash", "gender": "male", "style": "calm, clear"},
    "karun": {"name": "Karun", "gender": "male", "style": "warm, conversational"},
    "hitesh": {"name": "Hitesh", "gender": "male", "style": "confident"},
    "aditya": {"name": "Aditya", "gender": "male", "style": "professional, steady"},
}

# bulbul:v3 speakers. Kept for deployments that set SARVAM_TTS_MODEL=bulbul:v3
# and accept the extra latency for the newer voice model.
SARVAM_V3_VOICES = {
    "ishita": {"name": "Ishita", "gender": "female", "style": "warm, default clinic voice"},
    "priya": {"name": "Priya", "gender": "female", "style": "calm, professional"},
    "neha": {"name": "Neha", "gender": "female", "style": "friendly, clear"},
    "pooja": {"name": "Pooja", "gender": "female", "style": "gentle, reassuring"},
    "kavya": {"name": "Kavya", "gender": "female", "style": "bright, energetic"},
    "shruti": {"name": "Shruti", "gender": "female", "style": "calm, measured"},
    "suhani": {"name": "Suhani", "gender": "female", "style": "soft, warm"},
    "roopa": {"name": "Roopa", "gender": "female", "style": "mature, steady"},
    "rupali": {"name": "Rupali", "gender": "female", "style": "professional"},
    "tanya": {"name": "Tanya", "gender": "female", "style": "young, friendly"},
    "aditya": {"name": "Aditya", "gender": "male", "style": "confident, clear"},
    "kabir": {"name": "Kabir", "gender": "male", "style": "calm, deep"},
    "rohan": {"name": "Rohan", "gender": "male", "style": "warm, conversational"},
    "amit": {"name": "Amit", "gender": "male", "style": "professional, steady"},
}

# What the picker shows. v2 first because it is the configured model; the merge
# order also means a bare name resolves to its v2 entry when both define it
# (e.g. "aditya" exists in both families).
SARVAM_VOICES = {**SARVAM_V3_VOICES, **SARVAM_V2_VOICES}

# --- Rumik Silk (mulberry model) -----------------------------------------
# Replaces ElevenLabs, which was permanently blocked on the configured
# account's plan (HTTP 402 on every voice - see CHANGELOG for the full
# investigation). Verified live 2026-07-10 against https://silk-api.rumik.ai.
#
# Rumik has no fixed voice-ID catalog for its `mulberry` model - a voice is
# steered by a natural-language `description` string (the 4 `speaker_1..4`
# presets exist but have no documented gender/accent, so they're not used
# here). Each entry below maps a stable internal slug to a curated
# Indian-English description string; rumik_wrappers.py sends that string as
# the `description` field on every synthesis call. Because this is sampled
# steering rather than a pinned voice, temperature is kept low (see
# rumik_wrappers.DEFAULT_TEMPERATURE) to keep the same "voice" reasonably
# consistent turn-to-turn.
RUMIK_VOICES = {
    "priya_warm": {
        "name": "Priya - Warm Receptionist", "gender": "female", "style": "warm, calm, professional, Bengaluru accent",
        "description": "a warm female 28 year old Indian English voice, calm, professional receptionist tone, Bengaluru accent, conversational pacing",
    },
    "ananya_bright": {
        "name": "Ananya - Bright & Friendly", "gender": "female", "style": "bright, energetic, friendly",
        "description": "a bright and energetic female 24 year old Indian English voice, friendly, clear diction, upbeat but not rushed",
    },
    "lakshmi_motherly": {
        "name": "Lakshmi - Motherly & Steady", "gender": "female", "style": "mature, warm, reassuring",
        "description": "a warm and steady female 45 year old Indian English voice, motherly, reassuring, unhurried, gentle authority",
    },
    "kavita_soft": {
        "name": "Kavita - Soft & Gentle", "gender": "female", "style": "soft, gentle, reassuring",
        "description": "a soft-spoken female 30 year old Indian English voice, gentle, reassuring, slow and calm pacing, like a caring nurse",
    },
    "meera_podcast": {
        "name": "Meera - Smooth Conversational", "gender": "female", "style": "smooth, conversational, podcast-host-like",
        "description": "a female 30s Indian English voice, smooth timbre, conversational pacing, like a podcast host",
    },
    "arjun_calm": {
        "name": "Arjun - Calm & Deep", "gender": "male", "style": "calm, deep, reassuring",
        "description": "a calm male 35 year old Indian English voice, deep timbre, professional and reassuring, steady pacing",
    },
    "rohan_friendly": {
        "name": "Rohan - Friendly & Warm", "gender": "male", "style": "warm, friendly, conversational",
        "description": "a warm and friendly male 27 year old Indian English voice, conversational, approachable, medium pace",
    },
    "vikram_steady": {
        "name": "Vikram - Steady & Confident", "gender": "male", "style": "steady, professional, confident",
        "description": "a steady male 40 year old Indian English voice, confident, professional, articulate, measured pacing",
    },
}
RUMIK_DEFAULT_MODEL = "mulberry"
RUMIK_SAMPLE_RATE = 24000

# --- Smallest.ai (lightning-v3.1) ----------------------------------------
# voice_id source: /api/v1/lightning-v3.1/get_voices, filtered accent=indian,
# usecase=conversational, curated for tone/gender balance.
SMALLEST_VOICES = {
    "maithili": {"name": "Maithili", "gender": "female", "style": "conversational, young"},
    "anika": {"name": "Anika", "gender": "female", "style": "conversational, young"},
    "advika": {"name": "Advika", "gender": "female", "style": "conversational, young"},
    "sana": {"name": "Sana", "gender": "female", "style": "conversational, young"},
    "sameera": {"name": "Sameera", "gender": "female", "style": "conversational, young"},
    "avni": {"name": "Avni", "gender": "female", "style": "conversational, young"},
    "ishani": {"name": "Ishani", "gender": "female", "style": "conversational, young"},
    "srishti": {"name": "Srishti", "gender": "female", "style": "conversational, young"},
    "neel": {"name": "Neel", "gender": "male", "style": "conversational, young"},
    "arjun": {"name": "Arjun", "gender": "male", "style": "conversational, young"},
    "mihir": {"name": "Mihir", "gender": "male", "style": "conversational, young"},
    "gaurav": {"name": "Gaurav", "gender": "male", "style": "conversational, young"},
}
SMALLEST_DEFAULT_MODEL = "lightning-v3.1"
SMALLEST_SAMPLE_RATE = 24000

GEMINI_VOICES = {
    "Kore": {"name": "Kore", "gender": "female", "style": "firm, professional"},
    "Aoede": {"name": "Aoede", "gender": "female", "style": "breezy, warm"},
    "Puck": {"name": "Puck", "gender": "male", "style": "upbeat, friendly"},
    "Zephyr": {"name": "Zephyr", "gender": "female", "style": "bright, conversational"},
    "Charon": {"name": "Charon", "gender": "male", "style": "informative, measured"},
    "Leda": {"name": "Leda", "gender": "female", "style": "youthful, clear"},
}

# --- Gnani.ai Timbre (timbre-v2.5) ---------------------------------------
# Second Kannada provider. Benchmarked against Sarvam on identical Kannada text,
# interleaved to control for network drift (2026-08-17):
#
#   Sarvam bulbul:v2 / manisha   p50 636ms   spread 253ms
#   Gnani  timbre-v2.5 / Janaki  p50 655ms   spread 415ms
#
# Effectively tied on median; Sarvam has the tighter tail so it stays default.
# Gnani speaks slower for the same sentence (6.2s vs 4.6s), which may suit a
# rural ASHA worker better - a field judgement, hence both are selectable.
#
# Female voices only: Sakhi is a female persona addressed as akkara.
GNANI_VOICES = {
    "Janaki":  {"name": "Janaki",  "gender": "female", "style": "warm, steady - fastest measured"},
    "Nayana":  {"name": "Nayana",  "gender": "female", "style": "soft, gentle"},
    "Lavanya": {"name": "Lavanya", "gender": "female", "style": "clear, measured"},
    "Vidya":   {"name": "Vidya",   "gender": "female", "style": "calm, professional"},
    "Lalita":  {"name": "Lalita",  "gender": "female", "style": "bright, friendly"},
    "Kaveri":  {"name": "Kaveri",  "gender": "female", "style": "even pace"},
}

PROVIDERS = ("sarvam", "gnani")

CATALOG = {
    "sarvam": SARVAM_VOICES,
    "gnani": GNANI_VOICES,
}

PROVIDER_AVAILABLE = {
    "sarvam": True,
    "gnani": True,
}
PROVIDER_UNAVAILABLE_REASON: dict[str, str] = {}


def default_voice(provider: str) -> str | None:
    # "manisha", not "pooja": pooja is a bulbul:v3 speaker and the pilot runs
    # bulbul:v2, so every call was logging a "Voice remapped" warning and
    # falling back anyway. Naming the real default removes the remap entirely.
    defaults = {"sarvam": "manisha", "gnani": "Janaki"}
    return defaults.get(provider)


def is_valid(provider: str, voice_id: str) -> bool:
    return provider in CATALOG and voice_id in CATALOG[provider]


def is_available(provider: str) -> bool:
    return PROVIDER_AVAILABLE.get(provider, False)


def as_json_catalog() -> dict:
    """Serializable shape for the /api/tts-catalog endpoint."""
    return {
        provider: {
            "default": default_voice(provider),
            "available": PROVIDER_AVAILABLE.get(provider, True),
            "unavailable_reason": PROVIDER_UNAVAILABLE_REASON.get(provider),
            "voices": [
                {"voice_id": vid, **meta} for vid, meta in voices.items()
            ],
        }
        for provider, voices in CATALOG.items()
    }
