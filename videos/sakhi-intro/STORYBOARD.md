---
format: 1920x1080
duration: 58s
message: "Sakhi is a warm, careful voice co-pilot that helps ASHA workers screen mothers for pregnancy risk over the phone - she listens, records real clinical data, and escalates calmly when something looks serious, but she never replaces a doctor."
arc: Greeting → What she does → Trust (never guesses, always escalates) → Boundary (not a doctor) → Warmth (extra pair of hands) → Close (her name means friend)
audience: "people being introduced to or evaluating the product - warm and human, not feature-listy"
mode: autonomous
---

Single-file composition (`index.html`) - one continuous timeline, real avatar
clips cycled/crossfaded per beat rather than separate sub-compositions per
frame (built inline; below ~6 scenes, per production-loop guidance this is
faster than dispatch packeting). `src` is `index.html` for every frame below;
timings are the frame's window on the shared timeline.

## Frame 1 — Greeting
- status: built
- src: index.html
- duration: 3.2s
- transition_in: cut
- scene: namaskara.mp4 plays once, full length - her real greeting gesture
- voiceover: "Namaskara. I am Sakhi,"

Her real on-screen avatar performs its one-shot namaskara (greeting) gesture
against the soft mint radial-glow background. This is the only "cold open" -
everything after crossfades.

## Frame 2 — What she does
- status: built
- src: index.html
- duration: 14.9s
- transition_in: crossfade
- scene: talk_loop 1/2 cycling; caption cards "For ASHA workers in rural Karnataka" then "Kannada · Hindi · English"
- voiceover: "a voice companion built to walk beside ASHA workers across rural Karnataka...gently guide the conversation. Her name, her age...the symptoms that matter."

Establishes who she's for and what a call looks like. Two small IBM Plex
Mono caption tags surface the key claims a viewer should read, not just hear.

## Frame 3 — Trust (never guesses, always escalates)
- status: built
- src: index.html
- duration: 7.98s
- transition_in: crossfade
- scene: talk_loop continues; larger title-card overlay "Never guesses. Always escalates." with scrim, held for the full beat
- voiceover: "I never rush her, and I never guess. If a reading looks serious, I flag it right away and connect a specialist, calmly, before it becomes an emergency."

The most important trust-building line in the whole piece - given real visual
weight (a held title card, not just talking-head) per the brief.

## Frame 4 — Boundary (real record, not a doctor)
- status: built
- src: index.html
- duration: 8.59s
- transition_in: crossfade
- scene: idle_loop (calmer register for a reflective claim)
- voiceover: "Every answer I collect becomes a real clinical record...I am not a replacement for a doctor."

Register drops slightly (idle instead of talk) - she's stating a boundary,
not actively guiding a conversation.

## Frame 5 — Warmth (extra pair of hands)
- status: built
- src: index.html
- duration: 6.75s
- transition_in: crossfade
- scene: talk_loop cycling (returns to active/warm energy)
- voiceover: "I am the extra pair of hands, the steady voice, the safety net for the ASHA worker doing the hardest job in the room."

## Frame 6 — Close (her name means friend)
- status: built
- src: index.html
- duration: 4.93s
- transition_in: crossfade
- scene: idle_loop, calm
- voiceover: "My name means friend. That is what I try to be, one call at a time."

## Frame 7 — Title card
- status: built
- src: index.html
- duration: 11.65s
- transition_in: crossfade
- scene: idle_loop continues softly under a scrim; Montserrat title lockup "Sakhi — a voice companion for ASHA workers." fades/settles in and holds
- voiceover: (none - narration has ended)

Generous hold (unhurried pacing, per design.md) rather than a snap cut to
black. The video's real audio track (46.35s) has already finished; this
frame is the silent close.
