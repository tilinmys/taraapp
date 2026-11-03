import { FRAME_SAMPLES, SAMPLE_RATE } from './vad';

// The answer being spoken, as PCM16 in ONE preallocated buffer (20 s at
// 16 kHz = 640 KB), reused for every turn. While nobody speaks, frames go
// to a 300 ms pre-roll ring; when speech starts the ring is copied in first
// so the first syllable is never cut. `toWav()` makes the upload body.

const MAX_SECONDS = 20;
const PREROLL_FRAMES = 15; // 300 ms

export class Utterance {
  private readonly buf = new Int16Array(SAMPLE_RATE * MAX_SECONDS);
  private readonly ring = new Int16Array(FRAME_SAMPLES * PREROLL_FRAMES);
  private ringHead = 0; // next frame slot
  private ringFilled = 0;
  private len = 0;
  private recording = false;

  reset() {
    this.len = 0;
    this.ringHead = 0;
    this.ringFilled = 0;
    this.recording = false;
  }

  /** Speech started: move the pre-roll in, then keep appending. */
  begin() {
    this.len = 0;
    const frames = this.ringFilled;
    for (let k = 0; k < frames; k++) {
      const slot = (this.ringHead - frames + k + PREROLL_FRAMES) % PREROLL_FRAMES;
      this.append(this.ring, slot * FRAME_SAMPLES, FRAME_SAMPLES);
    }
    this.recording = true;
  }

  /** End of turn: nothing more is appended until the next begin(). */
  end() {
    this.recording = false;
    this.ringHead = 0;
    this.ringFilled = 0;
  }

  /** One 20 ms frame (a view into the mic buffer; copied, never kept). */
  frame(pcm: Int16Array, offset: number) {
    if (this.recording) {
      this.append(pcm, offset, FRAME_SAMPLES);
      return;
    }
    this.ring.set(pcm.subarray(offset, offset + FRAME_SAMPLES), this.ringHead * FRAME_SAMPLES);
    this.ringHead = (this.ringHead + 1) % PREROLL_FRAMES;
    this.ringFilled = Math.min(PREROLL_FRAMES, this.ringFilled + 1);
  }

  get durationMs() {
    return Math.round((this.len / SAMPLE_RATE) * 1000);
  }

  private append(src: Int16Array, offset: number, n: number) {
    const room = this.buf.length - this.len;
    const take = Math.min(room, n);
    if (take <= 0) return;
    this.buf.set(src.subarray(offset, offset + take), this.len);
    this.len += take;
  }

  /** WAV (RIFF, PCM16 mono 16 kHz): 44-byte header + the samples. */
  toWav(): Uint8Array {
    const bytes = this.len * 2;
    const out = new Uint8Array(44 + bytes);
    const v = new DataView(out.buffer);
    const ascii = (o: number, s: string) => {
      for (let i = 0; i < s.length; i++) out[o + i] = s.charCodeAt(i);
    };
    ascii(0, 'RIFF');
    v.setUint32(4, 36 + bytes, true);
    ascii(8, 'WAVE');
    ascii(12, 'fmt ');
    v.setUint32(16, 16, true);
    v.setUint16(20, 1, true); // PCM
    v.setUint16(22, 1, true); // mono
    v.setUint32(24, SAMPLE_RATE, true);
    v.setUint32(28, SAMPLE_RATE * 2, true);
    v.setUint16(32, 2, true);
    v.setUint16(34, 16, true);
    ascii(36, 'data');
    v.setUint32(40, bytes, true);
    out.set(new Uint8Array(this.buf.buffer, this.buf.byteOffset, bytes), 44);
    return out;
  }
}
