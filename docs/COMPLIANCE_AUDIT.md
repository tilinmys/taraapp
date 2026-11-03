# Compliance audit (2026-11-03)

Checked against the UNICEF Innovation Fund Request for Expression of Interest
(open source technology solutions for children), the Digital Public Goods
Standard, and the India DPDP Act 2023. ✅ met in the repository, ⚠️ partly or
outside the code, ❌ open.

| Rule | Status | Where |
| --- | --- | --- |
| Open source licence for software (GPL/MIT/BSD) | ✅ MIT | [`LICENSE`](../LICENSE) |
| CC-BY for design and content | ✅ CC BY 4.0, names and logos excluded | [`LICENSE-CONTENT.md`](../LICENSE-CONTENT.md) |
| Third-party material keeps its licence | ✅ LiveKit client, Baloo fonts, Esri imagery listed | [`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md) |
| Rights to Canva AI images and TTS recordings | ⚠️ pending the providers' terms | [`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md#pending-a-rights-check) |
| Publicly exposed, real-time, measurable data | ✅ live aggregate counts, no patient-level data, counts 1-4 shown as "<5" | `status_server.py` `/public/metrics`; [`tests/test_public_metrics.py`](../tests/test_public_metrics.py); nginx `/status/public/metrics` |
| Platform independence (DPG): third-party services replaceable | ✅ every service configurable, each with an open alternative; the app holds no provider key | [`THIRD_PARTY_SERVICES.md`](THIRD_PARTY_SERVICES.md) |
| Canva AI used at run time? | ✅ no: images are bundled files | [`THIRD_PARTY_SERVICES.md`](THIRD_PARTY_SERVICES.md#design-time-tools-no-data-at-run-time) |
| DPDP s.6 consent before collection, naming the processors | ✅ notice in her language, explicit tick, check-in blocked without it; who/when/language/notice version recorded | `app/src/app/asha/session/[id].tsx`, `app/src/data/store.ts`; [`app/src/data/__tests__/consent.test.ts`](../app/src/data/__tests__/consent.test.ts) |
| Consent synced to the server-side record | ❌ not wired | [`deploy/supabase/20261102_dpdp_consent.sql`](../deploy/supabase/20261102_dpdp_consent.sql) defines it |
| DPDP s.8(5): no transcripts in logs or telemetry | ✅ ingress, gate, canonicalisation, Sarvam STT/TTS and the app agent log lengths only | [`tests/test_ingress_pii_masking.py`](../tests/test_ingress_pii_masking.py) |
| Data processing agreements with providers | ⚠️ outside the code | [`THIRD_PARTY_SERVICES.md`](THIRD_PARTY_SERVICES.md#obligations-for-a-deployment) |
| DPDP s.12 right to erasure | ❌ not built (manual on request) | [`SECURITY.md`](../SECURITY.md#data-protection-principles) |
| Access control on patient data; no secrets in repo or app | ✅ | [`tests/test_api_auth.py`](../tests/test_api_auth.py), [`SECURITY.md`](../SECURITY.md) |
| Impact on children | ✅ newborn outcomes stated | [`README.md`](../README.md) |
| Prototype with pilot results | ⚠️ prototype yes; pilot outcomes not documented | to add from real pilot data |
| Registered private company in a programme country | ⚠️ outside the code | the applicant confirms |
