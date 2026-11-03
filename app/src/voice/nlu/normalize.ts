// Text normalisation for what the speech-to-text returns, so that matching
// is exact and cheap afterwards (voice/nlu). Same function for the lexicon
// and for every answer, so both sides always agree:
//   1 Unicode NFC; zero-width joiners / BOM removed
//   2 Devanagari nukta folded (क़ -> क, ड़ -> ड), chandrabindu -> anusvara,
//     so "हाँ" == "हां" and "ज़्यादा" == "ज्यादा"
//   3 Kannada and Devanagari digits -> 0-9
//   4 lower case; punctuation -> space (except the "/" and "." inside numbers)
//   5 number WORDS -> digits, joined the way people say them:
//        "ನೂರ ನಲವತ್ತೆಂಟು" -> 148   "एक सौ अड़तालीस" -> 148
//        "one forty eight" -> 148   "ಇಪ್ಪತ್ತೈದು" -> 25
//   6 single spaces
// Linear in the text length; the number tables are built once.

const ZERO_WIDTH = /[​-‍⁠﻿]/g;
const NUKTA_COMPOSED: Record<string, string> = {
  'क़': 'क', 'ख़': 'ख', 'ग़': 'ग', 'ज़': 'ज',
  'ड़': 'ड', 'ढ़': 'ढ', 'फ़': 'फ', 'य़': 'य',
};

function foldScripts(s: string) {
  let out = '';
  for (const ch of s) {
    const c = ch.codePointAt(0) as number;
    if (c >= 0x0ce6 && c <= 0x0cef) out += String(c - 0x0ce6); // Kannada digits
    else if (c >= 0x0966 && c <= 0x096f) out += String(c - 0x0966); // Devanagari digits
    else if (NUKTA_COMPOSED[ch]) out += NUKTA_COMPOSED[ch];
    else if (c === 0x093c) continue; // nukta
    else if (c === 0x0901) out += 'ं'; // chandrabindu -> anusvara
    else out += ch;
  }
  return out;
}

// ---- number words -------------------------------------------------------------

const KN_UNITS: [string, number][] = [
  ['ಸೊನ್ನೆ', 0], ['ಒಂದು', 1], ['ಎರಡು', 2], ['ಮೂರು', 3], ['ನಾಲ್ಕು', 4], ['ಐದು', 5], ['ಆರು', 6], ['ಏಳು', 7], ['ಎಂಟು', 8],
  ['ಒಂಬತ್ತು', 9], ['ಹತ್ತು', 10], ['ಹನ್ನೊಂದು', 11], ['ಹನ್ನೆರಡು', 12], ['ಹದಿಮೂರು', 13], ['ಹದಿನಾಲ್ಕು', 14], ['ಹದಿನೈದು', 15],
  ['ಹದಿನಾರು', 16], ['ಹದಿನೇಳು', 17], ['ಹದಿನೆಂಟು', 18], ['ಹತ್ತೊಂಬತ್ತು', 19], ['ನೂರು', 100], ['ನೂರ', 100], ['ನೂರಾ', 100],
];
// Tens and their fused stems: ಇಪ್ಪತ್ತು (20), ಇಪ್ಪತ್ತ + ೈದು = ಇಪ್ಪತ್ತೈದು (25).
const KN_TENS: [string, string, number][] = [
  ['ಇಪ್ಪತ್ತು', 'ಇಪ್ಪತ್ತ', 20], ['ಮೂವತ್ತು', 'ಮೂವತ್ತ', 30], ['ನಲವತ್ತು', 'ನಲವತ್ತ', 40], ['ಐವತ್ತು', 'ಐವತ್ತ', 50],
  ['ಅರವತ್ತು', 'ಅರವತ್ತ', 60], ['ಎಪ್ಪತ್ತು', 'ಎಪ್ಪತ್ತ', 70], ['ಎಂಬತ್ತು', 'ಎಂಬತ್ತ', 80], ['ತೊಂಬತ್ತು', 'ತೊಂಬತ್ತ', 90],
];
const KN_FUSED_UNITS: [string, number][] = [
  ['ೊಂದು', 1], ['ೆರಡು', 2], ['ಮೂರು', 3], ['ನಾಲ್ಕು', 4], ['ೈದು', 5], ['ಾರು', 6], ['ೇಳು', 7], ['ೆಂಟು', 8], ['ೊಂಬತ್ತು', 9],
];

const HI_WORDS =
  'शून्य एक दो तीन चार पांच छह सात आठ नौ दस ग्यारह बारह तेरह चौदह पंद्रह सोलह सत्रह अठारह उन्नीस बीस इक्कीस बाईस तेईस चौबीस पच्चीस छब्बीस सत्ताईस अट्ठाईस उनतीस तीस इकतीस बत्तीस तैंतीस चौंतीस पैंतीस छत्तीस सैंतीस अड़तीस उनतालीस चालीस इकतालीस बयालीस तैंतालीस चवालीस पैंतालीस छियालीस सैंतालीस अड़तालीस उनचास पचास इक्यावन बावन तिरपन चौवन पचपन छप्पन सत्तावन अट्ठावन उनसठ साठ इकसठ बासठ तिरसठ चौंसठ पैंसठ छियासठ सड़सठ अड़सठ उनहत्तर सत्तर इकहत्तर बहत्तर तिहत्तर चौहत्तर पचहत्तर छिहत्तर सतहत्तर अठहत्तर उन्यासी अस्सी इक्यासी बयासी तिरासी चौरासी पचासी छियासी सत्तासी अट्ठासी नवासी नब्बे इक्यानबे बानबे तिरानबे चौरानबे पचानबे छियानबे सत्तानबे अट्ठानबे निन्यानबे'.split(' ');
const HI_EXTRA: [string, number][] = [['पाँच', 5], ['छः', 6], ['छे', 6], ['पन्द्रह', 15], ['अठ्ठाईस', 28], ['सौ', 100]];

const EN_WORDS =
  'zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen'.split(' ');
const EN_TENS: [string, number][] = [['twenty', 20], ['thirty', 30], ['forty', 40], ['fourty', 40], ['fifty', 50], ['sixty', 60], ['seventy', 70], ['eighty', 80], ['ninety', 90], ['hundred', 100]];

let NUMBER_WORDS: Map<string, number> | null = null;

function numberWords() {
  if (NUMBER_WORDS) return NUMBER_WORDS;
  const m = new Map<string, number>();
  const add = (w: string, v: number) => m.set(foldScripts(w.normalize('NFC')), v);
  KN_UNITS.forEach(([w, v]) => add(w, v));
  for (const [full, stem, v] of KN_TENS) {
    add(full, v);
    for (const [suf, u] of KN_FUSED_UNITS) add(stem + suf, v + u);
  }
  HI_WORDS.forEach((w, i) => add(w, i));
  HI_EXTRA.forEach(([w, v]) => add(w, v));
  EN_WORDS.forEach((w, i) => add(w, i));
  EN_TENS.forEach(([w, v]) => add(w, v));
  NUMBER_WORDS = m;
  return m;
}

/**
 * A run of number WORDS -> numbers. Hundreds multiply, tens take a unit
 * ("forty eight"), and a leading 1-2 before a two-digit number is spoken
 * hundreds ("one forty eight" = 148, how BP is read aloud).
 */
export function combineNumbers(values: number[]): number[] {
  const out: number[] = [];
  let acc = 0;
  let pending: number | null = null;
  const flush = () => {
    if (pending !== null || acc) out.push(acc + (pending ?? 0));
    acc = 0;
    pending = null;
  };
  for (let i = 0; i < values.length; i++) {
    const v = values[i];
    if (v === 100) {
      acc += (pending ?? 1) * 100;
      pending = null;
      continue;
    }
    if (pending === null) {
      const next = values[i + 1];
      // "one forty eight": 1 or 2, then a two-digit number, no "hundred" yet
      if (!acc && (v === 1 || v === 2) && next !== undefined && next >= 10 && next < 100) {
        acc = v * 100;
        continue;
      }
      pending = v;
      continue;
    }
    if (pending >= 20 && pending % 10 === 0 && v < 10) {
      pending += v; // forty + eight
      continue;
    }
    if (acc && pending < 100 && v < 100 && pending + v < 100 && pending % 10 === 0 && pending >= 20) {
      pending += v;
      continue;
    }
    flush();
    pending = v;
  }
  flush();
  return out;
}

const TOKEN_SPLIT = /\s+/;

/** Full normalisation (see the header). */
export function normalize(raw: string): string {
  let s = foldScripts(raw.normalize('NFC').replace(ZERO_WIDTH, '')).toLowerCase();
  // Keep "148/96", "11.2", "c-section"; everything else that is punctuation is a space.
  s = s.replace(/(\d)\s*[/]\s*(\d)/g, '$1/$2').replace(/(\d)[.,](\d)/g, '$1.$2');
  s = s.replace(/[!"#$%&'()*+,:;<=>?@[\\\]^_`{|}~।॥“”‘’…]/g, ' ').replace(/(^|\s)[./-]+(?=\s|$)/g, ' ');
  s = s.replace(/(\D)\.(\D)/g, '$1 $2').replace(/\.(\s|$)/g, ' ');
  const words = numberWords();
  const tokens = s.split(TOKEN_SPLIT).filter(Boolean);
  const out: string[] = [];
  let run: number[] = [];
  const flushRun = () => {
    if (run.length) out.push(...combineNumbers(run).map(String));
    run = [];
  };
  for (const t of tokens) {
    const v = words.get(t);
    if (v !== undefined) {
      run.push(v);
      continue;
    }
    flushRun();
    out.push(t);
  }
  flushRun();
  return out.join(' ');
}
