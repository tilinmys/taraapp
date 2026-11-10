# ಸಖಿ · Sakhi (Tara) — Maternal Health Voice Agent

[![Code: Apache-2.0](https://img.shields.io/badge/Code-Apache--2.0-blue.svg)](LICENSE)
[![Content: CC BY 4.0](https://img.shields.io/badge/Content-CC%20BY%204.0-lightgrey.svg)](LICENSE-CONTENT.md)
[![Digital Public Good](https://img.shields.io/badge/DPG-Candidate-green.svg)](https://digitalpublicgoods.net/)
[![SDG 3](https://img.shields.io/badge/SDG-3.1%20%26%203.2%20Good%20Health-orange.svg)](https://sdgs.un.org/goals/goal3)
[![DPDP Act 2023](https://img.shields.io/badge/Privacy-DPDP%20Act%202023%20aligned-success.svg)](#privacy--data-protection)

> **Digital Public Good notice:**  
> Sakhi (Tara) is open-source software and a candidate Digital Public Good designed to advance **UN Sustainable Development Goal 3 (Good Health and Well-Being)** by reducing preventable maternal and newborn complications in low-resource settings.
>
> **Clinical AI & medical disclaimer (following WHO guidance on AI for health):**  
> Sakhi is a **Clinical Decision Support System (CDSS)** engineered for trained community health workers (ASHAs). **Sakhi is not an autonomous medical practitioner, does not diagnose, and never prescribes medication or dosages.** When critical danger signs or elevated blood pressure are identified, the system immediately recommends clinical escalation to qualified obstetricians and district health facilities; an integrated SOS alert notifies nearest on-duty health workers and provides one-tap connection to emergency medical services (108).

A real-time, voice-first antenatal care assistant built for **frontline ASHA workers** in rural India (piloted in Bidar, Karnataka). Operating in **Kannada and Hindi**, Sakhi allows health workers to conduct standardized 24-question check-ins hands-free, records point-of-care vital readings, and applies deterministic clinical rules to detect pre-eclampsia early.

```
Frontline ASHA Worker (Spoken Kannada / Hindi)
                     │
                     ▼
  Open-Source Indic STT (bodhan-ai/indic-transcribe-flex)
                     │
                     ▼
          Clinical Decision Engine (Deterministic Rule Matrix)
                     │
         ┌───────────┴───────────┐
         ▼                       ▼
  Audio / Spoken Outcome     Immediate Specialist Tele-Triage
  (Local Speech Feedback)    (High-Risk Cases Bypassing PHC Delays)
```

---

## 1. Core Architecture & Open-Source Stack

Tara is composed of three interconnected open-source layers:

1. **Mobile Application (`app/`):** An offline-first **React Native / Expo SDK 58** cross-platform client with role-based portals for **ASHA Workers**, **Expectant Mothers**, and **Specialist Doctors**.
2. **Local Open-Source Speech-to-Text (`stt_service/`):** Self-hosted Indic speech recognition powered by the open-source **[bodhan-ai/indic-transcribe-flex](https://huggingface.co/bodhan-ai/indic-transcribe-flex)** model (1.2B parameters, supporting Kannada, Hindi, and English). Runs locally on GPU or CPU without paid third-party STT dependencies.
3. **Deterministic Clinical Decision Engine (`sakhi_triage.py`):** Pure, auditable clinical triage logic strictly mapped to Government of India (GoI) Antenatal Care protocols and ACOG guidelines with 100% deterministic reproducibility.
4. **Real-Time Telemedicine Infrastructure:** Low-latency WebRTC media pipelines powered by open-source **LiveKit** connecting frontline workers directly with remote hospital obstetricians.

---

## 2. Safety by Design: The Deterministic Rule

> **The language model collects the readings and delivers spoken guidance.**  
> **Deterministic code decides the clinical outcome.**

Clinical risk tiers are calculated strictly through programmatic rule execution (`sakhi_triage.py`), never generated probabilistically by a language model:

| Blood Pressure | Danger Symptoms | Outcome | Action |
|---|---|---|---|
| **≥140/90 mmHg** | Any danger sign | 🔴 **HIGH_RISK** | Immediate referral to District Hospital; connects specialist doctor |
| **≥140/90 mmHg** | None reported | 🟡 **MODERATE** | Schedule repeat blood pressure check within 48 hours |
| **Normal (<140/90)** | Any danger sign | 🟡 **OBSERVATION** | Log symptoms, clinical review, and close home monitoring |
| **Normal (<140/90)** | None reported | 🟢 **ROUTINE** | Confirm standard schedule for next ANC check-up |

* **Severe-Range BP (≥160/110 mmHg):** Escalates directly to `HIGH_RISK` regardless of symptom status.
* **Gestation < 20 Weeks:** Elevated BP flags potential chronic hypertension; clinical thresholds never down-triage a patient.

---

## 3. Role-Based Applications (`app/`)

The mobile client (`app/`) provides three specialized personas:

* **ASHA Worker (`/asha`):**
  * Standardized 24-question conversational ANC intake with real-time Indic voice capture.
  * Point-of-care vital inputs (automated digital BP monitor, urine protein dipstick results).
  * Automated 1-page A4 PDF referral report generation with risk classification and signature lines.
* **Expectant Mother (`/mother`):**
  * Daily care checklist, baby development milestones, and localized audio playback of doctor's advice.
  * Direct Care Circle (calling assigned village ASHA, booking PHC doctor slots).
  * One-tap emergency SOS beacon alerting nearest on-duty ASHAs and ambulance (108).
* **Specialist Doctor (`/doctor`):**
  * Tele-triage queue prioritizing high-risk pre-eclampsia cases.
  * Appointment slot manager and direct digital advice handoff to ASHA and mother.

---

## 4. Quick Start: Running Locally

### Prerequisites

* **Node.js** (v18 or v20+)
* **Python** (3.10.x recommended)
* **ffmpeg** installed and available on system `PATH`
* *(Optional for GPU acceleration)*: NVIDIA GPU with CUDA support (≥ 4 GB VRAM)

---

### Step 1: Run the Mobile Application (`app/`)

```bash
# Navigate to app directory
cd app

# Install dependencies
npm install

# Option A: Start development tunnel (Recommended for physical devices via Expo Go)
npm run tunnel

# Option B: Local Wi-Fi network server
npm start

# Option C: Web browser preview
npm run web
```

#### Run Automated Test Suites
```bash
# Run all 291 unit, integration, and clinical flow tests
npm test

# Verify TypeScript compilation (0 errors)
npm run typecheck

# Verify code formatting and lint standards (0 errors)
npm run lint
```

---

### Step 2: Set Up Local Speech-to-Text (`stt_service/`)

The speech recognition engine runs the open-source `bodhan-ai/indic-transcribe-flex` model locally on your machine.

```bash
# Create dedicated Python 3.10 environment
py -3.10 -m venv stt_service\.venv

# Activate environment (Windows)
stt_service\.venv\Scripts\activate
# (Linux/macOS: source stt_service/.venv/bin/activate)

# Install PyTorch with CUDA (or CPU version) and dependencies
pip install -r stt_service/requirements.txt

# Download model weights from Hugging Face (~4.9 GB)
hf download bodhan-ai/indic-transcribe-flex

# Start the local STT service (default: port 8795)
# Windows:
stt_service\run.cmd
# Linux/macOS:
python -m uvicorn stt_service.app:app --host 127.0.0.1 --port 8795
```

Verify service status:
```bash
python stt_service/smoke_test.py
# Output: OK
```

---

### Step 3: Run the Voice Gateway

```bash
# In the repository root, install backend requirements
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Set INDIC_STT_URL=http://127.0.0.1:8795 in your .env file

# Start the local gateway service (port 8787)
python scripts/sakhi_gateway_dev.py
```

---

## 5. Privacy & Data Protection (DPDP Act 2023 Aligned)

* **Explicit Digital Consent:** Multilingual digital consent is recorded prior to beginning clinical check-ins; pregnant mothers may decline or request data deletion.
* **On-Device Data Encryption:** SQLite local caching on frontline tablets uses AES-256 encryption.
* **PII Redaction:** Sensitive personal identifiers (phone numbers, national IDs) are masked prior to database synchronization.
* **Zero Audio Retention:** Spoken audio streams are transcribed in memory; raw voice audio is not persisted to disk.

---

## 6. Verification & Automated Testing

| Layer | Framework | Coverage | Command |
|---|---|---|---|
| **Mobile Client & Flows** | Jest / React Native Testing Library | 291 tests across 18 suites | `npm --prefix app test` |
| **Static Typing** | TypeScript (strict mode) | Entire `app/src` codebase | `npm --prefix app run typecheck` |
| **Code Standards** | ESLint / Expo Lint | Zero warnings / errors | `npm --prefix app run lint` |
| **Clinical Decision Logic** | Pytest | 62 clinical triage edge-cases | `python -m pytest tests/` |

---

## 7. Open-Source Licences

* **Software Codebase:** [Apache License 2.0](LICENSE) — Permissive open-source license with explicit patent protection for public health deployments.
* **Documentation & Clinical Content:** [Creative Commons Attribution 4.0 International (CC BY 4.0)](LICENSE-CONTENT.md) — Covers clinical questions, triage guidelines, and ASHA training syllabus.
