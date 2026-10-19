# Sakhi does not advance past q01: STT fragmentation incident (2026-09-24)

## Symptom

The ASHA worker says the mother's name and age; Sakhi asks
"ತಾಯಿಯ ಹೆಸರು ಮತ್ತು ವಯಸ್ಸು ಎಷ್ಟು?" again and again. The log rail shows
`Transcript final` lines that are wrong or meaningless (`S.`, `10-20`,
`ಹೌದು ಹೌದು`, `Yes Sir.`, `ಆಯ್ತು`), and every `[SAKHI_TURN]` shows
`q01 -> q01`, `extracted_values={}`, `answer_saved=false`.

q01 repeating is a **consequence**: the intake is correctly refusing to save
a name/age it never received. Do not loosen q01 validation to "fix" this.

## Root cause

**Corruption happened inside STT, caused by how the audio stream was
segmented.** The 2026-09-24 Sarvam protocol update (needed - the old wire
format returned nothing at all) also turned on
`high_vad_sensitivity=true` and a `flush` on every provider `END_SPEECH`.

With high sensitivity Sarvam ends a "speech segment" at every micro-pause.
Each fragment is transcribed with no context, and our flush forces each one
final. One 3.6 s answer came back as 3-4 finals:

    ತಾಯಿಯ ಹೆಸರು | Priya Sharma | ವಯಸ್ಸು ಇಪ್ಪತ್ತು | 28 ವರ್ಷ

"ಇಪ್ಪತ್ತೆಂಟು" (28) was cut into "ಇಪ್ಪತ್ತು" (20) + "28", so extraction read
the age as 20; shorter fragments came back as filler hallucinations
("Yes Sir.", "Okay", "ನಮಸ್ಕಾರ"). LiveKit correctly joins the finals of one
turn - the text it joins is already broken.

**Fix:** `SARVAM_STT_HIGH_VAD_SENSITIVITY` now defaults to **false**
(`sarvam_wrappers._sarvam_stt_query`). The whole answer comes back as one
correct final.

## Evidence

### Raw STT (`scripts/stt_accuracy_probe.py`, production key/config, 10 runs each)

Synthetic sentence "ತಾಯಿಯ ಹೆಸರು ಪ್ರಿಯಾ ಶರ್ಮಾ, ವಯಸ್ಸು ಇಪ್ಪತ್ತೆಂಟು ವರ್ಷ."
through the real `SarvamSTT` wrapper; PASS = the production extractor gets
age 28 and the name.

| condition | high VAD **on** (before) | high VAD **off** (after) |
|---|---|---|
| clean speech | 0/10 (4 finals, age read 20) | 10/10 (1 final) |
| quiet (-24 dB) | 10/10 | 10/10 |
| room noise (SNR 10 dB) | 10/10 | 10/10 |
| speaker to mic (band-limited, reverb, noise) | 3/10 | 19/20 (1 empty, transient) |
| **total** | **23/40 (57%)** | **49/50 (98%)** |

A/B on the flush signal showed it is harmless once high sensitivity is off.

### Full production path (`scripts/e2e_call_probe.py`)

A synthetic caller joins a real LiveKit room like the browser, the
production worker is dispatched, and the sentence is published as the
microphone - through WebRTC/Opus, BVC, Silero VAD, the turn detector, Sarvam,
`on_user_turn_completed` and extraction.

| condition | result |
|---|---|
| clean, BVC on (production) | 3/3: one final, `q01 -> q02`, Sakhi: "ಸರಿ, ಪ್ರಿಯಾ ಶರ್ಮಾ 28 ವರ್ಷ" + q02 |
| speaker to mic, BVC on | 0/5: VAD never fired - the voice was erased before STT |
| speaker to mic, BVC off | transcribed correctly, `q01 -> q02` |

## Follow-up (2026-09-25): Sarvam's server VAD dropped whole utterances

With the fragmentation fixed, a real call still stalled on q01. The new
`[SAKHI_STT_DEBUG]` records showed why: LiveKit's VAD heard three speech
bursts (01:23:52, 01:24:02, 01:24:11), but Sarvam's streaming socket
reported speech only for the last (`vad_start_s 52.42`, `vad_end_s 54.47`)
and transcribed only that one. On the streaming socket **Sarvam's own server
VAD decides what speech is**, and it disagreed with ours. The first turn
also arrived half-heard ("... 28 ವರ್ಷ", no name) and Sakhi repeated the whole
q01 question.

**Fix (architecture):** one VAD owns segmentation. `agent.build_stt()` now
wraps `SarvamBatchSTT` (Sarvam REST, one request per complete utterance) in
LiveKit's `stt.StreamAdapter`, driven by a dedicated Silero instance
(`_build_stt_segmentation_vad`: 0.4 s pre-roll, 0.6 s silence). Sarvam no
longer decides what is dropped. REST latency measured 0.25-0.33 s per
utterance, faster than the streaming final (0.63-0.69 s). Deepgram remains
the fallback. `SARVAM_STT_SEGMENTATION=provider` restores the streaming
socket.

**Fix (flow):** `sakhi_intake.armed_question_text()` - when q01 has the age
but not the name (or the reverse), Sakhi asks only for the missing part
("ಸರಿ. ತಾಯಿಯ ಹೆಸರು ಏನು?") instead of repeating the full question.

**Verified through the full production path** (`scripts/e2e_call_probe.py`):
clean answers 2/2 and answers spoken in parts with 1.8 s pauses 3/3 all
reached q02 - e.g. "ತಾಯಿಯ ಹೆಸರು Priya Sharma." (name saved) ... "ವಯಸ್ಸು 28 ವರ್ಷ"
(q01 answered) ... "ಸರಿ, Priya Sharma, ಇಪ್ಪತ್ತೆಂಟು ವರ್ಷ ದಾಖಲಾಗಿದೆ" + q02.

In `[SAKHI_STT_DEBUG]` the batch path reports `stt_provider: sarvam-batch`,
`segmentation: livekit_vad` and `stt_latency_ms`.

## Follow-up 2 (2026-09-25 01:36): speech arrived near-silent, Sakhi said nothing

`[SAKHI_STT_DEBUG]` for the only utterance: `audio_duration_ms 2480`,
`audio_rms_dbfs -60.5`, `audio_peak_dbfs -34.1`, `transcript_length 0`.
Failure was **before STT** - the voice reached the worker almost muted
(normal speech in earlier calls: -27 to -36 dBFS) - and the design turned
"quiet" into "total silence" in three ways:

| gap | fix |
|---|---|
| BVC erases any voice not close to the mic | `NOISE_CANCELLATION=off` (in `/etc/sakhi-turn-tuning.env`) |
| quiet speech sent to Sarvam as-is | `SarvamBatchSTT._normalise`: lift to -3 dBFS peak, max +30 dB (`gain_db` in the debug record) |
| empty transcript = no reply | `STT_EMPTY_SINK` + a watchdog: speech >= 0.6 s with no final within 3 s -> Sakhi says "ಕ್ಷಮಿಸಿ, ಸರಿಯಾಗಿ ಕೇಳಿಸಲಿಲ್ಲ…" + the question (`_make_unheard_speech_handler`) |

Also: `SARVAM_STT_MODE=transcribe` (codemix wrote "ವಯಸ್ಸು" as "YSU");
the STT segmentation VAD is slightly more sensitive than the turn VAD
(0.25 vs 0.35) so the turn layer never commits speech STT did not get;
English is off for the pilot (`spoken_language()`, frontend Kannada-first,
English sentences dropped by the question lock on Kannada/Hindi calls).

Verified end to end (production, synthetic caller): faint (-56 dBFS
source) 2/2, clean, room noise, paused answers and one speaker-to-mic call
all reached q02.

## Follow-up 3 (2026-09-25): silent turns from a stale tool list

Worker log: `unknown AI function 'record_patient_intake'`. `llm_node`
re-scoped the agent's tools per turn and passed the NEW list to the model,
but livekit executes tool calls against the list it captured before
`llm_node` ran. The model called a tool livekit did not know, the turn
produced no speech, and Sakhi was silent. A second bug hid behind it:
`lean_stage_tools` de-duplicated with `is`, but every `toolset.x` lookup is
a new bound-method object, so the stage tool appeared twice and a session
built from that list fails with "duplicate function name".

**Fix:** one constant tool set for the whole call (5 recording tools +
`connect_smfmi_specialist` + `end_call`), registered on the Agent at
construction; the session itself gets no tools; de-duplication by name.
The per-turn re-scope now finds nothing to change. Rule for future edits:
**never change an agent's tool list inside `llm_node`.**

Verified: 8/8 full-path synthetic calls (clean, faint, speaker, paused,
room noise) - Sakhi spoke in every one, 1.1-2.8 s after the caller stopped;
zero `unknown AI function` / `duplicate function` / validation errors.

## Second finding: BVC and loudspeaker audio

`NOISE_CANCELLATION=bvc` (LiveKit Background Voice Cancellation) removes every
voice that is not the close-talking primary speaker. A voice played from a
phone or laptop speaker - or a person talking from a metre away - is exactly
what it removes. Close-talk speech is unaffected (3/3).

* Testing by playing a recording into the microphone **will fail with BVC on**.
  Test by speaking directly into the device, or set `NOISE_CANCELLATION=off`
  for the test.
* Field decision (open): if mothers answer Sakhi directly from a distance,
  BVC may suppress them. `off` kept every voice in tests and Sarvam handled
  room noise at SNR 10 dB without any noise filter (10/10).
* **Do not use `noise_cancellation.NC()`**: on this 24 kHz room input it aborts
  every job process (`NcSession::initSession: Input and output sample rates
  must be equal`) - a completely deaf agent. The option was removed.

## How to diagnose this next time

1. Every transcript now logs `[SAKHI_STT_DEBUG]` (log rail / dashboard):
   provider, model, mode, language, utterance id, `is_final`,
   `transcript_length`, `transcript_hash` (never the text),
   `audio_duration_ms`, `audio_rms_dbfs`, `audio_peak_dbfs`, provider VAD
   start/end, and the high-VAD setting.
2. Place the failure:
   * **before STT** - no `User audio state -> speaking`, or tiny
     `audio_duration_ms` / very low `audio_rms_dbfs` (below about -50 dBFS):
     microphone, browser, BVC or VAD.
   * **inside STT** - normal duration and level but wrong text, or several
     finals for one sentence: provider configuration (this incident).
   * **after STT** - correct `Transcript final` but `extracted_values` empty:
     extraction / canonicalisation (`clinical_terms`, `sakhi_domain`).
3. Measure instead of guessing:

```bash
# raw STT, on the server (uses the production key; synthetic audio only)
python scripts/stt_accuracy_probe.py --runs 10
# full path through LiveKit + the deployed worker (writes a test call record)
python scripts/e2e_call_probe.py fixture.wav --condition speaker
```

Never run a second worker next to production on this instance: a single call
process reaches ~850 MB-1 GB and the 2 GB t3.small hits the OOM killer
(this killed the production worker once during this investigation).

## Rollback

* `SARVAM_STT_HIGH_VAD_SENSITIVITY=true` restores the old segmentation
  (not recommended).
* `SARVAM_STT_CURRENT_PROTOCOL_ENABLED=false` restores the pre-2026-09-24
  wire protocol (which returned no transcripts at all).
* Emergency provider switch: `STT_PRIMARY=deepgram`.

## Files

* `sarvam_wrappers.py` - high-VAD default off; `[SAKHI_STT_DEBUG]` records
  (`_SegmentAudio`, `_emit_stt_debug`, `STT_DEBUG_SINK`).
* `agent.py` - debug sink wired to `pipeline_event`; NC option removed.
* `tests/test_stt_fragmentation_2026_09_24.py`, `tests/test_sarvam_stt_protocol.py`.
* `scripts/stt_accuracy_probe.py`, `scripts/e2e_call_probe.py`.
