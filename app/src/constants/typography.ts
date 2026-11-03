import { Platform, type TextStyle } from 'react-native';

// tara typography. Three faces, chosen by how much attention a piece of text
// gets and how it should FEEL to a pregnant woman reading it:
//
//   Baloo (Latin + Devanagari: Baloo 2, Kannada: Baloo Tamma 2)
//     Everything she must read or act on. Rounded, soft terminals and open
//     counters: friendly and calm, never clinical, and very legible on
//     low-end screens. Ek Type drew one design across all three scripts, so
//     ಕನ್ನಡ, हिंदी and English look like the same gentle voice. Weight carries
//     focus, but Baloo's bold is heavy, so headings stop at SemiBold (600).
//   Satisfy (handwritten script, Latin only)
//     Warm, "a note from someone who loves you" moments: the mother's screen
//     titles and the baby's words ("Hi Amma, I'm here!"). Short English
//     phrases only, never instructions, never below 24 pt. Any Kannada or
//     Hindi run falls back to Baloo automatically.
//   Parisienne (formal script, Latin only)
//     The brand tagline beside the logo, matching the wordmark. Decorative.
//
// Both scripts were picked from the brand's Canva font board (Parisienne and
// Satisfy are on it; the others have no Google Fonts release).
//
// Focus tiers, highest first:
//   1 Act      buttons, input values, critical numbers  -> Baloo 600 / 700
//   2 Orient   screen titles, section headings           -> Baloo 600
//   3 Read     body copy, field labels                   -> Baloo 400 / 500
//   4 Glance   captions, hints, dividers, timestamps     -> Baloo 400 / 500
//   5 Feel     greetings, the baby's voice               -> Satisfy 400
//              brand tagline                             -> Parisienne 400

// Font family names as registered by expo-font (see app/_layout.tsx).
export const Families = {
  latin: {
    400: 'Baloo2_400Regular',
    500: 'Baloo2_500Medium',
    600: 'Baloo2_600SemiBold',
    700: 'Baloo2_700Bold',
  },
  kannada: {
    400: 'BalooTamma2_400Regular',
    500: 'BalooTamma2_500Medium',
    600: 'BalooTamma2_600SemiBold',
    700: 'BalooTamma2_700Bold',
  },
  devanagari: {
    400: 'Baloo2_400Regular',
    500: 'Baloo2_500Medium',
    600: 'Baloo2_600SemiBold',
    700: 'Baloo2_700Bold',
  },
  /** Brand tagline (formal script). */
  script: 'Parisienne_400Regular',
  /** Greetings and the baby's voice (handwritten script). */
  hand: 'Satisfy_400Regular',
} as const;

export type Weight = 400 | 500 | 600 | 700;
export type Script = 'latin' | 'kannada' | 'devanagari';

type Role = {
  weight: Weight;
  size: number;
  lineHeight: number;
  letterSpacing?: number;
  /** Parisienne (brand tagline) instead of Baloo. */
  script?: boolean;
  /** Satisfy (greetings, the baby's voice) instead of Baloo; Latin only. */
  hand?: boolean;
};

// The scale. Sizes in pt; line heights tuned for Indic scripts, which need
// more vertical room than Latin (tall matras and below-base forms).
export const Type = {
  // 2 Orient
  display: { weight: 600, size: 32, lineHeight: 42, letterSpacing: -0.3 }, // welcome / hero titles
  title: { weight: 600, size: 26, lineHeight: 36, letterSpacing: -0.2 }, // H1: screen titles
  heading: { weight: 600, size: 19, lineHeight: 27 }, // H2: card titles, role names

  // 1 Act
  button: { weight: 600, size: 18, lineHeight: 26, letterSpacing: 0.1 }, // primary + secondary buttons
  buttonSmall: { weight: 600, size: 16, lineHeight: 23, letterSpacing: 0.1 }, // social, chips, text links
  input: { weight: 500, size: 18, lineHeight: 26 }, // what the user types
  metric: { weight: 700, size: 28, lineHeight: 36, letterSpacing: -0.2 }, // critical numbers (BP, weeks, risk)

  // 3 Read
  body: { weight: 400, size: 17, lineHeight: 26 }, // primary body copy
  bodyStrong: { weight: 600, size: 17, lineHeight: 26 }, // emphasis, danger alerts
  label: { weight: 600, size: 15, lineHeight: 22 }, // form field labels
  subtitle: { weight: 400, size: 15, lineHeight: 22 }, // supporting line under a title

  // 4 Glance
  caption: { weight: 500, size: 14, lineHeight: 20 }, // subtext, timestamps, metadata
  overline: { weight: 600, size: 12, lineHeight: 16, letterSpacing: 1.2 }, // small uppercase labels

  // 5 Feel
  greeting: { weight: 600, size: 34, lineHeight: 44, hand: true }, // mother's screen titles, welcome
  greetingSmall: { weight: 600, size: 28, lineHeight: 36, hand: true }, // the baby's words in her bubble
  tagline: { weight: 400, size: 30, lineHeight: 40, script: true }, // brand line, decorative
} satisfies Record<string, Role>;

export type TypeRole = keyof typeof Type;

const KANNADA = /[ಀ-೿]/;
const DEVANAGARI = /[ऀ-ॿ꣠-ꣿ]/;

export function scriptOf(char: string): Script | null {
  if (KANNADA.test(char)) return 'kannada';
  if (DEVANAGARI.test(char)) return 'devanagari';
  if (/[A-Za-z0-9]/.test(char)) return 'latin';
  return null; // punctuation, spaces, symbols: join the neighbouring run
}

/** Split text into runs of a single script so each run gets the right Baloo cut. */
export function splitByScript(text: string): { text: string; script: Script }[] {
  const runs: { text: string; script: Script }[] = [];
  let current: Script | null = null;
  let buffer = '';
  for (const ch of text) {
    const s: Script = scriptOf(ch) ?? current ?? 'latin';
    if (s !== current && buffer) {
      runs.push({ text: buffer, script: current ?? 'latin' });
      buffer = '';
    }
    current = s;
    buffer += ch;
  }
  if (buffer) runs.push({ text: buffer, script: current ?? 'latin' });
  return runs;
}

// The vertical extent each Indic cut really draws, as a multiple of the font
// size (hhea ascent + descent, measured from the font files). The type scale's
// line heights are set for Latin (~1.4-1.5x); Kannada stacks vowel signs above
// and conjuncts (ottakshara) below the line, so at those heights its glyphs
// spill outside their text box: clipped, or over the next element, on Android
// and iOS. Indic runs get at least this line height.
const INDIC_LINE: Record<Exclude<Script, 'latin'>, number> = {
  kannada: 1.75, // Baloo Tamma 2: 1078 + 673 per 1000
  devanagari: 1.6, // Baloo 2: 1078 + 524 per 1000
};

/** The line height a role needs in a script (Latin: the scale's own). */
export function lineHeightFor(role: TypeRole, script: Script = 'latin', size: number = Type[role].size): number {
  const base = Type[role].lineHeight;
  return script === 'latin' ? base : Math.max(base, Math.ceil(size * INDIC_LINE[script]));
}

export function fontFor(role: TypeRole, script: Script = 'latin'): TextStyle {
  const r: Role = Type[role];
  if (r.script) {
    return { fontFamily: Families.script, fontSize: r.size, lineHeight: r.lineHeight, letterSpacing: r.letterSpacing };
  }
  if (r.hand) {
    if (script === 'latin') return { fontFamily: Families.hand, fontSize: r.size, lineHeight: r.lineHeight };
    // Satisfy has no Kannada / Devanagari: set that run in Baloo, sized to
    // sit with the script's smaller x-height.
    const size = Math.round(r.size * 0.78);
    return { fontFamily: Families[script][r.weight], fontSize: size, lineHeight: lineHeightFor(role, script, size) };
  }
  return {
    fontFamily: Families[script][r.weight],
    fontSize: r.size,
    lineHeight: lineHeightFor(role, script),
    letterSpacing: script === 'latin' ? r.letterSpacing : 0, // never track Indic scripts
  };
}

/** Monospace for rule codes and audit traces (system face: always present). */
export const MONO = Platform.select({ ios: 'Menlo', android: 'monospace', default: 'monospace' });
