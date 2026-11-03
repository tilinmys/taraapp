// MOCK SCREENING DATA + DETERMINISTIC RULES (UI demo only).
//
// The 24 questions mirror Sakhi's real intake (sakhi_intake.py): same ids,
// same order, and the Kannada / Hindi wording is copied from it verbatim. The
// English is a plain-language rendering. Rule codes are a demo labelling of
// the real triage logic (sakhi_triage.py: any reading >= 140/90 is high risk;
// danger symptoms escalate), evaluated deterministically from the answers so
// every flag can be traced to a rule. Clinical content must be reviewed by
// clinicians before any real use.

export type Lang = 'en' | 'kn' | 'hi';

export type RuleCode = 'PE-01' | 'PE-02' | 'PE-03' | 'PE-04' | 'EC-01' | 'HM-01' | 'FM-01' | 'IN-01' | 'AN-01';

export type Question = {
  id: string;
  section: string;
  en: string;
  questionKn: string;
  questionHi: string;
  /** Can an answer to this question be a danger sign? */
  dangerTrigger: boolean;
  /** Rule raised when this answer is a danger sign. */
  rule?: RuleCode;
  /** Demo answers for the simulator bar. */
  sampleNormal: string;
  sampleDanger?: string;
};

export const RULES: Record<RuleCode, { title: string; logic: string; action: string; severity: 'refer' | 'emergency' | 'watch' }> = {
  'PE-01': { title: 'Raised blood pressure', logic: 'BP >= 140/90', action: 'Re-check BP and refer to PHC today.', severity: 'refer' },
  'PE-02': { title: 'Severe blood pressure', logic: 'BP >= 160/110', action: 'Refer to PHC immediately.', severity: 'emergency' },
  'PE-03': { title: 'Pre-eclampsia symptoms', logic: 'HEADACHE | BLURRED_VISION | FACE_HAND_SWELLING', action: 'Check BP now and refer to PHC today.', severity: 'refer' },
  'PE-04': { title: 'Severe pre-eclampsia risk', logic: 'PE-01 AND PE-03', action: 'Refer to PHC immediately. Call 108 if she has a fit.', severity: 'emergency' },
  'EC-01': { title: 'Fits / convulsions', logic: 'CONVULSIONS == TRUE', action: 'Emergency: call 108 now.', severity: 'emergency' },
  'HM-01': { title: 'Bleeding or leaking fluid', logic: 'ABDOMINAL_PAIN | BLEEDING | LEAKING_FLUID', action: 'Refer to hospital immediately.', severity: 'emergency' },
  'FM-01': { title: 'Reduced baby movements', logic: 'FETAL_MOVEMENT == REDUCED (>= 24w)', action: 'Refer to PHC today for a check.', severity: 'refer' },
  'IN-01': { title: 'Fever, vomiting or breathlessness', logic: 'FEVER | SEVERE_VOMITING | BREATHLESSNESS', action: 'Refer to PHC today.', severity: 'refer' },
  'AN-01': { title: 'Anaemia or swelling signs', logic: 'PALLOR | PEDAL_EDEMA', action: 'Check Hb and BP at the next visit.', severity: 'watch' },
};

export const ANTENATAL_QUESTIONS: Question[] = [
  { id: 'q01', section: 'Identity', en: "What is the mother's name and age?", questionKn: 'ತಾಯಿಯ ಹೆಸರು ಮತ್ತು ವಯಸ್ಸು ಎಷ್ಟು?', questionHi: 'माता का नाम और उम्र बताइए दीदी', dangerTrigger: false, sampleNormal: 'Lakshmi D., 26' },
  { id: 'q02', section: 'Pregnancy', en: 'When was the last period or due date? How many months or weeks now?', questionKn: 'ಕೊನೆಯ ಮುಟ್ಟಿನ ದಿನಾಂಕ ಅಥವಾ ಹೆರಿಗೆಯ ಅಂದಾಜು ದಿನಾಂಕ ಯಾವಾಗ? ಈಗ ಎಷ್ಟು ತಿಂಗಳು ಅಥವಾ ವಾರಗಳು?', questionHi: 'आखिरी माहवारी की तारीख या प्रसव की संभावित तारीख कब है? अभी कितने महीने या हफ्ते हैं?', dangerTrigger: false, sampleNormal: '7 months (about 30 weeks)' },
  { id: 'q03', section: 'Pregnancy', en: 'When was the last menstrual period?', questionKn: 'ಕೊನೆಯ ಮುಟ್ಟಿನ ದಿನಾಂಕ ಯಾವಾಗ?', questionHi: 'आखिरी माहवारी की तारीख कब थी?', dangerTrigger: false, sampleNormal: 'Not needed: months already known' },
  { id: 'q04', section: 'Pregnancy', en: 'Which pregnancy is this? How many children were born before?', questionKn: 'ಇದು ಎಷ್ಟನೇ ಗರ್ಭಧಾರಣೆ? ಈ ಮೊದಲು ಎಷ್ಟು ಮಕ್ಕಳು ಜನಿಸಿದ್ದಾರೆ?', questionHi: 'यह कौन सा गर्भ है? पहले कितने बच्चे जन्मे हैं?', dangerTrigger: false, sampleNormal: '2nd pregnancy, 1 child' },
  { id: 'q05', section: 'Pregnancy', en: 'Any C-section, heavy bleeding or other problems in past deliveries?', questionKn: 'ಹಿಂದಿನ ಹೆರಿಗೆಗಳಲ್ಲಿ ಸಿ-ಸೆಕ್ಷನ್, ಅಧಿಕ ರಕ್ತಸ್ರಾವ, ಅಥವಾ ಬೇರೆ ತೊಂದರೆಗಳು ಆಗಿದ್ದವಾ?', questionHi: 'पहले के प्रसव में सी-सेक्शन, अधिक रक्तस्राव, या कोई और समस्या हुई थी?', dangerTrigger: false, sampleNormal: 'No problems' },
  { id: 'q21', section: 'Pregnancy', en: 'Is this a natural pregnancy or after IVF treatment?', questionKn: 'ಇದು ನೈಸರ್ಗಿಕ ಗರ್ಭಧಾರಣೆಯಾ ಅಥವಾ IVF ಚಿಕಿತ್ಸೆ ಪಡೆದಿದ್ದಾರಾ?', questionHi: 'यह प्राकृतिक गर्भ है या आई.वी.एफ इलाज करवाया था?', dangerTrigger: false, sampleNormal: 'Natural' },
  { id: 'q06', section: 'Vitals', en: "What is today's blood pressure reading?", questionKn: 'ತಾಯಿಯ ಇಂದಿನ ಬಿಪಿ ಎಷ್ಟು ದಾಖಲಾಗಿದೆ?', questionHi: 'आज का रक्तचाप कितना दर्ज हुआ है?', dangerTrigger: true, rule: 'PE-01', sampleNormal: '118/76 mmHg', sampleDanger: '148/96 mmHg' },
  { id: 'q07', section: 'Vitals', en: "What is the mother's weight in kg?", questionKn: 'ತಾಯಿಯ ತೂಕ ಎಷ್ಟು ಕೆಜಿ ಇದೆ?', questionHi: 'माता का वजन कितने किलो है?', dangerTrigger: false, sampleNormal: '58 kg' },
  { id: 'q08', section: 'Vitals', en: 'Is she taking her iron tablets every day?', questionKn: 'ಕಬ್ಬಿಣಾಂಶ ಗುಳಿಗೆ ತಪ್ಪದೆ ತಿನ್ನುತ್ತಿದ್ದಾಳಾ?', questionHi: 'आयरन की गोली रोज बिना चूके खा रही है क्या?', dangerTrigger: false, sampleNormal: 'Yes, every day' },
  { id: 'q24', section: 'Vitals', en: 'Any paleness in eyes or nails, or swelling in the feet?', questionKn: 'ಕಣ್ಣು ಅಥವಾ ಉಗುರುಗಳಲ್ಲಿ ರಕ್ತಹೀನತೆ (ಬಿಳುಪು), ಅಥವಾ ಪಾದಗಳಲ್ಲಿ ಊತ ಕಂಡುಬಂದಿದೆಯಾ?', questionHi: 'आँख या नाखूनों में खून की कमी, या पैरों में सूजन दिखी है क्या?', dangerTrigger: true, rule: 'AN-01', sampleNormal: 'No', sampleDanger: 'Both feet swollen since last week' },
  { id: 'q25', section: 'Vitals', en: "Was the baby's heart rate (FHR) checked? What was it?", questionKn: 'ಮಗುವಿನ ಹೃದಯ ಬಡಿತ (FHR) ಪರೀಕ್ಷೆ ಮಾಡಲಾಗಿದೆಯಾ? ಎಷ್ಟಿದೆ?', questionHi: 'बच्चे की धड़कन (एफ.एच.आर) जांची क्या, कितनी है?', dangerTrigger: false, sampleNormal: 'Yes, 140 per minute' },
  { id: 'q09', section: 'Danger signs', en: 'Any severe headache, blurred vision, or swelling of face, hands or feet?', questionKn: 'ತೀವ್ರ ತಲೆನೋವು, ಕಣ್ಣು ಮಂಜಾಗುವುದು, ಅಥವಾ ಮುಖ ಕೈಕಾಲುಗಳಲ್ಲಿ ಊತ ಇದೆಯಾ?', questionHi: 'तेज सिरदर्द, धुंधला दिखना, या चेहरे-हाथ-पैरों में सूजन है क्या?', dangerTrigger: true, rule: 'PE-03', sampleNormal: 'No', sampleDanger: 'Severe headache for 2 days, blurred vision' },
  { id: 'q10', section: 'Danger signs', en: 'Is there any stomach pain, bleeding, or leaking of water now?', questionKn: 'ಈಗ ತಾಯಿಗೆ ಹೊಟ್ಟೆ ನೋವು, ರಕ್ತಸ್ರಾವ, ಅಥವಾ ನೀರಿನಂಶ ಹೋಗುವ ತೊಂದರೆ ಇದೆಯಾ?', questionHi: 'अभी पेट दर्द, रक्तस्राव, या पानी जाने की तकलीफ है क्या?', dangerTrigger: true, rule: 'HM-01', sampleNormal: 'No', sampleDanger: 'Spotting since this morning' },
  { id: 'q11', section: 'Danger signs', en: 'Has she had any fits or convulsions?', questionKn: 'ಫಿಟ್ಸ್ ಅಥವಾ ಸೆಳೆತ ಆಗಿದೆಯ?', questionHi: 'दौरा या झटके आए हैं क्या?', dangerTrigger: true, rule: 'EC-01', sampleNormal: 'No', sampleDanger: 'Yes, one fit last night' },
  { id: 'q12', section: 'Danger signs', en: "Can she feel the baby's movements properly?", questionKn: 'ಮಗುವಿನ ಚಲನೆ ಸರಿಯಾಗಿ ಗೊತ್ತಾಗುತ್ತಿದೆಯಾ?', questionHi: 'बच्चे की हलचल ठीक से महसूस हो रही है क्या?', dangerTrigger: true, rule: 'FM-01', sampleNormal: 'Yes', sampleDanger: 'Fewer movements since yesterday' },
  { id: 'q18', section: 'Danger signs', en: 'Any fever, severe vomiting, or difficulty breathing?', questionKn: 'ಜ್ವರ, ತೀವ್ರ ವಾಂತಿ, ಅಥವಾ ಉಸಿರಾಟದ ತೊಂದರೆ ಏನಾದರೂ ಇದೆಯಾ?', questionHi: 'बुखार, तेज उल्टी, या सांस लेने में कोई तकलीफ है क्या?', dangerTrigger: true, rule: 'IN-01', sampleNormal: 'No', sampleDanger: 'Fever for 3 days' },
  { id: 'q19', section: 'History', en: 'Has she had sugar, BP, thyroid or fits problems from before?', questionKn: 'ತಾಯಿಗೆ ಮೊದಲಿನಿಂದ ಶುಗರ್, ಬಿಪಿ, ಥೈರಾಯ್ಡ್, ಅಥವಾ ಫಿಟ್ಸ್ ತೊಂದರೆ ಇದೆಯಾ?', questionHi: 'माता को पहले से शुगर, बीपी, थायरॉइड, या मिर्गी की समस्या है क्या?', dangerTrigger: false, sampleNormal: 'No' },
  { id: 'q20', section: 'History', en: 'Is she taking any medicines now? Any medicine allergy?', questionKn: 'ಈಗ ಯಾವುದಾದರೂ ಮಾತ್ರೆಗಳನ್ನು ತೆಗೆದುಕೊಳ್ಳುತ್ತಿದ್ದಾರಾ? ಯಾವುದಾದರೂ ಔಷಧಿಯಿಂದ ಅಲರ್ಜಿ ಇದೆಯಾ?', questionHi: 'अभी कोई दवाइयां ले रही हैं? किसी दवाई से एलर्जी है क्या?', dangerTrigger: false, sampleNormal: 'Iron and calcium. No allergies' },
  { id: 'q13', section: 'Scans & labs', en: 'Has a scan been done (BRIMS or taluk hospital)?', questionKn: 'ಸ್ಕ್ಯಾನ್ ಮಾಡಿದೆಯ? (ಬಿ.ಆರ್.ಐ.ಎಂ.ಎಸ್ ಅಥವಾ ತಾಲೂಕು ಆಸ್ಪತ್ರೆ)', questionHi: 'स्कैन करवाया है क्या? (बी.आर.आई.एम.एस या तालुक अस्पताल में)', dangerTrigger: false, sampleNormal: 'Yes, at the taluk hospital' },
  { id: 'q14', section: 'Scans & labs', en: 'Has she had both tetanus injections?', questionKn: 'ಎರಡು ಧನುರ್ವಾತ ಚುಚ್ಚುಮದ್ದು ತೆಗೆದೆಯ?', questionHi: 'धनुर्वात के दोनों इंजेक्शन लगवाए हैं क्या?', dangerTrigger: false, sampleNormal: 'Yes, both' },
  { id: 'q26', section: 'Scans & labs', en: 'What were the latest haemoglobin (Hb) and sugar results?', questionKn: 'ಇತ್ತೀಚಿನ ರಕ್ತ ಪರೀಕ್ಷೆಯಲ್ಲಿ ಹಿಮೋಗ್ಲೋಬಿನ್ (Hb) ಮತ್ತು ಶುಗರ್ ವರದಿ ಎಷ್ಟಿದೆ?', questionHi: 'हाल की जांच में हीमोग्लोबिन और शुगर की रिपोर्ट कितनी है?', dangerTrigger: false, sampleNormal: 'Hb 11.2, sugar normal' },
  { id: 'q15', section: 'Birth plan', en: 'Will a doctor and nurse be at the hospital for the delivery?', questionKn: 'ಪ್ರಸವಕ್ಕೆ ಆಸ್ಪತ್ರೆಯಲ್ಲಿ ಡಾಕ್ಟರ್ ನರ್ಸ್ ಇದ್ದಾರಾ?', questionHi: 'प्रसव के लिए अस्पताल में डॉक्टर नर्स मौजूद हैं क्या?', dangerTrigger: false, sampleNormal: 'Yes' },
  { id: 'q16', section: 'Birth plan', en: 'Is there a way to call the 108 ambulance?', questionKn: 'ನೂರನೆಂಟು (108) ಗಾಡಿ ಕರೆ ಮಾಡುವ ವ್ಯವಸ್ಥೆ ಇದೆಯ?', questionHi: 'एक सौ आठ (108) गाड़ी बुलाने की व्यवस्था है क्या?', dangerTrigger: false, sampleNormal: 'Yes' },
  { id: 'q17', section: 'Close', en: 'Thank you. All details are recorded and will be sent to the PHC doctor.', questionKn: 'ಧನ್ಯವಾದಗಳು. ಎಲ್ಲಾ ವಿವರಗಳನ್ನು ಯಶಸ್ವಿಯಾಗಿ ದಾಖಲಿಸಲಾಗಿದೆ. ನಾನು ಈ ಮಾಹಿತಿಯನ್ನು ಪ್ರಾಥಮಿಕ ಆರೋಗ್ಯ ಕೇಂದ್ರದ ವೈದ್ಯರಿಗೆ ಕಳುಹಿಸುತ್ತಿದ್ದೇನೆ.', questionHi: 'धन्यवाद। सभी विवरण सफलतापूर्वक दर्ज कर लिए गए हैं। मैं यह जानकारी डॉक्टर को भेज रही हूँ।', dangerTrigger: false, sampleNormal: 'Summary sent' },
];

export function questionText(q: Question, lang: Lang) {
  return lang === 'kn' ? q.questionKn : lang === 'hi' ? q.questionHi : q.en;
}

export type AnswerRecord = { value: string; danger: boolean };

/** Parse "148/96 mmHg" -> [148, 96]. */
function parseBp(v?: string): [number, number] | null {
  const m = v?.match(/(\d{2,3})\s*\/\s*(\d{2,3})/);
  return m ? [Number(m[1]), Number(m[2])] : null;
}

export type RuleHit = { code: RuleCode; line: string };

/**
 * Deterministic evaluation of the answers. Same input, same output; every
 * hit carries a printable trace line for the audit box.
 */
export function evaluate(answers: Record<string, AnswerRecord>): RuleHit[] {
  const hits: RuleHit[] = [];
  const bp = parseBp(answers.q06?.value);
  if (bp && (bp[0] >= 160 || bp[1] >= 110)) hits.push({ code: 'PE-02', line: `BP ${bp[0]}/${bp[1]} >= 160/110          TRUE` });
  else if (bp && (bp[0] >= 140 || bp[1] >= 90)) hits.push({ code: 'PE-01', line: `BP ${bp[0]}/${bp[1]} >= 140/90           TRUE` });
  const pe3 = !!answers.q09?.danger;
  if (pe3) hits.push({ code: 'PE-03', line: 'HEADACHE|VISION|SWELLING     TRUE' });
  if (pe3 && hits.some((h) => h.code === 'PE-01' || h.code === 'PE-02'))
    hits.push({ code: 'PE-04', line: 'PE-01 AND PE-03  -> HIGH_RISK, REFER NOW' });
  for (const [qid, code] of [
    ['q11', 'EC-01'],
    ['q10', 'HM-01'],
    ['q12', 'FM-01'],
    ['q18', 'IN-01'],
    ['q24', 'AN-01'],
  ] as const) {
    if (answers[qid]?.danger) hits.push({ code, line: `${RULES[code].logic.padEnd(28).slice(0, 28)} TRUE` });
  }
  return hits;
}

export function isHighRisk(hits: RuleHit[]) {
  return hits.some((h) => RULES[h.code].severity !== 'watch');
}

// What Sakhi says after each answer, in the conversation screen.
export const SAKHI_REPLIES: Record<Lang, { ok: string; danger: string; done: string }> = {
  en: {
    ok: 'Thank you, noted.',
    danger: 'This is a danger sign. I have flagged it for the doctor.',
    done: 'All 24 questions are done. I am preparing the summary.',
  },
  kn: {
    ok: 'ಧನ್ಯವಾದಗಳು, ದಾಖಲಿಸಿಕೊಂಡೆ.',
    danger: 'ಇದು ಅಪಾಯದ ಲಕ್ಷಣ. ವೈದ್ಯರಿಗಾಗಿ ಗುರುತಿಸಿದ್ದೇನೆ.',
    done: 'ಎಲ್ಲಾ 24 ಪ್ರಶ್ನೆಗಳು ಮುಗಿದಿವೆ. ಸಾರಾಂಶ ಸಿದ್ಧಪಡಿಸುತ್ತಿದ್ದೇನೆ.',
  },
  hi: {
    ok: 'धन्यवाद, दर्ज कर लिया।',
    danger: 'यह खतरे का संकेत है। मैंने डॉक्टर के लिए इसे चिह्नित कर दिया है।',
    done: 'सभी 24 प्रश्न पूरे हो गए। मैं सारांश तैयार कर रही हूँ।',
  },
};
