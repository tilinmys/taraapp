import { REPORT_LOGO_DATA_URI } from '@/data/report-logo';
import type { MotherRecord } from '@/data/store';
import { dateParts, type Visit } from '@/data/visits';
import { sharePdf } from '@/lib/report-pdf';

// The two documents a mother can download for each visit:
//   Prescription   the doctor's medicines (Rx table), advice, next visit
//   Visit summary  what was found, the vitals, and the same advice
// Same letterhead as the check-in report: tara logo, PHC, A4.

const esc = (v: string) => v.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

function page(title: string, m: MotherRecord, v: Visit, body: string) {
  const d = dateParts(v.date);
  return `<!doctype html><html><head><meta charset="utf-8"/><style>
  @page { size: A4; margin: 32px 36px; }
  * { box-sizing: border-box; }
  body { font-family: -apple-system, 'Helvetica Neue', Roboto, Arial, sans-serif; color: #2B1B21; font-size: 11px; line-height: 1.5; margin: 0; }
  header { display: flex; align-items: center; justify-content: space-between; padding-bottom: 12px; border-bottom: 2px solid #A11D44; }
  header img { height: 44px; }
  header .t { text-align: right; }
  h1 { font-size: 18px; margin: 0; color: #A11D44; }
  h2 { font-size: 10px; letter-spacing: 0.8px; text-transform: uppercase; color: #6F5A61; margin: 20px 0 6px; }
  .muted { color: #6F5A61; }
  .grid { display: grid; grid-template-columns: repeat(3, 1fr); border: 1px solid #E5D8D0; border-radius: 8px; overflow: hidden; margin-top: 14px; }
  .grid div { padding: 8px 10px; border-right: 1px solid #E5D8D0; }
  .grid div:last-child { border-right: 0; }
  .grid small { display: block; color: #6F5A61; font-size: 9px; text-transform: uppercase; letter-spacing: 0.6px; }
  table { width: 100%; border-collapse: collapse; }
  th, td { padding: 7px 8px; border-bottom: 1px solid #F0E6E1; text-align: left; vertical-align: top; }
  th { font-size: 9px; text-transform: uppercase; letter-spacing: 0.6px; color: #6F5A61; background: #FAF1EC; font-weight: 600; }
  .rx { font-size: 26px; color: #A11D44; font-family: Georgia, serif; font-weight: bold; margin: 18px 0 2px; }
  ol, ul { margin: 4px 0; padding-left: 18px; }
  li { margin: 3px 0; }
  .box { background: #FAF1EC; border-radius: 8px; padding: 10px 12px; margin-top: 8px; }
  .sign { margin-top: 40px; display: flex; justify-content: flex-end; }
  .sign div { width: 220px; border-top: 1px solid #2B1B21; padding-top: 6px; text-align: center; }
  footer { margin-top: 28px; font-size: 9px; color: #6F5A61; text-align: center; }
</style></head><body>
<header><img src="${REPORT_LOGO_DATA_URI}" alt="tara"/><div class="t"><h1>${esc(title)}</h1><div class="muted">${esc(v.place)} · ${d.long}</div></div></header>
<div class="grid">
  <div><small>Patient</small><b>${esc(m.name)}</b><br/><span class="muted">${esc(m.weeksLabel)} pregnant</span></div>
  <div><small>Doctor</small><b>${esc(v.doctor)}</b><br/><span class="muted">${esc(v.role)}</span></div>
  <div><small>Visit</small><b>${esc(v.kind)}</b><br/><span class="muted">${d.long}</span></div>
</div>
${body}
<div class="sign"><div>${esc(v.doctor)}<br/><span class="muted">${esc(v.role)}, ${esc(v.place)}</span></div></div>
<footer>Downloaded from tara · Keep this with your mother card and show it at every visit.</footer>
</body></html>`;
}

export function buildPrescriptionHtml(m: MotherRecord, v: Visit) {
  const p = v.prescription;
  if (!p) return page('Prescription', m, v, '<p>No medicines were prescribed at this visit.</p>');
  const rows = p.medicines
    .map((x, i) => `<tr><td>${i + 1}</td><td><b>${esc(x.name)}</b></td><td>${esc(x.dose)}</td><td>${esc(x.when)}</td><td>${esc(x.days)}</td></tr>`)
    .join('');
  return page(
    'Prescription',
    m,
    v,
    `<div class="rx">&#8478;</div>
<table><tr><th>#</th><th>Medicine</th><th>Dose</th><th>When</th><th>For</th></tr>${rows}</table>
<h2>Advice</h2><ol>${p.advice.map((a) => `<li>${esc(a)}</li>`).join('')}</ol>
${p.nextVisit ? `<div class="box"><b>Next visit:</b> ${esc(p.nextVisit)}</div>` : ''}`,
  );
}

export function buildVisitHtml(m: MotherRecord, v: Visit) {
  const vit = v.vitals
    ? `<h2>Vitals</h2><table><tr><th>Blood pressure</th><th>Weight</th><th>Haemoglobin</th></tr><tr><td>${esc(v.vitals.bp ?? '—')}</td><td>${esc(v.vitals.weight ?? '—')}</td><td>${esc(v.vitals.hb ?? '—')}</td></tr></table>`
    : '';
  return page(
    'Visit summary',
    m,
    v,
    `<h2>Summary</h2><p>${esc(v.summary)}</p>
<h2>Findings</h2><ul>${v.findings.map((f) => `<li>${esc(f)}</li>`).join('')}</ul>
${vit}
${v.prescription ? `<h2>Advice</h2><ol>${v.prescription.advice.map((a) => `<li>${esc(a)}</li>`).join('')}</ol>` : ''}`,
  );
}

export function sharePrescription(m: MotherRecord, v: Visit) {
  return sharePdf(buildPrescriptionHtml(m, v), `Prescription · ${dateParts(v.date).long}`);
}

export function shareVisitSummary(m: MotherRecord, v: Visit) {
  return sharePdf(buildVisitHtml(m, v), `Visit summary · ${dateParts(v.date).long}`);
}
