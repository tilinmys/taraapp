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
 * - The three <select> dropdowns (language / TTS provider / TTS voice) are
 *   gone from the main flow. TTS provider and voice are engineering knobs, not
 *   field decisions - the backend runs Sarvam bulbul:v3 regardless - and a
 *   dropdown hides its options behind a tap on a small touch screen. Language
 *   is now two large pills.
 * - The Three.js/WebGL canvas is gone. It shipped a 3D model and ran a
 *   continuous render loop on a device class that cannot afford either; the
 *   Tavus video track replaces it with a plain <video> element.
 * - The log console moved from the main column to a quiet, collapsible left
 *   rail, so it stays available for supervisors without competing with the call.
 * - Status is now icon + colour + Kannada word together, never colour alone.
 */

import { useState, useRef, useEffect, useCallback, useMemo } from "react";
import {
  LiveKitRoom,
  RoomAudioRenderer,
  VideoTrack,
  useTracks,
  useVoiceAssistant,
  useRoomContext,
} from "@livekit/components-react";
import { Room, RoomEvent, Track } from "livekit-client";
import "@livekit/components-styles";
import styles from "./page.module.css";

type LangKey = "english" | "kannada" | "hindi";
type TtsProvider = "sarvam" | "gnani";

const VOICES: Record<TtsProvider, { id: string; label: string; note: string }[]> = {
  sarvam: [
    { id: "manisha", label: "Manisha", note: "Warm, clear — default voice" },
    { id: "vidya",   label: "Vidya",   note: "Calm, balanced" },
    { id: "anushka", label: "Anushka", note: "Bright, friendly" },
    { id: "arya",    label: "Arya",    note: "Soft, reassuring" },
    { id: "ritu",    label: "Ritu",    note: "Steady, professional" },
  ],
  gnani: [
    { id: "Janaki",  label: "Janaki",  note: "Warm, steady" },
    { id: "Nayana",  label: "Nayana",  note: "Soft, gentle" },
    { id: "Lavanya", label: "Lavanya", note: "Clear, balanced" },
    { id: "Vidya",   label: "Vidya",   note: "Calm, professional" },
    { id: "Lalita",  label: "Lalita",  note: "Bright, friendly" },
    { id: "Kaveri",  label: "Kaveri",  note: "Even pace" },
  ],
};
type RiskLevel = "HIGH_RISK" | "MODERATE" | "OBSERVATION" | "ROUTINE";

interface LogMessage {
  id: number;
  time: string;
  text: string;
  kind: "info" | "ok" | "warn" | "error";
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
    chooseVoice: "Choose Voice Engine",
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
    chooseVoice: "Choose Voice / ಧ್ವನಿ ಆಯ್ಕೆ ಮಾಡಿ",
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
    chooseVoice: "Choose Voice / आवाज़ चुनें",
    micNote: "Please allow microphone access (माइक्रोफ़ोन की अनुमति दें)",
  },
} as const;

const STATE_COPY: Record<string, { label: string; icon: string }> = {
  listening: { label: "Listening...", icon: "👂" },
  thinking: { label: "Thinking...", icon: "💭" },
  speaking: { label: "Speaking...", icon: "🗣️" },
  idle: { label: "Ready", icon: "🌸" },
};

const RISK_COPY: Record<RiskLevel, { title: string; body: string; icon: string }> = {
  HIGH_RISK: {
    title: "High Risk — Immediate Hospital Referral Required",
    body: "Refer mother to District Hospital immediately. Specialist auto-escalation active.",
    icon: "🔴",
  },
  MODERATE: {
    title: "Moderate Risk — Blood Pressure Elevated",
    body: "Raised blood pressure detected without danger symptoms. Re-check within 48 hours.",
    icon: "🟡",
  },
  OBSERVATION: {
    title: "Under Observation — Symptoms Reported",
    body: "Blood pressure normal, but symptoms reported. Continue monitoring closely.",
    icon: "🟡",
  },
  ROUTINE: {
    title: "Routine Care — All Vital Signs Normal",
    body: "Blood pressure and screening are normal. Schedule next routine ANC checkup.",
    icon: "🟢",
  },
};

/* -------------------------------------------------------------------------
 * Avatar stage — renders the Tavus video track.
 *
 * Tavus joins the LiveKit room as its OWN participant publishing a camera
 * track (echo mode: it receives Sakhi's Sarvam audio and returns lip-synced
 * video). So we take the first remote camera track rather than looking at the
 * agent participant. If no video arrives - Tavus down, or SAKHI_AVATAR=none -
 * this falls back to the calm idle art and the call continues on audio, which
 * is the same degradation rule the backend follows.
 * ---------------------------------------------------------------------- */
function AvatarStage({ lang }: { lang: LangKey }) {
  const tracks = useTracks([Track.Source.Camera], { onlySubscribed: true });
  const avatarTrack = tracks.find((t) => !t.participant.isLocal);
  const t = COPY[lang];

  if (!avatarTrack) {
    return (
      <div className={styles.avatarCard}>
        <div className={styles.idleArt}>
          <div className={styles.idleFace} aria-hidden="true">👩‍⚕️</div>
          <p className={styles.idleLine}>{STATE_COPY.idle.label}</p>
          <p className={styles.idleSub}>{t.hint}</p>
        </div>
      </div>
    );
  }

  return (
    <div className={styles.avatarCard}>
      <VideoTrack trackRef={avatarTrack} />
    </div>
  );
}

function StatusPill({ lang }: { lang: LangKey }) {
  const { state } = useVoiceAssistant();
  const key = state === "listening" || state === "thinking" || state === "speaking" ? state : "idle";
  const copy = STATE_COPY[key];

  return (
    <div className={styles.statusPill} data-state={key} role="status" aria-live="polite">
      <span className={styles.statusIcon} aria-hidden="true">{copy.icon}</span>
      <span>{copy.label}</span>
      {key === "listening" && <span className={styles.pulse} aria-hidden="true" />}
    </div>
  );
}

/* Bridges LiveKit room events into the log rail. */
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
  const [lang, setLang] = useState<LangKey>("english");
  const [provider, setProvider] = useState<TtsProvider>("sarvam");
  const [voice, setVoice] = useState<string>("manisha");
  const [connecting, setConnecting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [room, setRoom] = useState<Room | null>(null);
  const [risk, setRisk] = useState<RiskLevel | null>(null);
  // Live position in the clinical intake, derived from the worker's
  // intake_armed / intake_answered pipeline events. The ASHA worker can see
  // which question Sakhi is on and how much of the screening remains.
  const [intake, setIntake] = useState<{ done: number; total: number; question: string } | null>(null);
  const [logsOpen, setLogsOpen] = useState(true);
  const roomRef = useRef<Room | null>(null);

  const [logs, setLogs] = useState<LogMessage[]>([]);
  const logsCursorRef = useRef<number>(-1);
  const logIdRef = useRef(0);
  const logsEndRef = useRef<HTMLDivElement>(null);

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
          return {
            id: logIdRef.current++,
            time,
            text: `[${ev.stage || "worker"}] ${ev.label || ""} ${ev.message ? "— " + ev.message : ""}`,
            kind: (["ok", "warn", "error"].includes(status) ? status : "info") as LogMessage["kind"],
          };
        });
        setLogs((prev) => [...prev.slice(-(400 - entries.length)), ...entries]);

        for (const ev of data.events) {
          const label = String(ev.label || "");
          const match = label.match(/^Triage:\s*(HIGH_RISK|MODERATE|OBSERVATION|ROUTINE)/);
          if (match) setRisk(match[1] as RiskLevel);

          // "q07 (6/26): Does she know her estimated due date?"
          if (label === "Intake question armed") {
            const m = String(ev.message || "").match(/^\S+\s+\((\d+)\/(\d+)\):\s*(.+)$/);
            if (m) setIntake({ done: Number(m[1]), total: Number(m[2]), question: m[3] });
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

  useEffect(() => {
    logsEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [logs]);

  const copyLogs = () => {
    navigator.clipboard?.writeText(logs.map((l) => `[${l.time}] ${l.text}`).join("\n"));
  };

  const startCall = async () => {
    if (connecting || roomRef.current) return;
    setConnecting(true);
    setError(null);
    setRisk(null);
    setIntake(null);
    addLog("Requesting token from /api/token…", "info");

    try {
      const params = new URLSearchParams({ language: lang, provider, voice });
      const res = await fetch(`/api/token?${params.toString()}`);
      const data = await res.json();
      if (data.error) throw new Error(data.error);

      addLog(`Token issued for ${data.participant}`, "ok");
      const active = new Room();
      roomRef.current = active;
      setRoom(active);
      setToken(data.token);
      setUrl(data.url);
    } catch (e: any) {
      addLog(`Connection failed: ${e.message}`, "error");
      setError(e.message);
      setConnecting(false);
    }
  };

  const endCall = useCallback(() => {
    roomRef.current?.disconnect();
    roomRef.current = null;
    setRoom(null);
    setToken(null);
    setUrl(null);
    setConnecting(false);
    setRisk(null);
    setIntake(null);
    addLog("Call ended by user", "info");
  }, [addLog]);

  const riskCopy = useMemo(() => (risk ? RISK_COPY[risk] : null), [risk]);

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
              {logs.map((l) => (
                <div key={l.id} className={styles.logRow} data-kind={l.kind}>
                  <time>{l.time}</time>{l.text}
                </div>
              ))}
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
      <main className={styles.stage}>
        <header className={styles.header}>
          <div className={styles.brandMark} aria-hidden="true">S</div>
          <div className={styles.brandText}>
            <h1 className={styles.brandName}>Sakhi · AI Clinical Co-Pilot</h1>
            <p className={styles.brandSub}>{t.tagline} — {t.taglineSub}</p>
          </div>
          <span className={styles.linkPill}>
            <span className={styles.dot} data-live={connected} />
            {connected ? "Connected" : "Standby"}
          </span>
        </header>

        {riskCopy && (
          <div className={styles.riskBanner} data-level={risk!} role="alert">
            <span className={styles.riskIcon} aria-hidden="true">{riskCopy.icon}</span>
            <div>
              <p className={styles.riskTitle}>{riskCopy.title}</p>
              <p className={styles.riskBody}>{riskCopy.body}</p>
            </div>
          </div>
        )}

        {intake && (
          <div className={styles.intakePanel}>
            <div className={styles.intakeHead}>
              <span className={styles.intakeLabel}>Clinical intake</span>
              <span className={styles.intakeCount}>{intake.done} / {intake.total}</span>
            </div>
            <div
              className={styles.intakeTrack}
              role="progressbar"
              aria-valuenow={intake.done}
              aria-valuemin={0}
              aria-valuemax={intake.total}
            >
              <div
                className={styles.intakeFill}
                style={{ width: `${Math.round((intake.done / intake.total) * 100)}%` }}
              />
            </div>
            <p className={styles.intakeQuestion}>{intake.question}</p>
          </div>
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
            style={{ width: "100%", display: "flex", flexDirection: "column", alignItems: "center", gap: 18 }}
          >
            <RoomAudioRenderer />
            <RoomLogger onLog={addLog} />
            <AvatarStage lang={lang} />
            <StatusPill lang={lang} />
            <div className={styles.controls}>
              <button className={`${styles.bigButton} ${styles.end}`} onClick={endCall}>
                <span aria-hidden="true">✕</span> {t.end}
              </button>
              <p className={styles.buttonHint}>Click to finish the consultation and generate summary</p>
            </div>
          </LiveKitRoom>
        ) : (
          <>
            <div className={styles.avatarCard}>
              {connecting ? (
                <div className={styles.connectingWrap}>
                  <div className={styles.spinner} />
                  <p className={styles.idleLine}>{t.connecting}</p>
                  <p className={styles.idleSub}>{t.connectingSub}</p>
                </div>
              ) : (
                <div className={styles.idleArt}>
                  <div className={styles.idleFace} aria-hidden="true">👩‍⚕️</div>
                  <p className={styles.idleLine}>{t.idleTitle}</p>
                  <p className={styles.idleSub}>{t.idleSub}</p>
                </div>
              )}
            </div>

            <div className={styles.controls}>
              <button
                className={`${styles.bigButton} ${styles.start}`}
                onClick={startCall}
                disabled={connecting}
              >
                <span aria-hidden="true">🎤</span> {connecting ? t.connecting : t.start}
              </button>
              <p className={styles.buttonHint}>{t.hint}</p>
              <p className={styles.buttonHint}>{t.micNote}</p>

              {error && <div className={styles.errorBox}>{error}</div>}

              <p className={styles.sectionLabel}>{t.chooseLang}</p>
              <div className={styles.langRow}>
                <button
                  className={styles.langPill}
                  data-active={lang === "english"}
                  onClick={() => setLang("english")}
                  disabled={connecting}
                >
                  English
                </button>
                <button
                  className={styles.langPill}
                  data-active={lang === "kannada"}
                  onClick={() => setLang("kannada")}
                  disabled={connecting}
                >
                  ಕನ್ನಡ (Kannada)
                </button>
                <button
                  className={styles.langPill}
                  data-active={lang === "hindi"}
                  onClick={() => setLang("hindi")}
                  disabled={connecting}
                >
                  हिंदी (Hindi)
                </button>
              </div>

              <p className={styles.sectionLabel}>{t.chooseVoice}</p>
              <div className={styles.langRow}>
                {(["sarvam", "gnani"] as TtsProvider[]).map((p) => (
                  <button
                    key={p}
                    className={styles.voicePill}
                    data-active={provider === p}
                    onClick={() => { setProvider(p); setVoice(VOICES[p][0].id); }}
                    disabled={connecting}
                  >
                    {p === "sarvam" ? "Sarvam AI (Neural)" : "Gnani AI"}
                  </button>
                ))}
              </div>
              <div className={styles.voiceRow}>
                {VOICES[provider].map((v) => (
                  <button
                    key={v.id}
                    className={styles.voicePill}
                    data-active={voice === v.id}
                    onClick={() => setVoice(v.id)}
                    disabled={connecting}
                    title={v.note}
                  >
                    {v.label}
                    <span className={styles.voiceNote}>{v.note}</span>
                  </button>
                ))}
              </div>
            </div>
          </>
        )}
      </main>
    </div>
  );
}
