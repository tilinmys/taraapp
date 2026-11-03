# Third-party services and platform independence

Sakhi (Tara) is open source (code MIT, content CC BY 4.0), and it calls some
third-party services at run time. This page lists every one the code can use,
what data each receives, whether it is on by default, and the open alternative
that removes the dependency. It answers the Digital Public Goods Standard's
*platform independence* question, and it is the list of **data processors** a
deployment must cover under the DPDP Act 2023 (see
[Obligations for a deployment](#obligations-for-a-deployment)).

Every provider is chosen by configuration (environment variables on the
server), never hard-wired, and **no provider key is ever in the app**: the app
talks only to the project's own gateway.

## The in-app voice check-in (the tara app)

| Service | Used for | Data it receives | Default | Open alternative |
| --- | --- | --- | --- | --- |
| Gnani (`GNANI_API_KEY`) | Speech-to-text of her answers; speech for lines not recorded | The mother's **voice** (answers); Sakhi's question text | Yes, when its key is set (`SAKHI_APP_PROVIDER=gnani`) | AI4Bharat IndicConformer or OpenAI Whisper (self-hosted) |
| Sarvam (`SARVAM_API_KEY`) | Same, as the alternative provider | Same | When Gnani is not configured (`SAKHI_APP_PROVIDER=sarvam`) | As above |
| LiveKit Cloud (`LIVEKIT_*`) | Carries the live call between the app and Sakhi's agent | Call **audio** | Yes, for "Live Sakhi" | LiveKit server, open source (Apache-2.0), self-hosted |

Not a dependency: **Sakhi's 24 questions and prompts are recorded files
shipped in the app** (`app/assets/voice/`). The check-in asks every question
with no speech service at all; answers recorded offline are transcribed when a
connection returns.

## The phone line and dashboard (server)

| Service | Used for | Data it receives | Default | Open alternative |
| --- | --- | --- | --- | --- |
| Deepgram (`DEEPGRAM_API_KEY`) | Speech-to-text on calls | Caller **voice** | Primary (`STT_PRIMARY=deepgram`) | IndicConformer / Whisper, self-hosted |
| Sarvam, Gnani, Smallest | Speech-to-text fallbacks; Sarvam speaks the replies (`TTS_PROVIDER=sarvam`) | Caller **voice**; reply text | Fallback / TTS default | Self-hosted open TTS (e.g. AI4Bharat Indic Parler-TTS); recorded prompts (`assets/audio/prompts/`) |
| Groq, OpenRouter, OpenAI | The LLM that phrases replies (`LLM_PROVIDER_ORDER=groq,openrouter,openai`) | **Transcripts** of the call | Groq first | Open-weight models (the default `qwen/qwen3-32b` is one) served on your own hardware; triage never depends on the LLM (`sakhi_triage.py` decides) |
| LiveKit Cloud + SIP | Call media, the phone line | Call **audio** | Yes | LiveKit server, self-hosted |
| Exotel (SIP trunk) | Connects phone numbers to LiveKit | Call **audio**, phone numbers | When the phone line is used | Any SIP trunk provider |
| AWS: EC2, S3, Secrets Manager, Cost Explorer (`ap-south-1`, Mumbai) | Hosting, call recordings, secrets, the cost tab | Health records, recordings | Production deployment | Any Linux host or Docker; any S3-compatible store |
| Supabase / PostgreSQL (`DATABASE_URL`) | The clinical database | Health records | Production | PostgreSQL anywhere (Supabase is itself open source); SQLite for a single site |
| Vercel | Can host the gateway | What the gateway handles | Optional | Any host (`scripts/sakhi_gateway_dev.py` runs it anywhere) |
| Tavus, Beyond Presence, D-ID | Optional video avatar | Sakhi's reply text and audio | **Off** (`SAKHI_AVATAR=none`) | Not needed: the voice works without it |
| Murf, Cartesia, Rumik, Skillies, Vakyam, Google Gemini | Alternative speech providers kept for comparison | As the provider they replace | **Off** unless selected | As above |

## Design-time tools (no data at run time)

| Tool | Used for | Run-time data |
| --- | --- | --- |
| Canva AI | The app's clay icons and the baby/story illustrations | **None.** The images are bundled files; the app never calls Canva. Licence status: [`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md) |
| Gnani / Sarvam text-to-speech | Recording Sakhi's fixed lines once (`scripts/prerecord_sakhi.py`) | **None** at run time: only Sakhi's own question text was sent, once |

## Obligations for a deployment

A deployment that turns on a service above makes that provider a **data
processor** under the DPDP Act 2023. Before using real patient data:

1. **Sign a data processing agreement** with each provider that receives voice,
   transcripts or records.
2. **Name them in the consent notice.** The app's notice names the speech
   services of the in-app check-in (Gnani, LiveKit); if you switch providers,
   update `cNotice` in `app/src/i18n/strings.ts` and raise the notice version
   (`CONSENT_NOTICE_VERSION` in `app/src/data/store.ts`).
3. **Know where data is processed.** AWS (`ap-south-1`) and LiveKit's India
   region keep data in India; confirm each other provider's region in its terms
   (some LLM providers process outside India, which the DPDP Act allows except
   to restricted countries).
4. **Prefer the open alternative where it works**: it keeps health data on
   infrastructure you control.
