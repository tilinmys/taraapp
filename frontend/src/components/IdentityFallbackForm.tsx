"use client";

/*
 * Typed name/age fallback, shown after one failed voice lookup.
 *
 * Armed by the backend (sakhi_tools.py's search_patient, lookup_attempts>=1
 * branch): it sets state.awaiting_identity_box and emits a pipeline_event
 * labelled "Identity fallback: show text box", which page.tsx's existing
 * log-poll loop already watches for (same pattern as the intake-progress
 * events) and turns into the `visible` prop below - ~1.2s worst-case lag,
 * accepted deliberately since this isn't a per-turn-critical signal.
 *
 * Submission is the one direction that genuinely needed a new channel: there
 * is no other way to get typed text into a live call. Uses LiveKit RPC
 * (room.localParticipant.performRpc), calling the "submit_identity" method
 * the agent registers in agent.py right after AgentSession construction.
 * Nothing in this codebase used RPC before this - both SDKs already shipped
 * it, unused.
 */

import { useState } from "react";
import { useRoomContext } from "@livekit/components-react";

export function IdentityFallbackForm({
  visible,
  onResolved,
}: {
  visible: boolean;
  onResolved: () => void;
}) {
  const room = useRoomContext();
  const [name, setName] = useState("");
  const [age, setAge] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!visible) return null;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!room) return;
    setError(null);

    const trimmedName = name.trim();
    const ageNum = Number(age);
    if (!trimmedName) {
      setError("Enter the mother's name.");
      return;
    }
    if (!Number.isInteger(ageNum) || ageNum < 10 || ageNum > 60) {
      setError("Age must be a number between 10 and 60.");
      return;
    }

    // Two-party call: the agent is the only remote participant.
    const agentIdentity = Array.from(room.remoteParticipants.keys())[0];
    if (!agentIdentity) {
      setError("Not connected to Sakhi yet - try again in a moment.");
      return;
    }

    setSubmitting(true);
    try {
      await room.localParticipant.performRpc({
        destinationIdentity: agentIdentity,
        method: "submit_identity",
        payload: JSON.stringify({ name: trimmedName, age: ageNum }),
      });
      onResolved();
    } catch (err: any) {
      setError(err?.message || "Could not save - please try again.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      role="alertdialog"
      aria-label="Enter mother's name and age"
      style={{
        width: "100%",
        maxWidth: 420,
        margin: "0 auto",
        padding: 18,
        borderRadius: 16,
        border: "2px solid #0f766e",
        background: "#f0fdfa",
      }}
    >
      <p style={{ margin: "0 0 10px", fontWeight: 600, fontSize: 15 }}>
        Sakhi didn&apos;t catch the name clearly. Please type it in:
      </p>
      <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Mother's full name"
          autoFocus
          disabled={submitting}
          style={{ padding: "10px 12px", borderRadius: 10, border: "1px solid #cbd5c0", fontSize: 16 }}
        />
        <input
          value={age}
          onChange={(e) => setAge(e.target.value)}
          placeholder="Age"
          inputMode="numeric"
          disabled={submitting}
          style={{ padding: "10px 12px", borderRadius: 10, border: "1px solid #cbd5c0", fontSize: 16 }}
        />
        {error && <p style={{ color: "#b91c1c", margin: 0, fontSize: 13 }}>{error}</p>}
        <button
          type="submit"
          disabled={submitting}
          style={{
            padding: "10px 16px",
            borderRadius: 10,
            border: "none",
            background: "#0f766e",
            color: "white",
            fontWeight: 600,
            fontSize: 15,
            cursor: submitting ? "default" : "pointer",
          }}
        >
          {submitting ? "Saving…" : "Continue"}
        </button>
      </form>
    </div>
  );
}
