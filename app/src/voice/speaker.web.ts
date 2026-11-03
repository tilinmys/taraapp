import { Asset } from 'expo-asset';
import * as Speech from 'expo-speech';

import { LANGUAGES, type Lang } from '@/i18n/strings';

import { gateway } from './gateway';
import { short, vlog } from './log';
import { prerecorded } from './prerecorded';
import { TtsQueue, type TtsPriority } from './tts-queue';
import { audioContext, audioRunning } from './web-audio';

// Audio-out on the web (Expo web, e.g. testing on a desktop). Same voice as
// the phones: Gnani's Kaveri from the gateway, same priority queue
// (tts-queue.ts). The WAV bytes stay in memory (LRU, ~120 KB a line) and play
// through the page's AudioContext (web-audio.ts), which the Start tap
// unlocks. The browser's own voice is only a BACKUP, reported through
// onVoice('backup') and shown on screen.

const MAX_LINES = 80;
const NET_WAIT_MS = 6000;

export type VoiceSource = 'sakhi' | 'backup';

export class Speaker {
  private bytes = new Map<string, ArrayBuffer>();
  private queue: TtsQueue<ArrayBuffer>;
  private source: AudioBufferSourceNode | null = null;
  private done: (() => void) | null = null;
  private gen = 0;
  private released = false;
  private tag: string;

  constructor(
    private readonly lang: Lang,
    private readonly useGateway: boolean,
    private readonly onVoice: (v: VoiceSource) => void = () => {},
  ) {
    this.tag = LANGUAGES.find((l) => l.id === lang)?.speech ?? 'en-IN';
    this.queue = new TtsQueue((text, prio) => this.download(text, prio));
  }

  async checkDeviceVoice() {
    const voices = typeof speechSynthesis === 'undefined' ? [] : speechSynthesis.getVoices();
    const has = voices.some((v) => v.lang.toLowerCase().startsWith(this.tag.slice(0, 2)));
    vlog('say', `backup (browser) voice for ${this.tag}: ${has ? 'yes' : 'no'}`);
  }

  /** Same interface as speaker.ts: every recorded line into memory, in speaking order. */
  async preloadRecorded(texts: string[]) {
    for (const t of texts) if (prerecorded(this.lang, t)) await this.recorded(t);
  }

  warm(texts: string[]) {
    texts.forEach((t) => (prerecorded(this.lang, t) ? this.recorded(t) : this.fetchAudio(t, 'warm')));
    vlog('tts', `warm-up: ${texts.length} lines, ${this.queue.pending} to download`);
  }

  prefetch(text: string) {
    return this.fetchAudio(text, 'next');
  }

  has(text: string) {
    return !!prerecorded(this.lang, text) || this.bytes.has(this.key(text));
  }

  /** A line recorded in the app: loaded once from the bundle, then kept in memory. */
  private async recorded(text: string): Promise<ArrayBuffer | null> {
    const pre = prerecorded(this.lang, text);
    if (!pre) return null;
    const k = this.key(text);
    const hit = this.bytes.get(k);
    if (hit) return hit;
    try {
      const asset = Asset.fromModule(pre.asset);
      const res = await fetch(asset.uri);
      const buf = await res.arrayBuffer();
      this.bytes.set(k, buf);
      return buf;
    } catch (e) {
      vlog('err', `recorded line not loaded: ${String((e as Error)?.message ?? e)}`);
      return null;
    }
  }

  /** Audio for a line that arrived another way (Sakhi Direct's WebSocket). */
  async put(text: string, bytes: Uint8Array) {
    this.bytes.set(this.key(text), bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer);
    while (this.bytes.size > MAX_LINES) this.bytes.delete(this.bytes.keys().next().value as string);
  }

  private key(text: string) {
    return `${this.lang}|${text}`;
  }

  private fetchAudio(text: string, prio: TtsPriority): Promise<ArrayBuffer | null> {
    if (!this.useGateway || gateway.offline || this.released) return Promise.resolve(null);
    const k = this.key(text);
    const hit = this.bytes.get(k);
    if (hit) {
      this.bytes.delete(k); // LRU: most recent last
      this.bytes.set(k, hit);
      return Promise.resolve(hit);
    }
    return this.queue.get(k, text, prio);
  }

  private async download(text: string, prio: TtsPriority): Promise<ArrayBuffer | null> {
    const t = Date.now();
    try {
      const buf = await gateway.tts(text, this.lang, prio);
      vlog('tts', `net ${Date.now() - t} ms [${prio}] ${buf.byteLength} B "${short(text)}"`);
      this.bytes.set(this.key(text), buf);
      while (this.bytes.size > MAX_LINES) this.bytes.delete(this.bytes.keys().next().value as string);
      return buf;
    } catch (e) {
      vlog('err', `tts download failed [${prio}] after ${Date.now() - t} ms: ${String((e as Error)?.message ?? e)} "${short(text)}"`);
      return null;
    }
  }

  async speak(text: string, onStart?: () => void): Promise<void> {
    this.stop();
    const my = this.gen;
    const t = Date.now();
    const cached = this.bytes.get(this.key(text)) ?? (await this.recorded(text));
    if (my !== this.gen) return;
    let buf: ArrayBuffer | null = cached;
    if (cached) vlog('tts', `${prerecorded(this.lang, text) ? 'recorded line' : 'hit (in memory)'} "${short(text)}"`);
    else if (this.useGateway && !gateway.offline) {
      buf = await Promise.race([this.fetchAudio(text, 'now'), new Promise<null>((r) => setTimeout(() => r(null), NET_WAIT_MS))]);
      if (my !== this.gen) return;
      vlog(buf ? 'tts' : 'err', buf ? `ready after ${Date.now() - t} ms` : `no Sakhi audio after ${Date.now() - t} ms`);
    }
    if (buf) {
      if (await audioRunning()) {
        try {
          await this.play(buf, text, my, onStart);
          return;
        } catch (e) {
          vlog('err', `play failed: ${String((e as Error)?.message ?? e)}`);
        }
      } else {
        vlog('err', 'web audio is locked (the page was not tapped): cannot play Sakhi voice');
      }
      if (my !== this.gen) return;
    }
    await this.device(text, onStart);
  }

  private async play(bytes: ArrayBuffer, text: string, my: number, onStart?: () => void) {
    const c = audioContext();
    // decodeAudioData detaches its input: decode a copy, keep the cached bytes.
    const audio = await c.decodeAudioData(bytes.slice(0));
    if (my !== this.gen) return;
    await new Promise<void>((resolve) => {
      const src = c.createBufferSource();
      src.buffer = audio;
      src.connect(c.destination);
      const t0 = Date.now();
      let settled = false;
      const settle = (why: string | null) => {
        if (settled) return;
        settled = true;
        clearTimeout(safety);
        if (this.source === src) this.source = null;
        if (this.done === stopped) this.done = null;
        if (why) vlog('play', `${why} ${((Date.now() - t0) / 1000).toFixed(1)} s "${short(text, 24)}"`);
        resolve();
      };
      const stopped = () => settle(null);
      src.onended = () => settle('done');
      const safety = setTimeout(() => settle('safety (no end event)'), audio.duration * 1000 + 1500);
      this.source = src;
      this.done = stopped;
      src.start();
      vlog('play', `SAKHI VOICE start (${audio.duration.toFixed(1)} s, ${audio.sampleRate} Hz) "${short(text, 24)}"`);
      this.onVoice('sakhi');
      onStart?.();
    });
  }

  private device(text: string, onStart?: () => void) {
    vlog('err', `BACKUP VOICE (browser ${this.tag}) "${short(text)}"`);
    this.onVoice('backup');
    return new Promise<void>((resolve) => {
      // The browser may never fire its events (no voice / autoplay rules).
      const cap = setTimeout(() => finish(), 2400 + text.length * 60);
      const finish = () => {
        clearTimeout(cap);
        if (this.done === finish) this.done = null;
        resolve();
      };
      this.done = finish;
      onStart?.();
      Speech.speak(text, { language: this.tag, rate: 0.92, onDone: finish, onStopped: finish, onError: finish });
    });
  }

  stop() {
    this.gen++;
    const done = this.done;
    this.done = null;
    try {
      this.source?.stop();
    } catch {
      // not started / already ended
    }
    this.source = null;
    Speech.stop().catch(() => {});
    done?.();
  }

  release() {
    this.stop();
    this.released = true;
    this.queue.close();
    this.bytes.clear();
  }
}
