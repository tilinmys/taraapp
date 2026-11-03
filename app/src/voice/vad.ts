// Endpointing: an adaptive energy VAD over 20 ms frames of 16 kHz PCM16.
// Decides, with no network, when the ASHA has finished answering, so Sakhi
// can ask the next question (docs/VOICE_IN_APP_DESIGN.md 4.2).
//
//   noise floor  follows the quiet frames (EMA), clamped to [-75, -35] dBFS
//   start        3 frames in a row above floor + 10 dB (and above -55 dBFS)
//   stay         above floor + 6 dB (hysteresis: harder to start than stay)
//   end          quiet for hangover = base + min(300, 0.08 * speech ms)
//   no answer    nothing in noSpeechMs -> 'noSpeech'
//   cap          maxSpeechMs of speech -> 'end'
// O(1) per frame and no allocation per frame.

export const SAMPLE_RATE = 16000;
export const FRAME_MS = 20;
export const FRAME_SAMPLES = (SAMPLE_RATE * FRAME_MS) / 1000; // 320

export type VadEvent = 'none' | 'start' | 'end' | 'noSpeech';

export type VadOptions = {
  /** Base silence (ms) that ends a turn: shorter for yes/no, longer for descriptions. */
  hangoverMs: number;
  noSpeechMs?: number;
  maxSpeechMs?: number;
};

const FLOOR_MIN = -75;
const FLOOR_MAX = -35;
const START_DB = 10;
const STAY_DB = 6;
const START_FRAMES = 3;
const ABS_MIN_DB = -55;
const CALIBRATE_FRAMES = 10; // 200 ms
/** Exact zeros (a mic that is still starting): not the room, never taught to the floor. */
const DIGITAL_SILENCE_DB = -90;

/** dBFS of `n` samples of `pcm` from `offset` (int16). Silence -> -100. */
export function frameDb(pcm: Int16Array, offset: number, n: number) {
  let sum = 0;
  for (let i = offset, end = offset + n; i < end; i++) {
    const s = pcm[i];
    sum += s * s;
  }
  const rms = Math.sqrt(sum / n) / 32768;
  return rms > 0 ? 20 * Math.log10(rms) : -100;
}

export class Endpointer {
  private opts: Required<VadOptions> = { hangoverMs: 700, noSpeechMs: 7000, maxSpeechMs: 20000 };
  private floor = -60;
  private calib: number[] = [];
  private above = 0;
  private speaking = false;
  private speechMs = 0;
  private quietMs = 0;
  private waitedMs = 0;
  /** 0..1, for the orb (smoothed). */
  level = 0;
  /** Loudest frame since reset (dBFS), for diagnostics. */
  peakDb = -100;

  reset(opts: VadOptions) {
    this.opts = { noSpeechMs: 7000, maxSpeechMs: 20000, ...opts };
    this.above = 0;
    this.speaking = false;
    this.speechMs = 0;
    this.quietMs = 0;
    this.waitedMs = 0;
    this.peakDb = -100;
    // The noise floor carries over between questions (same room); it is
    // re-calibrated only on the very first turn.
  }

  get isSpeaking() {
    return this.speaking;
  }

  get speechDurationMs() {
    return this.speechMs;
  }

  get floorDb() {
    return this.floor;
  }

  /** Feed one 20 ms frame's level. Returns what happened. */
  push(db: number): VadEvent {
    if (db > this.peakDb) this.peakDb = db;
    if (db <= DIGITAL_SILENCE_DB && !this.speaking) {
      this.waitedMs += FRAME_MS;
      return this.waitedMs >= this.opts.noSpeechMs ? 'noSpeech' : 'none';
    }
    // Level for the UI: 0 at the floor, 1 at floor + 30 dB.
    const target = Math.max(0, Math.min(1, (db - this.floor) / 30));
    this.level += (target - this.level) * 0.35;

    if (this.calib.length < CALIBRATE_FRAMES) {
      this.calib.push(db);
      if (this.calib.length === CALIBRATE_FRAMES) {
        const sorted = [...this.calib].sort((a, b) => a - b);
        this.floor = clamp(sorted[Math.floor(sorted.length / 2)]);
      }
      this.waitedMs += FRAME_MS;
      return 'none';
    }

    if (!this.speaking) {
      this.waitedMs += FRAME_MS;
      if (db > this.floor + START_DB && db > ABS_MIN_DB) {
        this.above++;
        if (this.above >= START_FRAMES) {
          this.speaking = true;
          this.speechMs = this.above * FRAME_MS;
          this.quietMs = 0;
          return 'start';
        }
      } else {
        this.above = 0;
        // Only quiet frames teach the floor (slow EMA).
        this.floor = clamp(this.floor + 0.05 * (db - this.floor));
        if (this.waitedMs >= this.opts.noSpeechMs) return 'noSpeech';
      }
      return 'none';
    }

    this.speechMs += FRAME_MS;
    if (db > this.floor + STAY_DB) this.quietMs = 0;
    else this.quietMs += FRAME_MS;
    const hangover = this.opts.hangoverMs + Math.min(300, 0.08 * this.speechMs);
    if (this.quietMs >= hangover || this.speechMs >= this.opts.maxSpeechMs) {
      this.speaking = false;
      return 'end';
    }
    return 'none';
  }
}

const clamp = (v: number) => Math.max(FLOOR_MIN, Math.min(FLOOR_MAX, v));
