"use client";

import { useEffect, useState } from "react";
import styles from "./reports.module.css";

type CallReport = {
  id: number;
  session_id: string | null;
  patient_id: string | null;
  language: string | null;
  risk_level: string | null;
  intake_answered: number;
  intake_total: number;
  escalated: number;
  duration_ms: number | null;
  manual_risk_flag: number;
  flagged_to_doctor: number;
  flagged_by: string | null;
  flagged_at: string | null;
  recording_status: "none" | "stopped" | "available" | string | null;
  recording_url: string | null;
  created_at: string;
};

// recording_status as agent.py/status_server.py actually produce it:
// 'none' - no recording was ever started for this call (feature off, or the
//   S3/credential config was missing - agent.py never let that break the call)
// 'stopped' - egress was told to finish; the file is uploading to S3 right
//   now (status_server.py hasn't been able to HEAD it in the bucket yet)
// 'available' - status_server.py confirmed the object exists in S3 (a real
//   HeadObject, not just "we asked for a recording") and handed back a
//   presigned URL good for the next hour
const RECORDING_LABEL: Record<string, string> = {
  none: "No recording",
  stopped: "Processing…",
  available: "Available",
};

// Plain list view over sakhi_sql_repository's call_reports table (the
// clinically-real report shape - risk_level/escalated/intake progress -
// wired up in place of the old generic, never-clinical report path). No
// auth exists anywhere in this app, so this page inherits that: open to
// anyone who reaches the URL, same as /schedule or /doctors. `flagged_by`
// is a freeform name field, not a login - there is no worker identity
// captured anywhere in the system today.
export default function ReportsPage() {
  const [reports, setReports] = useState<CallReport[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [flaggedByName, setFlaggedByName] = useState("");
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const load = async () => {
    try {
      const res = await fetch("/api/reports?limit=100", { cache: "no-store" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      setReports(data.reports || []);
      setError(null);
      setLastUpdated(new Date());
    } catch (e: any) {
      setError(e.message || "Failed to load reports");
    } finally {
      setLoading(false);
    }
  };

  // Live-updating dashboard: a call in progress, or a recording still
  // uploading to S3, changes state on its own with nobody touching this
  // page. Polling (not a websocket) matches the rest of this app - the
  // live-call side already polls /api/logs the same way, see page.tsx.
  useEffect(() => {
    load();
    const interval = setInterval(load, 5000);
    return () => clearInterval(interval);
  }, []);

  const flag = async (report: CallReport, field: "manual_risk_flag" | "flagged_to_doctor") => {
    const nextValue = !report[field];
    // Optimistic update so the button responds instantly; reconciled by the
    // server's actual row on the next full load if this request fails.
    setReports((prev) =>
      prev.map((r) =>
        r.id === report.id
          ? { ...r, [field]: nextValue ? 1 : 0, flagged_by: flaggedByName || r.flagged_by }
          : r
      )
    );
    try {
      const res = await fetch("/api/reports/flag", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          report_id: report.id,
          field,
          value: nextValue,
          flagged_by: flaggedByName || undefined,
        }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error || `HTTP ${res.status}`);
      }
    } catch (e: any) {
      setError(`Flag update failed: ${e.message}`);
      load(); // revert to server truth
    }
  };

  return (
    <div className={styles.page}>
      <div className={styles.header}>
        <h1 className={styles.title}>Call Reports</h1>
        <p className={styles.subtitle}>
          Recent Sakhi calls, with risk level, escalation, intake completeness, and recording status.
        </p>
        <p className={styles.subtitle} style={{ marginTop: 4, opacity: 0.7 }}>
          <span aria-hidden="true">●</span> Live — refreshes every 5s
          {lastUpdated && <> · last updated {lastUpdated.toLocaleTimeString()}</>}
        </p>
        <p className={styles.subtitle} style={{ marginTop: 10 }}>
          <label>
            Your name (recorded against any flag you set):{" "}
            <input
              value={flaggedByName}
              onChange={(e) => setFlaggedByName(e.target.value)}
              placeholder="e.g. Dr. Rao"
              style={{ padding: "4px 8px", borderRadius: 6, border: "1px solid var(--sk-line)" }}
            />
          </label>
        </p>
      </div>

      {error && <div className={styles.error}>{error}</div>}

      <div className={styles.tableWrap}>
        {loading ? (
          <div className={styles.empty}>Loading…</div>
        ) : reports.length === 0 ? (
          <div className={styles.empty}>No call reports yet.</div>
        ) : (
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Created</th>
                <th>Patient</th>
                <th>Language</th>
                <th>Risk</th>
                <th>Escalated</th>
                <th>Intake</th>
                <th>Duration</th>
                <th>Recording</th>
                <th>Flags</th>
              </tr>
            </thead>
            <tbody>
              {reports.map((r) => (
                <tr key={r.id}>
                  <td>{r.created_at}</td>
                  <td>{r.patient_id || "—"}</td>
                  <td>{r.language || "—"}</td>
                  <td>
                    <span className={styles.riskPill} data-level={r.risk_level || "ROUTINE"}>
                      {r.risk_level || "ROUTINE"}
                    </span>
                  </td>
                  <td>{r.escalated ? "Yes" : "No"}</td>
                  <td>{r.intake_answered}/{r.intake_total}</td>
                  <td>{r.duration_ms ? `${Math.round(r.duration_ms / 1000)}s` : "—"}</td>
                  <td>
                    {r.recording_status === "none" || !r.recording_status ? (
                      <span style={{ opacity: 0.6 }}>{RECORDING_LABEL.none}</span>
                    ) : (
                      <span
                        className={styles.riskPill}
                        data-level={r.recording_status === "available" ? "ROUTINE" : "MODERATE"}
                      >
                        {RECORDING_LABEL[r.recording_status] || r.recording_status}
                      </span>
                    )}
                    {r.recording_status === "available" && r.recording_url && (
                      <>
                        {" "}
                        <a href={r.recording_url} target="_blank" rel="noopener noreferrer">
                          ▶ Play
                        </a>
                      </>
                    )}
                  </td>
                  <td>
                    <button
                      className={styles.flagBtn}
                      data-active={r.manual_risk_flag ? "true" : "false"}
                      data-kind="risk"
                      onClick={() => flag(r, "manual_risk_flag")}
                    >
                      {r.manual_risk_flag ? "Risky ✓" : "Flag as Risky"}
                    </button>
                    <button
                      className={styles.flagBtn}
                      data-active={r.flagged_to_doctor ? "true" : "false"}
                      data-kind="doctor"
                      onClick={() => flag(r, "flagged_to_doctor")}
                    >
                      {r.flagged_to_doctor ? "To Doctor ✓" : "Flag to Doctor"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
