"use client";

/*
 * SakhiAvatarStage - center stage (~42% viewport) for the redesigned rural
 * call screen (SakhiLiveSession.tsx, 2026-08-30).
 *
 * Wraps the existing dual-slot crossfading avatar engine (SakhiVideoAvatar)
 * or, in low-power mode, the CSS/SVG AudioOrb (Module C) - same fallback
 * this app already ships, just re-homed inside the new terracotta-bordered
 * viewport instead of the old page.module.css `.avatarCard`.
 */

import { useState } from "react";
import { motion } from "framer-motion";
import { SakhiVideoAvatar, type AgentVoiceState } from "./SakhiVideoAvatar";
import { AudioOrb } from "./AudioOrb";

export function SakhiAvatarStage({
  voiceState,
  subtitle,
  lowPowerMode = false,
}: {
  voiceState: AgentVoiceState;
  subtitle?: string;
  lowPowerMode?: boolean;
}) {
  const [videoAvatarFailed, setVideoAvatarFailed] = useState(false);
  const speaking = voiceState === "speaking";

  const cleanedSubtitle = subtitle
    ? subtitle
        .replace(/\[\s*VAD\s*:\s*[^\]]+\]/gi, "")
        .replace(/\[.*?\]/g, "")
        .replace(/\(.*?\)/g, "")
        .trim()
    : undefined;

  return (
    // flex-1 + min-h-0, not a fixed h-[42dvh]: the other three sections
    // (header/drawer/reactor) are each sized to their own content and kept
    // shrink-0, so this - the one section with nothing time-critical to
    // read - is what actually yields space when a sibling needs more of it
    // (e.g. LiveIntakeDrawer's name-fallback drawer sliding open). A fixed
    // 42dvh here happened to sum to exactly 100dvh with the other three's
    // ORIGINAL sizes, but silently clipped instead of adapting the moment
    // any one of them grew even slightly - confirmed live in this session.
    <section className="flex min-h-0 w-full flex-1 flex-col items-center justify-center gap-3 overflow-hidden">
      <motion.div
        className="relative h-48 w-48 overflow-hidden rounded-full border-4 border-sand bg-cream shadow-xl md:h-56 md:w-56"
        animate={
          speaking
            ? {
                // Saffron pulse (2026-09-04 palette; was turmeric-amber
                // rgba(217,119,6,*) - this box-shadow is a raw rgba(), not a
                // Tailwind utility, so it didn't inherit the @theme remap in
                // globals.css and needs updating here directly).
                boxShadow: [
                  "0 0 0px rgba(255, 85, 0, 0)",
                  "0 0 35px rgba(255, 85, 0, 0.45)",
                  "0 0 0px rgba(255, 85, 0, 0)",
                ],
              }
            : { boxShadow: "0 0 0px rgba(255, 85, 0, 0)" }
        }
        transition={
          speaking
            ? { duration: 1.8, repeat: Infinity, ease: "easeInOut" }
            : { duration: 0.4 }
        }
      >
        {lowPowerMode ? (
          <AudioOrb voiceState={voiceState} />
        ) : !videoAvatarFailed ? (
          <SakhiVideoAvatar voiceState={voiceState} onFatalError={() => setVideoAvatarFailed(true)} />
        ) : (
          <div className="flex h-full w-full items-center justify-center text-6xl" aria-hidden="true">
            👩‍⚕️
          </div>
        )}
      </motion.div>

      {cleanedSubtitle && (
        <motion.div
          key={cleanedSubtitle}
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          className="max-w-[90%] rounded-full border border-white/60 bg-white/70 px-4 py-2 text-center text-base font-medium text-forest shadow-sm backdrop-blur-md sm:text-lg"
        >
          {cleanedSubtitle}
        </motion.div>
      )}
    </section>
  );
}
