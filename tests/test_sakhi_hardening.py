"""Production-hardening tests: the 10 live failure modes.

Each test names the failure mode it guards. Run:
    python -m pytest tests/test_sakhi_hardening.py -q
"""

import asyncio
import json
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent  # noqa: E402
from sakhi_domain import SakhiCallState  # noqa: E402
from sakhi_repository import (  # noqa: E402
    BIDAR_VILLAGES,
    MockSixtyDBRepository,
    correct_proper_noun,
    export_stt_biasing_keywords,
)
from sakhi_tools import ClinicalToolset  # noqa: E402
from sakhi_triage import TRIAGE  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "db.json"
    path.write_text(json.dumps({
        "patients": [
            {"patient_id": "M-201", "name": "Suma Gowda", "age": 24,
             "village": "Kamalnagar", "phone": "9876543210",
             "gestational_weeks": 28, "risk_status": "ROUTINE",
             "history": [], "symptoms": {}},
            {"patient_id": "M-202", "name": "Priya Patil", "age": 29,
             "village": "Aurad", "phone": "8765432109",
             "gestational_weeks": 33, "risk_status": "MODERATE",
             "history": [], "symptoms": {}},
        ],
        "bp_records": [], "teleconsult_logs": [],
    }), encoding="utf-8")
    return MockSixtyDBRepository(path, flush_delay=0.01)


# --- 2. Backchannel over-yielding -------------------------------------------

@pytest.mark.parametrize("utterance", [
    "ಹೌದು", "ಆಯ್ತು", "ಸರಿ", "हाँ", "जी", "mm-hmm", "uh huh", "okay", "yeah",
])
def test_backchannels_do_not_take_the_floor(utterance):
    assert agent.is_backchannel(utterance)


# --- 3. Vocal tics ----------------------------------------------------------

@pytest.mark.parametrize("tic", ["[cough]", "(sneezes)", "[laughs]", "cough"])
def test_vocal_tics_are_ignored(tic):
    assert agent.is_backchannel(tic)


@pytest.mark.parametrize("real", [
    "ತಾಯಿಯ ಬಿಪಿ ನೂರ ನಲವತ್ತೈದು",
    "ಹೌದು, ಅವಳಿಗೆ ತಲೆನೋವು ಇದೆ",
    "yes she has a headache",
])
def test_real_speech_is_never_swallowed(real):
    """A false positive here loses a clinical statement - far worse than
    answering one extra grunt."""
    assert not agent.is_backchannel(real)


def test_a_drawn_out_yes_is_an_answer_not_a_grunt():
    assert agent.is_backchannel("ಹೌದು", duration_ms=200)
    assert not agent.is_backchannel("ಹೌದು", duration_ms=900)


# --- 5. False clause boundaries ---------------------------------------------

@pytest.mark.parametrize("text", [
    "BP is 145.5 today",        # decimal
    "she is 1,20,000 rupees",   # Indian digit grouping
    "Dr. Rao will see her",     # honorific
    "give 500 mg. twice",       # unit abbreviation
    "wait 20 min. please",
])
def test_splitter_does_not_cut_inside_numbers_or_abbreviations(text):
    cuts = [m.end() for m in agent._TTS_CLAUSE_CUT_RE.finditer(text)]
    for cut in cuts:
        before, after = text[:cut].rstrip(), text[cut:].lstrip()
        assert not (before and before[-1].isdigit() and after and after[0].isdigit()), (
            f"split a number in {text!r}"
        )
    assert "Dr." not in [text[c - 3:c] for c in cuts]


def test_splitter_still_cuts_real_sentence_ends():
    text = "ತಾಯಿಯ ಬಿಪಿ ಸಾಮಾನ್ಯವಾಗಿದೆ. ಈಗ ಲಕ್ಷಣಗಳನ್ನು ಕೇಳೋಣ. "
    assert list(agent._TTS_CLAUSE_CUT_RE.finditer(text))


def test_minimum_sentence_length_is_enforced():
    """The floor must hold even when the tuned thresholds are set lower.

    Constructed inside a running loop: EagerClauseSentenceStream opens a
    channel, which needs one.
    """
    assert agent.TTS_MIN_SENTENCE_CHARS >= 10

    async def go():
        stream = agent.EagerClauseSentenceStream(
            min_chunk_chars=2, max_chunk_chars=160, rest_min_chunk_chars=2
        )
        assert stream._active_min >= agent.TTS_MIN_SENTENCE_CHARS
        stream._emitted_first = True
        assert stream._active_min >= agent.TTS_MIN_SENTENCE_CHARS
        await stream.aclose()

    _run(go())


# --- 7. Database de-sync ----------------------------------------------------

def test_tool_never_confirms_a_write_that_did_not_happen(repo):
    """Live behaviour: record_bp returned recorded=True with zero rows written
    whenever the mother had not been identified yet."""
    async def go():
        await repo.load()
        tools = ClinicalToolset(repo, TRIAGE)
        ctx = types.SimpleNamespace(userdata=SakhiCallState())   # no patient_id
        result = json.loads(await tools.record_bp(ctx, 145, 95))
        await repo.flush()

        assert result["persisted"] is False
        assert result["recorded"] is False
        assert repo._data["bp_records"] == []
        assert "DO NOT tell her" in result["instruction"]
    _run(go())


def test_tool_confirms_once_the_write_succeeds(repo):
    async def go():
        await repo.load()
        tools = ClinicalToolset(repo, TRIAGE)
        ctx = types.SimpleNamespace(userdata=SakhiCallState(patient_id="M-201"))
        result = json.loads(await tools.record_bp(ctx, 150, 100))
        await repo.flush()

        assert result["persisted"] is True
        assert len(repo._data["bp_records"]) == 1
    _run(go())


# --- 1. ASR proper-noun degradation -----------------------------------------

@pytest.mark.parametrize("heard,expected", [
    ("Kamalnagara", "Kamalnagar"),
    ("Kamal Nagar", "Kamalnagar"),
    ("ಕಮಲನಗರ", "Kamalnagar"),
    ("ಔರಾದ್", "Aurad"),
    ("Balki", "Bhalki"),
])
def test_mangled_village_names_are_corrected(heard, expected):
    assert correct_proper_noun(heard, list(BIDAR_VILLAGES)) == expected


def test_a_village_outside_the_catchment_is_left_alone():
    """Guessing a village is worse than admitting uncertainty."""
    assert correct_proper_noun("Bengaluru", list(BIDAR_VILLAGES)) == "Bengaluru"


def test_biasing_vocabulary_includes_villages_and_patients(repo):
    async def go():
        await repo.load()
        vocab = export_stt_biasing_keywords(await repo.find_patients(name="suma"))
        assert "Kamalnagar" in vocab and "Aurad" in vocab and "Bhalki" in vocab
        assert "Suma Gowda" in vocab
    _run(go())


# --- 9. LLM TTFT cold starts ------------------------------------------------

def test_latent_filler_exists_for_every_language():
    for language, phrase in agent._LATENT_FILLER.items():
        assert phrase and len(phrase) < 60, f"{language} filler too long to overlap"


# --- 6. Multi-step tool chain state -----------------------------------------

def test_call_state_survives_a_sequential_tool_chain(repo):
    """Verified identity must not have to be re-supplied by the model on each
    step - it is locked in session.userdata."""
    async def go():
        await repo.load()
        tools = ClinicalToolset(repo, TRIAGE)
        state = SakhiCallState()
        ctx = types.SimpleNamespace(userdata=state)

        await tools.search_patient(ctx, name="ಸುಮಾ ಗೌಡ")
        assert state.patient_id == "M-201"

        await tools.record_bp(ctx, 145, 95)          # no id passed by the model
        await tools.record_symptoms(ctx, headache=True)
        await repo.flush()

        assert state.patient_id == "M-201"
        assert repo._data["bp_records"], "chain lost the identity mid-sequence"
    _run(go())


# --- 10. Unresponsiveness ---------------------------------------------------

def test_silence_watchdog_is_configured():
    import os
    assert int(os.getenv("LINE_LIVE_MAX_UNANSWERED", "3")) <= 3


# --- Calibration defaults ---------------------------------------------------

def test_llm_calibration_defaults():
    from sakhi_prompts import LLM_CONFIG
    assert LLM_CONFIG["temperature"] == 0.15
    assert LLM_CONFIG["top_p"] == 0.9
    assert LLM_CONFIG["max_completion_tokens"] in (50, 200)


# --- 3b. Non-directed speech (talking to someone else in the room) ----------

@pytest.mark.parametrize("utterance", [
    "hold on a second", "wait a sec", "just a minute", "one moment",
    "ಒಂದು ನಿಮಿಷ ತಡೀರಿ", "ಸ್ವಲ್ಪ ಇರಿ", "ತಡೀರಿ",
    "एक मिनट रुको", "रुकिए",
])
def test_non_directed_speech_does_not_take_the_floor(utterance):
    """She is talking to someone else, or asking Sakhi to wait. Answering is
    actively wrong - she is mid-task and about to come back."""
    assert agent.is_backchannel(utterance)


@pytest.mark.parametrize("utterance", [
    "wait, her BP is 145 over 95",
    "hold on she also has a headache",
    "ತಾಯಿಯ ಬಿಪಿ ನೂರ ನಲವತ್ತೈದು",
])
def test_a_pause_word_followed_by_content_is_still_a_turn(utterance):
    assert not agent.is_backchannel(utterance)


# --- VAD Fragment Mode: dangling connectives --------------------------------

@pytest.mark.parametrize("fragment", [
    "her BP is 145 and",
    "she has a headache because",
    "ಅವಳ ಬಿಪಿ ನೂರ ನಲವತ್ತೈದು ಮತ್ತು",
    "ಅವಳಿಗೆ ತಲೆನೋವು ಇದೆ ಆದರೆ",
    "उनका बीपी है और",
    "I checked her and",
])
def test_dangling_connective_is_treated_as_incomplete(fragment):
    """Committing here answers half a sentence, and on a clinical call the
    missing half is often the important one."""
    assert agent.looks_incomplete(fragment)


@pytest.mark.parametrize("complete", [
    "her BP is 145 over 95",
    "ಅವಳಿಗೆ ತಲೆನೋವು ಇದೆ",
    "she has a headache",
    "उनका बीपी एक सौ पैंतालीस है",
    "",
])
def test_complete_utterances_are_not_held(complete):
    """A false positive makes Sakhi wait in silence for a continuation that
    never comes - worse than answering slightly early."""
    assert not agent.looks_incomplete(complete)


def test_fragment_recovery_reaches_the_long_window():
    from unittest.mock import MagicMock
    controller = agent.DynamicVADController(MagicMock())
    controller.fragment_recovery()
    assert controller.state == "FRAGMENT_RECOVERY"


# --- 1b. Identity fallback after the first miss -----------------------------
#
# Used to ask her to spell it letter-by-letter over voice here - replaced by
# arming the frontend text box instead (tests/test_identity_frontend_fallback.py
# has the full coverage for that path: tools_for() withdrawal, state fields).
# Re-asking the same way, spoken or spelled, burns another LLM/STT round-trip
# to fix a problem STT just caused; this test now covers what survived of the
# original principle - a first miss must switch strategy, not repeat it.

def test_first_failed_lookup_switches_strategy_not_repeats_it(repo):
    """Name collection is the dominant production bottleneck: re-asking the
    same way gets the same mangled transcript and traps the worker in a loop."""
    async def go():
        await repo.load()
        tools = ClinicalToolset(repo, TRIAGE)
        state = SakhiCallState()
        ctx = types.SimpleNamespace(userdata=state)

        result = json.loads(await tools.search_patient(ctx, name="Xyzabc Qwerty"))
        assert result["found"] is False
        assert result["attempt"] == 1
        assert "type the mother's name and age" in result["instruction"]
        assert state.lookup_attempts == 1
        assert state.awaiting_identity_box is True
    _run(go())


def test_a_successful_lookup_does_not_count_as_a_miss(repo):
    async def go():
        await repo.load()
        tools = ClinicalToolset(repo, TRIAGE)
        state = SakhiCallState()
        ctx = types.SimpleNamespace(userdata=state)
        await tools.search_patient(ctx, name="ಸುಮಾ ಗೌಡ")
        assert state.lookup_attempts == 0
        assert state.patient_id == "M-201"
    _run(go())


# --- 4. Packet-loss clipped transcripts -------------------------------------

@pytest.mark.parametrize("clipped,expected", [
    ("S_ma Go_da", "Suma Gowda"),
    ("Sma Goda", "Suma Gowda"),
    ("Prya Ptil", "Priya Patil"),
])
def test_clipped_transcripts_still_resolve(repo, clipped, expected):
    """Gilbert-Elliott frame drops clip syllables out of a name; the consonant
    skeleton survives and is what matching keys on."""
    async def go():
        await repo.load()
        hits = await repo.find_patients(name=clipped)
        assert hits and hits[0].name == expected
    _run(go())


# --- Tool parameter hardening (SQL payloads instead of integers) ------------

@pytest.mark.parametrize("payload", [
    "145; DROP TABLE patients",
    "' OR '1'='1",
    "145 UNION SELECT",
    None,
    1e9,
])
def test_record_bp_rejects_non_integer_payloads(repo, payload):
    """A SQL string where an int belongs must fail closed - rejected, logged,
    nothing written - and must never raise, which would kill the turn."""
    async def go():
        await repo.load()
        tools = ClinicalToolset(repo, TRIAGE)
        ctx = types.SimpleNamespace(userdata=SakhiCallState(patient_id="M-201"))
        result = json.loads(await tools.record_bp(ctx, payload, 95))
        await repo.flush()

        assert result["recorded"] is False
        assert repo._data["bp_records"] == []
        assert len(repo._data["patients"]) == 2, "patient table was mutated"
    _run(go())


def test_search_patient_with_a_sql_payload_returns_nothing(repo):
    """Must not behave like `WHERE name = '' OR '1'='1'` and dump the table."""
    async def go():
        await repo.load()
        tools = ClinicalToolset(repo, TRIAGE)
        ctx = types.SimpleNamespace(userdata=SakhiCallState())
        result = json.loads(await tools.search_patient(ctx, name="' OR '1'='1"))
        assert result["found"] is False
    _run(go())


def test_db_helper_uses_only_parameterised_sql():
    """Audited, then pinned: no f-string, %-format, .format() or concatenation
    may appear in a SQL statement in db_helper.py."""
    import re as _re
    source = Path(__file__).resolve().parents[1].joinpath("db_helper.py").read_text(encoding="utf-8")
    dangerous = _re.compile(
        r"execute\(\s*f[\"']"                 # f-string SQL
        r"|execute\([^)]*%\s*[\(a-z_]"        # %-formatting
        r"|execute\([^)]*\.format\("          # .format()
        r"|execute\(\s*[\"'][^\"']*[\"']\s*\+",  # concatenation
        _re.IGNORECASE,
    )
    hits = dangerous.findall(source)
    assert not hits, f"dynamic SQL construction found: {hits[:3]}"
