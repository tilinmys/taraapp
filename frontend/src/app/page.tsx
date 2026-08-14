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

type LangKey = "kannada" | "hindi";
type RiskLevel = "HIGH_RISK" | "MODERATE" | "OBSERVATION" | "ROUTINE";

interface LogMessage {
  id: number;
  time: string;
  text: string;
  kind: "info" | "ok" | "warn" | "error";
}

/* -------------------------------------------------------------------------
 * Copy. Kannada is the primary string; English is the small subtitle beneath
 * it, never a replacement. Kept in one table so a reviewer who speaks Kannada
 * can check every user-facing word in one place without reading JSX.
 * ---------------------------------------------------------------------- */
const COPY = {
  kannada: {
    tagline: "ತಾಯಿಯ ಆರೋಗ್ಯ ಸಹಾಯಕಿ",
    taglineEn: "Maternal health assistant for ASHA workers",
    idleTitle: "ಮಾತನಾಡಲು ಸಿದ್ಧ",
    idleSub: "ಕೆಳಗಿನ ಹಸಿರು ಗುಂಡಿಯನ್ನು ಒತ್ತಿರಿ",
    idleSubEn: "Press the green button below to start",
    connecting: "ಸಂಪರ್ಕಿಸುತ್ತಿದೆ...",
    connectingEn: "Connecting, please wait",
    start: "ಮಾತನಾಡಿ",
    startEn: "Start talking",
    end: "ಕರೆ ಮುಗಿಸಿ",
    endEn: "End call",
    hint: "ಗುಂಡಿ ಒತ್ತಿದ ನಂತರ ಸಾಮಾನ್ಯವಾಗಿ ಮಾತನಾಡಿ. ಸಖಿ ಕೇಳುತ್ತಾಳೆ.",
    hintEn: "After pressing, just speak normally. Sakhi is listening.",
    chooseLang: "ಭಾಷೆ ಆಯ್ಕೆ ಮಾಡಿ / Choose language",
    micNote: "ಮೈಕ್ರೊಫೋನ್ ಅನುಮತಿ ನೀಡಿ",
    micNoteEn: "Please allow microphone access",
  },
  hindi: {
    tagline: "माँ के स्वास्थ्य की सहायक",
    taglineEn: "Maternal health assistant for ASHA workers",
    idleTitle: "बात करने के लिए तैयार",
    idleSub: "नीचे हरा बटन दबाएँ",
    idleSubEn: "Press the green button below to start",
    connecting: "जुड़ रहे हैं...",
    connectingEn: "Connecting, please wait",
    start: "बात करें",
    startEn: "Start talking",
    end: "कॉल समाप्त करें",
    endEn: "End call",
    hint: "बटन दबाने के बाद सामान्य रूप से बोलें। सखी सुन रही है।",
    hintEn: "After pressing, just speak normally. Sakhi is listening.",
    chooseLang: "भाषा चुनें / Choose language",
    micNote: "माइक्रोफ़ोन की अनुमति दें",
    micNoteEn: "Please allow microphone access",
  },
} as const;

/* Agent state -> what the worker sees. Icon + colour + word, so the state is
   readable without relying on text or on colour perception alone. */
const STATE_COPY: Record<string, { kn: string; hi: string; en: string; icon: string }> = {
  listening: { kn: "ಕೇಳುತ್ತಿದ್ದೇನೆ", hi: "सुन रही हूँ", en: "Listening", icon: "👂" },
  thinking: { kn: "ಯೋಚಿಸುತ್ತಿದ್ದೇನೆ", hi: "सोच रही हूँ", en: "Thinking", icon: "💭" },
  speaking: { kn: "ಮಾತನಾಡುತ್ತಿದ್ದೇನೆ", hi: "बोल रही हूँ", en: "Speaking", icon: "🗣️" },
  idle: { kn: "ಸಿದ್ಧ", hi: "तैयार", en: "Ready", icon: "🌸" },
};

/* Risk copy. Deliberately instructional, addressed to the ASHA worker, and it
   never names a diagnosis - Sakhi is decision support, not a practitioner. */
const RISK_COPY: Record<RiskLevel, { kn: string; hi: string; en: string; icon: string }> = {
  HIGH_RISK: {
    kn: "ತಕ್ಷಣ ಆಸ್ಪತ್ರೆಗೆ ಕರೆದೊಯ್ಯಿರಿ",
    hi: "तुरंत अस्पताल ले जाएँ",
    en: "High risk — refer to Bidar District Hospital now. A specialist is being connected.",
    icon: "🔴",
  },
  MODERATE: {
    kn: "48 ಗಂಟೆಗಳಲ್ಲಿ ಬಿ.ಪಿ. ಮತ್ತೆ ಪರೀಕ್ಷಿಸಿ",
    hi: "48 घंटे में बी.पी. दोबारा जाँचें",
    en: "Raised blood pressure, no danger symptoms. Re-check within 48 hours.",
    icon: "🟡",
  },
  OBSERVATION: {
    kn: "ಲಕ್ಷಣಗಳನ್ನು ಗಮನಿಸುತ್ತಿರಿ",
    hi: "लक्षणों पर नज़र रखें",
    en: "Blood pressure normal, but symptoms reported. Keep watching.",
    icon: "🟡",
  },
  ROUTINE: {
    kn: "ಎಲ್ಲವೂ ಸಾಮಾನ್ಯವಾಗಿದೆ",
    hi: "सब सामान्य है",
    en: "Reading and screening both normal. Confirm the next ANC visit.",
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
          <p className={styles.idleLine}>{STATE_COPY.idle[lang === "kannada" ? "kn" : "hi"]}</p>
          <p className={styles.idleSub}>{t.hintEn}</p>
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
      <span>{lang === "kannada" ? copy.kn : copy.hi}</span>
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
  const [lang, setLang] = useState<LangKey>("kannada");
  const [connecting, setConnecting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [room, setRoom] = useState<Room | null>(null);
  const [risk, setRisk] = useState<RiskLevel | null>(null);
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

  /* Poll worker pipeline events. Same plumbing as before; additionally, the
     triage labels emitted by ClinicalToolset drive the risk banner, so the
     worker's deterministic decision is what the ASHA worker sees - the UI
     never infers a risk level for itself. */
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
        }
      } catch {
        /* transient poll failure is not worth surfacing to a field user */
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
    addLog("Requesting token from /api/token…", "info");

    try {
      const params = new URLSearchParams({ language: lang, provider: "sarvam", voice: "pooja" });
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
          <div className={styles.brandMark} aria-hidden="true">ಸ</div>
          <div className={styles.brandText}>
            <h1 className={styles.brandName}>ಸಖಿ · Sakhi</h1>
            <p className={styles.brandSub}>{t.tagline}</p>
          </div>
          <span className={styles.linkPill}>
            <span className={styles.dot} data-live={connected} />
            {connected ? "Live" : "Offline"}
          </span>
        </header>

        {riskCopy && (
          <div className={styles.riskBanner} data-level={risk!} role="alert">
            <span className={styles.riskIcon} aria-hidden="true">{riskCopy.icon}</span>
            <div>
              <p className={styles.riskTitle}>{lang === "kannada" ? riskCopy.kn : riskCopy.hi}</p>
              <p className={styles.riskBody}>{riskCopy.en}</p>
            </div>
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
            onConnected={() => { setConnecting(false); addLog("Connected to Sakhi", "ok"); }}
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
              <p className={styles.buttonHint}>{t.endEn}</p>
            </div>
          </LiveKitRoom>
        ) : (
          <>
            <div className={styles.avatarCard}>
              {connecting ? (
                <div className={styles.connectingWrap}>
                  <div className={styles.spinner} />
                  <p className={styles.idleLine}>{t.connecting}</p>
                  <p className={styles.idleSub}>{t.connectingEn}</p>
                </div>
              ) : (
                <div className={styles.idleArt}>
                  <div className={styles.idleFace} aria-hidden="true">👩‍⚕️</div>
                  <p className={styles.idleLine}>{t.idleTitle}</p>
                  <p className={styles.idleSub}>{t.idleSub}</p>
                  <p className={styles.idleSub}>{t.idleSubEn}</p>
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
              <p className={styles.buttonHint}>{t.micNote} · {t.micNoteEn}</p>

              {error && <div className={styles.errorBox}>{error}</div>}

              <p className={styles.sectionLabel}>{t.chooseLang}</p>
              <div className={styles.langRow}>
                <button
                  className={styles.langPill}
                  data-active={lang === "kannada"}
                  onClick={() => setLang("kannada")}
                  disabled={connecting}
                >
                  ಕನ್ನಡ
                </button>
                <button
                  className={styles.langPill}
                  data-active={lang === "hindi"}
                  onClick={() => setLang("hindi")}
                  disabled={connecting}
                >
                  हिंदी
                </button>
              </div>
            </div>
          </>
        )}
      </main>
    </div>
  );
}
