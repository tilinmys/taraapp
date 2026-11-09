import { afterEach, beforeEach, describe, expect, it, jest } from '@jest/globals';

import { QUESTION_BANK, questionById, questionsFor, questionText, SAKHI_REPLIES, type Visit } from '@/data/mockScreening';
import { store } from '@/data/store';
import { STRINGS } from '@/i18n/strings';

import type { RemoteAnswer } from '../live';

// The question engine (voice/engine.ts) is the app's state manager for a
// check-in. These tests drive it with a fake remote voice (the WebSocket /
// WebRTC agent) and a fake microphone, and check the flow itself:
//   * her visit decides the questionnaire (first / second / third)
//   * every question asked exactly once, in order, then done: no stall
//   * an urgent danger sign stops the questions until the ASHA acts
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
const REASK = STRINGS[LANG].vReprompt;
const DANGER = SAKHI_REPLIES[LANG].danger;
const URGENT = SAKHI_REPLIES[LANG].urgent;

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
const qText = (id: string) => questionText(questionById(id)!, LANG);
const idOf = (asked: string) => QUESTION_BANK.find((q) => questionText(q, LANG) === asked)?.id;
const Q2 = questionsFor(2).map((q) => questionText(q, LANG));

/** What a mother typically says to each question (Gnani-style transcripts). */
const TYPICAL: Record<string, string> = {
  name: 'ಕವಿತಾ', age: '24 ವರ್ಷ', village: 'ಕಮಠಾಣ', firstPregnancy: 'ಇಲ್ಲ', prevPregnancies: '1', lmp: 'ನೆನಪಿಲ್ಲ', regularPeriods: 'ಹೌದು',
  confirmed: 'ಹೌದು ಆಸ್ಪತ್ರೆಯಲ್ಲಿ', deliveries: '1', losses: 'ಇಲ್ಲ', pastProblems: 'ಇಲ್ಲ ಏನೂ ತೊಂದರೆ ಇಲ್ಲ', csection: 'ಇಲ್ಲ', conditions: 'ಇಲ್ಲ',
  longIllness: 'ಇಲ್ಲ', medicines: 'ಕಬ್ಬಿಣ ಮತ್ತು ಕ್ಯಾಲ್ಸಿಯಂ', allergy: 'ಇಲ್ಲ', transfusion: 'ಇಲ್ಲ', bleedDischarge: 'ಇಲ್ಲ', feverPain: 'ಇಲ್ಲ ಏನೂ ಇಲ್ಲ',
  tobacco: 'ಇಲ್ಲ', concerns: 'ಏನೂ ಇಲ್ಲ', newProblems: 'ಇಲ್ಲ', bleedFluid: 'ಇಲ್ಲ', warning: 'ಇಲ್ಲ ಏನೂ ಇಲ್ಲ', movementFelt: 'ಹೌದು ಗೊತ್ತಾಗುತ್ತಿದೆ',
  ifa: 'ಹೌದು ದಿನಾ ತಿನ್ನುತ್ತಿದ್ದೇನೆ', calcium: 'ಹೌದು', testsDone: 'ಹೌದು ಮಾಡಿಸಿದ್ದೇನೆ', followedAdvice: 'ಹೌದು', movement: 'ಹೌದು ಸರಿಯಾಗಿ ಆಡುತ್ತಿದೆ',
  movementLess: 'ಇಲ್ಲ', tummySize: 'ಇಲ್ಲ ಏನೂ ಬದಲಾವಣೆ ಇಲ್ಲ', bleedLeak: 'ಇಲ್ಲ', breathing: 'ಇಲ್ಲ', ironCalcium: 'ಹೌದು ದಿನಾ', deliveryPlace: 'ತಾಲೂಕು ಆಸ್ಪತ್ರೆ', support: 'ಹೌದು ಗಂಡ ಇದ್ದಾರೆ',
};
const typical = (asked: string) => {
  const id = idOf(asked);
  return text(id ? TYPICAL[id] : 'ಸರಿ');
};
let engines: SakhiEngine[] = [];

beforeEach(() => {
  store.resetDemo();
  engines = [];
  micMod.Mic.last = null;
});
afterEach(() => engines.forEach((e) => e.release()));

/** Kavitha (21 weeks, checked before) is a second visit; `visit` picks another questionnaire. */
function engineFor(id = 'kavitha', visit?: Visit) {
  const mother = store.get().find((m) => m.id === id)!;
  const e = new SakhiEngine(mother, LANG, { warm: false, visit });
  engines.push(e);
  return e;
}
const draftOf = (id = 'kavitha') => store.get().find((m) => m.id === id)!.draft!;

describe('SakhiEngine with a remote voice', () => {
  it('her visit decides the questionnaire: asks each question once, in order, then closes', async () => {
    const e = engineFor();
    expect(e.visit).toBe(2);
    const remote = new FakeRemote((t) => typical(t));
    await e.start(remote);
    await waitFor(() => e.getSnapshot().stage === 'done');
    // the closing line is said, not asked
    expect(remote.asked()).toEqual(questionsFor(2).filter((q) => q.id !== 'close').map((q) => questionText(q, LANG)));
    expect(remote.events[0]).toBe(`say:${GREETING}`);
    expect(remote.events).toContain(`say:${qText('close')}`);
    expect(remote.events).not.toContain(`say:${SAKHI_REPLIES[LANG].done}`); // one goodbye, not two
    expect(e.getSnapshot().answered).toBe(questionsFor(2).length);
    expect(e.getSnapshot().transport).toBe('websocket');
    expect(store.get().find((m) => m.id === 'kavitha')!.draftVisit).toBe(2);
  });

  it('a first visit asks the registration questions; a third visit the birth plan', async () => {
    const first = engineFor('kavitha', 1);
    const r1 = new FakeRemote((t) => typical(t));
    await first.start(r1);
    await waitFor(() => first.getSnapshot().stage === 'done');
    expect(r1.asked()).toContain(qText('lmp'));
    expect(r1.asked()).toContain(qText('transfusion'));
    expect(r1.asked()).not.toContain(qText('deliveryPlace'));
    const third = engineFor('meena', 3);
    const r3 = new FakeRemote((t) => typical(t));
    await third.start(r3);
    await waitFor(() => third.getSnapshot().stage === 'done');
    expect(r3.asked()).toContain(qText('deliveryPlace'));
    expect(r3.asked()).not.toContain(qText('movementLess')); // "moving as usual: yes" settled it
  });

  it('records what was understood in English for the doctor, with her words on screen', async () => {
    const e = engineFor();
    const remote = new FakeRemote((t) => typical(t));
    await e.start(remote);
    await waitFor(() => e.getSnapshot().stage === 'done');
    expect(draftOf().warning).toEqual({ value: 'No', danger: false });
    expect(draftOf().ifa.value).toBe('Yes, every day');
    const idx = questionsFor(2).findIndex((q) => q.id === 'ifa');
    const line = e.getSnapshot().lines.find((l) => l.from === 'asha' && l.idx === idx);
    expect(line?.text).toBe(TYPICAL.ifa);
    expect(line?.sub).toBe('Yes, every day');
  });

  it('an answer that fills later questions skips them (never a danger one)', async () => {
    const e = engineFor('kavitha', 1);
    const remote = new FakeRemote((t) => (t === qText('firstPregnancy') ? text('ಹೌದು ಇದು ಮೊದಲನೇ ಗರ್ಭ') : typical(t)));
    await e.start(remote);
    await waitFor(() => e.getSnapshot().stage === 'done');
    const asked = remote.asked();
    for (const id of ['prevPregnancies', 'deliveries', 'losses', 'pastProblems', 'csection']) expect([id, asked.includes(qText(id))]).toEqual([id, false]);
    expect(asked).toContain(qText('bleedDischarge')); // danger questions are always asked
    const note = STRINGS[LANG].vFromEarlier.split('{value}')[0];
    expect(e.getSnapshot().lines.some((l) => l.from === 'asha' && l.text.startsWith(note))).toBe(true);
  });

  it('an answer that does not fill the question: "sorry, once more" + the SAME question, once', async () => {
    const e = engineFor();
    const q = qText('testsDone');
    let tries = 0;
    const remote = new FakeRemote((t) => (t === q ? text(++tries === 1 ? 'ಏನು' : 'ಹೌದು') : typical(t)));
    await e.start(remote);
    await waitFor(() => e.getSnapshot().stage === 'done');
    const at = remote.events.indexOf(`ask:${q}`);
    expect(remote.events.slice(at, at + 3)).toEqual([`ask:${q}`, `say:${REASK_SORRY}`, `ask:${q}`]);
    expect(draftOf().testsDone.value).toBe('Yes, done');
  });

  it('still unclear after the second try: recorded as not clear, flagged (danger question), flow goes on', async () => {
    const e = engineFor();
    const q = qText('warning');
    const remote = new FakeRemote((t) => (t === q ? text('ಏನು') : typical(t)));
    await e.start(remote);
    await waitFor(() => e.getSnapshot().stage === 'done');
    expect(remote.asked().filter((x) => x === q)).toHaveLength(2);
    expect(draftOf().warning).toEqual({ value: 'Not clear: "ಏನು"', danger: true });
  });

  it('silence: ONE nudge ("say it once more") with the question again, then the next question', async () => {
    const e = engineFor();
    const remote = new FakeRemote((t, n) => (n === 2 || n === 3 ? { kind: 'silence' } : typical(t)));
    await e.start(remote);
    await waitFor(() => e.getSnapshot().stage === 'done');
    expect(remote.asked().slice(0, 4)).toEqual([Q2[0], Q2[1], Q2[1], Q2[2]]);
    const at = remote.events.indexOf(`ask:${Q2[1]}`);
    expect(remote.events.slice(at, at + 3)).toEqual([`ask:${Q2[1]}`, `say:${REASK}`, `ask:${Q2[1]}`]);
    expect(e.getSnapshot().held).toBe(false);
  });

  it('Next with nothing said moves on with no re-ask', async () => {
    const e = engineFor();
    const remote = new FakeRemote((t, n) => (n === 1 ? { kind: 'skip' } : typical(t)));
    await e.start(remote);
    await waitFor(() => e.getSnapshot().stage === 'done');
    expect(remote.asked().slice(0, 2)).toEqual([Q2[0], Q2[1]]);
  });

  it('a danger sign that is not urgent (fever) is flagged and announced ONCE, BETWEEN questions', async () => {
    const e = engineFor();
    const q = qText('feverPain');
    const next = qText('movementFelt');
    const remote = new FakeRemote((t) => (t === q ? text('ಮೂರು ದಿನದಿಂದ ಜ್ವರ ಇದೆ') : typical(t)));
    await e.start(remote);
    await waitFor(() => e.getSnapshot().stage === 'done');
    expect(e.getSnapshot().alerts.map((a) => [a.qid, a.code])).toContainEqual(['feverPain', 'IN-01']);
    const at = (ev: string) => remote.events.indexOf(ev);
    expect(at(`say:${DANGER}`)).toBeGreaterThan(at(`ask:${q}`));
    expect(at(`say:${DANGER}`)).toBeLessThan(at(`ask:${next}`));
    expect(remote.events.filter((x) => x === `say:${DANGER}`)).toHaveLength(1);
    expect(remote.events).not.toContain(`say:${URGENT}`);
    expect(draftOf().feverPain).toMatchObject({ danger: true, signs: ['fever'] });
  });

  it('an URGENT sign stops the questions until the ASHA acts; "referred" is recorded, then it goes on', async () => {
    const e = engineFor();
    const q = qText('warning');
    const remote = new FakeRemote((t) => (t === q ? text('ತುಂಬಾ ತಲೆನೋವು ಇದೆ') : typical(t)));
    await e.start(remote);
    await waitFor(() => !!e.getSnapshot().urgent);
    const u = e.getSnapshot().urgent!;
    expect(u).toMatchObject({ qid: 'warning', code: 'PE-03', signs: ['headache'] });
    expect(remote.events[remote.events.length - 1]).toBe(`say:${URGENT}`);
    const asked = remote.asked().length;
    await new Promise((r) => setTimeout(r, 50));
    expect(remote.asked()).toHaveLength(asked); // nothing asked while stopped
    e.repeat(); // Repeat does nothing while stopped
    expect(remote.asked()).toHaveLength(asked);
    e.acknowledgeUrgent('referred');
    await waitFor(() => e.getSnapshot().stage === 'done');
    expect(remote.asked()[asked]).toBe(qText('feverPain')); // the next question, not the urgent one again
    expect(store.get().find((m) => m.id === 'kavitha')!.referrals?.[0]).toMatchObject({ code: 'PE-03', signs: ['headache'], status: 'referred' });
    expect(e.getSnapshot().urgent).toBeNull();
  });

  it('the safety net: fits said in "new problems" stop the questions at once; a sign acted on never stops them twice', async () => {
    const e = engineFor();
    const remote = new FakeRemote((t) =>
      t === qText('newProblems') ? text('ನಿನ್ನೆ ರಾತ್ರಿ ಫಿಟ್ಸ್ ಬಂದಿತ್ತು') : typical(t),
    );
    await e.start(remote);
    await waitFor(() => !!e.getSnapshot().urgent);
    expect(e.getSnapshot().urgent).toMatchObject({ qid: 'newProblems', code: 'EC-01', signs: ['fits'] });
    expect(remote.asked()).toEqual([qText('newProblems')]); // stopped before the second question
    e.acknowledgeUrgent('noted');
    await waitFor(() => e.getSnapshot().stage === 'done');
    expect(remote.events.filter((x) => x === `say:${URGENT}`)).toHaveLength(1);
    expect(store.get().find((m) => m.id === 'kavitha')!.referrals?.[0].status).toBe('noted');
  });

  it('third visit: "no" to "moving as usual?" (34 weeks) is urgent (FM-01)', async () => {
    const e = engineFor('meena', 3);
    const remote = new FakeRemote((t) => (t === qText('movement') ? text('ಇಲ್ಲ') : typical(t)));
    await e.start(remote);
    await waitFor(() => !!e.getSnapshot().urgent);
    expect(e.getSnapshot().urgent).toMatchObject({ code: 'FM-01', signs: ['reducedMovement'] });
    e.acknowledgeUrgent('referred');
    await waitFor(() => e.getSnapshot().stage === 'done');
    expect(remote.asked()).not.toContain(qText('movementLess'));
  });

  it('an understood answer gets a short spoken "noted" before the next question (not before the closing line or a danger line)', async () => {
    const e = engineFor();
    const OK = SAKHI_REPLIES[LANG].ok;
    const remote = new FakeRemote((t) => (t === qText('feverPain') ? text('ಜ್ವರ ಇದೆ') : typical(t)));
    await e.start(remote);
    await waitFor(() => e.getSnapshot().stage === 'done');
    const ev = remote.events;
    const at = (x: string) => ev.indexOf(x);
    expect(ev[at(`ask:${Q2[0]}`) + 1]).toBe(`say:${OK}`); // newProblems: understood -> noted
    expect(ev[at(`ask:${qText('feverPain')}`) + 1]).toBe(`say:${DANGER}`); // a danger answer: the danger line, no "noted"
    expect(ev[ev.indexOf(`say:${qText('close')}`) - 1]).toBe(`ask:${qText('followedAdvice')}`); // straight to the closing line
  });

  it('a lost connection continues on the phone at the SAME question, without asking it twice', async () => {
    const e = engineFor();
    const remote = new FakeRemote((t, n) => (n === 3 ? { kind: 'lost', why: 'mic', said: true } : typical(t)));
    await e.start(remote);
    await waitFor(() => e.getSnapshot().transport === 'phone' && e.getSnapshot().stage === 'listening');
    const s = e.getSnapshot();
    expect(s.liveLost).toBe(true);
    const third = remote.asked()[2];
    expect(Q2[s.idx]).toBe(third); // still that question
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
    expect(remote.asked().slice(0, 2)).toEqual([Q2[0], Q2[0]]);
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
    gatewayMod.gateway.stt.mockResolvedValue({ text: 'ಇಲ್ಲ', en: '', ms: 300 });
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

  it('a late transcript (after 3.5 s) is still waited for: her answer is understood before the next question', async () => {
    answerFiles.saveAnswer.mockImplementation(() => ({ uri: 'file://late.wav', ready: Promise.resolve(true) }));
    gatewayMod.gateway.stt.mockReset();
    gatewayMod.gateway.stt.mockImplementation(() => new Promise((r) => setTimeout(() => r({ text: 'ಇಲ್ಲ', en: '', ms: 4500 }), 4500)));
    const e = engineFor();
    await e.start();
    await waitFor(() => e.getSnapshot().stage === 'listening' && !!micMod.Mic.last?.on);
    const mic = micMod.Mic.last!;
    for (let i = 0; i < 15; i++) mic.cb(frame(26), 0);
    for (let i = 0; i < 40; i++) mic.cb(frame(1300), 0);
    for (let i = 0; i < 70; i++) mic.cb(frame(26), 0);
    await waitFor(() => e.getSnapshot().processing);
    await new Promise((r) => setTimeout(r, 4000));
    expect(e.getSnapshot().idx).toBe(0); // still on the first question at 4 s
    await waitFor(() => !!store.get().find((m) => m.id === 'kavitha')!.draft?.newProblems, 4000);
    expect(store.get().find((m) => m.id === 'kavitha')!.draft?.newProblems.value).toBe('No');
  }, 15000);

  it('DPDP: Exit deletes every answer recording still waiting for its transcript', async () => {
    answerFiles.saveAnswer.mockImplementation(() => ({ uri: 'file://owed.wav', ready: new Promise<boolean>(() => {}) }));
    const del = (jest.requireMock('../answer-files') as { deleteAnswer: jest.Mock }).deleteAnswer;
    del.mockClear();
    const e = engineFor();
    await e.start();
    await waitFor(() => e.getSnapshot().stage === 'listening' && !!micMod.Mic.last?.on);
    const mic = micMod.Mic.last!;
    for (let i = 0; i < 15; i++) mic.cb(frame(26), 0);
    for (let i = 0; i < 40; i++) mic.cb(frame(1300), 0);
    for (let i = 0; i < 70; i++) mic.cb(frame(26), 0);
    await waitFor(() => answerFiles.saveAnswer.mock.calls.length >= 1);
    e.exit();
    expect(del).toHaveBeenCalledWith('file://owed.wav');
  });

  it('her voice level goes to onAudioLevel, never through the snapshot (no screen re-render per level)', async () => {
    answerFiles.saveAnswer.mockImplementation(() => ({ uri: 'file://a0.wav', ready: new Promise<boolean>(() => {}) }));
    const e = engineFor();
    const levels: number[] = [];
    e.onAudioLevel((l) => levels.push(l));
    await e.start();
    await waitFor(() => e.getSnapshot().stage === 'listening' && !!micMod.Mic.last?.on);
    const mic = micMod.Mic.last!;
    for (let i = 0; i < 15; i++) mic.cb(frame(26), 0);
    let renders = 0;
    const unsub = e.subscribe(() => renders++);
    for (let batch = 0; batch < 4; batch++) {
      for (let i = 0; i < 10; i++) mic.cb(frame(1300), 0);
      await new Promise((r) => setTimeout(r, 110)); // past the 100 ms level cadence
    }
    unsub();
    expect(levels.filter((l) => l > 0).length).toBeGreaterThanOrEqual(3);
    expect(renders).toBeLessThanOrEqual(1); // only listening -> hearing
    for (let i = 0; i < 70; i++) mic.cb(frame(26), 0); // end of answer: the ring rests
    await waitFor(() => e.getSnapshot().processing);
    expect(levels[levels.length - 1]).toBe(0);
  });

  it('back from a phone call: a fresh recorder (audio focus re-bound) and the same question again', async () => {
    const e = engineFor();
    await e.start();
    await waitFor(() => e.getSnapshot().stage === 'listening' && !!micMod.Mic.last?.on);
    const before = micMod.Mic.last!;
    const idx = e.getSnapshot().idx;
    const ctl = e as unknown as { pause(): void; resume(): void };
    ctl.pause(); // the call took the microphone
    expect(before.on).toBe(false);
    ctl.resume();
    await waitFor(() => e.getSnapshot().stage === 'listening' && !!micMod.Mic.last?.on);
    expect(micMod.Mic.last).not.toBe(before);
    expect(e.getSnapshot().idx).toBe(idx);
  });
});

// ---- the per-question state machine (voice/turn-policy.ts) on real turns --------

/** Script: per question asked, the answers in order; anything else answers typically. */
function scripted(plan: Record<string, string[]>) {
  const used: Record<string, number> = {};
  return (asked: string) => {
    const id = idOf(asked);
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
    const e = engineFor('kavitha', 1);
    const remote = new FakeRemote(scripted({ deliveries: ['ಹೌದು'], [S.vAskHowMany]: ['2'] }));
    await e.start(remote);
    await waitFor(() => e.getSnapshot().stage === 'done');
    const asked = remote.asked();
    expect(asked.filter((t) => t === qText('deliveries'))).toHaveLength(1); // never the whole question again
    expect(asked[asked.indexOf(qText('deliveries')) + 1]).toBe(S.vAskHowMany);
    expect(recordOf('deliveries')?.value).toBe('2 previous deliveries');
  });

  it('the snapshot says WHY a question is asked again (followUp / reask / nudge), null on a first ask', async () => {
    const e = engineFor('kavitha', 1);
    const seen: [string, string | null][] = [];
    const answer = scripted({ deliveries: ['ಹೌದು'], [S.vAskHowMany]: ['2'], losses: ['ಏನು', 'ಇಲ್ಲ'], allergy: ['', 'ಇಲ್ಲ'] });
    const remote = new FakeRemote((t) => {
      seen.push([t, e.getSnapshot().reason]);
      return answer(t);
    });
    await e.start(remote);
    await waitFor(() => e.getSnapshot().stage === 'done');
    const reasonsFor = (asked: string) => seen.filter(([t]) => t === asked).map(([, r]) => r);
    expect(reasonsFor(qText('deliveries'))).toEqual([null]);
    expect(reasonsFor(S.vAskHowMany)).toEqual(['followUp']);
    expect(reasonsFor(qText('losses'))).toEqual([null, 'reask']);
    expect(reasonsFor(qText('allergy'))).toEqual([null, 'nudge']);
    expect(e.getSnapshot().reason).toBeNull();
  });

  it('"no headache" is not "no" to vision and swelling: the rest is asked', async () => {
    const e = engineFor();
    const rest = 'ಮತ್ತು ಕಣ್ಣು ಮಂಜಾಗುವುದು ಅಥವಾ ಮುಖ ಕೈಗಳಲ್ಲಿ ಊತ ಇದೆಯಾ?';
    const remote = new FakeRemote(scripted({ warning: ['ತಲೆನೋವು ಇಲ್ಲ'], [rest]: ['ಇಲ್ಲ'] }));
    await e.start(remote);
    await waitFor(() => e.getSnapshot().stage === 'done');
    expect(remote.asked()).toContain(rest);
    expect(recordOf('warning')).toEqual({ value: 'No', danger: false });
  });

  it('a follow-up that is never answered is recorded as partly answered, flagged, and the flow goes on', async () => {
    const e = engineFor();
    const remote = new FakeRemote(scripted({ warning: ['ತಲೆನೋವು ಇಲ್ಲ', '', ''], 'ಮತ್ತು ಕಣ್ಣು ಮಂಜಾಗುವುದು ಅಥವಾ ಮುಖ ಕೈಗಳಲ್ಲಿ ಊತ ಇದೆಯಾ?': ['', ''] }));
    await e.start(remote);
    await waitFor(() => e.getSnapshot().stage === 'done');
    const r = recordOf('warning')!;
    expect(r.danger).toBe(true);
    expect(r.value).toContain('Not answered');
    expect(e.getSnapshot().answered).toBe(questionsFor(2).length);
  });

  it('"say it again" repeats the question without using up the re-ask', async () => {
    const e = engineFor();
    const remote = new FakeRemote(scripted({ testsDone: ['ಮತ್ತೊಮ್ಮೆ ಹೇಳಿ', 'ಹೌದು'] }));
    await e.start(remote);
    await waitFor(() => e.getSnapshot().stage === 'done');
    expect(remote.asked().filter((t) => t === qText('testsDone'))).toHaveLength(2);
    expect(remote.events).not.toContain(`say:${REASK_SORRY}`);
    expect(recordOf('testsDone')?.value).toBe('Yes, done');
  });

  it('"one minute" waits for her (longer), then takes the answer', async () => {
    const e = engineFor();
    const remote = new FakeRemote(scripted({ testsDone: ['ಒಂದು ನಿಮಿಷ'], [S.vWaiting]: ['ಹೌದು'] }));
    await e.start(remote);
    await waitFor(() => e.getSnapshot().stage === 'done');
    expect(remote.asked()).toContain(S.vWaiting);
    expect(recordOf('testsDone')?.value).toBe('Yes, done');
  });

  it('"skip" moves on at once and records it as skipped, with no alarm', async () => {
    const e = engineFor();
    const remote = new FakeRemote(scripted({ followedAdvice: ['ಮುಂದಿನ ಪ್ರಶ್ನೆ'] }));
    await e.start(remote);
    await waitFor(() => e.getSnapshot().stage === 'done');
    expect(remote.asked().filter((t) => t === qText('followedAdvice'))).toHaveLength(1);
    expect(recordOf('followedAdvice')).toEqual({ value: 'Skipped (check)', danger: false });
  });

  it('silence: one nudge, then recorded as no answer; never stuck', async () => {
    const e = engineFor();
    const remote = new FakeRemote(scripted({ followedAdvice: ['', ''] }));
    await e.start(remote);
    await waitFor(() => e.getSnapshot().stage === 'done');
    expect(remote.events.filter((t) => t === `say:${REASK}`)).toHaveLength(1);
    expect(remote.asked().filter((t) => t === qText('followedAdvice'))).toHaveLength(2);
    expect(e.getSnapshot().answered).toBe(questionsFor(2).length);
  });

  it('nobody speaks for two questions in a row: Sakhi says so once, holds, and asks again on Continue', async () => {
    const e = engineFor();
    const remote = new FakeRemote(scripted({ testsDone: ['', ''], followedAdvice: ['', ''] }));
    await e.start(remote);
    await waitFor(() => e.getSnapshot().held);
    const asked = remote.asked().length;
    expect(remote.events.filter((t) => t === `say:${S.vAway}`)).toHaveLength(1);
    expect(recordOf('followedAdvice')).toBeUndefined(); // not recorded as "no answer": it is asked again
    await new Promise((r) => setTimeout(r, 50));
    expect(remote.asked()).toHaveLength(asked); // nothing asked while held
    e.continueAfterHold();
    await waitFor(() => e.getSnapshot().stage === 'done');
    expect(remote.asked().filter((t) => t === qText('followedAdvice'))).toHaveLength(3);
    expect(recordOf('followedAdvice')?.value).toBeTruthy();
    expect(e.getSnapshot().held).toBe(false);
  });

  it('one silent question, then an answer: the silence count starts over (no hold)', async () => {
    const e = engineFor();
    const remote = new FakeRemote(scripted({ ifa: ['', ''], testsDone: ['', ''] }));
    await e.start(remote);
    await waitFor(() => e.getSnapshot().stage === 'done');
    expect(remote.events).not.toContain(`say:${S.vAway}`);
  });

  it('never more than five turns on one question, whatever she says', async () => {
    const e = engineFor();
    const remote = new FakeRemote(scripted({ warning: ['ಮತ್ತೊಮ್ಮೆ', 'ಮತ್ತೊಮ್ಮೆ', 'ಮತ್ತೊಮ್ಮೆ', 'ಏನು', 'ಏನು', 'ಏನು', 'ಏನು'] }));
    await e.start(remote);
    await waitFor(() => e.getSnapshot().stage === 'done');
    const turns = remote.asked().slice(remote.asked().indexOf(qText('warning')), remote.asked().indexOf(qText('feverPain')));
    expect(turns.length).toBeLessThanOrEqual(5);
    expect(recordOf('warning')?.danger).toBe(true); // an unclear danger question is flagged for the doctor
  });
});
