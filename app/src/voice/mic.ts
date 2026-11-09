import { AudioModule, requestRecordingPermissionsAsync, setAudioModeAsync, setIsAudioActiveAsync } from 'expo-audio';
import type { AudioStream, AudioStreamBuffer } from 'expo-audio/build/AudioStream.types';
import { Platform } from 'react-native';

import { vlog } from './log';
import { FRAME_SAMPLES, SAMPLE_RATE } from './vad';

// Audio-in layer (phones): expo-audio's AudioStream, raw PCM16 from the
// microphone in real time (works in Expo Go).
//
// HALF-DUPLEX, on purpose. On iOS, AudioStream.start() switches the audio
// session to the RECORD-ONLY category, which mutes every sound the app makes
// (Sakhi's voice and the phone's own voice). So the mic is ON only while
// Sakhi listens, and before she speaks the session goes back to playback on
// the loudspeaker (playbackMode). Bonus: no echo of her own voice, and the
// mic is off most of the time (battery).
//
// Buffers arrive in any size and possibly at 44.1 / 48 kHz: they are
// resampled to 16 kHz into one reusable scratch array and cut into 20 ms
// frames. No allocation per buffer once warmed up.

export const micSupported = true;

/** Web needs the Start tap to unlock sound; phones do not. */
export function unlockAudio() {}

/** expo-audio / native audio HAL deactivates ~80-130 ms after a stream stops; speak after that. */
export const AFTER_STOP_MS = Platform.OS === 'ios' ? 130 : 80;

export async function micPermission(): Promise<boolean> {
  const res = await requestRecordingPermissionsAsync();
  return res.granted;
}

/** Sakhi is about to speak: playback category, loudspeaker, also in silent mode. */
export async function playbackMode() {
  const t = Date.now();
  await setAudioModeAsync({ playsInSilentMode: true, allowsRecording: false, shouldRouteThroughEarpiece: false, interruptionMode: 'doNotMix', shouldPlayInBackground: false });
  // iOS: the mic stream deactivated the session; switch it on explicitly so
  // the next play() does not wait on a slow route change (measured on an
  // iPhone: 0.8 s mode switch, then no sound within 2.5 s).
  if (Platform.OS === 'ios') await setIsAudioActiveAsync(true).catch(() => {});
  vlog('mode', `playback (speaker) ${Date.now() - t} ms`);
}

/**
 * Live Sakhi (WebRTC in a WebView): play AND record at once, on the
 * loudspeaker (iOS would otherwise route a call to the quiet earpiece).
 */
export async function liveAudioMode() {
  await setAudioModeAsync({ playsInSilentMode: true, allowsRecording: true, shouldRouteThroughEarpiece: false, interruptionMode: 'doNotMix', shouldPlayInBackground: false });
  vlog('mode', 'live: play + record, loudspeaker');
}

export class Mic {
  private stream: AudioStream | null = null;
  private sub: { remove: () => void } | null = null;
  private frame = new Int16Array(FRAME_SAMPLES);
  private fill = 0;
  private scratch = new Int16Array(4096);
  private firstBuffer = true;
  /** When the mic last stopped (see AFTER_STOP_MS). */
  stoppedAt = 0;

  constructor(private readonly onFrame: (pcm: Int16Array, offset: number) => void) {}

  get on() {
    return !!this.stream?.isStreaming;
  }

  /** Turn the mic on (one stream object, reused every turn). */
  async start() {
    if (this.on) return;
    const t = Date.now();
    if (!this.stream) {
      // AudioModule is the native module OBJECT (the import plugin mistakes it for a namespace).
      // eslint-disable-next-line import/namespace
      this.stream = new AudioModule.AudioStream({ sampleRate: SAMPLE_RATE, channels: 1, encoding: 'int16' });
      this.sub = this.stream.addListener('audioStreamBuffer', (b: AudioStreamBuffer) => this.feed(b));
    }
    this.fill = 0;
    this.firstBuffer = true;
    await this.stream.start();
    vlog('mic', `on ${Date.now() - t} ms`);
  }

  /** Turn the mic off (keeps the stream object for the next turn). */
  stop() {
    if (!this.on) return;
    try {
      this.stream?.stop();
    } catch {
      // already stopped
    }
    this.stoppedAt = Date.now();
    this.fill = 0;
    vlog('mic', 'off');
  }

  /** End of the check-in: free the native stream. */
  dispose() {
    this.stop();
    this.sub?.remove();
    this.sub = null;
    try {
      this.stream?.release();
    } catch {
      // already released
    }
    this.stream = null;
  }

  private feed(b: AudioStreamBuffer) {
    if (this.firstBuffer) {
      this.firstBuffer = false;
      vlog('mic', `first buffer ${b.data.byteLength} B, ${b.sampleRate} Hz, ${b.channels} ch`);
    }
    const raw = new Int16Array(b.data);
    const ch = Math.max(1, b.channels);
    const inLen = Math.floor(raw.length / ch);
    // Resample (linear) and take channel 0, into the reusable scratch.
    const ratio = b.sampleRate / SAMPLE_RATE;
    const outLen = ratio === 1 ? inLen : Math.floor(inLen / ratio);
    if (this.scratch.length < outLen) this.scratch = new Int16Array(outLen);
    if (ratio === 1 && ch === 1) {
      this.scratch.set(raw.subarray(0, outLen));
    } else {
      for (let i = 0; i < outLen; i++) {
        const pos = i * ratio;
        const j = Math.floor(pos);
        const a = raw[j * ch];
        const c = j + 1 < inLen ? raw[(j + 1) * ch] : a;
        this.scratch[i] = a + (c - a) * (pos - j);
      }
    }
    // Cut into 20 ms frames.
    let i = 0;
    while (i < outLen) {
      const take = Math.min(FRAME_SAMPLES - this.fill, outLen - i);
      this.frame.set(this.scratch.subarray(i, i + take), this.fill);
      this.fill += take;
      i += take;
      if (this.fill === FRAME_SAMPLES) {
        this.onFrame(this.frame, 0);
        this.fill = 0;
      }
    }
  }
}
