"use client";

/*
 * AshaVoiceReactor - bottom voice-reactive column (~20% viewport) for the
 * redesigned rural call screen (SakhiLiveSession.tsx, 2026-08-30).
 *
 * 2026-09-03 rewrite: this used to be a tappable mic mute/unmute button.
 * Removed entirely - a real field report was that the mic read as "always
 * muted" and the agent never heard anything. The actual bug was upstream
 * (getUserMedia unavailable over a non-secure HTTP origin, now fixed by
 * serving over HTTPS - see deploy/aws/README.md's "Known gaps" section) and
 * had nothing to do with this button, but a manual mute control is also a
 * genuine hazard for this app's actual users: an ASHA worker mid-screening,
 * on a phone, under time pressure, one stray tap from silencing herself
 * with no clear way to notice. There is no clinical or UX reason this call
 * ever needs to be self-muted - removing the control removes that failure
 * mode entirely, not just its confusing bug.
 *
 * What replaces it: a single column that grows and glows with whichever
 * side of the conversation is actually making sound - the ASHA worker's own
 * mic (useLocalParticipant + useTrackVolume, as before) OR Sakhi's own
 * speech (useVoiceAssistant's audioTrack, fed through the same
 * useTrackVolume hook). Purely reflects reality; nothing to tap, nothing to
 * get wrong.
 */

import { motion } from "framer-motion";
import { useLocalParticipant, useTrackVolume, useVoiceAssistant } from "@livekit/components-react";
import type { LocalAudioTrack, RemoteAudioTrack } from "livekit-client";
import type { AgentVoiceState } from "./SakhiVideoAvatar";

// Every state livekit's useVoiceAssistant can report. The fallback used to
// be "Speak anytime", so while Sakhi had not yet joined ("connecting",
// "initializing", "disconnected" were missing here) the screen told the ASHA
// worker to talk to no one.
const JOINING = "ಸಖಿ ಬರುತ್ತಿದ್ದಾಳೆ... (Sakhi is joining)";
const STATE_LABEL: Record<string, string> = {
  listening: "ಕೇಳಿಸಿಕೊಳ್ಳುತ್ತಿದ್ದೇನೆ... (Listening)",
  thinking: "ಪರಿಶೀಲಿಸಲಾಗುತ್ತಿದೆ... (Processing)",
  speaking: "ಸಖಿ ಮಾತನಾಡುತ್ತಿದ್ದಾಳೆ (Speaking)",
  idle: "ಮಾತನಾಡಿ (Speak anytime)",
  connecting: JOINING,
  initializing: JOINING,
  "pre-connect-buffering": JOINING,
  disconnected: JOINING,
  failed: "ಸಂಪರ್ಕ ತಪ್ಪಿದೆ (Connection lost)",
};

export function AshaVoiceReactor({ agentState }: { agentState: AgentVoiceState }) {
  const { microphoneTrack } = useLocalParticipant();
  const { audioTrack: agentAudioTrack } = useVoiceAssistant();

  const localLevel = useTrackVolume(microphoneTrack?.track as LocalAudioTrack | undefined);
  const agentLevel = useTrackVolume(agentAudioTrack?.publication?.track as RemoteAudioTrack | undefined);

  // Raw RMS is small for normal speech - amplified and clamped so the
  // column visibly reacts without needing a shout. Whichever side is
  // currently louder drives the column; there is no need to distinguish
  // who is making the sound, only that the call is alive and audible.
  const level = Math.min(1, Math.max(localLevel, agentLevel) * 4);

  return (
    <section
      className="flex h-[20dvh] min-h-32 w-full shrink-0 flex-col items-center justify-center gap-3"
      role="img"
      aria-label={STATE_LABEL[agentState] || JOINING}
    >
      {/* Saffron accent (2026-09-04 palette): this column is the one
          element on screen whose whole job is "the call is alive and
          audible right now" - the brand accent colour is the right choice
          for it, where the navy ink above is for static text/chrome. */}
      <motion.div
        className="w-10 rounded-full bg-terracotta shadow-2xl"
        animate={{
          height: 56 + level * 64,
          opacity: 0.75 + level * 0.25,
        }}
        transition={{ duration: 0.12, ease: "easeOut" }}
      />

      <p className="text-center text-sm font-semibold text-forest sm:text-base">
        {STATE_LABEL[agentState] || JOINING}
      </p>
    </section>
  );
}
