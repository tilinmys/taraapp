"use client";

/*
 * CallHealthNotice - the one plain-language line that tells an ASHA worker
 * the call is NOT working, and what to do about it.
 *
 * Why it exists: the room connecting and Sakhi actually being there are
 * different events. Production logs (2026-09-22..24) show calls where the
 * room joined, the screen looked live, and then nothing: Sakhi never
 * joined, or joined and never received the worker's microphone audio
 * ("Remote audio wait timed out" on 37 of 46 calls, several of which never
 * recovered). Every one of those looked identical to a healthy call on
 * this screen. This watches the three things the browser can actually see
 * and says so, in her language, with no technical words:
 *
 *   mic-blocked  getUserMedia failed / permission denied
 *   mic-missing  no microphone track published after MIC_GRACE_MS
 *   no-sakhi     Sakhi's participant not ready after JOIN_GRACE_MS, or gone
 *   no-voice     Sakhi is listening but the mic has carried no sound for
 *                SILENT_MIC_MS - wrong input device, or muted by the OS
 *
 * Each issue is reported upward once (onIssue) so the supervisor log rail
 * records it for the admin dashboard.
 */

import { useEffect, useRef, useState } from "react";
import { useLocalParticipant, useTrackVolume } from "@livekit/components-react";
import type { LocalAudioTrack } from "livekit-client";

type Lang = "english" | "kannada" | "hindi";
type Issue = "mic-blocked" | "mic-missing" | "no-sakhi" | "no-voice";

const JOIN_GRACE_MS = 20_000;
const MIC_GRACE_MS = 8_000;
const SILENT_MIC_MS = 25_000;
const VOICE_LEVEL = 0.02;

const PRESENT_STATES = new Set(["idle", "listening", "thinking", "speaking"]);

const COPY: Record<Lang, Record<Issue, string> & { retry: string; end: string }> = {
  english: {
    "mic-blocked": "The microphone is blocked. Allow the microphone for this site, then try again.",
    "mic-missing": "Please allow the microphone when your phone asks.",
    "no-sakhi": "Sakhi is not joining. Please try again.",
    "no-voice": "Sakhi cannot hear you. Hold the phone closer and speak.",
    retry: "Try again",
    end: "End call",
  },
  kannada: {
    "mic-blocked": "ಮೈಕ್ರೊಫೋನ್ ತಡೆಯಲಾಗಿದೆ. ಅನುಮತಿ ನೀಡಿ ಮತ್ತೆ ಪ್ರಯತ್ನಿಸಿ. (Microphone blocked)",
    "mic-missing": "ಫೋನ್ ಕೇಳಿದಾಗ ಮೈಕ್ರೊಫೋನ್ ಅನುಮತಿ ನೀಡಿ. (Allow the microphone)",
    "no-sakhi": "ಸಖಿ ಬರುತ್ತಿಲ್ಲ. ಮತ್ತೆ ಪ್ರಯತ್ನಿಸಿ. (Sakhi is not joining)",
    "no-voice": "ಸಖಿಗೆ ನಿಮ್ಮ ಧ್ವನಿ ಕೇಳುತ್ತಿಲ್ಲ. ಫೋನ್ ಹತ್ತಿರ ಹಿಡಿದು ಮಾತನಾಡಿ. (Sakhi cannot hear you)",
    retry: "ಮತ್ತೆ ಪ್ರಯತ್ನಿಸಿ (Try again)",
    end: "ಕರೆ ಮುಗಿಸಿ (End call)",
  },
  hindi: {
    "mic-blocked": "माइक्रोफ़ोन बंद है। अनुमति दें और फिर से कोशिश करें। (Microphone blocked)",
    "mic-missing": "फ़ोन पूछे तो माइक्रोफ़ोन की अनुमति दें। (Allow the microphone)",
    "no-sakhi": "सखी नहीं जुड़ रही है। फिर से कोशिश करें। (Sakhi is not joining)",
    "no-voice": "सखी को आपकी आवाज़ नहीं आ रही। फ़ोन पास रखकर बोलें। (Sakhi cannot hear you)",
    retry: "फिर से कोशिश करें (Try again)",
    end: "कॉल समाप्त करें (End call)",
  },
};

/** Pure decision, exported for tests: which issue (if any) to show now. */
export function callIssue(input: {
  elapsedMs: number;
  micBlocked: boolean;
  micPublished: boolean;
  agentState: string;
  everPresent: boolean;
  silentForMs: number;
}): Issue | null {
  const present = PRESENT_STATES.has(input.agentState);
  if (input.micBlocked) return "mic-blocked";
  if (!input.micPublished && input.elapsedMs >= MIC_GRACE_MS) return "mic-missing";
  if (!present && (input.everPresent || input.elapsedMs >= JOIN_GRACE_MS)) return "no-sakhi";
  if (present && input.micPublished && input.silentForMs >= SILENT_MIC_MS) return "no-voice";
  return null;
}

export function CallHealthNotice({
  language,
  agentState,
  micBlocked,
  onRetry,
  onEndCall,
  onIssue,
}: {
  language: Lang;
  agentState: string;
  micBlocked: boolean;
  onRetry: () => void;
  onEndCall: () => void;
  onIssue?: (issue: Issue) => void;
}) {
  const { microphoneTrack } = useLocalParticipant();
  const micLevel = useTrackVolume(microphoneTrack?.track as LocalAudioTrack | undefined);

  // Latest inputs for the 1s check below; written in effects, read in it.
  const latest = useRef({ agentState, micBlocked, micPublished: false, micLevel: 0 });
  useEffect(() => {
    latest.current = { agentState, micBlocked, micPublished: Boolean(microphoneTrack), micLevel };
  }, [agentState, micBlocked, microphoneTrack, micLevel]);

  const onIssueRef = useRef(onIssue);
  useEffect(() => {
    onIssueRef.current = onIssue;
  }, [onIssue]);

  const [issue, setIssue] = useState<Issue | null>(null);

  useEffect(() => {
    const startedAt = Date.now();
    let lastVoiceAt = startedAt;
    let everPresent = false;
    const reported = new Set<Issue>();

    const check = () => {
      const now = Date.now();
      const s = latest.current;
      const present = PRESENT_STATES.has(s.agentState);
      if (present) everPresent = true;
      // Sakhi's own turns, and time before she is here, are not "no voice".
      if (s.micLevel > VOICE_LEVEL || !present || s.agentState === "speaking") lastVoiceAt = now;

      const next = callIssue({
        elapsedMs: now - startedAt,
        micBlocked: s.micBlocked,
        micPublished: s.micPublished,
        agentState: s.agentState,
        everPresent,
        silentForMs: now - lastVoiceAt,
      });
      setIssue(next);
      if (next && !reported.has(next)) {
        reported.add(next);
        onIssueRef.current?.(next);
      }
    };
    const id = window.setInterval(check, 1000);
    return () => window.clearInterval(id);
  }, []);

  if (!issue) return null;
  const copy = COPY[language] ?? COPY.kannada;
  const canRetry = issue === "no-sakhi" || issue === "mic-blocked";

  return (
    <div
      role="alert"
      className="absolute inset-x-3 top-14 z-20 rounded-2xl border border-rose-300 bg-rose-50 px-4 py-3 text-center shadow-lg"
    >
      <p className="m-0 text-base font-semibold text-rose-900">{copy[issue]}</p>
      {canRetry && (
        <div className="mt-3 flex justify-center gap-2">
          <button
            type="button"
            onClick={onRetry}
            className="rounded-full bg-forest px-5 py-2 text-sm font-bold text-white"
          >
            {copy.retry}
          </button>
          <button
            type="button"
            onClick={onEndCall}
            className="rounded-full border border-forest/30 bg-white px-5 py-2 text-sm font-semibold text-forest"
          >
            {copy.end}
          </button>
        </div>
      )}
    </div>
  );
}
