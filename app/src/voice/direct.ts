import type { Lang } from '@/i18n/strings';

import { gateway } from './gateway';
import type { RemoteAnswer, RemoteVoice } from './live';
import { short, vlog } from './log';
import { AFTER_STOP_MS, Mic, playbackMode } from './mic';
import { Speaker, type VoiceSource } from './speaker';
import { FRAME_SAMPLES } from './vad';

// Sakhi Direct: the check-in's voice over ONE WebSocket to the gateway
// (sakhi_ws.py). Built into React Native, so it works in Expo Go with no
// WebView, no WebRTC and no extra native module.
//
//   roles     the question ENGINE (engine.ts) stays the single state manager;
//             this class is a RemoteVoice: say a line, hear one answer
//   out       every line Sakhi will say is fetched over the socket while the
//             intro is read and kept on the phone (Speaker.put): a line plays
//             from disk, with no network wait, the moment it is due
//   in        half-duplex like the phone mode: the mic is on ONLY while Sakhi
//             listens; 20 ms PCM16 frames stream to the server, which finds
//             the start and end of the answer (Silero VAD) and transcribes it
//   order     one outstanding listen; every reply carries its seq and a reply
//             for any other seq is dropped: a late answer can never land on
//             the wrong question
//   network   ping every 10 s (round trip shown on screen). A dropped socket
//             reconnects by itself (3 tries); an answer that was being heard
//             comes back as "silence", so Sakhi simply asks again and the
//             flow never stops. Only if the server stays away does the
//             engine continue on the phone, at the same question.
//
//   connection  idle -> connecting -> live <-> reconnecting -> ended | failed

const OPEN_MS = 6000;
const READY_MS = 6000;
const LINE_MS = 8000;
const ANSWER_MS = 45_000;
const PING_MS = 10_000;
const RETRY_MS = [300, 1000, 2500];

export type DirectStage = 'idle' | 'connecting' | 'live' | 'reconnecting' | 'ended' | 'failed';
export type DirectSnapshot = {
  stage: DirectStage;
  /** Socket round trip, from the pings. */
  rttMs: number | null;
  /** The last answer's transcription time on the server. */
  sttMs: number | null;
  /** Lines on the phone / lines in this check-in. */
  ready: number;
  total: number;
  error: string | null;
};

type Msg = { t: string; seq?: number; id?: number; text?: string; bytes?: number; ms?: number; sttMs?: number; skipped?: boolean; message?: string; at?: number };

type Listening = {
  seq: number;
  resolve: (a: RemoteAnswer) => void;
  onSpeech?: () => void;
  timer: ReturnType<typeof setTimeout>;
};

/** A socket that has not opened yet (also what a screen shows when there is no direct session). */
export const DIRECT_IDLE: DirectSnapshot = { stage: 'idle', rttMs: null, sttMs: null, ready: 0, total: 0, error: null };

export class DirectSession implements RemoteVoice {
  readonly transport = 'websocket' as const;
  private snap: DirectSnapshot = DIRECT_IDLE;
  private listeners = new Set<() => void>();
  private ws: WebSocket | null = null;
  private lines: string[] = [];
  private speaker: Speaker;
  private mic: Mic;
  private seq = 0;
  /** Bumped by stop(): a say() or listen that was cancelled must not continue. */
  private turn = 0;
  private listening: Listening | null = null;
  private levelCb: ((level: number) => void) | null = null;
  private lostCb: ((why: string) => void) | null = null;
  private released = false;
  private needPlayback = true;
  private pinger: ReturnType<typeof setInterval> | null = null;
  // Line audio: request id -> waiter; the next binary frame belongs to `expect`.
  private reqId = 0;
  private waiting = new Map<number, { text: string; resolve: (ok: boolean) => void }>();
  private inflight = new Map<string, Promise<boolean>>();
  private expect: { id: number; bytes: number } | null = null;
  private ready: (() => void) | null = null;

  constructor(
    readonly lang: Lang,
    onVoice: (v: VoiceSource) => void = () => {},
  ) {
    // No gateway downloads here: lines come over the socket (put); a line
    // that never arrives is said by the phone's backup voice, never silence.
    this.speaker = new Speaker(lang, false, onVoice);
    this.mic = new Mic((pcm, off) => this.onFrame(pcm, off));
  }

  // ---- React bridge --------------------------------------------------------
  subscribe = (l: () => void) => {
    this.listeners.add(l);
    return () => void this.listeners.delete(l);
  };
  getSnapshot = () => this.snap;
  private set(patch: Partial<DirectSnapshot>) {
    this.snap = { ...this.snap, ...patch };
    this.listeners.forEach((l) => l());
  }

  /** Is the WebSocket path configured at all? */
  static get configured() {
    return !!gateway.wsUrl;
  }

  // ---- connection ------------------------------------------------------------

  private connecting: Promise<void> | null = null;

  /**
   * Open the socket, say hello, and start bringing every line to the phone.
   * Idempotent: the screen calls it on the intro (so the socket is open and
   * the lines are arriving while the ASHA reads), the engine again on Start.
   */
  connect(lines: string[]): Promise<void> {
    if (this.snap.stage === 'live') return Promise.resolve();
    this.connecting ??= this.doConnect(lines).finally(() => (this.connecting = null));
    return this.connecting;
  }

  private async doConnect(lines: string[]): Promise<void> {
    // Only lines with no recording in the app go to the server: it would
    // otherwise synthesise (and pay for) audio the phone already has.
    this.lines = lines.filter((l) => !this.speaker.has(l));
    this.set({ stage: 'connecting', error: null, total: lines.length, ready: lines.length - this.lines.length });
    const t = Date.now();
    try {
      await this.open();
    } catch (e) {
      const why = String((e as Error)?.message ?? e);
      this.set({ stage: 'failed', error: why });
      vlog('err', `direct: could not connect (${why})`);
      throw e;
    }
    vlog('live', `direct: WebSocket ready ${Date.now() - t} ms after Start`);
    this.set({ stage: 'live' });
    this.fetchAll();
  }

  private open(): Promise<void> {
    if (!gateway.wsUrl) return Promise.reject(new Error('gateway not configured'));
    return new Promise((resolve, reject) => {
      const t = Date.now();
      let settled = false;
      const fail = (why: string) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        this.ready = null;
        try {
          ws.close();
        } catch {
          // already closed
        }
        reject(new Error(why));
      };
      const timer = setTimeout(() => fail(this.ws === ws && ws.readyState === WebSocket.OPEN ? 'no hello from the server' : 'connect timeout'), OPEN_MS + READY_MS);
      const ws = gateway.openSocket();
      ws.binaryType = 'arraybuffer';
      this.ws = ws;
      ws.onopen = () => {
        vlog('live', `direct: socket open in ${Date.now() - t} ms`);
        this.ready = () => {
          if (settled) return;
          settled = true;
          clearTimeout(timer);
          this.ready = null;
          this.startPing();
          resolve();
        };
        ws.send(JSON.stringify({ t: 'hello', lang: this.lang, lines: this.lines }));
      };
      ws.onmessage = (e) => this.onMessage(e.data);
      ws.onerror = () => fail('socket error');
      ws.onclose = (e) => {
        if (!settled) return fail(`closed (${e.code})`);
        if (this.ws === ws) this.onDrop(`closed (${e.code}${e.reason ? ` ${e.reason}` : ''})`);
      };
    });
  }

  /** The socket went away mid check-in: reconnect, keep the flow going. */
  private async onDrop(why: string) {
    if (this.released || this.snap.stage === 'ended') return;
    this.stopPing();
    this.ws = null;
    vlog('err', `direct: ${why}; reconnecting`);
    this.set({ stage: 'reconnecting' });
    // An answer that was being heard is lost with the socket: Sakhi asks again.
    this.resolveListen({ kind: 'silence' });
    this.waiting.forEach((w) => w.resolve(false));
    this.waiting.clear();
    this.expect = null;
    for (const wait of RETRY_MS) {
      await new Promise((r) => setTimeout(r, wait));
      if (this.released) return;
      try {
        await this.open();
        vlog('live', 'direct: reconnected');
        this.set({ stage: 'live' });
        this.fetchAll();
        return;
      } catch (e) {
        vlog('err', `direct: reconnect failed (${String((e as Error)?.message ?? e)})`);
      }
    }
    this.set({ stage: 'failed', error: why });
    this.lostCb?.(why);
  }

  private startPing() {
    this.stopPing();
    const ping = () => this.sendJson({ t: 'ping', at: Date.now() });
    ping();
    this.pinger = setInterval(ping, PING_MS);
  }
  private stopPing() {
    if (this.pinger) clearInterval(this.pinger);
    this.pinger = null;
  }

  private sendJson(msg: Record<string, unknown>) {
    const ws = this.ws;
    if (!ws || ws.readyState !== WebSocket.OPEN) return false;
    ws.send(JSON.stringify(msg));
    return true;
  }

  // ---- messages ----------------------------------------------------------------

  private onMessage(data: unknown) {
    if (typeof data !== 'string') {
      this.onBinary(data as ArrayBuffer);
      return;
    }
    let m: Msg;
    try {
      m = JSON.parse(data) as Msg;
    } catch {
      return;
    }
    switch (m.t) {
      case 'ready':
        this.ready?.();
        break;
      case 'pong':
        if (typeof m.at === 'number') this.set({ rttMs: Date.now() - m.at });
        break;
      case 'audio':
        this.expect = { id: Number(m.id), bytes: Number(m.bytes) || 0 };
        break;
      case 'audioError': {
        const w = this.waiting.get(Number(m.id));
        vlog('err', `direct: no audio for "${short(w?.text ?? '')}" (${String(m.message)})`);
        this.waiting.delete(Number(m.id));
        w?.resolve(false);
        break;
      }
      default:
        this.onTurnMessage(m);
    }
  }

  private onBinary(buf: ArrayBuffer) {
    const e = this.expect;
    this.expect = null;
    if (!e) return;
    const w = this.waiting.get(e.id);
    this.waiting.delete(e.id);
    if (!w) return;
    const bytes = new Uint8Array(buf);
    if (bytes.byteLength < 44 || (e.bytes && bytes.byteLength !== e.bytes)) {
      vlog('err', `direct: bad audio for "${short(w.text)}" (${bytes.byteLength} B)`);
      w.resolve(false);
      return;
    }
    this.speaker.put(w.text, bytes).then(
      () => {
        this.set({ ready: this.snap.total - this.lines.filter((l) => !this.speaker.has(l)).length });
        w.resolve(true);
      },
      () => w.resolve(false),
    );
  }

  private onTurnMessage(m: Msg) {
    const l = this.listening;
    if (!l || m.seq !== l.seq) {
      if (m.seq !== undefined) vlog('live', `direct: dropped stale "${m.t}" (seq ${String(m.seq)}, waiting for ${l ? l.seq : 'nothing'})`);
      return;
    }
    switch (m.t) {
      case 'speech':
        l.onSpeech?.();
        break;
      case 'heard':
        vlog('stt', `direct: heard in ${String(m.sttMs)} ms: "${short(String(m.text ?? ''), 60)}"`);
        this.set({ sttMs: Number(m.sttMs) || null });
        this.resolveListen({ kind: 'text', text: String(m.text ?? ''), sttMs: Number(m.sttMs) || 0 });
        break;
      case 'silence':
        this.resolveListen(m.skipped ? { kind: 'skip' } : { kind: 'silence' });
        break;
      case 'error':
        // A failed transcription is not a lost connection: Sakhi asks again.
        vlog('err', `direct: ${String(m.message)}`);
        this.resolveListen({ kind: 'silence' });
        break;
    }
  }

  // ---- line audio -----------------------------------------------------------------

  /** Make sure this line's audio is on the phone (asks the server if not). */
  private ensure(text: string, prio: 'now' | 'next' = 'now'): Promise<boolean> {
    if (this.speaker.has(text)) return Promise.resolve(true);
    const running = this.inflight.get(text);
    if (running) return running;
    const p = new Promise<boolean>((resolve) => {
      const id = ++this.reqId;
      const timer = setTimeout(() => {
        this.waiting.delete(id);
        resolve(false);
      }, LINE_MS);
      this.waiting.set(id, {
        text,
        resolve: (ok) => {
          clearTimeout(timer);
          resolve(ok);
        },
      });
      if (!this.sendJson({ t: 'tts', id, text, prio })) {
        this.waiting.delete(id);
        clearTimeout(timer);
        resolve(false);
      }
    }).finally(() => this.inflight.delete(text));
    this.inflight.set(text, p);
    return p;
  }

  /** Every line, in speaking order, one after another (the server warms them too). */
  private async fetchAll() {
    const t = Date.now();
    for (const line of this.lines) {
      if (this.released || this.snap.stage !== 'live') return;
      await this.ensure(line, 'next');
    }
    vlog('tts', `direct: ${this.snap.ready}/${this.lines.length} lines on the phone after ${Date.now() - t} ms`);
  }

  // ---- RemoteVoice ------------------------------------------------------------------

  async say(text: string, onStart?: () => void): Promise<boolean> {
    if (this.snap.stage !== 'live' && this.snap.stage !== 'reconnecting') return false;
    const my = ++this.turn;
    const t = Date.now();
    const have = await this.ensure(text, 'now');
    if (my !== this.turn || this.released) return false;
    if (!have) vlog('err', `direct: line not on the phone after ${Date.now() - t} ms: backup voice`);
    // The mic is off while she speaks; iOS needs the playback session back.
    if (this.mic.on) this.mic.stop();
    if (this.needPlayback) {
      const since = Date.now() - this.mic.stoppedAt;
      if (since < AFTER_STOP_MS) await new Promise((r) => setTimeout(r, AFTER_STOP_MS - since));
      await playbackMode().catch((e) => vlog('err', `audio mode: ${String(e)}`));
      this.needPlayback = false;
    }
    if (my !== this.turn) return false;
    await this.speaker.speak(text, onStart);
    return my === this.turn;
  }

  async ask(
    text: string,
    listen: { hangoverMs: number; noSpeechMs: number },
    hooks: { onStart?: () => void; onListening?: () => void; onSpeech?: () => void },
  ): Promise<RemoteAnswer> {
    const said = await this.say(text, hooks.onStart);
    if (!said) return { kind: 'lost', why: this.snap.stage === 'live' || this.snap.stage === 'reconnecting' ? 'stopped' : 'not connected' };
    const my = this.turn;
    // A socket that is coming back: wait for it rather than give up the turn.
    for (let i = 0; i < 40 && this.snap.stage === 'reconnecting'; i++) await new Promise((r) => setTimeout(r, 100));
    if (my !== this.turn) return { kind: 'lost', why: 'stopped' };
    if (this.snap.stage !== 'live') return { kind: 'lost', why: 'not connected' };
    const seq = ++this.seq;
    const answer = new Promise<RemoteAnswer>((resolve) => {
      const timer = setTimeout(() => {
        if (this.listening?.seq !== seq) return;
        vlog('err', `direct: no answer for seq ${seq} in ${ANSWER_MS / 1000} s`);
        this.resolveListen({ kind: 'silence' });
      }, ANSWER_MS);
      this.listening = { seq, resolve, onSpeech: hooks.onSpeech, timer };
    });
    this.sendJson({ t: 'listen', seq, hangoverMs: listen.hangoverMs, noSpeechMs: listen.noSpeechMs });
    try {
      await this.mic.start();
      this.needPlayback = true;
    } catch (e) {
      vlog('err', `direct: mic failed (${String((e as Error)?.message ?? e)})`);
      this.sendJson({ t: 'stop' });
      this.resolveListen({ kind: 'lost', why: 'mic', said: true });
    }
    if (this.listening?.seq === seq) hooks.onListening?.();
    return answer;
  }

  private resolveListen(a: RemoteAnswer) {
    const l = this.listening;
    if (!l) return;
    this.listening = null;
    clearTimeout(l.timer);
    if (this.mic.on) this.mic.stop();
    l.resolve(a);
  }

  /** Frames go to the server only while an answer is being listened for. */
  private onFrame(pcm: Int16Array, offset: number) {
    if (!this.listening) return;
    const frame = pcm.slice(offset, offset + FRAME_SAMPLES);
    const ws = this.ws;
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(frame.buffer);
    if (this.levelCb) {
      let sum = 0;
      for (let i = 0; i < frame.length; i++) sum += frame[i] * frame[i];
      this.levelCb(Math.min(1, (Math.sqrt(sum / frame.length) / 32768) * 6));
    }
  }

  next() {
    const l = this.listening;
    if (l) this.sendJson({ t: 'next', seq: l.seq });
  }

  stop() {
    this.turn++;
    this.speaker.stop();
    if (this.listening) {
      this.sendJson({ t: 'stop' });
      this.resolveListen({ kind: 'lost', why: 'stopped' });
    }
    if (this.mic.on) this.mic.stop();
  }

  onLevel(cb: ((level: number) => void) | null) {
    this.levelCb = cb;
  }

  onLost(cb: ((why: string) => void) | null) {
    this.lostCb = cb;
  }

  /** End of the check-in (finish, exit, leaving the screen). */
  release() {
    if (this.released) return;
    this.stop();
    this.released = true;
    this.stopPing();
    this.set({ stage: 'ended' });
    const ws = this.ws;
    this.ws = null;
    try {
      ws?.close(1000, 'done');
    } catch {
      // already closed
    }
    this.waiting.forEach((w) => w.resolve(false));
    this.waiting.clear();
    this.mic.dispose();
    this.speaker.release();
    this.levelCb = null;
    this.lostCb = null;
  }
}

/** The section's connection check: open, hello, one ping, close. */
export function probeDirect(lang: Lang = 'kn'): Promise<{ ok: boolean; ms: number; rttMs?: number; provider?: string; error?: string }> {
  return new Promise((resolve) => {
    const t = Date.now();
    if (!gateway.wsUrl) return resolve({ ok: false, ms: 0, error: 'gateway not configured' });
    let provider: string | undefined;
    let done = false;
    const ws = gateway.openSocket();
    const end = (r: { ok: boolean; rttMs?: number; error?: string }) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      try {
        ws.close(1000, 'probe');
      } catch {
        // already closed
      }
      resolve({ ...r, ms: Date.now() - t, provider });
    };
    const timer = setTimeout(() => end({ ok: false, error: 'timeout' }), OPEN_MS + READY_MS);
    ws.onopen = () => ws.send(JSON.stringify({ t: 'hello', lang, lines: [] }));
    ws.onmessage = (e) => {
      if (typeof e.data !== 'string') return;
      const m = JSON.parse(e.data) as Msg & { provider?: string };
      if (m.t === 'ready') {
        provider = m.provider;
        ws.send(JSON.stringify({ t: 'ping', at: Date.now() }));
      } else if (m.t === 'pong' && typeof m.at === 'number') end({ ok: true, rttMs: Date.now() - m.at });
    };
    ws.onerror = () => end({ ok: false, error: 'socket error' });
    ws.onclose = (e) => end({ ok: false, error: `closed (${e.code})` });
  });
}
