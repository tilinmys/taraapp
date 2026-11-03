import { Asset } from 'expo-asset';
import { createAudioPlayer, type AudioPlayer, type AudioStatus } from 'expo-audio';
import { Directory, File, Paths } from 'expo-file-system';
import * as Speech from 'expo-speech';
import { Platform } from 'react-native';

import { LANGUAGES, type Lang } from '@/i18n/strings';

import { gateway } from './gateway';
import { short, vlog } from './log';
import { prerecorded } from './prerecorded';
import { TtsQueue, type TtsPriority } from './tts-queue';

// Audio-out layer (phones). Sakhi's voice = Gnani's Kaveri (the same voice as
// the phone-call agent), from the gateway, with the least possible wait:
//   cache     cache/sakhi-tts/<hash>.wav, one file per (language, text);
//             LRU-capped at MAX_FILES (24 questions x 3 languages + prompts)
//   queue     tts-queue.ts: the provider takes one request at a time; the
//             line being said NOW skips the queue
//   play      ONE reused player; a watchdog falls back if sound has not
//             started within START_MS (never a silent 30 s wait)
//   backup    no file within NET_WAIT_MS, gateway down or playback error ->
//             the phone's own voice says the same words. This is reported
//             (onVoice('backup')) and shown on screen, never silent.
// speak() resolves when she has finished (or was stopped).

const MAX_FILES = 120;
/** How long a line may wait for Sakhi's own voice before the backup voice is used. */
const NET_WAIT_MS = 6000;
/**
 * How long the player may take to start before the backup voice is used.
 * iOS needs more after the mic was on (audio route change): 2.5 s was too
 * short on an iPhone and brought in the backup voice for a line that would
 * have played.
 */
const START_MS = Platform.OS === 'ios' ? 5000 : 3000;
const SAFETY_MS = 30_000;
/** How long a recorded line may take to reach the phone before the next-best audio is used. */
const ASSET_WAIT_MS = 4000;

/**
 * A recorded line as a file ON THE PHONE. In a release build it is inside
 * the app; in Expo Go it is served by Metro and, behind the tunnel, its
 * first play was a download that often missed the player's 5 s start
 * watchdog (logged live: "recorded line failed to play: no sound within
 * 5000 ms", 5 s lost per new question). Downloaded ahead (preloadRecorded)
 * and played from disk, it starts at once. expo-asset shares an in-flight
 * download, so the line said NOW never waits behind the queue.
 */
function localAsset(asset: number): Promise<string | null> {
  const a = Asset.fromModule(asset);
  if (a.downloaded && a.localUri) return Promise.resolve(a.localUri);
  return a.downloadAsync().then(
    (x) => x.localUri,
    () => null,
  );
}

export type VoiceSource = 'sakhi' | 'backup';

function hash(s: string) {
  // FNV-1a 32-bit: fast, stable file names.
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return (h >>> 0).toString(16).padStart(8, '0');
}

export class Speaker {
  private player: AudioPlayer | null = null;
  private playerUri: string | null = null;
  private dir = new Directory(Paths.cache, 'sakhi-tts');
  private queue: TtsQueue<File>;
  private done: (() => void) | null = null;
  /** Bumped by stop(): a speak() that was waiting for its file must not play late. */
  private gen = 0;
  private released = false;
  private tag: string;
  private hasDeviceVoice: boolean | null = null;

  constructor(
    private readonly lang: Lang,
    private readonly useGateway: boolean,
    private readonly onVoice: (v: VoiceSource) => void = () => {},
  ) {
    this.tag = LANGUAGES.find((l) => l.id === lang)?.speech ?? 'en-IN';
    if (!this.dir.exists) this.dir.create({ intermediates: true, idempotent: true });
    this.queue = new TtsQueue((text, prio) => this.download(text, prio));
  }

  private fileFor(text: string) {
    return new File(this.dir, `${this.lang}-${hash(text)}-${text.length}.wav`);
  }

  private onDisk(file: File) {
    return file.exists && (file.size ?? 0) > 44;
  }

  /** Does the phone have its own voice for this language? (Logged; iOS has no Kannada voice.) */
  async checkDeviceVoice() {
    try {
      const voices = await Speech.getAvailableVoicesAsync();
      const base = this.tag.slice(0, 2);
      this.hasDeviceVoice = voices.some((v) => v.language?.toLowerCase().startsWith(base));
      vlog('say', `backup (phone) voice for ${this.tag}: ${this.hasDeviceVoice ? 'yes' : 'NO'}`);
    } catch {
      this.hasDeviceVoice = null;
    }
  }

  /** Is this line's audio already on the phone (recorded in the app, or downloaded)? */
  has(text: string) {
    return !!prerecorded(this.lang, text) || this.onDisk(this.fileFor(text));
  }

  /**
   * Audio for a line that arrived another way (Sakhi Direct's WebSocket,
   * voice/direct.ts): stored like a download, so speak() plays it from disk.
   */
  async put(text: string, bytes: Uint8Array) {
    const file = this.fileFor(text);
    try {
      if (file.exists) file.delete();
      file.create();
      await file.write(bytes);
      this.prune();
    } catch (e) {
      vlog('err', `could not store line audio: ${String((e as Error)?.message ?? e)}`);
    }
  }

  /**
   * Every recorded line of the check-in onto the phone, in speaking order,
   * one at a time (the next question is always ready first). No network
   * provider involved: only the app's own files. Safe to call again.
   */
  async preloadRecorded(texts: string[]) {
    const t = Date.now();
    let n = 0;
    for (const text of texts) {
      if (this.released) return;
      const pre = prerecorded(this.lang, text);
      if (pre && (await localAsset(pre.asset))) n++;
    }
    vlog('tts', `recorded lines on the phone: ${n} in ${Date.now() - t} ms`);
  }

  /** Every line of the check-in, downloaded one by one in speaking order. */
  warm(texts: string[]) {
    texts.forEach((t) => !prerecorded(this.lang, t) && this.fetchAudio(t, 'warm'));
    vlog('tts', `warm-up: ${texts.length} lines, ${this.queue.pending} to download`);
  }

  /** The next question: ahead of warm-up, behind what is being said now. */
  prefetch(text: string) {
    if (prerecorded(this.lang, text)) return Promise.resolve(null);
    return this.fetchAudio(text, 'next');
  }

  private fetchAudio(text: string, prio: TtsPriority): Promise<File | null> {
    if (!this.useGateway || gateway.offline || this.released) return Promise.resolve(null);
    const file = this.fileFor(text);
    if (this.onDisk(file)) return Promise.resolve(file);
    return this.queue.get(file.uri, text, prio);
  }

  private async download(text: string, prio: TtsPriority): Promise<File | null> {
    const file = this.fileFor(text);
    const t = Date.now();
    try {
      const f = await File.downloadFileAsync(gateway.ttsUrl(text, this.lang), file, { headers: gateway.ttsHeaders(prio), idempotent: true });
      vlog('tts', `net ${Date.now() - t} ms [${prio}] ${file.size ?? 0} B "${short(text)}"`);
      this.prune();
      return f;
    } catch (e) {
      vlog('err', `tts download failed [${prio}] after ${Date.now() - t} ms: ${String((e as Error)?.message ?? e)} "${short(text)}"`);
      try {
        if (file.exists) file.delete();
      } catch {
        // ignore
      }
      return null;
    }
  }

  /** Say `text`. Resolves when finished or stopped. `onStart` fires when sound begins. */
  async speak(text: string, onStart?: () => void): Promise<void> {
    this.stop();
    const my = this.gen;
    const t = Date.now();
    // 1 recorded in the app: no network, no TTS
    const pre = prerecorded(this.lang, text);
    if (pre) {
      vlog('tts', `recorded line (${(pre.ms / 1000).toFixed(1)} s) "${short(text)}"`);
      try {
        const uri = await Promise.race([localAsset(pre.asset), new Promise<null>((r) => setTimeout(() => r(null), ASSET_WAIT_MS))]);
        if (my !== this.gen) return;
        if (!uri) throw new Error(`not on the phone after ${ASSET_WAIT_MS} ms`);
        await this.playSource({ uri }, `asset:${pre.asset}`, text, onStart);
        return;
      } catch (e) {
        vlog('err', `recorded line failed to play: ${String((e as Error)?.message ?? e)}`);
        if (my !== this.gen) return;
      }
    }
    let file: File | null = null;
    const local = this.fileFor(text);
    if (this.onDisk(local)) {
      file = local;
      vlog('tts', `hit (on phone) "${short(text)}"`);
    } else if (this.useGateway && !gateway.offline) {
      file = await Promise.race([this.fetchAudio(text, 'now'), new Promise<null>((r) => setTimeout(() => r(null), NET_WAIT_MS))]);
      if (my !== this.gen) return;
      vlog(file ? 'tts' : 'err', file ? `ready after ${Date.now() - t} ms` : `no Sakhi audio after ${Date.now() - t} ms`);
    }
    if (my !== this.gen) return;
    if (file) {
      try {
        await this.playSource({ uri: file.uri }, file.uri, text, onStart);
        return;
      } catch (e) {
        vlog('err', `play failed: ${String((e as Error)?.message ?? e)}`);
        if (my !== this.gen) return;
      }
    }
    await this.device(text, onStart);
  }

  /** Play a file on the phone (a recorded line or a download); `key` identifies it so a re-ask rewinds instead of reloading. */
  private playSource(source: { uri: string }, key: string, text: string, onStart?: () => void) {
    return new Promise<void>((resolve, reject) => {
      const player = this.player ?? createAudioPlayer(null, { updateInterval: 100 });
      this.player = player;
      const t0 = Date.now();
      let started = false;
      let safety = setTimeout(() => finish('safety timeout'), SAFETY_MS);
      const cleanup = () => {
        sub.remove();
        clearTimeout(safety);
        clearTimeout(watchdog);
        if (this.done === stopped) this.done = null;
      };
      const finish = (why: string) => {
        cleanup();
        vlog('play', `${why} ${((Date.now() - t0) / 1000).toFixed(1)} s "${short(text, 24)}"`);
        resolve();
      };
      const stopped = () => {
        cleanup();
        resolve();
      };
      const sub = player.addListener('playbackStatusUpdate', (s: AudioStatus) => {
        if (!started && s.playing) {
          started = true;
          clearTimeout(watchdog);
          vlog('play', `SAKHI VOICE start ${Date.now() - t0} ms (${s.duration ? s.duration.toFixed(1) : '?'} s) "${short(text, 24)}"`);
          this.onVoice('sakhi');
          onStart?.();
          if (s.duration > 0) {
            clearTimeout(safety);
            safety = setTimeout(() => finish('safety (no finish event)'), s.duration * 1000 + 1500);
          }
        }
        if (s.didJustFinish) finish('done');
      });
      const watchdog = setTimeout(() => {
        cleanup();
        this.playerUri = null; // reload next time
        try {
          player.pause();
        } catch {
          // ignore
        }
        reject(new Error(`no sound within ${START_MS} ms`));
      }, START_MS);
      this.done = stopped;
      try {
        player.volume = 1;
        if (this.playerUri === key) {
          // Same line again (a re-ask): rewind instead of reloading.
          player.seekTo(0).catch(() => {});
        } else {
          player.replace(source);
          this.playerUri = key;
        }
        player.play();
      } catch (e) {
        cleanup();
        reject(e);
      }
    });
  }

  private device(text: string, onStart?: () => void) {
    vlog('err', `BACKUP VOICE (phone ${this.tag}${this.hasDeviceVoice === false ? ', not installed: may be silent' : ''}) "${short(text)}"`);
    this.onVoice('backup');
    return new Promise<void>((resolve) => {
      const t0 = Date.now();
      // Some engines never call back (no voice installed): a reading-time cap.
      const cap = setTimeout(() => finish('cap'), 4000 + text.length * 110);
      const finish = (why: string) => {
        clearTimeout(cap);
        if (this.done === stopped) this.done = null;
        vlog('say', `${why} ${((Date.now() - t0) / 1000).toFixed(1)} s`);
        resolve();
      };
      const stopped = () => {
        clearTimeout(cap);
        resolve();
      };
      this.done = stopped;
      Speech.speak(text, {
        language: this.tag,
        rate: 0.92,
        onStart: () => onStart?.(),
        onDone: () => finish('done'),
        onStopped: () => finish('stopped'),
        onError: (e) => finish(`error ${String(e?.message ?? e)}`),
      });
    });
  }

  stop() {
    this.gen++;
    const done = this.done;
    this.done = null;
    try {
      this.player?.pause();
    } catch {
      // ignore
    }
    Speech.stop().catch(() => {});
    done?.();
  }

  release() {
    this.stop();
    this.released = true;
    this.queue.close();
    this.player?.remove();
    this.player = null;
    this.playerUri = null;
  }

  /** Keep the cache under MAX_FILES: oldest files go first. */
  private prune() {
    try {
      const files = this.dir.list().filter((x): x is File => x instanceof File);
      if (files.length <= MAX_FILES) return;
      files
        .map((f) => ({ f, t: f.modificationTime ?? 0 }))
        .sort((a, b) => a.t - b.t)
        .slice(0, files.length - MAX_FILES)
        .forEach(({ f }) => f.delete());
    } catch {
      // a full cache is not worth failing a question over
    }
  }
}
