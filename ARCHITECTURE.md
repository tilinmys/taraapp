# Sakhi — Architecture

Subsystem detail for the maternal-health voice agent. For the overview and the
refinement story, see [README.md](README.md).

---

## 1. Call flow

```
        ASHA worker (Bidar village)                Exotel / PSTN (future)
                 │                                          │
        browser mic + WebRTC                            SIP trunk
                 │                                          │
                 └──────────────┬───────────────────────────┘
                                ▼
                  LiveKit Cloud (room + explicit dispatch)
                                │  agent_name = mystree-care
                                ▼
        ┌───────────────────────────────────────────────────┐
        │  agent.py worker process                          │
        │                                                   │
        │  prewarm_process()                                │
        │    ├── Silero VAD              (once per process) │
        │    ├── MockSixtyDBRepository   (once per process) │
        │    └── TriageEngine            (once per process) │
        │                                                   │
        │  entrypoint()  — one per call                     │
        │    ├── parse dispatch metadata → Language         │
        │    ├── build STT / LLM / TTS / turn handling      │
        │    ├── SakhiCallState  → AgentSession.userdata    │
        │    ├── ClinicalToolset → session tools            │
        │    ├── AvatarBackend.start()  (degrades to audio) │
        │    └── supervision loop (silence auto-close)      │
        └───────────────────────────────────────────────────┘
                                │
                                ▼
                  pipeline_event() → logs/pipeline_events.jsonl
                                │
                                ▼
                  status_server (:8080) → frontend log rail
```

---

## 2. Turn pipeline

One turn, end to end:

```
1. Caller speaks
2. BVC noise cancellation
3. Silero VAD detects end of speech        VAD_MIN_SILENCE = 0.25s
4. Endpointing wait                        MIN_ENDPOINTING_DELAY = 0.3s
5. Sarvam STT final transcript             ~300ms
6. SakhiAgent.on_user_turn_completed
      └── guardrails.classify_turn()
          emergency / self-harm / medical-advice / jailbreak / abuse
          → fixed script + StopResponse, LLM never sees the turn
7. SakhiAgent.llm_node
      ├── inject SakhiCallState.prompt_summary()   (~29 tokens)
      ├── inject escalation directive if pending
      └── Groq → OpenAI FallbackAdapter            ~400ms TTFT
8. Tool loop (if the model calls one)
      └── ClinicalToolset → TriageEngine → PatientRepository
9. SakhiAgent.tts_node
      ├── EagerClauseTokenizer splits at clause boundaries
      ├── Sarvam REST synthesis per clause          ~440ms
      ├── DynamicVADController.evaluate_agent_text()
      ├── farewell detection → auto-hangup
      └── escalation safety net → force specialist
10. Audio track → caller
```

---

## 3. Turn detection

Two mechanisms, one owner.

**Silero VAD** (`VAD_MIN_SILENCE = 0.25s`) detects end of speech. Its wait
**stacks sequentially** with the endpointing delay — perceived silence before
commit ≈ VAD silence + `min_delay` ≈ 0.55s.

**`DynamicVADController`** owns endpointing, one instance per call, through the
public `session.update_options()` API. It switches on what Sakhi just said:

| State | Delay | Trigger |
|---|---|---|
| `TRANSACTIONAL` | 0.3s | Closed questions |
| `HIGH_COGNITIVE_LOAD` | 1.0s | BP readings, gestational age, names, villages — in Kannada, Hindi and English |
| `FRAGMENT_RECOVERY` | 1.3s | Currently unwired (see below) |

> **Why not the VAD instance:** the Silero object is shared across every
> concurrent call on the worker via `proc.userdata`. Mutating
> `min_silence_duration` would retune turn-taking for every other live call. An
> earlier `[VAD: FAST]` / `[VAD: SLOW]` tag mechanism did exactly that and was
> removed.

`FRAGMENT_RECOVERY` is reachable but unwired — its only trigger was an English
clinic-phrase fragment detector, removed with the booking flow. A Kannada
fragment detector can call `fragment_recovery()` directly.

**Barge-in:** 0.6s / 2 words. Filters backchannels (`haan`, `ಹೌದು`, `hmm`)
without disabling interruption — an ASHA worker correcting a misheard BP must be
able to cut in.

---

## 4. Clinical core

Dependency-free (stdlib only), so it tests without LiveKit, network or keys.

```
sakhi_domain.py          value objects + per-call state
  ├── Language           kn / hi / en → sarvam_code, honorific
  ├── RiskLevel          ROUTINE < OBSERVATION < MODERATE < HIGH_RISK
  ├── BloodPressure      frozen, self-validating
  ├── SymptomSet         frozen, keyword-only, accumulating
  ├── RiskAssessment     level + reasons + recommended_action
  └── SakhiCallState     one per call, mutated only through methods

sakhi_triage.py
  └── TriageEngine.assess(bp, symptoms, gestational_weeks) → RiskAssessment

sakhi_repository.py
  ├── PatientRepository        ABC — the swap point for real SixtyDB
  └── MockSixtyDBRepository    memory reads, debounced write-behind
```

### Why `BloodPressure` validates

STT mishearing "one forty over ninety" as "one forty over nine hundred" must
surface as a **re-ask**, never be recorded against a mother's chart. Rejected:
out-of-range values, and systolic ≤ diastolic (transposed numbers).

### Why symptoms accumulate

`merged_with()` means a later "no swelling" cannot erase an earlier "yes
headache". Risk is a function of the whole picture, and a symptom reported after
the BP reading can flip `MODERATE` into a red alert — which is why
`record_symptoms` re-triages rather than just storing.

### Why the repository is async and buffered

Reads serve from memory; writes mutate memory and a single debounced task does
the `json.dump` inside `asyncio.to_thread`. A burst of BP + symptoms +
teleconsult writes in one turn costs **one off-loop file write, after the caller
has already heard the reply**. The previous code did a full synchronous file
read *and* write on the event loop inside every tool call.

---

## 5. Providers

| Stage | Primary | Fallback |
|---|---|---|
| STT | Sarvam `saarika` (`kn-IN` / `hi-IN`) | — |
| LLM | Groq (`GROQ_LLM_MODEL`) | OpenAI `gpt-4o-mini` via `llm.FallbackAdapter`, 3.5s attempt timeout |
| TTS | Sarvam `bulbul:v2` REST | OpenAI `alloy` via `tts.FallbackAdapter`, pinned to 22050 Hz |

### Sarvam TTS: why REST, not WebSocket

The streaming endpoint `wss://api.sarvam.ai/text-to-speech/ws` was probed
directly and **never returns audio** for either model — it connects, accepts
`config`/`text`/`flush`, then hangs. The provider therefore declares
`streaming=False`, which routes AgentSession through `tts.StreamAdapter` +
`EagerClauseTokenizer`: one REST call per clause, first audio in ~440 ms.

Measured, same Kannada text:

| model | speaker | clause | sentence |
|---|---|---|---|
| bulbul:v2 | manisha | 438 ms | 779 ms |
| bulbul:v2 | vidya | 451 ms | 764 ms |
| bulbul:v3 | pooja | 1012 ms | 2655 ms |

`bulbul:v1` is not selectable — the API rejects it.

**Speaker sets do not overlap between models.** v2 accepts
`anushka, abhilash, manisha, vidya, arya, karun, hitesh, aditya, ritu`; v3 has a
different list. Sending a v3 name to v2 is a hard 400 and a silent turn, so
`resolve_sarvam_voice()` pairs them and remaps rather than failing.

Both models return **22050 Hz** from REST. A previous
`24000 if "v3" in model` guess meant audio played ~9% fast and pitched up.

The TTS fallback is a **degraded** path, not an equal one — OpenAI reads Kannada
with an English voice. It exists so a Sarvam failure is not dead air on a call
where a mother may be in danger.

---

## 6. Avatar seam

```
AvatarBackend (ABC)
  ├── NullAvatarBackend    audio-only, default, cannot fail
  ├── TavusAvatarBackend   echo mode — our audio drives the lips
  ├── BeyAvatarBackend     Beyond Presence
  └── DIDAvatarBackend     stub; refuses to start rather than pretend
```

`start()` returns a bool and **never raises**. An unknown `SAKHI_AVATAR` value
falls back to audio-only with a warning rather than blocking calls.

> **Plugins must be imported at module scope.** LiveKit's
> `Plugin.register_plugin()` raises *"Plugins must be registered on the main
> thread"*, and a backend's `start()` runs on a job thread — so a lazy import
> inside `_start()` fails on every call and silently degrades to audio-only.

---

## 7. Observability

`pipeline_event()` writes structured JSONL through a bounded queue drained by one
daemon writer thread — the hot path is a dict construction and a lock-free put.

Stages: `worker`, `auth`, `webrtc`, `microphone`, `dispatch`, `stt`, `turn`,
`llm`, `tts`, `playback`, `tools`.

Key events for latency work:

| Event | Meaning |
|---|---|
| `eou_delay_ms` | End-of-utterance commit delay |
| `llm_ttft_ms` | Time to first LLM token |
| `ttfa_ms` | Time to first audio |
| `triage_assessed` | Risk decision + reasons (drives the UI banner) |
| `forced_escalation` | Safety net fired because the model did not call the tool |

---

## 8. Design principles

1. **Clinical decisions are code, not prompts.** The model never computes risk.
2. **Safety-critical paths do not depend on model compliance.** Escalation fires
   deterministically.
3. **Degrade, never drop.** Avatar failure → audio-only. TTS failure → fallback
   voice. Repository failure → call continues without persistence. Voice is the
   product.
4. **Per-call state, never module globals.** One worker process serves many
   concurrent calls.
5. **Every latency- or safety-affecting feature has an env kill-switch**
   (`VOICE_GUARDRAILS_ENABLED`, `VOICE_TTS_FALLBACK_ENABLED`,
   `VOICE_ADAPTIVE_VAD_ENABLED`, `SAKHI_AVATAR`, …).
6. **Measure before changing.** The TTS rewrite followed direct probing of both
   Sarvam endpoints, not assumption.
