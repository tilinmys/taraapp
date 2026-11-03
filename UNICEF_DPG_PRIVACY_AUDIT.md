# UNICEF Venture Fund & Digital Public Goods (DPG) Privacy & Security Audit
**Project Name:** Sakhi Maternal Health Voice Agent (Tara)  
**Evaluator:** Principal Security & Systems Architect (Healthcare AI, Real-Time Voice & Digital Public Goods)  
**Target Standards:** UNICEF Venture Fund Investment Readiness, DPGA Standard (v1.3), India Digital Personal Data Protection Act (DPDP Act 2023 & 2025 Rules), WHO Guidelines on AI for Health & Digital Interventions, ABDM & HL7 FHIR R4 Standards.  
**Audit Date:** 2026-10-27  
**Analysis Mode:** Comprehensive Read-Only Static Code Analysis & Threat Modeling (Zero Code Modifications)  

---

## 1. Executive Readiness Score (0–100%)

| Audit Pillar | Score | Weight | Weighted Score | Status | Primary Compliance Mandates |
| :--- | :---: | :---: | :---: | :---: | :--- |
| **Pillar 1: Open-Source & DPG Readiness** | **35%** | 20% | 7.0 / 20 | 🔴 Critical Non-Compliance | DPGA Indicators 1, 2, 3, 6; UNICEF Venture Fund FOSS Criteria |
| **Pillar 2: Data Privacy & DPDP / GDPR Compliance** | **22%** | 25% | 5.5 / 25 | 🔴 Immediate Legal Liability | India DPDP Act 2023 §§ 6, 8, 12, 16; GDPR Arts. 5, 6, 17 |
| **Pillar 3: Database Security & Cryptographic Hygiene** | **30%** | 20% | 6.0 / 20 | 🔴 High Vulnerability | ISO/IEC 27001 A.9/A.10, OWASP API Top 10 (Broken Auth/IDOR) |
| **Pillar 4: Clinical Algorithm Safety & AI Guardrails** | **58%** | 20% | 11.6 / 20 | 🟡 Moderate Safety Deficit | WHO Safe AI for Health §4, IEC 62304 Medical Device Software |
| **Pillar 5: Interoperability & Low-Resource Resilience** | **32%** | 15% | 4.8 / 15 | 🔴 Interoperability Gap | ABDM M1/M2/M3, HL7 FHIR R4, DHIS2, Offline-First Standards |
| **OVERALL COMPLIANCE SCORE** | **34.9%** | **100%** | **34.9 / 100** | 🔴 **REJECTED (Pre-Submission)** | **Ineligible for Grant Funding / DPG Certification** |

### Executive Summary & System Verdict
Sakhi (Tara) demonstrates exceptional low-latency real-time voice orchestration, advanced bilingual turn-taking heuristics, and an exemplary mathematical rule engine for antenatal triage in [`sakhi_triage.py`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/sakhi_triage.py). 

However, from a regulatory, data protection, and open-source licensing perspective, **the repository fails core compliance gates for UNICEF Venture Fund investment and DPGA endorsement**. The application cannot be deployed or certified in its current state due to five catastrophic blockers:
1. **Live AWS IAM Credentials Stored on Disk:** Plaintext active credentials for `sakhi-egress-uploader` (`AKIASEU6STPHPWJAAIXD`) are stored in [`deploy/aws/.deploy-state/egress-access-key.json`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/deploy/aws/.deploy-state/egress-access-key.json#L1-L9).
2. **Systemic PII Leakage Prior to Sanitization:** Caller speech and names are logged into standard output, written to persistent pipeline event logs, and broadcast over unauthenticated Server-Sent Events (SSE) before guardrails are executed.
3. **Absence of DPDP-Compliant Digital Consent:** Health intake begins immediately at question `q01` without verifiable, timestamped, multilingual digital consent.
4. **Unauthenticated Health API Endpoints & IDOR:** The telemetry and status server exposes patient medical records, risk assessments, and live session turns without authentication or access control.
5. **English Fallback in Clinical Emergency:** Guardrails trip an English-language clinic receptionist script when a Kannada-speaking mother triggers a medication or safety rule.

---

## 2. Pillar-by-Pillar Detailed Findings

```
                                  REPOSITORY COMPLIANCE MATRIX
┌─────────────────────────────────┬───────┬────────────────────────────────────────────────────────┐
│ Pillar                          │ Score │ Key Vulnerabilities / Gaps                             │
├─────────────────────────────────┼───────┼────────────────────────────────────────────────────────┤
│ 1. Open-Source & DPG Readiness  │  35%  │ No root license, vendor coupling, no docker-compose    │
│ 2. Data Privacy & DPDP Act      │  22%  │ Pre-redaction logs, missing consent, no erasure API    │
│ 3. Database Security & Crypto   │  30%  │ IAM key on disk, unencrypted SQLite, unauth endpoints  │
│ 4. Clinical Safety & Guardrails │  58%  │ English trip text, validate_output unused in runtime   │
│ 5. Interoperability & Low-Res   │  32%  │ 0 FHIR/ABDM/DHIS2 schemas, drops drop state after 20s  │
└─────────────────────────────────┴───────┴────────────────────────────────────────────────────────┘
```

---

### PILLAR 1: Open-Source & Digital Public Goods (DPG) Readiness
*Evaluated against DPGA Standard v1.3 (Indicators 1–6) and UNICEF Venture Fund Open Source Policy.*

#### 1.1 License & Repo Hygiene
* **Missing Root OSI License:** The repository root has no `LICENSE` file. The only license file present is [`mobile/LICENSE`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/mobile/LICENSE#L1-L21), which is the unedited upstream boilerplate from Expo (`Copyright (c) 2015-present 650 Industries, Inc.`). Neither [`package.json`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/package.json) nor sub-package manifests declare an SPDX identifier (all set to `"license": null` or undeclared).
* **Missing Repository Governance:** Neither [`CONTRIBUTING.md`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/CONTRIBUTING.md) nor [`CODE_OF_CONDUCT.md`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/CODE_OF_CONDUCT.md) exists. Open-source maintainership, security vulnerability disclosure channels, and community guidelines are absent.
* *Standards Violated:* DPGA Indicator 1 (Open License), DPGA Indicator 2 (Clear Ownership), UNICEF Open Source Policy §2.1.

#### 1.2 Dependency Audit
* Manifests inspected: [`requirements.txt`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/requirements.txt), [`package.json`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/package.json), [`app/package.json`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/app/package.json), [`frontend/package.json`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/frontend/package.json), [`mobile/package.json`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/mobile/package.json).
* The primary Python dependencies (`livekit-agents`, `boto3`, `aiohttp`, `websockets`) use Apache-2.0, MIT, or BSD-3.
* **Proprietary Binary Wrapper:** [`app/package.json:58`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/app/package.json#L58) includes `"cloudflared": "^0.7.3"`. This package fetches proprietary compiled Cloudflare tunnel binaries governed by Cloudflare’s commercial terms, violating pure open-source redistribution requirements under DPG criteria.
* **LGPL Linking Consideration:** [`requirements.txt:45`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/requirements.txt#L45) pins `psycopg[binary,pool]>=3.2`. Psycopg 3 binary packages carry GNU LGPL v3 with C library linkings. While acceptable for dynamically linked backends, source distribution documentation must clearly isolate binary distributions.

#### 1.3 Vendor Lock-in & Modularity
* **Monolithic Tightly Coupled Dispatcher:** In [`agent.py`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/agent.py) (7,528 lines), provider instantiations are hardcoded:
  * [`agent.py:1746-1850`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/agent.py#L1746-L1850): `build_stt()` directly instantiates `deepgram.STT`, `SarvamSTT`, `SarvamBatchSTT`, `smallestai.STT`, and `gnani.STT`.
  * [`agent.py:1973-2130`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/agent.py#L1973-L2130): `build_llm()` directly constructs `openai.LLM` objects pointing to Groq and OpenRouter URLs.
  * [`agent.py:2657-2750`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/agent.py#L2657-L2750): `build_tts()` couples `VakyamTTS`, `SarvamTTS`, and `GnaniTTS`.
* There is no abstract `ProviderAdapter` or dependency injection layer. Swapping an STT or TTS engine for an on-premise local model (e.g., Whisper-Indic, Coqui, Piper, vLLM) requires editing thousands of lines of orchestration logic.

#### 1.4 Zero-Cost Local Bootability
* **Missing Containerization:** There is no `Dockerfile` or `docker-compose.yml` in the repository.
* **Hard Crash on Missing Proprietary Keys:** [`agent.py:1987-1989`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/agent.py#L1987-L1989) explicitly executes:
  ```python
  if not openrouter_keys and not groq_keys and not openai_key:
      raise RuntimeError("No LLM API key configured. Set OPENROUTER_API_KEY, GROQ_API_KEY, or OPENAI_API_KEY.")
  ```
* A peer reviewer, UNICEF evaluator, or rural hospital IT team cannot spin up this system in an offline sandbox without purchasing commercial subscriptions to US and Indian SaaS AI providers.

---

### PILLAR 2: Data Privacy & DPDP Act / GDPR Code Compliance
*Evaluated against India Digital Personal Data Protection Act (DPDP Act 2023 & 2025 Rules), GDPR, and WHO Digital Health Data Standards.*

#### 2.1 PII Leakage in Logs & Event Streams
* **Ingress Text Logged Unredacted:** In [`agent.py:4700-4708`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/agent.py#L4700-L4708), every finalized user utterance is logged at `INFO` level:
  ```python
  logger.info("TABLET_AUDIO_INGRESS: text='%s' duration=%.2f", raw_speech, ...)
  ```
* **Debug Leakage:** In [`agent.py:4732`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/agent.py#L4732):
  ```python
  logger.debug("[SAKHI_TURN_GATE] %r %s", raw_speech, _gates)
  ```
* **Multi-Sink Broadcast of Unsanitized PII:** In [`agent.py:4738-4739`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/agent.py#L4738-L4739), `raw_transcript=raw_speech` is passed into `pipeline_event()`. In [`agent.py:204-228`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/agent.py#L204-L228), `pipeline_event()`:
  1. Writes unredacted speech to disk in `logs/pipeline_events.jsonl`.
  2. Submits unredacted speech to the database table `pipeline_events` via `telemetry.submit()`.
  3. Broadcasts the raw event via `_broadcast_sse()` to all active SSE subscribers.
  4. Emits `log_method("[PIPELINE] ... %s", details)` directly to server logs.
* **Third-Party Provider Debug Log:** In [`sarvam_wrappers.py:750`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/sarvam_wrappers.py#L750):
  ```python
  logger.debug("Sarvam STT received: %s", resp)
  ```
  This logs the full raw JSON payload returned from Sarvam's WebSocket, including raw ASR transcriptions before masking.

#### 2.2 Flawed Order-of-Operations in Redaction & Incomplete Entity Masking
* **Execution Order Inversion:** In [`agent.py`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/agent.py), logging happens at lines 4700, 4732, and 4738. Guardrail sanitization ([`agent.py:5212`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/agent.py#L5212)) is delayed until 500 lines later in the turn cycle.
* **Bypass & Fail-Open Vulnerabilities:**
  * [`agent.py:5202`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/agent.py#L5202): Setting `VOICE_GUARDRAILS_ENABLED=false` completely skips sanitization.
  * [`agent.py:5215-5217`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/agent.py#L5215-L5217):
    ```python
    except Exception:
        logger.warning("Guardrail gate failed; turn continues normally", exc_info=True)
        return
    ```
    If an unexpected error occurs during regex matching or classification, the exception is swallowed and the raw, unredacted transcript is passed straight to the LLM.
* **Narrow PII Definition (Missing Names & Demographics):** In [`guardrails.py:324-333`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/guardrails.py#L324-L333), `_PII_PATTERNS` only checks for Aadhaar, PAN, phone number, and bank accounts. **Patient names, village names, taluk/district names, and ABHA numbers are never redacted**. Spoken inputs such as *"Mother's name is Renuka from Aurad village"* remain unmasked throughout logging and third-party transmission.

#### 2.3 Cross-Border Data Transfers & Missing Zero Data Retention (ZDR)
* **Unrestricted Egress to US Cloud APIs:** In [`agent.py:2050-2125`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/agent.py#L2050-L2125), clinical dialogues and patient histories are routed to Groq (`api.groq.com`), OpenRouter (`openrouter.ai`), and OpenAI (`api.openai.com`) in US data centers.
* **No ZDR / Privacy Opt-Out Flags:** None of the `openai.LLM()` client initializations pass privacy opt-out parameters, zero-data-retention headers (`X-Title`, `HTTP-Referer` data protection flags), or verified HIPAA BAA/DPA identifiers.
* *Standards Violated:* India DPDP Act 2023 § 16 (Cross-border data transfers), DPDP Rules 2025, GDPR Art. 44–49.

#### 2.4 Consent Enforcement & Right to Erasure
* **Absence of Pre-Screening Consent Gate:** In [`sakhi_intake.py:86-130`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/sakhi_intake.py#L86-L130), questionnaire stages run from `q01` (Basic Demographic Intake) to `q17` (Close). There is no "Stage 0: Informed Consent" question. The system does not record:
  * Timestamp of consent.
  * Notice version presented.
  * Spoken audio consent recording artifact.
  * Verified ASHA worker authorization.
* **No Mechanism for Right to Erasure (DPDP § 12 / GDPR Art. 17):**
  * The codebase contains **zero deletion endpoints** (`do_DELETE` does not exist in [`status_server.py`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/status_server.py)).
  * In [`sakhi_schema.py:149-150`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/sakhi_schema.py#L149-L150), records are declared permanent: `Immutable clinical log. Readings are never updated or deleted`. While medical records require an audit trail, identifiable personal data must be anonymizable upon consent withdrawal.
  * There are no automated cron jobs or TTL scripts to prune old call audio buffers, temporary transcripts, or session turns.

---

### PILLAR 3: Database Security & Cryptographic Hygiene
*Evaluated against ISO/IEC 27001, OWASP API Top 10, and Health Data Privacy Guidelines.*

#### 3.1 PII Separation & Lack of Pseudonymization
* **Co-Mingled Clinical and Demographic Data:** In [`sakhi_schema.py:134-145`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/sakhi_schema.py#L134-L145), the `mothers` table stores direct personal identifiers side-by-side with sensitive personal health data (SPDI):
  ```sql
  CREATE TABLE IF NOT EXISTS mothers (
      patient_id        TEXT PRIMARY KEY,
      name              TEXT NOT NULL,
      age               INTEGER,
      village           TEXT,
      phone             TEXT,
      gestational_weeks INTEGER,
      risk_status       TEXT NOT NULL DEFAULT 'ROUTINE',
      -- 14 clinical symptoms (bleeding, convulsions, etc.) inlined here
  );
  ```
* **Plaintext Indexes on Direct Identifiers:** In [`sakhi_schema.py:259-260`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/sakhi_schema.py#L259-L260):
  ```sql
  CREATE INDEX IF NOT EXISTS idx_mothers_phone ON mothers(phone);
  CREATE INDEX IF NOT EXISTS idx_mothers_name ON mothers(name);
  ```
  Direct phone numbers and names are indexed in cleartext without salted hashing (HMAC-SHA256).
* **Zero Field-Level Encryption:** Neither [`sakhi_sql_repository.py`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/sakhi_sql_repository.py) nor [`db_helper.py`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/db_helper.py) implements AES-256-GCM column encryption.
* **Unencrypted Mobile Storage:** In [`app/src/voice/db.native.ts:19-35`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/app/src/voice/db.native.ts#L19-L35), the mobile SQLite database `sakhi-voice.db` stores sessions, turns, transcripts, and clinical danger flags in standard unencrypted SQLite files without SQLCipher or OS Keystore integration.

#### 3.2 Secrets & Git Exposure
* **Active IAM Credentials Stored on Disk:** In [`deploy/aws/.deploy-state/egress-access-key.json:1-9`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/deploy/aws/.deploy-state/egress-access-key.json#L1-L9):
  ```json
  {
      "AccessKey": {
          "UserName": "sakhi-egress-uploader",
          "AccessKeyId": "AKIASEU6STPHPWJAAIXD",
          "Status": "Active",
          "SecretAccessKey": "<redacted: never publish a secret key>",
          "CreateDate": "2026-09-02T17:28:35+00:00"
      }
  }
  ```
  *Note:* While `.gitignore:28` lists `deploy/aws/.deploy-state/`, the file exists unencrypted on local disks and build machines, and script [`deploy/aws/push_secrets.sh:135`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/deploy/aws/push_secrets.sh#L135) actively writes newly minted IAM credentials to this path.
* **Hardcoded Static IP Committed:** [`deploy/aws/sakhi.nginx.conf:5,20`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/deploy/aws/sakhi.nginx.conf#L5) commits the static IP address `3.109.104.193` and nip.io domain into the repository.

#### 3.3 API Authentication & Insecure Direct Object Reference (IDOR)
* **Default-Open Gateway Authentication:** In [`sakhi_gateway.py:208-213`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/sakhi_gateway.py#L208-L213):
  ```python
  def authorized(headers) -> bool:
      expected = os.getenv("SAKHI_GATEWAY_TOKEN", "").strip()
      if not expected:
          return True
      return headers.get("Authorization", "") == f"Bearer {expected}"
  ```
  If `SAKHI_GATEWAY_TOKEN` is unset (the default configuration), all gateway routes are wide open without authentication.
* **Zero Backend Auth on Sensitive Endpoints:** In [`status_server.py:725-789`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/status_server.py#L725-L789), routes `/reports`, `/dashboard/patients`, `/dashboard/patient?id=...`, and `/dashboard/turns?session_id=...` have no JWT/bearer token verification. The code comments assume Nginx handles auth, violating Defense in Depth.
* **Direct Object Reference (IDOR):** [`status_server.py:760`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/status_server.py#L760) takes `id = parse_qs(parsed.query).get("id")` and returns the patient's entire clinical history with zero validation of whether the requester is the assigned ASHA worker.
* **Wildcard CORS on Sensitive Live Stream:** In [`status_server.py:903`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/status_server.py#L903), `_handle_sse()` sets:
  ```python
  self.send_header("Access-Control-Allow-Origin", "*")
  ```
  Any website visited in a browser on the local subnet can connect to `/events` and receive real-time maternal health screening streams.

---

### PILLAR 4: Clinical Algorithm Safety & AI Guardrails
*Evaluated against WHO Safe AI for Health Guidelines and IEC 62304 Medical Device Standards.*

#### 4.1 Deterministic vs. Probabilistic Triage
* **Exemplary Deterministic Engine:** [`sakhi_triage.py:91-275`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/sakhi_triage.py#L91-L275) implements a pure mathematical triage matrix (`TriageEngine.assess`). Blood pressure thresholds (140/90 mmHg for hypertension, 160/110 mmHg for severe range) and red-flag symptoms are evaluated strictly in Python without LLM hallucination risk.
* **Decoupled LLM Role:** The language model acts strictly as an audio extractor and dialogue synthesizer. System prompts ([`sakhi_prompts.py`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/sakhi_prompts.py)) prohibit the LLM from making autonomous diagnostic determinations.

#### 4.2 Disconnected Guardrails & Language Incongruence
* **English Clinic Script on Kannada Safety Violation:** In [`agent.py:776-795`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/agent.py#L776-L795), `medical_output_guard` calls `guardrails.output_flagged()`. On detection, it yields `guardrails.OUTPUT_REPLACEMENT`:
  ```python
  # guardrails.py:287-290
  OUTPUT_REPLACEMENT = (
      " ...actually, our doctors are the right people to guide you on that. "
      "Shall I book you an appointment?"
  )
  ```
  *Failure Mode:* If a Kannada-speaking rural mother mentions medication or a safety rule triggers, the synthesizer switches abruptly to English, speaking a clinic receptionist line offering to book an appointment that the Sakhi platform has no technical ability to fulfill.
* **Unused Output Validator in Production:** [`guardrails.py:695-724`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/guardrails.py#L695-L724) contains `validate_output()`, which implements localized Kannada refusal scripts and dosage regex filters. **This function is never called anywhere in `agent.py`**; it exists only in test suites ([`tests/test_sakhi_guardrails.py`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/tests/test_sakhi_guardrails.py#L176)).

#### 4.3 Red-Flag Clinical Escalation & Vital Bounds
* **Deterministic Emergency Locking:** [`agent.py:4602-4609`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/agent.py#L4602-L4609) correctly invokes `state.lock_intake_on_danger_sign()` when a Tier-1 red flag (vaginal bleeding, convulsions, rigid abdomen) is reported, bypassing subsequent questionnaire turns.
* **Strict Vital Bounds:** [`sakhi_domain.py:165-205`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/sakhi_domain.py#L165-L205) enforces physiologic limits (50–300 systolic, 30–200 diastolic, systolic > diastolic), raising `BloodPressureError` on impossible values.

---

### PILLAR 5: Interoperability & Low-Resource Resilience
*Evaluated against ABDM (Ayushman Bharat Digital Mission), HL7 FHIR R4, DHIS2, and Offline-First Architectures.*

#### 5.1 Healthcare Standards Interoperability (FHIR / ABDM / DHIS2)
* **Zero FHIR R4 Schema Support:** The codebase contains no serialization to HL7 FHIR R4 resources (`Patient`, `Observation`, `Condition`, `RiskAssessment`, or `QuestionnaireResponse`).
* **No ABDM / ABHA Integration:** No ABHA (Ayushman Bharat Health Account) verification, M1 (Milestone 1 - ABHA creation), or M2/M3 (health record linking) workflows exist.
* **No DHIS2 Integration:** No mapping exists to DHIS2 Tracker or Aggregate Data Value sets for rural district reporting.

#### 5.2 Low-Bandwidth & Disconnection Resilience
* **Mobile Offline Outbox Present:** [`app/src/voice/db.native.ts:31`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/app/src/voice/db.native.ts#L31) creates an indexed outbox (`turns_outbox`) in local SQLite for offline turns.
* **Server-Side Session Teardown Without Resume:** In [`agent.py:7300-7326`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/agent.py#L7300-L7326), if a WebRTC connection drops for longer than `reconnect_grace_s` (default 20 seconds), the session terminates. Partial session states cannot be resumed on reconnect; the ASHA worker must restart the intake from `q01`.

---

## 3. Critical Blockers (Must Fix Before Applying)

```
                                    CRITICAL BLOCKERS OVERVIEW
┌──────────┬─────────────────────────────────────────────────┬────────────────────────────┬────────────────────────┐
│ Blocker  │ Vulnerability Description                       │ Primary File & Line        │ Standard Violated      │
├──────────┼─────────────────────────────────────────────────┼────────────────────────────┼────────────────────────┤
│ 1 (P3.2) │ Plaintext AWS IAM Secret Stored on Disk         │ deploy/aws/.../key.json:1  │ ISO 27001 / DPGA Ind 6 │
│ 2 (P2.1) │ PII & Raw Audio Transcripts Logged at Ingress   │ agent.py:4700-4743         │ DPDP § 8(5) / GDPR 5.1 │
│ 3 (P2.3) │ Missing Pre-Screening Consent Workflow          │ sakhi_intake.py:86-130     │ DPDP § 6 / Consent '25 │
│ 4 (P4.2) │ English Receptionist Spoken on Kannada Safety   │ agent.py:776-795           │ WHO AI Safety § 4.2    │
│ 5 (P3.3) │ Unauthenticated Patient Endpoints & Open CORS   │ status_server.py:725-905   │ OWASP API 1 / DPDP § 8 │
└──────────┴─────────────────────────────────────────────────┴────────────────────────────┴────────────────────────┘
```

---

### Blocker 1: Plaintext AWS IAM Secret Stored on Disk
* **File:** [`deploy/aws/.deploy-state/egress-access-key.json:1-9`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/deploy/aws/.deploy-state/egress-access-key.json#L1-L9)
* **Standard Violated:** ISO/IEC 27001 A.9.4, UNICEF Venture Fund Cybersecurity Guidelines, DPGA Indicator 6.
* **Flaw:** Plaintext AWS IAM access key ID `AKIASEU6STPHPWJAAIXD` and secret access key are committed to filesystem storage.
* **Remediation Steps:**
  1. Revoke the access key immediately in AWS IAM console or CLI:
     ```bash
     aws iam delete-access-key --user-name sakhi-egress-uploader --access-key-id AKIASEU6STPHPWJAAIXD
     ```
  2. Refactor [`deploy/aws/push_secrets.sh:135`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/deploy/aws/push_secrets.sh#L135) to eliminate local `.deploy-state/*.json` credential dumping; use IAM Instance Profiles (`AmazonSSMManagedInstanceCore`) and AWS Secrets Manager directly.

---

### Blocker 2: PII & Raw Audio Transcripts Logged at Ingress
* **File:** [`agent.py:4700-4743`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/agent.py#L4700-L4743), [`sarvam_wrappers.py:750`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/sarvam_wrappers.py#L750)
* **Standard Violated:** India DPDP Act 2023 § 8(5), GDPR Article 5(1)(f).
* **Flaw:** Plaintext caller utterances (`raw_speech`) containing names, villages, and health symptoms are logged and dispatched to event streams before guardrail masking.
* **Remediation Code Snippet:**

```python
# Refactored agent.py: Execute PII Sanitization BEFORE any logging or event bus emission
async def on_user_turn_completed(self, turn_ctx, new_message) -> None:
    state = self._state()
    raw_speech = (new_message.text_content or "").strip()
    language = state.language.value if state else "kn"

    # Step 1: Pre-ingress NER and PII sanitization (Masks phone, Aadhaar, names, villages)
    sanitized_speech, pii_detected = guardrails.GUARDRAILS.sanitize_pii_and_entities(
        raw_speech, language=language
    )

    # Step 2: Log ONLY character length, duration, and masked metadata - NEVER free-text PII
    turn_id = self._turn_id(new_message, sanitized_speech)
    logger.info(
        "TABLET_AUDIO_INGRESS: turn_id=%s chars=%d duration=%.2f pii_detected=%s",
        turn_id,
        len(sanitized_speech),
        float(getattr(new_message, "duration", 0.0) or 0.0),
        bool(pii_detected),
    )

    # Step 3: Replace message content in-memory with sanitized text
    new_message.text_content = sanitized_speech
```

---

### Blocker 3: Missing Digital Consent Enforcement Workflow
* **File:** [`sakhi_intake.py:86-130`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/sakhi_intake.py#L86-L130), [`sakhi_schema.py:134-145`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/sakhi_schema.py#L134-L145)
* **Standard Violated:** India DPDP Act 2023 § 6 (Informed Consent) and DPDP Consent Rules 2025.
* **Flaw:** Clinical intake starts immediately without capturing verifiable digital consent in the patient's language.
* **Remediation Schema & Migration:**

```sql
-- deploy/supabase/20261028_dpdp_consent_enforcement.sql
CREATE TABLE IF NOT EXISTS sakhi.patient_consents (
    consent_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id          TEXT NOT NULL REFERENCES sakhi.mothers(patient_id) ON DELETE CASCADE,
    asha_id             TEXT NOT NULL,
    purpose             TEXT NOT NULL DEFAULT 'MATERNAL_PREECLAMPSIA_TRIAGE',
    language_code       TEXT NOT NULL CHECK (language_code IN ('kn', 'hi', 'en')),
    notice_version      TEXT NOT NULL DEFAULT 'v2026.1',
    consent_granted     BOOLEAN NOT NULL,
    audio_proof_hash    TEXT, -- SHA-256 hash of recorded verbal consent audio snippet
    granted_at          TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    withdrawn_at        TIMESTAMPTZ,
    tenant_id           TEXT NOT NULL DEFAULT 'mystree'
);

CREATE INDEX idx_consent_patient ON sakhi.patient_consents(patient_id, consent_granted);
```

```python
# Refactored sakhi_intake.py: Stage 0 Consent Gate
CONSENT_QUESTION = IntakeQuestion(
    id="q00_consent",
    section="Stage 0: Informed Consent",
    text="ತಾಯಿ ಮತ್ತು ಮಗುವಿನ ಆರೋಗ್ಯ ತಪಾಸಣೆಗಾಗಿ ಈ ವಿವರಗಳನ್ನು ದಾಖಲಿಸಲು ನಿಮ್ಮ ಒಪ್ಪಿಗೆ ಇದೆಯೇ?",
    text_hi="क्या आप माँ और बच्चे के स्वास्थ्य जांच के लिए इस विवरण को दर्ज करने की सहमति देते हैं?",
    vad="FAST",
    stage=0,
)
```

---

### Blocker 4: English Receptionist Script Spoken on Kannada Safety Violation
* **File:** [`agent.py:776-795`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/agent.py#L776-L795), [`guardrails.py:287-290`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/guardrails.py#L287-L290)
* **Standard Violated:** WHO Guidelines on Safe AI for Health §4.2, IEC 62304 Risk Control.
* **Flaw:** Spoken response falls back to an English appointment-booking script when output guardrails are tripped.
* **Remediation Code Snippet:**

```python
# Refactored agent.py: Connect guardrails.validate_output() to streaming output guard
async def medical_output_guard(text_stream, language: str = "kn"):
    buffer = ""
    async for chunk in text_stream:
        buffer += chunk
        # Run localized output validator
        decision = guardrails.GUARDRAILS.validate_output(buffer, language=language)
        if decision.blocked:
            pipeline_event(
                "tts", "warn", "Guardrail Blocked Output",
                decision.reason, event="output_blocked"
            )
            # Yield approved localized Kannada/Hindi refusal
            yield decision.text
            return
        yield chunk
```

---

### Blocker 5: Unauthenticated Patient Data Endpoints & Insecure CORS
* **File:** [`status_server.py:685-800, 903`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/status_server.py#L685-L800), [`sakhi_gateway.py:208-213`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/sakhi_gateway.py#L208-L213)
* **Standard Violated:** OWASP API Top 10 (API1: Broken Object Level Authorization, API2: Broken Authentication).
* **Flaw:** `/dashboard/patient?id=xxx` returns full clinical dossiers without token validation, and `/events` sets `Access-Control-Allow-Origin: *`.
* **Remediation Code Snippet:**

```python
# Refactored status_server.py: Enforce Cryptographic Bearer Token & Restrict CORS
def _verify_caller_auth(self, expected_role: str = "asha") -> bool:
    auth_header = self.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        self._send_json({"error": "Unauthorized: Bearer token required"}, status=401)
        return False
    
    token = auth_header.split(" ", 1)[1]
    claims = verify_jwt_token(token) # Validates signature, expiry, and tenant claims
    if not claims or claims.get("role") not in (expected_role, "admin", "doctor"):
        self._send_json({"error": "Forbidden: Insufficient privileges"}, status=403)
        return False
    return True

def _handle_sse(self) -> None:
    if not self._verify_caller_auth("asha"):
        return
    self.send_response(200)
    self.send_header("Content-Type", "text/event-stream")
    # Restrict origin to configured application origins - NO WILDCARDS
    allowed_origin = os.getenv("FRONTEND_ORIGIN", "https://sakhi.bidar.gov.in")
    self.send_header("Access-Control-Allow-Origin", allowed_origin)
    self.end_headers()
```

---

## 4. Architecture Refactoring Plan

To achieve DPGA certification and pass UNICEF Venture Fund due diligence, the repository must be modularized into a decoupled **Provider Adapter Pattern** and an isolated **Clinical Rule Engine**:

```
mystree-sakhi/
├── core/                                # Independent of network, audio, or cloud
│   ├── clinical/
│   │   ├── triage_engine.py             # Pure mathematical triage matrix (sakhi_triage.py)
│   │   ├── domain_models.py             # Immutable dataclasses: BloodPressure, SymptomSet
│   │   └── fhir_exporter.py             # HL7 FHIR R4 JSON serializers (Patient, Observation)
│   ├── consent/
│   │   ├── consent_manager.py           # DPDP consent enforcement state machine
│   │   └── notice_catalog.py            # Multilingual consent scripts (kn, hi, en)
│   └── privacy/
│       ├── redaction.py                 # Pre-logging entity masking (phone, Aadhaar, names)
│       └── crypto.py                    # AES-256-GCM field encryption for SQLite/Postgres
│
├── adapters/                            # Modular plug-and-play interfaces
│   ├── base.py                          # Abstract classes: STTAdapter, TTSAdapter, LLMAdapter
│   ├── mock/                            # Zero-cost local adapters for peer review (0 API keys)
│   │   ├── mock_stt.py                  # Emits synthetic Kannada audio text
│   │   ├── mock_tts.py                  # Returns silent WAV / pre-recorded test audio
│   │   └── mock_llm.py                  # Deterministic tool caller for clinical stages
│   ├── stt/
│   │   ├── sarvam_adapter.py
│   │   ├── deepgram_adapter.py
│   │   └── whisper_local_adapter.py     # Local on-premise FOSS fallback
│   ├── tts/
│   │   ├── sarvam_adapter.py
│   │   └── piper_local_adapter.py       # Local on-premise FOSS fallback
│   └── llm/
│       ├── groq_adapter.py
│       ├── openrouter_adapter.py
│       └── vllm_local_adapter.py        # Local on-premise FOSS fallback
│
├── services/
│   ├── voice_worker.py                  # Thin LiveKit orchestrator (reduced from 7,500 lines)
│   └── api_gateway.py                   # FastAPI / HTTPS service with JWT auth & RBAC
│
├── docker-compose.yml                   # One-command local boot with mock adapters
├── Dockerfile.worker
├── Dockerfile.web
├── CONTRIBUTING.md                      # Open-source governance and code guidelines
├── CODE_OF_CONDUCT.md                   # Contributor Covenant v2.1
└── LICENSE                              # Apache License 2.0 (OSI-approved)
```

---

## 5. Quick Wins (< 2 Hours Execution)

The following 6 remediation tasks can be applied immediately with zero risk of breaking existing clinical audio pipelines:

1. **Adopt Root Open-Source License:** Place an OSI-approved `Apache-2.0` license in the root [`LICENSE`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/LICENSE) file.
2. **Add Governance Standards:** Add standard [`CONTRIBUTING.md`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/CONTRIBUTING.md) and [`CODE_OF_CONDUCT.md`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/CODE_OF_CONDUCT.md) referencing the Contributor Covenant.
3. **Deactivate and Purge IAM Key:** Revoke `AKIASEU6STPHPWJAAIXD` in the AWS console and remove [`deploy/aws/.deploy-state/egress-access-key.json`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/deploy/aws/.deploy-state/egress-access-key.json).
4. **Fix Output Guard Script:** In [`guardrails.py:287-290`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/guardrails.py#L287-L290), replace the English receptionist string with the localized Kannada script:
   ```python
   OUTPUT_REPLACEMENT = "ನಮ್ಮ ವೈದ್ಯರು ನಿಮಗೆ ಸೂಕ್ತ ಮಾರ್ಗದರ್ಶನ ನೀಡುತ್ತಾರೆ. ದಯವಿಟ್ಟು ಹತ್ತಿರದ ಆಸ್ಪತ್ರೆಗೆ ಸಂಪರ್ಕಿಸಿ."
   ```
5. **Enforce Gateway Security Default:** In [`sakhi_gateway.py:208-213`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/sakhi_gateway.py#L208-L213), change `authorized()` so it returns `False` if `SAKHI_GATEWAY_TOKEN` is unset or empty.
6. **Mask Logging at Tablet Ingress:** In [`agent.py:4700`](file:///c:/Users/Tilin%20Bijoy/Desktop/mystreevoiceagent/mystreevoiceagentiog/agent.py#L4700), replace `logger.info("TABLET_AUDIO_INGRESS: text='%s'...", raw_speech)` with `logger.info("TABLET_AUDIO_INGRESS: chars=%d...", len(raw_speech))` to prevent streaming patient transcripts into standard system logs.
