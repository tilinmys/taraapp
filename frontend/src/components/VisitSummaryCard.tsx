"use client";

/*
 * End-of-visit MCP/RCH-shaped summary (Module C, 2026-08-30).
 *
 * Fetched from /api/reports/mcp-card?session_id=<room name> - the LiveKit
 * room name is already known to page.tsx right after end_call, so no new
 * id needs to round-trip back from the backend first. The backend write
 * (save_call_report, agent.py) happens during call teardown, so this is
 * only asked for ~10s after end_call (see page.tsx) to give that a moment
 * to land; a "not ready yet" 404 in that window is expected, not an error.
 *
 * DRAFT: status_server.mcp_card_for_report()'s field mapping has not been
 * checked against the real physical MCP/RCH register card - see that
 * function's docstring. This is a reasonable on-screen visit summary, not a
 * verified reproduction of the government form.
 */

import { useEffect, useState } from "react";

interface McpCard {
  mother_name: string | null;
  age: number | null;
  village: string | null;
  phone: string | null;
  gestational_weeks: number | null;
  blood_pressure: string | null;
  danger_signs: string[];
  risk_category: string | null;
  referral_status: string | null;
  referral_trigger: string | null;
  questions_answered: string;
  visit_date: string | null;
  language: string | null;
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 12, padding: "6px 0", borderBottom: "1px solid #eee" }}>
      <span style={{ color: "#475569", fontSize: 13 }}>{label}</span>
      <span style={{ fontWeight: 600, fontSize: 14, textAlign: "right" }}>{value ?? "—"}</span>
    </div>
  );
}

export function VisitSummaryCard({ sessionId, onClose }: { sessionId: string; onClose: () => void }) {
  const [card, setCard] = useState<McpCard | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`/api/reports/mcp-card?session_id=${encodeURIComponent(sessionId)}`, {
          cache: "no-store",
        });
        const data = await res.json();
        if (cancelled) return;
        if (!res.ok) {
          setError(data?.error === "report not found"
            ? "Visit summary is still being saved - please try again in a few seconds."
            : (data?.error || "Could not load the visit summary."));
        } else {
          setCard(data);
        }
      } catch (e: any) {
        if (!cancelled) setError(e?.message || "Could not load the visit summary.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [sessionId]);

  return (
    <div
      role="dialog"
      aria-label="Visit summary"
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
          maxWidth: 420,
          maxHeight: "85vh",
          overflowY: "auto",
          padding: 22,
          borderRadius: 16,
          background: "white",
          boxShadow: "0 12px 40px rgba(0,0,0,0.25)",
        }}
      >
        <p style={{ margin: "0 0 4px", fontWeight: 700, fontSize: 17 }}>Visit summary</p>
        <p style={{ margin: "0 0 16px", fontSize: 12, color: "#94a3b8" }}>
          Draft MCP/RCH-style card — verify against the printed register before filing.
        </p>

        {loading && <p style={{ fontSize: 14, color: "#475569" }}>Loading…</p>}
        {error && !loading && <p style={{ fontSize: 14, color: "#b91c1c" }}>{error}</p>}

        {card && !loading && !error && (
          <div>
            <Row label="Mother's name" value={card.mother_name} />
            <Row label="Age" value={card.age} />
            <Row label="Village" value={card.village} />
            <Row label="Phone" value={card.phone} />
            <Row label="Gestational age" value={card.gestational_weeks ? `${card.gestational_weeks} weeks` : null} />
            <Row label="Blood pressure" value={card.blood_pressure} />
            <Row
              label="Danger signs"
              value={card.danger_signs.length ? card.danger_signs.join(", ") : "None reported"}
            />
            <Row label="Risk category" value={card.risk_category} />
            <Row label="Referral" value={card.referral_status} />
            {card.referral_trigger && <Row label="Referral reason" value={card.referral_trigger} />}
            <Row label="Questions answered" value={card.questions_answered} />
            <Row label="Visit date" value={card.visit_date} />
          </div>
        )}

        <button
          type="button"
          onClick={onClose}
          style={{
            marginTop: 18,
            width: "100%",
            padding: "12px 10px",
            borderRadius: 10,
            border: "none",
            background: "#0f766e",
            color: "white",
            fontWeight: 600,
            fontSize: 15,
            cursor: "pointer",
          }}
        >
          Close
        </button>
      </div>
    </div>
  );
}
