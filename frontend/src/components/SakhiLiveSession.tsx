"use client";

/*
 * SakhiLiveSession - redesigned rural call screen (2026-08-30).
 *
 * Replaces the ad-hoc slate/grey in-call markup previously inlined in
 * page.tsx (AvatarStage + StatusPill + IdentityFallbackForm + controls)
 * with a warm-linen, single-screen, mobile-first layout built from four
 * fixed-proportion sections (SakhiHeader 10% / SakhiAvatarStage 42% /
 * LiveIntakeDrawer 28% / AshaVoiceReactor 20dvh min) so an ASHA worker's
 * Android phone never has to scroll mid-call.
 *
 * Architecture note on the two mutation callbacks (onManualSlotEdit,
 * onNameSubmit): both existing correction paths this app already ships
 * (CorrectionKeypad's "submit_correction" RPC, and the name-fallback
 * drawer's "submit_identity" RPC, both inside LiveIntakeDrawer/this file)
 * perform their own RPC call and only report upward on SUCCESS - the same
 * pattern the pre-redesign IdentityFallbackForm already used (its
 * `onResolved`). So these two props fire once a correction/identity save
 * has actually landed, not on every keystroke - they exist so the parent
 * (page.tsx) can keep its own optimistic UI state and identity-fallback
 * flag in sync, not so the parent has to perform the RPC itself.
 *
 * This component always renders inside a live <LiveKitRoom> (mounted by
 * page.tsx exactly where the old AvatarStage/StatusPill used to be) - the
 * LiveKit hooks used directly here and in its children (useVoiceAssistant,
 * useLocalParticipant, useTrackVolume, useRoomContext) all depend on that.
 */

import { useState } from "react";
import { X } from "lucide-react";
import { useVoiceAssistant } from "@livekit/components-react";
import { SakhiHeader, type LangKey, type TriageRisk } from "./SakhiHeader";
import { SakhiAvatarStage } from "./SakhiAvatarStage";
import { LiveIntakeDrawer, type IntakeSlotData, type SymptomFlags } from "./LiveIntakeDrawer";
import { AshaVoiceReactor } from "./AshaVoiceReactor";
import { CorrectionKeypad } from "./CorrectionKeypad";
import { CallHealthNotice } from "./CallHealthNotice";

export type { LangKey };
export type AgentState = "idle" | "listening" | "thinking" | "speaking" | "connecting";

export interface SakhiSessionProps {
  language: LangKey;
  motherName?: string;
  /** Not in the brief's literal interface, but SakhiHeader's own spec
   * ("ರತ್ನಮ್ಮ (24y)") needs it alongside the name - added rather than
   * baking a fake age into a formatted string the parent would have to
   * pre-render. */
  motherAge?: number;
  triageRisk: TriageRisk;
  /** Optional override, mainly for stories/screenshots. In real use this is
   * NOT something page.tsx can supply: `useVoiceAssistant()` only works
   * inside a mounted <LiveKitRoom>, and page.tsx's own top-level component
   * renders both the pre-call and in-call screens, so it sits OUTSIDE that
   * context and has no agentState of its own to hand down (the pre-redesign
   * AvatarStage/StatusPill worked around exactly this by each calling
   * useVoiceAssistant() themselves, rather than lifting it to page.tsx).
   * SakhiLiveSession does the same here - it reads the live value itself
   * and only falls back to this prop when explicitly given one. */
  agentState?: AgentState;
  intakeData: IntakeSlotData;
  /** Raw field-key -> boolean, alongside intakeData.symptoms' already-
   * localized labels - see LiveIntakeDrawer's SymptomFlags doc for why
   * both shapes are needed. Optional: omitting it just means danger-sign
   * chips render but are not individually tappable. */
  symptomFlags?: SymptomFlags;
  /** Drives the slide-down name-fallback drawer inside LiveIntakeDrawer. */
  identityFallbackTriggered?: boolean;
  /** Module C (2026-08-30), carried over from the previous design - true
   * shows the CSS/SVG AudioOrb instead of the video avatar. Optional,
   * defaults to false (video avatar), since the brief's own interface
   * doesn't mention it but removing an already-shipped battery/heat
   * mitigation for older Android phones would be a regression. */
  lowPowerMode?: boolean;
  onManualSlotEdit: (slot: string, value: number | boolean) => void;
  onNameSubmit: (name: string, age: number) => void;
  onEndCall: () => void;
  /** Microphone permission/device failure reported by LiveKitRoom. */
  micBlocked?: boolean;
  /** End this call and start a fresh one (CallHealthNotice's button). */
  onRetry?: () => void;
  /** A call problem CallHealthNotice detected, for the supervisor log. */
  onHealthIssue?: (issue: string) => void;
}

export function SakhiLiveSession({
  language,
  motherName,
  motherAge,
  triageRisk,
  agentState: agentStateOverride,
  intakeData,
  symptomFlags,
  identityFallbackTriggered = false,
  lowPowerMode = false,
  onManualSlotEdit,
  onNameSubmit,
  onEndCall,
  micBlocked = false,
  onRetry,
  onHealthIssue,
}: SakhiSessionProps) {
  const { state: liveAgentState, agentTranscriptions } = useVoiceAssistant();
  const agentState = agentStateOverride ?? liveAgentState;
  const rawSubtitle = agentTranscriptions?.[agentTranscriptions.length - 1]?.text;
  const subtitle = rawSubtitle
    ? rawSubtitle
        .replace(/\[\s*VAD\s*:\s*[^\]]+\]/gi, "")
        .replace(/\[.*?\]/g, "")
        .replace(/\(.*?\)/g, "")
        .trim()
    : undefined;

  const [editing, setEditing] = useState<{ field: string; value: number | boolean | null } | null>(null);

  return (
    // h-full, not h-[100dvh]: the parent <main> (page.tsx's .stageLive) is
    // itself sized to exactly 100dvh - this fills that box rather than
    // measuring its own 100dvh, which would double-count against the log
    // rail's own height on the single-column mobile layout and force page
    // scroll, the exact thing the mobile-first single-screen brief for this
    // component was written to avoid.
    <div className="relative flex h-full w-full flex-col justify-between overflow-hidden bg-linen p-4">
      <button
        type="button"
        onClick={onEndCall}
        aria-label="End call"
        className="absolute right-3 top-3 z-10 flex h-9 w-9 items-center justify-center rounded-full bg-terracotta text-white shadow-md"
      >
        <X size={18} />
      </button>

      <CallHealthNotice
        language={language}
        agentState={agentState}
        micBlocked={micBlocked}
        onRetry={onRetry ?? onEndCall}
        onEndCall={onEndCall}
        onIssue={onHealthIssue}
      />

      <SakhiHeader
        language={language}
        motherName={motherName}
        motherAge={motherAge}
        triageRisk={triageRisk}
      />

      <SakhiAvatarStage voiceState={agentState} subtitle={subtitle} lowPowerMode={lowPowerMode} />

      <LiveIntakeDrawer
        intakeData={intakeData}
        symptomFlags={symptomFlags}
        identityFallbackTriggered={identityFallbackTriggered}
        onOpenEdit={(field, value) => setEditing({ field, value })}
        onNameSubmit={onNameSubmit}
      />

      <AshaVoiceReactor agentState={agentState} />

      {editing && (
        <CorrectionKeypad
          field={editing.field}
          currentValue={editing.value}
          onClose={() => setEditing(null)}
          onCorrected={(field, value) => onManualSlotEdit(field, value)}
        />
      )}
    </div>
  );
}
