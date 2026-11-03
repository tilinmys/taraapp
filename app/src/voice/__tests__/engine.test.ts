import { afterEach, beforeEach, describe, expect, it, jest } from '@jest/globals';

import { ANTENATAL_QUESTIONS, questionText, SAKHI_REPLIES } from '@/data/mockScreening';
import { store } from '@/data/store';
import { STRINGS } from '@/i18n/strings';

import type { RemoteAnswer } from '../live';

// The question engine (voice/engine.ts) is the app's state manager for a
// check-in. These tests drive it with a fake remote voice (the WebSocket /
// WebRTC agent) and a fake microphone, and check the flow itself:
//   * every question asked exactly once, in order, then done: no stall
//   * silence -> one short re-ask, then on; Next with nothing said -> on
//   * a danger answer is flagged, and Sakhi says so BETWEEN questions
//   * a lost connection continues on the phone at the same question,
//     without asking it twice
//   * Repeat / Exit: a cancelled turn never comes back to life
//   * phone mode: an answer is uploaded only once its WAV is on disk

jest.mock('../speaker', () => {
  class Speaker {
    static last: Speaker;
    spoken: string[] = [];
    constructor() {
      Speaker.last = this;
    }
    checkDeviceVoice() {}
    async preloadRecorded() {}
    warm() {}
    prefetch() {
      return Promise.resolve(null);
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
jest.mock('../mic', () => {
  class Mic {
    static last: Mic | null = null;
    on = false;
    stoppedAt = 0;
    cb: (pcm: Int16Array, off: number) => void;
    constructor(fn: (pcm: Int16Array, off: number) => void) {
      this.cb = fn;
      Mic.last = this;
    }
    async start() {
      this.on = true;
    }
    stop() {
      this.on = false;
    }
    dispose() {
      this.on = false;
    }
  }
  return { Mic, micSupported: true, micPermission: async () => true, playbackMode: async () => {}, unlockAudio: () => {}, AFTER_STOP_MS: 0 };
});
jest.mock('../gateway', () => {
  class GatewayError extends Error {
    retryable: boolean;
    constructor(m: string, retryable: boolean) {
      super(m);
      this.retryable = retryable;
    }
  }
  return { GatewayError, gateway: { offline: false, health: async () => ({ stt: true, tts: true }), stt: jest.fn() } };
});
jest.mock('../db', () => ({
  voiceDb: { createSession: async () => {}, endSession: async () => {}, upsertTurn: async () => {}, updateTurn: async () => {}, logEvent: async () => {} },
}));
jest.mock('../answer-files', () => ({ saveAnswer: jest.fn(), answerBody: jest.fn(() => ({ wav: true })), deleteAnswer: jest.fn() }));

// eslint-disable-next-line import/first
import { SakhiEngine } from '../engine';

const answerFiles = jest.requireMock('../answer-files') as { saveAnswer: jest.Mock<(...a: unknown[]) => { uri: string; ready: Promise<boolean> }> };
const gatewayMod = jest.requireMock('../gateway') as { gateway: { stt: jest.Mock<(...a: unknown[]) => Promise<{ text: string; en: string; ms: number }>> } };
const micMod = jest.requireMock('../mic') as { Mic: { last: { cb: (p: Int16Array, o: number) => void; on: boolean } | null } };

const LANG = 'kn' as const;
const Q = ANTENATAL_QUESTIONS.map((q) => questionText(q, LANG));
const REASK = STRINGS[LANG].vReprompt;
const DANGER = SAKHI_REPLIES[LANG].danger;

async function waitFor(pred: () => boolean, ms = 5000) {
  const until = Date.now() + ms;
  while (!pred()) {
    if (Date.now() > until) throw new Error('timed out waiting');
    await new Promise((r) => setTimeout(r, 5));
  }
}

/** A remote voice that answers from a script. */
class FakeRemote {
  readonly transport = 'websocket' as const;
  events: string[] = []; // "say:..." / "ask:..." in order
  lost: ((why: string) => void) | null = null;
  stop = jest.fn();
  next = jest.fn();
  constructor(private readonly reply: (text: string, n: number) => RemoteAnswer) {}
  async connect() {}
  async say(text: string, onStart?: () => void) {
    this.events.push(`say:${text}`);
    onStart?.();
    return true;
  }
  async ask(text: string, _l: unknown, hooks: { onStart?: () => void; onListening?: () => void; onSpeech?: () => void }) {
    this.events.push(`ask:${text}`);
    hooks.onStart?.();
    hooks.onListening?.();
    await Promise.resolve();
    return this.reply(text, this.events.filter((e) => e.startsWith('ask:')).length);
  }
  onLevel() {}
  onLost(cb: ((why: string) => void) | null) {
    this.lost = cb;
  }
  asked() {
    return this.events.filter((e) => e.startsWith('ask:')).map((e) => e.slice(4));
  }
}

const text = (t: string): RemoteAnswer => ({ kind: 'text', text: t, sttMs: 300 });
const REASK_SORRY = STRINGS[LANG].vReask;
const GREETING = STRINGS[LANG].vGreetingVoice;
const qText = (id: string) => Q[ANTENATAL_QUESTIONS.findIndex((q) => q.id === id)];

/** What an ASHA typically says to each question (Gnani-style transcripts). */
const TYPICAL: Record<string, string> = {
  q01: 'ಕವಿತಾ ವಯಸ್ಸು 24', q02: '5 ತಿಂಗಳು', q03: 'ಗೊತ್ತಿಲ್ಲ', q04: 'ಎರಡನೇ ಗರ್ಭ ಒಂದು ಮಗು', q05: 'ಇಲ್ಲ ಏನೂ ತೊಂದರೆ ಇಲ್ಲ', q21: 'ನೈಸರ್ಗಿಕ',
  q06: 'ಬಿ ಪಿ 112 ಬೈ 72', q07: '53 ಕೆಜಿ', q08: 'ಹೌದು ದಿನಾ ತಿನ್ನುತ್ತಿದ್ದಾಳೆ', q24: 'ಇಲ್ಲ', q25: '140', q09: 'ಇಲ್ಲ ಏನೂ ಇಲ್ಲ', q10: 'ಇಲ್ಲ',
  q11: 'ಇಲ್ಲ', q12: 'ಹೌದು ಸರಿಯಾಗಿ ಗೊತ್ತಾಗುತ್ತಿದೆ', q18: 'ಇಲ್ಲ', q19: 'ಇಲ್ಲ', q20: 'ಕಬ್ಬಿಣ ಮತ್ತು ಕ್ಯಾಲ್ಸಿಯಂ, ಅಲರ್ಜಿ ಇಲ್ಲ', q13: 'ಹೌದು ತಾಲೂಕು ಆಸ್ಪತ್ರೆ',
  q14: 'ಎರಡೂ ಆಗಿದೆ', q26: 'ಹಿಮೋಗ್ಲೋಬಿನ್ 11.8, ಶುಗರ್ ನಾರ್ಮಲ್', q15: 'ಹೌದು', q16: 'ಹೌದು', q17: '',
};
const typical = (asked: string) => {
  const i = Q.indexOf(asked);
  return text(i >= 0 ? TYPICAL[ANTENATAL_QUESTIONS[i].id] : 'ಸರಿ');
};
let engines: SakhiEngine[] = [];

beforeEach(() => {
  store.resetDemo();
  engines = [];
  micMod.Mic.last = null;
});
afterEach(() => engines.forEach((e) => e.release()));

function engineFor(id = 'kavitha') {
  const mother = store.get().find((m) => m.id === id)!;
  const e = new SakhiEngine(mother, LANG, { warm: false });
  engines.push(e);
  return e;
}
const draftOf = () => store.get().find((m) => m.id === 'kavitha')!.draft!;

describe('SakhiEngine with a remote voice', () => {
  it('asks each needed question once, in order, then closes: no stall, no repeats', async () => {
    const e = engineFor();
    const remote = new FakeRemote((t) => typical(t));
    await e.start(remote);
    await waitFor(() => e.getSnapshot().stage === 'done');
    // q03 (period date) is not asked: the months were already said in q02;
    // q17 is the closing line: said, not asked.
    const expected = ANTENATAL_QUESTIONS.filter((q) => q.id !== 'q03' && q.id !== 'q17').map((q) => questionText(q, LANG));
    expect(remote.asked()).toEqual(expected);
    expect(remote.events[0]).toBe(`say:${GREETING}`);
    expect(remote.events).toContain(`say:${qText('q17')}`);
    expect(remote.events).not.toContain(`say:${SAKHI_REPLIES[LANG].done}`); // one goodbye, not two
    expect(e.getSnapshot().answered).toBe(24);
    expect(e.getSnapshot().transport).toBe('websocket');
  });

  it('records what was understood in English for the doctor, with her words on screen', async () => {
    const e = engineFor();
    const remote = new FakeRemote((t) => typical(t));
    await e.start(remote);
    await waitFor(() => e.getSnapshot().stage === 'done');
    expect(draftOf().q06).toEqual({ value: '112/72 mmHg', danger: false });
    expect(draftOf().q04.value).toBe('2nd pregnancy, 1 child before');
    expect(draftOf().q03.value).toBe('Not needed: months known');
    const idx = ANTENATAL_QUESTIONS.findIndex((q) => q.id === 'q06');
    const bpLine = e.getSnapshot().lines.find((l) => l.from === 'asha' && l.idx === idx);
    expect(bpLine?.text).toBe('ಬಿ ಪಿ 112 ಬೈ 72');
    expect(bpLine?.sub).toBe('112/72 mmHg');
  });

  it('an answer that fills later questions skips them (never a danger one)', async () => {
    const e = engineFor();
    const remote = new FakeRemote((t) => (t === Q[0] ? text('ಕವಿತಾ 24 ವರ್ಷ 5 ತಿಂಗಳು ಇದು ಮೊದಲನೇ ಗರ್ಭ') : typical(t)));
    await e.start(remote);
    await waitFor(() => e.getSnapshot().stage === 'done');
    const asked = remote.asked();
    expect(asked).not.toContain(qText('q02')); // months
    expect(asked).not.toContain(qText('q04')); // first pregnancy
    expect(asked).not.toContain(qText('q05')); // no past deliveries in a first pregnancy
    expect(asked).toContain(qText('q06')); // danger questions are always asked
    const note = STRINGS[LANG].vFromEarlier.split('{value}')[0];
    expect(e.getSnapshot().lines.some((l) => l.from === 'asha' && l.text.startsWith(note))).toBe(true);
  });

  it('an answer that does not fill the question: "sorry, once more" + the SAME question, once', async () => {
    const e = engineFor();
    const bp = qText('q06');
    let tries = 0;
    const remote = new FakeRemote((t) => (t === bp ? text(++tries === 1 ? 'ಏನು' : 'ಬಿ ಪಿ 118 ಬೈ 76') : typical(t)));
    await e.start(remote);
    await waitFor(() => e.getSnapshot().stage === 'done');
    const at = remote.events.indexOf(`ask:${bp}`);
    expect(remote.events.slice(at, at + 3)).toEqual([`ask:${bp}`, `say:${REASK_SORRY}`, `ask:${bp}`]);
    expect(draftOf().q06.value).toBe('118/76 mmHg');
  });

  it('still unclear after the second try: recorded as not clear, flagged (danger question), flow goes on', async () => {
    const e = engineFor();
    const bp = qText('q06');
    const remote = new FakeRemote((t) => (t === bp ? text('ಏನು') : typical(t)));
    await e.start(remote);
    await waitFor(() => e.getSnapshot().stage === 'done');
    expect(remote.asked().filter((x) => x === bp)).toHaveLength(2);
    expect(draftOf().q06).toEqual({ value: 'Not clear: "ಏನು"', danger: true });
  });

  it('silence: ONE nudge ("say it once more") with the question again, then the next question', async () => {
    const e = engineFor();
    const remote = new FakeRemote((t, n) => (n === 2 || n === 3 ? { kind: 'silence' } : typical(t)));
    await e.start(remote);
    await waitFor(() => e.getSnapshot().stage === 'done');
    expect(remote.asked().slice(0, 4)).toEqual([Q[0], Q[1], Q[1], Q[2]]);
    const at = remote.events.indexOf(`ask:${Q[1]}`);
    expect(remote.events.slice(at, at + 3)).toEqual([`ask:${Q[1]}`, `say:${REASK}`, `ask:${Q[1]}`]);
    expect(e.getSnapshot().held).toBe(false);
  });

  it('Next with nothing said moves on with no re-ask', async () => {
    const e = engineFor();
    const remote = new FakeRemote((t, n) => (n === 1 ? { kind: 'skip' } : typical(t)));
    await e.start(remote);
    await waitFor(() => e.getSnapshot().stage === 'done');
    expect(remote.asked().slice(0, 2)).toEqual([Q[0], Q[1]]);
  });

  it('a danger answer is flagged and announced ONCE, BETWEEN questions', async () => {
    const e = engineFor();
    const bp = qText('q06');
    const next = qText('q07');
    const remote = new FakeRemote((t) => (t === bp ? text('ಬಿ ಪಿ 160 ಬೈ 110') : typical(t)));
    await e.start(remote);
    await waitFor(() => e.getSnapshot().stage === 'done');
    expect(e.getSnapshot().alerts.map((a) => a.qid)).toContain('q06');
    const at = (ev: string) => remote.events.indexOf(ev);
    expect(at(`say:${DANGER}`)).toBeGreaterThan(at(`ask:${bp}`));
    expect(at(`say:${DANGER}`)).toBeLessThan(at(`ask:${next}`));
    expect(remote.events.filter((x) => x === `say:${DANGER}`)).toHaveLength(1);
    expect(draftOf().q06.danger).toBe(true);
  });

  it('a lost connection continues on the phone at the SAME question, without asking it twice', async () => {
    const e = engineFor();
    const remote = new FakeRemote((t, n) => (n === 3 ? { kind: 'lost', why: 'mic', said: true } : typical(t)));
    await e.start(remote);
    await waitFor(() => e.getSnapshot().transport === 'phone' && e.getSnapshot().stage === 'listening');
    const s = e.getSnapshot();
    expect(s.liveLost).toBe(true);
    const third = remote.asked()[2];
    expect(Q[s.idx]).toBe(third); // still that question
    const speaker = (jest.requireMock('../speaker') as { Speaker: { last: { spoken: string[] } } }).Speaker.last;
    expect(speaker.spoken).not.toContain(third); // not asked a second time
    expect(remote.lost).toBeNull(); // the engine let go of the remote
  });

  it('Repeat re-asks the current question; the cancelled turn never comes back', async () => {
    const e = engineFor();
    let release!: (a: RemoteAnswer) => void;
    const remote = new FakeRemote((t) => typical(t));
    remote.ask = async function (t, _l, hooks) {
      this.events.push(`ask:${t}`);
      hooks.onListening?.();
      if (this.asked().length === 1) return new Promise<RemoteAnswer>((r) => (release = r));
      return typical(t);
    };
    await e.start(remote);
    await waitFor(() => remote.asked().length === 1 && e.getSnapshot().stage === 'listening');
    e.repeat();
    expect(remote.stop).toHaveBeenCalled();
    await waitFor(() => remote.asked().length >= 2);
    release(text('LATE ANSWER')); // the first, cancelled turn answers late
    await waitFor(() => e.getSnapshot().stage === 'done');
    expect(remote.asked().slice(0, 2)).toEqual([Q[0], Q[0]]);
    expect(e.getSnapshot().lines.some((l) => l.text === 'LATE ANSWER')).toBe(false);
  });

  it('Exit stops everything and keeps the record unchanged', async () => {
    const e = engineFor();
    const before = store.get().find((m) => m.id === 'kavitha')!.answers;
    const remote = new FakeRemote((t) => typical(t));
    remote.ask = async function (t) {
      this.events.push(`ask:${t}`);
      return new Promise<RemoteAnswer>(() => {});
    };
    await e.start(remote);
    await waitFor(() => remote.asked().length === 1);
    e.exit();
    expect(e.getSnapshot().stage).toBe('exited');
    const m = store.get().find((x) => x.id === 'kavitha')!;
    expect(m.answers).toBe(before);
    expect(m.draft).toBeNull();
  });
});

describe('SakhiEngine on the phone (no remote)', () => {
  const frame = (amp: number) => new Int16Array(320).fill(amp);

  it('REGRESSION: an answer is uploaded only after its WAV is fully written', async () => {
    let written!: (ok: boolean) => void;
    answerFiles.saveAnswer.mockImplementation(() => ({ uri: 'file://a0.wav', ready: new Promise<boolean>((r) => (written = r)) }));
    gatewayMod.gateway.stt.mockResolvedValue({ text: 'ಲಕ್ಷ್ಮಿ', en: '', ms: 300 });
    const e = engineFor();
    await e.start();
    await waitFor(() => e.getSnapshot().stage === 'listening' && !!micMod.Mic.last?.on);
    const mic = micMod.Mic.last!;
    for (let i = 0; i < 15; i++) mic.cb(frame(26), 0); // the room (~ -62 dB)
    for (let i = 0; i < 40; i++) mic.cb(frame(1300), 0); // her answer (~ -28 dB)
    for (let i = 0; i < 70; i++) mic.cb(frame(26), 0); // quiet: end of answer
    await waitFor(() => answerFiles.saveAnswer.mock.calls.length === 1);
    await new Promise((r) => setTimeout(r, 30));
    expect(gatewayMod.gateway.stt).not.toHaveBeenCalled(); // still writing
    written(true);
    await waitFor(() => gatewayMod.gateway.stt.mock.calls.length === 1);
  });

  it('an answer that could not be saved is never uploaded', async () => {
    answerFiles.saveAnswer.mockImplementation(() => ({ uri: 'file://a0.wav', ready: Promise.resolve(false) }));
    gatewayMod.gateway.stt.mockReset();
    const e = engineFor();
    await e.start();
    await waitFor(() => e.getSnapshot().stage === 'listening' && !!micMod.Mic.last?.on);
    const mic = micMod.Mic.last!;
    for (let i = 0; i < 15; i++) mic.cb(frame(26), 0);
    for (let i = 0; i < 40; i++) mic.cb(frame(1300), 0);
    for (let i = 0; i < 70; i++) mic.cb(frame(26), 0);
    await waitFor(() => answerFiles.saveAnswer.mock.calls.length >= 1);
    await new Promise((r) => setTimeout(r, 50));
    expect(gatewayMod.gateway.stt).not.toHaveBeenCalled();
  });
});

// ---- the per-question state machine (voice/turn-policy.ts) on real turns --------

/** Script: per question asked, the answers in order; anything else answers typically. */
function scripted(plan: Record<string, string[]>) {
  const used: Record<string, number> = {};
  return (asked: string) => {
    const id = ANTENATAL_QUESTIONS[Q.indexOf(asked)]?.id;
    const key = id && plan[id] ? id : plan[asked] ? asked : null;
    if (key) {
      const n = (used[key] = (used[key] ?? 0) + 1);
      const answers = plan[key];
      if (n <= answers.length) return answers[n - 1] === '' ? ({ kind: 'silence' } as RemoteAnswer) : text(answers[n - 1]);
    }
    return typical(asked);
  };
}
const S = STRINGS[LANG];
const recordOf = (id: string) => store.get().find((m) => m.id === 'kavitha')!.draft?.[id];

describe('SakhiEngine: partial answers, commands and edge cases', () => {
  it('a half answer is followed up with ONLY the missing part, then moves on', async () => {
    const e = engineFor();
    const remote = new FakeRemote(scripted({ q01: ['ಕವಿತಾ'], [S.vAskAge]: ['24'] }));
    await e.start(remote);
    await waitFor(() => e.getSnapshot().stage === 'done');
    const asked = remote.asked();
    expect(asked.filter((t) => t === qText('q01'))).toHaveLength(1); // never the whole question again
    expect(asked[asked.indexOf(qText('q01')) + 1]).toBe(S.vAskAge);
    expect(recordOf('q01')?.value).toBe('ಕವಿತಾ, 24 years');
  });

  it('"no headache" is not "no" to vision and swelling: the rest is asked', async () => {
    const e = engineFor();
    const rest = 'ಮತ್ತು ಕಣ್ಣು ಮಂಜಾಗುವುದು ಅಥವಾ ಮುಖ ಕೈಗಳಲ್ಲಿ ಊತ ಇದೆಯಾ?';
    const remote = new FakeRemote(scripted({ q09: ['ತಲೆನೋವು ಇಲ್ಲ'], [rest]: ['ಇಲ್ಲ'] }));
    await e.start(remote);
    await waitFor(() => e.getSnapshot().stage === 'done');
    expect(remote.asked()).toContain(rest);
    expect(recordOf('q09')).toEqual({ value: 'No', danger: false });
  });

  it('a follow-up that is never answered is recorded as partly answered, flagged, and the flow goes on', async () => {
    const e = engineFor();
    const remote = new FakeRemote(scripted({ q09: ['ತಲೆನೋವು ಇಲ್ಲ', '', ''] , 'ಮತ್ತು ಕಣ್ಣು ಮಂಜಾಗುವುದು ಅಥವಾ ಮುಖ ಕೈಗಳಲ್ಲಿ ಊತ ಇದೆಯಾ?': ['', ''] }));
    await e.start(remote);
    await waitFor(() => e.getSnapshot().stage === 'done');
    const r = recordOf('q09')!;
    expect(r.danger).toBe(true);
    expect(r.value).toContain('Not answered');
    expect(e.getSnapshot().answered).toBe(24);
  });

  it('"say it again" repeats the question without using up the re-ask', async () => {
    const e = engineFor();
    const remote = new FakeRemote(scripted({ q07: ['ಮತ್ತೊಮ್ಮೆ ಹೇಳಿ', '53 ಕೆಜಿ'] }));
    await e.start(remote);
    await waitFor(() => e.getSnapshot().stage === 'done');
    expect(remote.asked().filter((t) => t === qText('q07'))).toHaveLength(2);
    expect(remote.events).not.toContain(`say:${REASK_SORRY}`);
    expect(recordOf('q07')?.value).toBe('53 kg');
  });

  it('"one minute" waits for her (longer), then takes the answer', async () => {
    const e = engineFor();
    const remote = new FakeRemote(scripted({ q07: ['ಒಂದು ನಿಮಿಷ'], [S.vWaiting]: ['55 ಕೆಜಿ'] }));
    await e.start(remote);
    await waitFor(() => e.getSnapshot().stage === 'done');
    expect(remote.asked()).toContain(S.vWaiting);
    expect(recordOf('q07')?.value).toBe('55 kg');
  });

  it('"skip" moves on at once and records it as skipped, with no alarm', async () => {
    const e = engineFor();
    const remote = new FakeRemote(scripted({ q19: ['ಮುಂದಿನ ಪ್ರಶ್ನೆ'] }));
    await e.start(remote);
    await waitFor(() => e.getSnapshot().stage === 'done');
    expect(remote.asked().filter((t) => t === qText('q19'))).toHaveLength(1);
    expect(recordOf('q19')).toEqual({ value: 'Skipped (check)', danger: false });
  });

  it('silence: one nudge, then recorded as no answer; never stuck', async () => {
    const e = engineFor();
    const remote = new FakeRemote(scripted({ q15: ['', ''] }));
    await e.start(remote);
    await waitFor(() => e.getSnapshot().stage === 'done');
    expect(remote.events.filter((t) => t === `say:${REASK}`)).toHaveLength(1);
    expect(remote.asked().filter((t) => t === qText('q15'))).toHaveLength(2);
    expect(e.getSnapshot().answered).toBe(24);
  });

  it('nobody speaks for two questions in a row: Sakhi says so once, holds, and asks again on Continue', async () => {
    const e = engineFor();
    const remote = new FakeRemote(scripted({ q15: ['', ''], q16: ['', ''] }));
    await e.start(remote);
    await waitFor(() => e.getSnapshot().held);
    const asked = remote.asked().length;
    expect(remote.events.filter((t) => t === `say:${S.vAway}`)).toHaveLength(1);
    expect(recordOf('q16')).toBeUndefined(); // not recorded as "no answer": it is asked again
    await new Promise((r) => setTimeout(r, 50));
    expect(remote.asked()).toHaveLength(asked); // nothing asked while held
    e.continueAfterHold();
    await waitFor(() => e.getSnapshot().stage === 'done');
    expect(remote.asked().filter((t) => t === qText('q16'))).toHaveLength(3);
    expect(recordOf('q16')?.value).toBeTruthy();
    expect(e.getSnapshot().held).toBe(false);
  });

  it('one silent question, then an answer: the silence count starts over (no hold)', async () => {
    const e = engineFor();
    const remote = new FakeRemote(scripted({ q15: ['', ''], q18: ['', ''] }));
    await e.start(remote);
    await waitFor(() => e.getSnapshot().stage === 'done');
    expect(remote.events).not.toContain(`say:${S.vAway}`);
  });

  it('never more than five turns on one question, whatever she says', async () => {
    const e = engineFor();
    const remote = new FakeRemote(scripted({ q06: ['ಮತ್ತೊಮ್ಮೆ', 'ಮತ್ತೊಮ್ಮೆ', 'ಮತ್ತೊಮ್ಮೆ', 'ಏನು', 'ಏನು', 'ಏನು', 'ಏನು'] }));
    await e.start(remote);
    await waitFor(() => e.getSnapshot().stage === 'done');
    const q06Turns = remote.asked().slice(remote.asked().indexOf(qText('q06')), remote.asked().indexOf(qText('q07')));
    expect(q06Turns.length).toBeLessThanOrEqual(5);
    expect(recordOf('q06')?.danger).toBe(true); // unclear BP is flagged for the doctor
  });
});
