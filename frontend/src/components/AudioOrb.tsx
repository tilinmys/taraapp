"use client";

/*
 * AudioOrb — the low-power alternative to SakhiVideoAvatar (Module C,
 * 2026-08-30).
 *
 * Why this exists: SakhiVideoAvatar crossfades between two <video> elements
 * running continuously for the whole call. Fine on a decent phone; on the
 * cheap, hot, battery-starved Android device this whole app is built for
 * (see page.tsx's header comment), two concurrent video decoders is a real
 * cost with no visual payoff the worker asked for. This component decodes
 * nothing - it is CSS transitions on a handful of SVG circles, driven by the
 * same four-state voiceState signal SakhiVideoAvatar already receives.
 *
 * Deliberately voiceState-driven, not a real audio-level/RMS tap: a Web
 * Audio AnalyserNode wired off RoomAudioRenderer's element would react a
 * little more finely, but it is new surface area (another thing to keep
 * working across LiveKit/browser versions) for a purely cosmetic gain on a
 * component whose entire point is to do less, not more. voiceState
 * (listening/thinking/speaking/idle) is coarser but already reliable - see
 * SakhiVideoAvatar's own AgentVoiceState type, reused as-is here.
 *
 * No requestAnimationFrame/Date.now() loop, same discipline as
 * SakhiVideoAvatar - everything below is a CSS animation or a plain
 * transition, paused entirely under prefers-reduced-motion.
 */

import { useEffect, useState } from "react";
import type { AgentVoiceState } from "./SakhiVideoAvatar";
import styles from "./AudioOrb.module.css";

function usePrefersReducedMotion(): boolean {
  // Lazy initializer, not an effect + setState: this only ever renders
  // behind a client-only gate (inside a live LiveKitRoom - see page.tsx's
  // AvatarStage), so there is no SSR/hydration mismatch to worry about, and
  // it avoids the flash of full-motion styles a post-render effect would
  // otherwise cause for someone who has actually asked for reduced motion.
  const [reduced, setReduced] = useState(() => {
    if (typeof window === "undefined" || !window.matchMedia) return false;
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  });
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const onChange = () => setReduced(mq.matches);
    mq.addEventListener?.("change", onChange);
    return () => mq.removeEventListener?.("change", onChange);
  }, []);
  return reduced;
}

export function AudioOrb({ voiceState }: { voiceState: AgentVoiceState }) {
  const reducedMotion = usePrefersReducedMotion();
  const state: "listening" | "thinking" | "speaking" | "idle" =
    voiceState === "listening" || voiceState === "thinking" || voiceState === "speaking"
      ? voiceState
      : "idle";

  return (
    <div
      className={styles.stage}
      data-state={state}
      data-motion={reducedMotion ? "reduced" : "full"}
      role="img"
      aria-label={`Sakhi is ${state}`}
    >
      <svg viewBox="0 0 200 200" className={styles.orb} aria-hidden="true">
        <circle className={styles.ringOuter} cx="100" cy="100" r="86" />
        <circle className={styles.ringMid} cx="100" cy="100" r="66" />
        <circle className={styles.core} cx="100" cy="100" r="46" />
      </svg>
      <p className={styles.caption}>Low-power mode</p>
    </div>
  );
}
