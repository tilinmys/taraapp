import { describe, expect, it } from '@jest/globals';

import type { Visit } from '@/data/mockScreening';

import { Intake } from '../intake';

// STT end to end, seen by the app's own understanding (voice/nlu): for every
// answer sent through the phone's path (scripts/stt_eval/run_e2e.py ->
// results.json), the record Intake makes from the STT transcript is compared
// with the record it makes from what was actually SAID (the reference text).
//   same record                 the STT was good enough for this question
//   different record            an STT error that changes what is saved
//   reference not understood    an NLU gap (the parser, not the STT)
// The comparison is written to scripts/stt_eval/nlu_compare.json. Skipped
// when results.json is absent (it needs the GPU service and the tunnel).

type Row = { id: string; qid: string; visit: number; lang: string; text: string; stt: string; status: number; variant: string };
type Rec = { value: string; danger: boolean; signs?: string[] };

// Node built-ins, typed here (the app has no @types/node; this test runs under Jest / Node).
declare const __dirname: string;
type Fs = { existsSync(p: string): boolean; readFileSync(p: string, enc: 'utf8'): string; writeFileSync(p: string, data: string): void };
type PathMod = { resolve(...p: string[]): string; join(...p: string[]): string };
// eslint-disable-next-line @typescript-eslint/no-require-imports
const fs = require('fs') as Fs;
// eslint-disable-next-line @typescript-eslint/no-require-imports
const path = require('path') as PathMod;

const ROOT = path.resolve(__dirname, '../../../../..');
const RESULTS = path.join(ROOT, 'scripts', 'stt_eval', 'results.json');
const rows: Row[] = fs.existsSync(RESULTS) ? JSON.parse(fs.readFileSync(RESULTS, 'utf8')) : [];
const UNCLEAR = /^(not clear|no answer)|\(check\)|part not answered/i;

const record = (r: Row, text: string): Rec => {
  const rec = new Intake(r.visit as Visit).answer(r.qid, text).record;
  return { value: rec.value, danger: !!rec.danger, ...(rec.signs?.length ? { signs: [...rec.signs].sort() } : {}) };
};
const same = (a: Rec, b: Rec) => a.value === b.value && a.danger === b.danger && JSON.stringify(a.signs ?? []) === JSON.stringify(b.signs ?? []);

(rows.length ? describe : describe.skip)('STT end to end through the app NLU', () => {
  const out = rows
    .filter((r) => r.status === 200)
    .map((r) => {
      const ref = record(r, r.text);
      const stt = record(r, r.stt);
      const nluGap = UNCLEAR.test(ref.value);
      return { id: r.id, qid: r.qid, lang: r.lang, variant: r.variant, said: r.text, heard: r.stt, ref, stt, agree: same(ref, stt), nluGap, dangerMissed: ref.danger && !stt.danger, dangerAdded: !ref.danger && stt.danger };
    });

  it('compares every transcribed answer and writes the report', () => {
    fs.writeFileSync(path.join(ROOT, 'scripts', 'stt_eval', 'nlu_compare.json'), JSON.stringify(out, null, 1));
    expect(out.length).toBeGreaterThan(0);
  });

  it('on clean speech, STT never hides a danger sign the reference text carries', () => {
    const missed = out.filter((o) => o.variant === 'clean' && o.dangerMissed).map((o) => `${o.id}: said "${o.said}" heard "${o.heard}"`);
    expect(missed).toEqual([]);
  });
});
