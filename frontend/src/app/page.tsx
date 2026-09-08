"use client";

/*
 * Sakhi — ASHA worker console.
 *
 * Built for one user: an ASHA worker in a Bidar village, on a cheap Android
 * phone, often outdoors, Kannada-first, with variable literacy and a patchy
 * 3G link. Every decision below follows from that.
 *
 * What changed from the previous dashboard, and why:
 *
 * - Language is the ONLY thing she chooses, as three large pills. The TTS
 *   provider and voice pickers are gone entirely: the backend picks the
 *   voice via its own TTS_PROVIDER env var (see agent.py build_tts), and
 *   asking a field worker to pick a speech vendor was a way to start a call
 *   wrong. Which provider that currently resolves to is a backend/.env
 *   decision, not a frontend one - keeping it in one place after an earlier
 *   version hardcoded a provider here and silently ignored every later
 *   backend change.
 * - The Three.js/WebGL canvas is gone. It shipped a 3D model and ran a
 *   continuous render loop on a device class that cannot afford either; the
 *   Tavus video track replaces it with a plain <video> element.
 * - The log console moved from the main column to a quiet, collapsible left
 *   rail, so it stays available for supervisors without competing with the call.
 * - Status is now icon + colour + Kannada word together, never colour alone.
 */

import { useState, useRef, useEffect, useCallback } from "react";
import {
  LiveKitRoom,
  RoomAudioRenderer,
  useRoomContext,
} from "@livekit/components-react";
import { Room, RoomEvent } from "livekit-client";
import "@livekit/components-styles";
import styles from "./page.module.css";
import { VisitSummaryCard } from "../components/VisitSummaryCard";
import { SakhiLiveSession } from "../components/SakhiLiveSession";
import type { TriageRisk } from "../components/SakhiHeader";

type LangKey = "english" | "kannada" | "hindi";

// Sakhi speaks with ONE voice, chosen server-side.
//
// The provider/voice pickers are gone. They were an engineering knob on a
// screen used by an ASHA worker outdoors on a cheap phone - she has no basis
// to choose a TTS vendor, and every extra control is a way to start a call
// wrong.
//
// Deliberately NOT hardcoded here. An earlier version pinned this to
// "gnani"/"Kaveri" - fine at the time, but it meant every later change to
// the backend's TTS_PROVIDER env var (e.g. switching to Sarvam after a
// faster benchmark) silently had NO EFFECT on real calls, because dispatch
// metadata from THIS constant always overrode the .env default. Sending no
// provider/voice at all makes the backend's env var the single source of
// truth again - see build_tts() in agent.py.
//
// Language stays a choice: that one is hers to make.

// Reuses SakhiHeader's own TriageRisk rather than redeclaring an identical
// union here - the previous RiskLevel type predated the redesign.
type RiskLevel = TriageRisk;

interface LogMessage {
  id: number;
  time: string;
  text: string;
  kind: "info" | "ok" | "warn" | "error";
  // The pipeline_event's full `details` object (agent.py), when this entry
  // came from the worker's own log stream rather than a frontend-only line
  // (a bare startCall()/addLog() call has none). This is where "which
  // provider, how long, what value got recorded, what actually raised" all
  // live - previously fetched from /api/logs and then discarded before ever
  // reaching render. See the poll loop below.
  details?: Record<string, unknown>;
  stage?: string;
}

// Live Form Sync (Module C, 2026-08-30): the verified slot values Sakhi has
// recorded so far, pushed via the same pipeline_event/poll channel as
// risk/intake above. See sakhi_tools.py's _emit_live_form_sync.
interface LiveFormValues {
  systolic: number | null;
  diastolic: number | null;
  gestational_weeks: number | null;
  symptoms: {
    headache: boolean;
    blurry_vision: boolean;
    swelling: boolean;
    vaginal_bleeding: boolean;
    severe_abdominal_pain: boolean;
  };
}

const FIELD_CHIP_LABELS: Record<string, string> = {
  headache: "Headache",
  blurry_vision: "Blurred vision",
  swelling: "Swelling",
  vaginal_bleeding: "Vaginal bleeding",
  severe_abdominal_pain: "Severe abdominal pain",
};

const LOW_POWER_STORAGE_KEY = "sakhi_low_power_mode";

// Default guess only - see the manual toggle in page.tsx's controls, which
// is what actually matters. A tablet on wifi doesn't need power-saving, and
// an old Android phone on 3G does regardless of screen size, so a heuristic
// alone is never enough here.
function guessLowPowerDefault(): boolean {
  if (typeof window === "undefined") return false;
  const stored = window.localStorage?.getItem(LOW_POWER_STORAGE_KEY);
  if (stored === "1") return true;
  if (stored === "0") return false;
  const narrow = window.innerWidth > 0 && window.innerWidth < 480;
  const mobileUa = /Android|iPhone/i.test(window.navigator.userAgent || "");
  return narrow && mobileUa;
}

const COPY = {
  english: {
    tagline: "Maternal Health Voice Assistant",
    taglineSub: "Clinical co-pilot for ASHA healthcare workers",
    idleTitle: "Ready to Assist",
    idleSub: "Press the green button below to start your call",
    connecting: "Connecting to Sakhi...",
    connectingSub: "Establishing WebRTC voice session",
    start: "Start Conversation",
    end: "End Call",
    hint: "After pressing start, speak naturally. Sakhi is listening.",
    chooseLang: "Select Language",
    micNote: "Please allow microphone access when prompted",
  },
  kannada: {
    tagline: "Maternal Health Voice Assistant",
    taglineSub: "ತಾಯಿಯ ಆರೋಗ್ಯ ಸಹಾಯಕಿ",
    idleTitle: "Ready to Assist (ಮಾತನಾಡಲು ಸಿದ್ಧ)",
    idleSub: "Press the green button below (ಕೆಳಗಿನ ಹಸಿರು ಗುಂಡಿಯನ್ನು ಒತ್ತಿರಿ)",
    connecting: "Connecting... (ಸಂಪರ್ಕಿಸುತ್ತಿದೆ...)",
    connectingSub: "Please wait a moment",
    start: "Start Talking (ಮಾತನಾಡಿ)",
    end: "End Call (ಕರೆ ಮುಗಿಸಿ)",
    hint: "After pressing, just speak normally. Sakhi is listening.",
    chooseLang: "Select Language / ಭಾಷೆ ಆಯ್ಕೆ ಮಾಡಿ",
    micNote: "Please allow microphone access (ಮೈಕ್ರೊಫೋನ್ ಅನುಮತಿ ನೀಡಿ)",
  },
  hindi: {
    tagline: "Maternal Health Voice Assistant",
    taglineSub: "माँ के स्वास्थ्य की सहायक",
    idleTitle: "Ready to Assist (बात करने के लिए तैयार)",
    idleSub: "Press the green button below (नीचे हरा बटन दबाएँ)",
    connecting: "Connecting... (जुड़ रहे हैं...)",
    connectingSub: "Please wait a moment",
    start: "Start Talking (बात करें)",
    end: "End Call (कॉल समाप्त करें)",
    hint: "After pressing, just speak normally. Sakhi is listening.",
    chooseLang: "Select Language / भाषा चुनें",
    micNote: "Please allow microphone access (माइक्रोफ़ोन की अनुमति दें)",
  },
} as const;

// The old full-width RISK_COPY banner (title + body paragraph per risk
// level) is retired with this redesign, not replaced 1:1: SakhiHeader's
// pulsing risk pill is the persistent visual signal now, and it is
// deliberately NOT the primary safety channel - sakhi_tools.py's
// _speak_instruction already makes the model speak the equivalent
// first-aid/referral guidance out loud for every HIGH_RISK turn. A second,
// silent-until-read paragraph of text was defense-in-depth on a screen that
// no longer has a spare full-width band to put it in (the four sections
// below intentionally sum to exactly one viewport).

/* Bridges LiveKit room events into the log rail. */
// Formats one detail value for the log rail's key=value strip. Handles the
// shapes agent.py's pipeline_event() actually produces (see _jsonable in
// agent.py): a caught exception serialises to {type, message}, not a plain
// string, and anything with no bespoke JSON shape (a VADMetrics dataclass,
// for instance) falls back to Python's own str() - already a string by the
// time it reaches here, so this only needs to handle what JSON itself can
// carry: string, number, boolean, null, plain object, array.
function formatDetailValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(2);
  if (typeof value === "boolean") return value ? "true" : "false";
  if (Array.isArray(value)) return value.map(formatDetailValue).join(", ");
  if (typeof value === "object") {
    const obj = value as Record<string, unknown>;
    if (typeof obj.type === "string" && typeof obj.message === "string") {
      return `${obj.type}: ${obj.message}`;      // a serialised exception
    }
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }
  return String(value);
}

function RoomLogger({ onLog }: { onLog: (text: string, kind?: LogMessage["kind"]) => void }) {
  const room = useRoomContext();

  useEffect(() => {
    if (!room) return;
    const onJoin = (p: any) => onLog(`Participant joined: ${p.identity}`, "ok");
    const onLeave = (p: any) => onLog(`Participant left: ${p.identity}`, "warn");
    const onTrack = (_t: any, _pub: any, p: any) => onLog(`Track subscribed from ${p.identity}`, "ok");
    const onDisc = () => onLog("Room disconnected", "warn");

    room.on(RoomEvent.ParticipantConnected, onJoin);
    room.on(RoomEvent.ParticipantDisconnected, onLeave);
    room.on(RoomEvent.TrackSubscribed, onTrack);
    room.on(RoomEvent.Disconnected, onDisc);
    onLog("WebRTC connection established", "ok");

    return () => {
      room.off(RoomEvent.ParticipantConnected, onJoin);
      room.off(RoomEvent.ParticipantDisconnected, onLeave);
      room.off(RoomEvent.TrackSubscribed, onTrack);
      room.off(RoomEvent.Disconnected, onDisc);
    };
  }, [room, onLog]);

  return null;
}

/* ---------------------------------------------------------------------- */

export default function Home() {
  const [token, setToken] = useState<string | null>(null);
  const [url, setUrl] = useState<string | null>(null);
  const [workerUrl, setWorkerUrl] = useState<string | null>(null);
  const [lang, setLang] = useState<LangKey>("english");
  const [connecting, setConnecting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [room, setRoom] = useState<Room | null>(null);
  const [risk, setRisk] = useState<RiskLevel | null>(null);
  // SakhiHeader's centre label (Module: rural redesign, 2026-08-30) - first
  // known from whichever of "Patient found" (search_patient),
  // "Mother registered" (register_mother), or "Identity resolved via
  // frontend" (the name-fallback RPC) fires first in a given call. The old
  // per-question intake progress bar this design replaces is not carried
  // over - LiveIntakeDrawer's compact grid is the new "how much is known"
  // signal, and there is no spare vertical band left for a second one.
  const [motherName, setMotherName] = useState<string | null>(null);
  const [motherAge, setMotherAge] = useState<number | null>(null);
  // Shown after one failed voice name-lookup - see LiveIntakeDrawer's name-
  // fallback drawer and agent.py's "submit_identity" RPC handler. Set from
  // the same log-poll loop as `risk` above; cleared once the RPC succeeds.
  const [showIdentityBox, setShowIdentityBox] = useState(false);
  // Defaults closed (2026-09-04) - raw pipeline_event JSON (provider names,
  // timings, tracebacks) is developer telemetry, not something an ASHA
  // worker using this screen should see by default. The existing "Logs ›"
  // rail toggle (below) already gives a technician a one-click way back in
  // when they need it - only the DEFAULT was wrong, the mechanism was fine.
  const [logsOpen, setLogsOpen] = useState(false);
  const roomRef = useRef<Room | null>(null);

  const [logs, setLogs] = useState<LogMessage[]>([]);
  const logsCursorRef = useRef<number>(-1);
  const logIdRef = useRef(0);
  const logsEndRef = useRef<HTMLDivElement>(null);

  // Low-power mode (Module C, 2026-08-30): heuristic default, explicit
  // manual override, persisted per-device - see guessLowPowerDefault above.
  //
  // Starts false, corrected in an effect below - NOT a lazy useState
  // initializer, even though this toggle's own checkbox is a controlled
  // input. This one IS rendered on the very first paint (outside the
  // connected-call gate, unlike AudioOrb's own prefers-reduced-motion read),
  // so it goes through real SSR where `window`/localStorage don't exist;
  // React hydration deliberately does not force a controlled checkbox's
  // `checked` DOM property to match a client-only initializer value (to
  // avoid clobbering browser autofill), so a value that can only be known
  // client-side has to arrive via a post-mount effect here, not the
  // initializer, or a stored "1" would silently render unchecked after
  // every reload - confirmed against a real reload during this session.
  const [lowPowerMode, setLowPowerMode] = useState(false);
  useEffect(() => {
    setLowPowerMode(guessLowPowerDefault());
  }, []);
  const toggleLowPower = useCallback(() => {
    setLowPowerMode((prev) => {
      const next = !prev;
      try {
        window.localStorage?.setItem(LOW_POWER_STORAGE_KEY, next ? "1" : "0");
      } catch {
        /* private browsing / storage disabled - the toggle still works for this session */
      }
      return next;
    });
  }, []);

  // Live Form Sync (Module C, 2026-08-30) - verified slot values, tap to
  // correct (the correction modal itself now lives inside SakhiLiveSession,
  // which is why there is no `correctingField` state here anymore - this is
  // just the data feeding SakhiSessionProps.intakeData/symptomFlags).
  // Cleared whenever a fresh call starts.
  const [liveForm, setLiveForm] = useState<LiveFormValues | null>(null);

  // "View visit summary" (Module C, 2026-08-30): shown ~10s after end_call,
  // matching the time save_call_report's async DB write needs to land.
  const [endedSessionId, setEndedSessionId] = useState<string | null>(null);
  const [showSummaryPrompt, setShowSummaryPrompt] = useState(false);
  const [showSummaryCard, setShowSummaryCard] = useState(false);

  const t = COPY[lang];
  const connected = Boolean(token && url);

  const addLog = useCallback((text: string, kind: LogMessage["kind"] = "info") => {
    const time = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    setLogs((prev) => [...prev.slice(-399), { id: logIdRef.current++, time, text, kind }]);
  }, []);

  useEffect(() => {
    let cancelled = false;
    let inFlight = false;

    const bootstrap = async () => {
      try {
        const res = await fetch("/api/logs?since=latest");
        const data = await res.json();
        logsCursorRef.current = typeof data.next === "number" ? data.next : 0;
      } catch {
        logsCursorRef.current = 0;
      }
    };

    const poll = async () => {
      if (logsCursorRef.current < 0 || inFlight) return;
      inFlight = true;
      try {
        const res = await fetch(`/api/logs?since=${logsCursorRef.current}`, { cache: "no-store" });
        const data = await res.json();
        if (cancelled || !data || !Array.isArray(data.events)) return;
        logsCursorRef.current = typeof data.next === "number" ? data.next : logsCursorRef.current;
        if (!data.events.length) return;

        const entries: LogMessage[] = data.events.map((ev: any) => {
          const time = ev.ts
            ? new Date(ev.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })
            : new Date().toLocaleTimeString();
          const status = String(ev.status || "info");
          const details =
            ev.details && typeof ev.details === "object" && Object.keys(ev.details).length
              ? (ev.details as Record<string, unknown>)
              : undefined;
          return {
            id: logIdRef.current++,
            time,
            text: `[${ev.stage || "worker"}] ${ev.label || ""} ${ev.message ? "— " + ev.message : ""}`,
            kind: (["ok", "warn", "error"].includes(status) ? status : "info") as LogMessage["kind"],
            details,
            stage: ev.stage ? String(ev.stage) : undefined,
          };
        });
        setLogs((prev) => [...prev.slice(-(400 - entries.length)), ...entries]);

        for (const ev of data.events) {
          const label = String(ev.label || "");
          const match = label.match(/^Triage:\s*(HIGH_RISK|MODERATE|OBSERVATION|ROUTINE)/);
          if (match) setRisk(match[1] as RiskLevel);

          if (
            label === "Patient found" ||
            label === "Mother registered" ||
            label === "Identity resolved via frontend"
          ) {
            const d = ev.details || {};
            if (typeof d.name === "string" && d.name) setMotherName(d.name);
            if (typeof d.age === "number") setMotherAge(d.age);
          }

          if (label === "Identity fallback: show text box") setShowIdentityBox(true);
          if (label === "Identity resolved via frontend") setShowIdentityBox(false);

          if (label === "Live form sync") {
            const d = ev.details || {};
            const symptoms = d.symptoms || {};
            setLiveForm({
              systolic: typeof d.systolic === "number" ? d.systolic : null,
              diastolic: typeof d.diastolic === "number" ? d.diastolic : null,
              gestational_weeks: typeof d.gestational_weeks === "number" ? d.gestational_weeks : null,
              symptoms: {
                headache: Boolean(symptoms.headache),
                blurry_vision: Boolean(symptoms.blurry_vision),
                swelling: Boolean(symptoms.swelling),
                vaginal_bleeding: Boolean(symptoms.vaginal_bleeding),
                severe_abdominal_pain: Boolean(symptoms.severe_abdominal_pain),
              },
            });
          }
        }
      } catch {
        /* transient poll failure */
      } finally {
        inFlight = false;
      }
    };

    logsCursorRef.current = -1;
    bootstrap().then(() => { if (!cancelled) poll(); });
    const interval = setInterval(poll, 1200);
    return () => { cancelled = true; clearInterval(interval); };
  }, []);

  // SSE for real-time log streaming (replaces polling when connected)
  useEffect(() => {
    if (!workerUrl || !connected) return;
    let eventSource: EventSource | null = null;
    let cancelled = false;

    const connectSSE = () => {
      try {
        eventSource = new EventSource(`${workerUrl}/events`);
        eventSource.onmessage = (event) => {
          if (cancelled) return;
          try {
            const ev = JSON.parse(event.data);
            // Skip heartbeat comments
            if (ev.type === "connected") return;

            const time = ev.ts
              ? new Date(ev.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })
              : new Date().toLocaleTimeString();
            const status = String(ev.status || "info");
            const details =
              ev.details && typeof ev.details === "object" && Object.keys(ev.details).length
                ? (ev.details as Record<string, unknown>)
                : undefined;
            const entry: LogMessage = {
              id: logIdRef.current++,
              time,
              text: `[${ev.stage || "worker"}] ${ev.label || ""} ${ev.message ? "— " + ev.message : ""}`,
              kind: (["ok", "warn", "error"].includes(status) ? status : "info") as LogMessage["kind"],
              details,
              stage: ev.stage ? String(ev.stage) : undefined,
            };
            setLogs((prev) => [...prev.slice(-399), entry]);

            // Handle special events
            const label = String(ev.label || "");
            const match = label.match(/^Triage:\s*(HIGH_RISK|MODERATE|OBSERVATION|ROUTINE)/);
            if (match) setRisk(match[1] as RiskLevel);

            if (
              label === "Patient found" ||
              label === "Mother registered" ||
              label === "Identity resolved via frontend"
            ) {
              const d = ev.details || {};
              if (typeof d.name === "string" && d.name) setMotherName(d.name);
              if (typeof d.age === "number") setMotherAge(d.age);
            }

            if (label === "Identity fallback: show text box") setShowIdentityBox(true);
            if (label === "Identity resolved via frontend") setShowIdentityBox(false);

            if (label === "Live form sync") {
              const d = ev.details || {};
              const symptoms = d.symptoms || {};
              setLiveForm({
                systolic: typeof d.systolic === "number" ? d.systolic : null,
                diastolic: typeof d.diastolic === "number" ? d.diastolic : null,
                gestational_weeks: typeof d.gestational_weeks === "number" ? d.gestational_weeks : null,
                symptoms: {
                  headache: Boolean(symptoms.headache),
                  blurry_vision: Boolean(symptoms.blurry_vision),
                  swelling: Boolean(symptoms.swelling),
                  vaginal_bleeding: Boolean(symptoms.vaginal_bleeding),
                  severe_abdominal_pain: Boolean(symptoms.severe_abdominal_pain),
                },
              });
            }
          } catch (e) {
            console.warn("Failed to parse SSE event:", e);
          }
        };

        eventSource.onerror = () => {
          if (!cancelled) {
            // Reconnect after 2s
            setTimeout(() => {
              if (!cancelled && eventSource) {
                eventSource.close();
                connectSSE();
              }
            }, 2000);
          }
        };
      } catch (e) {
        console.warn("SSE connection failed:", e);
      }
    };

    connectSSE();
    return () => {
      cancelled = true;
      if (eventSource) eventSource.close();
    };
  }, [workerUrl, connected]);

  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [logs]);

  const copyLogs = () => {
    // Details included, same reasoning as the on-screen render below - a
    // copied log that dropped the "where the breakage is" fields would be
    // no more useful for debugging than what was already on screen.
    navigator.clipboard?.writeText(
      logs
        .map((l) => {
          const lines = [`[${l.time}] ${l.text}`];
          if (l.details) {
            for (const [key, value] of Object.entries(l.details)) {
              lines.push(`    ${key}: ${formatDetailValue(value)}`);
            }
          }
          return lines.join("\n");
        })
        .join("\n")
    );
  };

  const startCall = async () => {
    if (connecting || roomRef.current) return;
    setConnecting(true);
    setError(null);
    setRisk(null);
    setMotherName(null);
    setMotherAge(null);
    setLiveForm(null);
    setShowSummaryPrompt(false);
    setShowSummaryCard(false);
    setEndedSessionId(null);
    // The debug log rail auto-collapses on every call start: it is a
    // supervisor/dev tool, not part of the redesigned ASHA-facing screen,
    // and on a narrow phone its mobile layout stacks BELOW the live session
    // rather than beside it - left open, its own height would push the
    // single-screen, no-scroll live session below the fold. Still
    // reachable via the "Logs ›" tab if a supervisor needs it mid-call.
    setLogsOpen(false);
    addLog("Requesting token from /api/token…", "info");

    try {
      // No provider/voice params - see the comment above. The backend's
      // TTS_PROVIDER env var decides, so a provider change never again
      // requires a matching frontend edit to actually take effect.
      const params = new URLSearchParams({ language: lang });
      const res = await fetch(`/api/token?${params.toString()}`);
      const data = await res.json();
      if (data.error) throw new Error(data.error);

      addLog(`Token issued for ${data.participant}`, "ok");
      const active = new Room();
      roomRef.current = active;
      setRoom(active);
      setToken(data.token);
      setUrl(data.url);
      if (data.worker_url) setWorkerUrl(data.worker_url);
    } catch (e: any) {
      addLog(`Connection failed: ${e.message}`, "error");
      setError(e.message);
      setConnecting(false);
    }
  };

  const endCall = useCallback(() => {
    // Captured before disconnect/null-out below: save_call_report (agent.py)
    // keys the persisted row by this LiveKit room name, and the "View visit
    // summary" button (Module C, 2026-08-30) needs it to fetch the MCP card
    // afterwards - see /api/reports/mcp-card.
    const sessionId = roomRef.current?.name || null;
    roomRef.current?.disconnect();
    roomRef.current = null;
    setRoom(null);
    setToken(null);
    setUrl(null);
    setConnecting(false);
    setRisk(null);
    setLiveForm(null);
    addLog("Call ended by user", "info");

    if (sessionId) {
      setEndedSessionId(sessionId);
      // 10s: the time save_call_report's async DB write (agent.py, call
      // teardown) needs to land before the card is fetchable - matches the
      // plan's timing ask.
      window.setTimeout(() => setShowSummaryPrompt(true), 10_000);
    }
  }, [addLog]);

  return (
    <div className={styles.shell} data-logs={logsOpen ? "open" : "closed"}>
      {/* ---------- Left log rail ---------- */}
      <aside className={styles.logRail}>
        {logsOpen ? (
          <>
            <div className={styles.logHead}>
              <span className={styles.logTitle}>System logs</span>
              <button className={styles.logCopy} onClick={copyLogs} title="Copy logs" aria-label="Copy logs">⧉</button>
              <button className={styles.logToggle} onClick={() => setLogsOpen(false)} title="Hide logs" aria-label="Hide logs">‹</button>
            </div>
            <div className={styles.logBody}>
              {logs.length === 0 && <div className={styles.logEmpty}>Waiting for worker events…</div>}
              {logs.map((l) => {
                // `error`/`traceback` get their own prominent block - these
                // are literally "where the breakage is". Everything else in
                // `details` is a compact key=value strip: which provider
                // handled this, how long it took, what value actually got
                // recorded - the full pipeline_event() payload that used to
                // be fetched and thrown away before it ever reached render.
                const { error, traceback, ...rest } =
                  (l.details as { error?: unknown; traceback?: unknown } & Record<string, unknown>) || {};
                const restEntries = Object.entries(rest);
                const hasFailure = error !== undefined || traceback !== undefined;
                return (
                  <div key={l.id} className={styles.logRow} data-kind={l.kind}>
                    <div>
                      <time>{l.time}</time>{l.text}
                    </div>
                    {restEntries.length > 0 && (
                      <div className={styles.logDetails}>
                        {restEntries.map(([key, value]) => (
                          <span key={key} className={styles.logDetailPair}>
                            <span className={styles.logDetailKey}>{key}</span>=
                            {formatDetailValue(value)}
                          </span>
                        ))}
                      </div>
                    )}
                    {hasFailure && (
                      <pre className={styles.logFailure}>
                        {error !== undefined ? formatDetailValue(error) : null}
                        {error !== undefined && traceback !== undefined ? "\n" : null}
                        {traceback !== undefined ? String(traceback) : null}
                      </pre>
                    )}
                  </div>
                );
              })}
              <div ref={logsEndRef} />
            </div>
          </>
        ) : (
          <button
            className={styles.railClosed}
            onClick={() => setLogsOpen(true)}
            title="Show logs"
            aria-label="Show system logs"
          >
            Logs ›
          </button>
        )}
      </aside>

      {/* ---------- Main stage ---------- */}
      {/* SakhiLiveSession owns its own exact-100dvh box with no padding
         (styles.stageLive) - the brand header/low-power toggle belong to
         the pre-call screen only (styles.stage), not the redesigned
         in-call layout, which has no spare band for either. */}
      <main className={connected ? styles.stageLive : styles.stage}>
        {!connected && (
          <header className={styles.header}>
            <div className={styles.brandMark} aria-hidden="true">S</div>
            <div className={styles.brandText}>
              <h1 className={styles.brandName}>Sakhi · AI Clinical Co-Pilot</h1>
              <p className={styles.brandSub}>{t.tagline} — {t.taglineSub}</p>
            </div>
            <label className={styles.lowPowerToggle} title="Use a low-power orb instead of the video avatar during the call - saves battery/heat on older phones">
              <input type="checkbox" checked={lowPowerMode} onChange={toggleLowPower} />
              Low-power mode
            </label>
            <span className={styles.linkPill}>
              <span className={styles.dot} data-live={connected} />
              {connected ? "Connected" : "Standby"}
            </span>
          </header>
        )}

        {connected && room ? (
          <LiveKitRoom
            room={room}
            token={token!}
            serverUrl={url!}
            connect
            audio
            video={false}
            onConnected={() => { setConnecting(false); addLog("Connected to Sakhi voice session", "ok"); }}
            onDisconnected={endCall}
            onError={(e) => { addLog(`Room error: ${e.message}`, "error"); setError(e.message); }}
            style={{ width: "100%", height: "100%" }}
          >
            <RoomAudioRenderer />
            <RoomLogger onLog={addLog} />
            <SakhiLiveSession
              language={lang}
              motherName={motherName ?? undefined}
              motherAge={motherAge ?? undefined}
              // No assessment has run yet at the very start of a call - a
              // calm ROUTINE default rather than inventing a fifth "unknown"
              // pill variant the brief's TriageRisk type has no room for.
              triageRisk={risk ?? "ROUTINE"}
              intakeData={{
                bp_systolic: liveForm?.systolic ?? undefined,
                bp_diastolic: liveForm?.diastolic ?? undefined,
                symptoms: liveForm
                  ? (Object.keys(liveForm.symptoms) as Array<keyof LiveFormValues["symptoms"]>)
                      .filter((key) => liveForm.symptoms[key])
                      .map((key) => FIELD_CHIP_LABELS[key])
                  : [],
                gestational_month: liveForm?.gestational_weeks ?? undefined,
              }}
              symptomFlags={liveForm?.symptoms}
              identityFallbackTriggered={showIdentityBox}
              lowPowerMode={lowPowerMode}
              onManualSlotEdit={(field, value) => {
                // Optimistic local update - the next poll tick will confirm
                // it via the live_form_sync event apply_field_correction
                // also emits (sakhi_tools.py).
                setLiveForm((prev) => {
                  if (!prev) return prev;
                  if (field === "systolic" || field === "diastolic" || field === "gestational_weeks") {
                    return { ...prev, [field]: value as number };
                  }
                  return { ...prev, symptoms: { ...prev.symptoms, [field]: value as boolean } };
                });
              }}
              onNameSubmit={() => setShowIdentityBox(false)}
              onEndCall={endCall}
            />
          </LiveKitRoom>
        ) : (
          <>
            {showSummaryPrompt && endedSessionId && (
              <div className="w-full max-w-md rounded-2xl border border-sage/40 bg-sage/10 px-4 py-3 text-center">
                <p className="m-0 text-sm text-forest">The last call&apos;s visit summary is ready.</p>
                <button
                  type="button"
                  onClick={() => setShowSummaryCard(true)}
                  className="mt-2 rounded-xl bg-forest px-4 py-2 text-sm font-bold text-white"
                >
                  View visit summary
                </button>
              </div>
            )}

            <div className="flex w-48 h-48 md:w-56 md:h-56 flex-col items-center justify-center rounded-full border-4 border-sand bg-cream shadow-xl">
              {connecting ? (
                <div className="flex flex-col items-center gap-2 px-4 text-center">
                  <div className={styles.spinner} />
                  <p className="m-0 text-base font-semibold text-forest">{t.connecting}</p>
                  <p className="m-0 text-xs text-forest/60">{t.connectingSub}</p>
                </div>
              ) : (
                <div className="flex flex-col items-center gap-2 px-4 text-center">
                  <div className="text-5xl" aria-hidden="true">👩‍⚕️</div>
                  <p className="m-0 text-base font-semibold text-forest">{t.idleTitle}</p>
                </div>
              )}
            </div>

            <div className="flex w-full max-w-md flex-col items-center gap-3">
              <button
                onClick={startCall}
                disabled={connecting}
                className="flex w-full items-center justify-center gap-2 rounded-full bg-forest px-6 py-4 text-lg font-bold text-white shadow-lg transition active:scale-[0.98] disabled:opacity-60 sm:text-xl"
              >
                <span aria-hidden="true">🎤</span> {connecting ? t.connecting : t.start}
              </button>
              <p className="m-0 text-center text-sm text-forest/70">{t.hint}</p>
              <p className="m-0 text-center text-xs text-forest/50">{t.micNote}</p>

              {error && (
                <div className="w-full rounded-xl border border-rose-300 bg-rose-50 px-3 py-2 text-sm text-rose-800">
                  {error}
                </div>
              )}

              <p className="mt-2 text-xs font-bold uppercase tracking-wide text-forest/60">{t.chooseLang}</p>
              <div className="flex w-full flex-wrap justify-center gap-2">
                {(["english", "kannada", "hindi"] as const).map((key) => (
                  <button
                    key={key}
                    onClick={() => setLang(key)}
                    disabled={connecting}
                    aria-pressed={lang === key}
                    className={`rounded-full border px-4 py-2 text-sm font-semibold transition disabled:opacity-50 ${
                      lang === key
                        ? "border-terracotta bg-terracotta text-white"
                        : "border-sand bg-white/70 text-forest"
                    }`}
                  >
                    {key === "english" ? "English" : key === "kannada" ? "ಕನ್ನಡ (Kannada)" : "हिंदी (Hindi)"}
                  </button>
                ))}
              </div>
            </div>
          </>
        )}
      </main>

      {showSummaryCard && endedSessionId && (
        <VisitSummaryCard sessionId={endedSessionId} onClose={() => setShowSummaryCard(false)} />
      )}
    </div>
  );
}
