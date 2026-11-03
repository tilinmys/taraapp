# Security Policy & Health Data Protection

Sakhi (Tara) processes maternal health screening data to assist frontline ASHA workers. We work to the **India Digital Personal Data Protection Act 2023 (DPDP Act)** and its 2025 Rules, the **WHO guidance on ethics and governance of AI for health**, and **UNICEF's child and maternal safeguarding principles**.

## Supported Versions

Only the latest commit on `main` receives security updates.

| Version | Supported |
| :--- | :--- |
| `main` (latest) | :white_check_mark: |
| Older commits and forks | :x: |

## Reporting a Vulnerability

**Do NOT file public GitHub issues for security vulnerabilities or personal data leakages.**

Please report security issues confidentially via GitHub Private Vulnerability Reporting (under the **Security** tab of this repository) or by emailing:

* **security@mystree.org**

### What to Include

1. Description of the vulnerability and attack vector.
2. Affected components (e.g. voice gateway, WebRTC endpoint, database, dashboard, or LLM guardrails).
3. Proof-of-concept steps or transcripts (ensure **NO real patient PII** is included).

If you come across real personal data while testing, stop, do not keep a copy, and tell us in the report.

### Response Timeline

* **Acknowledgment:** Within 48 hours.
* **Assessment & Remediation Plan:** Within 7 business days.
* **Coordinated Public Disclosure:** Following patch verification, on a date agreed with the reporter. We credit reporters who want to be credited.

Treat anything that could expose a mother's health record, an ASHA's identity, a call recording, or a provider credential as **critical**.

## Data Protection Principles

What the code does today, and what is not built yet:

* **No transcripts in logs or telemetry at ingress.** The voice agent logs a turn's length and timing, never its words; pipeline events (written to `pipeline_events.jsonl` and streamed to the dashboard) carry lengths, slot states and question ids, not the mother's words. Covered by `tests/test_ingress_pii_masking.py`.
* **Identifier masking.** Aadhaar, PAN, phone and bank-account numbers are masked (`guardrails.redact_pii`) before a transcript is stored as a conversation turn or put in a report.
* **Clinical records are stored, behind access control.** The screening record a doctor needs (name, age, readings, symptoms, the conversation) is kept in the database. Patient-level dashboard routes require a bearer token and sit behind the dashboard's login (`tests/test_api_auth.py`); the speech gateway refuses every request without its token.
* **Consent.** Before a voice check-in, the app reads a notice to the mother in her language (purpose, who sees her answers, the speech services that process her voice, her right to refuse) and the check-in cannot start until the ASHA records that she agreed; the record keeps who took it, when, the language and the notice version (`app/src/data/store.ts`, tested in `app/src/data/__tests__/consent.test.ts`). The server-side record is the append-only table in `deploy/supabase/20261102_dpdp_consent.sql`; syncing the app's consent to it is **not wired yet**.
* **Third-party processors.** Speech, LLM, call and hosting providers, what each receives, and the open alternatives: [`docs/THIRD_PARTY_SERVICES.md`](docs/THIRD_PARTY_SERVICES.md). A deployment needs a data processing agreement with each provider it turns on.
* **Public data, aggregate only.** `/public/metrics` publishes live counts (check-ins, mothers screened, high-risk mothers, referrals) with no patient-level data; any count from 1 to 4 is shown as "<5" (`tests/test_public_metrics.py`).
* **Right to erasure (DPDP s.12).** An automated erasure or anonymisation workflow is **not built yet**. Until it is, erasure requests to the deploying organisation are handled manually; contact security@mystree.org.
* **No secrets in the repository or the app.** Provider keys live in AWS Secrets Manager (production) or a local, gitignored `.env`; nothing provider-side is placed in an `EXPO_PUBLIC_*` variable, which would ship inside the app.
