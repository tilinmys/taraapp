import { vlog } from './log';
import { FRAME_SAMPLES, SAMPLE_RATE } from './vad';
import { audioContext, unlockAudio } from './web-audio';

// Audio-in on the web (Expo web, e.g. testing on a desktop): the browser
// microphone through an AudioWorklet, delivering the SAME 20 ms frames of
// 16 kHz PCM16 as the phones, so the VAD, the STT upload and the turn logic
// are identical.
//   getUserMedia   once per check-in, echo cancellation + noise suppression
//                  on (Sakhi's voice comes out of the same laptop)
//   worklet        runs on the audio thread: box-filter decimation from the
//                  context rate (44.1 / 48 kHz) to 16 kHz, cut into 320-sample
//                  frames, posted to the page (50 messages a second)
//   on / off       half-duplex like the phones: frames flow only while
//                  Sakhi listens; the stream stays open between turns (no
//                  permission prompt or warm-up per question)

export const micSupported =
  typeof navigator !== 'undefined' && !!navigator.mediaDevices?.getUserMedia && typeof window !== 'undefined' && typeof window.AudioWorkletNode !== 'undefined';
export const AFTER_STOP_MS = 0;
export { unlockAudio };

const CONSTRAINTS: MediaStreamConstraints = {
  audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
};

let stream: MediaStream | null = null;

async function openStream() {
  if (stream && stream.getAudioTracks().some((t) => t.readyState === 'live')) return stream;
  stream = await navigator.mediaDevices.getUserMedia(CONSTRAINTS);
  const track = stream.getAudioTracks()[0];
  vlog('mic', `browser mic: ${track?.label || 'default'}`);
  return stream;
}

export async function micPermission(): Promise<boolean> {
  try {
    await openStream();
    return true;
  } catch (e) {
    vlog('err', `browser mic refused: ${String((e as Error)?.message ?? e)}`);
    return false;
  }
}

/** The browser mixes playback and capture itself: nothing to switch. */
export async function playbackMode() {}
export async function liveAudioMode() {}

const WORKLET = `
class SakhiCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.on = false;
    this.ratio = sampleRate / ${SAMPLE_RATE};
    this.frame = new Int16Array(${FRAME_SAMPLES});
    this.fill = 0; this.acc = 0; this.n = 0; this.pos = 0;
    this.port.onmessage = (e) => { this.on = !!e.data.on; this.fill = 0; this.acc = 0; this.n = 0; this.pos = 0; };
  }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!this.on || !ch) return true;
    for (let i = 0; i < ch.length; i++) {
      // Box filter: average every input sample that falls in one output sample.
      this.acc += ch[i]; this.n++; this.pos += 1;
      if (this.pos >= this.ratio) {
        this.pos -= this.ratio;
        let v = this.acc / this.n; this.acc = 0; this.n = 0;
        v = v < -1 ? -1 : v > 1 ? 1 : v;
        this.frame[this.fill++] = v < 0 ? v * 32768 : v * 32767;
        if (this.fill === ${FRAME_SAMPLES}) {
          this.port.postMessage(this.frame, [this.frame.buffer]);
          this.frame = new Int16Array(${FRAME_SAMPLES});
          this.fill = 0;
        }
      }
    }
    return true;
  }
}
registerProcessor('sakhi-capture', SakhiCapture);
`;

let workletReady: Promise<void> | null = null;

function loadWorklet(ctx: AudioContext) {
  workletReady ??= (async () => {
    const url = URL.createObjectURL(new Blob([WORKLET], { type: 'application/javascript' }));
    try {
      await ctx.audioWorklet.addModule(url);
    } finally {
      URL.revokeObjectURL(url);
    }
  })();
  return workletReady;
}

export class Mic {
  stoppedAt = 0;
  private node: AudioWorkletNode | null = null;
  private src: MediaStreamAudioSourceNode | null = null;
  private sink: GainNode | null = null;
  private active = false;
  private first = true;

  constructor(private readonly onFrame: (pcm: Int16Array, offset: number) => void) {}

  get on() {
    return this.active;
  }

  async start() {
    if (this.active) return;
    const t = Date.now();
    const ctx = audioContext();
    if (ctx.state !== 'running') await ctx.resume();
    if (!this.node) {
      await loadWorklet(ctx);
      const s = await openStream();
      this.src = ctx.createMediaStreamSource(s);
      this.node = new AudioWorkletNode(ctx, 'sakhi-capture', { numberOfInputs: 1, numberOfOutputs: 1, channelCount: 1 });
      // A silent sink keeps the node pulled by the audio graph in every browser.
      this.sink = ctx.createGain();
      this.sink.gain.value = 0;
      this.node.port.onmessage = (e: MessageEvent<Int16Array>) => {
        if (this.first) {
          this.first = false;
          vlog('mic', `first frame (${ctx.sampleRate} Hz -> ${SAMPLE_RATE} Hz)`);
        }
        this.onFrame(e.data, 0);
      };
      this.src.connect(this.node);
      this.node.connect(this.sink);
      this.sink.connect(ctx.destination);
    }
    this.first = true;
    this.node.port.postMessage({ on: true });
    this.active = true;
    vlog('mic', `on ${Date.now() - t} ms`);
  }

  stop() {
    if (!this.active) return;
    this.node?.port.postMessage({ on: false });
    this.active = false;
    this.stoppedAt = Date.now();
    vlog('mic', 'off');
  }

  dispose() {
    this.stop();
    try {
      this.src?.disconnect();
      this.node?.disconnect();
      this.sink?.disconnect();
    } catch {
      // already disconnected
    }
    this.node = null;
    this.src = null;
    this.sink = null;
    stream?.getTracks().forEach((t) => t.stop());
    stream = null;
  }
}
