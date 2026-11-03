import { afterEach, describe, expect, it, jest } from '@jest/globals';

import type { MotherRecord } from '@/data/store';

// Live Sakhi (voice/live.ts): the app <-> WebView page commands and the
// agent protocol. Includes the regression that hung up every phone call
// ("send" was mapped to end(), so the first hello closed the room).

jest.mock('../gateway', () => ({
  gateway: {
    liveSession: async () => ({ url: 'wss://lk.test', token: 'tok', room: 'room-1' }),
    liveConfig: async () => ({ available: true, url: 'wss://lk.test', agentReady: true }),
  },
}));
jest.mock('../mic', () => ({ liveAudioMode: async () => {} }));

// eslint-disable-next-line import/first
import { LiveSession, pageCall, pageScript, type LiveCmd } from '../live';

const mother = { id: 'lakshmi', name: 'Lakshmi D.', months: 7 } as MotherRecord;
const tick = () => new Promise<void>((r) => setTimeout(r, 0));
const sessions: LiveSession[] = [];

/** A session wired to a fake page: returns what the app asked the page to do. */
function wired() {
  const s = new LiveSession(mother, 'kn');
  sessions.push(s);
  const cmds: LiveCmd[] = [];
  s.attach((c) => cmds.push(c));
  const page = (m: Record<string, unknown>) => s.onPageMessage(JSON.stringify(m));
  page({ type: 'ready' });
  const sent = () => cmds.filter((c): c is Extract<LiveCmd, { cmd: 'send' }> => c.cmd === 'send').map((c) => c.msg);
  return { s, cmds, page, sent };
}

afterEach(() => {
  sessions.splice(0).forEach((s) => s.release());
  jest.useRealTimers();
});

describe('page commands', () => {
  it('REGRESSION: "send" is send(...), never end()', () => {
    expect(pageCall({ cmd: 'send', msg: { t: 'hello' } })).toBe('send({"t":"hello"})');
    expect(pageScript({ cmd: 'send', msg: { t: 'hello' } })).toContain('window.sakhi.send(');
    expect(pageScript({ cmd: 'send', msg: { t: 'hello' } })).not.toContain('sakhi.end(');
  });

  it('every command maps to its own page function', () => {
    expect(pageCall({ cmd: 'end' })).toBe('end()');
    expect(pageCall({ cmd: 'mute', on: true })).toBe('mute(true)');
    expect(pageCall({ cmd: 'prepare', url: 'wss://x' })).toBe('prepare("wss://x")');
    expect(pageCall({ cmd: 'resumeAudio' })).toBe('resumeAudio()');
    expect(pageCall({ cmd: 'start', opts: { url: 'u', token: 't' } })).toBe('start({"url":"u","token":"t"})');
  });

  it('Kannada text is passed as safe JSON inside the script', () => {
    const js = pageScript({ cmd: 'send', msg: { t: 'say', text: 'ತಾಯಿಯ "ಬಿಪಿ"' } });
    expect(js).toContain('\\"ಬಿಪಿ\\"');
    expect(js.endsWith('true;')).toBe(true);
  });
});

describe('LiveSession', () => {
  it('commands before the page is ready are queued, then flushed in order', () => {
    const s = new LiveSession(mother, 'kn');
    sessions.push(s);
    const cmds: LiveCmd[] = [];
    s.attach((c) => cmds.push(c));
    s.mute(true);
    expect(cmds).toHaveLength(0);
    s.onPageMessage(JSON.stringify({ type: 'ready' }));
    expect(cmds).toEqual([{ cmd: 'mute', on: true }]);
  });

  it('connect: room, agent joins, hello, ready -> live', async () => {
    const { s, page, sent, cmds } = wired();
    const p = s.connect(['greeting', 'Q1']);
    await tick();
    expect(cmds.some((c) => c.cmd === 'start')).toBe(true);
    page({ type: 'connected' });
    page({ type: 'agentJoined' });
    expect(sent()[0]).toEqual({ t: 'hello', lang: 'kn', lines: ['greeting', 'Q1'] });
    page({ type: 'agentMsg', msg: { t: 'ready', lines: 2 } });
    await p;
    expect(s.getSnapshot().stage).toBe('live');
  });

  it('REGRESSION: the room closing while joining fails Start at once (no 28 s wait)', async () => {
    const { s, page } = wired();
    const p = s.connect(['x']);
    await tick();
    page({ type: 'agentJoined' });
    page({ type: 'ended', reason: '1' });
    await expect(p).rejects.toThrow(/room closed/);
    expect(s.getSnapshot().stage).toBe('failed');
  });

  async function live() {
    const ctx = wired();
    const p = ctx.s.connect(['x']);
    await tick();
    ctx.page({ type: 'agentMsg', msg: { t: 'ready' } });
    await p;
    return ctx;
  }

  it('a turn: say + listen -> said -> speech -> heard', async () => {
    const { s, page, sent } = await live();
    const hooks = { onListening: jest.fn(), onSpeech: jest.fn() };
    const answer = s.ask('Q1', { hangoverMs: 800, noSpeechMs: 7000 }, hooks);
    const say = sent().find((m) => m.t === 'say')!;
    expect(say).toMatchObject({ text: 'Q1', listen: { hangoverMs: 800, noSpeechMs: 7000 } });
    page({ type: 'agentMsg', msg: { t: 'said', seq: say.seq } });
    expect(hooks.onListening).toHaveBeenCalled();
    page({ type: 'agentMsg', msg: { t: 'speech', seq: say.seq } });
    expect(hooks.onSpeech).toHaveBeenCalled();
    page({ type: 'agentMsg', msg: { t: 'heard', seq: say.seq, text: '7 ತಿಂಗಳು', ms: 600 } });
    await expect(answer).resolves.toEqual({ kind: 'text', text: '7 ತಿಂಗಳು', sttMs: 600 });
  });

  it('a stale reply (another seq) is dropped', async () => {
    const { s, page, sent } = await live();
    const answer = s.ask('Q2', { hangoverMs: 800, noSpeechMs: 7000 }, {});
    const seq = sent().find((m) => m.t === 'say')!.seq as number;
    let done = false;
    answer.then(() => (done = true));
    page({ type: 'agentMsg', msg: { t: 'heard', seq: seq + 5, text: 'wrong' } });
    await tick();
    expect(done).toBe(false);
    page({ type: 'agentMsg', msg: { t: 'heard', seq, text: 'right' } });
    await expect(answer).resolves.toMatchObject({ text: 'right' });
  });

  it('the room closing mid check-in tells the engine (it continues on the phone)', async () => {
    const { s, page } = await live();
    const lost = jest.fn();
    s.onLost(lost);
    const answer = s.ask('Q3', { hangoverMs: 800, noSpeechMs: 7000 }, {});
    page({ type: 'ended', reason: 'network' });
    await expect(answer).resolves.toMatchObject({ kind: 'lost' });
    expect(lost).toHaveBeenCalledTimes(1);
  });

  it('hanging up ourselves is not "lost"', async () => {
    const { s, page } = await live();
    const lost = jest.fn();
    s.onLost(lost);
    s.end();
    page({ type: 'ended', reason: '1' });
    expect(lost).not.toHaveBeenCalled();
    expect(s.getSnapshot().stage).toBe('ended');
  });

  it('junk from the page is ignored, never thrown', () => {
    const { s } = wired();
    expect(() => s.onPageMessage('not json')).not.toThrow();
    expect(() => s.onPageMessage(JSON.stringify({ no: 'type' }))).not.toThrow();
    expect(() => s.onPageMessage(JSON.stringify({ type: 'agentMsg', msg: null }))).not.toThrow();
  });
});
