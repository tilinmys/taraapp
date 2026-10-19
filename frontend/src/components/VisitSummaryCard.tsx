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
  const [savingNotice, setSavingNotice] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);

  // Field teams need a durable hand-off after the voice call, not only a
  // transient modal. Keep this entirely client-side: the summary has already
  // passed through the existing authenticated report API, and generating a
  // UTF-8 text file adds no server endpoint, dependency, or patient-data copy.
  // The BOM preserves Kannada/Hindi text when the file is opened in common
  // Windows tools used by ASHA programme staff.
  const downloadReport = () => {
    if (!card) return;
    const line = (label: string, value: unknown) =>
      `${label}: ${value === null || value === undefined || value === "" ? "—" : String(value)}`;
    const report = [
      "Sakhi Maternal Health Visit Summary",
      "===================================",
      line("Session ID", sessionId),
      line("Mother's name", card.mother_name),
      line("Age", card.age),
      line("Village", card.village),
      line("Phone", card.phone),
      line("Gestational age", card.gestational_weeks ? `${card.gestational_weeks} weeks` : null),
      line("Blood pressure", card.blood_pressure),
      line("Danger signs", card.danger_signs.length ? card.danger_signs.join(", ") : "None reported"),
      line("Risk category", card.risk_category),
      line("Referral", card.referral_status),
      line("Referral reason", card.referral_trigger),
      line("Questions answered", card.questions_answered),
      line("Visit date", card.visit_date),
      line("Language", card.language),
      "",
      "Draft MCP/RCH-style summary — verify against the printed register before filing.",
    ].join("\n");
    const blob = new Blob(["\uFEFF", report], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `sakhi-visit-${sessionId.replace(/[^a-zA-Z0-9_-]/g, "-")}.txt`;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  };

  useEffect(() => {
    let cancelled = false;
    let timer: any = null;

    const fetchCard = async (retryIndex: number) => {
      try {
        setLoading(true);
        setError(null);
        if (retryIndex > 0) {
          setSavingNotice("Saving visit summary… retrying automatically.");
        }
        const res = await fetch(`/api/reports/mcp-card?session_id=${encodeURIComponent(sessionId)}`, {
          cache: "no-store",
        });
        const data = await res.json();
        if (cancelled) return;

        if (res.ok) {
          setCard(data);
          setError(null);
          setSavingNotice(null);
          setLoading(false);
          return;
        }

        // If report is not found yet, auto-retry up to 8 times with 1.5s interval
        if (data?.error === "report not found" && retryIndex < 8) {
          setSavingNotice("Saving visit summary… retrying automatically.");
          timer = setTimeout(() => {
            if (!cancelled) fetchCard(retryIndex + 1);
          }, 1500);
          return;
        }

        // Exceeded retries or other error
        setSavingNotice(null);
        setError(
          data?.error === "report not found"
            ? "Visit summary is still being saved - please click Try Again in a moment."
            : (data?.error || "Could not load the visit summary.")
        );
        setLoading(false);
      } catch (e: any) {
        if (!cancelled) {
          setSavingNotice(null);
          setError(e?.message || "Could not load the visit summary.");
          setLoading(false);
        }
      }
    };

    fetchCard(0);

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [sessionId, attempt]);

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

        {loading && (
          <div style={{ padding: "12px 0" }}>
            <p style={{ fontSize: 14, color: "#475569", margin: "0 0 6px" }}>Loading…</p>
            {savingNotice && (
              <p style={{ fontSize: 13, color: "#0f766e", fontStyle: "italic", margin: 0 }}>
                ⏳ {savingNotice}
              </p>
            )}
          </div>
        )}

        {error && !loading && (
          <div style={{ marginTop: 8, marginBottom: 12 }}>
            <p style={{ fontSize: 14, color: "#b91c1c", marginBottom: 10 }}>{error}</p>
            <button
              type="button"
              onClick={() => {
                setError(null);
                setAttempt((a) => a + 1);
              }}
              style={{
                width: "100%",
                padding: "9px 12px",
                borderRadius: 8,
                border: "1px solid #cbd5e1",
                background: "#f1f5f9",
                color: "#1e293b",
                fontWeight: 600,
                fontSize: 14,
                cursor: "pointer",
              }}
            >
              🔄 Try Again
            </button>
          </div>
        )}

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

            <button
              type="button"
              onClick={downloadReport}
              style={{
                marginTop: 18,
                width: "100%",
                padding: "12px 10px",
                borderRadius: 10,
                border: "1px solid #0f766e",
                background: "white",
                color: "#0f766e",
                fontWeight: 700,
                fontSize: 15,
                cursor: "pointer",
              }}
            >
              Download report
            </button>
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
