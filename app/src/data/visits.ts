import type { EmblemTone } from '@/components/system/emblem';
import type { IconName } from '@/components/system/icon';

// The mother's doctor visits and prescriptions (her History tab). Mock data until
// the PHC records service is connected: the same shape the backend will send.
// Every visit has a visit summary PDF; some also carry a prescription.

export type Medicine = { name: string; dose: string; when: string; days: string };

export type Visit = {
  id: string;
  date: string; // ISO yyyy-mm-dd
  doctor: string;
  role: string; // specialty
  place: string;
  kind: 'Check-up' | 'Scan' | 'Lab tests' | 'Referral';
  icon: IconName;
  tone: EmblemTone;
  /** One plain sentence: what happened at this visit. */
  summary: string;
  findings: string[];
  vitals?: { bp?: string; weight?: string; hb?: string };
  prescription?: { medicines: Medicine[]; advice: string[]; nextVisit?: string };
  /** Follow-up booked at this visit (ISO date) and where. */
  next?: { date: string; where: string };
  /** The one reading worth showing on the card. */
  highlight?: { label: string; value: string; alert?: boolean };
};

export const VISITS: Visit[] = [
  {
    id: 'v-2026-10-21',
    date: '2026-10-21',
    doctor: 'Dr. Anand Kumar',
    role: 'Medical Officer',
    place: 'Hosahalli PHC',
    kind: 'Referral',
    icon: 'doctor',
    tone: 'cherry',
    next: { date: '2026-10-22', where: 'Taluk Hospital' },
    highlight: { label: 'BP', value: '148/96', alert: true },
    summary: 'BP check. Your doctor asked for a follow-up check at the taluk hospital.',
    findings: ['BP 148/96 mmHg', 'Headache and blurred vision for 2 days', 'Both feet swollen'],
    vitals: { bp: '148/96', weight: '61.2 kg', hb: '9.6 g/dL' },
    prescription: {
      medicines: [
        { name: 'Labetalol 100 mg', dose: '1 tablet', when: 'Morning and night, after food', days: 'Until review' },
        { name: 'Iron + Folic acid', dose: '1 tablet', when: 'After lunch', days: '30 days' },
        { name: 'Calcium 500 mg', dose: '1 tablet', when: 'After dinner', days: '30 days' },
      ],
      advice: ['Go to the taluk hospital today.', 'Rest on your left side.', 'Call 108 if headache or vision gets worse.'],
      nextVisit: 'Today, taluk hospital',
    },
  },
  {
    id: 'v-2026-10-02',
    date: '2026-10-02',
    doctor: 'Dr. Shalini Rao',
    role: 'Obstetrician',
    place: 'Taluk Hospital, Kadur',
    kind: 'Scan',
    icon: 'search',
    tone: 'plum',
    next: { date: '2026-10-30', where: 'Growth scan' },
    highlight: { label: 'Baby', value: '~1.3 kg' },
    summary: 'Growth scan: baby growing well, normal heartbeat and fluid.',
    findings: ['Baby weight about 1.3 kg (normal)', 'Heartbeat 142 per minute', 'Placenta and fluid normal'],
    vitals: { bp: '138/90', weight: '58.9 kg' },
  },
  {
    id: 'v-2026-09-10',
    date: '2026-09-10',
    doctor: 'Dr. Anand Kumar',
    role: 'Medical Officer',
    place: 'Hosahalli PHC',
    kind: 'Check-up',
    icon: 'pulse',
    tone: 'pumpkin',
    next: { date: '2026-10-08', where: 'Hosahalli PHC' },
    highlight: { label: 'Hb', value: '10.1 g/dL', alert: true },
    summary: 'Routine check-up. Haemoglobin a little low, iron increased.',
    findings: ['BP 126/82 mmHg', 'Hb 10.1 g/dL (slightly low)', 'Tetanus injection 2 given'],
    vitals: { bp: '126/82', weight: '57.0 kg', hb: '10.1 g/dL' },
    prescription: {
      medicines: [
        { name: 'Iron + Folic acid', dose: '2 tablets', when: 'After lunch and dinner', days: '30 days' },
        { name: 'Calcium 500 mg', dose: '1 tablet', when: 'After breakfast', days: '30 days' },
      ],
      advice: ['Eat greens, dal and jaggery every day.', 'Take iron with lemon water, not tea.'],
      nextVisit: 'In 4 weeks',
    },
  },
  {
    id: 'v-2026-08-14',
    date: '2026-08-14',
    doctor: 'Lab · Hosahalli PHC',
    role: 'Blood and urine tests',
    place: 'Hosahalli PHC',
    kind: 'Lab tests',
    icon: 'drop',
    tone: 'sage',
    highlight: { label: 'Sugar', value: '104 mg/dL' },
    summary: 'Routine blood and urine tests. Sugar and urine normal.',
    findings: ['Hb 10.6 g/dL', 'Blood sugar 104 mg/dL (normal)', 'Urine: no protein, no sugar', 'Blood group B+'],
  },
  {
    id: 'v-2026-07-17',
    date: '2026-07-17',
    doctor: 'Dr. Anand Kumar',
    role: 'Medical Officer',
    place: 'Hosahalli PHC',
    kind: 'Check-up',
    icon: 'pulse',
    tone: 'pumpkin',
    next: { date: '2026-09-10', where: 'Hosahalli PHC' },
    highlight: { label: 'BP', value: '118/76' },
    summary: 'All normal. Started calcium; tetanus injection 1 given.',
    findings: ['BP 118/76 mmHg', 'Weight 55.4 kg', 'Tetanus injection 1 given'],
    vitals: { bp: '118/76', weight: '55.4 kg', hb: '11.2 g/dL' },
    prescription: {
      medicines: [
        { name: 'Iron + Folic acid', dose: '1 tablet', when: 'After lunch', days: '60 days' },
        { name: 'Calcium 500 mg', dose: '1 tablet', when: 'After dinner', days: '60 days' },
      ],
      advice: ['One extra meal every day.', 'Next check-up in 8 weeks.'],
      nextVisit: 'In 8 weeks',
    },
  },
];

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const MONTHS_LONG = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];

/** "2026-10-21" -> { day: '21', month: 'Oct', monthYear: 'October 2026', long: '21 Oct 2026' } */
export function dateParts(iso: string) {
  const [y, m, d] = iso.split('-').map(Number);
  return {
    day: String(d),
    month: MONTHS[m - 1],
    monthYear: `${MONTHS_LONG[m - 1]} ${y}`,
    long: `${d} ${MONTHS[m - 1]} ${y}`,
  };
}
