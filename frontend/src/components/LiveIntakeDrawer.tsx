"use client";

/*
 * LiveIntakeDrawer - live clinical data grid + one-tap fallback (~28%
 * viewport) for the redesigned rural call screen (SakhiLiveSession.tsx,
 * 2026-08-30).
 *
 * The actual numeric/boolean edit modal (CorrectionKeypad) is intentionally
 * NOT rendered inside this component - it is a `position: fixed` overlay,
 * and Framer Motion's transform-based slide-in animation on this drawer's
 * own root would otherwise become its CSS containing block (a `transform`
 * on an ancestor makes it the containing block for `position: fixed`
 * descendants), breaking the overlay's full-viewport positioning. Tapping a
 * cell here calls `onOpenEdit`, and SakhiLiveSession renders the keypad at
 * its own top level instead - see that file.
 */

import { motion, AnimatePresence } from "framer-motion";
import { AlertTriangle, Droplet, CalendarDays } from "lucide-react";
import { useState } from "react";
import { useRoomContext } from "@livekit/components-react";

export interface IntakeSlotData {
  bp_systolic?: number;
  bp_diastolic?: number;
  /** Already-localized labels for display, e.g. ["ತಲೆನೋವು", "ಊತ"] - see the
   * brief's SakhiSessionProps.intakeData shape. */
  symptoms: string[];
  gestational_month?: number;
}

/** field-key -> boolean, the same shape sakhi_tools.py's
 * _emit_live_form_sync sends over the wire - needed alongside the
 * already-localized `symptoms` labels above so a flagged sign can be
 * tapped and corrected (an addition beyond the brief's literal
 * intakeData shape; without the raw field keys there is nothing to send
 * back through onManualSlotEdit/submit_correction). */
export type SymptomFlags = Record<string, boolean>;

const SYMPTOM_LABELS: Record<string, string> = {
  headache: "ತಲೆನೋವು",
  blurry_vision: "ಕಣ್ಣು ಮಂಜು",
  swelling: "ಊತ",
  vaginal_bleeding: "ರಕ್ತಸ್ರಾವ",
  severe_abdominal_pain: "ಹೊಟ್ಟೆ ನೋವು",
};

// Two separate return paths rather than one component picking a dynamic
// `Tag` (motion.button vs a plain "div") - a dynamic Tag meant either
// passing Framer Motion props like `whileTap` to a real <div> (React warns:
// "does not recognize the whileTap prop on a DOM element", confirmed live
// in this session) or awkwardly stripping them conditionally. Two plain
// branches are simpler and there is nothing to keep in sync between them
// beyond the shared inner markup, factored into CellBody.
function CellBody({ icon, label, children }: { icon: React.ReactNode; label: string; children: React.ReactNode }) {
  return (
    <>
      <span className="text-forest-light" aria-hidden="true">
        {icon}
      </span>
      <span className="text-xs font-semibold text-forest/70">{label}</span>
      {children}
    </>
  );
}

function Cell({
  icon,
  label,
  onClick,
  children,
}: {
  icon: React.ReactNode;
  label: string;
  onClick?: () => void;
  children: React.ReactNode;
}) {
  const className =
    "flex flex-col items-center justify-center gap-1 rounded-2xl border border-sand bg-white/80 px-2 py-3 text-center shadow-sm";

  if (onClick) {
    return (
      <motion.button type="button" onClick={onClick} whileTap={{ scale: 0.96 }} className={`${className} cursor-pointer active:bg-cream`}>
        <CellBody icon={icon} label={label}>{children}</CellBody>
      </motion.button>
    );
  }

  return (
    <div className={className}>
      <CellBody icon={icon} label={label}>{children}</CellBody>
    </div>
  );
}

export function LiveIntakeDrawer({
  intakeData,
  symptomFlags = {},
  identityFallbackTriggered = false,
  onOpenEdit,
  onNameSubmit,
}: {
  intakeData: IntakeSlotData;
  symptomFlags?: SymptomFlags;
  identityFallbackTriggered?: boolean;
  onOpenEdit: (field: string, value: number | boolean | null) => void;
  onNameSubmit: (name: string, age: number) => void;
}) {
  const hasBp = intakeData.bp_systolic != null && intakeData.bp_diastolic != null;
  const flaggedKeys = Object.keys(symptomFlags).filter((k) => symptomFlags[k]);

  return (
    <section className="flex min-h-0 w-full shrink-0 flex-col justify-center gap-2 py-2 md:h-[28dvh]">
      <div className="grid grid-cols-3 gap-2">
        <Cell
          icon={<Droplet size={20} />}
          label="ರಕ್ತದೊತ್ತಡ"
          onClick={() => onOpenEdit("systolic", intakeData.bp_systolic ?? null)}
        >
          <span className="text-lg font-bold text-forest sm:text-xl">
            {hasBp ? `${intakeData.bp_systolic}/${intakeData.bp_diastolic}` : "--/--"}
          </span>
          {hasBp && <span className="text-[10px] text-forest/50">mmHg</span>}
        </Cell>

        <Cell icon={<AlertTriangle size={20} />} label="ಅಪಾಯದ ಲಕ್ಷಣಗಳು">
          {flaggedKeys.length === 0 ? (
            <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-sm font-semibold text-emerald-700">
              ಯಾವುದೂ ಇಲ್ಲ
            </span>
          ) : (
            <div className="flex flex-wrap justify-center gap-1">
              {flaggedKeys.map((key) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => onOpenEdit(key, true)}
                  className="rounded-full bg-rose-100 px-2 py-0.5 text-xs font-bold text-rose-800"
                >
                  {SYMPTOM_LABELS[key] || key}
                </button>
              ))}
            </div>
          )}
        </Cell>

        <Cell
          icon={<CalendarDays size={20} />}
          label="ತಿಂಗಳು"
          onClick={() => onOpenEdit("gestational_weeks", intakeData.gestational_month ?? null)}
        >
          <span className="text-lg font-bold text-forest sm:text-xl">
            {intakeData.gestational_month != null ? intakeData.gestational_month : "--"}
          </span>
        </Cell>
      </div>

      <NameFallbackDrawer visible={identityFallbackTriggered} onSubmit={onNameSubmit} />
    </section>
  );
}

function NameFallbackDrawer({
  visible,
  onSubmit,
}: {
  visible: boolean;
  /** Called AFTER the "submit_identity" RPC below succeeds - same
   * self-contained-RPC-then-notify-parent pattern IdentityFallbackForm.tsx
   * already used (its `onResolved`), reused here so the parent
   * (SakhiSessionProps.onNameSubmit) only ever hears about a save that
   * actually landed, not a raw keystroke. */
  onSubmit: (name: string, age: number) => void;
}) {
  const room = useRoomContext();
  const [name, setName] = useState("");
  const [age, setAge] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  return (
    <AnimatePresence>
      {visible && (
        <motion.form
          // A height: 0 -> "auto" animation (the usual slide-down recipe)
          // measured this form's natural height wrong in practice - its
          // caption line rendered above the form's own clipped box,
          // confirmed live in this session. A plain opacity/y slide needs
          // no pixel-height measurement at all and sidesteps the bug
          // entirely; SakhiAvatarStage's flex-1 sizing (not this animation)
          // is what actually makes room for this drawer regardless.
          initial={{ opacity: 0, y: -8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -8 }}
          className="rounded-2xl border border-terracotta/40 bg-white/90 px-3"
          onSubmit={async (e) => {
            e.preventDefault();
            const ageNum = Number(age);
            if (!name.trim()) {
              setError("ಹೆಸರು ನಮೂದಿಸಿ (enter the name)");
              return;
            }
            if (!Number.isInteger(ageNum) || ageNum < 10 || ageNum > 60) {
              setError("ವಯಸ್ಸು 10-60 ಇರಬೇಕು (age must be 10-60)");
              return;
            }
            if (!room) {
              setError("Sakhi ಜೊತೆ ಸಂಪರ್ಕವಿಲ್ಲ (not connected)");
              return;
            }
            const agentIdentity = Array.from(room.remoteParticipants.keys())[0];
            if (!agentIdentity) {
              setError("Sakhi ಜೊತೆ ಸಂಪರ್ಕವಿಲ್ಲ (not connected)");
              return;
            }
            setSubmitting(true);
            setError(null);
            try {
              await room.localParticipant.performRpc({
                destinationIdentity: agentIdentity,
                method: "submit_identity",
                payload: JSON.stringify({ name: name.trim(), age: ageNum }),
              });
              onSubmit(name.trim(), ageNum);
            } catch (err) {
              setError(err instanceof Error ? err.message : "ಉಳಿಸಲು ಆಗಲಿಲ್ಲ (could not save)");
            } finally {
              setSubmitting(false);
            }
          }}
        >
          <div className="flex flex-col gap-2 py-3">
            <p className="text-sm font-semibold text-terracotta">
              ಹೆಸರು ಸ್ಪಷ್ಟವಾಗಿಲ್ಲ - ಟೈಪ್ ಮಾಡಿ (name unclear - type it)
            </p>
            <div className="flex gap-2">
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="ಹೆಸರು"
                autoFocus
                disabled={submitting}
                className="min-w-0 flex-[2] rounded-xl border border-sand bg-linen px-3 py-2 text-base"
              />
              <input
                value={age}
                onChange={(e) => setAge(e.target.value)}
                placeholder="ವಯಸ್ಸು"
                inputMode="numeric"
                disabled={submitting}
                className="w-20 rounded-xl border border-sand bg-linen px-3 py-2 text-base"
              />
            </div>
            {error && <p className="text-xs font-medium text-rose-700">{error}</p>}
            <button
              type="submit"
              disabled={submitting}
              className="rounded-xl bg-forest px-4 py-2 text-sm font-bold text-white disabled:opacity-60"
            >
              {submitting ? "ಉಳಿಸಲಾಗುತ್ತಿದೆ… (Saving…)" : "ದಾಖಲಿಸಿ (Save)"}
            </button>
          </div>
        </motion.form>
      )}
    </AnimatePresence>
  );
}
