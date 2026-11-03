# Sakhi Voice Agent Diagnostic and Repair Report

Date: 2026-09-22  
Scope: localhost only; no AWS deployment or remote infrastructure changes

## Outcome

The post-transcription silence was caused primarily by a re-entrant speech race in the tool loop. Clinical tools called `session.say()` while LiveKit still owned the active generation, and two additional completion listeners could start more speech handles before LiveKit scheduled its normal post-tool continuation. Those competing handles could interrupt or cancel playback.

The repaired design has one owner for a conversational turn:

1. A tool persists data and returns a non-empty speech directive.
2. LiveKit performs the normal post-tool LLM continuation.
3. If that continuation is truly empty, `SakhiAgent.llm_node()` yields the deterministic `last_spoken_prompt` into the same generation.
4. No tool or tool-completion callback starts a second `session.say()`.

The localhost worker and console are running. The worker is registered with LiveKit as `sakhi-maternal-care`; its live STT, LLM fallback, and TTS prewarms passed.

## Hypothesis Isolation

| Hypothesis | Result | Evidence |
|---|---|---|
| TTS chunk contract violation | Hazard found and repaired, but not the primary silence trigger | Sarvam REST returns a WAV container. The adapter now parses the WAV and accepts only non-empty PCM16, mono, 22,050 Hz frames. It never forwards RIFF bytes. A live Sarvam request passed the raw-PCM assertions. LiveKit accepts the provider-declared rate; forcing 24 kHz onto a 22.05 kHz response would change speed and pitch. |
| `StreamAdapter` starvation | Ruled out | `EagerClauseSentenceStream.end_input()` flushes its remaining buffer. A regression test proves a final Kannada phrase without `.`, `!`, or `?` is emitted. The provider correctly declares `streaming=False`, so LiveKit uses the REST `StreamAdapter` path. |
| Audio track publication/subscription | Verified end to end | `AgentSession` owns track publication. A real client created through `/api/token` joined the dispatched room, subscribed to the agent track, and received a non-empty audio frame. |
| Tool-loop silence/turn lockout | Confirmed root cause | The tools and two completion listeners called `session.say()` during the active tool generation. LiveKit emits its tool-completed event before scheduling the normal tool-response speech, so the callbacks created competing speech handles at exactly the unsafe point. |
| Browser autoplay suspension | Frontend hazard found and repaired | The client had both `RoomAudioRenderer` and a manual attach path, risking duplicate audio elements, while its custom unlock listener could miss the original start gesture. Playback is now owned solely by `RoomAudioRenderer`, with LiveKit's `StartAudio` control shown only when the browser reports playback is blocked. |

The observed `speaking -> listening` transition before the log line `Greeting accepted` is expected: that log is written after `await session.say(...)` returns, which happens after playout has finished. It is not proof that audio was dropped.

## Repairs

- Removed direct and background `session.say()` calls from every intake/clinical tool.
- Removed duplicate tool-completion speech watchdogs from the session and agent event listeners.
- Kept one safe empty-continuation fallback in `SakhiAgent.llm_node()` and clear it when normal text is produced, preventing duplicate speech on later turns.
- Restored the tested `aiohttp` Sarvam seam and added strict WAV, sample width, channel, sample-rate, non-empty, and byte-alignment validation.
- Added a shared Kannada/Indic speech normalizer. Slash notation becomes the spoken Kannada word `ಅಥವಾ`; Markdown and unsafe punctuation are removed.
- Fixed the lean stage prompt so Kannada, Hindi, and English retain their selected language and honorific instead of always receiving Kannada.
- Fixed `EagerClauseTokenizer.tokenize()` for LiveKit Agents 1.6.10, where the private `ChanEmpty` exception is no longer exported.
- Changed the localhost LLM order from the exhausted OpenAI account to the measured Groq-first chain and disabled redundant per-provider retries. No secret values were changed.
- Allowed `127.0.0.1` as a Next.js development origin. Without this, Next 16 blocked its own HMR/dev resources when the console was opened by IP instead of `localhost`.
- Replaced the frontend's ad-hoc audio attachment/unlock behavior with LiveKit's managed `RoomAudioRenderer` and playback-state-aware `StartAudio` control.
- Corrected the status/config endpoint to report the configured Sarvam model instead of a hard-coded `bulbul:v2` value.

## Frontend-to-Backend Connectivity

The first failing `/api/token` request was not an application routing bug: the original frontend process had been started in a network-restricted sandbox, so it could proxy to the local worker but could not reach LiveKit Cloud to create the dispatch. Restarting that same localhost frontend with outbound access restored the route. No AWS service was contacted or changed.

The verified path is:

`Next.js /api/token -> LiveKit dispatch -> sakhi-maternal-care worker -> published remote audio track -> browser subscription`

The opt-in integration test exercises that entire path rather than mocking it.

## Verification

- Focused voice pipeline: **93 passed**.
- Complete `tests/` suite: **1,164 passed, 14 skipped** (the two additional skips are the opt-in live frontend integration checks).
- Live frontend/backend/WebRTC integration: **2 passed**; token/dispatch creation succeeded and a real client received a non-empty agent audio frame.
- Live Sarvam integration: **1 passed**; non-empty, header-free, aligned PCM returned.
- Next.js production build after the frontend audio fix: **passed**, including TypeScript and all 18 routes/pages.
- Worker health: `ready=true`, `reason="worker registered"`.
- Console proxy health: `ready=true`.
- Console config: Sarvam `bulbul:v3`, voice `shreya` (matches the running worker).
- Console home page: HTTP 200.
- Live prewarm after restart:
  - LLM Groq-first chain: passed in about 1.2 seconds.
  - Sarvam TTS: 18,816 bytes PCM16 at 22,050 Hz in about 0.44 seconds.
  - Gnani STT: passed.

The bare repository-root `pytest` command also discovers an untracked `test_ec2_tts.py` scratch script that executes AWS SSM during import. It was intentionally excluded; no AWS deployment or mutation was performed. The maintained suite is `python -m pytest -q tests`.

## Local Test

The requested console is available at:

`http://127.0.0.1:3001`

Port 3000 was already owned by another local Node process and returned an unrelated 404, so this console selected 3001. Press **ಮಾತನಾಡಿ**, allow microphone/audio access, hear the greeting, then answer with a mother's name and age. The next response should acknowledge the saved values and ask the pregnancy question without starting a competing speech handle.

## Non-blocking Existing Warnings

- ESLint currently reports 30 pre-existing frontend typing/hook errors. The production build passes; these errors are unrelated to WebRTC audio or this repair.
- Python 3.10 reaches the cited Google client support boundary in October 2026; upgrading to Python 3.11 is advisable but not required for this localhost verification.
