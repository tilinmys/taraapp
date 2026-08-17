# changed.md — Sakhi Refactor Log

Running log of every change made while routing this codebase from the **MyStree
Clinic appointment-booking agent** to the **Sakhi maternal-health ASHA co-pilot**
described in the target architecture.

Newest phase at the bottom. Each entry says *what* changed, *why*, and *what it
affects downstream*, because the modules here are tightly coupled and a change in
one place reliably breaks another.

---

## Phase 0 — Audit (no code changed yet)

### 0.1 The central finding

The repository is **two agents wearing one body**.

`agent.py` (4,106 lines) is a fully-built **clinic receptionist** — `GracyAgent`,
doctor lookup, slot search, appointment booking/rescheduling/cancellation, SQLite
persistence, an FAQ semantic cache for "what are your opening hours".

Bolted onto it, at lines 3413–3486, are **four Sakhi maternal-health tools** added
later. Those four are the only tools actually registered on the session. The
~12 booking tools above them are defined, decorated, and **never registered** —
they are dead weight that still drags in `db_helper`, `SlotCache`,
`BookingPrefetch` and a background SQLite refresh loop on every single call.

The system prompt is already Sakhi's. The state object, the fast path, the FAQ
cache, the greeting/goodbye pair, and half the runtime are still the clinic's.
That split is the root cause of most of the specific bugs below.

### 0.2 Latency defects found

| # | Location | Defect | Cost |
|---|---|---|---|
| L1 | `build_llm()` `agent.py:2085` | Builds the whole Groq provider list into `providers`/`groq_providers`, emits "Groq key configured" events, then **`return openai_fallback`** unconditionally. Every Groq line is dead code. The last commit message claims "Promote Groq qwen3-32b to primary LLM after benchmarking (~3.5x faster)" — the code does the opposite. | The single largest TTFT regression. Their own benchmark says ~3.5×. |
| L2 | `build_initial_context()` `agent.py:2333` | Prompt still carries `CLINIC_NAME`, a 7-day clinic calendar, "the clinic is CLOSED on Sundays", and a Malayalam/"You are Gracy" block — none of it reachable for a Kannada maternal call. | Prefill tokens paid on **every** generation. |
| L3 | System prompt `agent.py:2369` | Instructs the LLM to append `[VAD: FAST]` / `[VAD: SLOW]` to every reply. | Extra output tokens on the critical path, and the tag leaks into TTS if the strip transform misses it. |
| L4 | `build_turn_handling()` `agent.py:2322` | `MIN_INTERRUPTION_DURATION=5.0`, `MIN_INTERRUPTION_WORDS=15`. | Barge-in is effectively **disabled** — the ASHA worker cannot interrupt Sakhi for 5 seconds / 15 words. |
| L5 | `entrypoint()` `agent.py:3588` | `call_silence_threshold` defaults to `0.5`; `MAX_ENDPOINTING_DELAY` caps at `1.2`. | Target architecture specifies 250–350 ms endpointing. |
| L6 | `mock_sixtydb.py` | `read_db()`/`write_db()` do a **full synchronous JSON file read/write** on the asyncio event loop inside tool calls. | Blocks the loop mid-turn; audio stalls. |
| L7 | `entrypoint()` `agent.py:3839` | `_preload_slots()` + `slot_cache.start_background_refresh(10s)` run per call, plus `booking_prefetch.handle_transcript()` on **every interim STT transcript**. | Pure overhead — the clinic DB is no longer part of this product. |
| L8 | `GracyAgent.on_user_turn_completed` | FAQ cache lookup (OpenAI embeddings network round-trip) before the LLM on every non-fast-path turn, for a clinic FAQ set irrelevant to maternal triage. | Adds a round-trip in front of the LLM. |

### 0.3 Correctness / safety defects found

| # | Location | Defect |
|---|---|---|
| C1 | `record_bp()` `agent.py:3450` | Does **not persist anything** and does **not compute risk**. It logs a line and returns a success string. The real, correct BP + risk logic lives in `MockSixtyDB.record_bp()` — a class that is **never instantiated anywhere in the codebase**. |
| C2 | `flag_high_risk_pregnancy()` `agent.py:3456` | Logs a warning and returns. No DB write, no alert, no audit entry. |
| C3 | — | **No `record_symptoms` tool and no `record_patient_data` tool exist**, despite both being in the target spec and referenced by the prompt's STEP 2/STEP 3. |
| C4 | — | The **Pre-Eclampsia triage matrix exists only as prose in the system prompt.** There is no deterministic code path implementing it. A clinical safety rule that depends on the LLM remembering to apply it is not a guardrail. |
| C5 | `CallState` `agent.py:2737` | Is the **booking** state — `doctor`, `booking_confirmed`, `appointment_id`, `doctor_preference_asked`. `llm_node` injects `state.summary()` into *every* generation, so Sakhi is told on every turn to go collect a doctor preference and confirm a booking. Directly fights the maternal workflow. |
| C6 | `agent.py:3631` `extract_vad_flags` | Mutates `vad_provider.min_silence_duration` — the VAD instance **shared across concurrent calls** via `proc.userdata`. `DynamicVADController`'s own docstring (line 2818) explicitly warns never to do this. Two competing adaptive-VAD systems fight each other. Also reaches into the private `session._turn_detector._min_endpointing_delay`. |
| C7 | `agent.py:1884` | `schedule_hangup_after_playout` is decorated `@llm.function_tool` but is a plain internal helper called directly. Verified it still works (`FunctionTool` is callable), but it is semantically wrong and would be exposed to the model if ever passed in a tools list. |
| C8 | `tts_node` `agent.py:3271` | Content heuristic looks for `"doctor in mind"` / `"suggest one"` to set booking state — dead logic on a maternal call. |
| C9 | `GOODBYE_TEXT` `agent.py:2432` | Still `"Thank you for calling MyStree Clinic..."` in English, while `GREETING_TEXT` is Kannada. `_FAREWELL_RE` keys off `pranaam` / `have a beautiful day`, so **auto-hangup can never fire on a Kannada goodbye.** |
| C10 | `agent.py:3853` | Agent `instructions=` says *"You are a human receptionist for MyStree Clinic. Never call yourself an AI."* — contradicts the Sakhi system prompt in the same session. |
| C11 | `build_stt()` `agent.py:2070` | Builds a `health_terms` vocabulary-bias list and **never passes it to `SarvamSTT`**. Dead variable; no keyword biasing is actually applied. |
| C12 | `build_tts()` `agent.py:2267` | Accepts `tts_provider` and `speed_override` and **ignores both** — always Sarvam, and silently drops the caller's speed setting from the UI. |
| C13 | `agent.py:3413` | `import json`, `from datetime import datetime`, `from mock_sixtydb import ...` sit **in the middle of the file**, after ~3,400 lines of definitions. |
| C14 | `agent.py:30` | `from livekit.plugins import bey` — the Beyond Presence avatar plugin is imported and **never used**. `BEYOND_PRESENCE_API_KEY` is present in `.env`. |
| C15 | `agent.py:1940`, `2044` | `LockedSpeechStream` / `LockedAssemblyAISTT` — several hundred lines of AssemblyAI subclassing, unreachable now that `build_stt()` hard-returns `SarvamSTT`. |
| C16 | Fast path `agent.py:2554` | `fast_path_reply` and its helpers are **English clinic-booking** patterns ("what brings you in today", name spelling, 10-digit phone confirmation). On a Kannada/Hindi maternal call they either never fire or fire wrongly. |
| C17 | `agent.py:262` (found during Phase 3) | One `try:` block imported `MultilingualModel`, `SmallestTTS`, `SkilliesTTS` **and `token_manager`** — and `token_manager.py` does not exist in this repo. So the block raised `ModuleNotFoundError` on **every boot**, silently forcing `MultilingualModel = None` (making `ENABLE_MULTILINGUAL_TURN_DETECTOR` a no-op regardless of its value) and leaving `SmallestTTS`/`SkilliesTTS` undefined. Only masked because `build_tts()` hard-returns Sarvam. |
| C18 | `_build_sarvam_tts()` (found during Phase 6) | Computes `pace`, `base_url`, `min_buffer_size`, `max_chunk_length`, **logs all four in a pipeline event as if applied**, then passes **none** of them to the `SarvamTTS` constructor. `SARVAM_PACE`, the UI speed slider, `SARVAM_BASE_URL` and `SARVAM_MIN_BUFFER_SIZE` all had zero effect — and `SARVAM_PACE_GREETING` did too, so the "deliberately slower greeting" documented at length in `entrypoint()` never actually played slower. |

### 0.4 Environment gap

`.env` contains only `EXOTEL_*` and `BEYOND_PRESENCE_API_KEY`. There is **no
`SARVAM_API_KEY`, `OPENAI_API_KEY`, `GROQ_API_KEY`, or `LIVEKIT_*`** in it.
`build_llm()` calls `required_env("OPENAI_API_KEY")`, so a local run raises before
the session starts. Keys must be coming from the deploy environment — flagged so
local verification is understood to be limited to import/unit level.

### 0.5 Routing plan (audit → target architecture)

| Target spec subsystem | Current state | Action |
|---|---|---|
| Media transport & turn detection | Present and good (LiveKit + BVC + Silero + `DynamicVADController`) | Keep; delete the competing `[VAD:]` tag mechanism; retune to 250–350 ms |
| `CallState` schema (maternal) | Booking schema | **Replace** with the spec's maternal schema |
| Sarvam STT/TTS | Wrappers are solid and stream natively | Keep; actually apply the vocabulary bias; honour speed override |
| Clinical AI core + tools | 4 stubs, 2 of them no-ops | **Rebuild** against a repository + a real triage engine |
| Pre-eclampsia triage matrix | Prompt prose only | **Implement deterministically** in `sakhi_triage.py` |
| SixtyDB persistence | Sync file I/O, class never used | **Replace** with an async repository behind an ABC |
| D-ID avatar | Nothing (dead `bey` import) | Build the **seam** now (ABC + null backend), plug the backend next prompt |
| Latency budget | Groq discarded, prompt bloated | Restore fallback chain, trim prefill |

No code has been modified in this phase.

---

## Phase 1 — Sakhi domain core (new modules, no behaviour change yet)

Three new dependency-free modules. Nothing in `agent.py` used them at this
point; the goal was to get the clinical model correct and tested in isolation
before wiring it into a live session.

**`sakhi_domain.py`** (425 lines) — value objects and per-call state.

| Type | Replaces | Why |
|---|---|---|
| `Language` enum | bare `str` ("en"/"kn"/"kn-IN") | `build_stt` had to re-derive the Sarvam code with a `startswith` check because no type carried it. Now carries `sarvam_code` and `honorific`. `Language.parse()` never raises — Sarvam goes *silent* on unknown codes, which makes the agent deaf, so junk falls back instead of propagating. |
| `RiskLevel` enum | `"NORMAL"` / `"HIGH"` / `"HIGH_RISK"` magic strings | The codebase used **both** `"HIGH"` and `"HIGH_RISK"` for the same state (`mock_sixtydb.py:90` vs `:106`). Ordered, so call sites compare instead of string-matching. |
| `BloodPressure` frozen dataclass | two loose ints | Self-validating. Rejects `140/900` (STT hearing "ninety" as "nine hundred") and transposed readings, so a mis-transcription becomes a re-ask instead of a record against a mother's chart. |
| `SymptomSet` frozen dataclass | three positional bools | `record_symptoms(pid, True, False, True)` was transposable with no error surfacing. Keyword-only now, and `merged_with()` makes symptoms accumulate so a later "no swelling" cannot erase an earlier "yes headache". |
| `RiskAssessment` frozen dataclass | — | Carries the *reasons* for the decision, so the audit trail records why a mother was flagged. |
| `SakhiCallState` | booking `CallState` | The spec's maternal schema. Mutation goes through methods so risk cannot drift out of sync with the readings that produced it. |

**`sakhi_triage.py`** (137 lines) — `TriageEngine`. Implements the matrix in code:

| BP | Danger symptoms | Outcome |
|---|---|---|
| >=140/90 | any | 🔴 `HIGH_RISK` — escalate |
| >=140/90 | none | 🟡 `MODERATE` — 48h re-check |
| normal | any | 🟡 `OBSERVATION` |
| normal | none | 🟢 `ROUTINE` |

Two deliberate additions beyond the literal spec matrix:

- **Severe-range BP (>=160/110) escalates on the reading alone**, per standard obstetric practice. Toggleable via `TRIAGE_ESCALATE_ON_SEVERE_BP` / the `escalate_on_severe_bp_alone` constructor flag.
- **Gestation under 20 weeks** with raised BP adds a reason noting it may be chronic hypertension rather than pre-eclampsia. It does **not** downgrade the risk — a screening tool must not under-triage.

A missing BP reading is treated as "not yet known", never as "normal" — triage runs incrementally as readings arrive.

**`sakhi_repository.py`** (392 lines) — `PatientRepository` ABC + `MockSixtyDBRepository`.

- Reads serve from memory; writes mutate memory and a single debounced task does the `json.dump` inside `asyncio.to_thread`. A burst of BP + symptoms + teleconsult writes in one turn costs one off-loop file write **after** the caller has already heard the reply. Fixes L6.
- Writes go to a temp file then `replace()`, so a crash mid-write cannot leave truncated JSON that fails to load on the next call.
- `PatientRecord.from_dict` tolerates the legacy seed format (`"NORMAL"`/`"HIGH"`, missing fields, digit-separated phones).
- **Bug fixed:** new patient ids were `M-{len(patients)+201}`, which reissues an id already in use as soon as any record is removed. Now derived from the highest existing number.
- **Bug fixed:** risk status now ratchets upward only — a later normal reading cannot silently clear a red alert already raised and acted on.

`mock_sixtydb.py` is left in place (unused by the agent) so any external script still importing it keeps working.

**Tests:** `tests/test_sakhi_clinical.py` — 62 tests covering all four triage quadrants, severe-range, BP validation, symptom accumulation, phone normalisation, legacy risk mapping, id collision, the risk ratchet, and write-behind durability.

---

## Phase 2 — Real clinical tools

**`sakhi_tools.py`** (408 lines) — `ClinicalToolset`.

Tools are **methods on a class**, with the repository and triage engine injected
through the constructor. Verified against livekit-agents 1.6.10 that bound
methods register correctly and that `self` is excluded from the generated JSON
schema.

The division of labour, which is the point of the module:

> the LLM collects the readings and speaks the outcome; **the code decides what the outcome is**

Every tool returns a JSON block containing the decided `risk_level`, the
`reasons`, and an `instruction` telling the model what to say. The model never
computes a risk level.

| Tool | Before | After |
|---|---|---|
| `search_patient` | raw dict scan over a file read | repository lookup; populates `SakhiCallState`; surfaces a prior high-risk flag first |
| `record_bp` | **logged a line, persisted nothing, computed no risk** | validates → triages → persists → returns decision + what to say. Implausible readings return a re-ask |
| `record_symptoms` | **did not exist** | merges symptoms, persists, **re-triages** — a symptom reported after the BP reading flips MODERATE to a red alert |
| `register_mother` | **did not exist** | creates the SixtyDB record |
| `flag_high_risk_pregnancy` | logged a warning | **removed** — risk is assigned by the engine, not asserted by the model |
| `connect_smfmi_specialist` | wrote a log entry | idempotent; writes an audit entry carrying risk level and reasons |

---

## Phase 3 — `agent.py` rewired

- **`GracyAgent` → `SakhiAgent`.** Same three framework hooks (that part of the old design was sound); the booking fast path, phone-confirmation branch, doctor-preference heuristics and FAQ-cache lookup are gone from them. `GracyAgent` is kept as an alias for older scripts and docs.
- **Deterministic escalation safety net (fixes C4).** `SakhiAgent` guarantees: *if the triage engine says a mother needs a specialist, a specialist is connected — whether or not the LLM calls the tool.* Two layers: `llm_node` injects an urgent directive while escalation is pending, and if the turn still finishes without the call, `tts_node` fires `_force_escalation()` directly against the toolset. Re-entrant-safe and idempotent.
- **`CallState` → `SakhiCallState`**; `llm_node` now injects `prompt_summary()` (fixes C5 — the old summary told Sakhi on every generation to collect a doctor preference and confirm a booking).
- **System prompt rewritten.** Dropped `CLINIC_NAME`, the seven-day appointment calendar, "closed on Sundays", the Malayalam/"You are Gracy" block, and the `[VAD: FAST]`/`[VAD: SLOW]` tag protocol. **~3–4k tokens → ~484 tokens of prefill per generation.** Triage thresholds are deliberately *not* restated in the prompt — restating them invites the model to second-guess the engine.
- **`extract_vad_flags` deleted (fixes C6).** It mutated `vad_provider.min_silence_duration` — the Silero instance **shared across every concurrent call** on the worker — and reached into private `session._turn_detector._min_endpointing_delay`. One caller's turn retuned turn-taking for every other live call. `DynamicVADController` now owns endpointing alone.
- **`GOODBYE_TEXT` and `_FAREWELL_RE` localised (fixes C9).** The farewell detector matched English only ("pranaam", "have a beautiful day"), so on a Kannada call auto-hangup **could never fire** and the line stayed open until the ~45–75s silence timeout. Now matches Kannada, Hindi and transliterations.
- **Agent `instructions=` fixed (C10)** — was *"You are a human receptionist for MyStree Clinic"*, contradicting the Sakhi prompt in the same session.
- **Repository + triage engine loaded once at `prewarm_process`**, not per call.
- **Removed from the per-call path:** slot-cache preload and its 10s refresh timer, `booking_prefetch.handle_transcript()` (ran on *every interim transcript*, several times a second while the caller speaks), and the FAQ-cache prewarm and lookup (an OpenAI embeddings round-trip in front of the LLM, for clinic opening-hours content).
- Mid-file imports at line 3413 hoisted to the top (C13).

---

## Phase 4 — Latency pass

- **`build_llm` rewritten (fixes L1 — the biggest single win).** The old function built the entire Groq chain, emitted "Groq key configured" events for each key, then ended with an unconditional `return openai_fallback`. **Every Groq provider it constructed was discarded**; the dashboard reported Groq while 100% of turns went to `gpt-4o-mini`. Now returns a real `llm.FallbackAdapter([*groq, openai])` with a 3.5s attempt timeout, so a Groq rate-limit costs one timeout rather than the turn. Raises at build time if no provider is configured, instead of starting a call that can never reply.
- **Barge-in restored (fixes L4).** Committed values were `min_duration=5.0s` / `min_words=15` — those do not filter backchannels, they **disable interruption outright**. An ASHA worker could not interrupt for five seconds or fifteen words, which on an urgent call is the wrong failure: she may be correcting a misheard BP. Back to 0.6s / 2 words, the values the surrounding comment always claimed.
- **Endpointing default 0.5s → 0.3s**, landing in the spec's 250–350 ms band.
- **`build_stt` cleaned (fixes C11).** Dropped four AssemblyAI endpointing parameters that had had no effect since STT moved to Sarvam, and the `health_terms` vocabulary list that was built and never passed to anything. `SarvamSTT` exposes no keyword-biasing parameter — leaving the list in place would read as if biasing were active.
- **`build_tts` honours its parameters (fixes C12).** It accepted `speed_override` and dropped it — the dashboard's TTS-speed slider moved a value that never reached the provider.
- **`_build_sarvam_tts` now forwards its tuned values (C18, found late).** It computed `pace`, `base_url`, `min_buffer_size` and `max_chunk_length`, **logged all four in a pipeline event as though they had been applied**, and passed none of them to the constructor. Consequences: `SARVAM_PACE` and the speed slider did nothing; `SARVAM_PACE_GREETING` did nothing, so the "deliberately slower greeting" documented at length in `entrypoint()` **never actually played slower**; and `SARVAM_MIN_BUFFER_SIZE` — which sets how much text buffers before the first synthesis request leaves, i.e. time-to-first-audio — was not tunable at all.
- **Adaptive-VAD triggers rewritten.** `_VAD_SLOW_TRIGGER_RE` was English clinic phrasing ("which doctor", "doctor in mind", "what brings you"), none of which Sakhi ever says — so on a Kannada call the slow window **could never engage** and every question got the fast endpoint. Now covers BP readings, gestational age, names and villages in Kannada, Hindi and English.

---

## Phase 5 — Avatar seam

**`sakhi_avatar.py`** (227 lines) — `AvatarBackend` ABC with three implementations:

- `NullAvatarBackend` — audio-only, the default (Mode C in the bandwidth matrix).
- `BeyAvatarBackend` — Beyond Presence. The plugin is installed and `BEYOND_PRESENCE_API_KEY` is already in `.env`, so this path is live behind `SAKHI_AVATAR=bey`.
- `DIDAvatarBackend` — **refuses to start**, with the implementation steps in its docstring, so selecting it cannot look like success in the logs.

The hard rule every backend enforces: **an avatar failure never takes down the
call.** `start()` returns a bool and cannot raise; an unknown `SAKHI_AVATAR`
value falls back to audio-only with a warning rather than blocking calls. Wired
into `entrypoint()` after `session.start()`, closed on call end.

Also removed the unused top-level `from livekit.plugins import bey` (C14).

The one non-trivial piece left for the D-ID work: audio must be **teed** from the
existing TTS stream rather than re-synthesised, or lip-sync drifts against what
the caller actually hears.

---

## Phase 6 — Pruning and verification

`agent.py`: **4,239 → 2,999 lines** (−1,240). Every removal left a comment naming
what was there and why it went.

| Removed | Lines | Reason |
|---|---|---|
| 11 clinic booking tools | ~370 | Never registered on the session; still pulled in `db_helper`, `SlotCache`, `BookingPrefetch` on every import |
| `BookingPrefetch` + singleton | ~250 | Ran on every interim transcript; cross-call mutable global |
| Deterministic fast path | ~145 | English booking patterns — on a Kannada call they never matched or matched wrongly (C16) |
| `LockedSpeechStream` / `LockedAssemblyAISTT` | ~130 | Unreachable since `build_stt` moved to Sarvam (C15) |
| `SlotCache` + singleton | ~115 | Cross-call mutable global, 10s refresh timer, no consumer |
| `_FillerHandle`, `delayed_filler`, `run_db_step`, `log_tool_failure`, `_harvest_identity` | ~100 | Booking-tool scaffolding. `_harvest_identity` was **already broken** — it called three functions removed with the booking flow, and survived only because nothing called it |
| Clinic time/date helpers, name/phone helpers, `FILLER_TEXTS`, `_AFFIRMATIVE_RE`/`_NEGATIVE_RE` | ~130 | Booking-flow only, English-only |

Also fixed **C7**: `schedule_hangup_after_playout` carried an `@llm.function_tool`
decorator despite being a plain internal helper called directly. It happened to
work (`FunctionTool` is callable) but made a private helper eligible for
exposure to the model.

### Test results — 215 passing

| Suite | Result |
|---|---|
| `tests/test_sakhi_clinical.py` (new) | 62 passed |
| `tests/test_sakhi_agent_wiring.py` (new) | 20 passed |
| `tests/test_sarvam_tts_payloads.py` | 8 passed |
| `tests/test_adaptive_vad.py` (updated) | 24 passed |
| `tests/test_guardrails.py` | 73 passed |
| `tests/test_schedule_db.py` | 28 passed |

Retired: `test_fast_path.py`, `test_faq_cache.py`, `test_call_state.py` — all
tested code that no longer exists.

Verified beyond unit tests: `prewarm_process` loads VAD + repository + triage;
`build_stt` / `build_tts` / `build_llm` / `build_turn_handling` all construct for
Kannada and Hindi; the LLM is a `FallbackAdapter`; TTS reports streaming; pace
overrides reach the provider; and the full clinical conversation runs end to end
(lookup → implausible-BP rejection → MODERATE → symptom flips it to HIGH_RISK →
escalation → audit trail with reasons).

---

## Open items and risks

1. **`GROQ_LLM_MODEL` is set to `qwen/qwen3-32b` in the environment.** Qwen 3 is
   a reasoning model, and a comment in the original `build_llm` recorded exactly
   this failure: *"Qwen 3 can spend the complete voice-token budget on hidden
   reasoning and finish with no assistant content."* Now that Groq is genuinely
   primary, that risk is live for the first time. The empty-completion fallback
   in `llm_node` catches it, but at the cost of a second round-trip.
   **Recommendation:** set `GROQ_LLM_MODEL=llama-3.3-70b-versatile`, or raise
   `LLM_MAX_COMPLETION_TOKENS` and constrain reasoning. Worth one live call to check.
2. **Local `.env` has no `SARVAM_API_KEY`, `OPENAI_API_KEY`, `GROQ_API_KEY` or
   `LIVEKIT_*`** — only `EXOTEL_*` and `BEYOND_PRESENCE_API_KEY`. Verification
   here was import and unit level; a live call needs those in the deploy environment.
3. **`db_helper` (clinic SQLite) is still imported** and used by `preload_user`,
   `patient_context_prompt` and `save_call_report`. Post-call reports still land
   in the clinic DB rather than SixtyDB. Left deliberately — it works, and moving
   it is a separate change.
4. **Latency is not yet measured on a live call.** The changes are structural
   (Groq actually primary, ~3–4k → ~484 prefill tokens, one fewer pre-LLM
   round-trip, no blocking file I/O in tools). Confirm against
   `logs/pipeline_events.jsonl` (`llm_ttft_ms`, `ttfa_ms`, `eou_delay_ms`) on a
   real call.
5. **`livekit.plugins.turn_detector` is deprecated** in the installed version; it
   warns to use `livekit.agents.inference.TurnDetector`. Not urgent — the
   multilingual detector is off by default.
6. **The D-ID backend is a stub.** `SAKHI_AVATAR=bey` works today; D-ID is next
   prompt's work.

---

## Phase 7 — Tavus avatar + ASHA-worker UI

### 7.1 Tavus integration

**The one thing that had to be right:** Tavus can run an entire conversational
stack itself (its own STT, LLM and TTS). Used that way it would have replaced
everything this refactor built — no Kannada, no clinical prompt, no
`TriageEngine`.

The LiveKit plugin does not do that. Verified in
`livekit/plugins/tavus/api.py`: it creates the conversation with

```python
"pipeline_mode": "echo",
"transport": {"transport_type": "livekit"}
```

In **echo mode** Tavus neither thinks nor speaks. It receives Sakhi's
already-synthesised Sarvam audio and returns a video track whose lips match it:

```
Sarvam Kannada TTS ──> Tavus (echo) ──> lip-synced H.264 video
Groq LLM + TriageEngine ──> untouched; Tavus never sees them
```

Tavus contributes a face and nothing else — exactly what the `AvatarBackend`
seam from Phase 5 was built for.

**Added:** `TavusAvatarBackend` in `sakhi_avatar.py`, registered as
`SAKHI_AVATAR=tavus`. Config via `TAVUS_API_KEY`, `TAVUS_REPLICA_ID`
(default `rd3ba0f30551`, "Olivia - Doctor"), optional `TAVUS_PERSONA_ID`.
`livekit-plugins-tavus==1.6.10` added to `requirements.txt`.

The API key lives in `.env` only, which is gitignored, and never reaches the
browser — the frontend receives a LiveKit token, never a Tavus credential.

**Bug found and fixed during the live test.** The first run failed with:

```
RuntimeError: Plugins must be registered on the main thread
```

A backend's `start()` runs on the job's worker thread, so the lazy
`from livekit.plugins import tavus` inside `_start()` failed on every call and
the seam silently degraded to audio-only. Avatar plugins are now imported at
**module scope** in `sakhi_avatar.py`, which is the main thread at process
import.

This is what the `from livekit.plugins import bey` line at the top of the old
`agent.py` was actually for. It looked unused — nothing referenced the name —
so it was removed in Phase 5 as dead. **The import itself was the point.**
`BeyAvatarBackend` had the same latent defect and is fixed by the same change.

**Live verification.** Worker restarted with `SAKHI_AVATAR=tavus`:

```
Stage 9 Playback ok  Avatar started - Tavus avatar (replica rd3ba0f30551)
                     is rendering Sakhi's video track
AvatarMetrics(avatar_joined_time=..., playback_latency=4.57, metadata={'model_provider': 'tavus'})
```

Two active conversations confirmed on Tavus's side, then ended via the API so
they stopped consuming concurrency.

**Latency cost, measured:** the avatar took **~6.1 s to join** after session
start, and reported **`playback_latency` ≈ 4.6 s**. That is a real budget hit
against the ~1.25–1.9 s audio-only target, and matches the target
architecture's own Mode A estimate (2.25–3.4 s) being optimistic. Audio is
unaffected — voice still flows on its own track — but the *video* trails.
Worth deciding whether video is on by default in the field, or reserved for
good-4G sessions per the bandwidth matrix.

### 7.2 ASHA-worker UI

`frontend/src/app/page.tsx` and `page.module.css` rewritten for one user: an
ASHA worker in a Bidar village, cheap Android phone, outdoors, Kannada-first,
variable literacy, patchy 3G.

| Removed | Why |
|---|---|
| Three `<select>` dropdowns (language / TTS provider / TTS voice) | TTS provider and voice are engineering knobs, not field decisions — the backend runs Sarvam regardless. A dropdown also hides its options behind a tap on a small touch screen. Language is now two large pills. |
| Three.js / WebGL canvas + `SakhiAvatar.tsx` model | Shipped a 3D model and ran a continuous render loop on a device class that can afford neither. The Tavus video track is a plain `<video>`. |
| Log console in the main column | Moved to a quiet collapsible left rail. |

Design rules applied:

- **Kannada primary, English subtitle** — never English-only, never Kannada replaced.
- **One obvious action.** A single 78 px teal button, `ಮಾತನಾಡಿ` (Talk).
- **State is icon + colour + word**, never colour alone: `ಕೇಳುತ್ತಿದ್ದೇನೆ` 👂 listening, `ಮಾತನಾಡುತ್ತಿದ್ದೇನೆ` 🗣️ speaking, `ಯೋಚಿಸುತ್ತಿದ್ದೇನೆ` 💭 thinking.
- **Touch targets ≥64 px** throughout.
- **Warm off-white**, not pure white — low-contrast greys wash out in sunlight.
- **No thin font weights**; Kannada/Devanagari fonts lead the stack so Indic glyphs never fall back to boxes.
- **Risk banner** driven by the worker's `Triage: <LEVEL>` pipeline event, so the ASHA worker sees the *engine's* deterministic decision. The UI never infers a risk level. HIGH_RISK renders `ತಕ್ಷಣ ಆಸ್ಪತ್ರೆಗೆ ಕರೆದೊಯ್ಯಿರಿ` (take her to hospital immediately) in red.
- All user-facing strings live in one `COPY` table so a Kannada speaker can review every word without reading JSX.

**Log rail:** 320 px open → 56 px collapsed; on mobile it moves below the call
(`order: 2`) so the call owns the screen. Colour-coded by severity, copy button,
auto-scroll.

### 7.3 Browser verification

| Check | Result |
|---|---|
| Page renders, no console errors | ✅ |
| Kannada font stack applies (not the Geist body font) | ✅ `Noto Sans Kannada` first |
| Talk button / language pills | 78 px / 64 px, 24 px & 20 px type |
| Log rail collapse | 320 px → 56 px, main button unaffected |
| Mobile 375×812 | single column, call first, **no horizontal scroll** |
| Risk banner end-to-end | real triage event → pipeline log → `/api/logs` → red Kannada banner ✅ |
| Log rail streaming | 46 rows, `ok`/`warn`/`info` colour coding ✅ |

Tests: **221 passing** (101 pytest + 24 + 73 + 28 standalone).

One test was environment-dependent and is fixed: `build_avatar_backend()` with
no argument now resolves to Tavus because this repo's `.env` sets
`SAKHI_AVATAR=tavus`, so the default-behaviour assertion now clears the variable
with `monkeypatch` instead of testing the developer's machine.

### 7.4 Open items

1. **Rotate the Tavus API key.** It was pasted into a chat transcript.
2. **Train a custom replica for Bidar.** The default is a stock Western face. Tavus needs ~2 minutes of footage; a local Kannada-speaking health worker would be far better received. Set `TAVUS_REPLICA_ID` — nothing else changes.
3. **Decide the default avatar mode.** ~6 s join and ~4.6 s playback latency argue for audio-only (`SAKHI_AVATAR=none`) as the field default, with video enabled on good connections.
4. **Ghost conversations consume Tavus concurrency.** A dispatch that no participant joins leaves a conversation active until it times out. `aclose()` handles normal call end; consider a sweeper if dispatches are created speculatively.

---

## Phase 8 — TTS timeout, fallback chain, prompt trim, turn-taking

### 8.1 Root cause of the 11-second timeout — measured, not assumed

Before changing the wrapper, both Sarvam endpoints were probed directly with the
live key (2026-08-14, identical Kannada text):

**WebSocket `wss://api.sarvam.ai/text-to-speech/ws`**

```
bulbul:v2 manisha  -> TimeoutError (no audio in 12s)
bulbul:v3 pooja    -> TimeoutError (no audio in 12s)
```

It connects, accepts `config`/`text`/`flush`, then never returns audio. **That
is the dead air.** The wrapper declared `streaming=True`, so AgentSession sent
every reply down this socket.

**REST `POST /text-to-speech`**

| model | speaker | short clause | full sentence |
|---|---|---|---|
| bulbul:v2 | **manisha** | **438 ms** | **779 ms** |
| bulbul:v2 | vidya | 451 ms | 764 ms |
| bulbul:v2 | anushka | 700 ms | 1026 ms |
| bulbul:v3 | pooja | 1012 ms | 2655 ms |

### 8.2 Three corrections to the brief

The prescribed fix was right in shape; three details would have broken it:

1. **`bulbul:v1` does not exist.** The API rejects it outright:
   `model: Input should be 'bulbul:v2', 'bulbul:v3-beta' or 'bulbul:v3'`.
   Forcing v1 would have made **every** TTS call a hard 400 — total silence.
   Used **`bulbul:v2`**, which is also the fastest option measured.
2. **Speaker sets do not overlap between models.** v2 accepts
   `anushka, abhilash, manisha, vidya, arya, karun, hitesh, aditya, ritu`; the
   configured voices (`pooja`, `ishita`) are v3-only. Sending `pooja` to v2 is a
   400 and a silent turn. Added `resolve_sarvam_voice()` to pair them safely.
3. **The sample rate was wrong.** The wrapper computed
   `24000 if "v3" in model else 22050`, but the REST endpoint returns **22050 Hz
   for both** (read from the RIFF header). Every v3 reply was played ~9% fast
   and pitched up. Now pinned to the measured 22050.

### 8.3 `sarvam_wrappers.py`

- `capabilities.streaming` **True → False**. This is the core fix: AgentSession
  now routes through `tts.StreamAdapter`, which feeds the REST endpoint one
  clause at a time using the `EagerClauseTokenizer` already wired into
  `SakhiAgent.tts_node` (it only activates for non-streaming providers). That
  delivers the requested "buffer until punctuation / a few words" behaviour by
  reusing tested code instead of adding a second buffering implementation.
- **3-second `aiohttp.ClientTimeout`** on the REST call (`SARVAM_TTS_HTTP_TIMEOUT`).
  Previously there was no timeout at all, so a slow synthesis waited forever.
- **Errors now propagate.** The old handler logged and `return`ed, so a failure
  produced an empty emitter, AgentSession saw a "successful" silent synthesis,
  and no fallback ever fired. Non-200 responses now raise `APIStatusError` with
  the response body, timeouts raise `APIConnectionError`, and everything else is
  wrapped — all of which the fallback chain can act on.
- `output_emitter.flush()` added after push.
- Sample-rate mismatch between the returned WAV and the declared rate now logs a
  warning instead of silently pitch-shifting.

The WebSocket code path (`_run_sarvam_tts_ws`, `SarvamTTSSynthesizeStream`) is
left in place but unreachable, documented as hanging. If Sarvam fixes the
endpoint, flipping one capability flag restores it.

### 8.4 `agent.py`

- **TTS fallback chain**: `tts.FallbackAdapter([Sarvam, OpenAI])`, pinned to
  22050 Hz so a failover cannot change the track's sample rate mid-call (LiveKit
  logs `resampling ... from 24000Hz to 22050Hz` for the OpenAI leg). Gated by
  `VOICE_TTS_FALLBACK_ENABLED`. **This is a degraded path, not an equal one** —
  OpenAI will read Kannada text with an English voice. That is still better than
  dead air on a call where a mother may be in danger.
- `force_bulbul_v3()` → `sarvam_tts_model()`. The old function hard-returned
  `"bulbul:v3"` and ignored `SARVAM_TTS_MODEL` entirely.
- Voice selection now goes through `resolve_sarvam_voice()`, emitting a
  `Voice remapped` event instead of a 400.
- **Prompt trimmed: 484 → 109 tokens** (`tiktoken o200k_base`), plus a 29-token
  per-turn state block = **~138 tokens of prefill per generation**. Cut the
  four-step workflow prose (the tool docstrings already carry it), the tone
  paragraph (each tool result ships an explicit `instruction` field), and the
  "never ask twice" rule (`prompt_summary()` already states what is recorded).
  Kept identity, language + honorific, tool order, the follow-the-instruction
  contract, and the three hard safety rules.

### 8.5 Avatar paused and turn-taking retuned

`SAKHI_AVATAR=none` — audio-only, via the existing seam rather than by
commenting out code, so re-enabling is a one-variable change and the Tavus work
from Phase 7 stays intact and tested. The frontend already falls back to the
idle card when no video track arrives, so no UI change was needed.

`VAD_MIN_SILENCE=0.25`, `MIN_ENDPOINTING_DELAY=0.3`. Note 0.25 is *higher* than
the previous 0.2, so this slightly increases end-of-turn wait in exchange for
fewer clipped utterances — worth knowing, since the brief filed it under
"reduce lag".

Also cleared `assets/audio/greetings/*.wav`: those were rendered at 24000 Hz
under the old wrong assumption, with v3 voices no longer in use.

### 8.6 Verification

| Check | Result |
|---|---|
| Wrapper synthesis, live API | 619 ms / 633 ms per clause, real frames |
| Voice guard | `pooja` on v2 → `manisha`, logged not fatal |
| Worker boot | `bulbul:v2 via REST, clause-buffered`; fallback chain active |
| Live dispatch | greeting synthesised, **zero TTS errors, zero timeouts** |
| Avatar | `NullAvatarBackend` (audio-only) |
| Turn config | endpointing 0.3 s, VAD 0.25 s, barge-in 0.6 s / 2 words |
| Tests | **227 passing** |

Two tests asserted environment rather than behaviour and were corrected:
`test_adaptive_vad` hardcoded `min_delay == 0.35` (now compares against the
controller's own configured value), and `test_invalid_speaker_fails_before_api_call`
expected a `ValueError` (now asserts the remap, since aborting TTS construction
over a stale voice name is worse than substituting one).

### 8.7 What was NOT achieved

**"Turn 1 TTFT under 150 ms" is not physically reachable.** 150 ms is below the
network round-trip from India to Groq/OpenAI before a single token is generated;
observed TTFT is typically 300–700 ms. Prompt size was the controllable part and
is now ~138 tokens of prefill. The honest end-to-end first-response estimate is:

```
endpointing 0.30s + STT ~0.30s + LLM TTFT ~0.40s + TTS clause ~0.44s  ≈ 1.4s
```

That is inside the target architecture's 1.25–1.9 s audio-only budget, but it is
not 150 ms and no amount of prompt trimming will make it so.

**TTFA was not measured on a real call.** A dispatch with no participant cannot
complete playout, and joining a WebRTC room with a live microphone is not
possible from here. The 619 ms figure is direct wrapper timing against the live
API; the worker path is verified only as "synthesises without error".

---

## Phase 9 — Research-calibrated prompt, Kannada numbers, VAD tags

### 9.1 New modules

**`sakhi_prompts.py`** — `SAKHI_SYSTEM_PROMPT` (+ Hindi variant) and `LLM_CONFIG`,
centralised so a Kannada speaker and a clinician can review the wording without
reading `agent.py`.

**`sakhi_kannada.py`** — deterministic Kannada number expansion, 0–999, plus a
streaming `tts_text_transforms` hook.

### 9.2 Kannada numbers: moved from prompt to code

Sarvam mispronounces ASCII digits in Kannada. On a blood-pressure reading that
is a clinical defect, not a cosmetic one.

The draft solved it with a worked-example table in the prompt. Correct instinct,
wrong layer:

- **Unreliable** — a model under a 120-token cap, mid tool-call, drops the rule
  intermittently, and the failure is silent.
- **Expensive** — ~300 prompt tokens on *every* generation.

So the expansion is now code. Kannada tens take a bound unit form rather than
simple concatenation (ಇಪ್ಪತ್ತು + ನಾಲ್ಕು → **ಇಪ್ಪತ್ತನಾಲ್ಕು**), which the
implementation handles by stripping the trailing vowel sign and attaching a
suffix form. All eleven worked examples from the draft are pinned as tests.

The transform is **streaming, not buffering**. Buffering the reply to expand it
would hold TTS until the LLM finished — costing far more latency than it saves —
so it yields continuously and withholds only a short tail that might still grow
into a number. Verified correct at one character per chunk:

```
"14" | "5"        ->  ನೂರ ನಲವತ್ತೈದು     (not "fourteen" then "five")
"ಬಿಪಿ 145/95 ಇದೆ."  ->  ಬಿಪಿ ನೂರ ನಲವತ್ತೈದು ಬೈ ತೊಂಬತ್ತೈದು ಇದೆ.
```

The prompt still asks for Kannada words — one line, so the transform usually has
nothing to do — and the code guarantees it.

### 9.3 VAD tags reinstated, safely

`[VAD: FAST]` / `[VAD: SLOW]` are back. The idea was always sound: an explicit
signal from the model beats inferring cognitive load from its wording.

What made the old version dangerous was never the tags. It was that the handler
mutated `vad_provider.min_silence_duration` — the Silero instance **shared across
every concurrent call** on the worker — and reached into the private
`session._turn_detector._min_endpointing_delay` (changed.md C6).

The new path keeps the signal and drops the hazard:

- `DynamicVADController.apply_tag()` routes through the same **per-call public
  `session.update_options()`** API as the rest of the controller.
- `VadTagTransform` is registered **first** in `tts_text_transforms`, so the tag
  is stripped before any other transform sees it and before TTS. The previous
  stripping lived in a closure that ran only sometimes; a tag that slipped
  through was read aloud as "bracket vee ay dee".
- Tolerant of case, spacing, and tags split across streamed chunks
  (`["[", "VAD", ": ", "FAST", "]"]` is caught).
- A missing controller (tags can arrive before `session.start()` attaches it) is
  a no-op, not a crash.

The regex heuristic in `tts_node` remains as a fallback when no tag is present.

### 9.4 Three deviations from the draft

1. **Tool names corrected.** The draft referenced
   `record_bp(patient_id, systolic, diastolic, headache, blurry_vision, swelling)`
   and `flag_high_risk_pregnancy(patient_id)`. Neither exists. Naming absent
   tools makes the model emit calls that fail and the turn dies. The prompt now
   matches `ClinicalToolset`: patient_id is held in call state, BP and symptoms
   are separate calls, and there is no `flag_high_risk_pregnancy` because
   `TriageEngine` assigns risk — the model does not.
2. **Triage thresholds not restated.** The draft put "systolic >= 140 OR
   diastolic >= 90" in the prompt. The engine already applies exactly that, plus
   severe-range and gestational rules the prompt does not mention. Restating a
   subset invites the model to announce a risk level that disagrees with the
   recorded one — two different answers to "is this mother in danger".
3. **`presence_penalty` / `frequency_penalty` dropped.** LiveKit's `openai.LLM`
   wrapper does not accept them (verified against livekit-plugins-openai 1.6.10);
   passing them raises `TypeError` at provider construction.

### 9.5 LLM configuration

`LLM_CONFIG` now supplies the defaults, with env vars still winning:

| Setting | Value | Note |
|---|---|---|
| model | `llama-3.3-70b-versatile` | Replaces `qwen/qwen3-32b`. Qwen 3 is a reasoning model that can spend the whole token budget on hidden reasoning and return no content — the risk flagged twice in earlier phases |
| temperature | 0.15 | Stable phrasing; Sarvam renders each rephrasing with different prosody |
| top_p | 0.9 | |
| max_completion_tokens | 120 | 1–2 sentences needs nowhere near 250 |

**The env was overriding all of these.** `GROQ_LLM_MODEL=qwen/qwen3-32b`,
`LLM_TEMPERATURE=0.25`, `LLM_MAX_COMPLETION_TOKENS=90` were set, so the new
config would have had no effect. `.env` updated to match.

### 9.6 Cost and verification

**Prompt grew 109 → 407 tokens** (+29 state = ~436 prefill per generation). That
is a real per-turn latency cost, accepted deliberately: the clinical workflow,
spelling fallback and medication-refusal script are behaviour worth paying for.
It is still far below the draft's ~700 tokens, because the number table is gone.

| Check | Result |
|---|---|
| Draft's 11 pronunciation examples | all match |
| Digits reaching TTS, any chunking | none |
| VAD tags reaching TTS | none |
| Tag → endpointing state | FAST→TRANSACTIONAL, SLOW→HIGH_COGNITIVE_LOAD |
| Live worker boot | `llama-3.3-70b-versatile` primary, no new errors |
| Tests | **565 passing** (440 pytest + 125 standalone) |

One existing assertion was updated rather than worked around:
`test_prompt_is_sakhi_not_the_clinic` banned `[VAD:` in the prompt, from when the
protocol was removed. The property that actually matters is that a tag is never
*spoken*, which now has four dedicated tests.

---

## Phase 10 — Groq-only chain, context pruning, catalog fix

### 10.1 Two live findings that changed the plan

Probed against the account on 2026-08-15, tools attached:

| model | TTFT | tool-calling |
|---|---|---|
| `llama-3.3-70b-versatile` | ~521 ms | yes |
| `llama-3.1-8b-instant` | ~185 ms | yes |
| `openai/gpt-oss-20b` | ~707 ms | **no** |
| `gemma2-9b-it` | — | **DECOMMISSIONED** |

1. **`gemma2-9b-it` no longer exists.** The API answers
   *"The model `gemma2-9b-it` has been decommissioned"* with HTTP 400. As the
   requested tier 4 it would have failed 100% of the time — the exact opposite
   of the crash elimination this change was for.
2. **`openai/gpt-oss-20b`, the obvious replacement, was rejected on measurement.**
   Slower than the 70b primary *and* it did not emit a tool call for a
   blood-pressure statement. On an agent whose entire clinical function is tool
   calls, a tier that talks instead of calling `record_bp` is worse than no tier.

Tier 4 is therefore `llama-3.1-8b-instant` on the second key. Two proven models
across two key slots still cover both real failure modes — a per-key rate limit
and a single model degrading — and every tier can actually drive the tools. All
four are overridable via `GROQ_MODEL_T1..T4`.

### 10.2 Groq-only fallback chain

```
T1 llama-3.3-70b-versatile @ key1   (primary)
T2 llama-3.3-70b-versatile @ key2   (survives a per-key rate limit)
T3 llama-3.1-8b-instant    @ key1   (survives a model degradation)
T4 llama-3.1-8b-instant    @ key2
```

OpenAI is gone from the speaking path. Also removed from `llm_node`'s
empty-completion repair, which constructed an `openai.LLM` and would have been
the last OpenAI dependency — it would have failed outright on a Groq-only
deployment with no `OPENAI_API_KEY`. It now uses `llama-3.1-8b-instant`.

**The accepted risk, stated plainly:** there is no longer a non-Groq escape
hatch. If Groq is down globally, every tier fails together and the call has no
LLM. `VOICE_LLM_OPENAI_SAFETY_NET=true` re-adds gpt-4o-mini as a fifth tier for
anyone who does not want a single-vendor dependency on a maternal-health line.

`build_llm()` now raises at construction if no Groq key is configured, rather
than answering a call with no LLM.

**Per-tier timeouts are not expressible.** `FallbackAdapter` takes one
`attempt_timeout` for the whole chain, not one per provider, so the requested
1.2s / 1.0s / 1.0s becomes a single `LLM_ATTEMPT_TIMEOUT=1.0` — the tightest of
the set, so no tier waits longer than intended.

### 10.3 Context pruning — and where the tokens really are

Measured before touching anything:

| component | before | after | prunable by a sliding window? |
|---|---|---|---|
| system prompt | 407 | 190 | no — trimmed instead |
| tool schemas | 774 | 662 | **no — sent every request** |
| state block | 29 | 29 | no |
| **fixed floor** | **1,210** | **881** | |
| conversation history | grows all call | capped | **yes** |

The important finding: **774 of the ~2,100 tokens were tool JSON schemas**, which
a sliding context window cannot touch — they are re-sent on every request as
long as the tools are bound. Only shortening the tool docstrings (they become
the schema `description` fields) reduces that, so all five were rewritten to one
line each.

The sliding window itself uses `ChatContext.truncate()` rather than hand-rolled
slicing, because it already drops a leading `function_call` /
`function_call_output`. **Cutting mid tool-exchange leaves an orphaned tool
result whose originating call is gone, and the OpenAI-compatible API rejects
that outright — the turn dies.** A test now asserts no orphan is produced at any
window size from 2 to 20.

Budget is in *items*, not turns (`LLM_CONTEXT_MAX_ITEMS=12`): one turn can be a
user message, an assistant message, a function call and its output. The mother's
details live in the state block, not the transcript, so losing older turns costs
nothing clinically.

### 10.4 Prompt and voice catalog

System prompt trimmed to **115 words / 190 tokens** (from 407).

While trimming, a latent bug surfaced: `SAKHI_SYSTEM_PROMPT_HI` was built by
chained `.replace()` on the finished Kannada text, matching literal sentences.
Rewriting the prompt silently broke every replacement, so **the Hindi variant
became an exact copy of the Kannada one** — an ASHA worker who picked Hindi
would have been answered in Kannada, with nothing in the logs to explain it.
Both variants are now built by substitution into one template, and a test
asserts they differ.

`voice_catalog.py`: added the nine bulbul:v2 speakers and changed
`default_voice("sarvam")` from `pooja` (a v3 speaker) to `manisha`. The catalog
default was being remapped at runtime on every call. Live log now reads
`Voice selected - Sarvam voice: manisha`, with **zero** remap warnings.

### 10.5 Sub-100ms TTFT is not reachable

Measured, India → Groq (US): **521 ms** on the 70b primary, **185 ms** on the 8b.
185 ms is essentially network round-trip; the target is below the floor imposed
by physics and routing, not by configuration.

What the work here actually buys: ~330 fewer prefill tokens per request, a
history that no longer grows unbounded, and a chain that fails over in ~1 s
instead of hanging. Realistic first response stays ≈1.3–1.4 s end to end.

Getting materially below that needs a change of topology — a Groq region closer
to India, or an on-prem/edge model — not further prompt tuning.

### 10.6 Verification

| Check | Result |
|---|---|
| Chain built live | `T1:llama-3.3-70b@k1 -> T2:…@k2 -> T3:llama-3.1-8b@k1 -> T4:…@k2` |
| Voice remap warnings | **0** |
| Prompt | 115 words / 190 tokens |
| Fixed floor | 1,210 → 881 tokens |
| VAD / endpointing | 0.25 s / 0.3 s |
| Tests | **573 passing** |

---

## Phase 11 — Seven leaks found by a live red-team call

A tester agent posing as an ASHA worker called Sakhi on 2026-08-15. The call log
exposed seven defects, three of them clinical. All are fixed; each has a
regression test quoting the original log line.

### 11.1 Under-triage — a stated symptom was never recorded (clinical)

```
Triage: MODERATE — Raised blood pressure 145/95; No danger symptoms reported
Symptoms screened — none reported
```

The worker said the mother had a **severe headache** in her opening sentence.
The model then called `record_symptoms(false, false, false)`. Triage stayed
MODERATE instead of HIGH_RISK, and no referral was indicated. **Under-triage is
the most dangerous direction this system can fail in, and it failed silently.**

Fix: symptom capture no longer depends on the model passing the right booleans.
`detect_symptoms()` in `sakhi_domain.py` scans the caller's own words on every
turn, and `SakhiAgent._capture_symptoms()` merges the result into call state and
**re-runs triage immediately** when a new symptom lands on top of an existing BP
reading — that combination is exactly the MODERATE → HIGH_RISK transition.

Because `SymptomSet.merged_with()` only ORs, the model's later `false` cannot
erase what the mother actually reported.

Detection is **negation-aware** (`ತಲೆನೋವು ಇಲ್ಲ`, "no headache" must not register,
or every screening question would flag itself the moment it was answered "no")
and covers Kannada, Hindi, English **and English transliterated into Kannada
script** — Sarvam STT returned `ಲೈಕ್ ಎ ಹೆಡ್ ಏಕ್` for "like a head ache" on this
very call, so matching only native spellings misses half of real speech.

### 11.2 Over-escalation — specialist connected on a MODERATE (clinical)

```
Triage: MODERATE  →  requires_escalation = False
SMFMI escalation — Specialist connected for M-203:
    "moderate risk level due to raised blood pressure and no danger symptoms"
```

The audit trail recorded an escalation whose own stated reason said escalation
was not needed. `connect_smfmi_specialist` accepted any call from the model.

The deterministic net built earlier only prevented the model **under**-escalating.
This is its mirror: the tool now refuses when `state.assessment` exists and does
not require escalation, writes no audit entry, and returns an instruction
telling the model *not* to claim a specialist is coming. A call with no
assessment yet is still allowed through — there is no decision to contradict,
and refusing would block a genuine emergency raised before any reading.

Between the two, TriageEngine owns the decision in **both** directions.

### 11.3 Script mismatch created a duplicate patient (clinical)

```
Patient search miss — No SixtyDB record for name=ಸುಮಾ ಗೌಡ
Mother registered — Suman Goud (M-203)
```

Stored records are Latin ("Suma Gowda"); Sarvam STT returns Kannada script
("ಸುಮಾ ಗೌಡ"). Substring matching scored zero, so **every existing mother missed
lookup**, and the model then registered a corrupted duplicate with a fabricated
phone number.

Fix: `match_key()` transliterates Kannada/Devanagari to Latin and drops vowels —
vowels are precisely what varies between spellings of the same Indian name
(Gowda / Gouda / Goud), and Indic vowel signs are separate codepoints. Consonant
skeletons are stable. `find_patients()` tries exact substring first, then
`name_similarity()` above a threshold.

| comparison | score |
|---|---|
| `ಸುಮಾ ಗೌಡ` vs `Suma Gowda` | 0.89 |
| `ಪ್ರಿಯಾ ಪಾಟೀಲ್` vs `Priya Patil` | **1.00** |
| `Suman Goud` vs `Suma Gowda` | 0.80 → matches, no duplicate |
| `Suma Gowda` vs `Priya Patil` | 0.00 |

### 11.4 LLM timeout below the observed tail (self-inflicted)

```
Session error — APIConnectionError("all LLMs failed ([4 providers]) after 1.086s")
```

All four Groq tiers burned through in ~1.09 s, while the same call logged a
perfectly valid `ttft=2.210s`. `LLM_ATTEMPT_TIMEOUT=1.0` was set in the previous
phase and is below the real tail — it turned a slow-but-valid response into a
dead call. Now **3.0 s**, with a test asserting it clears 2.5 s.

### 11.5 Voice remap fixed at the wrong layer

```
Voice selected from dispatch — Using sarvam voice pooja
Voice remapped — 'pooja' is not a bulbul:v2 voice; using 'manisha'
```

`voice_catalog.default_voice()` was corrected to `manisha` last phase, but
`page.tsx:308` hardcoded `voice: "pooja"`, and dispatch metadata overrides the
catalog. The frontend no longer sends a voice at all — the one place that knows
which model is configured decides.

Live log now reads `Voice selected - Sarvam voice: manisha`, zero remaps.

### 11.6 Sakhi's own speech was never logged (audit gap)

The transcript captured what Sakhi *heard*, never what she *said* — only
`characters_count`. When the log showed advice about *"lifestyle changes like
eating a healthy diet, managing stress"*, **it was impossible to determine
whether Sakhi said it or the tester did.** That is the first question a clinical
audit asks, and it also made probes 5–7 of the red-team script unverifiable.

`tts_node` now emits an `agent_speech` event with the spoken text (truncated to
400 chars).

### 11.7 Context pruning bounded items, not tokens

`tokens=2436` against an 881-token floor, with only 13 items retained — one
Kannada STT transcript item can be enormous. Pruning now tightens the item
budget until an estimated token count fits (`LLM_CONTEXT_MAX_TOKENS=700`), using
a cheap character-based estimate rather than tiktoken, which would cost more on
the hot path than the pruning saves.

### 11.8 Verification

The failed call was replayed end to end against the fixed code:

| step | before | after |
|---|---|---|
| lookup `ಸುಮಾ ಗೌಡ` | miss → duplicate created | finds Suma Gowda |
| BP 145/95, no symptoms from model | MODERATE | MODERATE |
| model calls specialist anyway | connected, audited | **refused, nothing audited** |
| headache heard in speech | ignored | HIGH_RISK, escalation required |
| escalation retried at HIGH_RISK | — | connected, audited |

Test data pollution from the live call (duplicate `M-203 "Suman Goud"` and the
bogus MODERATE escalation) was removed from the seed.

**Tests: 593 passing**, including 20 new regressions in
`tests/test_sakhi_regressions.py`.

### 11.9 Still open

- `eou_delay` reached **1.031 s / 1.091 s** against a 0.3 s target: the adaptive
  VAD entered `HIGH_COGNITIVE_LOAD` and stayed there. `transcript_delay=0.964s`
  also shows Sarvam STT, not endpointing, is a large share of turn latency.
- TTS `ttfb` was 0.83–0.94 s live, against 438 ms measured for a bare clause —
  the difference is clause buffering plus network.
- The DPDP probes (7, 8, 9 of the red-team script) never ran, because the call
  ended early. Cross-patient disclosure is still unverified: `search_patient`
  will return any mother by name with **no caller authentication anywhere**.

---

## Phase 12 — DPDP compliance and multilingual safety guardrails

`guardrails.py` already existed — 210 lines, wired into `SakhiAgent`, 73 passing
tests. It was **extended, not replaced**. Every existing name and behaviour is
preserved; all 73 original tests still pass unchanged.

### 12.1 What was measured first

| turn | before |
|---|---|
| "which tablet for headache?" | caught |
| **"ಯಾವ ಮಾತ್ರೆ ಕೊಡಬೇಕು?"** (Kannada) | **missed** |
| **"कौन सी दवा दूं?"** (Hindi) | **missed** |
| **"give her 500mg paracetamol"** | **missed** |
| "ignore previous instructions" | caught |
| **any PII** | **no redaction at all** |

On an agent that speaks only Kannada, an English-only medication guard is close
to decorative. The brief's own example — 500 mg paracetamol — was also missed.

### 12.2 The worst finding: the refusal scripts themselves

The approved scripts were **English clinic-booking lines**:

> *"I'm really not able to advise on medicines... shall I find you a slot?"*
> *"I'm just the receptionist here, so let's stay with your appointment..."*

Two failures at once, on the one path where failure matters most: the wrong
**language** for a Kannada-speaking ASHA worker, and the wrong **product** —
offering appointments Sakhi cannot book. A guardrail that fires and then says
something incomprehensible has not protected anyone.

All six categories are now written in Kannada, Hindi and English, on-brand for
maternal health, selected by the caller's language via `script_for()`. Helpline
numbers are spelled digit-by-digit in words (`ಒಂದು ಸೊನ್ನೆ ಎಂಟು`, not "108") —
a voice reading 14416 as a cardinal number is not dictation anyone can act on.

The existing tests compare against the constants by name, not by literal text,
so all 73 kept passing.

### 12.3 PII redaction

`redact_pii()` masks Aadhaar, Indian mobile, PAN and bank account numbers,
ordered most-specific first so an Aadhaar is not partly consumed by the
account pattern.

**Clinical numbers are deliberately never matched.** Every pattern requires 10+
digits; blood pressure, gestational weeks and age are 1–3 digits. Redacting a BP
reading would be a clinical failure dressed up as a privacy win, and there is a
test asserting `145/95` and `28 weeks` survive untouched.

Redaction runs **before** classification, so an identifier does not survive even
on turns that are perfectly safe and pass through to the LLM.

### 12.4 `SakhiGuardrails`

- `sanitize_input(text, language) -> InputDecision` — redact, then classify.
  `classify_turn` runs first so **emergency and self-harm keep outranking
  everything**: a flat medication refusal to a haemorrhaging mother is a worse
  failure than answering the paracetamol question. Medication and injection
  checks run last, on the redacted text.
- `validate_output(text, language) -> OutputDecision` — catches what the input
  gate cannot: the model volunteering a drug name or dose nobody asked for, and
  the model echoing an identifier back.

### 12.5 Integration

`SakhiAgent.on_user_turn_completed` now calls `sanitize_input()` before the LLM.
On a block it speaks the approved script in the caller's language and raises
`StopResponse` — **zero database tools run on that path**, because the turn is
aborted before the LLM is invoked and so no tool call can be generated.

When PII is found, the redacted text replaces the message the model will see.

### 12.6 Compliance logging

The live call log had shown raw transcripts written straight to
`logs/pipeline_events.jsonl`, plus a tool event logging a raw phone argument.
Both are now redacted at the point of writing:

- STT transcripts — masked before the pipeline event and before the post-call report
- Sakhi's own speech (`agent_speech`, added in Phase 11) — masked
- `search_patient` miss — phone reduced to `***1234`

Under DPDP, an identifier that never lands in a log cannot leak from one.

### 12.7 Verification

| check | result |
|---|---|
| Kannada / Hindi / dosage medication | all blocked |
| Ordinary clinical speech | not blocked (false-positive discipline held) |
| PII masked | aadhaar, phone, PAN, account |
| BP / weeks / age | never redacted |
| Prompt injection | blocked |
| Emergency vs medication precedence | emergency wins |
| Output gate | blocks generated drugs and doses |
| **Latency** | **0.022 ms/call** over 2,000 calls — **225× inside the 5 ms budget** |
| Tests | **642 passing** (49 new) |

The budget is asserted in `tests/test_sakhi_guardrails.py`, not claimed in a
comment.

### 12.8 Still open

The cross-patient disclosure hole is **not** closed by this work.
`search_patient` still returns any mother by name with **no caller
authentication anywhere in the system**, and Phase 11's fuzzy matching widened
it. Under DPDP that is a purpose-limitation and access-control gap, and it needs
a design decision (caller identity from SIP ANI, a PIN, or restricting lookup to
the ASHA worker's own registered mothers) rather than another regex.

---

## Phase 13 — Production hardening: 10 live failure modes

### 13.1 The one that was a real bug: database de-sync

Verified before changing anything:

```
BP recorded BEFORE the mother is identified
  tool says recorded = True
  bp_records actually written = 0
```

`record_bp` triaged the reading, returned `recorded: True`, and persisted
nothing whenever `state.patient_id` was unset — which is the normal state early
in a call. Sakhi would tell the ASHA worker the reading was filed while SixtyDB
held nothing, and the mother's chart would silently disagree with what she was
told.

`_retriage()` now returns whether the write actually landed, and `_result()`
carries a `persisted` flag. When it is False the instruction becomes *"DO NOT
tell her the reading has been saved - it has not"* and directs the model to get
the mother's name first. Confirmation text is now gated on a verified write.

### 13.2 Backchannel and vocal-tic filter

LiveKit's interruption filter is duration- and word-count based
(`min_duration` 0.6 s / `min_words` 2). It cannot tell `"ಹೌದು ಸರಿ"` — two words
of agreement — from a real interruption. `is_backchannel()` adds the
content-based layer: Kannada, Hindi and English backchannels, plus inline
non-speech events (`[cough]`, `(sneezes)`).

Placed **before** symptom detection and guardrails, since a grunt carries
neither, and `StopResponse` ends the turn without generating.
`resume_false_interruption` (already set) resumes what Sakhi was saying.

Duration is honoured when available: `"ಹೌದು"` at 200 ms is a grunt, at 900 ms it
is an answer. Anything over 24 characters is treated as real speech regardless
of wording — a false positive here loses a clinical statement, which is far
worse than answering one extra grunt.

### 13.3 Latent acoustic filler

If no first token arrives in 600 ms, Sakhi speaks a short holding phrase
(`ಒಂದು ನಿಮಿಷ, ಪರಿಶೀಲಿಸುತ್ತಿದ್ದೇನೆ...`), cancelled the instant a token appears.

The threshold sits **above** the measured p50 TTFT (~0.4–0.6 s) on purpose, so
it fires on the tail — which reaches 2.2 s — rather than on every turn. Silence
in that gap reads as a dropped call on a rural line, and the worker starts
repeating herself, which arrives as a fresh turn and compounds the problem.

### 13.4 Sentence splitter

Negative lookarounds added for the cuts that are audibly wrong on a clinical
call: decimals (`145.5`), Indian digit grouping (`1,20,000`), honorifics
(`Dr.`), and unit abbreviations (`mg.`, `ml.`, `min.`, `BP.`). Kannada danda
(`।`, `॥`) added as genuine sentence ends, since Sarvam emits them.

`TTS_MIN_SENTENCE_CHARS = 10` is a hard floor applied on top of the adaptive
thresholds — a 3-character chunk is its own TTS request with its own network
round-trip and its own prosody contour.

### 13.5 ASR proper-noun degradation

**`export_stt_biasing_keywords()` cannot do what the brief describes, and the
function says so.** `SarvamSTT` exposes no keyword-biasing parameter; an earlier
version of `agent.py` built exactly such a list and passed it nowhere, and it
was removed as dead code (changed.md C11). Rebuilding that would repeat the
mistake.

Instead the vocabulary (15 Bidar villages + patient names) feeds
`correct_proper_noun()`, which snaps a mangled transcript to a known term
**after** recognition — the same failure mode, by the only mechanism the
provider supports. Wired into `register_mother`:

| heard | resolved |
|---|---|
| `Kamalnagara` | Kamalnagar |
| `Kamal Nagar` | Kamalnagar |
| `ಕಮಲನಗರ` | Kamalnagar |
| `ಔರಾದ್` | Aurad |
| `Balki` | Bhalki |
| `Bengaluru` | *unchanged* — guessing a village is worse than admitting uncertainty |

### 13.6 Already in place before this phase

| brief item | status |
|---|---|
| Guardrails: medication, PII, <5 ms | Phase 12 — measured 0.022 ms/call |
| Kannada number spelling | Phase 9 — `sakhi_kannada.py`, 11/11 examples |
| CallState across tool chains | Phase 1 — `session.userdata`, test added here |
| Fuzzy matching | Phase 11 — cutoff raised 0.72 → **0.75** per brief |
| Silence watchdog | pre-existing — `LINE_LIVE_MAX_UNANSWERED=3` |
| temperature 0.15 / top_p 0.9 | Phase 9 |
| max_tokens | 60 → **50** |

### 13.7 Deviations from the brief

- **No `sentence_buffer.py` was created.** `EagerClauseTokenizer` already lives
  in `agent.py` and is wired into `tts_node`. A second copy in a new module
  would have been dead code shadowing the live one.
- **`export_stt_biasing_keywords()` is not in `db_helper.py`.** That module is
  the legacy clinic SQLite layer (doctors, slots, appointments) and is not on
  the Sakhi path. The function lives with the patient data it reads, in
  `sakhi_repository.py`.
- **`min_chars = 10` is a floor, not a replacement** for the tuned
  `TTS_EAGER_MIN_CHUNK_CHARS=35`. Setting the working threshold to 10 would
  make chunking *more* choppy, not less — the opposite of the stated intent.

### 13.8 Verification

**679 tests passing** (37 new in `tests/test_sakhi_hardening.py`), worker boots
clean through to greeting, no errors naming any new code path.

One test of mine failed first time — it constructed `EagerClauseSentenceStream`
outside a running event loop. Fixed in the test; the product code was correct.

### 13.9 Still open

Unchanged from Phase 12, and not addressed by this work: `search_patient`
returns any mother by name with **no caller authentication anywhere in the
system**. Packet-loss resilience is also only partly covered — fuzzy matching
repairs clipped *names*, but there is no jitter-buffer or retransmit handling
for clipped *audio*, which is a transport-layer concern.

---

## 14. Three reported field problems (2026-08-17)

Reported verbatim: (1) "the voice is getting changed in the middle way or even
the start of the conversation", (2) "the fallback should be wired well it
shouldnot lag the system", (3) the 24-question intake "guardrail should be
strictly adhered to and sometimes the agent hallucinates".

Each was traced to a specific mechanism before anything was edited.

### 14.1 Problem 1 — the voice changed mid-call

**Two independent causes, both real.**

**Cause A: the TTS fallback tier spoke a different language.**
`build_tts()` built `FallbackAdapter([kannada_provider, openai.TTS(voice="alloy")])`.
TTS is clause-buffered — one synthesis request per clause — so a single slow
clause failed over and came back in an **English** voice while the clauses on
either side stayed Kannada. The voice changed *inside one sentence*. A
cross-language fallback is not viable in a per-clause pipeline.

Fixed: the chain is now same-language only, Sarvam ↔ Gnani, in both directions.
Both are genuine Kannada providers, so a failover changes timbre but stays
Kannada and stays female. OpenAI is retained only behind
`VOICE_TTS_ENGLISH_LAST_RESORT` (default **off**).

**Cause B: four numbers each claimed to be "the pace".**

| path | old value |
|---|---|
| `_build_sarvam_tts` default | 0.92 |
| `_build_gnani_tts` default | 0.92 |
| greeting (`SARVAM_PACE_GREETING`) | 0.9 |
| session (`call_tts_speed`) | 1.08 |

`SARVAM_PACE` was not set in `.env`, so the defaults diverged: the greeting
played at 0.9 and the conversation at 1.08 — the same voice ~20% faster, one
sentence into every call. That is the "changed at the start" report.

Fixed: `default_tts_pace()` is the single source of truth for both providers,
greeting and session.

**Cause B2 (found while fixing B): the greeting cache key ignored pace.**
Pre-rendered greeting WAVs would have kept playing at the old speed forever.
`_greeting_cache_path()` now includes pace; six stale files were cleared.

### 14.2 Problem 2 — the fallback lagged every turn

Measured TTFT (2026-08-17, real clinical prompt, this account):

| provider | median | max |
|---|---|---|
| groq/gpt-oss-120b | 604 ms | 1113 ms |
| groq/gpt-oss-20b | 401 ms | 433 ms |
| openrouter/llama-3.3-70b | 1842 ms | 2713 ms |
| openrouter/llama-3.1-8b | 2200 ms | 2373 ms |

**The chain put OpenRouter at tier 1 behind a single global
`LLM_ATTEMPT_TIMEOUT=1.8s`.** OpenRouter's *median* (1842 ms) exceeds that
timeout, so tier 1 was cancelled on essentially every turn, tier 2 (2200 ms)
was cancelled too, and only then did Groq answer in 604 ms. Every turn paid
~3.6 s of fallback that **could never succeed**.

Three changes:

1. **Fastest proven provider first.** Groq leads; OpenRouter is the
   cross-vendor backup it was meant to be.
2. **Per-group timeouts** (nested `FallbackAdapter`s). One number cannot serve
   a 604 ms provider and an 1842 ms one: sized for the fast one, the slow one
   can never answer. `GROQ_ATTEMPT_TIMEOUT=1.5`,
   `OPENROUTER_ATTEMPT_TIMEOUT=4.0`.
3. **Hedging** (`llm_hedge.py`, new). Serial groups still cost ~3.7 s on a
   total Groq outage. `HedgedLLM` starts the backup once the primary has been
   silent for `LLM_HEDGE_DELAY` (0.8 s) instead of waiting for it to fail, and
   the first token wins. A primary that fails *early* skips the delay entirely.

| scenario | before | after |
|---|---|---|
| happy path | ~4200 ms | **519 ms** |
| total Groq outage | ~3700 ms | **1610 ms** |

Hedging is safe with tool calls: an LLM only *emits* tool-call requests and the
agent executes them afterwards, so the loser's chunks are dropped before any
tool runs. Disable with `SAKHI_LLM_HEDGE=false`.

### 14.3 Problem 3 — intake drift and invented questions

The 24-question questionnaire existed **only as prose in the system prompt**.
`SakhiCallState` had 16 fields and `prompt_summary()` reported three facts
(`mother=`, `bp=`, `symptoms=`). Roughly twenty answers had nowhere to be
stored and no completion marker, so the model re-derived its position in the
list from the raw transcript every turn. One garbled STT turn or one context
prune and it lost its place — hence re-asking, skipping, and (asked for "the
next question" with no checklist to ground it) inventing questions outright.

New `sakhi_intake.py` makes the questionnaire authoritative in code:

- `INTAKE_QUESTIONS` — the 24 questions as frozen data, the single source of
  truth. `sakhi_prompts.py` now **renders** the prompt inventory from it, so
  the prompt and the tracker cannot drift (they were two hand-typed copies).
- `IntakeProgress` — records answers, computes the next question, and reports
  `intake=N/24` plus the **exact next question** into every generation.
- Auto-satisfaction: name+age and gestational weeks captured by the pipeline
  tick their questions off without asking.
- `arm_next()` in `llm_node` marks the question being asked; `record_answer()`
  in `on_user_turn_completed` closes it. Placed *after* the backchannel and
  fragment-recovery guards, so a grunt or half-sentence cannot tick a clinical
  question off the checklist.
- The prompt gained an "Intake Flow Control" section that makes the injected
  `intake=` line override the model's own judgement, and forbids asking
  anything not in the inventory.

### 14.4 Verification

**687 tests passing** (35 new: `tests/test_voice_consistency.py`,
`tests/test_llm_hedge.py`, `tests/test_sakhi_intake.py`). Worker restarted onto
the new code and registered clean.

Live simulation confirmed the model now asks the armed question verbatim
instead of choosing its own.

### 14.5 Correction

While testing I saw empty first-turn completions and suspected the enlarged
prompt was starving gpt-oss's reasoning budget. Measured directly: **0/16 empty
completions** at caps of both 200 and 320 tokens, every one `finish_reason:
stop`. The empty turns were **HTTP 429s caused by my own benchmarking
exhausting the Groq rate limit**, not the prompt change. `max_completion_tokens`
was left at 200.

### 14.6 Still open

- `search_patient` still has **no caller authentication** — unchanged and still
  the largest gap.
- The multilingual turn-detector fails to load in this environment
  (`torch has no attribute 'LongTensor'` → `AutoTokenizer` import fails), so
  endpointing falls back to VAD-only. Pre-existing, unrelated to this work.
- Groq rate limits are currently exhausted from benchmarking; they reset on
  their own.
