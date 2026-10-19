"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import styles from "./report.module.css";
import { PatientReport, RISK_LABEL, formatTime } from "../types";

// Printable report card for one mother. Opened from the admin dashboard in a
// new tab; it prints itself once loaded, so "Save as PDF" in the print
// dialog produces the downloadable report. Plain HTML + print CSS rather
// than a PDF library: nothing extra to install, and the output is exactly
// what is on screen.

function ReportCard() {
  const id = useSearchParams().get("id") || "";
  const [report, setReport] = useState<PatientReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    fetch(`/api/dashboard/patient?id=${encodeURIComponent(id)}`, { cache: "no-store" })
      .then(async (res) => {
        const body = await res.json();
        if (!res.ok) throw new Error(body?.error || `HTTP ${res.status}`);
        setReport(body as PatientReport);
      })
      .catch((err) => setError(err instanceof Error ? err.message : String(err)));
  }, [id]);

  useEffect(() => {
    if (!report) return;
    // The file name the browser suggests for "Save as PDF".
    document.title = `Sakhi report - ${report.mother.name} - ${report.mother.patient_id}`;
    const timer = setTimeout(() => window.print(), 400);
    return () => clearTimeout(timer);
  }, [report]);

  if (!id) return <main className={styles.page}><p>Could not load report: No patient id given.</p></main>;
  if (error) return <main className={styles.page}><p>Could not load report: {error}</p></main>;
  if (!report) return <main className={styles.page}><p>Preparing report…</p></main>;

  const m = report.mother;
  const risk = (m.risk_status || "ROUTINE").toUpperCase();

  return (
    <main className={styles.page}>
      <div className={styles.noPrint}>
        <button onClick={() => window.print()} className={styles.printBtn}>Print / Save as PDF</button>
      </div>

      <header className={styles.header}>
        <div>
          <div className={styles.brand}>Sakhi · Antenatal Visit Report</div>
          <h1 className={styles.name}>{m.name}</h1>
          <div className={styles.meta}>
            ID {m.patient_id} · Age {m.age ?? "—"} · {m.gestational_weeks ? `${m.gestational_weeks} weeks pregnant` : "gestation not recorded"}
            {m.village ? ` · ${m.village}` : ""}{m.phone ? ` · ${m.phone}` : ""}
          </div>
        </div>
        <div className={`${styles.risk} ${styles[`risk_${risk}`] || ""}`}>{RISK_LABEL[risk] || risk}</div>
      </header>

      <section className={styles.section}>
        <h2>Danger signs</h2>
        <p>{report.danger_signs.length ? report.danger_signs.join(", ") : "None recorded"}</p>
      </section>

      <section className={styles.section}>
        <h2>Blood pressure readings</h2>
        {report.bp_readings.length ? (
          <table className={styles.table}>
            <thead><tr><th>Date</th><th>Reading (mmHg)</th><th>Risk at the time</th></tr></thead>
            <tbody>
              {report.bp_readings.map((r, i) => (
                <tr key={i}><td>{formatTime(r.recorded_at)}</td><td>{r.systolic}/{r.diastolic}</td><td>{RISK_LABEL[r.risk_level] || r.risk_level}</td></tr>
              ))}
            </tbody>
          </table>
        ) : <p>No readings recorded.</p>}
      </section>

      {report.teleconsults.length > 0 && (
        <section className={styles.section}>
          <h2>Escalations / referrals</h2>
          <table className={styles.table}>
            <thead><tr><th>Date</th><th>Trigger</th><th>Reason</th></tr></thead>
            <tbody>
              {report.teleconsults.map((t) => (
                <tr key={t.channel_id}><td>{formatTime(t.created_at)}</td><td>{t.trigger_sign || "—"}</td><td>{t.reason || "—"}</td></tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      <section className={styles.section}>
        <h2>Visits ({report.calls.length})</h2>
        {report.calls.map((c) => (
          <div key={c.id} className={styles.visit}>
            <div className={styles.visitHead}>
              {formatTime(c.created_at)} · {RISK_LABEL[(c.risk_level || "").toUpperCase()] || c.risk_level || "—"}
              {" · "}{c.intake_answered}/{c.intake_total} questions answered{c.escalated ? " · referred" : ""}
            </div>
            {c.turns.length > 0 && (
              <ol className={styles.transcript}>
                {c.turns.map((t) => (
                  <li key={t.turn_no}><b>{t.speaker === "sakhi" ? "Sakhi" : "ASHA"}:</b> {t.text}</li>
                ))}
              </ol>
            )}
          </div>
        ))}
      </section>

      <footer className={styles.footer}>
        Generated {formatTime(report.generated_at)} by Sakhi. Decision-support summary for the care team; not a diagnosis.
      </footer>
    </main>
  );
}

export default function ReportPage() {
  return (
    <Suspense fallback={<main style={{ padding: 32 }}>Preparing report…</main>}>
      <ReportCard />
    </Suspense>
  );
}
