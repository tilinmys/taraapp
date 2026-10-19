"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import styles from "./admin.module.css";
import AwsCosts from "./AwsCosts";
import {
  Component,
  Mother,
  PatientReport,
  PipelineEvent,
  RISK_LABEL,
  Summary,
  formatTime,
} from "./types";

// Admin / engineering dashboard. Read-only view over the same database the
// voice worker writes (via /api/dashboard -> status_server ->
// sakhi_dashboard_store). Polling, not websockets: a keyset event query and a
// ~8-row health table are O(log n) and O(1) per poll, so 1-2 s polling is
// cheap and survives a worker restart with no reconnect logic. Supabase
// Realtime can replace the timers later without changing any view.

const SUMMARY_POLL_MS = 2000;
const EVENTS_POLL_MS = 1000;
const LOG_LIMIT = 400;
// A component stays red this long after its last error, so a failure that
// recovered two seconds later is still visible to the engineer.
const ERROR_STICKY_MS = 5 * 60 * 1000;

// The voice pipeline, in the order audio flows through it.
const PIPELINE: { key: string; title: string; hint: string }[] = [
  { key: "microphone", title: "Microphone", hint: "Tablet audio + VAD" },
  { key: "livekit", title: "LiveKit", hint: "WebRTC room + dispatch" },
  { key: "stt", title: "STT", hint: "Speech → text" },
  { key: "turn", title: "Turn", hint: "End of utterance" },
  { key: "llm", title: "LLM", hint: "Reasoning + tools" },
  { key: "tts", title: "TTS", hint: "Text → speech" },
  { key: "playback", title: "Playback", hint: "Audio to tablet" },
];
const SUPPORT: { key: string; title: string; hint: string }[] = [
  { key: "database", title: "Database", hint: "Clinical records" },
  { key: "worker", title: "Worker", hint: "Agent process" },
];

type Health = "ok" | "warn" | "error" | "unknown";

function healthOf(c: Component | undefined, now: number): Health {
  if (!c) return "unknown";
  if (c.last_status === "error") return "error";
  if (c.last_error_at && now - new Date(c.last_error_at).getTime() < ERROR_STICKY_MS) return "error";
  if (c.last_status === "warn") return "warn";
  return c.last_status === "ok" ? "ok" : "unknown";
}

async function getJSON<T>(url: string): Promise<T> {
  const res = await fetch(url, { cache: "no-store" });
  const body = await res.json();
  if (!res.ok) throw new Error(body?.error || `HTTP ${res.status}`);
  return body as T;
}

export default function AdminDashboard() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [summaryError, setSummaryError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [selectedComponent, setSelectedComponent] = useState<string | null>(null);
  const [tab, setTab] = useState<"patients" | "log" | "costs">("patients");

  const [now, setNow] = useState(() => Date.now());

  const loadSummary = useCallback(async () => {
    try {
      setSummary(await getJSON<Summary>("/api/dashboard/summary"));
      setSummaryError(null);
      setLastUpdated(new Date());
    } catch (err) {
      setSummaryError(err instanceof Error ? err.message : String(err));
    }
    setNow(Date.now());
  }, []);

  useEffect(() => {
    // Schedule the first fetch after the effect subscribes. React 19's hooks
    // lint rejects a state-producing callback invoked synchronously inside an
    // effect; the zero-delay timer also makes cleanup deterministic on a
    // StrictMode mount/unmount probe.
    const initial = setTimeout(loadSummary, 0);
    const timer = setInterval(loadSummary, SUMMARY_POLL_MS);
    return () => { clearTimeout(initial); clearInterval(timer); };
  }, [loadSummary]);

  const byKey = new Map((summary?.components || []).map((c) => [c.component, c]));
  const broken = [...PIPELINE, ...SUPPORT].filter((s) => healthOf(byKey.get(s.key), now) === "error");
  const selected = selectedComponent ? byKey.get(selectedComponent) : undefined;

  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <div>
          <h1 className={styles.title}>Sakhi Control Room</h1>
          <p className={styles.subtitle}>
            Live system health, mothers&apos; records and call logs
            {summary ? ` · database: ${summary.backend}` : ""}
          </p>
        </div>
        <div className={summaryError ? styles.liveDown : styles.live}>
          <span className={styles.liveDot} />
          {summaryError ? "Worker unreachable" : lastUpdated ? `Live · ${lastUpdated.toLocaleTimeString()}` : "Connecting…"}
        </div>
      </header>

      {summaryError && <div className={styles.banner}>Cannot reach the worker: {summaryError}</div>}
      {!summaryError && broken.length > 0 && (
        <div className={styles.banner}>
          Failing now: {broken.map((b) => b.title).join(", ")} — click a red box for the error.
        </div>
      )}

      <section className={styles.counters}>
        <Counter label="Mothers" value={summary?.mothers} />
        <Counter label="High risk" value={summary?.high_risk_mothers} tone="danger" />
        <Counter label="Calls" value={summary?.calls} />
        <Counter label="Escalations" value={summary?.escalations} tone="warn" />
        <Counter label="Errors logged" value={summary?.errors} tone={summary?.errors ? "danger" : undefined} />
      </section>

      <section className={styles.card}>
        <h2 className={styles.cardTitle}>Voice pipeline</h2>
        <div className={styles.pipeline}>
          {PIPELINE.map((stage, i) => (
            <div key={stage.key} className={styles.pipelineStep}>
              <StageBox
                stage={stage}
                health={healthOf(byKey.get(stage.key), now)}
                active={selectedComponent === stage.key}
                onClick={() => setSelectedComponent(selectedComponent === stage.key ? null : stage.key)}
              />
              {i < PIPELINE.length - 1 && <span className={styles.arrow}>→</span>}
            </div>
          ))}
        </div>
        <div className={styles.support}>
          {SUPPORT.map((stage) => (
            <StageBox
              key={stage.key}
              stage={stage}
              health={healthOf(byKey.get(stage.key), now)}
              active={selectedComponent === stage.key}
              onClick={() => setSelectedComponent(selectedComponent === stage.key ? null : stage.key)}
            />
          ))}
          <Legend />
        </div>
        {selectedComponent && <ComponentDetail name={selectedComponent} component={selected} />}
      </section>

      <nav className={styles.tabs}>
        <button className={tab === "patients" ? styles.tabActive : styles.tab} onClick={() => setTab("patients")}>
          Mothers &amp; reports
        </button>
        <button className={tab === "log" ? styles.tabActive : styles.tab} onClick={() => setTab("log")}>
          Live log
        </button>
        <button className={tab === "costs" ? styles.tabActive : styles.tab} onClick={() => setTab("costs")}>
          AWS costs
        </button>
      </nav>

      {tab === "patients" ? <PatientsView /> : tab === "log" ? <LiveLog /> : <AwsCosts />}
    </main>
  );
}

function Counter({ label, value, tone }: { label: string; value: number | undefined; tone?: "danger" | "warn" }) {
  return (
    <div className={`${styles.counter} ${tone === "danger" ? styles.counterDanger : tone === "warn" ? styles.counterWarn : ""}`}>
      <div className={styles.counterValue}>{value ?? "—"}</div>
      <div className={styles.counterLabel}>{label}</div>
    </div>
  );
}

function StageBox({ stage, health, active, onClick }: {
  stage: { key: string; title: string; hint: string };
  health: Health;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      className={`${styles.stage} ${styles[`stage_${health}`]} ${active ? styles.stageActive : ""}`}
      onClick={onClick}
      title={`${stage.title}: ${health}`}
    >
      <span className={styles.stageTitle}>{stage.title}</span>
      <span className={styles.stageHint}>{stage.hint}</span>
      <span className={styles.stageState}>
        {health === "unknown" ? "no data" : health === "ok" ? "healthy" : health === "warn" ? "warning" : "FAILING"}
      </span>
    </button>
  );
}

function Legend() {
  return (
    <div className={styles.legend}>
      <span><i className={styles.dotOk} /> healthy</span>
      <span><i className={styles.dotWarn} /> warning</span>
      <span><i className={styles.dotError} /> failed (last 5 min)</span>
      <span><i className={styles.dotUnknown} /> no data yet</span>
    </div>
  );
}

function ComponentDetail({ name, component }: { name: string; component: Component | undefined }) {
  if (!component) {
    return <div className={styles.detail}>No events recorded for <b>{name}</b> yet — it appears after the first call.</div>;
  }
  return (
    <div className={styles.detail}>
      <div className={styles.detailGrid}>
        <div><span className={styles.k}>Component</span>{component.component}</div>
        <div><span className={styles.k}>Current status</span>{component.last_status}</div>
        <div><span className={styles.k}>Last event</span>{component.last_label} — {component.last_message}</div>
        <div><span className={styles.k}>Updated</span>{formatTime(component.updated_at)}</div>
        <div><span className={styles.k}>Last healthy</span>{formatTime(component.last_ok_at)}</div>
        <div><span className={styles.k}>Errors (total)</span>{component.error_count}</div>
      </div>
      {component.last_error && (
        <div className={styles.errorBox}>
          <b>Last error</b> at {formatTime(component.last_error_at)}
          {component.last_error_session ? ` · call ${component.last_error_session}` : ""}
          <pre>{component.last_error}</pre>
        </div>
      )}
    </div>
  );
}

// --- Mothers & report cards -------------------------------------------------

function PatientsView() {
  const [query, setQuery] = useState("");
  const [risk, setRisk] = useState("");
  const [patients, setPatients] = useState<Mother[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      const params = new URLSearchParams({ limit: "200" });
      if (query.trim()) params.set("q", query.trim());
      if (risk) params.set("risk", risk);
      try {
        const data = await getJSON<{ patients: Mother[] }>(`/api/dashboard/patients?${params}`);
        if (!cancelled) {
          setPatients(data.patients);
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      }
    };
    // Debounced search; refreshes every 5 s so new calls appear on their own.
    const first = setTimeout(load, 250);
    const timer = setInterval(load, 5000);
    return () => {
      cancelled = true;
      clearTimeout(first);
      clearInterval(timer);
    };
  }, [query, risk]);

  return (
    <section className={styles.split}>
      <div className={styles.card}>
        <div className={styles.toolbar}>
          <input
            className={styles.input}
            placeholder="Search name, ID or phone…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <select className={styles.input} value={risk} onChange={(e) => setRisk(e.target.value)}>
            <option value="">All risk levels</option>
            {Object.entries(RISK_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </div>
        {error && <p className={styles.muted}>Could not load: {error}</p>}
        <table className={styles.table}>
          <thead>
            <tr><th>Mother</th><th>Weeks</th><th>Risk</th><th>Danger signs</th><th>Updated</th></tr>
          </thead>
          <tbody>
            {patients.map((p) => (
              <tr
                key={p.patient_id}
                className={selected === p.patient_id ? styles.rowSelected : styles.row}
                onClick={() => setSelected(p.patient_id)}
              >
                <td><b>{p.name}</b><div className={styles.muted}>{p.patient_id}{p.village ? ` · ${p.village}` : ""}</div></td>
                <td>{p.gestational_weeks ?? "—"}</td>
                <td><RiskPill risk={p.risk_status} /></td>
                <td>{p.danger_signs.length ? p.danger_signs.join(", ") : <span className={styles.muted}>none</span>}</td>
                <td className={styles.muted}>{formatTime(p.updated_at)}</td>
              </tr>
            ))}
            {!patients.length && !error && (
              <tr><td colSpan={5} className={styles.muted}>No mothers recorded yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      <div className={styles.card}>
        {selected ? <PatientPanel key={selected} patientId={selected} /> : <p className={styles.muted}>Select a mother to see her full report.</p>}
      </div>
    </section>
  );
}

function RiskPill({ risk }: { risk: string | null }) {
  const key = (risk || "ROUTINE").toUpperCase();
  return <span className={`${styles.pill} ${styles[`risk_${key}`] || ""}`}>{RISK_LABEL[key] || key}</span>;
}

function PatientPanel({ patientId }: { patientId: string }) {
  const [report, setReport] = useState<PatientReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getJSON<PatientReport>(`/api/dashboard/patient?id=${encodeURIComponent(patientId)}`)
      .then((r) => !cancelled && setReport(r))
      .catch((err) => !cancelled && setError(err instanceof Error ? err.message : String(err)));
    return () => { cancelled = true; };
  }, [patientId]);

  if (error) return <p className={styles.muted}>Could not load report: {error}</p>;
  if (!report) return <p className={styles.muted}>Loading report…</p>;

  const m = report.mother;
  const downloadJson = () => {
    const blob = new Blob([JSON.stringify(report, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `sakhi-report-${m.name.replace(/\s+/g, "_")}-${m.patient_id}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div>
      <div className={styles.panelHeader}>
        <div>
          <h2 className={styles.cardTitle}>{m.name}</h2>
          <p className={styles.muted}>
            {m.patient_id} · {m.age ? `${m.age} yrs` : "age —"} · {m.gestational_weeks ? `${m.gestational_weeks} weeks` : "weeks —"}
            {m.village ? ` · ${m.village}` : ""}
          </p>
        </div>
        <RiskPill risk={m.risk_status} />
      </div>
      <div className={styles.actions}>
        <a className={styles.primaryBtn} href={`/admin/report?id=${encodeURIComponent(m.patient_id)}`} target="_blank" rel="noreferrer">
          Download report (PDF)
        </a>
        <button className={styles.secondaryBtn} onClick={downloadJson}>Download data (JSON)</button>
      </div>

      <h3 className={styles.sectionTitle}>Danger signs</h3>
      <p>{report.danger_signs.length ? report.danger_signs.join(", ") : "None recorded"}</p>

      <h3 className={styles.sectionTitle}>Blood pressure</h3>
      {report.bp_readings.length ? (
        <table className={styles.table}>
          <thead><tr><th>Reading</th><th>Risk</th><th>Recorded</th></tr></thead>
          <tbody>
            {report.bp_readings.map((r, i) => (
              <tr key={i}><td>{r.systolic}/{r.diastolic}</td><td><RiskPill risk={r.risk_level} /></td><td className={styles.muted}>{formatTime(r.recorded_at)}</td></tr>
            ))}
          </tbody>
        </table>
      ) : <p className={styles.muted}>No readings</p>}

      {report.teleconsults.length > 0 && (
        <>
          <h3 className={styles.sectionTitle}>Escalations</h3>
          <ul className={styles.list}>
            {report.teleconsults.map((t) => (
              <li key={t.channel_id}><b>{t.trigger_sign || "escalated"}</b> — {t.reason || "—"} <span className={styles.muted}>({formatTime(t.created_at)})</span></li>
            ))}
          </ul>
        </>
      )}

      <h3 className={styles.sectionTitle}>Calls ({report.calls.length})</h3>
      {report.calls.map((c) => (
        <details key={c.id} className={styles.call}>
          <summary>
            {formatTime(c.created_at)} · <RiskPill risk={c.risk_level} /> · {c.intake_answered}/{c.intake_total} questions
            {c.escalated ? " · escalated" : ""}
          </summary>
          {c.turns.length ? (
            <ol className={styles.transcript}>
              {c.turns.map((t) => (
                <li key={t.turn_no} className={t.speaker === "sakhi" ? styles.turnSakhi : styles.turnAsha}>
                  <span className={styles.speaker}>{t.speaker === "sakhi" ? "Sakhi" : "ASHA"}</span>{t.text}
                </li>
              ))}
            </ol>
          ) : <p className={styles.muted}>No conversation recorded for this call.</p>}
        </details>
      ))}
    </div>
  );
}

// --- Live log -----------------------------------------------------------------

function LiveLog() {
  const [events, setEvents] = useState<PipelineEvent[]>([]);
  const [status, setStatus] = useState("");
  const [session, setSession] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [paused, setPaused] = useState(false);
  const cursor = useRef(0);

  // Changing a filter restarts from the beginning of the matching events.
  // Reset in the input event (where the change originates), not in a second
  // effect after rendering the new filter; that avoids one poll using a new
  // filter with the old cursor and satisfies React 19's effect-state rule.
  const resetEvents = () => {
    cursor.current = 0;
    setEvents([]);
  };
  const changeStatus = (value: string) => {
    resetEvents();
    setStatus(value);
  };
  const changeSession = (value: string) => {
    resetEvents();
    setSession(value);
  };

  useEffect(() => {
    if (paused) return;
    let cancelled = false;
    const poll = async () => {
      const params = new URLSearchParams({ after: String(cursor.current), limit: "200" });
      if (status) params.set("status", status);
      if (session.trim()) params.set("session_id", session.trim());
      try {
        const data = await getJSON<{ events: PipelineEvent[]; next: number }>(`/api/dashboard/events?${params}`);
        if (cancelled) return;
        cursor.current = data.next;
        if (data.events.length) {
          // Newest first; keep a bounded window so the page never slows down.
          setEvents((prev) => [...data.events.slice().reverse(), ...prev].slice(0, LOG_LIMIT));
        }
        setError(null);
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      }
    };
    poll();
    const timer = setInterval(poll, EVENTS_POLL_MS);
    return () => { cancelled = true; clearInterval(timer); };
  }, [status, session, paused]);

  return (
    <section className={styles.card}>
      <div className={styles.toolbar}>
        <select className={styles.input} value={status} onChange={(e) => changeStatus(e.target.value)}>
          <option value="">All events</option>
          <option value="error">Errors only</option>
          <option value="warn">Warnings only</option>
          <option value="ok">OK only</option>
        </select>
        <input className={styles.input} placeholder="Filter by call (session id)…" value={session} onChange={(e) => changeSession(e.target.value)} />
        <button className={styles.secondaryBtn} onClick={() => setPaused((p) => !p)}>{paused ? "Resume" : "Pause"}</button>
      </div>
      {error && <p className={styles.muted}>Could not load log: {error}</p>}
      <div className={styles.log}>
        {events.map((e) => (
          <div key={e.id} className={`${styles.logRow} ${styles[`log_${e.status}`] || ""}`}>
            <span className={styles.logTime}>{new Date(e.ts).toLocaleTimeString()}</span>
            <span className={styles.logStage}>{e.stage}</span>
            <span className={styles.logLabel}>{e.label}</span>
            <span className={styles.logMsg}>{e.message}</span>
            {e.session_id && (
              <button className={styles.logSession} onClick={() => changeSession(e.session_id || "")} title="Show only this call">
                {e.session_id.slice(-12)}
              </button>
            )}
          </div>
        ))}
        {!events.length && !error && <p className={styles.muted}>Waiting for events… they appear here within a second of happening.</p>}
      </div>
    </section>
  );
}
