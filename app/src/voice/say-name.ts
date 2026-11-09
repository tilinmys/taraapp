import type { Lang } from '@/i18n/strings';

import { gateway } from './gateway';
import { Mic, micPermission, micSupported } from './mic';
import { Utterance } from './utterance';

// "Say the name": a few seconds of speech, written down by Sakhi's speech
// service (the same /stt as the check-in). Used to name an uploaded lab
// report by voice. stop() ends it early (the ASHA tapped the mic again).
// Resolves with the words, or null when there is no mic, no permission, no
// network or nothing was heard (the ASHA types the name instead).

export type Listening = { stop: () => void; result: Promise<string | null> };

export function sayName(lang: Lang, maxMs = 5000): Listening {
  let stop = () => {};
  const result = (async () => {
    if (!micSupported || gateway.offline || !gateway.configured) return null;
    if (!(await micPermission().catch(() => false))) return null;
    const utt = new Utterance();
    utt.begin();
    const mic = new Mic((pcm, off) => utt.frame(pcm, off));
    try {
      await mic.start();
      await new Promise<void>((done) => {
        const timer = setTimeout(done, maxMs);
        stop = () => {
          clearTimeout(timer);
          done();
        };
      });
    } catch {
      return null;
    } finally {
      mic.dispose();
      utt.end();
    }
    if (utt.durationMs < 400) return null;
    try {
      const r = await gateway.stt(utt.toWav(), lang);
      return (r.text || r.en).trim() || null;
    } catch {
      return null;
    }
  })();
  return { stop: () => stop(), result };
}
