# Sakhi inside the app: voice architecture

How the ASHA's Sakhi check-in talks and listens **inside the tara app**
(Expo Go today, a store build later): the layers, how they communicate, the
algorithms that keep the conversation continuous and fast, local storage,
the PDF, and memory limits. Code: `app/src/voice/`, gateway: `api/index.py`.

## 1. What is different from the phone-call agent

The existing Sakhi (`agent.py`) is a **LiveKit call**: WebRTC audio both
ways, server-side VAD, STT, an LLM in every turn and streamed TTS. That is
right for open conversation, but inside the app the job is a **fixed,
24-question antenatal check-in** with deterministic danger rules. So:

* **No LLM in the turn loop.** The next question is known in advance, and
  the danger rules are code (`data/mockScreening.ts`). This removes about
  0.4-1.0 s per turn (LLM time to first token in `LATENCY_REPORT.md`).
* **Speech is understood after the turn, not before the next one.** Sakhi
  moves on as soon as the ASHA stops speaking; the transcript and the danger
  check arrive in the background (section 4.3).
* **No WebRTC.** LiveKit's native SDK cannot run in Expo Go. The app uses
  `expo-audio`'s `AudioStream` (raw 16-bit PCM from the microphone, in Expo
  Go) and plain HTTPS to a small gateway, which also works on 2G/3G where a
  WebRTC session would keep dropping.

## 2. Layers

```
+--------------------------------------------------------------------+
| UI          asha/session/[id].tsx: renders the engine's snapshot   |
|             (stage, question, live mic level, lines, danger flags) |
+-----------------------------^--------------------------------------+
                              | subscribe / snapshot (10 Hz max)
+-----------------------------v--------------------------------------+
| ENGINE      voice/engine.ts: the conversation state machine        |
|             greet -> ask -> listen -> (speech|silence) -> advance  |
|             + STT job queue + danger alerts + finish               |
+------+-------------+---------------+---------------+---------------+
       |             |               |               |
+------v-----+ +-----v------+ +------v------+ +------v---------------+
| AUDIO IN   | | AUDIO OUT  | | TRANSPORT   | | PERSISTENCE          |
| mic.ts     | | speaker.ts | | gateway.ts  | | db.native.ts (SQLite)|
| AudioStream| | cached TTS | | HTTPS, retry| | sessions, turns,     |
| vad.ts     | | file player| | breaker,    | | events; outbox for   |
| utterance  | | device TTS | | timeouts    | | offline answers      |
| buffer     | | fallback   | |             | | report PDF (expo-    |
+------------+ +------------+ +------+------+ | print)               |
                                     |        +----------------------+
                          HTTPS (TLS)| keys never on the phone
+------------------------------------v-------------------------------+
| GATEWAY   api/index.py (Vercel, ap-south-1 edge cache)             |
|   GET  /api/sakhi/health          which providers are configured   |
|   GET  /api/sakhi/tts?text&lang   Gnani Kaveri (timbre-v2.5) -> WAV|
|                                   LRU in the process + 1 year at   |
|                                   the CDN (same text = same URL)   |
|   POST /api/sakhi/stt?lang        WAV body -> Gnani /stt/v3,       |
|                                   format=transcribe (digits)       |
|                                   -> {text, en, ms, provider}      |
|   (Sarvam is the fallback: transcribe + translate in parallel)     |
+--------------------------------------------------------------------+
```

Communication rules between layers:

* UI -> engine: commands only (`start`, `exit`, `undo(alert)`); never timers.
* Engine -> UI: one immutable snapshot through `subscribe()` and
  `useSyncExternalStore`. The mic level is throttled to 10 updates per second
  so the screen never re-renders at audio rate.
* Engine -> services: async calls with a **turn token**. Every await checks
  the token, so a late callback from an old turn (exit, app in background)
  can never change the current one.
* Audio never crosses into React state. PCM frames go mic -> VAD ->
  utterance buffer -> WAV file -> upload, all outside React.

## 3. Turn timeline (target)

```
ASHA stops speaking
 |-- 0 ms      last voiced frame
 |-- +550-850  VAD hangover ends (adaptive, 4.2)  -> END OF TURN
 |-- +5 ms     WAV written to disk, STT job queued (background)
 |-- +20 ms    next question audio starts (already on disk, 4.4)
 |   ...       ASHA hears question n+1 (about 3 s)
 |-- +0.5 s     STT result for answer n arrives (measured: Gnani via
 |              the gateway, kn 0.51 s / hi 0.49 s end to end)
 |              -> rules -> danger? -> flag shown, spoken at the
 |                 next question boundary
```

The ASHA waits only for the VAD hangover: **about 0.6-0.9 s from her last
word to Sakhi's next word**, with no network on that path. With the phone
call agent the same gap measured p50 1.06 s and p95 3.09 s.

## 4. Algorithms

### 4.1 Audio capture (`mic.ts`)

`AudioStream` at 16 kHz mono int16 (what the STT wants; if the device
delivers another rate it is resampled linearly into a reused buffer).

**Half-duplex (required on iOS).** `AudioStream.start()` on iOS sets the
audio session to the record-only category, which mutes every sound the app
plays, including Sakhi's voice and the phone's own voice (this was the
"Sakhi is silent" bug). So the mic is ON only while Sakhi listens. Before
each line she says, the engine stops the mic, waits for expo-audio's
session hand-off (~130 ms on iOS), and sets the playback category on the
loudspeaker (`playbackMode()`). The mic being off while she speaks also
means no echo and less battery. The VAD ignores the exact-zero frames a
restarting mic delivers, so they never lower the noise floor. Each callback delivers a PCM `ArrayBuffer`; it is read through
an `Int16Array` view (no copy) and split into 20 ms frames (320 samples).

### 4.2 Endpointing: adaptive energy VAD (`vad.ts`)

Per 20 ms frame: RMS -> dBFS.

```
noise floor F: starts at the median of the first 200 ms, then follows the
  quietest frames: during silence F <- F + 0.05 * (dB - F) (slow, EMA);
  clamped to [-75, -35] dBFS so a fan or a loud room still works
speech starts: dB > F + 10 for 3 frames in a row (60 ms), and dB > -55
speech continues: dB > F + 6 (hysteresis: harder to start than to stay)
end of turn: after speech, quiet for H ms, where
  H = base(question) + min(300, 0.08 * speech_ms)
  base = 550 ms yes/no questions, 800 ms numbers, 850 ms descriptions
  (longer answers get a little more patience for a mid-sentence pause)
pre-roll: the 300 ms before speech start are kept (first syllable intact)
no answer: no speech within 7 s -> re-ask once (short prompt), then
  record "no answer" and continue
cap: 20 s of speech -> end of turn (no runaway recordings)
```

O(1) work per frame and no allocation per frame.

### 4.3 Optimistic advance + background STT queue (`engine.ts`)

The questionnaire's next step never depends on the words (except danger,
which only raises a flag), so at end of turn the engine:

1. writes the utterance to `cache/sakhi-audio/<session>-<n>.wav`
2. inserts a `turns` row (`stt_status = pending`)
3. queues an STT job, and **immediately asks the next question**

The STT queue runs at most 2 jobs at once (enough to keep up, low memory),
with a timeout of 8 s, 2 retries with backoff (0.4 s, 1.2 s) for 5xx / 429 /
network errors, and a **circuit breaker**: 3 failures in a row -> the
transport is "offline" for 30 s; new answers go straight to the outbox
(`stt_status = offline`) and the conversation does NOT stop. When the
network returns (or at the end of the check-in) the outbox is drained.

### 4.4 Question audio: prefetch + disk LRU (`speaker.ts`)

* The question text is local (Kannada / Hindi / English already in the app).
* Audio comes from the gateway (Gnani's Kaveri voice, Kannada / Hindi) and
  is saved to `cache/sakhi-tts/<hash>.wav`, streamed to disk, never held in
  JS memory.
* **One provider request at a time.** Gnani allows a single active request
  per key, shared by STT and TTS (HTTP 429 "Only 1 active request is allowed
  at a time", measured). The gateway runs every Gnani call through one
  priority lane: STT, then the line being said now, then the next question,
  then warm-up. Measured: a "now" line waits only for the one request
  already in flight. The phone downloads in the background one file at a
  time; the line being said now skips that queue.
* **Warm-up agent:** while the intro screen is shown, `prepare()` downloads
  every line of the check-in (greeting, 24 questions, re-prompt, danger and
  closing lines), in speaking order. A first TTS request costs about
  0.5-1 s; a cached one 0.08 s; a file already on the phone 0 ms. While
  question n plays, n+1 and n+2 are fetched again in case warm-up was cut
  short.
* After the first check-in all 24 questions are on the phone: **0 ms network
  per question** from then on. The gateway also caches each file at the CDN
  edge for a year (same text = same URL).
* LRU: at most 120 files (24 questions x 3 languages + prompts); the oldest
  are deleted when that is exceeded.
* If the file is not ready in 6 s, the gateway is unconfigured or failing,
  or playback has not started within 2.5 s, Sakhi speaks the same text with
  the phone's own voice (`expo-speech`). The wait is long on purpose: an
  iPhone has no Kannada voice of its own, so Gnani's voice is worth a short
  wait.
* Every step is logged to the Metro terminal (`voice/log.ts`, development
  only): gateway health, mode, mic on/off, VAD start/end with levels, each
  TTS download and playback, STT time and text, and every fallback with its
  reason.

### 4.5 Answer understanding (`classify.ts`)

There is **no LLM in the turn loop**: the check-in is a fixed
questionnaire, so an LLM would only add 0.5-2 s and a failure point. Gnani's
`format=transcribe` writes numbers as digits ("ಬಿ ಪಿ 148 ಬೈ 96 ಇದೆ",
"बी पी 148 बाई 96 है"), so the rules read the Kannada / Hindi transcript
directly; when the Sarvam fallback is used, its English translation is
checked as well.

* BP: `(\d{2,3}) (/|by|over|ಬೈ|ಓವರ್|बाई|बटा|ओवर) (\d{2,3})`; danger if
  >= 140 or >= 90. If only one number is understood, danger if it is >= 140
* danger-sign questions: danger unless the answer is a clear "no": a
  negation (no / ಇಲ್ಲ / नहीं), short, and no "but" (ಆದರೆ / लेकिन / थोड़ा).
  For baby movements the logic is reversed (less / ಕಡಿಮೆ / कम = danger)
* other questions: stored as said, no flag

### 4.6 Continuous flow and danger

Sakhi never waits for a tap between questions. A danger answer does NOT
stop her: the flag appears at the top (with **Undo** for a misheard
answer), she says "I have marked this for the doctor" before the next
question, and she continues. The ASHA can still exit at any time (with a
confirm). If the app goes to the background the engine pauses and re-asks
the current question on return.

Two controls on the live panel, because real visits are not ideal:

* **Repeat**: the mother did not hear. Sakhi stops and asks again at once.
* **Next**: ends the answer now (a noisy room where the end of speech is
  never detected). What was said is kept and transcribed; with nothing said,
  the question is recorded as "No answer" and Sakhi moves on without a
  re-ask.

The panel shows ONE state at a time ("Sakhi is asking" / "Speak now" /
"Hearing you"), the question once, a level ring around the orb while she
listens, and which voice is speaking: **Sakhi's voice**, or a warning if the
backup voice was ever used. Answers appear underneath, newest first. No
developer numbers on screen (they are in the log).

### 4.7 The web (Expo web, desktop testing)

The same engine runs in a browser, with Sakhi's real voice and a real mic:

* `speaker.web.ts`: Gnani audio from the gateway, same priority queue
  (`tts-queue.ts`), WAV bytes in memory (LRU), played through one
  `AudioContext` (`web-audio.ts`) that the Start tap unlocks (browser
  autoplay rules). The browser's own voice is a backup only, and shown.
* `mic.web.ts`: `getUserMedia` (echo cancellation, noise suppression) into
  an AudioWorklet that decimates 44.1/48 kHz to 16 kHz with a box filter and
  posts the same 20 ms PCM16 frames as the phones. VAD, WAV upload and the
  turn logic are shared, unchanged.
* The gateway sends CORS headers on `/api/sakhi/*` (no cookies; optional
  bearer token), and on `localhost` the app calls `http://localhost:8787`
  directly (`EXPO_PUBLIC_SAKHI_GATEWAY_LOCAL`), not through the tunnel.

Verified end to end in a browser with a synthetic microphone (real Kannada
speech recorded through Gnani, over -50 dB room noise): every line in
Sakhi's voice, mic on 0-37 ms after she stops, next question ~20 ms after
end of speech is detected, Gnani STT 330-770 ms in the background, correct
transcripts.

## 5. Local storage (`expo-sqlite`, works in Expo Go)

```sql
sessions(id PK, mother_id, lang, started_at, ended_at, status, pdf_uri)
turns(session_id, idx, qid, question, audio_uri, transcript, transcript_en,
      danger, stt_status, ended_speech_at, stt_ms, PRIMARY KEY(session_id, idx))
events(session_id, at, kind, ms)        -- latency telemetry, small
```

WAL journal mode, prepared statements reused per session, writes off the UI
path (fire-and-forget with error logging). An audio file is deleted as soon
as its transcript is saved, so storage stays small; offline answers keep
their file until uploaded. On web (preview) the same interface is an
in-memory store.

## 6. The report PDF

At the end the engine flushes the outbox (up to 8 s), commits the answers to
the mother's record (the same `CheckInReport` the doctor reads), and builds
the A4 PDF with the existing template (`lib/report-pdf.ts`) via
`expo-print`. Its file path is saved in `sessions.pdf_uri`, so the ASHA can
share it from the report page, even offline.

## 7. Memory and CPU budget

| Item | Size | Notes |
|---|---|---|
| Utterance buffer | 640 KB max, **one** preallocated `Int16Array` (20 s at 16 kHz) | reused every turn |
| Pre-roll ring | 9.6 KB | 300 ms |
| PCM frames | 0 copies | `Int16Array` views over the native buffer |
| TTS audio | 0 in JS | streamed to disk, played from the file |
| Snapshot | ~5 KB | last 60 lines only |
| Mic level to UI | 10 Hz | not 50 Hz |
| One audio player | reused | replace source per question, never one per question |

## 8. Gateway

`api/index.py` (same Vercel function as `/api/token`), logic in
`sakhi_gateway.py`. Needs `GNANI_API_KEY` (or `SARVAM_API_KEY`, chosen with
`SAKHI_APP_PROVIDER`) in Vercel's environment; optional `SAKHI_GATEWAY_TOKEN`
(then the app must send `Authorization: Bearer ...`). The app reads the base
URL from `EXPO_PUBLIC_SAKHI_GATEWAY` (e.g. `https://<project>.vercel.app/api/sakhi`).
Without it, or when `/health` says a provider is missing, the session runs
in **demo mode**: the phone's voice asks the questions and the Testing
tools supply answers. The flow, storage and PDF are identical.

For development, `python scripts/sakhi_gateway_dev.py` runs the same handler
on this PC with the repo's `.env`, opens a Cloudflare quick tunnel and
writes `app/.env.local` (the tunnel URL for phones, `localhost:8787` for the
desktop browser); restart the app tunnel afterwards. Provider
requests send a `User-Agent` header (Gnani's Cloudflare edge rejects
Python's default with 403 / error 1010).

## 10. Live Sakhi: real-time voice over WebRTC

### 10.1 Why not the phone-call agent

The first live version joined the phone-call agent (`agent.py`). In the app
that agent showed three faults, all from its design rather than the network:

* an LLM writes every sentence Sakhi says, so she merged questions ("...when
  was the last period? how many months now?") and invented wording;
* after Gnani's streaming STT closed on 60 s of silence, its fallback switched
  to an English-first recogniser (Deepgram nova-3) for the rest of the call;
* nothing in it guaranteed "one question, then wait".

### 10.2 The design: one state manager, one voice executor

```
 tara app                                        server
 voice/engine.ts  THE state manager              sakhi_app_agent.py
   question order, rules (classify.ts),            speaks ONE line, listens
   flags, records, SQLite, PDF                     for ONE answer, transcribes
        |                                                 ^
        | voice/live.ts  RemoteVoice                      |
        |   {t:"say", seq, text, listen:{hangoverMs}}     |
        |   <- said / speech / heard{text} / silence      |
        v                                                 |
 WebView page (livekit-client) ==== WebRTC (LiveKit Cloud) ====
                  audio both ways + reliable data channel "sakhi.app"
```

* The ENGINE decides everything. Live and on-the-phone check-ins run the same
  code, questions, rules and records; only the voice transport differs.
* The voice agent has no LLM and no opinion: `say` = exact text (Gnani
  Kaveri, pre-synthesised for the whole check-in at "hello"), then listen.
* **Sequence numbers.** Every command carries a seq; every reply echoes it;
  the app accepts only the outstanding seq. A late answer can never land on
  the wrong question (Repeat, pause, exit all bump it).
* **Stage machine.** `engine.ts` has an explicit table of allowed moves
  (`MOVES`); `go()` enforces it and refuses any move out of done / exited.
* **Never stops.** If the live connection breaks, or an answer does not come
  back within 45 s, the engine continues on the phone with the very same
  question (`loseRemote`).
* **Handshake.** The agent can appear in the room a moment before it reads
  commands: it says `up`, and the app repeats the idempotent `hello` every
  second until `ready`.

### 10.3 Listening (the STT the call agent got wrong)

* Silero VAD (neural) on the ASHA's audio decides speech / silence.
* Capture is continuous while she answers, from 0.5 s before speech started
  (a ring buffer covers the VAD's reaction time). Pauses shorter than the
  question's hangover stay inside ONE answer; Next ends it at any instant
  with everything said so far.
* The whole answer goes to Gnani in ONE request (`format=transcribe`: digits),
  trailing silence trimmed. No WebSocket to time out, no English fallback.
* Half-duplex: audio while Sakhi speaks is never an answer.
* TTS and STT go through the gateway's single priority lane (Gnani allows one
  request per key at a time) and share its cache with the offline mode.

### 10.4 Measured

`scripts/test_live_agent.py` (a real LiveKit participant, spoken Kannada over
room noise), two runs, all checks passed:

| Check | Result |
|---|---|
| BP answer | "ಬಿಪಿ 148 ಬೈ 96 ಇದೆ", 1.8-2.2 s from her last word (hangover 0.8 s + STT 0.7-1.0 s) |
| silence | `silence` after the no-speech time |
| 0.45 s pause inside an answer | one answer: "ತಲೆನೋವು ಇದೆ ಮತ್ತು ಕಣ್ಣು ಮಂಜಾಗುತ್ತದೆ" |
| Next while still talking | the answer so far, 0.5-0.9 s after Next |

In the app (browser, synthetic voice): all 24 questions live over WebRTC, one
per turn, no fallback; STT 0.35-1.2 s; Start -> live 1.9-2.9 s; report built.

### 10.5 The conversation on screen

One UI for both modes: a compact pinned panel (orb, one state, question,
Repeat / Next) with a stable height, and the conversation as a keyed chat
thread underneath. `Screen followContent` follows new lines only while the
reader is at the bottom: "pinned" breaks only on an upward scroll, re-pins at
the bottom, and is measured from the content's own layout (the animated
ScrollView's `onContentSizeChange` never fired on the web). Measured: at the
bottom, new lines follow (gap 0-1 pt); scrolled up, the view moves 0 pt.

### 10.6 Running it on this PC

`python scripts/sakhi_agent_dev.py` (the voice agent, as `sakhi-app-dev`,
`start` mode, worker HTTP on 8790), `python scripts/sakhi_gateway_dev.py`
(gateway + tunnel; restarts only the server on code changes, so the tunnel URL
in the app stays valid), and the app's `npm run tunnel`. The page's test
microphone (`?test=1`, localhost only) lets the whole loop run without a
person.

### 10.7 iOS notes (from an iPhone log)

* The WebView page no longer disconnects itself: livekit's
  `disconnectOnPageLeave` also fires on WebKit's `freeze`, seen as a
  client-side disconnect 20 ms after the iPhone mic started.
* After the mic, iOS takes long to switch back to playback (0.8 s measured);
  the app re-activates the session explicitly and waits up to 5 s for the
  first sound before using the backup voice.

## 11. Sakhi Direct: the check-in over one WebSocket

The ASHA home's **Sakhi Direct** section (`app/asha/direct.tsx`) runs the
same check-in (`session/[id]?via=direct`) with the voice on ONE WebSocket to
the gateway: built into React Native, so it works in Expo Go with no WebView
and no WebRTC.

* Server: `sakhi_ws.py`, at `/api/sakhi/ws` on the gateway's own host and
  tunnel. In development `scripts/sakhi_gateway_dev.py` runs an aiohttp
  front on :8787 (the socket + a pass-through of every other route to
  `api/index.py` on :8786), all in ONE process so the Gnani lane and the TTS
  cache are shared. Vercel functions cannot hold a WebSocket: in production
  this runs on the worker host.
* Client: `voice/direct.ts`, a `RemoteVoice` for the engine (the engine stays
  the single state manager). The socket opens on the intro, says hello with
  every line, and the lines arrive on the phone in speaking order (stored by
  `Speaker.put`), so a line plays from disk the moment it is due.
* Protocol (JSON + binary): `hello`/`ready`, `tts`/`audio`+WAV, `listen` then
  20 ms PCM16 frames, `speech`, `heard`/`silence`, `next`, `stop`,
  `ping`/`pong`. Every reply carries its seq; stale ones are dropped.
* Listening: half-duplex (mic only while Sakhi listens). Server-side Silero on
  32 ms windows: start = 3 voiced of 5 (~100 ms), end = hangover without
  voice (pauses inside one answer stay one answer), < 200 ms of voice is
  ignored, 0.5 s pre-roll, trimmed tail, 25 s cap. Time is counted in audio
  received; a wall-clock watchdog ends the turn if audio stops coming.
* Flow never stops: a dropped socket reconnects by itself (3 tries); an
  answer lost with it returns as silence and Sakhi asks again; only if the
  server stays away does the engine continue on the phone, same question,
  without repeating a question already asked.
* Test: `python scripts/test_ws_voice.py wss://<tunnel>/api/sakhi/ws`
  (real spoken Kannada answers: heard, silence, a pause, Next mid-answer).
  Measured through the Cloudflare tunnel: STT 0.4-1.2 s after the answer
  ends; a cached line plays ~50 ms after it is due.

## 12. Recorded lines and slot filling (no LLM, no run-time TTS)

* **Recorded once.** `scripts/prerecord_sakhi.py` records every fixed line
  (greeting, the 24 questions, the re-ask, "please say it again", the danger
  warning, the closing) in Kannada and Hindi with Gnani's female voice
  Kaveri, reading the texts from the app's own sources. 58 WAVs, ~6 MB, in
  `app/assets/voice/`; `voice/prerecorded.ts` finds a recording by the exact
  text. A reworded line falls back to TTS until the script is run again
  (`prerecorded.test.ts` fails meanwhile). Gnani streams WAV with placeholder
  sizes; `sakhi_gateway.fix_wav_header` writes the real ones.
* **Understanding** (`voice/nlu/`): normalise (scripts, digits, number words:
  "ನೂರ ನಲವತ್ತೆಂಟು" → 148) → one Aho–Corasick pass over the lexicon
  (`lexicon.ts`: Kannada, Hindi, English and romanised forms) → polarity by
  scope (fused Kannada endings, English pre-cues, post-cues covering a list,
  clause breaks at "but / ಆದರೆ / लेकिन") → regexes for BP, units, dates.
* **Slots decide the flow** (`voice/nlu/intake.ts`): every answer fills the
  asked question and prefills later ones from explicit facts. Next question =
  the first not yet answered; a non-danger question already answered (or not
  applicable) is skipped; a danger question is always asked. An answer that
  does not fill its question gets ONE "sorry, once more" + the same question,
  then is recorded as "Not clear" (danger questions flagged for the doctor).
* **Cost per check-in:** speech-to-text for each answer only. No TTS for
  Kannada / Hindi, no language model.

## 13. Later

* Streaming STT (partial transcripts while she speaks) on the Sakhi Direct
  socket: the frames already stream, only the server changes.
* A dev build with LiveKit for open conversation with the mother.
* Sync `turns` to Supabase (`sakhi.check_ins`) from the same outbox.
