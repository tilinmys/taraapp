# Sakhi Production Latency Audit

**Audit mode:** read-only production measurement. No production code, environment variable, service, or infrastructure was changed.

## Executive summary

1. **Later-turn end-of-speech to first audio:** p50 **1060.7 ms**, p95 **3091.5 ms** (n=20). [Evidence](latency_audit_raw/log_aggregate_full.json)
2. **First user turn end-to-end:** p50 **1415.5 ms**, p95 **3008.7 ms** (n=32).
3. **Largest endpoint bottleneck—REST TTS full sentence:** p50 **1751.7 ms**, p95 **2133.4 ms** to first response byte (n=20).
4. **REST short-clause TTS:** p50 **624.3 ms**, p95 **722.4 ms** (n=20). Clause buffering saves roughly 1127.4 ms at median versus sending the measured full sentence.
5. **WebSocket TTS now works:** first audio **445.9 ms** after start, 25 audio frames / 143360 bytes. This directly contradicts the historical 'WebSocket hangs' assumption, but it is a single architecture probe, not a distribution.

Interpretation: endpointing and LLM TTFT each contribute roughly 0.6–1.0 seconds in observed turns, while REST TTS contributes ~0.6 seconds for a short first clause and ~1.7 seconds for a full sentence. The working WebSocket path is the strongest optimization candidate, subject to a proper n≥20 validation before migration.

## Endpoint benchmarks

### Sarvam TTS REST

| Case / metric | n | min ms | p50 ms | p95 ms | max ms |
|---|---:|---:|---:|---:|---:|
| Short clause TTFB | 20 | 512.2 | 624.3 | 722.4 | 1000.4 |
| Short clause full response | 20 | 521.4 | 633.9 | 731.8 | 1005.4 |
| Full sentence TTFB | 20 | 1365.7 | 1751.7 | 2133.4 | 3534.2 |
| Full sentence full response | 20 | 1380.3 | 1768.4 | 2152.4 | 3553.5 |

All 40 production-shape requests succeeded using `bulbul:v3`, speaker `shreya`, `kn-IN`, pace `0.95`, and effective `enable_preprocessing=false`. Every decoded WAV reported **22,050 Hz mono PCM16**, matching the current wrapper pin; the feared 24 kHz mismatch was not present in these responses. The wrapper does not send a TTS temperature parameter.

The single v2 control returned HTTP **400** in 162.1 ms. The response body was not readable after streamed inspection (`<unreadable body: ResponseNotRead>`), so the exact provider message is unavailable; no retry was made because the audit allowed n=1.
The HTTP streaming endpoint probe returned HTTP **400** in 128.9 ms; its body had the same post-stream readability limitation. This is an instrumentation gap, not evidence that HTTP streaming is universally unsupported.

### Sarvam TTS WebSocket

Connection: 185.7 ms; first audio: 445.9 ms; audio frames: 25; audio bytes: 143360; completion event received: yes. The client sent config, text, and flush messages. The 10.2-second total reflects the probe waiting after the completion event and must not be interpreted as synthesis duration. Sarvam documents the [WebSocket streaming endpoint](https://docs.sarvam.ai/api-reference/text-to-speech/stream) and [HTTP versus WebSocket selection](https://docs.sarvam.ai/api/api-guides-tutorials/text-to-speech/which-api-to-use).

### Sarvam STT

| Model/mode / metric | n | min ms | p50 ms | p95 ms | max ms |
|---|---:|---:|---:|---:|---:|
| saarika:v2.5_transcribe — connect_ms | 10 | 76.6 | 150.5 | 177.1 | 178.5 |
| saarika:v2.5_transcribe — first_interim_ms | 0 | — | — | — | — |
| saarika:v2.5_transcribe — final_ms | 10 | 1602.9 | 1655.5 | 1718.2 | 1736.4 |
| saaras:v3_codemix — connect_ms | 10 | 77.7 | 136.4 | 511.9 | 792.3 |
| saaras:v3_codemix — first_interim_ms | 0 | — | — | — | — |
| saaras:v3_codemix — final_ms | 10 | 1573.8 | 1652.8 | 2054.3 | 2318.1 |

The same five-second cached Kannada greeting was used for all 20 streams. Both model groups completed 10/10. The legacy endpoint emitted final `type=data` results but no interim transcript in these runs; therefore first-interim distributions have n=0. The server finalized a speech segment after roughly 1.6 seconds while the five-second fixture was still being streamed, so `final_ms` is segment-final latency from connection start—not finalization after all five seconds. Transcript text is retained only in the raw fixed-fixture artifact, not repeated here. Clinical-word comparison is limited because the greeting is not a clinical-number test clip.

### LLM endpoints

| Provider case / TTFT | n | min ms | p50 ms | p95 ms | max ms |
|---|---:|---:|---:|---:|---:|
| groq_primary — first stream event | 20 | 238.4 | 382.2 | 564.4 | 687.5 |
| groq_primary — first visible text (partial) | 12 | 266.0 | 419.5 | 471.6 | 474.4 |
| openrouter_primary — first stream event | 20 | 467.7 | 850.0 | 1719.0 | 3503.4 |

Exactly 20 Groq-primary and 20 OpenRouter-primary minimal streamed completions were issued (40 total). Groq produced a first stream event in all 20 samples, but only 12 yielded a non-empty visible text delta under this minimal prompt; both distributions are shown rather than silently dropping eight samples. Secondary models and the OpenAI fallback were not independently probed because doing so at n=20 would violate the global ~40-completion cap. Their configuration is still audited below.

### Network and host

Worker: `i-0b4c434a3c7647384` / `t3.small`, `ap-south-1a` (`ap-south-1`).
- `api.sarvam.ai`: rtt min/avg/max/mdev = 4.200/4.238/4.286/0.020 ms; DNS `4.247.234.152`. DNS geography cannot be proved from an A record alone.
- `api.groq.com`: rtt min/avg/max/mdev = 0.909/0.971/1.019/0.031 ms; DNS `104.18.38.236, 172.64.149.20, 2a06:98c1:3105::6812:26ec, 2a06:98c1:310a::ac40:9514`. DNS geography cannot be proved from an A record alone.

## Production log analysis

Source window: `2026-09-02T12:36:22.525791+00:00` to `2026-09-23T08:30:49.793367+00:00`; 7779 events; 0 malformed lines; 109 call boundaries, 55 complete. The parser observed 53 overlapping start boundaries, so call-level grouping is approximate and this is itself an observability finding.

### Greeting

| Metric | n | min ms | p50 ms | p95 ms | max ms |
|---|---:|---:|---:|---:|---:|
| tts_ttfa_ms | 51 | 501.9 | 1468.2 | 3918.6 | 4642.9 |
| first_audio_total_ms | 51 | 501.9 | 1468.2 | 3918.6 | 4642.9 |

### First user turn

| Metric | n | min ms | p50 ms | p95 ms | max ms |
|---|---:|---:|---:|---:|---:|
| tts_ttfa_ms | 32 | 559.1 | 909.2 | 1568.7 | 1605.4 |
| first_audio_total_ms | 32 | 559.1 | 1415.5 | 3008.7 | 3156.3 |
| eou_delay_ms | 13 | 0.0 | 1000.7 | 1372.1 | 1392.4 |
| llm_ttft_ms | 16 | 294.2 | 717.0 | 876.2 | 883.9 |
| stt_final_ms | 13 | 0.0 | 300.3 | 595.2 | 702.2 |
| llm_total_ms | 16 | 365.2 | 755.6 | 926.0 | 934.8 |

### Later user turns

| Metric | n | min ms | p50 ms | p95 ms | max ms |
|---|---:|---:|---:|---:|---:|
| tts_ttfa_ms | 19 | 454.0 | 819.6 | 1616.4 | 1648.9 |
| first_audio_total_ms | 20 | 122.0 | 1060.7 | 3091.5 | 3486.7 |
| eou_delay_ms | 8 | 600.4 | 798.0 | 1054.5 | 1083.5 |
| llm_ttft_ms | 10 | 178.2 | 645.5 | 871.6 | 884.0 |
| stt_final_ms | 8 | 0.0 | 353.9 | 561.7 | 638.0 |
| llm_total_ms | 10 | 274.7 | 786.8 | 981.3 | 1035.7 |

Negative `-1000` sentinel values were excluded from distributions and counted separately: `{"first_user_turn:tts_ttfa_ms": 3, "first_user_turn:first_audio_total_ms": 3, "later_user_turn:tts_ttfa_ms": 4, "later_user_turn:first_audio_total_ms": 3}`.

### Turn outcome funnel

| Outcome | Count |
|---|---:|
| backchannel_ignored | 2 |
| llm_generation | 26 |

Only outcomes explicitly logged can be counted. Several requested categories (assent exemption, repeat request, duplicate turn, side-talk, unbacked-claim block, guardrail StopResponse) had zero matching events in this window; absence may mean either no occurrence or missing instrumentation.

### Adaptive VAD

| Applied state/threshold | Count |
|---|---:|
| FRAGMENT_RECOVERY:1300ms | 1 |
| HIGH_COGNITIVE_LOAD:1000ms | 40 |
| HIGH_COGNITIVE_LOAD:1400ms | 8 |
| HIGH_COGNITIVE_LOAD:450ms | 1 |
| HIGH_COGNITIVE_LOAD:600ms | 20 |
| TRANSACTIONAL:200ms | 1 |
| TRANSACTIONAL:250ms | 1 |
| TRANSACTIONAL:300ms | 5 |
| TRANSACTIONAL:400ms | 3 |
| TRANSACTIONAL:550ms | 18 |
| TRANSACTIONAL:850ms | 1 |

Actual thresholds span 200–1,400 ms and do not reduce to only the documented 300/1,000 ms pair. This indicates adaptive/runtime overrides or historical configurations within the log window.

### Failures and fallbacks

| Event group | Count |
|---|---:|
| fallback_event | 451 |
| warn:Caller metadata skipped | 105 |
| warn:Room disconnected | 65 |
| warn:Farewell ignored - no user turn yet | 57 |
| warn:Remote audio wait timed out | 53 |
| latent_filler | 31 |
| error:Recording failed to stop | 21 |
| error:Session error | 14 |
| error:Recording failed to start | 9 |
| warn:TTS metrics | 8 |
| warn:Line live check | 7 |
| warn:TTS prewarm failed | 2 |
| error:Duplicate worker blocked | 1 |
| warn:Silence auto-close | 1 |
| hedge_or_race_event | 1 |
| warn:LLM prewarm failed | 1 |
| error:Fatal entrypoint error | 1 |
| warn:LLM metrics | 1 |

The five most recent redacted events per group are preserved in [log_aggregate_full.json](latency_audit_raw/log_aggregate_full.json). They are not duplicated here to avoid a raw-log blob and unnecessary exposure. The largest class, `fallback_event` (451), warrants event-name-level instrumentation because the aggregate matcher intentionally includes every event containing “fallback.”

### Three most recent complete call timelines

#### Call sequence 106 — room `sakhi-room-mucmx7rh-d7f07769ed9d` — 79604.2 ms

| Turn | Path | EOU ms | LLM TTFT ms | TTS TTFA ms | First audio total ms | Cancelled |
|---:|---|---:|---:|---:|---:|---|
| 2 | llm | — | 178.2 | -1000.0 | -821.8 | True |
| 2 | llm | 600.4 | 515.8 | 762.5 | 1878.7 | False |
| 2 | deterministic | — | — | 1518.0 | 1518.0 | False |
| 3 | llm | 642.3 | 546.6 | 819.6 | 2008.5 | False |
| 3 | deterministic | — | — | 1281.9 | 1281.9 | False |
| 3 | deterministic | — | — | 719.5 | 719.5 | False |
| 4 | llm | — | 534.4 | -1000.0 | -465.6 | True |

#### Call sequence 107 — room `sakhi-room-mucn76g1-e7be22ae8c0e` — 21028.2 ms

| Turn | Path | EOU ms | LLM TTFT ms | TTS TTFA ms | First audio total ms | Cancelled |
|---:|---|---:|---:|---:|---:|---|
| 1 | llm | — | 294.2 | 1044.3 | 1338.5 | False |
| 2 | llm | 600.4 | 521.6 | -1000.0 | 122.0 | True |

#### Call sequence 108 — room `sakhi-room-mucqeg4b-dd5eaf20064a` — 65652.0 ms

| Turn | Path | EOU ms | LLM TTFT ms | TTS TTFA ms | First audio total ms | Cancelled |
|---:|---|---:|---:|---:|---:|---|
| 1 | llm | — | 770.0 | 829.5 | 1599.5 | False |
| 2 | llm | 601.4 | 856.5 | 1612.8 | 3070.7 | False |

Repeated turn numbers and deterministic metrics adjacent to LLM metrics show that a single semantic turn can emit multiple latency records; future analysis should key every record by a stable turn/utterance ID.

## Live test calls

TC-1 through TC-8 were **skipped**. No dedicated instrumented test LiveKit room with a browser microphone/audio fixture and normal-network client was available to this audit. Running synthetic calls against production without that isolation would create clinical sessions and violate the read-only constraint. Existing production logs were analyzed instead; they cannot prove the exact scripted TC behaviors.

## Configuration audit

| Variable | Effective value | Finding |
|---|---|---|
| `TTS_PROVIDER` | `sarvam` |  |
| `SARVAM_TTS_MODEL` | `bulbul:v3` |  |
| `SARVAM_SPEAKER` | `shreya` |  |
| `SARVAM_PACE` | `0.95` |  |
| `SARVAM_PACE_GREETING` | `0.85` |  |
| `SARVAM_TTS_ENABLE_PREPROCESSING` | `false` | v3 documentation says preprocessing is always on/ignored; effective false may not affect provider behavior. |
| `SARVAM_STT_MODEL` | `saaras:v3` | Contradiction: task described saarika:v2.5; production is saaras:v3. |
| `SARVAM_STT_MODE` | `codemix` | Contradiction: task described kn-IN transcription; production uses codemix. |
| `TTS_EAGER_MIN_CHUNK_CHARS` | `25` |  |
| `TTS_EAGER_MAX_CHUNK_CHARS` | `160` |  |
| `SARVAM_MIN_BUFFER_SIZE` | `20` |  |
| `MIN_ENDPOINTING_DELAY` | `0.20` | Differs from documented 0.30 s default. |
| `MAX_ENDPOINTING_DELAY` | `1.2` |  |
| `VAD_MIN_SILENCE` | `0.30` | Differs from documented 0.45 s default. |
| `ENABLE_MULTILINGUAL_TURN_DETECTOR` | `false` |  |
| `SAKHI_PRESPEECH_GUARD_ENABLED` | `true` |  |
| `VOICE_EAGER_TTS_CHUNKING_ENABLED` | `true` |  |
| `PREEMPTIVE_GENERATION` | `false` |  |
| `PREEMPTIVE_TTS` | `false` |  |
| `LLM_PROVIDER_ORDER` | `groq,openrouter` |  |
| `GROQ_LLM_MODEL` | `openai/gpt-oss-120b` |  |
| `GROQ_LLM_MODEL_FAST` | `openai/gpt-oss-20b` |  |
| `OPENROUTER_LLM_MODEL` | `meta-llama/llama-3.3-70b-instruct` |  |
| `OPENROUTER_LLM_MODEL_FAST` | `meta-llama/llama-3.1-8b-instruct` |  |
| `OPENAI_LLM_MODEL` | `gpt-4o-mini` |  |
| `LLM_TEMPERATURE` | `0.3` |  |
| `LLM_MAX_COMPLETION_TOKENS` | `200` |  |
| `MAX_TOOL_STEPS` | `6` |  |
| `SARVAM_TTS_TEMPERATURE` | `<unset>` | Wrapper does not send temperature. |

Credential values were never read into output. Presence only: `SARVAM_API_KEY`=True, `GROQ_API_KEY`=True, `GROQ_API_KEY_2`=False, `OPENROUTER_API_KEY`=True, `OPENAI_API_KEY`=True.

## Humanization audit

- Greeting cache hits: 62; queued greetings: 105. The log-derived cache-hit share of all `Sakhi said` events is **57.41%**, but this denominator is event-based, not audio-duration based.
- Latent filler fired **31** times.
- Assent versus backchannel: **0 vs 2**. Assent instrumentation is absent/zero in this window, so recognition quality cannot be concluded.
- The Sarvam REST call path passes pace. It mentions pitch handling, does not pass loudness, and does not support/send temperature. Verify pitch semantics separately before assuming the provider receives it.
- Bidar/dialect evidence: 15 code mentions across the inspected canonical map/agent. The map exists, but counts alone do not prove coverage quality.
- Address terms found include Kannada `ಅಕ್ಕಾ` (4 occurrences) and `ತಾಯಿ` (5 occurrences). The active prompt context should be reviewed to determine which form is actually spoken to an ASHA worker.

## Anomalies and open questions

- The WebSocket TTS probe produced audio at 445.9 ms, disproving the current historical assumption that it always hangs. Repeat with n≥20 and production sentence boundaries before changing architecture.
- The REST v3 sample rate was consistently 22,050 Hz despite current Sarvam documentation describing a 24 kHz default. Provider behavior may be request/account/version dependent.
- The HTTP streaming probe returned 400, but the body was not captured due the probe’s streamed-response read state. A future read-only probe should explicitly call `aread()` before error extraction.
- Production STT configuration is saaras:v3/codemix, not the saarika:v2.5/kn-IN stack stated in the audit brief. This affects both latency and accuracy interpretation.
- No interim STT messages were observed; finals arrived, but interim-dependent UI/turn logic cannot benefit from this endpoint behavior.
- Fifty-three overlapping call-start boundaries and 54 incomplete calls make per-call attribution uncertain. A stable session ID plus turn ID is needed in every event.
- Seven negative latency sentinels were present; these were excluded rather than treated as real milliseconds.
- The most recent complete-call records contain repeated turn numbers and cancelled generations, so per-turn tables are event timelines rather than one-row-per-human-utterance truth.
- No live test room was available, so greeting cache hit/miss, barge-in, repeat, silence, and deterministic paths were not actively validated.
- The OpenAI fallback was configured but not benchmarked due the total-completion cap; its latency remains open.

## Methodology and spend

Endpoint window: `2026-09-23T08:47:11.359493+00:00` to `2026-09-23T08:50:01.755920+00:00` from EC2 `i-0b4c434a3c7647384` in `ap-south-1a`. Log window is stated above. All remote scripts ran in memory through SSM and only read files/configuration or contacted measured endpoints.

- TTS: 40 distributed REST requests + 1 deprecated-v2 control + 1 HTTP-stream probe + 1 WebSocket probe = 43 endpoint attempts. Using the supplied v2 reference price ₹15/10K characters as a rough proxy, measured REST text was about 1,960 characters (~₹2.94), plus two short streaming probes; actual v3 billing may differ.
- STT: 20 × 5-second clips = 100 audio seconds. At ₹30/hour, approximate cost is ₹0.83, excluding any provider minimum rounding.
- LLM: 40 minimal completions. Rupee cost is not estimated because model/token pricing was not supplied; raw records retain provider/model and success/latency.
- Probe failures were retained. No silent retries were used after budgeted endpoint attempts.

## Raw evidence index

- [endpoint_probe_full.json](latency_audit_raw/endpoint_probe_full.json) — Every TTS/LLM/network sample and WebSocket message metadata.
- [stt_probe_full.json](latency_audit_raw/stt_probe_full.json) — Every STT sample and fixed-fixture transcript.
- [log_aggregate_full.json](latency_audit_raw/log_aggregate_full.json) — Redacted aggregate, failures, VAD, and timelines.
- [config_effective.json](latency_audit_raw/config_effective.json) — Allowlisted effective configuration and static wrapper findings.
- [log_schema_ssm.json](latency_audit_raw/log_schema_ssm.json) — Log schema inventory.
- [audio_fixture_inventory_ssm.json](latency_audit_raw/audio_fixture_inventory_ssm.json) — Read-only audio fixture inventory.

## Constraints recap

Production remained unchanged. No secrets or `.env` contents are included. Endpoint distributions use n=20 where required; STT comparisons use n=10 per model as specified. Single architecture/deprecation probes are labeled as single observations. Raw evidence is preserved beside this report.
