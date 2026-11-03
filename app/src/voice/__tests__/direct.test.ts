import { afterEach, beforeEach, describe, expect, it, jest } from '@jest/globals';

import { FakeSocket, wav } from '@/test/fake-socket';

// Sakhi Direct's network layer (voice/direct.ts) against a fake server:
// handshake, line audio framing, the listen protocol, stale replies,
// Next / Stop, dropped connections and reconnects, a failing microphone.

jest.mock('../gateway', () => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { FakeSocket: Socket } = require('@/test/fake-socket');
  return { gateway: { wsUrl: 'ws://test/ws', openSocket: () => new Socket() } };
});
jest.mock('../mic', () => {
  class Mic {
    static last: Mic;
    static failNext = false;
    on = false;
    stoppedAt = 0;
    cb: (pcm: Int16Array, offset: number) => void;
    constructor(fn: (pcm: Int16Array, offset: number) => void) {
      this.cb = fn;
      Mic.last = this;
    }
    async start() {
      if (Mic.failNext) {
        Mic.failNext = false;
        throw new Error('permission denied');
      }
      this.on = true;
    }
    stop() {
      this.on = false;
      this.stoppedAt = Date.now();
    }
    dispose() {
      this.on = false;
    }
  }
  return { Mic, AFTER_STOP_MS: 0, playbackMode: async () => {} };
});
jest.mock('../speaker', () => {
  class Speaker {
    static last: Speaker;
    files = new Map<string, Uint8Array>();
    spoken: string[] = [];
    constructor() {
      Speaker.last = this;
    }
    has(t: string) {
      return this.files.has(t);
    }
    async put(t: string, b: Uint8Array) {
      this.files.set(t, b);
    }
    async speak(t: string, onStart?: () => void) {
      this.spoken.push(t);
      onStart?.();
    }
    stop() {}
    release() {}
  }
  return { Speaker };
});

// eslint-disable-next-line import/first
import { DirectSession, probeDirect } from '../direct';

type MicMock = { last: { on: boolean; cb: (pcm: Int16Array, offset: number) => void }; failNext: boolean };
type SpeakerMock = { last: { files: Map<string, Uint8Array>; spoken: string[] } };
const Mic = (jest.requireMock('../mic') as { Mic: MicMock }).Mic;
const Speaker = (jest.requireMock('../speaker') as { Speaker: SpeakerMock }).Speaker;

const tick = () => new Promise<void>((r) => setTimeout(r, 0));
// Every session is released after its test: no ping timer outlives a test.
const sessions: DirectSession[] = [];
const track = (s: DirectSession) => (sessions.push(s), s);
const LISTEN = { hangoverMs: 800, noSpeechMs: 7000 };

/** The server's side of a line request. */
function serveLine(sock: FakeSocket, text: string, bytes = wav()) {
  const req = sock.messages().find((m) => m.t === 'tts' && m.text === text);
  if (!req) throw new Error(`no request for "${text}"`);
  sock.json({ t: 'audio', id: req.id, bytes: bytes.byteLength });
  sock.binary(bytes);
}

async function connected(lines = ['L1', 'L2']) {
  const s = track(new DirectSession('kn'));
  const p = s.connect(lines);
  const sock = FakeSocket.last();
  sock.accept();
  sock.json({ t: 'ready', provider: 'gnani' });
  await p;
  return { s, sock };
}

beforeEach(() => {
  FakeSocket.reset();
  (globalThis as { WebSocket: unknown }).WebSocket = FakeSocket;
});
afterEach(() => {
  sessions.splice(0).forEach((s) => s.release());
  jest.useRealTimers();
});

describe('DirectSession: connection', () => {
  it('says hello with the language and every line, then is live', async () => {
    const { s, sock } = await connected(['greeting', 'Q1']);
    expect(sock.messages()[0]).toEqual({ t: 'hello', lang: 'kn', lines: ['greeting', 'Q1'] });
    expect(sock.binaryType).toBe('arraybuffer');
    expect(s.getSnapshot().stage).toBe('live');
  });

  it('connect is idempotent (the intro and Start both call it)', async () => {
    const s = track(new DirectSession('kn'));
    const a = s.connect(['x']);
    const b = s.connect(['x']);
    expect(FakeSocket.all).toHaveLength(1);
    FakeSocket.last().accept();
    FakeSocket.last().json({ t: 'ready' });
    await Promise.all([a, b]);
    await s.connect(['x']);
    expect(FakeSocket.all).toHaveLength(1);
  });

  it('a socket that never opens fails cleanly (no hang)', async () => {
    jest.useFakeTimers();
    const s = track(new DirectSession('kn'));
    const p = s.connect(['x']);
    const failed = expect(p).rejects.toThrow(/timeout|socket/);
    await jest.advanceTimersByTimeAsync(13_000);
    await failed;
    expect(s.getSnapshot().stage).toBe('failed');
  });
});

describe('DirectSession: line audio', () => {
  it('fetches lines one at a time, in speaking order, and stores them', async () => {
    const { s, sock } = await connected(['L1', 'L2']);
    await tick();
    expect(sock.messages().filter((m) => m.t === 'tts').map((m) => m.text)).toEqual(['L1']);
    serveLine(sock, 'L1');
    await tick();
    await tick();
    expect(Speaker.last.files.has('L1')).toBe(true);
    expect(sock.messages().filter((m) => m.t === 'tts').map((m) => m.text)).toEqual(['L1', 'L2']);
    serveLine(sock, 'L2');
    await tick();
    await tick();
    expect(s.getSnapshot().ready).toBe(2);
  });

  it('rejects audio whose size does not match its header', async () => {
    const { sock } = await connected(['L1']);
    await tick();
    const req = sock.messages().find((m) => m.t === 'tts')!;
    sock.json({ t: 'audio', id: req.id, bytes: 9999 });
    sock.binary(wav());
    await tick();
    expect(Speaker.last.files.has('L1')).toBe(false);
  });

  it('ignores a binary frame that no header announced', async () => {
    const { sock } = await connected(['L1']);
    await tick();
    sock.binary(wav());
    await tick();
    expect(Speaker.last.files.size).toBe(0);
  });

  it('a line the server cannot make is still said (backup voice), never skipped', async () => {
    const { s, sock } = await connected([]);
    const said = s.say('Q9');
    await tick();
    const req = sock.messages().find((m) => m.t === 'tts' && m.text === 'Q9')!;
    sock.json({ t: 'audioError', id: req.id, message: 'busy' });
    await expect(said).resolves.toBe(true);
    expect(Speaker.last.spoken).toEqual(['Q9']);
  });
});

describe('DirectSession: one turn', () => {
  async function askQ1() {
    const ctx = await connected([]);
    const hooks = { onStart: jest.fn(), onListening: jest.fn(), onSpeech: jest.fn() };
    const answer = ctx.s.ask('Q1', LISTEN, hooks);
    await tick();
    serveLine(ctx.sock, 'Q1');
    await tick();
    await tick();
    await tick();
    const listen = ctx.sock.messages().find((m) => m.t === 'listen')!;
    return { ...ctx, hooks, answer, seq: listen.seq as number, listen };
  }

  it('speaks, then listens with the turn settings, then hears ONE answer', async () => {
    const { sock, hooks, answer, seq, listen } = await askQ1();
    expect(Speaker.last.spoken).toEqual(['Q1']);
    expect(listen).toEqual({ t: 'listen', seq, hangoverMs: 800, noSpeechMs: 7000 });
    expect(Mic.last.on).toBe(true);
    expect(hooks.onListening).toHaveBeenCalledTimes(1);
    // mic frames go to the server as 20 ms PCM16 binary frames (640 bytes)
    Mic.last.cb(new Int16Array(640).fill(5), 0);
    expect(sock.binaries()).toHaveLength(1);
    expect(sock.binaries()[0].byteLength).toBe(640);
    sock.json({ t: 'speech', seq });
    expect(hooks.onSpeech).toHaveBeenCalledTimes(1);
    sock.json({ t: 'heard', seq, text: 'ಬಿ ಪಿ 148 ಬೈ 96', sttMs: 420 });
    await expect(answer).resolves.toEqual({ kind: 'text', text: 'ಬಿ ಪಿ 148 ಬೈ 96', sttMs: 420 });
    expect(Mic.last.on).toBe(false); // half-duplex: mic off as soon as the turn ends
    Mic.last.cb(new Int16Array(320), 0);
    expect(sock.binaries()).toHaveLength(1); // nothing streams between turns
  });

  it('drops a reply for any other seq (a late answer never lands on this question)', async () => {
    const { sock, answer, seq } = await askQ1();
    let settled = false;
    answer.then(() => (settled = true));
    sock.json({ t: 'heard', seq: seq - 1, text: 'old answer' });
    await tick();
    expect(settled).toBe(false);
    sock.json({ t: 'heard', seq, text: 'right answer' });
    await expect(answer).resolves.toMatchObject({ text: 'right answer' });
  });

  it('Next asks the server to end THIS answer now', async () => {
    const { s, sock, seq } = await askQ1();
    s.next();
    expect(sock.messages().at(-1)).toEqual({ t: 'next', seq });
  });

  it('Stop cancels the turn and tells the server', async () => {
    const { s, sock, answer } = await askQ1();
    s.stop();
    await expect(answer).resolves.toEqual({ kind: 'lost', why: 'stopped' });
    expect(sock.messages().at(-1)).toEqual({ t: 'stop' });
    expect(Mic.last.on).toBe(false);
  });

  it('a failed transcription is "silence" (Sakhi asks again), not a lost call', async () => {
    const { sock, answer, seq } = await askQ1();
    sock.json({ t: 'error', seq, message: 'stt: busy' });
    await expect(answer).resolves.toEqual({ kind: 'silence' });
  });

  it('a mic that will not start: lost, the server is told, no repeat of the question', async () => {
    const ctx = await connected([]);
    Mic.failNext = true;
    const answer = ctx.s.ask('Q1', LISTEN, {});
    await tick();
    serveLine(ctx.sock, 'Q1');
    await expect(answer).resolves.toEqual({ kind: 'lost', why: 'mic', said: true });
    expect(ctx.sock.messages().map((m) => m.t)).toContain('stop');
  });
});

describe('DirectSession: dropped connections', () => {
  it('a drop mid-answer returns "silence", then reconnects and says hello again', async () => {
    const { s, sock } = await connected(['L1']);
    jest.useFakeTimers();
    const answer = s.ask('Q1', LISTEN, {});
    await jest.advanceTimersByTimeAsync(0);
    serveLine(sock, 'Q1');
    await jest.advanceTimersByTimeAsync(0);
    sock.drop();
    await expect(answer).resolves.toEqual({ kind: 'silence' });
    expect(s.getSnapshot().stage).toBe('reconnecting');
    await jest.advanceTimersByTimeAsync(300);
    const again = FakeSocket.last();
    expect(again).not.toBe(sock);
    again.accept();
    expect(again.messages()[0]).toMatchObject({ t: 'hello', lines: ['L1'] });
    again.json({ t: 'ready' });
    await jest.advanceTimersByTimeAsync(0);
    expect(s.getSnapshot().stage).toBe('live');
  });

  it('three failed reconnects: the engine is told (it continues on the phone)', async () => {
    const { s, sock } = await connected([]);
    const lost = jest.fn();
    s.onLost(lost);
    jest.useFakeTimers();
    sock.drop();
    for (const wait of [300, 1000, 2500]) {
      await jest.advanceTimersByTimeAsync(wait);
      FakeSocket.last().fail();
      await jest.advanceTimersByTimeAsync(0);
    }
    expect(s.getSnapshot().stage).toBe('failed');
    expect(lost).toHaveBeenCalledTimes(1);
  });

  it('release closes cleanly (1000) and never reconnects', async () => {
    const { s, sock } = await connected([]);
    jest.useFakeTimers();
    s.release();
    expect(sock.closedWith?.code).toBe(1000);
    await jest.advanceTimersByTimeAsync(10_000);
    expect(FakeSocket.all).toHaveLength(1);
    expect(s.getSnapshot().stage).toBe('ended');
  });
});

describe('probeDirect', () => {
  it('connects, says hello, measures one round trip, closes', async () => {
    const p = probeDirect('kn');
    const sock = FakeSocket.last();
    sock.accept();
    expect(sock.messages()[0]).toEqual({ t: 'hello', lang: 'kn', lines: [] });
    sock.json({ t: 'ready', provider: 'gnani' });
    const ping = sock.messages().find((m) => m.t === 'ping')!;
    sock.json({ t: 'pong', at: ping.at });
    const r = await p;
    expect(r).toMatchObject({ ok: true, provider: 'gnani' });
    expect(sock.closedWith?.code).toBe(1000);
  });

  it('reports a refused connection', async () => {
    const p = probeDirect('kn');
    FakeSocket.last().drop(1006);
    await expect(p).resolves.toMatchObject({ ok: false });
  });
});
