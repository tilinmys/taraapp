import { describe, expect, it } from '@jest/globals';

import { Endpointer, FRAME_SAMPLES, frameDb, type VadEvent } from '../vad';
import { Utterance } from '../utterance';

// The on-phone endpointer (used when Sakhi talks on the phone, not over a
// live connection) and the answer buffer it fills.

const ROOM = -62; // quiet room, dBFS
const VOICE = -28;

/** Feeds frames like the engine does: it stops at the first 'end' / 'noSpeech'. */
function run(vad: Endpointer, levels: number[]) {
  const events: { i: number; e: VadEvent }[] = [];
  for (let i = 0; i < levels.length; i++) {
    const e = vad.push(levels[i]);
    if (e === 'none') continue;
    events.push({ i, e });
    if (e === 'end' || e === 'noSpeech') break;
  }
  return events;
}
const frames = (db: number, n: number) => Array<number>(n).fill(db);

describe('frameDb', () => {
  it('silence is -100 dB; full scale is ~0 dB', () => {
    expect(frameDb(new Int16Array(320), 0, 320)).toBe(-100);
    expect(frameDb(new Int16Array(320).fill(32767), 0, 320)).toBeCloseTo(0, 0);
  });
});

describe('Endpointer', () => {
  it('learns the room, starts on voice, ends after the hangover', () => {
    const vad = new Endpointer();
    vad.reset({ hangoverMs: 600 });
    const ev = run(vad, [...frames(ROOM, 20), ...frames(VOICE, 40), ...frames(ROOM, 60)]);
    expect(ev.map((x) => x.e)).toEqual(['start', 'end']);
    expect(ev[0].i).toBe(22); // 3 loud frames in a row
    // hangover 600 ms + up to 300 ms extra for 0.8 s of speech
    const quietFrames = ev[1].i - 59;
    expect(quietFrames * 20).toBeGreaterThanOrEqual(600);
    expect(quietFrames * 20).toBeLessThanOrEqual(900);
  });

  it('a short pause inside one answer does not end it', () => {
    const vad = new Endpointer();
    vad.reset({ hangoverMs: 800 });
    const ev = run(vad, [...frames(ROOM, 20), ...frames(VOICE, 30), ...frames(ROOM, 20), ...frames(VOICE, 30), ...frames(ROOM, 80)]);
    expect(ev.map((x) => x.e)).toEqual(['start', 'end']);
  });

  it('a click (1-2 loud frames) is not speech', () => {
    const vad = new Endpointer();
    vad.reset({ hangoverMs: 600, noSpeechMs: 2000 });
    const ev = run(vad, [...frames(ROOM, 20), VOICE, VOICE, ...frames(ROOM, 100)]);
    expect(ev.map((x) => x.e)).toEqual(['noSpeech']);
  });

  it('exact digital silence (a mic still starting) is never taught as the room', () => {
    const vad = new Endpointer();
    vad.reset({ hangoverMs: 600 });
    run(vad, frames(-100, 50));
    expect(vad.floorDb).toBe(-60); // untouched default
    const ev = run(vad, [...frames(ROOM, 20), ...frames(VOICE, 10)]);
    expect(ev.map((x) => x.e)).toEqual(['start']);
  });

  it('caps a very long answer', () => {
    const vad = new Endpointer();
    vad.reset({ hangoverMs: 600, maxSpeechMs: 2000 });
    const ev = run(vad, [...frames(ROOM, 20), ...frames(VOICE, 200)]);
    expect(ev.map((x) => x.e)).toEqual(['start', 'end']);
    expect(ev[1].i - ev[0].i).toBeLessThanOrEqual(100);
  });
});

describe('Utterance', () => {
  const frame = (v: number) => new Int16Array(FRAME_SAMPLES).fill(v);

  it('keeps 300 ms of pre-roll so the first syllable is never cut', () => {
    const u = new Utterance();
    for (let i = 0; i < 30; i++) u.frame(frame(1), 0); // 600 ms before speech
    u.begin();
    u.frame(frame(2), 0);
    expect(u.durationMs).toBe(320); // 15 pre-roll frames + 1
  });

  it('writes a valid 16 kHz mono PCM16 WAV', () => {
    const u = new Utterance();
    u.begin();
    for (let i = 0; i < 50; i++) u.frame(frame(1000), 0);
    const wav = u.toWav();
    const v = new DataView(wav.buffer);
    const ascii = (o: number) => String.fromCharCode(...wav.slice(o, o + 4));
    expect(ascii(0)).toBe('RIFF');
    expect(ascii(8)).toBe('WAVE');
    expect(v.getUint32(24, true)).toBe(16000);
    expect(v.getUint16(22, true)).toBe(1);
    expect(v.getUint32(40, true)).toBe(50 * FRAME_SAMPLES * 2);
    expect(wav.byteLength).toBe(44 + 50 * FRAME_SAMPLES * 2);
  });

  it('never grows past 20 s (fixed buffer, no allocation per turn)', () => {
    const u = new Utterance();
    u.begin();
    for (let i = 0; i < 1200; i++) u.frame(frame(1), 0); // 24 s
    expect(u.durationMs).toBe(20000);
  });
});
