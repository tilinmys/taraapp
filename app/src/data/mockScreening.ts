// THE ANTENATAL QUESTIONNAIRE + DETERMINISTIC RULES.
//
// Built on the MCP card (Mother and Child Protection card) and the ICMR
// antenatal workflow, as three voice questionnaires, one per visit:
//   1 first visit    registration: who she is, pregnancy history, health
//                    conditions, this pregnancy so far (21 questions)
//   2 second visit   second trimester: what changed since the last check-up,
//                    danger signs, baby's movements, medicines, tests (9)
//   3 third visit    third trimester: changes, movements, tummy size, danger
//                    signs, medicines, birth preparedness (11)
// Sakhi asks ONLY what the woman can report herself. What needs a
// measurement, an examination or a lab report (BP, weight, Hb, pallor,
// oedema, blood group, urine, scans) is entered by the ASHA / health worker
// in the clinical entry (data/clinical.ts), with who entered it, when and
// from what source.
//
// Rules are a demo labelling of the real triage logic (sakhi_triage.py: BP
// >= 140/90 is high risk, >= 160/110 severe; danger symptoms escalate;
// reduced movement counts from 28 weeks). They are evaluated from the DANGER
// SIGNS an answer reported (whichever question it answered) and the clinical
// entry, so every flag traces to a sign or a measurement. Clinical content
// and the Kannada / Hindi wording must be reviewed by clinicians and native
// speakers before any real use.

import type { ClinicalFacts } from './clinical';

export type Lang = 'en' | 'kn' | 'hi';

export type RuleCode = 'PE-01' | 'PE-02' | 'PE-03' | 'PE-04' | 'EC-01' | 'HM-01' | 'FM-01' | 'IN-01' | 'AN-01';

/** The visit a questionnaire is for (MCP card / ICMR ANC schedule). */
export type Visit = 1 | 2 | 3;

/**
 * What a woman can report (patient-reported, NOT clinically verified: her
 * "my feet are swollen" is a reported sign; oedema is an examination finding
 * in the clinical entry).
 */
export type Sign =
  | 'headache' | 'vision' | 'swellFace' | 'swellFeet' | 'pallor' | 'abdPain' | 'bleeding' | 'leaking' | 'fits'
  | 'fever' | 'vomiting' | 'breathless' | 'discharge' | 'tired' | 'palpitations' | 'reducedMovement';

/** Signs that STOP the questionnaire: the ASHA arranges urgent assessment before anything else. */
export const URGENT_SIGNS: ReadonlySet<Sign> = new Set(['headache', 'vision', 'swellFace', 'abdPain', 'bleeding', 'leaking', 'fits', 'breathless', 'reducedMovement']);
/** Signs recorded for the doctor to see, with no alarm (watch). */
export const WATCH_SIGNS: ReadonlySet<Sign> = new Set(['swellFeet', 'pallor', 'discharge', 'tired', 'palpitations']);

export const SIGN_RULE: Partial<Record<Sign, RuleCode>> = {
  headache: 'PE-03', vision: 'PE-03', swellFace: 'PE-03',
  bleeding: 'HM-01', leaking: 'HM-01', abdPain: 'HM-01',
  fits: 'EC-01',
  fever: 'IN-01', vomiting: 'IN-01', breathless: 'IN-01',
  reducedMovement: 'FM-01',
  swellFeet: 'AN-01', pallor: 'AN-01', tired: 'AN-01', palpitations: 'AN-01',
};

export type Question = {
  id: string;
  section: string;
  en: string;
  questionKn: string;
  questionHi: string;
  /** Can an answer to this question be a danger sign? (patience at the end of the answer, demo danger answers) */
  dangerTrigger: boolean;
  /** The rule a flagged answer raises when no sign was named ("yes", "don't know"). */
  rule?: RuleCode;
  /** Demo answers for the simulator bar. */
  sampleNormal: string;
  sampleDanger?: string;
  sampleSigns?: Sign[];
};

export const RULES: Record<RuleCode, { title: string; logic: string; action: string; severity: 'refer' | 'emergency' | 'watch' }> = {
  'PE-01': { title: 'Raised blood pressure', logic: 'BP >= 140/90 (measured)', action: 'Re-check BP and refer to PHC today.', severity: 'refer' },
  'PE-02': { title: 'Severe blood pressure', logic: 'BP >= 160/110 (measured)', action: 'Refer to PHC immediately.', severity: 'emergency' },
  'PE-03': { title: 'Pre-eclampsia symptoms', logic: 'HEADACHE | BLURRED_VISION | FACE_HAND_SWELLING', action: 'Check BP now and refer to PHC today.', severity: 'refer' },
  'PE-04': { title: 'Severe pre-eclampsia risk', logic: 'PE-01 AND PE-03', action: 'Refer to PHC immediately. Call 108 if she has a fit.', severity: 'emergency' },
  'EC-01': { title: 'Fits / convulsions', logic: 'CONVULSIONS == TRUE', action: 'Emergency: call 108 now.', severity: 'emergency' },
  'HM-01': { title: 'Bleeding, leaking fluid or abdominal pain', logic: 'BLEEDING | LEAKING_FLUID | ABDOMINAL_PAIN', action: 'Refer to hospital immediately.', severity: 'emergency' },
  'FM-01': { title: 'Reduced baby movements', logic: 'FETAL_MOVEMENT == REDUCED (>= 28w)', action: 'Refer to PHC today for a check.', severity: 'refer' },
  'IN-01': { title: 'Fever, vomiting or breathlessness', logic: 'FEVER | SEVERE_VOMITING | BREATHLESSNESS', action: 'Refer to PHC today.', severity: 'refer' },
  'AN-01': { title: 'Anaemia or swelling signs', logic: 'PALLOR | OEDEMA (exam) | TIRED | PALPITATIONS', action: 'Check Hb and BP at this visit.', severity: 'watch' },
};

// One line per question: scripts/prerecord_sakhi.py reads id, questionKn and
// questionHi from here (keep them on the same line, in this order).
export const QUESTION_BANK: Question[] = [
  // ---- first visit: personal and family information -----------------------------
  { id: 'name', section: 'Personal', en: 'What is your full name?', questionKn: 'ನಿಮ್ಮ ಪೂರ್ಣ ಹೆಸರು ಏನು?', questionHi: 'आपका पूरा नाम क्या है?', dangerTrigger: false, sampleNormal: 'Lakshmi D.' },
  { id: 'age', section: 'Personal', en: 'How old are you?', questionKn: 'ನಿಮ್ಮ ವಯಸ್ಸು ಎಷ್ಟು?', questionHi: 'आपकी उम्र कितनी है?', dangerTrigger: false, sampleNormal: '26 years' },
  { id: 'village', section: 'Personal', en: 'Which village or area do you live in?', questionKn: 'ನೀವು ಯಾವ ಊರು ಅಥವಾ ಪ್ರದೇಶದಲ್ಲಿ ವಾಸಿಸುತ್ತೀರಿ?', questionHi: 'आप किस गाँव या इलाके में रहती हैं?', dangerTrigger: false, sampleNormal: 'Kamthana' },
  { id: 'firstPregnancy', section: 'Personal', en: 'Is this your first pregnancy?', questionKn: 'ಇದು ನಿಮ್ಮ ಮೊದಲ ಗರ್ಭಧಾರಣೆಯೇ?', questionHi: 'क्या यह आपका पहला गर्भ है?', dangerTrigger: false, sampleNormal: 'No' },
  { id: 'prevPregnancies', section: 'Personal', en: 'How many times have you been pregnant before this pregnancy?', questionKn: 'ಈ ಗರ್ಭಧಾರಣೆಗೆ ಮೊದಲು ನೀವು ಎಷ್ಟು ಬಾರಿ ಗರ್ಭಿಣಿಯಾಗಿದ್ದೀರಿ?', questionHi: 'इस गर्भ से पहले आप कितनी बार गर्भवती हुई हैं?', dangerTrigger: false, sampleNormal: '1 time before' },
  // ---- first visit: pregnancy history ---------------------------------------------
  { id: 'lmp', section: 'Pregnancy history', en: 'Do you remember the first day of your last menstrual period?', questionKn: 'ನಿಮ್ಮ ಕೊನೆಯ ಮುಟ್ಟಿನ ಮೊದಲ ದಿನ ನೆನಪಿದೆಯೇ?', questionHi: 'क्या आपको अपनी आखिरी माहवारी का पहला दिन याद है?', dangerTrigger: false, sampleNormal: 'LMP 12/3' },
  { id: 'regularPeriods', section: 'Pregnancy history', en: 'Were your periods usually regular before this pregnancy?', questionKn: 'ಈ ಗರ್ಭಧಾರಣೆಗೆ ಮೊದಲು ನಿಮ್ಮ ಮುಟ್ಟು ಸಾಮಾನ್ಯವಾಗಿ ಸರಿಯಾದ ಸಮಯಕ್ಕೆ ಆಗುತ್ತಿತ್ತೇ?', questionHi: 'क्या इस गर्भ से पहले आपकी माहवारी आमतौर पर समय पर आती थी?', dangerTrigger: false, sampleNormal: 'Yes, regular' },
  { id: 'confirmed', section: 'Pregnancy history', en: 'Have you visited a health centre or taken a pregnancy test to confirm this pregnancy?', questionKn: 'ಈ ಗರ್ಭಧಾರಣೆಯನ್ನು ಖಚಿತಪಡಿಸಲು ಆರೋಗ್ಯ ಕೇಂದ್ರಕ್ಕೆ ಹೋಗಿದ್ದೀರಾ ಅಥವಾ ಗರ್ಭ ಪರೀಕ್ಷೆ ಮಾಡಿಸಿದ್ದೀರಾ?', questionHi: 'क्या इस गर्भ की पुष्टि के लिए आप स्वास्थ्य केंद्र गई हैं या गर्भ जांच करवाई है?', dangerTrigger: false, sampleNormal: 'Yes, at the PHC' },
  { id: 'deliveries', section: 'Pregnancy history', en: 'Have you had any previous deliveries? If yes, how many?', questionKn: 'ಇದಕ್ಕೂ ಮೊದಲು ನಿಮಗೆ ಹೆರಿಗೆ ಆಗಿದೆಯೇ? ಆಗಿದ್ದರೆ, ಎಷ್ಟು?', questionHi: 'क्या पहले आपका प्रसव हुआ है? अगर हाँ, तो कितने?', dangerTrigger: false, sampleNormal: '1 delivery' },
  { id: 'losses', section: 'Pregnancy history', en: 'Have you had any miscarriages, abortions or stillbirths in the past?', questionKn: 'ಹಿಂದೆ ನಿಮಗೆ ಗರ್ಭಪಾತ, ಗರ್ಭ ತೆಗೆಸುವುದು ಅಥವಾ ಸತ್ತ ಮಗುವಿನ ಜನನ ಆಗಿದೆಯೇ?', questionHi: 'क्या पहले कभी आपका गर्भपात, गर्भ गिरवाना या मृत बच्चे का जन्म हुआ है?', dangerTrigger: false, sampleNormal: 'No' },
  { id: 'pastProblems', section: 'Pregnancy history', en: 'Were there any problems during your previous pregnancies or deliveries?', questionKn: 'ಹಿಂದಿನ ಗರ್ಭಧಾರಣೆ ಅಥವಾ ಹೆರಿಗೆಗಳಲ್ಲಿ ಯಾವುದಾದರೂ ತೊಂದರೆ ಆಗಿತ್ತೇ?', questionHi: 'क्या पिछले गर्भ या प्रसव में कोई परेशानी हुई थी?', dangerTrigger: false, sampleNormal: 'No problems' },
  { id: 'csection', section: 'Pregnancy history', en: 'Have you ever delivered a baby through an operation (C-section)?', questionKn: 'ಹಿಂದೆ ಎಂದಾದರೂ ಆಪರೇಷನ್ (ಸಿ-ಸೆಕ್ಷನ್) ಮೂಲಕ ಹೆರಿಗೆ ಆಗಿದೆಯೇ?', questionHi: 'क्या कभी आपका प्रसव ऑपरेशन (सी-सेक्शन) से हुआ है?', dangerTrigger: false, sampleNormal: 'No' },
  // ---- first visit: existing health conditions -------------------------------------
  { id: 'conditions', section: 'Health conditions', en: 'Do you have any health problems such as high blood pressure, diabetes, heart disease, asthma or thyroid problems?', questionKn: 'ನಿಮಗೆ ಬಿಪಿ, ಶುಗರ್, ಹೃದಯದ ಕಾಯಿಲೆ, ಅಸ್ತಮಾ ಅಥವಾ ಥೈರಾಯ್ಡ್ ತೊಂದರೆ ಏನಾದರೂ ಇದೆಯೇ?', questionHi: 'क्या आपको बीपी, शुगर, दिल की बीमारी, दमा या थायरॉइड जैसी कोई समस्या है?', dangerTrigger: false, sampleNormal: 'No' },
  { id: 'longIllness', section: 'Health conditions', en: 'Have you ever been treated for tuberculosis (TB), epilepsy or any other long-term illness?', questionKn: 'ಎಂದಾದರೂ ಟಿಬಿ (ಕ್ಷಯ), ಅಪಸ್ಮಾರ ಅಥವಾ ಬೇರೆ ಯಾವುದಾದರೂ ದೀರ್ಘಕಾಲದ ಕಾಯಿಲೆಗೆ ಚಿಕಿತ್ಸೆ ಪಡೆದಿದ್ದೀರಾ?', questionHi: 'क्या कभी आपका टीबी, मिर्गी या किसी और लंबी बीमारी का इलाज हुआ है?', dangerTrigger: false, sampleNormal: 'No' },
  { id: 'medicines', section: 'Health conditions', en: 'Are you currently taking any medicines prescribed by a doctor?', questionKn: 'ಈಗ ವೈದ್ಯರು ಕೊಟ್ಟ ಯಾವುದಾದರೂ ಔಷಧಿ ತೆಗೆದುಕೊಳ್ಳುತ್ತಿದ್ದೀರಾ?', questionHi: 'क्या आप अभी डॉक्टर की दी हुई कोई दवाई ले रही हैं?', dangerTrigger: false, sampleNormal: 'Iron and calcium' },
  { id: 'allergy', section: 'Health conditions', en: 'Do you have any allergies to medicines?', questionKn: 'ನಿಮಗೆ ಯಾವುದಾದರೂ ಔಷಧಿಯಿಂದ ಅಲರ್ಜಿ ಇದೆಯೇ?', questionHi: 'क्या आपको किसी दवाई से एलर्जी है?', dangerTrigger: false, sampleNormal: 'No allergies' },
  { id: 'transfusion', section: 'Health conditions', en: 'Have you ever received a blood transfusion?', questionKn: 'ನಿಮಗೆ ಎಂದಾದರೂ ರಕ್ತ ಹಾಕಿದ್ದಾರೆಯೇ (ರಕ್ತ ವರ್ಗಾವಣೆ)?', questionHi: 'क्या आपको कभी खून चढ़ाया गया है?', dangerTrigger: false, sampleNormal: 'No' },
  // ---- first visit: this pregnancy and lifestyle --------------------------------------
  { id: 'bleedDischarge', section: 'This pregnancy', en: 'Since becoming pregnant, have you experienced any bleeding or unusual discharge?', questionKn: 'ಗರ್ಭಿಣಿಯಾದ ನಂತರ ನಿಮಗೆ ರಕ್ತಸ್ರಾವ ಅಥವಾ ಅಸಾಮಾನ್ಯ ಸ್ರಾವ (ಬಿಳಿ ಮುಟ್ಟು) ಆಗಿದೆಯೇ?', questionHi: 'गर्भवती होने के बाद क्या आपको खून आना या असामान्य स्राव (सफेद पानी) हुआ है?', dangerTrigger: true, rule: 'HM-01', sampleNormal: 'No', sampleDanger: 'Spotting since this morning', sampleSigns: ['bleeding'] },
  { id: 'feverPain', section: 'Danger signs', en: 'Have you had fever, severe vomiting or abdominal pain?', questionKn: 'ನಿಮಗೆ ಜ್ವರ, ತೀವ್ರ ವಾಂತಿ ಅಥವಾ ಹೊಟ್ಟೆ ನೋವು ಆಗಿದೆಯೇ?', questionHi: 'क्या आपको बुखार, तेज उल्टी या पेट दर्द हुआ है?', dangerTrigger: true, rule: 'IN-01', sampleNormal: 'No', sampleDanger: 'Fever for 3 days', sampleSigns: ['fever'] },
  { id: 'tobacco', section: 'This pregnancy', en: 'Do you use tobacco, smoke or consume alcohol?', questionKn: 'ನೀವು ತಂಬಾಕು, ಬೀಡಿ-ಸಿಗರೇಟ್ ಅಥವಾ ಮದ್ಯ ಸೇವಿಸುತ್ತೀರಾ?', questionHi: 'क्या आप तंबाकू, बीड़ी-सिगरेट या शराब लेती हैं?', dangerTrigger: false, sampleNormal: 'No' },
  { id: 'concerns', section: 'This pregnancy', en: 'Is there anything about your health or this pregnancy that is worrying you?', questionKn: 'ನಿಮ್ಮ ಆರೋಗ್ಯ ಅಥವಾ ಈ ಗರ್ಭಧಾರಣೆಯ ಬಗ್ಗೆ ನಿಮಗೆ ಏನಾದರೂ ಚಿಂತೆ ಇದೆಯೇ?', questionHi: 'क्या आपकी सेहत या इस गर्भ को लेकर कोई बात आपको परेशान कर रही है?', dangerTrigger: false, sampleNormal: 'No' },
  // ---- second and third visits ---------------------------------------------------------
  { id: 'newProblems', section: 'Since last visit', en: 'Since your last check-up, have you experienced any new health problems?', questionKn: 'ಕಳೆದ ತಪಾಸಣೆಯ ನಂತರ ನಿಮಗೆ ಯಾವುದಾದರೂ ಹೊಸ ಆರೋಗ್ಯ ತೊಂದರೆ ಆಗಿದೆಯೇ?', questionHi: 'पिछली जांच के बाद क्या आपको कोई नई स्वास्थ्य समस्या हुई है?', dangerTrigger: false, sampleNormal: 'No' },
  { id: 'bleedFluid', section: 'Danger signs', en: 'Have you had any bleeding or unusual fluid discharge?', questionKn: 'ನಿಮಗೆ ರಕ್ತಸ್ರಾವ ಅಥವಾ ಅಸಾಮಾನ್ಯವಾಗಿ ನೀರು ಹೋಗುವುದು ಆಗಿದೆಯೇ?', questionHi: 'क्या आपको खून आना या असामान्य रूप से पानी जाना हुआ है?', dangerTrigger: true, rule: 'HM-01', sampleNormal: 'No', sampleDanger: 'Spotting since this morning', sampleSigns: ['bleeding'] },
  { id: 'bleedLeak', section: 'Danger signs', en: 'Have you experienced any vaginal bleeding or watery fluid leaking?', questionKn: 'ನಿಮಗೆ ಯೋನಿಯಿಂದ ರಕ್ತಸ್ರಾವ ಅಥವಾ ನೀರು ಸೋರುವುದು ಆಗಿದೆಯೇ?', questionHi: 'क्या आपको योनि से खून आना या पानी रिसना हुआ है?', dangerTrigger: true, rule: 'HM-01', sampleNormal: 'No', sampleDanger: 'Water leaking since last night', sampleSigns: ['leaking'] },
  { id: 'warning', section: 'Danger signs', en: 'Have you experienced severe headaches, blurred vision or sudden swelling of your face or hands?', questionKn: 'ನಿಮಗೆ ತೀವ್ರ ತಲೆನೋವು, ಕಣ್ಣು ಮಂಜಾಗುವುದು ಅಥವಾ ಮುಖ, ಕೈಗಳಲ್ಲಿ ದಿಢೀರ್ ಊತ ಆಗಿದೆಯೇ?', questionHi: 'क्या आपको तेज सिरदर्द, धुंधला दिखना या चेहरे या हाथों में अचानक सूजन हुई है?', dangerTrigger: true, rule: 'PE-03', sampleNormal: 'No', sampleDanger: 'Severe headache for 2 days, blurred vision', sampleSigns: ['headache', 'vision'] },
  { id: 'movementFelt', section: 'Baby', en: "Have you started feeling your baby's movements?", questionKn: 'ನಿಮಗೆ ಮಗುವಿನ ಚಲನೆ ಗೊತ್ತಾಗಲು ಶುರುವಾಗಿದೆಯೇ?', questionHi: 'क्या आपको बच्चे की हलचल महसूस होने लगी है?', dangerTrigger: false, sampleNormal: 'Yes' },
  { id: 'ifa', section: 'Medicines & tests', en: 'Are you taking the iron and folic acid tablets given to you?', questionKn: 'ನಿಮಗೆ ಕೊಟ್ಟ ಕಬ್ಬಿಣಾಂಶ ಮತ್ತು ಫೋಲಿಕ್ ಆಸಿಡ್ ಮಾತ್ರೆಗಳನ್ನು ತೆಗೆದುಕೊಳ್ಳುತ್ತಿದ್ದೀರಾ?', questionHi: 'क्या आप दी गई आयरन और फोलिक एसिड की गोलियां ले रही हैं?', dangerTrigger: false, sampleNormal: 'Yes, every day' },
  { id: 'calcium', section: 'Medicines & tests', en: 'Are you taking the calcium tablets prescribed to you?', questionKn: 'ನಿಮಗೆ ಬರೆದುಕೊಟ್ಟ ಕ್ಯಾಲ್ಸಿಯಂ ಮಾತ್ರೆಗಳನ್ನು ತೆಗೆದುಕೊಳ್ಳುತ್ತಿದ್ದೀರಾ?', questionHi: 'क्या आप लिखी गई कैल्शियम की गोलियां ले रही हैं?', dangerTrigger: false, sampleNormal: 'Yes, every day' },
  { id: 'testsDone', section: 'Medicines & tests', en: 'Have you completed the blood and urine tests advised during your last visit?', questionKn: 'ಕಳೆದ ಭೇಟಿಯಲ್ಲಿ ಹೇಳಿದ ರಕ್ತ ಮತ್ತು ಮೂತ್ರ ಪರೀಕ್ಷೆಗಳನ್ನು ಮಾಡಿಸಿದ್ದೀರಾ?', questionHi: 'क्या आपने पिछली बार बताई गई खून और पेशाब की जांच करवा ली है?', dangerTrigger: false, sampleNormal: 'Yes' },
  { id: 'followedAdvice', section: 'Medicines & tests', en: 'Have you visited the doctor or health centre as advised during your previous check-up?', questionKn: 'ಕಳೆದ ತಪಾಸಣೆಯಲ್ಲಿ ಹೇಳಿದಂತೆ ವೈದ್ಯರನ್ನು ಅಥವಾ ಆರೋಗ್ಯ ಕೇಂದ್ರವನ್ನು ಭೇಟಿ ಮಾಡಿದ್ದೀರಾ?', questionHi: 'क्या पिछली जांच में बताए अनुसार आप डॉक्टर या स्वास्थ्य केंद्र गई थीं?', dangerTrigger: false, sampleNormal: 'Yes' },
  { id: 'movement', section: 'Baby', en: 'Is your baby moving as usual?', questionKn: 'ನಿಮ್ಮ ಮಗು ಎಂದಿನಂತೆ ಚಲಿಸುತ್ತಿದೆಯೇ?', questionHi: 'क्या आपका बच्चा पहले की तरह हिल-डुल रहा है?', dangerTrigger: true, rule: 'FM-01', sampleNormal: 'Yes, moving normally', sampleDanger: 'Fewer movements since yesterday', sampleSigns: ['reducedMovement'] },
  { id: 'movementLess', section: 'Baby', en: "Have you noticed a decrease or absence of your baby's movements?", questionKn: 'ಮಗುವಿನ ಚಲನೆ ಕಡಿಮೆಯಾಗಿದೆ ಅಥವಾ ನಿಂತುಹೋಗಿದೆ ಎಂದು ಅನಿಸಿದೆಯೇ?', questionHi: 'क्या आपको लगा कि बच्चे की हलचल कम हो गई है या बंद हो गई है?', dangerTrigger: true, rule: 'FM-01', sampleNormal: 'No', sampleDanger: 'Yes, much less since yesterday', sampleSigns: ['reducedMovement'] },
  { id: 'tummySize', section: 'Baby', en: 'Have you noticed any changes in your tummy size as your pregnancy progresses?', questionKn: 'ನಿಮ್ಮ ಗರ್ಭಧಾರಣೆ ಮುಂದುವರಿದಂತೆ ಹೊಟ್ಟೆಯ ಗಾತ್ರದಲ್ಲಿ ಯಾವುದೇ ಬದಲಾವಣೆ ಗಮನಿಸಿದ್ದೀರಾ?', questionHi: 'जैसे-जैसे आपका गर्भ बढ़ रहा है, क्या आपने पेट के आकार में कोई बदलाव देखा है?', dangerTrigger: false, sampleNormal: 'Growing normally' },
  { id: 'breathing', section: 'Danger signs', en: 'Are you experiencing difficulty breathing, unusual tiredness or palpitations?', questionKn: 'ನಿಮಗೆ ಉಸಿರಾಟದ ತೊಂದರೆ, ಅಸಾಮಾನ್ಯ ಸುಸ್ತು ಅಥವಾ ಎದೆ ಡವಡವ ಆಗುವುದು ಇದೆಯೇ?', questionHi: 'क्या आपको सांस लेने में तकलीफ, असामान्य थकान या दिल की धड़कन तेज होना महसूस हो रहा है?', dangerTrigger: true, rule: 'IN-01', sampleNormal: 'No', sampleDanger: 'Short of breath when walking', sampleSigns: ['breathless'] },
  { id: 'ironCalcium', section: 'Medicines & tests', en: 'Are you taking your prescribed iron and calcium tablets regularly?', questionKn: 'ನಿಮಗೆ ಕೊಟ್ಟ ಕಬ್ಬಿಣಾಂಶ ಮತ್ತು ಕ್ಯಾಲ್ಸಿಯಂ ಮಾತ್ರೆಗಳನ್ನು ತಪ್ಪದೆ ತೆಗೆದುಕೊಳ್ಳುತ್ತಿದ್ದೀರಾ?', questionHi: 'क्या आप दी गई आयरन और कैल्शियम की गोलियां नियमित रूप से ले रही हैं?', dangerTrigger: false, sampleNormal: 'Yes, every day' },
  { id: 'deliveryPlace', section: 'Birth plan', en: 'Have you decided where you would like to deliver your baby?', questionKn: 'ಹೆರಿಗೆ ಎಲ್ಲಿ ಮಾಡಿಸಬೇಕು ಎಂದು ನಿರ್ಧರಿಸಿದ್ದೀರಾ?', questionHi: 'क्या आपने तय कर लिया है कि प्रसव कहाँ करवाना है?', dangerTrigger: false, sampleNormal: 'Yes, at the taluk hospital' },
  { id: 'support', section: 'Birth plan', en: 'Do you have someone who can help you reach the hospital if you need medical attention?', questionKn: 'ತುರ್ತು ಸಮಯದಲ್ಲಿ ನಿಮ್ಮನ್ನು ಆಸ್ಪತ್ರೆಗೆ ಕರೆದುಕೊಂಡು ಹೋಗಲು ಸಹಾಯ ಮಾಡುವವರು ಯಾರಾದರೂ ಇದ್ದಾರೆಯೇ?', questionHi: 'अगर ज़रूरत पड़े तो क्या कोई है जो आपको अस्पताल पहुँचाने में मदद कर सके?', dangerTrigger: false, sampleNormal: 'Yes, my husband' },
  // ---- every visit ends with this line (said, not asked) --------------------------------
  { id: 'close', section: 'Close', en: 'Thank you. All details are recorded and will be sent to the PHC doctor.', questionKn: 'ಧನ್ಯವಾದಗಳು. ಎಲ್ಲಾ ವಿವರಗಳನ್ನು ಯಶಸ್ವಿಯಾಗಿ ದಾಖಲಿಸಲಾಗಿದೆ. ನಾನು ಈ ಮಾಹಿತಿಯನ್ನು ಪ್ರಾಥಮಿಕ ಆರೋಗ್ಯ ಕೇಂದ್ರದ ವೈದ್ಯರಿಗೆ ಕಳುಹಿಸುತ್ತಿದ್ದೇನೆ.', questionHi: 'धन्यवाद। सभी विवरण सफलतापूर्वक दर्ज कर लिए गए हैं। मैं यह जानकारी डॉक्टर को भेज रही हूँ।', dangerTrigger: false, sampleNormal: 'Summary sent' },
];

/** The questions of each visit, in the order Sakhi asks them (the closing line last). */
export const VISIT_QUESTIONS: Record<Visit, string[]> = {
  1: [
    'name', 'age', 'village', 'firstPregnancy', 'prevPregnancies',
    'lmp', 'regularPeriods', 'confirmed', 'deliveries', 'losses', 'pastProblems', 'csection',
    'conditions', 'longIllness', 'medicines', 'allergy', 'transfusion',
    'bleedDischarge', 'feverPain', 'tobacco', 'concerns',
    'close',
  ],
  2: ['newProblems', 'bleedFluid', 'warning', 'feverPain', 'movementFelt', 'ifa', 'calcium', 'testsDone', 'followedAdvice', 'close'],
  3: ['newProblems', 'movement', 'movementLess', 'tummySize', 'bleedLeak', 'warning', 'feverPain', 'breathing', 'ironCalcium', 'deliveryPlace', 'support', 'close'],
};

const BY_ID = new Map(QUESTION_BANK.map((q) => [q.id, q]));

/** A question by id (any visit). */
export function questionById(id: string): Question | undefined {
  return BY_ID.get(id);
}

/** The questionnaire of one visit, in asking order. */
export function questionsFor(visit: Visit): Question[] {
  return VISIT_QUESTIONS[visit].map((id) => BY_ID.get(id) as Question);
}

/**
 * The questions of a report, in asking order: its visit's questionnaire, or
 * (an old report without a visit) every question it has an answer for.
 */
export function reportQuestions(answers: Record<string, unknown>, visit?: Visit): Question[] {
  return visit ? questionsFor(visit) : QUESTION_BANK.filter((q) => q.id in answers);
}

/** Questions Sakhi ASKS in a visit (the closing line is said, not asked). */
export function questionCount(visit: Visit) {
  return VISIT_QUESTIONS[visit].length - 1;
}

/**
 * Which visit's questionnaire fits her: the first (registration) until she
 * has a check-in on record; then by trimester (MCP card / ICMR schedule):
 * up to 27 weeks the second-trimester follow-up, from 28 weeks the third.
 */
export function visitFor(m: { reports: unknown[]; months: number; weeksLabel?: string }): Visit {
  if (!m.reports.length) return 1;
  return gestationWeeks(m) >= 28 ? 3 : 2;
}

/** Completed weeks from "30w 2d" (else from the months). */
export function gestationWeeks(m: { months: number; weeksLabel?: string }) {
  const w = m.weeksLabel?.match(/(\d{1,2})\s*w/);
  return w ? Number(w[1]) : Math.round(m.months * 4.35);
}

export function questionText(q: Question, lang: Lang) {
  return lang === 'kn' ? q.questionKn : lang === 'hi' ? q.questionHi : q.en;
}

/**
 * One answer as the doctor and the rules read it. `signs`: the danger signs
 * it reported (any question: a sign said in "anything worrying you?" counts).
 */
export type AnswerRecord = {
  value: string;
  danger: boolean;
  signs?: Sign[];
  /** The ASHA corrected or filled it in after the voice check-in (who, when). */
  corrected?: { by: string; at: number };
};

export type RuleHit = { code: RuleCode; line: string };

/** Every sign reported across the answers. */
export function signsIn(answers: Record<string, AnswerRecord>): Set<Sign> {
  const out = new Set<Sign>();
  for (const a of Object.values(answers)) a?.signs?.forEach((s) => out.add(s));
  return out;
}

/** The answers that raised a rule (for the audit box). */
export function triggersOf(code: RuleCode, answers: Record<string, AnswerRecord>): string[] {
  return Object.entries(answers)
    .filter(([id, a]) => a?.signs?.some((s) => SIGN_RULE[s] === code) || (a?.danger && !a.signs?.length && questionById(id)?.rule === code))
    .map(([id]) => id);
}

const ORDER: RuleCode[] = ['EC-01', 'PE-04', 'PE-02', 'HM-01', 'PE-01', 'PE-03', 'FM-01', 'IN-01', 'AN-01'];

/**
 * Deterministic evaluation of the answers and the clinical entry. Same
 * input, same output; every hit carries a printable trace line for the
 * audit box.
 */
export function evaluate(answers: Record<string, AnswerRecord>, clinical?: ClinicalFacts): RuleHit[] {
  const hits: RuleHit[] = [];
  const signs = signsIn(answers);
  // A flagged answer with no sign named ("yes", "don't know") raises its question's rule.
  const vague = new Set<RuleCode>();
  for (const [id, a] of Object.entries(answers)) if (a?.danger && !a.signs?.length && questionById(id)?.rule) vague.add(questionById(id)!.rule!);
  const has = (code: RuleCode) => vague.has(code) || [...signs].some((s) => SIGN_RULE[s] === code);

  const bp = clinical?.bp;
  if (bp && (bp[0] >= 160 || bp[1] >= 110)) hits.push({ code: 'PE-02', line: `BP ${bp[0]}/${bp[1]} >= 160/110          TRUE` });
  else if (bp && (bp[0] >= 140 || bp[1] >= 90)) hits.push({ code: 'PE-01', line: `BP ${bp[0]}/${bp[1]} >= 140/90           TRUE` });
  const pe3 = has('PE-03');
  if (pe3) hits.push({ code: 'PE-03', line: 'HEADACHE|VISION|SWELLING     TRUE' });
  if (pe3 && hits.some((h) => h.code === 'PE-01' || h.code === 'PE-02')) hits.push({ code: 'PE-04', line: 'PE-01 AND PE-03  -> HIGH_RISK, REFER NOW' });
  for (const code of ['EC-01', 'HM-01', 'FM-01', 'IN-01'] as const) {
    if (has(code)) hits.push({ code, line: `${RULES[code].logic.padEnd(28).slice(0, 28)} TRUE` });
  }
  if (has('AN-01') || clinical?.pallor || clinical?.oedema) hits.push({ code: 'AN-01', line: `${RULES['AN-01'].logic.padEnd(28).slice(0, 28)} TRUE` });
  return hits.sort((a, b) => ORDER.indexOf(a.code) - ORDER.indexOf(b.code));
}

export function isHighRisk(hits: RuleHit[]) {
  return hits.some((h) => RULES[h.code].severity !== 'watch');
}

// What Sakhi says after each answer, in the conversation screen. `urgent`:
// a sign that stops the questionnaire (the ASHA acts before anything else).
export const SAKHI_REPLIES: Record<Lang, { ok: string; danger: string; done: string; urgent: string }> = {
  en: {
    ok: 'Noted.',
    danger: 'This is a danger sign. I have flagged it for the doctor.',
    done: 'All questions are done. I am preparing the summary.',
    urgent: 'This is a danger sign. I am pausing the questions. Please get her checked by a doctor now.',
  },
  kn: {
    ok: 'ಧನ್ಯವಾದಗಳು, ದಾಖಲಿಸಿಕೊಂಡೆ.',
    danger: 'ಇದು ಅಪಾಯದ ಲಕ್ಷಣ. ವೈದ್ಯರಿಗಾಗಿ ಗುರುತಿಸಿದ್ದೇನೆ.',
    done: 'ಎಲ್ಲಾ ಪ್ರಶ್ನೆಗಳು ಮುಗಿದಿವೆ. ಸಾರಾಂಶ ಸಿದ್ಧಪಡಿಸುತ್ತಿದ್ದೇನೆ.',
    urgent: 'ಇದು ಅಪಾಯದ ಲಕ್ಷಣ. ಪ್ರಶ್ನೆಗಳನ್ನು ನಿಲ್ಲಿಸುತ್ತಿದ್ದೇನೆ. ದಯವಿಟ್ಟು ಈಗಲೇ ವೈದ್ಯರಿಂದ ಪರೀಕ್ಷೆ ಮಾಡಿಸಿ.',
  },
  hi: {
    ok: 'धन्यवाद, दर्ज कर लिया।',
    danger: 'यह खतरे का संकेत है। मैंने डॉक्टर के लिए इसे चिह्नित कर दिया है।',
    done: 'सभी प्रश्न पूरे हो गए। मैं सारांश तैयार कर रही हूँ।',
    urgent: 'यह खतरे का संकेत है। मैं सवाल रोक रही हूँ। कृपया अभी डॉक्टर से जांच करवाएं।',
  },
};
