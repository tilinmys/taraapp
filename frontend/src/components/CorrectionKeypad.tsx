"use client";

/*
 * One-tap field correction overlay (Module C, 2026-08-30).
 *
 * Opened by tapping a Live Form Sync chip in page.tsx. Two shapes:
 *   - numeric fields (systolic/diastolic/gestational_weeks): a plain numeric
 *     keypad, since typing "one forty" on a phone thumb-keyboard is exactly
 *     the friction this exists to remove.
 *   - boolean symptom fields: a yes/no toggle - there is no number to type.
 *
 * Same RPC pattern as IdentityFallbackForm.tsx (room.localParticipant.
 * performRpc against a method the agent registers - here, "submit_correction"
 * / agent.py's _handle_submit_correction, which delegates the actual
 * whitelist+mutation+retriage to ClinicalToolset.apply_field_correction in
 * sakhi_tools.py). Kept as its own overlay rather than inline-editable chips
 * because a fat-fingered edit on a live clinical value needs a deliberate,
 * separate "confirm" tap - not a bare input box that commits on blur.
 */

import { useState } from "react";
import { useRoomContext } from "@livekit/components-react";

const NUMERIC_FIELDS = new Set(["systolic", "diastolic", "gestational_weeks"]);

const FIELD_LABELS: Record<string, string> = {
  systolic: "Systolic BP",
  diastolic: "Diastolic BP",
  gestational_weeks: "Gestational age (weeks)",
  headache: "Headache",
  blurry_vision: "Blurred vision",
  swelling: "Swelling",
  vaginal_bleeding: "Vaginal bleeding",
  severe_abdominal_pain: "Severe abdominal pain",
};

export function CorrectionKeypad({
  field,
  currentValue,
  onClose,
  onCorrected,
}: {
  field: string;
  currentValue: number | boolean | null;
  onClose: () => void;
  onCorrected: (field: string, value: number | boolean) => void;
}) {
  const room = useRoomContext();
  const isNumeric = NUMERIC_FIELDS.has(field);
  const [text, setText] = useState(
    isNumeric && typeof currentValue === "number" ? String(currentValue) : ""
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (value: number | boolean) => {
    if (!room) {
      setError("Not connected to Sakhi.");
      return;
    }
    const agentIdentity = Array.from(room.remoteParticipants.keys())[0];
    if (!agentIdentity) {
      setError("Not connected to Sakhi yet - try again in a moment.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await room.localParticipant.performRpc({
        destinationIdentity: agentIdentity,
        method: "submit_correction",
        payload: JSON.stringify({ field, value }),
      });
      onCorrected(field, value);
      onClose();
    } catch (err: any) {
      setError(err?.message || "Could not save the correction - please try again.");
    } finally {
      setSubmitting(false);
    }
  };

  const submitNumeric = (e: React.FormEvent) => {
    e.preventDefault();
    const num = Number(text);
    if (!Number.isFinite(num)) {
      setError("Enter a number.");
      return;
    }
    submit(num);
  };

  return (
    <div
      role="alertdialog"
      aria-label={`Correct ${FIELD_LABELS[field] || field}`}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(15, 23, 42, 0.45)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 50,
        padding: 16,
      }}
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          width: "100%",
          maxWidth: 340,
          padding: 20,
          borderRadius: 16,
          background: "white",
          boxShadow: "0 12px 40px rgba(0,0,0,0.25)",
        }}
      >
        <p style={{ margin: "0 0 14px", fontWeight: 700, fontSize: 16 }}>
          Correct: {FIELD_LABELS[field] || field}
        </p>

        {isNumeric ? (
          <form onSubmit={submitNumeric} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <input
              value={text}
              onChange={(e) => setText(e.target.value.replace(/[^0-9]/g, ""))}
              inputMode="numeric"
              autoFocus
              disabled={submitting}
              style={{
                padding: "14px 12px",
                borderRadius: 10,
                border: "2px solid #cbd5e1",
                fontSize: 24,
                textAlign: "center",
                letterSpacing: "0.05em",
              }}
            />
            {error && <p style={{ color: "#b91c1c", margin: 0, fontSize: 13 }}>{error}</p>}
            <div style={{ display: "flex", gap: 8 }}>
              <button
                type="button"
                onClick={onClose}
                disabled={submitting}
                style={cancelButtonStyle}
              >
                Cancel
              </button>
              <button type="submit" disabled={submitting} style={confirmButtonStyle}>
                {submitting ? "Saving…" : "Save"}
              </button>
            </div>
          </form>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {error && <p style={{ color: "#b91c1c", margin: 0, fontSize: 13 }}>{error}</p>}
            <div style={{ display: "flex", gap: 8 }}>
              <button
                type="button"
                disabled={submitting}
                onClick={() => submit(false)}
                style={{ ...cancelButtonStyle, fontWeight: currentValue === false ? 700 : 500 }}
              >
                No
              </button>
              <button
                type="button"
                disabled={submitting}
                onClick={() => submit(true)}
                style={{ ...confirmButtonStyle, fontWeight: currentValue === true ? 700 : 500 }}
              >
                Yes
              </button>
            </div>
            <button
              type="button"
              onClick={onClose}
              disabled={submitting}
              style={{ ...cancelButtonStyle, marginTop: 4 }}
            >
              Cancel
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

const cancelButtonStyle: React.CSSProperties = {
  flex: 1,
  padding: "12px 10px",
  borderRadius: 10,
  border: "1px solid #cbd5e1",
  background: "white",
  fontSize: 15,
  cursor: "pointer",
};

const confirmButtonStyle: React.CSSProperties = {
  flex: 1,
  padding: "12px 10px",
  borderRadius: 10,
  border: "none",
  background: "#0f766e",
  color: "white",
  fontWeight: 600,
  fontSize: 15,
  cursor: "pointer",
};
