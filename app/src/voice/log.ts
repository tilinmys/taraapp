// Voice diagnostics. One line per event, in the Metro terminal (Expo Go
// prints the phone's console there as "iOS LOG" / "Android LOG"):
//
//   [sakhi  12.34s] tts   hit  q03 (file on phone)
//   [sakhi  12.36s] play  start 21 ms  q03
//   [sakhi  15.80s] play  done  3.4 s  q03
//   [sakhi  15.81s] mic   on 96 ms (16000 Hz, 1 ch)
//   [sakhi  17.90s] vad   end  speech 1.2 s, peak -24 dB, floor -58 dB
//   [sakhi  18.41s] stt   ok 498 ms  q03  "ಬಿ ಪಿ 148 ಬೈ 96"
//
// Events only, never per audio frame. Development builds only, so a
// production phone does no string work for it.

let t0 = Date.now();

/** Restart the clock (one check-in = one timeline). */
export function vlogReset() {
  t0 = Date.now();
}

export function vlog(area: 'run' | 'gate' | 'tts' | 'play' | 'say' | 'mic' | 'vad' | 'stt' | 'mode' | 'live' | 'err', msg: string) {
  if (!__DEV__) return;
  const s = ((Date.now() - t0) / 1000).toFixed(2).padStart(7);
  console.log(`[sakhi ${s}s] ${area.padEnd(4)} ${msg}`);
}

/** Short, single-line form of a text for the log. */
export const short = (text: string, n = 40) => {
  const one = text.replace(/\s+/g, ' ').trim();
  return one.length > n ? `${one.slice(0, n)}…` : one;
};
