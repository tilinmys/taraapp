import { beforeEach, describe, expect, it, jest } from '@jest/globals';

import { store } from '@/data/store';
import { STRINGS } from '@/i18n/strings';

import { voiceDb } from '../db';
import { claimSession, flushOfflineOutbox, releaseSession } from '../outbox-worker';

// The global outbox: an answer recorded with no network is transcribed when
// the phone is back online, its audio deleted, and it lands in her record
// (draft or filed report) through the same NLU and rules as a live answer.

// The in-memory store (the web one) stands in for SQLite (jest.mock is hoisted above the imports).
jest.mock('../db', () => jest.requireActual('../db.ts'));
jest.mock('../gateway', () => ({
  gateway: { offline: false, configured: true, stt: jest.fn() },
}));
jest.mock('../answer-files', () => ({ answerBody: jest.fn(() => ({ wav: true })), deleteAnswer: jest.fn() }));

type SttMock = jest.Mock<(...a: unknown[]) => Promise<{ text: string; en: string; ms: number }>>;
const gw = (jest.requireMock('../gateway') as { gateway: { offline: boolean; stt: SttMock } }).gateway;
const files = jest.requireMock('../answer-files') as { answerBody: jest.Mock<(u: string) => unknown>; deleteAnswer: jest.Mock };

const MOTHER = 'kavitha';
const QUESTION = 'ತಲೆನೋವು?';
let n = 0;

/** A visit-2 check-in whose "warning" answer was recorded offline. */
async function offlineCheckIn(file: boolean) {
  const sid = `s-${++n}`;
  store.startCheckIn(MOTHER, 2);
  await voiceDb.createSession({ id: sid, mother_id: MOTHER, lang: 'kn', mode: 'live' });
  await voiceDb.upsertTurn({ session_id: sid, idx: 2, qid: 'warning', question: QUESTION, audio_uri: file ? `file://${sid}.wav` : null, stt_status: 'offline', ended_speech_at: Date.now() });
  store.saveDraftTranscript(MOTHER, sid, [
    { from: 'sakhi', text: QUESTION },
    { from: 'asha', text: STRINGS.kn.vSavedOffline },
  ]);
  return sid;
}

const mother = () => store.get().find((m) => m.id === MOTHER)!;

describe('flushOfflineOutbox', () => {
  beforeEach(async () => {
    // The memory db lives for the whole file: settle what earlier tests left owed.
    for (const t of await voiceDb.outboxAll()) await voiceDb.updateTurn(t.session_id, t.idx, { stt_status: 'skipped', audio_uri: null });
    store.resetDemo();
    gw.offline = false;
    gw.stt.mockReset();
    files.answerBody.mockImplementation(() => ({ wav: true }));
  });

  it('a filed report gets the late answer: rules re-run, audio deleted, turn done', async () => {
    const sid = await offlineCheckIn(true);
    store.completeCheckIn(MOTHER, sid);
    expect(mother().reports[0].answers.warning).toBeUndefined();
    gw.stt.mockResolvedValue({ text: '2 ದಿನದಿಂದ ತುಂಬಾ ತಲೆನೋವಿದೆ', en: '', ms: 300 });

    expect(await flushOfflineOutbox()).toBe(1);

    expect(gw.stt).toHaveBeenCalledWith({ wav: true }, 'kn');
    const r = mother().reports[0];
    expect(r.answers.warning).toEqual({ value: 'Headache', danger: true, signs: ['headache'] });
    expect(r.high).toBe(true);
    expect(mother().answers.warning?.danger).toBe(true); // her latest report: her current answers too
    expect(r.transcript[1]).toMatchObject({ from: 'asha', text: '2 ದಿನದಿಂದ ತುಂಬಾ ತಲೆನೋವಿದೆ', sub: 'Headache', danger: true });
    expect(files.deleteAnswer).toHaveBeenCalledWith(`file://${sid}.wav`); // DPDP: gone once written
    expect((await voiceDb.turns(sid))[0]).toMatchObject({ stt_status: 'done', audio_uri: null, transcript: '2 ದಿನದಿಂದ ತುಂಬಾ ತಲೆನೋವಿದೆ' });
    expect(await voiceDb.outboxAll()).toHaveLength(0);
  });

  it('while the check-in is still open it fills the draft', async () => {
    await offlineCheckIn(true);
    gw.stt.mockResolvedValue({ text: 'ಇಲ್ಲ ಏನೂ ತೊಂದರೆ ಇಲ್ಲ', en: '', ms: 300 });
    expect(await flushOfflineOutbox()).toBe(1);
    expect(mother().draft?.warning).toEqual({ value: 'No', danger: false });
  });

  it('never overwrites what the ASHA corrected', async () => {
    const sid = await offlineCheckIn(true);
    store.recordAnswer(MOTHER, 'warning', { value: 'No', danger: false, corrected: { by: 'sunita', at: 1 } });
    store.completeCheckIn(MOTHER, sid);
    gw.stt.mockResolvedValue({ text: 'ತಲೆನೋವಿದೆ', en: '', ms: 300 });
    await flushOfflineOutbox();
    expect(mother().reports[0].answers.warning).toMatchObject({ value: 'No', corrected: { by: 'sunita' } });
  });

  it('offline: sends nothing, keeps everything', async () => {
    await offlineCheckIn(true);
    gw.offline = true;
    expect(await flushOfflineOutbox()).toBe(0);
    expect(gw.stt).not.toHaveBeenCalled();
    expect(await voiceDb.outboxAll()).toHaveLength(1);
  });

  it('a network failure keeps the answer owed for the next flush', async () => {
    await offlineCheckIn(true);
    gw.stt.mockRejectedValue(new Error('network'));
    expect(await flushOfflineOutbox()).toBe(0);
    expect(await voiceDb.outboxAll()).toHaveLength(1);
    expect(files.deleteAnswer).not.toHaveBeenCalled();
  });

  it('leaves a check-in the engine is still running alone', async () => {
    const sid = await offlineCheckIn(true);
    claimSession(sid);
    expect(await flushOfflineOutbox()).toBe(0);
    releaseSession(sid);
    gw.stt.mockResolvedValue({ text: 'ಇಲ್ಲ', en: '', ms: 300 });
    expect(await flushOfflineOutbox()).toBe(1);
  });

  it('audio already deleted (exited check-in): marked skipped, never sent', async () => {
    const sid = await offlineCheckIn(true);
    files.answerBody.mockImplementation(() => null);
    expect(await flushOfflineOutbox()).toBe(0);
    expect(gw.stt).not.toHaveBeenCalled();
    expect((await voiceDb.turns(sid))[0]).toMatchObject({ stt_status: 'skipped', audio_uri: null });
  });

  it('diagnostics count owed answers and timings only', async () => {
    await offlineCheckIn(true);
    const d = await voiceDb.getDiagnostics();
    expect(d.owedOutbox).toBe(1);
    expect(Object.keys(d).sort()).toEqual(['avgLatencyMs', 'completedTurns', 'owedOutbox', 'totalSessions']);
  });
});
