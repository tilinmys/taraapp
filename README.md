# ಸಖಿ · Sakhi — Maternal Health Voice Agent

[![Code: Apache-2.0](https://img.shields.io/badge/Code-Apache--2.0-blue.svg)](LICENSE)
[![Content: CC BY 4.0](https://img.shields.io/badge/Content-CC%20BY%204.0-lightgrey.svg)](LICENSE-CONTENT.md)
[![Digital Public Good](https://img.shields.io/badge/DPG-Candidate-green.svg)](https://digitalpublicgoods.net/)
[![SDG 3](https://img.shields.io/badge/SDG-3.1%20%26%203.2%20Good%20Health-orange.svg)](https://sdgs.un.org/goals/goal3)
[![DPDP Act 2023](https://img.shields.io/badge/Privacy-DPDP%20Act%202023%20aligned-success.svg)](#privacy--data-protection)

> **Digital Public Good notice:**
> Sakhi (Tara) is developed as open-source software, a candidate Digital Public Good, to support **UN Sustainable Development Goal 3 (Good Health and Well-Being)** by reducing maternal and newborn deaths in low-resource settings. Pre-eclampsia, the danger sign Sakhi screens for first, is a leading cause of preterm birth, stillbirth and newborn death: catching it at the doorstep protects the **baby** as much as the mother.
>
> **Clinical AI & medical disclaimer (following WHO guidance on AI for health):**
> Sakhi is **clinical decision support** built for trained community health workers (ASHAs). **Sakhi is not a medical practitioner, does not diagnose, and never names a medicine or a dose.** When a danger sign is reported, Sakhi stops the questions and refers the mother to a doctor or the nearest facility; the mother's SOS alerts her nearest ASHAs and offers a direct call to 108.

A real-time, Kannada-first voice assistant that helps **ASHA workers** screen
pregnant mothers for **pre-eclampsia** in rural Bidar, Karnataka.

An ASHA worker calls Sakhi from a village, reads out a blood-pressure reading,
answers three danger-symptom questions, and gets a decision — in Kannada, in
about a second and a half. If the reading is dangerous, a specialist is
connected automatically.

```
ASHA worker speaks Kannada
        │
        ▼
  Sarvam STT (kn-IN)  ──►  Groq LLM  ──►  clinical tools  ──►  TriageEngine
        │                                                            │
        │                                                    RiskAssessment
        ▼                                                            │
  Sarvam TTS (Kannada)  ◄────── what to say ◄─────────────────────────┘
        │
        ▼
  ASHA worker hears the answer   (+ optional Tavus video avatar)
```

---

## Quick Start: Tara Mobile Application (`app/`)

The primary field application is an offline-first **React Native / Expo SDK 58** app supporting three role-based personas:
* **ASHA Worker (`/asha`):** visit-based voice check-in with Sakhi (MCP card visits 1-3), review of every answer, measurements and lab reports, BP re-check, and A4 PDF referral report.
* **Expectant Mother (`/mother`):** Daily care checklist, localized audio advice, doctor bookings, and SOS emergency beacon.
* **Specialist Doctor (`/doctor`):** High-risk tele-triage dashboard, appointment slot manager, and direct clinical advice handoff.

```bash
cd app
npm install

# Development server
npm run tunnel     # Cloudflare quick tunnel for real Android/iOS devices (Expo Go)
npm start          # Local development server
npm run web        # Browser preview

# Quality gates
npm test           # Jest
npm run typecheck  # TypeScript (0 errors)
npm run lint       # Expo lint (0 warnings)
```

## Quick Start: local speech-to-text (open-source model)

The app's speech-to-text runs **on your own GPU** with the open-source
[bodhan-ai/indic-transcribe-flex](https://huggingface.co/bodhan-ai/indic-transcribe-flex) model
(Kannada, Hindi, English). No paid STT provider is used. The model weights are **not** in this
repository (4.9 GB, gated licence): you download them once from Hugging Face.

**Full, step-by-step guide (people and AI agents): [`stt_service/README.md`](stt_service/README.md).**
The order matters:

1. Prerequisites: Python 3.10, an NVIDIA GPU (>= 4 GB), ffmpeg.
2. `py -3.10 -m venv stt_service\.venv`, then install CUDA PyTorch and `stt_service/requirements.txt`.
3. **A person** accepts the model licence on Hugging Face, runs `hf auth login` and `hf download bodhan-ai/indic-transcribe-flex`.
4. `stt_service\run.cmd` starts the service on `http://127.0.0.1:8795`; `stt_service\smoke_test.py` must print `OK`.
5. Add `INDIC_STT_URL=http://127.0.0.1:8795` to `.env`, then `python scripts\sakhi_gateway_dev.py` (gateway, port 8787).
6. `cd app && npm run tunnel`, then scan the QR code with Expo Go.

> Built with Indic-Transcribe-Flex from Bodhan AI / AI4Bharat.

---

## 1. The one design rule

> **The LLM collects the readings and speaks the outcome.
> The code decides what the outcome is.**

The pre-eclampsia decision is made by [`sakhi_triage.py`](sakhi_triage.py) — a
pure function with 62 tests behind it — never by the language model. The model's
job is to hear numbers, call a tool, and read the returned instruction aloud.

This matters because the previous design had the entire triage matrix written as
**prose inside a prompt**. A clinical safety rule that depends on a model
remembering an instruction is not a safety rule.

### The triage matrix, as implemented

| Blood pressure | Danger symptoms | Outcome | Action |
|---|---|---|---|
| ≥140/90 | any | 🔴 `HIGH_RISK` | Refer to Bidar District Hospital, connect specialist **now** |
| ≥140/90 | none | 🟡 `MODERATE` | Re-check within 48 hours |
| normal | any | 🟡 `OBSERVATION` | Log and monitor |
| normal | none | 🟢 `ROUTINE` | Confirm next ANC visit |

Two additions beyond the base matrix:

- **Severe-range BP (≥160/110) escalates on the reading alone**, per standard
  obstetric practice. Toggle with `TRIAGE_ESCALATE_ON_SEVERE_BP`.
- **Under 20 weeks' gestation**, raised BP is flagged as possibly *chronic
  hypertension* rather than pre-eclampsia. It does **not** downgrade the risk —
  a screening tool must never under-triage.

### The safety net

If triage returns `HIGH_RISK`, a specialist is connected **whether or not the
model calls the tool**. `SakhiAgent` injects an urgent directive before
generation, and if the turn still ends without the call, it invokes the tool
directly. A red alert cannot depend on an LLM following a prompt.

---

## 2. Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  Next.js UI (Kannada-first, ASHA-worker console)             │
│  one big button · live status · risk banner · log rail       │
└───────────────┬──────────────────────────────────────────────┘
                │ WebRTC (LiveKit Cloud, India South)
┌───────────────▼──────────────────────────────────────────────┐
│  agent.py — LiveKit worker                                   │
│                                                              │
│   SakhiAgent(Agent)                                          │
│     ├── on_user_turn_completed  → guardrails (input gate)    │
│     ├── llm_node                → per-turn state injection    │
│     └── tts_node                → clause chunking + safety net│
│                                                              │
│   DynamicVADController          → adaptive endpointing        │
└───┬──────────┬──────────┬───────────┬────────────┬───────────┘
    │          │          │           │            │
    ▼          ▼          ▼           ▼            ▼
 Sarvam     Groq →     Clinical    Triage      Avatar seam
  STT       OpenAI     Toolset     Engine     (none/tavus/bey)
 (kn-IN)   fallback       │           │
                          ▼           │
                  PatientRepository ◄──┘
                     (SixtyDB)
```

### Modules

| Module | Responsibility |
|---|---|
| [`agent.py`](agent.py) | LiveKit worker, `SakhiAgent`, provider construction, session lifecycle |
| [`sakhi_domain.py`](sakhi_domain.py) | Value objects — `Language`, `RiskLevel`, `BloodPressure`, `SymptomSet`, `SakhiCallState` |
| [`sakhi_triage.py`](sakhi_triage.py) | `TriageEngine` — the pre-eclampsia matrix, pure and testable |
| [`sakhi_repository.py`](sakhi_repository.py) | `PatientRepository` ABC + async mock SixtyDB (in-memory reads, write-behind) |
| [`sakhi_tools.py`](sakhi_tools.py) | `ClinicalToolset` — the five function tools the model may call |
| [`sakhi_avatar.py`](sakhi_avatar.py) | `AvatarBackend` ABC — `none` / `tavus` / `bey` / `did` |
| [`sarvam_wrappers.py`](sarvam_wrappers.py) | Sarvam STT + TTS providers |
| [`guardrails.py`](guardrails.py) | Input/output safety gates (emergency, self-harm, medical advice, abuse) |
| [`frontend/`](frontend/src/app) | Next.js ASHA console + token/dispatch API |

### The clinical tools

| Tool | What it does |
|---|---|
| `search_patient` | Look up a mother by name or phone |
| `register_mother` | Create a SixtyDB record |
| `record_bp` | Validate → triage → persist → return the decision |
| `record_symptoms` | Merge symptoms, **re-triage** (a symptom can flip MODERATE → HIGH_RISK) |
| `connect_smfmi_specialist` | Idempotent escalation + audit entry |

Every tool returns a JSON block containing the decided `risk_level`, the
`reasons`, and an `instruction` telling the model what to say.

---

## 3. How it was refined
It is a maternal-health co-pilot. Full log in
[`changed.md`](changed.md); OOP analysis in [`oops.md`](oops.md).

### 3.1 The starting point

`agent.py` was 4,239 lines of clinic receptionist — doctor lookup, slot search,
appointment booking, SQLite — with **four Sakhi tools bolted on at line 3413**.
Those four were the only tools registered. The twelve booking tools above them
were defined, decorated, and **never registered**: dead weight that still pulled
`db_helper`, `SlotCache` and a background SQLite refresh loop into every call.

### 3.2 The defects that mattered

| Defect | Impact |
|---|---|
| `build_llm()` built the whole Groq chain, then `return openai_fallback` unconditionally | Every Groq provider discarded. Dashboard said Groq; 100% of turns went to gpt-4o-mini |
| Sarvam **WebSocket TTS never returns audio** | The 11-second dead air |
| `record_bp` persisted nothing and computed no risk | The core clinical feature was a stub |
| Triage matrix existed only as prompt prose | No deterministic safety path |
| `extract_vad_flags` mutated the **process-shared** VAD | One caller's turn retuned turn-taking for every concurrent call |
| Farewell regex was English-only | Auto-hangup could never fire on a Kannada goodbye |
| Barge-in set to 5.0s / 15 words | Interruption effectively **disabled** |
| Sample rate declared 24 kHz; API returns 22.05 kHz | Every reply played ~9% fast and pitched up |
| `bulbul:v1` requested | Model does not exist — the API rejects it |

### 3.3 Measurements that drove decisions

Probed against the live Sarvam API rather than assumed:

```
WebSocket  bulbul:v2 / v3   →  TimeoutError, no audio in 12s   ← the dead air

REST       bulbul:v2 manisha   clause  438ms   sentence   779ms
           bulbul:v2 vidya     clause  451ms   sentence   764ms
           bulbul:v3 pooja     clause 1012ms   sentence  2655ms
```

Result: TTS switched to REST with **clause buffering**, model pinned to
`bulbul:v2`, sample rate corrected to 22050 Hz, and a 3-second timeout so a
failure fails *fast* into the OpenAI fallback instead of hanging.

### 3.4 Latency budget

| Stage | Now |
|---|---|
| Endpointing | 0.30 s |
| Sarvam STT | ~0.30 s |
| LLM TTFT (Groq primary) | ~0.40 s |
| TTS first clause | ~0.44 s |
| **Total first response** | **≈ 1.4 s** |

Prompt prefill went **484 → ~138 tokens per generation** (109 system + 29 state).

> **Note:** a sub-150 ms TTFT is not physically reachable — that is below the
> India→US network round-trip before a single token is generated. ~1.4 s
> end-to-end is inside the 1.25–1.9 s design budget.

### 3.5 Object model

The infrastructure OOP was already sound (provider wrappers, agent subclass).
The **domain** OOP was the gap — clinical subject matter lived as loose dicts,
magic strings, and prompt prose.

| Was | Now |
|---|---|
| `language: str` re-parsed in every builder | `Language` enum carrying `sarvam_code`, `honorific` |
| `"NORMAL"` / `"HIGH"` / `"HIGH_RISK"` (two spellings, one concept) | `RiskLevel` enum, ordered, `requires_escalation` |
| Two loose ints | `BloodPressure` — validates, rejects transposed/mis-transcribed readings |
| Three positional bools | `SymptomSet` — keyword-only, accumulating |
| `read_db()` / `write_db()` called directly from tools | `PatientRepository` ABC — swap mock → real SixtyDB with one subclass |
| Global singletons mutated across concurrent calls | Per-call state in `AgentSession.userdata` |

`agent.py`: **4,239 → 2,999 lines**. Every removal left a comment naming what
was there and why it went.

---

## 4. Running it

### Prerequisites

Python 3.10+, Node 18+, and keys for LiveKit, Sarvam, and Groq/OpenAI.

```bash
pip install -r requirements.txt
cd frontend && npm install && cd ..
cp .env.example .env      # then fill in your keys
```

### Start

```bash
python agent.py dev
```

```bash
npm --prefix frontend run dev
```

Open http://localhost:3000 and press **ಮಾತನಾಡಿ**.

### Tests

```bash
python -m pytest tests/ -q
```

**227 tests** — clinical core, agent wiring, guardrails, adaptive VAD, TTS payloads.

---

## 5. The ASHA-worker interface

Built for one user: a health worker in a Bidar village, cheap Android phone,
outdoors, Kannada-first, variable literacy, patchy 3G.

- **Kannada primary, English subtitle** — never English-only
- **One obvious action** — a single 78 px button, `ಮಾತನಾಡಿ`
- **State is icon + colour + word**, never colour alone — 👂 `ಕೇಳುತ್ತಿದ್ದೇನೆ`, 🗣️ `ಮಾತನಾಡುತ್ತಿದ್ದೇನೆ`
- **Touch targets ≥64 px**, warm off-white background (greys wash out in sunlight)
- **Risk banner** driven by the engine's decision, not the UI's guess
- **Log rail** on the left — collapsible, quiet, for supervisors and debugging

---

## 6. Avatar (optional)

Video is off by default (`SAKHI_AVATAR=none`). Audio is the product.

Tavus runs in **echo mode** — it receives Sakhi's already-synthesised Sarvam
audio and returns lip-synced video. It does **not** think or speak:

```
Sarvam Kannada TTS ──► Tavus (echo) ──► lip-synced video
Groq LLM + TriageEngine ──► untouched; Tavus never sees them
```

Measured cost: **~6 s to join, ~4.6 s playback latency**. Enable only on good
connectivity. An avatar failure can never end a call — every backend degrades to
audio-only.

---

## 7. Safety boundaries

- Sakhi is **decision support for a health worker**, never a practitioner. She
  does not diagnose and never names a medicine or dose.
- Emergency, self-harm, medical-advice and abuse turns are caught by
  [`guardrails.py`](guardrails.py) **before the LLM sees them** and answered
  with fixed approved scripts.
- Implausible BP readings (STT hearing "ninety" as "nine hundred", transposed
  numbers) are rejected and re-asked, never recorded.
- Risk status **ratchets upward only** — a later normal reading cannot silently
  clear a red alert already acted on.
- Every escalation writes an immutable audit entry with the reasons behind it.

---

## Privacy & data protection

Aligned with the India DPDP Act 2023; the full policy, and what is not built
yet, is in [`SECURITY.md`](SECURITY.md#data-protection-principles).

- Turn logs and pipeline events carry lengths and states, never the mother's words.
- Aadhaar, PAN, phone and bank numbers are masked before a transcript is stored.
- Patient-level dashboard routes need a bearer token; the speech gateway refuses
  requests without its token.
- Consent first: the app reads a notice to the mother in her language (what is
  collected, who sees it, which speech services process her voice, that she may
  refuse) and the check-in cannot start until the ASHA records that she agreed.
  The server-side record is the append-only table in
  [`deploy/supabase/20261102_dpdp_consent.sql`](deploy/supabase/20261102_dpdp_consent.sql);
  syncing the app's consent to it, and an erasure workflow, are not built yet.
- Public, anonymised, real-time counters at `/status/public/metrics`
  (counts only; any count from 1 to 4 is shown as "<5").

### Third-party services and platform independence

Sakhi uses third-party speech, LLM, call and hosting services, each chosen by
configuration and each with an open alternative; the app itself calls none of
them directly and holds no provider key. The images made with Canva AI are
bundled files: Canva receives no data at run time. Every service, the data it
receives and its open alternative: [`docs/THIRD_PARTY_SERVICES.md`](docs/THIRD_PARTY_SERVICES.md).

Report a vulnerability privately: see [`SECURITY.md`](SECURITY.md).

---

## 8. Status and known limits

| Area | State |
|---|---|
| Kannada / Hindi voice pipeline | Working |
| Clinical triage + persistence | Working, 227 tests |
| Deterministic escalation | Working |
| ASHA console | Working, mobile-verified |
| Tavus avatar | Working, off by default (latency) |
| D-ID backend | Stub — refuses to start rather than pretend |
| Real SixtyDB | Not wired; mock implements the same interface |

Open items:

1. `GROQ_LLM_MODEL` is `qwen/qwen3-32b`, a reasoning model that can spend the
   whole token budget on hidden reasoning and return nothing. The empty-completion
   fallback catches it at the cost of a round-trip. Consider `llama-3.3-70b-versatile`.
2. `/health` reads an append-only log, so it can report `ready` from a worker
   that has died — and `api/token` gates on it.
3. Post-call reports still write to the legacy clinic SQLite, not SixtyDB.
4. Field latency has not been measured on a real 3G link in Bidar.

---

## Documentation

| Document | Contents |
|---|---|
| [`changed.md`](changed.md) | Full refactor log — every change, why, and what it affected |
| [`oops.md`](oops.md) | Object-oriented design audit and target model |
| [`ARCHITECTURE.md`](ARCHITECTURE.md) | Subsystem detail and call flow |
