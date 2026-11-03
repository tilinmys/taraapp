import { vlog } from './log';

// Web only (imported by the *.web.ts voice modules). ONE AudioContext for
// the page, shared by Sakhi's voice and the microphone. Browsers keep it
// suspended until a user gesture, so unlockAudio() is called synchronously
// from the Start tap (before any await, or the browser no longer counts it
// as the tap's work).

let ctx: AudioContext | null = null;

export function audioContext(): AudioContext {
  ctx ??= new AudioContext({ latencyHint: 'interactive' });
  return ctx;
}

/** Call inside the tap handler. Safe to call again. */
export function unlockAudio() {
  const c = audioContext();
  if (c.state !== 'running') {
    c.resume()
      .then(() => vlog('mode', `web audio running at ${c.sampleRate} Hz`))
      .catch((e) => vlog('err', `web audio could not start: ${String(e)}`));
  }
  // One silent sample: Safari only unlocks output once something has played.
  const src = c.createBufferSource();
  src.buffer = c.createBuffer(1, 1, c.sampleRate);
  src.connect(c.destination);
  src.start();
}

/** Resolve when the context runs (or false after `ms`: it was never unlocked). */
export async function audioRunning(ms = 1500): Promise<boolean> {
  const c = audioContext();
  if (c.state === 'running') return true;
  try {
    await Promise.race([c.resume(), new Promise((_, rej) => setTimeout(() => rej(new Error('timeout')), ms))]);
  } catch {
    // fall through
  }
  return audioContext().state === 'running';
}
