import type { Lang } from '@/i18n/strings';

import { PRERECORDED, type PrerecordedLine } from './prerecorded.generated';

// Sakhi's fixed lines, recorded once in her own voice (Gnani, female) and
// shipped inside the app (scripts/prerecord_sakhi.py): the greeting, the 24
// questions, the re-ask, "please say it again", the danger warning and the
// closing. They play instantly, offline, with no TTS request.
//
// Looked up by the EXACT text on screen: a line whose wording changed no
// longer matches its old recording and simply falls back to live TTS until
// the script is run again (never the wrong words in Sakhi's voice).

const squash = (t: string) => t.normalize('NFC').replace(/\s+/g, ' ').trim();

const BY_TEXT: Partial<Record<Lang, Map<string, PrerecordedLine>>> = {};

function table(lang: Lang) {
  if (lang !== 'kn' && lang !== 'hi') return null;
  let m = BY_TEXT[lang];
  if (!m) {
    m = new Map(Object.values(PRERECORDED[lang]).map((l) => [squash(l.text), l]));
    BY_TEXT[lang] = m;
  }
  return m;
}

/** The recording of this exact line, if there is one. */
export function prerecorded(lang: Lang, text: string): PrerecordedLine | null {
  return table(lang)?.get(squash(text)) ?? null;
}

/** How many of these lines are recorded (for the log). */
export function prerecordedCount(lang: Lang, texts: string[]) {
  return texts.filter((t) => prerecorded(lang, t)).length;
}
