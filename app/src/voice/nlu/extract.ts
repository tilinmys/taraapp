import { AhoCorasick, withinOneEdit } from './aho';
import { CLAUSE_BREAKS, CONNECTORS, LEXICON, ORDINALS, type Concept } from './lexicon';
import { normalize } from './normalize';

// One spoken answer -> FACTS: which concepts were mentioned and whether each
// was affirmed or denied, the numbers with the unit they belong to, a blood
// pressure, an ordinal, and the leftover words (a name). No model, no
// network: one Aho–Corasick pass per clause plus a handful of regexes.
//
//   clauses   split at sentence ends and at "but / ಆದರೆ / लेकिन": a denial
//             never crosses into the next clause ("headache yes, BUT no
//             vision problem")
//   matches   Indic stems at the start of a word, Latin / digits as whole
//             words; overlapping matches keep the longest ("ಏನೂ ಇಲ್ಲ" beats
//             "ಇಲ್ಲ"; "साफ नहीं दिख" swallows its own "नहीं")
//   polarity  for each mention, in order of strength:
//               1 fused in the same Kannada word: ತಲೆನೋವಿಲ್ಲ (no), ತಲೆನೋವಿದೆ (yes)
//               2 an English pre-cue reaching it through a list:
//                 "no headache or blurred vision"
//               3 the next post-cue after it (Kannada / Hindi): one cue
//                 covers the whole list before it: "ತಲೆನೋವು, ಕಣ್ಣು ಮಂಜು ಇಲ್ಲ"
//               4 mentioned with no cue = present ("ತಲೆನೋವು, ವಾಂತಿ")
//   global    the answer's own yes / no / don't know (for yes-no questions)

export type Polarity = 1 | -1;
export type NumberFact = { value: number; unit: Concept | null; marker: Concept | null; token: number };
export type Facts = {
  raw: string;
  text: string;
  /** Polar concepts and their resolved polarity (a later clause wins). */
  said: Map<Concept, Polarity>;
  /** Every concept seen, cues and markers included. */
  seen: Set<Concept>;
  global: 'yes' | 'no' | 'unknown' | null;
  numbers: NumberFact[];
  bp: [number, number] | null;
  ordinal: number | null;
  /** A date said for the last period / due date ("12/3", "12 march"). */
  date: string | null;
  /** Words that matched nothing (a name, in "Roopa, 25"). */
  rest: string[];
};

const CUES: Partial<Record<Concept, { pol: Polarity; pre: boolean }>> = {
  NEG: { pol: -1, pre: false },
  NEG_PRE: { pol: -1, pre: true },
  AFF: { pol: 1, pre: false },
  AFF_PRE: { pol: 1, pre: true },
  NOTHING: { pol: -1, pre: false },
};
/** Concepts that are answers in themselves (they get a polarity). */
const POLAR = new Set<Concept>([
  'HEADACHE', 'VISION', 'SWELL_FACE', 'SWELL_FEET', 'SWELLING', 'PALLOR', 'ABD_PAIN', 'BLEEDING', 'LEAKING', 'FITS', 'MOVEMENT',
  'FEVER', 'VOMITING', 'BREATHLESS', 'CSECTION', 'NATURAL', 'IVF', 'IRON', 'SCAN', 'TT', 'DIABETES', 'HTN', 'THYROID', 'MEDICINE',
  'ALLERGY', 'DOCTOR', 'NURSE', 'AMBULANCE', 'REDUCED', 'DAILY', 'SOMETIMES', 'NORMAL', 'BOTH', 'ONE_ONLY', 'PLACE', 'CHILD',
]);
const MARKERS = new Set<Concept>(['MONTH', 'WEEK', 'YEAR', 'KG', 'HB', 'SUGAR', 'FHR', 'CHILD', 'PREGNANCY', 'NAME', 'AGE', 'BP']);

const LATIN_OR_DIGIT = /^[a-z0-9 ./'-]+$/;
const KN_NEG_SUFFIX = /(ಲ್ಲ|ಲ್ಲಾ|ಲ್ಲಪ್ಪ|ಲ್ಲಮ್ಮ|ಲ್ಲವ)$/;
const KN_AFF_SUFFIX = /(ಿದೆ|ಇದೆ|ತ್ತಿದೆ|ತಿದೆ|ಾಗಿದೆ|ಾಗಿತ್ತು|ಿತ್ತು|ದ್ದಾರೆ|ದ್ದಾಳೆ)$/;
const BP_RE = /(\d{2,3})\s*(?:\/|-|by|over|upon|slash|ಬೈ|ಬಾಯ್|ಓವರ್|ಮೇಲೆ|ಮ್ಯಾಲೆ|बाई|बाय|बटा|ओवर|पर)\s*(\d{2,3})/;
const DATE_RE = /\b(\d{1,2})\s*[/-]\s*(\d{1,2})(?:\s*[/-]\s*(\d{2,4}))?\b/;
const MONTH_NAMES =
  /\b(jan(uary)?|feb(ruary)?|mar(ch)?|apr(il)?|may|june?|july?|aug(ust)?|sep(tember)?|oct(ober)?|nov(ember)?|dec(ember)?|ಜನವರಿ|ಫೆಬ್ರವರಿ|ಮಾರ್ಚ್|ಏಪ್ರಿಲ್|ಮೇ|ಜೂನ್|ಜುಲೈ|ಆಗಸ್ಟ್|ಸೆಪ್ಟೆಂಬರ್|ಅಕ್ಟೋಬರ್|ನವೆಂಬರ್|ಡಿಸೆಂಬರ್|जनवरी|फरवरी|मार्च|अप्रैल|मई|जून|जुलाई|अगस्त|सितंबर|अक्टूबर|नवंबर|दिसंबर)\b/;
const ORDINAL_SUFFIX = /^(\d)(st|nd|rd|th|ನೇ|ನೆ|ನೆಯ|वां|वीं|वा|वी)$/;

type Payload = { concept: Concept; fixed: boolean };
type Hit = { start: number; end: number; concepts: Concept[]; fixed: boolean; tokenStart: number; tokenEnd: number };

let MATCHER: AhoCorasick<Payload> | null = null;
let LATIN_WORDS: Map<number, [string, Payload][]> | null = null;
let ORDINAL_MAP: Map<string, number> | null = null;

function build() {
  if (MATCHER) return;
  const entries: [string, Payload][] = [];
  const latin = new Map<number, [string, Payload][]>();
  for (const e of LEXICON) {
    for (const f of e.forms) {
      const form = normalize(f);
      if (!form) continue;
      const payload = { concept: e.concept, fixed: e.fixed === 1 };
      entries.push([form, payload]);
      // single Latin words of 5+ letters also match with one typo
      if (/^[a-z]{5,}$/.test(form)) {
        const list = latin.get(form.length) ?? [];
        list.push([form, payload]);
        latin.set(form.length, list);
      }
    }
  }
  MATCHER = new AhoCorasick(entries);
  LATIN_WORDS = latin;
  ORDINAL_MAP = new Map(ORDINALS.map(([w, n]) => [normalize(w), n]));
}

/** Token index for every character position, and token boundaries. */
function tokenize(text: string) {
  const starts: number[] = [];
  const ends: number[] = [];
  const at = new Int32Array(text.length + 1).fill(-1);
  const re = /\S+/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    const t = starts.length;
    starts.push(m.index);
    ends.push(m.index + m[0].length);
    for (let i = m.index; i < m.index + m[0].length; i++) at[i] = t;
  }
  return { starts, ends, at, tokens: starts.map((s, i) => text.slice(s, ends[i])) };
}

/** The matches of one clause, filtered by word boundaries, longest first, no overlaps. */
function matchesOf(text: string, tk: ReturnType<typeof tokenize>): Hit[] {
  const raw = (MATCHER as AhoCorasick<Payload>).search(text);
  const ok = raw.filter((h) => {
    const startsWord = h.start === 0 || text[h.start - 1] === ' ';
    if (!startsWord) return false;
    if (LATIN_OR_DIGIT.test(h.pattern)) return h.end === text.length || text[h.end] === ' ';
    return true;
  });
  // group identical spans (one form can mean two concepts: "sugar")
  const spans = new Map<string, Hit>();
  for (const h of ok) {
    const key = `${h.start}:${h.end}`;
    const s = spans.get(key);
    if (s) {
      if (!s.concepts.includes(h.payload.concept)) s.concepts.push(h.payload.concept);
      s.fixed ||= h.payload.fixed;
    } else {
      spans.set(key, { start: h.start, end: h.end, concepts: [h.payload.concept], fixed: h.payload.fixed, tokenStart: tk.at[h.start], tokenEnd: tk.at[h.end - 1] });
    }
  }
  // leftmost-longest, non-overlapping
  const sorted = [...spans.values()].sort((a, b) => a.start - b.start || b.end - a.end);
  const kept: Hit[] = [];
  let reach = -1;
  for (const h of sorted) {
    if (h.start < reach) continue;
    kept.push(h);
    reach = h.end;
  }
  // one-typo rescue for Latin words that matched nothing
  const covered = new Set<number>();
  kept.forEach((h) => {
    for (let t = h.tokenStart; t <= h.tokenEnd; t++) covered.add(t);
  });
  tk.tokens.forEach((tok, t) => {
    if (covered.has(t) || !/^[a-z]{5,}$/.test(tok)) return;
    for (const len of [tok.length - 1, tok.length, tok.length + 1]) {
      const hit = LATIN_WORDS?.get(len)?.find(([w]) => withinOneEdit(tok, w));
      if (hit) {
        kept.push({ start: tk.starts[t], end: tk.ends[t], concepts: [hit[1].concept], fixed: hit[1].fixed, tokenStart: t, tokenEnd: t });
        covered.add(t);
        break;
      }
    }
  });
  return kept.sort((a, b) => a.start - b.start);
}

type Ev = { kind: 'concept'; hit: Hit; concept: Concept } | { kind: 'cue'; pol: Polarity; pre: boolean; token: number };

function splitClauses(raw: string): string[] {
  const parts = raw.split(/[?!।॥;\n]+|\.(?=\s|$)/);
  const out: string[] = [];
  for (const p of parts) {
    const n = normalize(p);
    if (!n) continue;
    let cur: string[] = [];
    for (const tok of n.split(' ')) {
      if (CLAUSE_BREAKS.has(tok)) {
        if (cur.length) out.push(cur.join(' '));
        cur = [];
      } else cur.push(tok);
    }
    if (cur.length) out.push(cur.join(' '));
  }
  return out;
}

export function extract(raw: string): Facts {
  build();
  const facts: Facts = { raw, text: normalize(raw), said: new Map(), seen: new Set(), global: null, numbers: [], bp: null, ordinal: null, date: null, rest: [] };
  let firstCue: 'yes' | 'no' | 'unknown' | null = null;
  let nothing = false;
  let unknown = false;
  let tokenBase = 0;

  for (const clause of splitClauses(raw)) {
    const tk = tokenize(clause);
    const hits = matchesOf(clause, tk);
    const covered = new Set<number>();
    hits.forEach((h) => {
      for (let t = h.tokenStart; t <= h.tokenEnd; t++) covered.add(t);
    });

    // events in order: concepts and cues (lexicon cues + Kannada verb endings)
    const events: Ev[] = [];
    for (const h of hits) {
      for (const c of h.concepts) facts.seen.add(c);
      const cue = h.concepts.map((c) => CUES[c]).find(Boolean);
      if (h.concepts.includes('NOTHING')) nothing = true;
      if (h.concepts.includes('UNKNOWN')) unknown = true;
      if (cue) {
        events.push({ kind: 'cue', pol: cue.pol, pre: cue.pre, token: h.tokenStart });
        firstCue ??= cue.pol === 1 ? 'yes' : 'no';
        continue;
      }
      for (const c of h.concepts) if (POLAR.has(c)) events.push({ kind: 'concept', hit: h, concept: c });
    }
    tk.tokens.forEach((tok, t) => {
      if (covered.has(t)) return;
      if (KN_NEG_SUFFIX.test(tok)) {
        events.push({ kind: 'cue', pol: -1, pre: false, token: t });
        firstCue ??= 'no';
      } else if (KN_AFF_SUFFIX.test(tok)) {
        events.push({ kind: 'cue', pol: 1, pre: false, token: t });
        firstCue ??= 'yes';
      }
    });
    const tokOf = (e: Ev) => (e.kind === 'cue' ? e.token : e.hit.tokenStart);
    events.sort((a, b) => tokOf(a) - tokOf(b));

    events.forEach((e, k) => {
      if (e.kind !== 'concept') return;
      const pol = resolve(e, k, events, clause, tk, events[0]?.kind === 'cue');
      facts.said.set(e.concept, pol);
    });

    // numbers: digit tokens, with the unit / marker beside them
    tk.tokens.forEach((tok, t) => {
      const m = /^(\d{1,3}(?:\.\d)?)$/.exec(tok);
      if (!m) return;
      const after = hits.find((h) => h.tokenStart === t + 1);
      const before = [...hits].reverse().find((h) => h.tokenEnd < t && t - h.tokenEnd <= 2 && h.concepts.some((c) => MARKERS.has(c)));
      const unit = after?.concepts.find((c) => MARKERS.has(c)) ?? null;
      const marker = before?.concepts.find((c) => MARKERS.has(c)) ?? null;
      facts.numbers.push({ value: Number(m[1]), unit, marker, token: tokenBase + t });
    });
    const bp = BP_RE.exec(clause);
    if (bp && !facts.bp) {
      const sys = Number(bp[1]);
      const dia = Number(bp[2]);
      if (sys >= 70 && sys <= 260 && dia >= 40 && dia <= 160 && sys > dia) facts.bp = [sys, dia];
    }
    const date = DATE_RE.exec(clause) ?? MONTH_NAMES.exec(clause);
    if (date && !facts.date && !bp) facts.date = date[0];
    tk.tokens.forEach((tok, t) => {
      const ord = ORDINAL_MAP?.get(tok) ?? (ORDINAL_SUFFIX.test(tok) ? Number(tok[0]) : undefined);
      if (ord !== undefined && facts.ordinal === null) facts.ordinal = ord;
      if (!covered.has(t) && !/\d/.test(tok) && !CONNECTORS.has(tok) && !KN_NEG_SUFFIX.test(tok) && !KN_AFF_SUFFIX.test(tok) && ord === undefined) facts.rest.push(tok);
    });
    tokenBase += tk.tokens.length;
  }

  facts.global = nothing ? 'no' : firstCue ?? (unknown ? 'unknown' : null);
  if (unknown && !facts.said.size && firstCue === 'no') facts.global = 'unknown';
  return facts;
}

/**
 * Polarity of one mention (see the header for the order of the rules).
 * `prefix`: the clause opens with a cue ("no headache or blurred vision"),
 * so English cues govern what FOLLOWS them; otherwise ("headache no, vision
 * yes") every cue governs what came BEFORE it, like Kannada and Hindi.
 */
function resolve(e: Extract<Ev, { kind: 'concept' }>, k: number, events: Ev[], clause: string, tk: ReturnType<typeof tokenize>, prefix: boolean): Polarity {
  if (e.hit.fixed) return 1;
  // 1 fused in the same word: the rest of the token after the stem
  const tail = clause.slice(e.hit.end, tk.ends[e.hit.tokenEnd]);
  if (tail && KN_NEG_SUFFIX.test(tail)) return -1;
  if (tail && KN_AFF_SUFFIX.test(tail)) return 1;
  // 2 prefix clause: an English pre-cue reaching it through a list of mentions
  if (prefix) {
    for (let j = k - 1; j >= 0; j--) {
      const p = events[j];
      if (p.kind !== 'cue') continue;
      if (p.pre) return p.pol;
      break;
    }
  }
  // 3 the next cue after it covers every mention since the previous cue
  for (let j = k + 1; j < events.length; j++) {
    const n = events[j];
    if (n.kind !== 'cue') continue;
    if (!n.pre || !prefix) return n.pol;
    break; // in a prefix clause, a pre-cue belongs to what follows it
  }
  // 4 mentioned = present
  return 1;
}
