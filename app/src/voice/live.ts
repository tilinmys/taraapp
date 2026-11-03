import type { MotherRecord } from '@/data/store';
import type { Lang } from '@/i18n/strings';

import { gateway } from './gateway';
import { short, vlog } from './log';
import { liveAudioMode } from './mic';

// LIVE Sakhi: the connection to the voice agent (sakhi_app_agent.py) over
// WebRTC, and the command protocol the question engine (engine.ts) drives.
// The ENGINE is the single state manager (which question, what the answer
// means, flags, records); the agent only speaks one line and hears one answer.
//
//   network layers (docs/VOICE_IN_APP_DESIGN.md 10)
//     signalling  HTTPS to the gateway: /live/config (intro), /live/session
//                 (Start: room + token ~0.2 s; Sakhi dispatched in parallel)
//     media       WebRTC to LiveKit Cloud (India South, ~20 ms round trip):
//                 Opus, echo cancellation, DTX, RED, auto-reconnect. In a
//                 WebView (components/voice/live-bridge): Expo Go has no
//                 native WebRTC module
//     commands    reliable data channel, topic "sakhi.app", on the same
//                 connection: {t:"say", seq, text, listen} -> said / speech /
//                 heard {text} / silence / error
//
//   correctness
//     * one outstanding command; every reply carries its seq and a reply
//       whose seq is not the outstanding one is dropped (late, duplicate or
//       from a cancelled turn): a stale answer can never land on the wrong
//       question
//     * connection state is a small explicit machine:
//       idle -> ready -> connecting -> joining -> live -> ended | failed

const AGENT_JOIN_MS = 20_000;
const READY_MS = 8_000;
const SAY_TIMEOUT_MS = 30_000;
const ANSWER_TIMEOUT_MS = 45_000;
const HELLO_EVERY_MS = 1000;

export type LiveStage = 'idle' | 'ready' | 'connecting' | 'joining' | 'live' | 'ended' | 'failed';
export type LiveError = 'unavailable' | 'connect' | 'agent' | 'mic';
export type LiveSnapshot = {
  stage: LiveStage;
  available: boolean | null;
  me: number;
  muted: boolean;
  needsTap: boolean;
  reconnecting: boolean;
  rttMs: number | null;
  error: LiveError | null;
};

export type LiveCmd =
  | { cmd: 'prepare'; url: string }
  | { cmd: 'start'; opts: { url: string; token: string } }
  | { cmd: 'send'; msg: Record<string, unknown> }
  | { cmd: 'mute'; on: boolean }
  | { cmd: 'end' }
  | { cmd: 'resumeAudio' };

/**
 * The JavaScript a WebView runs for one command (page.html's window.sakhi).
 * Exhaustive on purpose: a command without a case is a type error, not a
 * silent fall-through (an unmapped "send" once became end(): the first
 * "hello" to the agent hung up the call). Covered by live.test.ts.
 */
export function pageCall(c: LiveCmd): string {
  switch (c.cmd) {
    case 'prepare':
      return `prepare(${JSON.stringify(c.url)})`;
    case 'start':
      return `start(${JSON.stringify(c.opts)})`;
    case 'send':
      return `send(${JSON.stringify(c.msg)})`;
    case 'mute':
      return `mute(${c.on ? 'true' : 'false'})`;
    case 'resumeAudio':
      return 'resumeAudio()';
    case 'end':
      return 'end()';
    default: {
      const never: never = c;
      throw new Error(`unmapped live command ${JSON.stringify(never)}`);
    }
  }
}

export const pageScript = (c: LiveCmd) => `window.sakhi && window.sakhi.${pageCall(c)}; true;`;

/** What the question engine gets back for one asked question. */
export type RemoteAnswer =
  | { kind: 'text'; text: string; sttMs: number }
  | { kind: 'silence' }
  | { kind: 'skip' }
  // said: the question was already spoken (the phone then only listens, no repeat)
  | { kind: 'lost'; why: string; said?: boolean };

/** The engine's view of a remote voice (see engine.ts). */
export interface RemoteVoice {
  /** Say one line. Resolves when it has been said (false: lost / cancelled). */
  say(text: string, onStart?: () => void): Promise<boolean>;
  /** Say a question, then listen for ONE answer (one round trip). */
  ask(text: string, listen: { hangoverMs: number; noSpeechMs: number }, hooks: { onStart?: () => void; onListening?: () => void; onSpeech?: () => void }): Promise<RemoteAnswer>;
  /** End the current answer now (the ASHA's Next). */
  next(): void;
  /** Cancel whatever is running (Repeat, pause, exit). */
  stop(): void;
  /** The ASHA's mic level for the orb, 10 a second. */
  onLevel(cb: ((level: number) => void) | null): void;
  /** The connection broke for good (the engine continues offline). */
  onLost(cb: ((why: string) => void) | null): void;
}

type PageMsg = { type: string; [k: string]: unknown };
type AgentMsg = { t: string; seq?: number; text?: string; ms?: number; skipped?: boolean; message?: string; lines?: number };

type Pending = {
  seq: number;
  kind: 'say' | 'ask';
  onStart?: () => void;
  onListening?: () => void;
  onSpeech?: () => void;
  resolve: (a: RemoteAnswer | 'said') => void;
  timer: ReturnType<typeof setTimeout>;
  said: boolean;
};

export class LiveSession implements RemoteVoice {
  private snap: LiveSnapshot = { stage: 'idle', available: null, me: 0, muted: false, needsTap: false, reconnecting: false, rttMs: null, error: null };
  private listeners = new Set<() => void>();
  private send: ((c: LiveCmd) => void) | null = null;
  private pageReady = false;
  private queue: LiveCmd[] = [];
  private startedAt = 0;
  private released = false;
  /** The app hung up itself (End, finish, exit): a closed room is expected. */
  private ending = false;
  private seq = 0;
  private pending: Pending | null = null;
  private levelCb: ((level: number) => void) | null = null;
  private lostCb: ((why: string) => void) | null = null;
  private agentReady: { resolve: () => void; reject: (e: Error) => void } | null = null;
  private joinTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(
    private readonly mother: MotherRecord,
    readonly lang: Lang,
  ) {}

  // ---- React bridge --------------------------------------------------------
  subscribe = (l: () => void) => {
    this.listeners.add(l);
    return () => void this.listeners.delete(l);
  };
  getSnapshot = () => this.snap;
  private set(patch: Partial<LiveSnapshot>) {
    this.snap = { ...this.snap, ...patch };
    this.listeners.forEach((l) => l());
  }

  // ---- the page (WebView / iframe) -------------------------------------------

  attach(send: ((c: LiveCmd) => void) | null) {
    this.send = send;
    if (!send) this.pageReady = false;
  }

  private command(c: LiveCmd) {
    if (this.send && this.pageReady) this.send(c);
    else this.queue.push(c);
  }

  onPageMessage(raw: unknown) {
    let m: PageMsg;
    try {
      m = (typeof raw === 'string' ? JSON.parse(raw) : raw) as PageMsg;
    } catch {
      return;
    }
    if (!m || typeof m.type !== 'string' || this.released) return;
    const since = () => Date.now() - this.startedAt;
    switch (m.type) {
      case 'ready':
        this.pageReady = true;
        this.queue.splice(0).forEach((c) => this.send?.(c));
        break;
      case 'prepared':
        vlog('live', `connection pre-warmed in ${String(m.ms)} ms`);
        break;
      case 'connected':
        vlog('live', `WebRTC connected ${since()} ms after Start`);
        if (this.snap.stage === 'connecting') this.set({ stage: 'joining' });
        break;
      case 'agentJoined':
        vlog('live', `Sakhi's voice agent joined ${since()} ms after Start`);
        this.sayHello();
        break;
      case 'agentMsg':
        this.onAgent(m.msg as AgentMsg);
        break;
      case 'level': {
        const me = Number(m.me) || 0;
        this.levelCb?.(me);
        if (Math.abs(me - this.snap.me) > 0.03) this.set({ me });
        break;
      }
      case 'stats':
        if (typeof m.rttMs === 'number' && m.rttMs > 0) this.set({ rttMs: m.rttMs });
        break;
      case 'net':
        vlog('live', m.state === 'reconnecting' ? 'network dropped: reconnecting' : 'network back');
        this.set({ reconnecting: m.state === 'reconnecting' });
        break;
      case 'needsTap':
        this.set({ needsTap: true });
        break;
      case 'muted':
        this.set({ muted: !!m.muted });
        break;
      case 'micRetry':
        vlog('live', `microphone publish retried (${String(m.message)})`);
        break;
      case 'testSaid':
        vlog('live', `test mic said: "${short(String(m.text), 50)}"`);
        break;
      case 'error':
        vlog('err', `live ${String(m.code)}: ${String(m.message)}`);
        this.fail(m.code === 'mic' ? 'mic' : 'connect', String(m.message));
        break;
      case 'ended': {
        if (this.snap.stage === 'ended' || this.snap.stage === 'failed') break;
        const why = `room closed (${String(m.reason || 'unknown')})`;
        vlog('live', `ended: ${why}`);
        // Closed while joining or mid check-in: Start must not wait for the
        // join timeout and the engine must continue offline at once.
        if (!this.ending && (this.snap.stage === 'live' || this.agentReady)) {
          this.fail('connect', why);
          break;
        }
        this.set({ stage: 'ended' });
        this.resolvePending({ kind: 'lost', why: 'ended' });
        break;
      }
    }
  }

  onPageError(what: string) {
    vlog('err', `live page failed to load: ${what}`);
    this.fail('connect', what);
  }

  // ---- the agent protocol -------------------------------------------------------

  private lines: string[] = [];

  /**
   * Hand the agent every line to pre-synthesize. The agent can appear in the
   * room a moment before it listens to commands, so "hello" (idempotent) is
   * re-sent every second until it answers "ready"; it also says "up" itself.
   */
  private helloTimer: ReturnType<typeof setInterval> | null = null;
  private sayHello() {
    const hello = () => this.command({ cmd: 'send', msg: { t: 'hello', lang: this.lang, lines: this.lines } });
    hello();
    if (this.helloTimer) return;
    let tries = 0;
    this.helloTimer = setInterval(() => {
      if (this.snap.stage === 'live' || this.released || ++tries > 15) return this.stopHello();
      hello();
    }, HELLO_EVERY_MS);
  }
  private stopHello() {
    if (this.helloTimer) clearInterval(this.helloTimer);
    this.helloTimer = null;
  }

  private onAgent(msg: AgentMsg) {
    if (!msg || typeof msg.t !== 'string') return;
    if (msg.t === 'up') {
      vlog('live', 'voice agent takes commands');
      this.sayHello();
      return;
    }
    if (msg.t === 'ready') {
      if (this.snap.stage === 'live') return; // a repeated hello's answer
      this.stopHello();
      vlog('live', `voice agent ready (${String(msg.lines)} lines to pre-synthesize)`);
      this.set({ stage: 'live' });
      this.agentReady?.resolve();
      this.agentReady = null;
      return;
    }
    const p = this.pending;
    // Only the outstanding command's replies count.
    if (!p || msg.seq !== p.seq) {
      vlog('live', `dropped stale "${msg.t}" (seq ${String(msg.seq)}, waiting for ${p ? p.seq : 'nothing'})`);
      return;
    }
    switch (msg.t) {
      case 'said':
        p.said = true;
        if (p.kind === 'say') this.resolvePending('said');
        else p.onListening?.();
        break;
      case 'speech':
        p.onSpeech?.();
        break;
      case 'heard':
        vlog('stt', `agent heard in ${String(msg.ms)} ms: "${short(String(msg.text ?? ''), 60)}"`);
        this.resolvePending({ kind: 'text', text: String(msg.text ?? ''), sttMs: Number(msg.ms) || 0 });
        break;
      case 'silence':
        this.resolvePending(msg.skipped ? { kind: 'skip' } : { kind: 'silence' });
        break;
      case 'error':
        vlog('err', `voice agent: ${String(msg.message)}`);
        this.resolvePending({ kind: 'lost', why: String(msg.message) });
        break;
    }
  }

  private resolvePending(a: RemoteAnswer | 'said') {
    const p = this.pending;
    if (!p) return;
    this.pending = null;
    clearTimeout(p.timer);
    p.resolve(a);
  }

  private issue(kind: 'say' | 'ask', text: string, listen: { hangoverMs: number; noSpeechMs: number } | null, hooks: Partial<Pending>): Promise<RemoteAnswer | 'said'> {
    // One outstanding command: a new one supersedes (and resolves) the old.
    this.resolvePending({ kind: 'lost', why: 'superseded' });
    const seq = ++this.seq;
    return new Promise((resolve) => {
      const timeout = kind === 'say' ? SAY_TIMEOUT_MS : ANSWER_TIMEOUT_MS;
      const timer = setTimeout(() => {
        if (this.pending?.seq !== seq) return;
        vlog('err', `voice agent did not answer seq ${seq} in ${timeout / 1000} s`);
        this.resolvePending({ kind: 'lost', why: 'timeout' });
        this.lostCb?.('timeout');
      }, timeout);
      this.pending = { seq, kind, resolve, timer, said: false, ...hooks };
      this.command({ cmd: 'send', msg: { t: 'say', seq, text, listen } });
    });
  }

  async say(text: string, onStart?: () => void) {
    if (this.snap.stage !== 'live') return false;
    onStart?.();
    const r = await this.issue('say', text, null, {});
    return r === 'said';
  }

  async ask(text: string, listen: { hangoverMs: number; noSpeechMs: number }, hooks: { onStart?: () => void; onListening?: () => void; onSpeech?: () => void }): Promise<RemoteAnswer> {
    if (this.snap.stage !== 'live') return { kind: 'lost', why: 'not connected' };
    hooks.onStart?.();
    const r = await this.issue('ask', text, listen, hooks);
    return r === 'said' ? { kind: 'lost', why: 'protocol' } : r;
  }

  next() {
    if (this.pending) this.command({ cmd: 'send', msg: { t: 'next', seq: this.pending.seq } });
  }

  stop() {
    this.resolvePending({ kind: 'lost', why: 'stopped' });
    this.command({ cmd: 'send', msg: { t: 'stop' } });
  }

  onLevel(cb: ((level: number) => void) | null) {
    this.levelCb = cb;
  }

  onLost(cb: ((why: string) => void) | null) {
    this.lostCb = cb;
  }

  // ---- connection ----------------------------------------------------------------

  /** On the intro: is live possible, and warm the connection meanwhile. */
  async prepare() {
    const c = await gateway.liveConfig();
    if (this.released) return;
    const available = c.available && c.agentReady !== false;
    this.set({ available, stage: available ? 'ready' : 'idle' });
    if (available && c.url) this.command({ cmd: 'prepare', url: c.url });
  }

  /**
   * Connect and wait until the voice agent is in the room and ready.
   * `lines` = everything Sakhi will say, for it to pre-synthesize.
   */
  async connect(lines: string[]): Promise<void> {
    this.lines = lines;
    this.startedAt = Date.now();
    this.ending = false;
    this.set({ stage: 'connecting', error: null, needsTap: false });
    vlog('live', `connect: ${this.mother.name}, ${this.lang}`);
    await liveAudioMode().catch(() => {});
    const ready = new Promise<void>((resolve, reject) => (this.agentReady = { resolve, reject }));
    try {
      const s = await gateway.liveSession(this.lang, { id: this.mother.id, name: this.mother.name, months: this.mother.months });
      if (this.released) throw new Error('released');
      vlog('live', `session ${s.room} in ${Date.now() - this.startedAt} ms`);
      this.command({ cmd: 'start', opts: { url: s.url, token: s.token } });
    } catch (e) {
      this.fail('connect', String((e as Error)?.message ?? e));
      throw e;
    }
    this.clearJoinTimer();
    this.joinTimer = setTimeout(() => this.fail('agent', `voice agent not ready within ${(AGENT_JOIN_MS + READY_MS) / 1000} s`), AGENT_JOIN_MS + READY_MS);
    await ready;
    this.clearJoinTimer();
    vlog('live', `LIVE ${Date.now() - this.startedAt} ms after Start`);
  }

  private fail(error: LiveError, why: string) {
    this.clearJoinTimer();
    this.stopHello();
    const wasLive = this.snap.stage === 'live';
    this.set({ stage: 'failed', error });
    this.agentReady?.reject(new Error(why));
    this.agentReady = null;
    this.resolvePending({ kind: 'lost', why });
    if (wasLive) this.lostCb?.(why);
  }

  mute(on: boolean) {
    this.command({ cmd: 'mute', on });
  }

  resumeAudio() {
    this.set({ needsTap: false });
    this.command({ cmd: 'resumeAudio' });
  }

  end() {
    this.ending = true;
    this.stop();
    this.command({ cmd: 'end' });
  }

  release() {
    if (this.released) return;
    this.end();
    this.released = true;
    this.clearJoinTimer();
    this.stopHello();
    this.levelCb = null;
    this.lostCb = null;
    this.send = null;
  }

  private clearJoinTimer() {
    if (this.joinTimer) clearTimeout(this.joinTimer);
    this.joinTimer = null;
  }
}
