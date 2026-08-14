# oops.md — Object-Oriented Design Audit & Target Model

How OOP is actually used in this codebase today, where it is strong, where it is
weak, and the class model the Sakhi refactor moves toward.

---

## 1. Inventory of existing classes

| Class | File | Verdict |
|---|---|---|
| `SarvamSTT`, `SarvamSpeechStream`, `SarvamTTS`, `SarvamTTSSynthesizeStream`, `SarvamTTSChunkedStream` | `sarvam_wrappers.py` | **Strong.** Proper implementation of the framework's `stt.STT` / `tts.TTS` ABCs. Polymorphic — the session neither knows nor cares which provider it holds. |
| `EagerClauseTokenizer`, `EagerClauseSentenceStream` | `agent.py:2684`, `2617` | **Strong.** Implements `SentenceTokenizer`/`SentenceStream` so it drops into `tts.StreamAdapter` unchanged. Textbook interface substitution. |
| `GracyAgent(Agent)` | `agent.py:2900` | **Strong pattern, wrong content.** Correct template-method use — overrides `on_user_turn_completed`, `llm_node`, `tts_node`. The overrides are full of booking logic. |
| `DynamicVADController` | `agent.py:2809` | **Strong.** One instance per call, private state, public API only, documented rationale for not touching the shared VAD. |
| `CallState` | `agent.py:2737` | **Weak — anemic model.** A dataclass of primitives plus one `summary()` string-builder. All the domain rules that should be its behaviour live in prompt prose. |
| `MockSixtyDB` | `mock_sixtydb.py:58` | **Correct code, dead.** Has the right methods (`record_bp` computes risk properly) and is **never instantiated**. The agent bypasses it and manipulates raw dicts through module-level `read_db`/`write_db`. |
| `SlotCache`, `BookingPrefetch` | `agent.py:662`, `972` | Clinic-domain, and instantiated as **module-level global singletons** (see §2.1). |
| `TurnLatencyAggregator` | `agent.py:777` | Reasonable, but also a module-level global. |
| `LockedSpeechStream`, `LockedAssemblyAISTT` | `agent.py:1940`, `2044` | Inheritance used to patch a vendor plugin. Now unreachable — `build_stt()` hard-returns `SarvamSTT`. |
| `FaqCache` | `faq_cache.py` | Fine in isolation; wrong domain for maternal triage. |

**The honest summary:** the *infrastructure* OOP in this project is good. The
provider wrappers and the agent subclass are well-designed. The **domain** OOP is
where it is weak-to-absent — there is no `Patient`, no `BloodPressure`, no
`RiskAssessment`, no repository. The clinical subject matter of the product is
modelled as loose dicts, bare `str`/`bool`/`int`, and English prose in a prompt.

---

## 2. Specific weaknesses

### 2.1 Global mutable singletons shared across concurrent calls

```python
slot_cache = SlotCache()            # agent.py:774
turn_latency = TurnLatencyAggregator()  # agent.py:847
booking_prefetch = BookingPrefetch()    # agent.py:1118
```

Constructed at **import time**, at module scope. One LiveKit worker process
handles multiple concurrent calls, so caller A's transcripts mutate the same
`booking_prefetch` that caller B's turn reads. `DynamicVADController` gets this
right (one instance per call, passed the session); these three do not.

### 2.2 No abstraction over persistence

The tools call `read_db()` / `write_db()` — free functions over a JSON file —
directly. There is no interface, so "swap mock SixtyDB for real SixtyDB" means
editing the body of every tool. This is the dependency-inversion violation that
matters most for the pilot, because the mock **is** meant to be swapped.

### 2.3 Primitive obsession

- Language is a bare `str` that means `"en"`, `"kn"`, `"hi"`, or `"kn-IN"`
  depending on which function you are in — `build_stt` has to re-derive
  `"kn-IN" if language.startswith("kn") else "hi-IN"` because no type carries it.
- Risk is the magic string `"NORMAL"` / `"HIGH"` / `"HIGH_RISK"` — and the codebase
  uses **both** `"HIGH"` and `"HIGH_RISK"` for the same concept
  (`mock_sixtydb.py:90` vs `:106`).
- Blood pressure is two loose ints threaded through five call sites.
- Symptoms are three positional bools — `record_symptoms(pid, True, False, True)`
  is unreadable and trivially transposed at a call site.

### 2.4 Encapsulation breaches

`extract_vad_flags` (`agent.py:3631`) reaches into
`session._turn_detector._min_endpointing_delay` (private framework attribute) and
mutates `vad_provider.min_silence_duration` on the **process-shared** VAD. It is
a closure buried inside `entrypoint()`, competing with the properly-encapsulated
`DynamicVADController` for the same knob.

### 2.5 A 570-line procedural `entrypoint()`

`entrypoint()` parses metadata, builds four providers, defines a closure, wires
eight event handlers, synthesises and caches a greeting, and then runs a
supervision loop — one function, no cohesion, nothing independently testable.

### 2.6 Behaviour that should be a method, isn't

The pre-eclampsia matrix is the product's whole clinical purpose. It exists as
prose in a prompt string. It should be a pure, unit-testable method on a domain
object, and it should be the code — not the model — that decides `HIGH_RISK`.

---

## 3. Target class model

```
                        ┌──────────────────────┐
                        │  «ABC» ClinicalRepo  │   dependency inversion:
                        │  PatientRepository   │   tools depend on this,
                        └──────────┬───────────┘   never on a JSON file
                                   │
                 ┌─────────────────┴─────────────────┐
                 │                                   │
      MockSixtyDBRepository                 SixtyDBRepository
      (async, in-memory + debounced         (real API, next phase)
       write-behind)

                        ┌──────────────────────┐
                        │     TriageEngine     │  pure, no I/O, unit-testable
                        │  assess(bp,symptoms) │  → RiskAssessment
                        └──────────────────────┘

   Value objects (immutable, self-validating):
      Language(Enum)        RiskLevel(Enum)      BloodPressure(frozen dataclass)
      SymptomSet(frozen)    Patient(dataclass)   RiskAssessment(frozen)

   Per-call state (one instance per session, never global):
      SakhiCallState  ── owns the maternal schema from the spec
                      ── owns its own prompt projection

   Agent layer:
      SakhiAgent(Agent) ── template-method overrides only
      DynamicVADController ── unchanged, already correct

   Avatar seam (built now, backend plugged next prompt):
      «ABC» AvatarBackend
        ├── NullAvatarBackend   (audio-only, default)
        ├── BeyAvatarBackend    (Beyond Presence — key already in .env)
        └── DIDAvatarBackend    (D-ID Talk Stream)
```

### Which pillar each piece is actually exercising

- **Encapsulation** — `SakhiCallState` owns its invariants (a BP reading updates
  risk through one method, not by three call sites writing the field);
  `MockSixtyDBRepository` hides whether persistence is a file, memory, or HTTP.
- **Abstraction** — `PatientRepository` and `AvatarBackend` are the two seams the
  pilot has to move across (mock → real SixtyDB, audio → avatar). Both are ABCs
  so the call pipeline never changes when the backend does.
- **Inheritance** — used only where the framework asks for it (`SakhiAgent(Agent)`,
  the Sarvam provider classes). Not used to share incidental code.
- **Polymorphism** — `build_tts()` returning any `tts.TTS`, repositories behind one
  interface, avatar backends behind one interface. The session holds the base type.

### Deliberate non-goals

- No class hierarchy for the sake of one. `TriageEngine` stays a small object with
  a pure method rather than a `Strategy` tree with one strategy.
- The value objects are `frozen=True` dataclasses, not full entities with identity —
  a BP reading has no lifecycle.

---

## 4. Status — all landed

Phases are logged in [changed.md](changed.md).

| Target piece | Status | Where |
|---|---|---|
| `Language`, `RiskLevel`, `BloodPressure`, `SymptomSet`, `RiskAssessment` | ✅ done | `sakhi_domain.py` |
| `SakhiCallState` | ✅ done | `sakhi_domain.py` |
| `TriageEngine` | ✅ done | `sakhi_triage.py` |
| `PatientRepository` ABC + `MockSixtyDBRepository` | ✅ done | `sakhi_repository.py` |
| `ClinicalToolset` (constructor-injected deps) | ✅ done | `sakhi_tools.py` |
| `SakhiAgent` | ✅ done | `agent.py` |
| `AvatarBackend` ABC + 3 backends | ✅ done | `sakhi_avatar.py` |
| Remove global singletons | ✅ done | `SlotCache`, `BookingPrefetch` deleted |

---

## 5. What actually changed, per weakness

### 5.1 Global singletons — resolved

`slot_cache` and `booking_prefetch` are deleted outright. `turn_latency`
(`TurnLatencyAggregator`) remains a module global; it only accumulates timing
metrics, so cross-call mixing skews a dashboard number rather than corrupting
call state. Flagged, not fixed.

Everything new is scoped correctly:

- `SakhiCallState` — one per call, held in `AgentSession.userdata`.
- `ClinicalToolset` — holds only injected dependencies, no per-call mutable state, so sharing it is safe by construction.
- `MockSixtyDBRepository` — one per **process** (loaded in `prewarm_process`), which is correct for a shared datastore, and its mutations are guarded by an `asyncio.Lock`.
- `AvatarBackend` — one per call.

### 5.2 Persistence abstraction — resolved

Tools now depend on the `PatientRepository` ABC. Swapping the mock for real
SixtyDB means adding one subclass and changing one line in `prewarm_process`;
no tool body changes. That was the whole reason the seam exists.

### 5.3 Primitive obsession — resolved

| Was | Now |
|---|---|
| `language: str` re-parsed in each builder | `Language` enum carrying `sarvam_code`, `honorific`, `display_name` |
| `"NORMAL"` / `"HIGH"` / `"HIGH_RISK"` (two spellings for one state) | `RiskLevel` enum, ordered, with `requires_escalation` |
| two loose ints | `BloodPressure` — validates, rejects transposed and mis-transcribed readings |
| three positional bools | `SymptomSet` — keyword-only, accumulating |

### 5.4 Encapsulation breaches — resolved

`extract_vad_flags` is gone, and with it the mutation of the process-shared VAD
and the reach into `session._turn_detector._min_endpointing_delay`.
`DynamicVADController` — which was already doing this correctly through the
public `session.update_options()` API — is now the only thing that touches
endpointing.

### 5.5 Behaviour that should be a method — resolved

The pre-eclampsia matrix is now `TriageEngine._classify()`: a pure function with
62 tests behind it, reading exactly like the diagram in the architecture doc.
`SakhiAgent` enforces the outcome deterministically rather than trusting the
model to act on it.

### 5.6 The 570-line procedural `entrypoint()` — partially addressed

Still long, but materially thinner: provider construction, clinical tools,
persistence and the avatar have all moved behind interfaces it merely calls.
Fully decomposing it (a `CallSession` object owning metadata parsing, provider
assembly, event wiring and the supervision loop) is the obvious next
refactor — deliberately **not** attempted here, because it touches the one
code path that cannot be unit-tested without a live LiveKit room, and the
clinical correctness work had to land first.

---

## 6. Honest assessment

The infrastructure OOP was already good and is largely untouched — the Sarvam
wrappers, `EagerClauseTokenizer` and `DynamicVADController` were all well
designed, and rewriting them would have been churn.

The domain OOP was the real gap, and that is what this refactor built: there is
now a `Patient`, a `BloodPressure`, a `RiskAssessment`, a repository and a triage
engine, where before there were dicts, magic strings, and clinical rules written
as English prose inside a prompt string.

The remaining weak point is `entrypoint()`, named above and left deliberately.
