import { AppState, Platform, type NativeEventSubscription } from 'react-native';

import { Haptic } from '@/lib/haptics';
import { clinicalFacts } from '@/data/clinical';
import { evaluate, gestationWeeks, questionsFor, questionText, RULES, SAKHI_REPLIES, SIGN_RULE, URGENT_SIGNS, visitFor, type AnswerRecord, type Question, type RuleCode, type Sign, type Visit } from '@/data/mockScreening';
import { ME_ASHA } from '@/data/care-network';
import { store, type MotherRecord, type TranscriptLine } from '@/data/store';
import { format, STRINGS, type Lang, type StringKey } from '@/i18n/strings';

import { answerBody, deleteAnswer, saveAnswer } from './answer-files';
import { voiceDb } from './db';
import { gateway, GatewayError } from './gateway';
import type { RemoteVoice } from './live';
import { short, vlog, vlogReset } from './log';
import { claimSession, releaseSession } from './outbox-worker';
import { commandIn, type Command } from './nlu/commands';
import { allFollowUps, followUpText } from './nlu/followup';
import { Intake, type Expect, type TurnOutcome } from './nlu/intake';
import { AFTER_STOP_MS, Mic, micPermission, micSupported, playbackMode, unlockAudio } from './mic';
import { Speaker, type VoiceSource } from './speaker';
import { decide, freshState, type Heard } from './turn-policy';
import { Utterance } from './utterance';
import { Endpointer, FRAME_SAMPLES, frameDb } from './vad';

// The Sakhi conversation engine (docs/VOICE_IN_APP_DESIGN.md). One object
// per check-in, OUTSIDE React: audio frames, timers and network never touch
// component state. The screen subscribes to an immutable snapshot.
//
//   prepare()  on screen mount: mic permission + gateway health, in the
//              background, so Start is instant
//   start()    greet -> question 1 -> ... -> 24, never waiting for a tap
//   per turn   speak (cached audio, mic OFF) -> mic ON -> listen (VAD) ->
//              end of turn -> mic OFF -> save WAV + queue STT ->
//              IMMEDIATELY next question. Half-duplex: on iOS a running
//              mic stream mutes all playback (see mic.ts)
//   STT queue  2 at a time, retries, breaker; results -> rules -> danger
//              flag (spoken at the next question boundary, never a stop)
//   finish     wait for owed transcripts (max 8 s) -> answers to the
//              mother's record -> the report (and its PDF) is built from them
//
// Every async step carries the turn TOKEN; exiting or backgrounding bumps
// it, so a late callback can never touch the current turn.
//
// ONE state manager for both ways Sakhi can talk:
//   live     the voice agent over WebRTC (voice/live.ts): this engine sends
//            ONE line + "listen" per turn and gets back the transcript; the
//            agent never decides what to ask (no LLM in the loop)
//   on the phone   cached TTS + the phone's mic + the gateway's STT
// If the live connection breaks, the engine carries on on the phone from
// the very same question: the check-in never stops.
//
// The stage is a finite-state machine with an explicit table of allowed
// moves (MOVES); go() enforces it, so an impossible state cannot happen
// silently.
//
// What to ask next is decided by SLOTS, not by a fixed list (voice/nlu):
// every transcript fills the slots of the question asked AND prefills later
// ones from what was said explicitly. A question already answered that way
// is skipped (never a danger question); a question whose answer did not
// fill it is asked once more ("Sorry, once more"), then recorded as unclear.
// Sakhi's fixed lines are recordings in the app (voice/prerecorded.ts): no
// language model and no TTS in the loop.
//
// The questionnaire is the one of HER visit (data/mockScreening.ts: first,
// second or third visit). A danger sign that needs urgent assessment
// (bleeding, leaking, fits, severe headache or blurred vision, sudden
// swelling, severe abdominal pain, breathlessness, reduced movements from 28
// weeks) STOPS the questionnaire at the next question boundary: Sakhi says
// so, the screen shows the sign and what to do, and nothing more is asked
// until the ASHA records what she did (referred / noted). Other danger signs
// are announced and flagged, as before.

const ECHO_GUARD_MS = 150;
const DEMO_LISTEN_MS = 2600;
const STT_CONCURRENCY = 2;
const STT_TRIES = 3;
const BACKOFF_MS = [400, 1200];
const FINISH_WAIT_MS = 8000;
const LEVEL_EMIT_MS = 100;
const NO_SPEECH_MS = 7000;
/** On the phone, how long a turn waits for its transcript before moving on without it. */
// How long the phone waits for a transcript before asking the next question
// (a late one is still applied to her record when it lands). A healthy one
// takes ~1-1.5 s through the tunnel; Gnani's hung requests fail at ~5.2 s,
// and 6000 turned every such failure into a 6 s silence between questions.
const STT_WAIT_MS = 3500;
/**
 * Still no transcript after STT_WAIT_MS while the speech service is up: wait
 * this much longer ("Writing it down...") rather than move on with her answer
 * not understood (its slots would be asked again later). Only offline, or
 * after this too, does the next question start; the answer is then applied
 * when its transcript lands (the outbox).
 */
const STT_EXTRA_WAIT_MS = 6000;
/** "Wait a minute" (she is asking the mother): listen this long for the answer. */
const WAIT_LISTEN_MS = 20_000;
const BACKGROUND_PAUSE_MS = 800;
/** Questions in a row with no voice at all: then Sakhi stops asking and waits for a tap. */
const AWAY_AFTER = 2;

export type Stage = 'idle' | 'preparing' | 'asking' | 'listening' | 'hearing' | 'finishing' | 'done' | 'exited';

/**
 * Every allowed move of the stage machine. A move not listed here is a bug:
 * it is logged, and refused once the check-in has ended (done / exited are
 * terminal: a late callback can never "restart" a finished check-in).
 */
const MOVES: Record<Stage, readonly Stage[]> = {
  idle: ['preparing', 'exited'],
  preparing: ['asking', 'idle', 'exited'],
  asking: ['listening', 'finishing', 'exited'],
  listening: ['hearing', 'asking', 'finishing', 'exited'],
  hearing: ['asking', 'listening', 'finishing', 'exited'],
  finishing: ['done', 'exited'],
  done: ['exited'],
  exited: [],
};
export type Line = { key: string; idx?: number; from: 'sakhi' | 'asha'; text: string; sub?: string; danger?: boolean; pending?: boolean; offline?: boolean };
export type Alert = { idx: number; qid: string; code: RuleCode; text: string; prev?: AnswerRecord };
/** A sign that stopped the questionnaire, waiting for the ASHA to act. */
export type Urgent = { idx: number; qid: string; code: RuleCode; signs: Sign[]; text: string };
export type Snapshot = {
  stage: Stage;
  idx: number;
  answered: number;
  lines: Line[];
  alerts: Alert[];
  level: number;
  mode: 'live' | 'demo';
  offline: boolean;
  pending: number;
  gapMs: number | null;
  micDenied: boolean;
  /** Who is speaking: Sakhi's own voice (Gnani) or the device's backup voice. */
  voice: VoiceSource | null;
  /** How the voice travels: the live agent (WebRTC), Sakhi Direct (WebSocket) or this phone. */
  transport: 'webrtc' | 'websocket' | 'phone';
  /** Live was wanted but is not available / broke: continuing on the phone. */
  liveLost: boolean;
  /** No voice for AWAY_AFTER questions in a row: waiting for the ASHA to tap Continue. */
  held: boolean;
  /** A danger sign stopped the questions: waiting for the ASHA (referred / noted). */
  urgent: Urgent | null;
  /** Her answer is in; its transcript is being made ("Writing it down..."). */
  processing: boolean;
  /**
   * Why Sakhi is asking again on this question (null on the first ask), so
   * the ASHA sees the reason at a glance: only what is missing (followUp),
   * not understood (reask), nothing heard (nudge), or the mother asked for
   * it ("say it again" / "one minute": command).
   */
  reason: AskReason | null;
};

export type AskReason = 'followUp' | 'reask' | 'nudge' | 'command';

/** 'skip': the ASHA tapped Next with nothing said (no re-ask). */
type ListenResult =
  | { kind: 'speech'; endedAt: number }
  | { kind: 'text'; text: string; endedAt: number }
  | { kind: 'silence' }
  | { kind: 'skip' }
  | { kind: 'demo'; value: string; danger: boolean; signs?: Sign[] };

/** What an answer turned out to be: understood (its outcome), a spoken command, or not known yet (null). */
type Understood = TurnOutcome | Command | null;

/** A remote voice the engine can drive (voice/live.ts LiveSession, voice/direct.ts DirectSession). */
type LiveVoice = RemoteVoice & { connect(lines: string[]): Promise<void>; readonly transport?: 'webrtc' | 'websocket' };

/** Base hangover: short answers end sooner, descriptions get patience. */
function hangoverFor(qid: string, dangerTrigger: boolean) {
  if (qid === 'age' || qid === 'prevPregnancies' || qid === 'deliveries' || qid === 'lmp') return 800; // numbers, dates
  if (qid === 'concerns' || qid === 'newProblems') return 1000; // she may think while she talks
  return dangerTrigger ? 850 : 550;
}

/** The most severe rule among these signs (EC-01 before HM-01 before the rest). */
const SEVERITY: RuleCode[] = ['EC-01', 'PE-04', 'PE-02', 'HM-01', 'PE-01', 'PE-03', 'FM-01', 'IN-01', 'AN-01'];
const worst = (codes: RuleCode[]) => [...codes].sort((a, b) => SEVERITY.indexOf(a) - SEVERITY.indexOf(b))[0];

export class SakhiEngine {
  readonly sessionId: string;
  private snap: Snapshot;
  private listeners = new Set<() => void>();
  // The mic level, ~10 times a second while she talks. NOT in the snapshot:
  // that would re-render the whole conversation screen each time; the
  // screen drives its ring from a shared value instead (UI thread).
  private levelListeners = new Set<(level: number) => void>();
  private token = 0;
  private prepared: Promise<'live' | 'demo'> | null = null;
  private mic: Mic | null = null;
  private vad = new Endpointer();
  private utt = new Utterance();
  private listening: { resolve: (r: ListenResult) => void } | null = null;
  /** Ends the current listen now (Next button), live or demo. */
  private forceEnd: (() => void) | null = null;
  private speaker: Speaker | null = null;
  private queue: number[] = [];
  private running = 0;
  private audio = new Map<number, string>();
  /** Per answer: settles when its WAV is fully on disk (upload only then). */
  private written = new Map<number, Promise<boolean>>();
  private notices: string[] = [];
  private retried = new Set<number>();
  private demoNext: 'normal' | 'danger' = 'normal';
  private lastLevel = 0;
  private gapFrom: number | null = null;
  private timers = new Set<ReturnType<typeof setTimeout>>();
  private appSub: NativeEventSubscription | null = null;
  private paused = false;
  /** After exit / finish: late network results are ignored. */
  private released = false;
  private seq = 0;
  /** The mic ran since the last switch to playback (iOS needs the switch back). */
  private needPlayback = true;
  /** The live voice agent, while connected. */
  private remote: RemoteVoice | null = null;
  /** Which visit's questionnaire, and its questions in asking order. */
  readonly visit: Visit;
  readonly questions: Question[];
  /** The slots of this check-in: what has been understood so far. */
  private intake: Intake;
  /** Urgent signs waiting to stop the questionnaire, and those the ASHA already acted on. */
  private urgentQueue: Urgent[] = [];
  private acted = new Set<Sign>();
  /** Phone mode: a turn waiting for its transcript (to decide: next or ask again). */
  private heard = new Map<number, (o: Understood) => void>();
  /** The slot the last follow-up of a question asked for (its bare reply goes there). */
  private expecting = new Map<number, Expect>();
  /** Questions whose danger was already announced (a re-ask never repeats it). */
  private noticed = new Set<number>();
  private closingSaid = false;
  /** Questions in a row that ended with no voice at all (see AWAY_AFTER). */
  private silentRun = 0;

  constructor(
    private readonly mother: MotherRecord,
    /** The language Sakhi speaks: the mother's. */
    private readonly lang: Lang,
    /**
     * warm: false when another voice brings the audio (Sakhi Direct): no HTTP
     * warm-up competing with it. visit: the questionnaire (default: hers, visitFor).
     */
    private readonly opts: { warm?: boolean; visit?: Visit } = {},
  ) {
    this.sessionId = `${mother.id}-${Date.now().toString(36)}`;
    this.visit = opts.visit ?? visitFor(mother);
    this.questions = questionsFor(this.visit);
    this.intake = new Intake(this.visit, gestationWeeks(mother));
    this.snap = { stage: 'idle', idx: 0, answered: 0, lines: [], alerts: [], level: 0, mode: 'demo', offline: false, pending: 0, gapMs: null, micDenied: false, voice: null, transport: 'phone', liveLost: false, held: false, urgent: null, processing: false, reason: null };
  }

  // ---- React bridge --------------------------------------------------------
  /** The mic level (0..1) while she talks; 0 when the turn ends. Returns unsubscribe. */
  onAudioLevel = (fn: (level: number) => void) => {
    this.levelListeners.add(fn);
    return () => void this.levelListeners.delete(fn);
  };
  private emitLevel(level: number) {
    this.levelListeners.forEach((fn) => fn(level));
  }

  subscribe = (l: () => void) => {
    this.listeners.add(l);
    return () => void this.listeners.delete(l);
  };
  getSnapshot = () => this.snap;
  private set(patch: Partial<Snapshot>) {
    this.snap = { ...this.snap, ...patch };
    this.listeners.forEach((l) => l());
  }
  /** Move the stage machine (see MOVES). */
  private go(next: Stage, patch: Partial<Snapshot> = {}) {
    const cur = this.snap.stage;
    if (next !== cur && !MOVES[cur].includes(next)) {
      vlog('err', `stage ${cur} -> ${next} is not an allowed move`);
      if (cur === 'done' || cur === 'exited') return;
    }
    this.set({ ...patch, stage: next });
    // Her turn is over (or not started): the ring rests.
    if (next !== 'hearing' && next !== cur) this.emitLevel(0);
  }

  private say(key: StringKey, values?: Record<string, string | number>) {
    const s = STRINGS[this.lang][key];
    return values ? format(s, values) : s;
  }
  private line(l: Omit<Line, 'key'>) {
    const key = `l${this.seq++}`;
    this.set({ lines: [...this.snap.lines, { ...l, key }] });
    return key;
  }
  /** Update the LATEST answer line of question `idx` (a re-asked question has two). */
  private patchLine(idx: number, patch: Partial<Line>) {
    let last = -1;
    this.snap.lines.forEach((l, k) => l.idx === idx && l.from === 'asha' && (last = k));
    if (last < 0) return;
    this.set({ lines: this.snap.lines.map((l, k) => (k === last ? { ...l, ...patch } : l)) });
  }
  private later(ms: number, fn: () => void) {
    const t = setTimeout(() => {
      this.timers.delete(t);
      fn();
    }, ms);
    this.timers.add(t);
  }
  private wait(ms: number) {
    return new Promise<void>((r) => this.later(ms, r));
  }

  // ---- lifecycle -----------------------------------------------------------

  /** Mic permission + gateway health in the background (call on mount). */
  prepare() {
    // Released before it ever started (an effect cleanup then remount): come back to life.
    if (this.released && this.snap.stage === 'idle') {
      this.released = false;
      this.prepared = null;
    }
    this.prepared ??= (async () => {
      vlogReset();
      vlog('run', `prepare: ${this.mother.name}, language ${this.lang}`);
      const [granted, health] = await Promise.all([micSupported ? micPermission().catch(() => false) : Promise.resolve(false), gateway.health()]);
      if (micSupported && !granted) this.set({ micDenied: true });
      vlog('mic', micSupported ? `permission ${granted ? 'granted' : 'DENIED'}` : 'not supported here (web): demo mode');
      const serverVoice = health.tts;
      // A microphone means real answers, always. Without the speech service
      // the answers are still recorded on the phone and transcribed when it
      // is back (the outbox); demo answers are ONLY for a device with no mic.
      const mode: 'live' | 'demo' = granted ? 'live' : 'demo';
      if (granted && !health.stt) vlog('err', 'speech service not reachable: answers are recorded and transcribed when it is back');
      if (this.released) return mode; // released meanwhile: no speaker, no downloads
      vlog('run', `mode ${mode.toUpperCase()}; Sakhi's voice: ${serverVoice ? 'server (Gnani)' : 'the phone'}`);
      this.speaker?.release();
      this.speaker = new Speaker(this.lang, serverVoice, (v) => this.snap.voice !== v && this.set({ voice: v }));
      await playbackMode().catch((e) => vlog('err', `audio mode: ${String(e)}`));
      this.needPlayback = false;
      this.speaker.checkDeviceVoice();
      // Recorded lines onto the phone first (every transport: Sakhi Direct
      // plays them here too), then downloads for anything not recorded.
      this.speaker.preloadRecorded(this.allLines()).catch(() => {});
      if (serverVoice && this.opts.warm !== false) this.warmAll();
      return mode;
    })();
    return this.prepared;
  }

  /**
   * Latency agent: while the ASHA reads the intro, every line Sakhi will say
   * (greeting, 24 questions, prompts) is downloaded to the phone in speaking
   * order, one at a time (the provider allows one request at a time; the
   * line being said NOW always jumps the queue). Files stay on the phone, so
   * from the second check-in no line waits on the network at all.
   */
  private warmAll() {
    this.speaker?.warm(this.allLines());
  }

  /** Everything Sakhi can say in this check-in, in speaking order. */
  allLines() {
    return [
      this.say('vGreetingVoice'),
      ...this.questions.map((q) => questionText(q, this.lang)),
      this.say('vReprompt'),
      this.say('vReask'),
      this.say('vWaiting'),
      this.say('vAway'),
      ...allFollowUps(this.lang),
      SAKHI_REPLIES[this.lang].ok,
      SAKHI_REPLIES[this.lang].danger,
      SAKHI_REPLIES[this.lang].urgent,
      SAKHI_REPLIES[this.lang].done,
    ];
  }

  /**
   * Sakhi says one line. The mic is OFF while she speaks, and the session is
   * switched back to playback first (a mic stream on iOS mutes everything).
   */
  private async speakLine(text: string, onStart?: () => void) {
    const my = this.token;
    if (this.remote) {
      const ok = await this.remote.say(text, onStart);
      if (ok || my !== this.token) return;
      // Not said by the agent (it went away): say it here instead.
    }
    if (this.mic?.on) this.mic.stop();
    if (this.needPlayback) {
      const since = Date.now() - (this.mic?.stoppedAt ?? 0);
      if (since < AFTER_STOP_MS) await this.wait(AFTER_STOP_MS - since);
      await playbackMode().catch((e) => vlog('err', `audio mode: ${String(e)}`));
      this.needPlayback = false;
    }
    // A Repeat / exit while switching: this line is no longer wanted.
    if (my !== this.token) return;
    await this.speaker?.speak(text, onStart);
  }

  /**
   * The intro's microphone test: the mic on for `ms`, its level to
   * onAudioLevel (the meter), then off. `ok` when a voice-loud frame was
   * heard (above -50 dBFS). Only before the check-in starts.
   */
  async testMic(ms = 3000): Promise<{ result: 'ok' | 'low' | 'unavailable' | 'denied'; peakDb: number }> {
    if (this.snap.stage !== 'idle') return { result: 'unavailable', peakDb: -100 };
    if (!micSupported) return { result: 'unavailable', peakDb: -100 };
    const granted = await micPermission().catch(() => false);
    if (!granted) {
      this.set({ micDenied: true });
      return { result: 'denied', peakDb: -100 };
    }
    let peak = -100;
    let level = 0;
    let last = 0;
    const mic = new Mic((pcm, off) => {
      const db = frameDb(pcm, off, FRAME_SAMPLES);
      if (db > peak) peak = db;
      level += (Math.max(0, Math.min(1, (db + 70) / 40)) - level) * 0.35;
      const now = Date.now();
      if (now - last >= 50) {
        last = now;
        this.emitLevel(level);
      }
    });
    try {
      await mic.start();
      await new Promise((r) => setTimeout(r, ms));
    } catch (e) {
      vlog('err', `mic test failed: ${String((e as Error)?.message ?? e)}`);
      return { result: 'unavailable', peakDb: peak };
    } finally {
      mic.dispose();
      this.emitLevel(0);
      this.needPlayback = true; // iOS: the next line must switch the audio back to playback
    }
    vlog('mic', `test: peak ${peak.toFixed(0)} dBFS`);
    return { result: peak > -50 ? 'ok' : 'low', peakDb: peak };
  }

  /** Start the check-in; with `live`, over the voice agent (falls back to the phone). */
  async start(live?: LiveVoice) {
    // First, inside the tap: the web only allows sound after a user gesture.
    unlockAudio();
    if (this.snap.stage !== 'idle') return;
    this.go('preparing');
    const mode = await this.prepare();
    this.speaker ??= new Speaker(this.lang, false, (v) => this.snap.voice !== v && this.set({ voice: v }));
    this.set({ mode });
    if (live) {
      // The live call (WebView capture) takes over the audio session: any
      // line said on the phone afterwards must switch it back first.
      this.needPlayback = true;
      this.set({ transport: live.transport ?? 'webrtc' });
      try {
        await live.connect(this.allLines());
        if (this.released) return;
        this.remote = live;
        live.onLost((why) => this.loseRemote(why));
        live.onLevel((level) => {
          if (this.snap.stage === 'listening' || this.snap.stage === 'hearing') this.emitLevel(Math.min(1, level * 2.5));
        });
        this.set({ voice: 'sakhi' });
      } catch (e) {
        if (this.released) return;
        vlog('err', `live Sakhi unavailable (${String((e as Error)?.message ?? e)}): asking on the phone`);
        this.set({ transport: 'phone', liveLost: true });
      }
    }
    store.startCheckIn(this.mother.id, this.visit);
    claimSession(this.sessionId); // its answers are sent by this engine, not the outbox worker
    voiceDb.createSession({ id: this.sessionId, mother_id: this.mother.id, lang: this.lang, mode }).catch(() => {});
    vlog('run', `start (${this.remote ? `LIVE over ${this.snap.transport === 'websocket' ? 'WebSocket' : 'WebRTC'}` : mode})`);
    // The mic turns on only when Sakhi listens (half-duplex, see mic.ts).
    if (mode === 'live' && !this.remote) this.mic = new Mic((pcm, off) => this.onFrame(pcm, off));
    // Pause only when the app really leaves the screen ('background'), after
    // a short debounce. NOT on 'inactive' (iOS: a notification banner, Control
    // Center) and not on the web (a hidden tab keeps playing; the embedded
    // browser flickers visibility).
    if (Platform.OS !== 'web') {
      let bg: ReturnType<typeof setTimeout> | null = null;
      this.appSub = AppState.addEventListener('change', (s) => {
        if (s === 'background') {
          bg ??= setTimeout(() => {
            bg = null;
            this.pause();
          }, BACKGROUND_PAUSE_MS);
        } else if (s === 'active') {
          if (bg) clearTimeout(bg);
          bg = null;
          this.resume();
        }
      });
    }
    // Warm the cache: greeting + the first two questions (a remote voice
    // brings its own audio: no second download path competing with it).
    const greeting = this.say('vGreetingVoice');
    if (!this.remote) {
      this.speaker.prefetch(greeting);
      for (let i = 0; i < 2; i++) this.speaker.prefetch(questionText(this.questions[i], this.lang));
    }
    this.line({ from: 'sakhi', text: greeting });
    const my = ++this.token;
    this.go('asking');
    await this.speakLine(greeting);
    if (my === this.token) this.run(0, my);
  }

  /** Leave without saving: the record stays as it was. */
  exit() {
    // DPDP: a cancelled check-in keeps none of her voice. Answers still
    // waiting for a transcript are deleted now (a finished check-in keeps
    // them in the outbox until transcribed, then deletes them).
    for (const uri of this.audio.values()) deleteAnswer(uri);
    this.audio.clear();
    this.written.clear();
    this.halt();
    store.discardCheckIn(this.mother.id);
    voiceDb.endSession(this.sessionId, 'abandoned').catch(() => {});
    this.go('exited');
  }

  /** Release audio and timers (screen unmount). */
  release() {
    this.halt();
  }

  private halt() {
    this.released = true;
    releaseSession(this.sessionId); // anything still owed goes to the outbox worker
    this.token++;
    this.listening = null;
    if (this.remote) {
      this.remote.stop();
      this.remote.onLost(null);
      this.remote.onLevel(null);
      this.remote = null;
    }
    this.timers.forEach(clearTimeout);
    this.timers.clear();
    this.speaker?.release();
    this.mic?.dispose();
    this.mic = null;
    this.appSub?.remove();
    this.appSub = null;
  }

  private pause() {
    // Held: nothing runs, and Continue (not coming back to the app) restarts it.
    if (this.paused || this.snap.held || this.snap.urgent || !['asking', 'listening', 'hearing'].includes(this.snap.stage)) return;
    this.paused = true;
    this.token++;
    this.listening = null;
    this.remote?.stop();
    this.speaker?.stop();
    this.mic?.stop();
    vlog('run', 'paused (app in background)');
  }

  private resume() {
    if (!this.paused) return;
    this.paused = false;
    // A phone call (Android Telecom) or another app may have taken audio
    // focus while she was away: the old recorder can come back silent. A
    // fresh one re-binds the microphone. Only when this check-in records on
    // the phone (demo and remote voice have no Mic).
    if (this.mic) {
      this.mic.dispose();
      this.mic = new Mic((pcm, off) => this.onFrame(pcm, off));
      vlog('mic', 'recorder re-created after the interruption');
    }
    this.run(this.snap.idx, ++this.token); // re-ask the current question
  }

  // ---- the conversation loop -------------------------------------------------

  private async run(from: number, my: number) {
    const total = this.questions.length;
    for (let i = from; i < total; i++) {
      if (my !== this.token) return;
      // An urgent sign stops everything here, before another question.
      if (this.urgentQueue.length) return this.holdUrgent(i, my);
      const q = this.questions[i];
      // Answered by an earlier answer (or not applicable): record it, do not ask.
      if (this.intake.skippable(q.id)) {
        this.fromEarlier(i);
        continue;
      }
      const text = questionText(q, this.lang);
      // A danger answer from earlier is acknowledged here, never mid-answer.
      while (this.notices.length) {
        const n = this.notices.shift() as string;
        this.line({ from: 'sakhi', text: n, danger: true });
        await this.speakLine(n);
        if (my !== this.token) return;
      }
      this.set({ idx: i });
      this.line({ from: 'sakhi', text, sub: this.lang === 'en' ? undefined : q.en, idx: i });
      // The closing line is said, not asked.
      if (this.intake.isSayOnly(q.id)) {
        vlog('run', `Q${i + 1}/${total} ${q.id} (closing, say only)`);
        this.go('asking');
        await this.speakLine(text, () => this.markGap());
        if (my !== this.token) return;
        this.closingSaid = true;
        store.recordAnswer(this.mother.id, q.id, this.intake.record(q.id));
        this.set({ answered: Math.max(this.snap.answered, i + 1) });
        continue;
      }
      if (i + 1 < total && !this.remote) this.speaker?.prefetch(questionText(this.questions[i + 1], this.lang));
      vlog('run', `Q${i + 1}/${total} ${q.id} "${short(text)}"${this.remote ? ' (live)' : ''}`);
      if (!(await this.converse(i, my, text))) return;
    }
    if (my !== this.token) return;
    if (this.urgentQueue.length) return this.holdUrgent(total, my);
    await this.finishFlow(my);
  }

  /** An urgent sign: say so, stop asking, wait for the ASHA (see acknowledgeUrgent). */
  private async holdUrgent(i: number, my: number) {
    const u = this.urgentQueue.shift() as Urgent;
    // Any other urgent sign waiting is covered by this stop.
    this.urgentQueue = [];
    vlog('run', `URGENT ${u.code} (${u.signs.join(', ')}) from ${u.qid}: questions paused for the ASHA`);
    Haptic.warning();
    const line = SAKHI_REPLIES[this.lang].urgent;
    this.notices = this.notices.filter((n) => n !== SAKHI_REPLIES[this.lang].danger);
    this.line({ from: 'sakhi', text: line, danger: true });
    this.go('asking');
    await this.speakLine(line);
    if (my !== this.token) return;
    this.token++;
    this.listening = null;
    this.forceEnd = null;
    this.mic?.stop();
    this.set({ urgent: u, idx: i, level: 0 });
  }

  /**
   * The ASHA acted on the urgent sign: `referred` (she is being checked) or
   * `noted` (to be checked; continue). Recorded with her record, then the
   * questions go on from where they stopped.
   */
  acknowledgeUrgent(status: 'referred' | 'noted') {
    const u = this.snap.urgent;
    if (!u) return;
    u.signs.forEach((sg) => this.acted.add(sg));
    store.recordReferral(this.mother.id, { code: u.code, signs: u.signs, text: u.text, status, by: ME_ASHA });
    vlog('run', `urgent ${u.code}: ${status}; continuing from Q${this.snap.idx + 1}`);
    this.set({ urgent: null });
    this.silentRun = 0;
    this.run(this.snap.idx, ++this.token);
  }

  /**
   * ONE question, as many turns as it needs and never more than the policy
   * allows (voice/turn-policy.ts): the full question, then only what is
   * missing ("and her age?"), a nudge after silence, one re-ask when nothing
   * was understood, "repeat" / "wait" / "skip" when she says so. Returns
   * false when the run was cancelled (Repeat button, pause, exit).
   */
  private async converse(i: number, my: number, text: string): Promise<boolean> {
    const q = this.questions[i];
    let state = freshState();
    let prompt = text;
    let onStart: (() => void) | undefined = () => this.markGap();
    let noSpeechMs: number | undefined;
    this.expecting.delete(i);
    this.set({ reason: null }); // a new question: asked for the first time
    for (;;) {
      const res = await this.ask(i, my, prompt, onStart, noSpeechMs);
      if (my !== this.token) return false;
      const heard = await this.hear(i, res, my);
      if (my !== this.token) return false;
      if (heard !== 'silence') this.silentRun = 0;
      const step = decide(state, heard);
      state = step.state;
      vlog('run', `Q${i + 1} ${q.id} turn ${state.turns}: heard ${heard} -> ${step.action}`);
      onStart = () => this.markGap();
      noSpeechMs = undefined;
      this.expecting.delete(i);
      switch (step.action) {
        case 'next':
          // Understood: a short "noted" (and a tap on her hand) so the ASHA
          // knows it was recorded without looking. Not when a danger line or
          // an urgent stop comes next (that speaks for itself), nor before
          // the closing line.
          if (heard === 'complete') {
            Haptic.light();
            const last = i + 1 >= this.questions.length || this.intake.isSayOnly(this.questions[i + 1].id);
            if (!last && !this.notices.length && !this.urgentQueue.length) {
              const ok = SAKHI_REPLIES[this.lang].ok;
              await this.speakLine(ok);
              if (my !== this.token) return false;
            }
          }
          return true;
        case 'skip':
          // Recorded as skipped for the doctor to see; not a danger SIGN, so no alarm.
          if (!this.intake.skippable(q.id)) this.record(i, { value: 'Skipped (check)', danger: false });
          return true;
        case 'giveUp': {
          // Nobody has spoken for several questions: she may have stepped
          // away. Stop asking into the void; this question is asked again on Continue.
          if (heard === 'silence' && ++this.silentRun >= AWAY_AFTER) {
            await this.hold(i, my);
            return false;
          }
          if (heard === 'silence') this.handle(i, { kind: 'silence' });
          const rec = this.intake.giveUp(q.id, this.lastHeard.get(i) ?? '');
          this.record(i, rec);
          vlog('run', `Q${i + 1} ${q.id} recorded as it stands: "${short(rec.value, 40)}"`);
          return true;
        }
        case 'followUp': {
          // Partly answered: ask ONLY what is missing, the rest is kept.
          const f = this.intake.missing(q.id);
          if (!f) return true;
          prompt = followUpText(this.lang, f);
          if ('key' in f) this.expecting.set(i, f.expect);
          this.set({ reason: 'followUp' });
          this.line({ from: 'sakhi', text: prompt, idx: i });
          break;
        }
        case 'reask': {
          this.set({ reason: 'reask' });
          const sorry = this.say('vReask');
          this.line({ from: 'sakhi', text: sorry });
          await this.speakLine(sorry);
          if (my !== this.token) return false;
          prompt = text;
          this.line({ from: 'sakhi', text, sub: this.lang === 'en' ? undefined : q.en, idx: i });
          break;
        }
        case 'nudge': {
          // "I did not hear an answer, please say it once more", then the
          // question itself: after a silence she may not remember it.
          this.set({ reason: 'nudge' });
          const nudge = this.say('vReprompt');
          this.line({ from: 'sakhi', text: nudge });
          await this.speakLine(nudge);
          if (my !== this.token) return false;
          prompt = text;
          this.line({ from: 'sakhi', text, sub: this.lang === 'en' ? undefined : q.en, idx: i });
          break;
        }
        case 'repeat':
          // She asked for it: the same question, no "sorry", no re-ask used up.
          this.set({ reason: 'command' });
          prompt = text;
          this.line({ from: 'sakhi', text, sub: this.lang === 'en' ? undefined : q.en, idx: i });
          break;
        case 'relisten':
          // "One minute": she is asking the mother. Say so, then wait longer.
          this.set({ reason: 'command' });
          prompt = this.say('vWaiting');
          noSpeechMs = WAIT_LISTEN_MS;
          this.line({ from: 'sakhi', text: prompt });
          break;
      }
    }
  }

  /** What one listen gave, for the policy. Applies the answer to the intake on the way. */
  private async hear(i: number, res: ListenResult, my: number): Promise<Heard> {
    if (res.kind === 'silence') return 'silence';
    if (res.kind === 'skip') {
      this.handle(i, res);
      return 'skip';
    }
    if (res.kind === 'demo') {
      this.handle(i, res);
      return 'complete';
    }
    let got: Understood;
    if (res.kind === 'text') {
      got = this.handle(i, res);
    } else {
      // Phone mode: the answer is being transcribed behind; wait for it
      // briefly to decide. Offline / slow: move on (it is applied later).
      const pending = new Promise<Understood>((resolve) => this.heard.set(i, resolve));
      this.handle(i, res);
      this.emitLevel(0);
      this.set({ processing: true });
      got = await Promise.race([pending, this.wait(STT_WAIT_MS).then(() => null)]);
      if (got === null && !gateway.offline && my === this.token) {
        vlog('stt', `Q${i + 1}: transcript not back after ${STT_WAIT_MS} ms; waiting up to ${STT_EXTRA_WAIT_MS} ms more`);
        got = await Promise.race([pending, this.wait(STT_EXTRA_WAIT_MS).then(() => null)]);
      }
      this.heard.delete(i);
      this.set({ processing: false });
      if (my !== this.token) return 'pending';
    }
    if (got === null) return 'pending';
    if (typeof got === 'string') return got; // a command: repeat / wait / skip
    if (got.prefilled.length) vlog('run', `prefilled from this answer: ${got.prefilled.join(', ')}`);
    if (got.complete) return 'complete';
    return this.intake.missing(this.questions[i].id) ? 'partial' : 'unclear';
  }

  /** A question answered earlier: its record, a quiet line, no question asked. */
  private fromEarlier(i: number) {
    const q = this.questions[i];
    const rec = this.intake.record(q.id);
    vlog('run', `Q${i + 1} ${q.id} skipped: already answered ("${short(rec.value, 40)}")`);
    store.recordAnswer(this.mother.id, q.id, rec);
    this.line({ from: 'asha', idx: i, text: this.say('vFromEarlier', { value: rec.value }), sub: questionText(q, this.lang) });
    this.set({ answered: Math.max(this.snap.answered, i + 1) });
    voiceDb.upsertTurn({ session_id: this.sessionId, idx: i, qid: q.id, question: questionText(q, this.lang), audio_uri: null, transcript: rec.value, stt_status: 'prefilled', ended_speech_at: null }).catch(() => {});
  }

  /**
   * One turn: say `text`, then hear ONE answer. Live: a single command to the
   * voice agent (it speaks, listens, transcribes). On the phone: speak, then
   * the phone's mic + VAD. A broken live connection continues on the phone
   * with this very question.
   */
  private async ask(i: number, my: number, text: string, onStart?: () => void, noSpeechMs = NO_SPEECH_MS): Promise<ListenResult> {
    const q = this.questions[i];
    this.go('asking');
    const remote = this.remote;
    if (remote) {
      const r = await remote.ask(
        text,
        { hangoverMs: hangoverFor(q.id, q.dangerTrigger), noSpeechMs },
        {
          onStart,
          onListening: () => my === this.token && this.go('listening', { level: 0 }),
          onSpeech: () => my === this.token && this.go('hearing'),
        },
      );
      if (r.kind === 'text') return r.text.trim() ? { kind: 'text', text: r.text.trim(), endedAt: Date.now() } : { kind: 'silence' };
      if (r.kind === 'silence' || r.kind === 'skip') return r;
      if (my !== this.token) return { kind: 'skip' }; // cancelled (Repeat / pause / exit): the caller stops
      this.loseRemote(r.why);
      // She already asked it: the phone just listens (no repeat of the question).
      if (r.said) return this.listen(i, my);
    }
    await this.speakLine(text, onStart);
    if (my !== this.token) return { kind: 'skip' };
    return this.listen(i, my);
  }

  /** The live connection is gone: continue on the phone, same question. */
  private loseRemote(why: string) {
    if (!this.remote) return;
    vlog('err', `live Sakhi lost (${why}): continuing on the phone from Q${this.snap.idx + 1}`);
    this.remote.onLost(null);
    this.remote.onLevel(null);
    this.remote = null;
    this.needPlayback = true;
    if (this.snap.mode === 'live' && !this.mic) this.mic = new Mic((pcm, off) => this.onFrame(pcm, off));
    this.set({ transport: 'phone', liveLost: true });
  }

  private listen(i: number, my: number): Promise<ListenResult> {
    const q = this.questions[i];
    const mic = this.mic;
    if (this.snap.mode === 'demo' || !mic) {
      return new Promise((resolve) => {
        this.later(ECHO_GUARD_MS, () => {
          if (my !== this.token) return;
          this.go('listening', { level: 0 });
          this.later(900, () => my === this.token && this.go('hearing'));
          let ended = false;
          const end = () => {
            if (ended || my !== this.token) return;
            ended = true;
            this.forceEnd = null;
            resolve(this.demoAnswer(i));
          };
          this.forceEnd = end;
          this.later(DEMO_LISTEN_MS, end);
        });
      });
    }
    return new Promise((resolve) => {
      this.vad.reset({ hangoverMs: hangoverFor(q.id, q.dangerTrigger) });
      this.utt.reset();
      const began = Date.now();
      this.listening = {
        resolve: (r) => {
          this.listening = null;
          this.forceEnd = null;
          mic.stop();
          const v = this.vad;
          vlog(
            'vad',
            r.kind === 'skip'
              ? 'ASHA tapped Next (no answer)'
              : r.kind === 'speech'
              ? `end: speech ${(v.speechDurationMs / 1000).toFixed(1)} s, peak ${v.peakDb.toFixed(0)} dB, floor ${v.floorDb.toFixed(0)} dB`
              : `no speech in ${((Date.now() - began) / 1000).toFixed(1)} s (peak ${v.peakDb.toFixed(0)} dB, floor ${v.floorDb.toFixed(0)} dB${v.peakDb <= -90 ? ': THE MIC GAVE ONLY SILENCE' : ''})`,
          );
          resolve(r);
        },
      };
      this.forceEnd = () => {
        const l = this.listening;
        if (!l) return;
        if (this.vad.isSpeaking) {
          this.utt.end();
          l.resolve({ kind: 'speech', endedAt: Date.now() });
        } else {
          l.resolve({ kind: 'skip' });
        }
      };
      this.needPlayback = true;
      this.go('listening', { level: 0 });
      mic.start().catch((e) => {
        vlog('err', `mic failed to start: ${String((e as Error)?.message ?? e)}; answers from now on are demo answers`);
        this.listening = null;
        this.mic = null;
        this.set({ mode: 'demo' });
        if (my === this.token) resolve(this.demoAnswer(i));
      });
    });
  }

  /** Mic frames (20 ms). Only used while listening; O(1), no allocation. */
  private onFrame(pcm: Int16Array, off: number) {
    const l = this.listening;
    if (!l) return;
    const ev = this.vad.push(frameDb(pcm, off, FRAME_SAMPLES));
    if (ev === 'start') {
      this.utt.begin();
      this.go('hearing');
      vlog('vad', `speech start (peak ${this.vad.peakDb.toFixed(0)} dB, floor ${this.vad.floorDb.toFixed(0)} dB)`);
    }
    this.utt.frame(pcm, off);
    const now = Date.now();
    if (now - this.lastLevel >= LEVEL_EMIT_MS) {
      this.lastLevel = now;
      this.emitLevel(this.vad.level);
    }
    if (ev === 'end') {
      this.utt.end();
      l.resolve({ kind: 'speech', endedAt: now });
    } else if (ev === 'noSpeech') {
      l.resolve({ kind: 'silence' });
    }
  }

  private demoAnswer(i: number): ListenResult {
    const q = this.questions[i];
    const danger = this.demoNext === 'danger' && q.dangerTrigger;
    if (danger) this.demoNext = 'normal';
    const known = q.id === 'name' ? this.mother.name : undefined;
    return { kind: 'demo', value: danger ? (q.sampleDanger ?? q.sampleNormal) : (known ?? q.sampleNormal), danger, signs: danger ? q.sampleSigns : undefined };
  }

  /** End of a turn: store it and move on at once (STT runs behind). */
  private handle(i: number, res: ListenResult): Understood {
    const q = this.questions[i];
    const question = questionText(q, this.lang);
    this.set({ answered: Math.max(this.snap.answered, i + 1) });
    if (res.kind === 'silence' || res.kind === 'skip') {
      this.line({ from: 'asha', idx: i, text: this.say('vNoAnswer'), offline: true });
      voiceDb.upsertTurn({ session_id: this.sessionId, idx: i, qid: q.id, question, audio_uri: null, stt_status: 'skipped', ended_speech_at: null }).catch(() => {});
      return null;
    }
    if (res.kind === 'text') {
      // Live: the agent already transcribed it. Straight to the rules.
      this.gapFrom = res.endedAt;
      Haptic.light();
      this.line({ from: 'asha', idx: i, text: res.text });
      voiceDb.upsertTurn({ session_id: this.sessionId, idx: i, qid: q.id, question, audio_uri: null, transcript: res.text, stt_status: 'done', ended_speech_at: res.endedAt }).catch(() => {});
      return this.apply(i, res.text, '');
    }
    if (res.kind === 'demo') {
      this.line({ from: 'asha', idx: i, text: res.value });
      voiceDb.upsertTurn({ session_id: this.sessionId, idx: i, qid: q.id, question, audio_uri: null, transcript: res.value, transcript_en: res.value, danger: res.danger ? 1 : 0, stt_status: 'demo', ended_speech_at: Date.now() }).catch(() => {});
      this.apply(i, res.value, res.value, res.danger, res.signs);
      Haptic.light();
      this.gapFrom = Date.now();
      return null;
    }
    // Live: the WAV goes to disk (and the outbox), the next question starts now.
    const { uri, ready } = saveAnswer(this.sessionId, i, this.utt.toWav());
    this.audio.set(i, uri);
    this.written.set(i, ready);
    this.gapFrom = res.endedAt;
    Haptic.light();
    this.line({ from: 'asha', idx: i, text: this.say('vUnderstanding'), pending: true });
    voiceDb.upsertTurn({ session_id: this.sessionId, idx: i, qid: q.id, question, audio_uri: uri, stt_status: 'pending', ended_speech_at: res.endedAt }).catch(() => {});
    this.enqueue(i);
    return null;
  }

  /** Sound of the next question started: measure the turn gap. */
  private markGap() {
    if (this.gapFrom === null) return;
    const ms = Date.now() - this.gapFrom;
    this.gapFrom = null;
    this.set({ gapMs: ms });
    voiceDb.logEvent(this.sessionId, 'turn_gap', ms).catch(() => {});
  }

  // ---- STT queue ---------------------------------------------------------------

  private enqueue(i: number) {
    this.queue.push(i);
    this.set({ pending: this.snap.pending + 1 });
    this.pump();
  }

  private pump() {
    while (this.running < STT_CONCURRENCY && this.queue.length) {
      const i = this.queue.shift() as number;
      this.running++;
      this.transcribe(i).finally(() => {
        this.running--;
        this.set({ pending: Math.max(0, this.snap.pending - 1), offline: gateway.offline });
        this.pump();
      });
    }
  }

  private async transcribe(i: number) {
    const uri = this.audio.get(i);
    // Never upload a WAV that is still being written.
    const written = await (this.written.get(i) ?? Promise.resolve(true));
    const body = uri && written ? answerBody(uri) : null;
    if (!uri || !body) {
      if (uri && !written) vlog('err', `Q${i + 1}: answer audio could not be saved`);
      this.heard.get(i)?.(null);
      return;
    }
    const started = Date.now();
    for (let attempt = 0; attempt < STT_TRIES; attempt++) {
      try {
        const r = await gateway.stt(body, this.lang);
        vlog('stt', `ok ${Date.now() - started} ms (server ${r.ms} ms) Q${i + 1} "${short(r.text, 60)}"`);
        const outcome = this.apply(i, r.text, r.en);
        this.heard.get(i)?.(outcome);
        voiceDb.updateTurn(this.sessionId, i, { transcript: r.text, transcript_en: r.en, stt_status: 'done', stt_ms: Date.now() - started, audio_uri: null }).catch(() => {});
        voiceDb.logEvent(this.sessionId, 'stt', Date.now() - started).catch(() => {});
        deleteAnswer(uri);
        this.audio.delete(i);
        this.written.delete(i);
        return;
      } catch (e) {
        const retry = e instanceof GatewayError && e.retryable && !gateway.offline;
        vlog('err', `stt Q${i + 1} attempt ${attempt + 1}: ${String((e as Error)?.message ?? e)}${retry ? ', retrying' : ''}`);
        if (!retry || attempt === STT_TRIES - 1) break;
        await this.wait(BACKOFF_MS[attempt] ?? 1200);
      }
    }
    this.heard.get(i)?.(null);
    if (this.released) return;
    // Offline (or the server refused): keep the audio, transcribe later.
    vlog('stt', `Q${i + 1} kept for later (offline)`);
    this.patchLine(i, { text: this.say('vSavedOffline'), pending: false, offline: true });
    voiceDb.updateTurn(this.sessionId, i, { stt_status: 'offline' }).catch(() => {});
  }

  /** The last transcript of each question (for the log and an unclear record). */
  private lastHeard = new Map<number, string>();

  /**
   * An answer's transcript -> the slots -> the record (and a flag if danger).
   * Demo answers pass their own danger flag; spoken ones are understood by
   * voice/nlu (which also prefills later questions).
   */
  private apply(i: number, native: string, en: string, demoDanger?: boolean, demoSigns?: Sign[]): Understood {
    if (this.released) return null;
    const q = this.questions[i];
    const said = (native || en).trim();
    // Said TO Sakhi ("say it again", "one minute", "skip"): not an answer,
    // so it never touches her record.
    const cmd = demoDanger === undefined ? commandIn(said) : null;
    if (cmd) {
      this.patchLine(i, { text: said, pending: false, offline: false });
      return cmd;
    }
    this.lastHeard.set(i, said);
    const outcome = this.intake.answer(q.id, said, this.expecting.get(i));
    const demo = demoDanger !== undefined;
    const rec: AnswerRecord = demo ? { value: said || this.say('vNotClear'), danger: demoDanger, ...(demoSigns?.length ? { signs: demoSigns } : {}) } : outcome.record;
    // Her words stay on screen; what Sakhi understood goes underneath.
    this.patchLine(i, { text: said || this.say('vNotClear'), sub: demo || rec.value === said ? undefined : rec.value, pending: false, offline: false, danger: rec.danger });
    this.record(i, rec);
    return demo ? null : outcome;
  }

  /**
   * Write a question's record; a danger sign raises its alert once. An urgent
   * sign stops the questionnaire at the next boundary; any other danger is
   * announced at the next question.
   */
  private record(i: number, rec: AnswerRecord) {
    const q = this.questions[i];
    const me = store.get().find((m) => m.id === this.mother.id);
    const draft = me?.draft ?? {};
    const prev = draft[q.id];
    store.recordAnswer(this.mother.id, q.id, rec);
    this.patchLine(i, { danger: rec.danger });
    const codes = (rec.signs ?? []).map((sg) => SIGN_RULE[sg]).filter((c): c is RuleCode => !!c && RULES[c].severity !== 'watch');
    if (!codes.length && q.rule) codes.push(q.rule);
    if (!rec.danger || !codes.length) return;
    const hits = evaluate({ ...draft, [q.id]: rec }, clinicalFacts(me?.clinical));
    const code: RuleCode = hits.some((h) => h.code === 'PE-04') && codes.includes('PE-03') ? 'PE-04' : worst(codes);
    this.set({ alerts: [...this.snap.alerts.filter((a) => a.idx !== i), { idx: i, qid: q.id, code, text: rec.value, prev }] });
    if (this.noticed.has(i)) return;
    this.noticed.add(i);
    Haptic.warning();
    const urgent = (rec.signs ?? []).filter((sg) => URGENT_SIGNS.has(sg) && !this.acted.has(sg));
    if (urgent.length) this.urgentQueue.push({ idx: i, qid: q.id, code, signs: urgent, text: rec.value });
    else this.notices.push(SAKHI_REPLIES[this.lang].danger);
  }

  // ---- end -----------------------------------------------------------------------

  private async finishFlow(my: number) {
    this.go('finishing', { reason: null });
    // The closing question already thanked her: no second goodbye.
    if (!this.closingSaid) {
      const done = SAKHI_REPLIES[this.lang].done;
      this.line({ from: 'sakhi', text: done });
      await this.speakLine(done);
    }
    this.mic?.dispose();
    this.mic = null;
    vlog('run', 'all questions asked; waiting for owed transcripts');
    // Wait for owed transcripts (bounded), then one more try at the outbox.
    const until = Date.now() + FINISH_WAIT_MS;
    while ((this.running || this.queue.length) && Date.now() < until && my === this.token) await this.wait(200);
    if (my !== this.token) return;
    if (!gateway.offline) {
      const owed = [...this.audio.keys()].filter((i) => !this.retried.has(i));
      owed.forEach((i) => {
        this.retried.add(i);
        this.enqueue(i);
      });
      while ((this.running || this.queue.length) && Date.now() < until + 4000 && my === this.token) await this.wait(200);
    }
    if (my !== this.token) return;
    voiceDb.endSession(this.sessionId, 'done').catch(() => {});
    vlog('run', `done; ${this.audio.size} answer(s) still offline`);
    this.go('done', { offline: gateway.offline });
    Haptic.success();
  }

  /** Commit the conversation for the report; returns the report id. */
  finish(): string {
    const lines: TranscriptLine[] = this.snap.lines.map(({ from, text, sub, danger }) => ({ from, text, sub, danger }));
    store.saveDraftTranscript(this.mother.id, this.sessionId, lines);
    this.release();
    return this.sessionId;
  }

  // ---- ASHA controls ------------------------------------------------------------

  /** Say the current question again (the mother did not hear it). */
  /** Nobody answering: say so once, switch the mic off, wait for Continue. */
  private async hold(i: number, my: number) {
    vlog('run', `Q${i + 1}: no voice for ${this.silentRun} questions in a row: holding until Continue`);
    const away = this.say('vAway');
    this.line({ from: 'sakhi', text: away });
    await this.speakLine(away);
    if (my !== this.token) return;
    this.token++;
    this.listening = null;
    this.forceEnd = null;
    this.mic?.stop();
    this.set({ held: true, idx: i, level: 0 });
  }

  /** The ASHA is back: the question that went unanswered is asked again. */
  continueAfterHold() {
    if (!this.snap.held) return;
    vlog('run', `continue: Q${this.snap.idx + 1} again`);
    this.silentRun = 0;
    this.set({ held: false });
    this.run(this.snap.idx, ++this.token);
  }

  repeat() {
    if (this.snap.urgent) return;
    if (this.snap.held) return this.continueAfterHold();
    if (!['asking', 'listening', 'hearing'].includes(this.snap.stage) || this.paused) return;
    vlog('run', `repeat Q${this.snap.idx + 1}`);
    const my = ++this.token;
    this.remote?.stop();
    this.listening = null;
    this.forceEnd = null;
    this.speaker?.stop();
    this.mic?.stop();
    this.run(this.snap.idx, my);
  }

  /** Next: end the answer now (or move on if nothing was said). */
  next() {
    if (this.snap.stage !== 'listening' && this.snap.stage !== 'hearing') return;
    vlog('run', `next tapped on Q${this.snap.idx + 1}`);
    if (this.remote) this.remote.next();
    else this.forceEnd?.();
  }

  /** Testing tools (demo): the next danger question gets a danger answer. */
  setDemoNext(kind: 'normal' | 'danger') {
    this.demoNext = kind;
  }

  /** A misheard answer: take the flag back. */
  undoAlert(idx: number) {
    const a = this.snap.alerts.find((x) => x.idx === idx);
    if (!a) return;
    store.clearAnswer(this.mother.id, a.qid, a.prev);
    this.set({ alerts: this.snap.alerts.filter((x) => x.idx !== idx), lines: this.snap.lines.map((l) => (l.idx === idx && l.from === 'asha' ? { ...l, danger: false } : l)) });
  }

  /** Testing tools: answer every remaining question "normal" and finish. */
  fillRemaining() {
    const my = ++this.token;
    this.listening = null;
    this.remote?.stop();
    this.speaker?.stop();
    for (let i = this.snap.idx; i < this.questions.length; i++) {
      if (!this.snap.lines.some((l) => l.idx === i && l.from === 'asha')) {
        const q = this.questions[i];
        if (i > this.snap.idx) this.line({ from: 'sakhi', text: questionText(q, this.lang), sub: this.lang === 'en' ? undefined : q.en, idx: i });
        this.handle(i, { kind: 'demo', value: q.sampleNormal, danger: false });
      }
    }
    this.set({ idx: this.questions.length - 1 });
    this.finishFlow(my);
  }
}
