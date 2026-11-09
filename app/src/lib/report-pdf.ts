import * as Print from 'expo-print';
import * as Sharing from 'expo-sharing';
import { Platform } from 'react-native';

import { CLINICAL_GROUPS, CLINICAL_TESTS } from '@/data/clinical';
import { reportQuestions, RULES, type RuleCode } from '@/data/mockScreening';
import { REPORT_LOGO_DATA_URI } from '@/data/report-logo';
import type { CheckInReport, MotherRecord } from '@/data/store';

// The Sakhi check-in as a one-document A4 PDF. The ASHA exports it from the
// report page and the doctor exports the very same file from the case view:
// both call shareReportPdf with the stored report, so they can never differ.
//   1 header: tara logo, title, report id and date
//   2 mother details in a 4-column grid
//   3 risk banner + the rules that fired (with their deterministic logic)
//   4 vitals: latest BP / Hb / sugar / weight and the previous visit
//   5 her answers by section (her visit's questionnaire), danger answers
//     marked; then the measurements and tests the health worker entered
//   6 conversation transcript (compact)
//   7 signatures: ASHA (captured at completion) and reviewing doctor

const A4 = { width: 595, height: 842 };

const esc = (v: string) =>
  v.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

function vitalRows(m: MotherRecord) {
  const v = m.vitals;
  const last = (a: number[]) => a[a.length - 1];
  const prev = (a: number[]) => (a.length > 1 ? a[a.length - 2] : undefined);
  const bp = (i: number) => (i >= 0 ? `${v.sys[i]}/${v.dia[i]}` : '—');
  const rows: [string, string, string, string][] = [
    ['Blood pressure', `${bp(v.sys.length - 1)} mmHg`, bp(v.sys.length - 2), last(v.sys) >= 140 || last(v.dia) >= 90 ? 'High' : 'Normal'],
    ['Haemoglobin', `${last(v.hb).toFixed(1)} g/dL`, prev(v.hb)?.toFixed(1) ?? '—', last(v.hb) < 10 ? 'Low' : 'Normal'],
    ['Blood sugar (random)', `${last(v.sugar)} mg/dL`, `${prev(v.sugar) ?? '—'}`, last(v.sugar) >= 140 ? 'High' : 'Normal'],
    ['Weight', `${last(v.weight).toFixed(1)} kg`, prev(v.weight)?.toFixed(1) ?? '—', '—'],
  ];
  return rows
    .map(
      ([k, now, before, flag]) =>
        `<tr><td>${k}</td><td class="num">${now}</td><td class="num muted">${before}</td><td class="${flag === 'Normal' || flag === '—' ? 'muted' : 'flag'}">${flag}</td></tr>`,
    )
    .join('');
}

const STATUS = { done: 'Done', pending: 'Pending', unavailable: 'Not available', not_indicated: 'Not needed' } as const;

/** Her latest clinical entry: every measured / examined value with where it came from. */
function clinicalRows(m: MotherRecord) {
  const e = m.clinical?.[0];
  if (!e || !Object.keys(e.fields).length) return '<p class="muted">Not entered yet: BP and weight are needed for the risk check.</p>';
  const fields = CLINICAL_GROUPS.flatMap((g) => g.fields)
    .filter((f) => e.fields[f.id])
    .map((f) => {
      const v = e.fields[f.id];
      const shown = f.kind === 'yesno' ? (v.value === 'yes' ? 'Yes' : 'No') : f.kind === 'choice' ? (f.options.find((o) => o.id === v.value)?.label.en ?? v.value) : `${v.value}${f.kind === 'number' ? ` ${f.unit}` : f.kind === 'bp' ? ' mmHg' : ''}`;
      return `<tr><td>${esc(f.label.en)}</td><td>${esc(shown)}</td><td class="muted">${esc(v.source)}</td></tr>`;
    })
    .join('');
  const when = new Date(e.at).toLocaleString('en-IN', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' });
  return `<table><tr><th>Item</th><th>Value</th><th>Source</th></tr>${fields}</table><p class="muted">Entered ${esc(when)} by ${esc(e.by)}</p>`;
}

/**
 * Her Lab report (the same entry's tests): each test with its status, and
 * its result with the unit (a choice shown by its name). A test that is not
 * Done says so: a missing result is never read as normal.
 */
function labRows(m: MotherRecord) {
  const e = m.clinical?.[0];
  const rows = CLINICAL_TESTS.filter((t) => e?.tests[t.id]);
  // The reports the ASHA uploaded (photos / files kept on her phone), newest first.
  const uploads = (m.labReports ?? [])
    .map((u) => `<tr><td>${esc(u.name)}</td><td>${u.kind === 'image' ? 'Photo' : u.kind === 'pdf' ? 'PDF' : 'File'}</td><td class="muted">${esc(new Date(u.at).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' }))} · ${esc(u.by)}</td></tr>`)
    .join('');
  const uploaded = uploads ? `<p class="muted">Reports uploaded by the ASHA</p><table><tr><th>Report</th><th>Type</th><th>Uploaded</th></tr>${uploads}</table>` : '';
  if (!e || !rows.length) return `${uploaded}${uploaded ? '' : '<p class="muted">No lab reports or results yet.</p>'}`;
  const body = rows
    .map((t) => {
      const x = e.tests[t.id];
      const k = t.result;
      const res = !x.result ? '' : k?.kind === 'choice' ? (k.options.find((o) => o.id === x.result)?.label.en ?? x.result) : k?.kind === 'number' ? `${x.result} ${k.unit}` : x.result;
      const when = new Date(x.at).toLocaleDateString('en-IN', { day: '2-digit', month: 'short' });
      return `<tr${x.status === 'pending' ? ' class="muted"' : ''}><td>${esc(t.label.en)}</td><td>${x.status === 'done' && res ? `<b>${esc(res)}</b>` : esc(STATUS[x.status])}</td><td class="muted">${esc(when)} · ${esc(x.by)}</td></tr>`;
    })
    .join('');
  return `${uploaded}<table><tr><th>Test</th><th>Result</th><th>Entered</th></tr>${body}</table>`;
}

/** Short, readable reference for a report id ("lakshmi-1792536039868" -> "TR-039868"). */
export const reportRef = (id: string) => `TR-${id.replace(/[^a-z0-9]/gi, '').slice(-6).toUpperCase()}`;

export function buildReportHtml(m: MotherRecord, r: CheckInReport, generatedAt = new Date()) {
  const date = generatedAt.toLocaleString('en-IN', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
  const rules = r.rules as RuleCode[];
  const questions = reportQuestions(r.answers, r.visit).filter((q) => q.id !== 'close');
  const sections = [...new Set(questions.map((q) => q.section))];
  const answerRows = sections
    .map((s) => {
      const qs = questions.filter((q) => q.section === s);
      return (
        `<tr class="sec"><td colspan="3">${esc(s)}</td></tr>` +
        qs
          .map((q) => {
            const a = r.answers[q.id];
            const n = questions.indexOf(q) + 1;
            return `<tr${a?.danger ? ' class="danger"' : ''}><td class="num muted">${n}</td><td>${esc(q.en)}</td><td>${a ? `${a.danger ? '⚠ ' : ''}${esc(a.value)}${a.corrected ? ' <span class="muted">(corrected by the ASHA)</span>' : ''}` : '<span class="muted">Not asked</span>'}</td></tr>`;
          })
          .join('')
      );
    })
    .join('');
  const transcript = r.transcript
    .map((l) => `<p class="${l.from}"><b>${l.from === 'sakhi' ? 'Sakhi' : 'ASHA'}:</b> ${l.danger ? '⚠ ' : ''}${esc(l.text)}</p>`)
    .join('');

  return `<!doctype html><html><head><meta charset="utf-8"/>
<style>
  @page { size: A4; margin: 28px 32px; }
  * { box-sizing: border-box; }
  body { font-family: -apple-system, 'Helvetica Neue', Roboto, Arial, sans-serif; color: #2B1B21; font-size: 10.5px; line-height: 1.45; margin: 0; }
  header { display: flex; align-items: center; justify-content: space-between; padding-bottom: 12px; border-bottom: 2px solid #A11D44; }
  header img { height: 46px; }
  header .t { text-align: right; }
  header h1 { font-size: 17px; margin: 0; color: #A11D44; letter-spacing: -0.2px; }
  .muted { color: #6F5A61; }
  h2 { font-size: 11px; letter-spacing: 0.8px; text-transform: uppercase; color: #6F5A61; margin: 18px 0 6px; }
  .grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 0; border: 1px solid #E5D8D0; border-radius: 8px; overflow: hidden; margin-top: 14px; }
  .grid div { padding: 8px 10px; border-right: 1px solid #E5D8D0; }
  .grid div:last-child { border-right: 0; }
  .grid small { display: block; color: #6F5A61; font-size: 9px; text-transform: uppercase; letter-spacing: 0.6px; }
  .grid b { font-size: 12px; }
  .risk { margin-top: 14px; padding: 12px 14px; border-radius: 8px; }
  .risk.high { background: #F7E4E8; border: 1.5px solid #A11D44; }
  .risk.ok { background: #EEF6F1; border: 1.5px solid #2F6B4F; }
  .risk b { font-size: 13px; }
  table { width: 100%; border-collapse: collapse; }
  td, th { padding: 5px 8px; border-bottom: 1px solid #F0E6E1; vertical-align: top; text-align: left; }
  th { font-size: 9px; text-transform: uppercase; letter-spacing: 0.6px; color: #6F5A61; font-weight: 600; background: #FAF1EC; }
  td.num { white-space: nowrap; font-variant-numeric: tabular-nums; }
  td.flag { color: #A11D44; font-weight: 700; }
  tr.sec td { background: #FAF1EC; font-weight: 700; font-size: 9.5px; text-transform: uppercase; letter-spacing: 0.6px; color: #C84B00; }
  tr.danger td { color: #A11D44; font-weight: 600; }
  .rule { font-family: Menlo, 'Courier New', monospace; font-size: 9.5px; }
  .two { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  .transcript p { margin: 0 0 3px; }
  .transcript .asha { padding-left: 14px; }
  .sign { display: grid; grid-template-columns: 1fr 1fr; gap: 28px; margin-top: 26px; page-break-inside: avoid; }
  .sign .ln { border-top: 1px solid #2B1B21; padding-top: 6px; }
  .sig { font-family: 'Snell Roundhand', 'Brush Script MT', cursive; font-size: 20px; color: #A11D44; height: 28px; }
  footer { margin-top: 18px; font-size: 8.5px; color: #6F5A61; text-align: center; }
  .page { page-break-before: always; }
</style></head><body>
<header>
  <img src="${REPORT_LOGO_DATA_URI}" alt="tara"/>
  <div class="t"><h1>Antenatal voice check-in</h1>
  <div class="muted">Ref ${reportRef(r.id)} · ${esc(date)}</div></div>
</header>

<div class="grid">
  <div><small>Mother</small><b>${esc(m.name)}</b></div>
  <div><small>Gestation</small><b>${esc(m.weeksLabel)} (${m.months} mo)</b></div>
  <div><small>PHC</small><b>${esc(m.village)}</b></div>
  <div><small>ASHA</small><b>${esc(r.asha)}</b></div>
</div>

<div class="risk ${r.high ? 'high' : 'ok'}">
  <b>${r.high ? '⚠ HIGH RISK — refer to PHC today' : 'Routine — no danger signs'}</b>
  <div>${rules.length ? rules.map((c) => esc(RULES[c].title)).join(' · ') : 'No rule fired. Next check-in in 2 weeks.'}</div>
</div>

<div class="two">
  <div>
    <h2>Vitals</h2>
    <table><tr><th>Measure</th><th>Today</th><th>Last visit</th><th>Flag</th></tr>${vitalRows(m)}</table>
  </div>
  <div>
    <h2>Rule audit</h2>
    <table><tr><th>Rule</th><th>Logic → action</th></tr>
    ${rules.length ? rules.map((c) => `<tr><td class="rule">#${c}</td><td><span class="rule">${esc(RULES[c].logic)}</span><br/>${esc(RULES[c].action)}</td></tr>`).join('') : '<tr><td colspan="2" class="muted">None</td></tr>'}
    </table>
  </div>
</div>

<h2>Reported by her (voice${r.visit ? `, visit ${r.visit}` : ''})</h2>
<table><tr><th>#</th><th>Question</th><th>Answer</th></tr>${answerRows}</table>

<h2>Measurements (entered by the health worker)</h2>
${clinicalRows(m)}

<h2>Lab report</h2>
${labRows(m)}

<div class="sign">
  <div><div class="sig">${esc(r.asha)}</div><div class="ln">ASHA worker · ${esc(r.asha)}<br/><span class="muted">Recorded by voice with Sakhi</span></div></div>
  <div><div class="sig"></div><div class="ln">Reviewing doctor · Hosahalli PHC<br/><span class="muted">Signature and date</span></div></div>
</div>

<div class="page"></div>
<h2>Conversation transcript</h2>
<div class="transcript">${transcript}</div>

<footer>Generated by tara · Decisions are rule-based (deterministic) and reviewed by a doctor. Not a diagnosis.</footer>
</body></html>`;
}

/** Turns any report HTML into a PDF: share sheet on phones, print dialog on web. */
export async function sharePdf(html: string, title: string) {
  if (Platform.OS === 'web') {
    await Print.printAsync({ html });
    return;
  }
  const { uri } = await Print.printToFileAsync({ html, ...A4 });
  if (await Sharing.isAvailableAsync()) {
    await Sharing.shareAsync(uri, { mimeType: 'application/pdf', UTI: 'com.adobe.pdf', dialogTitle: title });
  } else {
    await Print.printAsync({ uri });
  }
}

/** The Sakhi check-in report as a PDF. */
export function shareReportPdf(m: MotherRecord, r: CheckInReport) {
  return sharePdf(buildReportHtml(m, r), `${m.name} · check-in report`);
}
