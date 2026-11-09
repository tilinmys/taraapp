import { useOnboarding } from '@/features/onboarding/context';

import type { Lang } from './strings';

// The mother-facing CONTENT (visits, health topics, medicines, the daily
// checklist, Tara's answers, the doctor's seeded advice) is written in
// English in data/; this table gives each sentence in Kannada and Hindi, so
// every screen she reads is in her language. Keyed by the exact English, so
// the data files keep one shape; a line not listed falls back to English
// (data/__tests__/content-i18n.test.ts fails if a mother-facing line is missing).
// Names (doctors, places), numbers and units are left as they are.
// Wording must be reviewed by native speakers before real use.

type Tr = [kn: string, hi: string];

export const CONTENT: Record<string, Tr> = {
  // ---- visits (her History tab) ----------------------------------------------
  'Medical Officer': ['ವೈದ್ಯಾಧಿಕಾರಿ', 'चिकित्सा अधिकारी'],
  'Medical officer': ['ವೈದ್ಯಾಧಿಕಾರಿ', 'चिकित्सा अधिकारी'],
  'Obstetrician · Tue & Thu': ['ಪ್ರಸೂತಿ ತಜ್ಞರು · ಮಂಗಳ ಮತ್ತು ಗುರು', 'स्त्री रोग विशेषज्ञ · मंगल और गुरु'],
  Doctor: ['ವೈದ್ಯರು', 'डॉक्टर'],
  Obstetrician: ['ಪ್ರಸೂತಿ ತಜ್ಞರು', 'स्त्री रोग विशेषज्ञ'],
  'Founder & High Risk Obstetrician': ['ಸಂಸ್ಥಾಪಕರು ಮತ್ತು ಅಧಿಕ ಅಪಾಯದ ಪ್ರಸೂತಿ ತಜ್ಞರು', 'संस्थापक और उच्च जोखिम प्रसूति विशेषज्ञ'],
  'Co-Founder & Fertility Specialist': ['ಸಹ-ಸಂಸ್ಥಾಪಕರು ಮತ್ತು ಫಲವತ್ತತೆ ತಜ್ಞರು', 'सह-संस्थापक और प्रजनन विशेषज्ञ'],
  Referral: ['ಶಿಫಾರಸು', 'रेफ़रल'],
  'Check-up': ['ತಪಾಸಣೆ', 'जांच'],
  Scan: ['ಸ್ಕ್ಯಾನ್', 'स्कैन'],
  'Lab tests': ['ಪ್ರಯೋಗಾಲಯ ಪರೀಕ್ಷೆಗಳು', 'लैब जांच'],
  BP: ['ಬಿಪಿ', 'बीपी'],
  Baby: ['ಮಗು', 'बच्चा'],
  Hb: ['Hb', 'Hb'],
  Sugar: ['ಶುಗರ್', 'शुगर'],
  'BP check. Your doctor asked for a follow-up check at the taluk hospital.': ['ಬಿಪಿ ತಪಾಸಣೆ. ತಾಲೂಕು ಆಸ್ಪತ್ರೆಯಲ್ಲಿ ಮತ್ತೊಮ್ಮೆ ತಪಾಸಣೆ ಮಾಡಿಸಲು ವೈದ್ಯರು ಹೇಳಿದ್ದಾರೆ.', 'बीपी जांच। डॉक्टर ने तालुक अस्पताल में दोबारा जांच के लिए कहा है।'],
  'Headache and blurred vision for 2 days': ['2 ದಿನಗಳಿಂದ ತಲೆನೋವು ಮತ್ತು ಕಣ್ಣು ಮಂಜಾಗುವುದು', '2 दिन से सिरदर्द और धुंधला दिखना'],
  'Both feet swollen': ['ಎರಡೂ ಪಾದಗಳಲ್ಲಿ ಊತ', 'दोनों पैरों में सूजन'],
  '1 tablet': ['1 ಮಾತ್ರೆ', '1 गोली'],
  '2 tablets': ['2 ಮಾತ್ರೆಗಳು', '2 गोलियां'],
  'Morning and night, after food': ['ಬೆಳಿಗ್ಗೆ ಮತ್ತು ರಾತ್ರಿ, ಊಟದ ನಂತರ', 'सुबह और रात, खाने के बाद'],
  'Until review': ['ಮುಂದಿನ ತಪಾಸಣೆಯವರೆಗೆ', 'अगली जांच तक'],
  'Iron + Folic acid': ['ಕಬ್ಬಿಣ + ಫೋಲಿಕ್ ಆಸಿಡ್', 'आयरन + फोलिक एसिड'],
  'After lunch': ['ಮಧ್ಯಾಹ್ನದ ಊಟದ ನಂತರ', 'दोपहर के खाने के बाद'],
  'After dinner': ['ರಾತ್ರಿ ಊಟದ ನಂತರ', 'रात के खाने के बाद'],
  'After breakfast': ['ಬೆಳಗಿನ ಉಪಾಹಾರದ ನಂತರ', 'नाश्ते के बाद'],
  'After lunch and dinner': ['ಮಧ್ಯಾಹ್ನ ಮತ್ತು ರಾತ್ರಿ ಊಟದ ನಂತರ', 'दोपहर और रात के खाने के बाद'],
  '30 days': ['30 ದಿನಗಳು', '30 दिन'],
  '60 days': ['60 ದಿನಗಳು', '60 दिन'],
  'Go to the taluk hospital today.': ['ಇಂದೇ ತಾಲೂಕು ಆಸ್ಪತ್ರೆಗೆ ಹೋಗಿ.', 'आज ही तालुक अस्पताल जाएं।'],
  'Rest on your left side.': ['ಎಡಬದಿಗೆ ಮಲಗಿ ವಿಶ್ರಾಂತಿ ಪಡೆಯಿರಿ.', 'बाईं करवट लेटकर आराम करें।'],
  'Call 108 if headache or vision gets worse.': ['ತಲೆನೋವು ಅಥವಾ ದೃಷ್ಟಿ ಹೆಚ್ಚು ತೊಂದರೆಯಾದರೆ 108ಕ್ಕೆ ಕರೆ ಮಾಡಿ.', 'सिरदर्द या नज़र और बिगड़े तो 108 पर कॉल करें।'],
  'Today, taluk hospital': ['ಇಂದು, ತಾಲೂಕು ಆಸ್ಪತ್ರೆ', 'आज, तालुक अस्पताल'],
  'Growth scan': ['ಬೆಳವಣಿಗೆ ಸ್ಕ್ಯಾನ್', 'ग्रोथ स्कैन'],
  'Growth scan: baby growing well, normal heartbeat and fluid.': ['ಬೆಳವಣಿಗೆ ಸ್ಕ್ಯಾನ್: ಮಗು ಚೆನ್ನಾಗಿ ಬೆಳೆಯುತ್ತಿದೆ, ಹೃದಯ ಬಡಿತ ಮತ್ತು ನೀರು ಸಾಮಾನ್ಯ.', 'ग्रोथ स्कैन: बच्चा अच्छे से बढ़ रहा है, धड़कन और पानी सामान्य।'],
  'Baby weight about 1.3 kg (normal)': ['ಮಗುವಿನ ತೂಕ ಸುಮಾರು 1.3 ಕೆಜಿ (ಸಾಮಾನ್ಯ)', 'बच्चे का वज़न लगभग 1.3 किलो (सामान्य)'],
  'Heartbeat 142 per minute': ['ಹೃದಯ ಬಡಿತ ನಿಮಿಷಕ್ಕೆ 142', 'धड़कन 142 प्रति मिनट'],
  'Placenta and fluid normal': ['ಮಾಸು ಮತ್ತು ನೀರು ಸಾಮಾನ್ಯ', 'नाल और पानी सामान्य'],
  'Routine check-up. Haemoglobin a little low, iron increased.': ['ನಿಯಮಿತ ತಪಾಸಣೆ. ಹಿಮೋಗ್ಲೋಬಿನ್ ಸ್ವಲ್ಪ ಕಡಿಮೆ, ಕಬ್ಬಿಣ ಮಾತ್ರೆ ಹೆಚ್ಚಿಸಲಾಗಿದೆ.', 'नियमित जांच। हीमोग्लोबिन थोड़ा कम, आयरन बढ़ाया गया।'],
  'Hb 10.1 g/dL (slightly low)': ['Hb 10.1 g/dL (ಸ್ವಲ್ಪ ಕಡಿಮೆ)', 'Hb 10.1 g/dL (थोड़ा कम)'],
  'Tetanus injection 2 given': ['ಧನುರ್ವಾತ ಚುಚ್ಚುಮದ್ದು 2 ನೀಡಲಾಗಿದೆ', 'टिटनेस का दूसरा इंजेक्शन दिया गया'],
  'Tetanus injection 1 given': ['ಧನುರ್ವಾತ ಚುಚ್ಚುಮದ್ದು 1 ನೀಡಲಾಗಿದೆ', 'टिटनेस का पहला इंजेक्शन दिया गया'],
  'Eat greens, dal and jaggery every day.': ['ಪ್ರತಿದಿನ ಸೊಪ್ಪು, ಬೇಳೆ ಮತ್ತು ಬೆಲ್ಲ ತಿನ್ನಿ.', 'रोज़ हरी सब्ज़ी, दाल और गुड़ खाएं।'],
  'Take iron with lemon water, not tea.': ['ಕಬ್ಬಿಣ ಮಾತ್ರೆಯನ್ನು ನಿಂಬೆ ನೀರಿನೊಂದಿಗೆ ತೆಗೆದುಕೊಳ್ಳಿ, ಚಹಾದೊಂದಿಗೆ ಬೇಡ.', 'आयरन नींबू पानी के साथ लें, चाय के साथ नहीं।'],
  'In 4 weeks': ['4 ವಾರಗಳಲ್ಲಿ', '4 हफ़्ते में'],
  'In 8 weeks': ['8 ವಾರಗಳಲ್ಲಿ', '8 हफ़्ते में'],
  'Lab · Hosahalli PHC': ['ಪ್ರಯೋಗಾಲಯ · ಹೊಸಹಳ್ಳಿ ಪ್ರಾ.ಆ.ಕೇಂ', 'लैब · होसहल्ली PHC'],
  'Blood and urine tests': ['ರಕ್ತ ಮತ್ತು ಮೂತ್ರ ಪರೀಕ್ಷೆಗಳು', 'खून और पेशाब की जांच'],
  'Routine blood and urine tests. Sugar and urine normal.': ['ನಿಯಮಿತ ರಕ್ತ ಮತ್ತು ಮೂತ್ರ ಪರೀಕ್ಷೆ. ಶುಗರ್ ಮತ್ತು ಮೂತ್ರ ಸಾಮಾನ್ಯ.', 'नियमित खून और पेशाब की जांच। शुगर और पेशाब सामान्य।'],
  'Blood sugar 104 mg/dL (normal)': ['ರಕ್ತದ ಸಕ್ಕರೆ 104 mg/dL (ಸಾಮಾನ್ಯ)', 'ब्लड शुगर 104 mg/dL (सामान्य)'],
  'Urine: no protein, no sugar': ['ಮೂತ್ರ: ಪ್ರೋಟೀನ್ ಇಲ್ಲ, ಸಕ್ಕರೆ ಇಲ್ಲ', 'पेशाब: प्रोटीन नहीं, शुगर नहीं'],
  'Blood group B+': ['ರಕ್ತದ ಗುಂಪು B+', 'ब्लड ग्रुप B+'],
  'All normal. Started calcium; tetanus injection 1 given.': ['ಎಲ್ಲಾ ಸಾಮಾನ್ಯ. ಕ್ಯಾಲ್ಸಿಯಂ ಶುರು; ಧನುರ್ವಾತ ಚುಚ್ಚುಮದ್ದು 1 ನೀಡಲಾಗಿದೆ.', 'सब सामान्य। कैल्शियम शुरू; टिटनेस का पहला इंजेक्शन दिया गया।'],
  'One extra meal every day.': ['ಪ್ರತಿದಿನ ಒಂದು ಹೆಚ್ಚುವರಿ ಊಟ.', 'रोज़ एक अतिरिक्त भोजन।'],
  'Next check-up in 8 weeks.': ['ಮುಂದಿನ ತಪಾಸಣೆ 8 ವಾರಗಳಲ್ಲಿ.', 'अगली जांच 8 हफ़्ते में।'],
  'Calcium 500 mg': ['ಕ್ಯಾಲ್ಸಿಯಂ 500 mg', 'कैल्शियम 500 mg'],

  // ---- health topics -------------------------------------------------------------
  Maternity: ['ಗರ್ಭಧಾರಣೆ', 'गर्भावस्था'],
  'Week by week care for you and your baby.': ['ನಿಮಗೂ ನಿಮ್ಮ ಮಗುವಿಗೂ ವಾರದಿಂದ ವಾರಕ್ಕೆ ಆರೈಕೆ.', 'आपके और आपके बच्चे के लिए हफ़्ते-दर-हफ़्ते देखभाल।'],
  'Danger signs': ['ಅಪಾಯದ ಲಕ್ಷಣಗಳು', 'खतरे के संकेत'],
  'Bleeding, severe headache, blurred vision or fits: call 108 now.': ['ರಕ್ತಸ್ರಾವ, ತೀವ್ರ ತಲೆನೋವು, ಕಣ್ಣು ಮಂಜಾಗುವುದು ಅಥವಾ ಫಿಟ್ಸ್: ಈಗಲೇ 108ಕ್ಕೆ ಕರೆ ಮಾಡಿ.', 'खून आना, तेज़ सिरदर्द, धुंधला दिखना या दौरे: अभी 108 पर कॉल करें।'],
  'Swollen face or hands, or the baby moving less: tell your ASHA today.': ['ಮುಖ ಅಥವಾ ಕೈ ಊತ, ಅಥವಾ ಮಗುವಿನ ಚಲನೆ ಕಡಿಮೆ: ಇಂದೇ ನಿಮ್ಮ ಆಶಾಗೆ ತಿಳಿಸಿ.', 'चेहरे या हाथों में सूजन, या बच्चे की हलचल कम: आज ही अपनी आशा को बताएं।'],
  'High fever or water leaking: go to the PHC today.': ['ಹೆಚ್ಚು ಜ್ವರ ಅಥವಾ ನೀರು ಸೋರುವುದು: ಇಂದೇ ಪ್ರಾ.ಆ.ಕೇಂದ್ರಕ್ಕೆ ಹೋಗಿ.', 'तेज़ बुखार या पानी रिसना: आज ही PHC जाएं।'],
  'Eating well': ['ಚೆನ್ನಾಗಿ ತಿನ್ನಿ', 'अच्छा खाना'],
  'One extra meal a day.': ['ದಿನಕ್ಕೆ ಒಂದು ಹೆಚ್ಚುವರಿ ಊಟ.', 'दिन में एक अतिरिक्त भोजन।'],
  'Greens, dal, eggs or milk daily.': ['ಪ್ರತಿದಿನ ಸೊಪ್ಪು, ಬೇಳೆ, ಮೊಟ್ಟೆ ಅಥವಾ ಹಾಲು.', 'रोज़ हरी सब्ज़ी, दाल, अंडा या दूध।'],
  'Iron tablet after food, not with tea.': ['ಕಬ್ಬಿಣ ಮಾತ್ರೆ ಊಟದ ನಂತರ, ಚಹಾದೊಂದಿಗೆ ಬೇಡ.', 'आयरन की गोली खाने के बाद, चाय के साथ नहीं।'],
  'Baby movements': ['ಮಗುವಿನ ಚಲನೆ', 'बच्चे की हलचल'],
  'From 28 weeks, count 10 kicks in 2 hours.': ['28 ವಾರಗಳಿಂದ, 2 ಗಂಟೆಗಳಲ್ಲಿ 10 ಒದೆತ ಎಣಿಸಿ.', '28 हफ़्ते से, 2 घंटे में 10 लातें गिनें।'],
  'Fewer kicks than usual? Lie on your left side and count again.': ['ಎಂದಿಗಿಂತ ಕಡಿಮೆ ಒದೆತವೇ? ಎಡಬದಿಗೆ ಮಲಗಿ ಮತ್ತೆ ಎಣಿಸಿ.', 'रोज़ से कम लातें? बाईं करवट लेटकर फिर से गिनें।'],
  'Still fewer: call your ASHA.': ['ಇನ್ನೂ ಕಡಿಮೆಯೇ: ನಿಮ್ಮ ಆಶಾಗೆ ಕರೆ ಮಾಡಿ.', 'फिर भी कम: अपनी आशा को कॉल करें।'],
  'Sleep and rest': ['ನಿದ್ರೆ ಮತ್ತು ವಿಶ್ರಾಂತಿ', 'नींद और आराम'],
  'Sleep on your left side.': ['ಎಡಬದಿಗೆ ಮಲಗಿ ನಿದ್ರಿಸಿ.', 'बाईं करवट सोएं।'],
  'Rest for an hour after lunch.': ['ಮಧ್ಯಾಹ್ನದ ಊಟದ ನಂತರ ಒಂದು ಗಂಟೆ ವಿಶ್ರಾಂತಿ ಪಡೆಯಿರಿ.', 'दोपहर के खाने के बाद एक घंटा आराम करें।'],
  'Avoid lifting heavy water pots.': ['ಭಾರವಾದ ನೀರಿನ ಕೊಡಗಳನ್ನು ಎತ್ತಬೇಡಿ.', 'भारी पानी के मटके न उठाएं।'],
  'Tablets and injections': ['ಮಾತ್ರೆಗಳು ಮತ್ತು ಚುಚ್ಚುಮದ್ದುಗಳು', 'गोलियां और इंजेक्शन'],
  'Iron and calcium every day.': ['ಪ್ರತಿದಿನ ಕಬ್ಬಿಣ ಮತ್ತು ಕ್ಯಾಲ್ಸಿಯಂ.', 'रोज़ आयरन और कैल्शियम।'],
  'Two tetanus injections in pregnancy.': ['ಗರ್ಭಾವಸ್ಥೆಯಲ್ಲಿ ಎರಡು ಧನುರ್ವಾತ ಚುಚ್ಚುಮದ್ದು.', 'गर्भावस्था में टिटनेस के दो इंजेक्शन।'],
  'Ask before taking any other medicine.': ['ಬೇರೆ ಯಾವುದೇ ಔಷಧಿ ತೆಗೆದುಕೊಳ್ಳುವ ಮೊದಲು ಕೇಳಿ.', 'कोई और दवाई लेने से पहले पूछें।'],
  'Hospital bag': ['ಆಸ್ಪತ್ರೆ ಬ್ಯಾಗ್', 'अस्पताल का बैग'],
  'Mother card and ID.': ['ತಾಯಿ ಕಾರ್ಡ್ ಮತ್ತು ಗುರುತಿನ ಚೀಟಿ.', 'मां का कार्ड और पहचान पत्र।'],
  'Two clean saris, baby clothes, cloth pads.': ['ಎರಡು ಶುಭ್ರ ಸೀರೆಗಳು, ಮಗುವಿನ ಬಟ್ಟೆ, ಬಟ್ಟೆ ಪ್ಯಾಡ್‌ಗಳು.', 'दो साफ़ साड़ियां, बच्चे के कपड़े, कपड़े के पैड।'],
  'Keep the 108 number and ASHA number written down.': ['108 ಮತ್ತು ಆಶಾ ಅವರ ಸಂಖ್ಯೆಯನ್ನು ಬರೆದಿಟ್ಟುಕೊಳ್ಳಿ.', '108 और आशा का नंबर लिखकर रखें।'],
  Fertility: ['ಫಲವತ್ತತೆ', 'प्रजनन क्षमता'],
  'Plan a healthy pregnancy, when you are ready.': ['ನೀವು ಸಿದ್ಧರಾದಾಗ, ಆರೋಗ್ಯಕರ ಗರ್ಭಧಾರಣೆಗೆ ಯೋಜಿಸಿ.', 'जब आप तैयार हों, तब स्वस्थ गर्भ की योजना बनाएं।'],
  'Your cycle': ['ನಿಮ್ಮ ಮುಟ್ಟಿನ ಚಕ್ರ', 'आपका मासिक चक्र'],
  'A cycle is usually 21 to 35 days.': ['ಒಂದು ಚಕ್ರ ಸಾಮಾನ್ಯವಾಗಿ 21 ರಿಂದ 35 ದಿನಗಳು.', 'एक चक्र आमतौर पर 21 से 35 दिन का होता है।'],
  'The fertile days are about 2 weeks before the next period.': ['ಫಲವತ್ತಾದ ದಿನಗಳು ಮುಂದಿನ ಮುಟ್ಟಿಗೆ ಸುಮಾರು 2 ವಾರ ಮೊದಲು.', 'गर्भ ठहरने के दिन अगली माहवारी से लगभग 2 हफ़्ते पहले होते हैं।'],
  'Irregular cycles are common; tell a doctor if they stop for 3 months.': ['ಅನಿಯಮಿತ ಚಕ್ರ ಸಾಮಾನ್ಯ; 3 ತಿಂಗಳು ನಿಂತರೆ ವೈದ್ಯರಿಗೆ ತಿಳಿಸಿ.', 'अनियमित चक्र आम है; 3 महीने तक बंद रहे तो डॉक्टर को बताएं।'],
  'Folic acid': ['ಫೋಲಿಕ್ ಆಸಿಡ್', 'फोलिक एसिड'],
  'Start folic acid 3 months before trying.': ['ಪ್ರಯತ್ನಿಸುವ 3 ತಿಂಗಳು ಮೊದಲು ಫೋಲಿಕ್ ಆಸಿಡ್ ಶುರು ಮಾಡಿ.', 'कोशिश करने से 3 महीने पहले फोलिक एसिड शुरू करें।'],
  'It protects the baby’s brain and spine.': ['ಇದು ಮಗುವಿನ ಮೆದುಳು ಮತ್ತು ಬೆನ್ನುಹುರಿಯನ್ನು ರಕ್ಷಿಸುತ್ತದೆ.', 'यह बच्चे के दिमाग और रीढ़ की रक्षा करता है।'],
  'Free at your PHC.': ['ನಿಮ್ಮ ಪ್ರಾ.ಆ.ಕೇಂದ್ರದಲ್ಲಿ ಉಚಿತ.', 'आपके PHC पर मुफ़्त।'],
  'Healthy weight': ['ಆರೋಗ್ಯಕರ ತೂಕ', 'स्वस्थ वज़न'],
  'Very low or very high weight can delay pregnancy.': ['ತುಂಬಾ ಕಡಿಮೆ ಅಥವಾ ತುಂಬಾ ಹೆಚ್ಚು ತೂಕ ಗರ್ಭಧಾರಣೆಯನ್ನು ತಡಮಾಡಬಹುದು.', 'बहुत कम या बहुत ज़्यादा वज़न से गर्भ में देर हो सकती है।'],
  'Eat regular meals with protein.': ['ಪ್ರೋಟೀನ್ ಇರುವ ನಿಯಮಿತ ಊಟ ಮಾಡಿ.', 'प्रोटीन वाला नियमित भोजन करें।'],
  'Walk 30 minutes most days.': ['ಹೆಚ್ಚಿನ ದಿನಗಳಲ್ಲಿ 30 ನಿಮಿಷ ನಡೆಯಿರಿ.', 'ज़्यादातर दिन 30 मिनट टहलें।'],
  'When to see a doctor': ['ವೈದ್ಯರನ್ನು ಯಾವಾಗ ನೋಡಬೇಕು', 'डॉक्टर को कब दिखाएं'],
  'No pregnancy after 1 year of trying (6 months if over 35).': ['1 ವರ್ಷ ಪ್ರಯತ್ನಿಸಿದರೂ ಗರ್ಭ ಆಗದಿದ್ದರೆ (35 ಮೇಲ್ಪಟ್ಟವರಿಗೆ 6 ತಿಂಗಳು).', '1 साल कोशिश के बाद भी गर्भ न ठहरे (35 से ऊपर हों तो 6 महीने)।'],
  'Very painful periods or no periods.': ['ತುಂಬಾ ನೋವಿನ ಮುಟ್ಟು ಅಥವಾ ಮುಟ್ಟೇ ಇಲ್ಲದಿರುವುದು.', 'बहुत दर्द वाली माहवारी या माहवारी न आना।'],
  'Both partners can be checked.': ['ಇಬ್ಬರೂ ಸಂಗಾತಿಗಳನ್ನು ಪರೀಕ್ಷಿಸಬಹುದು.', 'दोनों साथियों की जांच हो सकती है।'],
  'Myths and facts': ['ತಪ್ಪು ನಂಬಿಕೆಗಳು ಮತ್ತು ಸತ್ಯಗಳು', 'मिथक और सच'],
  'Not getting pregnant is not only the woman’s problem.': ['ಗರ್ಭ ಆಗದಿರುವುದು ಕೇವಲ ಮಹಿಳೆಯ ಸಮಸ್ಯೆಯಲ್ಲ.', 'गर्भ न ठहरना सिर्फ़ महिला की समस्या नहीं है।'],
  'Papaya in normal food amounts is safe.': ['ಸಾಮಾನ್ಯ ಆಹಾರ ಪ್ರಮಾಣದಲ್ಲಿ ಪರಂಗಿ ಹಣ್ಣು ಸುರಕ್ಷಿತ.', 'सामान्य मात्रा में पपीता सुरक्षित है।'],
  'Stress alone rarely causes infertility.': ['ಕೇವಲ ಒತ್ತಡದಿಂದ ಬಂಜೆತನ ಬರುವುದು ಅಪರೂಪ.', 'सिर्फ़ तनाव से बांझपन बहुत कम होता है।'],
  'Reproductive health': ['ಸಂತಾನೋತ್ಪತ್ತಿ ಆರೋಗ್ಯ', 'प्रजनन स्वास्थ्य'],
  'Periods, infections, family planning and your rights.': ['ಮುಟ್ಟು, ಸೋಂಕುಗಳು, ಕುಟುಂಬ ಯೋಜನೆ ಮತ್ತು ನಿಮ್ಮ ಹಕ್ಕುಗಳು.', 'माहवारी, संक्रमण, परिवार नियोजन और आपके अधिकार।'],
  'Periods and hygiene': ['ಮುಟ್ಟು ಮತ್ತು ಸ್ವಚ್ಛತೆ', 'माहवारी और साफ़-सफ़ाई'],
  'Change pads every 4 to 6 hours.': ['ಪ್ರತಿ 4 ರಿಂದ 6 ಗಂಟೆಗೆ ಪ್ಯಾಡ್ ಬದಲಿಸಿ.', 'हर 4 से 6 घंटे में पैड बदलें।'],
  'Wash cloth pads with soap and dry them in the sun.': ['ಬಟ್ಟೆ ಪ್ಯಾಡ್‌ಗಳನ್ನು ಸಾಬೂನಿನಿಂದ ತೊಳೆದು ಬಿಸಿಲಿನಲ್ಲಿ ಒಣಗಿಸಿ.', 'कपड़े के पैड साबुन से धोकर धूप में सुखाएं।'],
  'Bleeding for more than 7 days: see a doctor.': ['7 ದಿನಕ್ಕಿಂತ ಹೆಚ್ಚು ರಕ್ತಸ್ರಾವ: ವೈದ್ಯರನ್ನು ನೋಡಿ.', '7 दिन से ज़्यादा खून आए: डॉक्टर को दिखाएं।'],
  'Signs of infection': ['ಸೋಂಕಿನ ಲಕ್ಷಣಗಳು', 'संक्रमण के संकेत'],
  'Itching, bad smell or unusual discharge.': ['ತುರಿಕೆ, ಕೆಟ್ಟ ವಾಸನೆ ಅಥವಾ ಅಸಾಮಾನ್ಯ ಸ್ರಾವ.', 'खुजली, बदबू या असामान्य स्राव।'],
  'Pain when passing urine, or fever with lower belly pain.': ['ಮೂತ್ರ ವಿಸರ್ಜನೆಯಲ್ಲಿ ನೋವು, ಅಥವಾ ಕೆಳಹೊಟ್ಟೆ ನೋವಿನೊಂದಿಗೆ ಜ್ವರ.', 'पेशाब में दर्द, या पेट के निचले हिस्से में दर्द के साथ बुखार।'],
  'Treatment is simple and private at the PHC.': ['ಪ್ರಾ.ಆ.ಕೇಂದ್ರದಲ್ಲಿ ಚಿಕಿತ್ಸೆ ಸರಳ ಮತ್ತು ಗೌಪ್ಯ.', 'PHC पर इलाज आसान और गोपनीय है।'],
  'Family planning': ['ಕುಟುಂಬ ಯೋಜನೆ', 'परिवार नियोजन'],
  'Space children at least 2 years apart.': ['ಮಕ್ಕಳ ನಡುವೆ ಕನಿಷ್ಠ 2 ವರ್ಷ ಅಂತರ ಇರಲಿ.', 'बच्चों के बीच कम से कम 2 साल का अंतर रखें।'],
  'Options: pills, Copper-T, injections, condoms.': ['ಆಯ್ಕೆಗಳು: ಮಾತ್ರೆಗಳು, ಕಾಪರ್-ಟಿ, ಚುಚ್ಚುಮದ್ದು, ಕಾಂಡೋಮ್.', 'विकल्प: गोलियां, कॉपर-टी, इंजेक्शन, कंडोम।'],
  'All free and confidential at the PHC.': ['ಪ್ರಾ.ಆ.ಕೇಂದ್ರದಲ್ಲಿ ಎಲ್ಲವೂ ಉಚಿತ ಮತ್ತು ಗೌಪ್ಯ.', 'PHC पर सब मुफ़्त और गोपनीय।'],
  Screening: ['ತಪಾಸಣೆ', 'जांच'],
  'Cervical screening from age 30.': ['30 ವರ್ಷದಿಂದ ಗರ್ಭಕಂಠ ತಪಾಸಣೆ.', '30 साल की उम्र से बच्चेदानी के मुंह की जांच।'],
  'Check your breasts once a month.': ['ತಿಂಗಳಿಗೊಮ್ಮೆ ನಿಮ್ಮ ಸ್ತನಗಳನ್ನು ಪರೀಕ್ಷಿಸಿಕೊಳ್ಳಿ.', 'महीने में एक बार अपने स्तनों की जांच करें।'],
  'Ask your ASHA about the next camp.': ['ಮುಂದಿನ ಶಿಬಿರದ ಬಗ್ಗೆ ನಿಮ್ಮ ಆಶಾ ಅವರನ್ನು ಕೇಳಿ.', 'अगले कैंप के बारे में अपनी आशा से पूछें।'],
  'Your rights and privacy': ['ನಿಮ್ಮ ಹಕ್ಕುಗಳು ಮತ್ತು ಗೌಪ್ಯತೆ', 'आपके अधिकार और गोपनीयता'],
  'Your health information is yours.': ['ನಿಮ್ಮ ಆರೋಗ್ಯ ಮಾಹಿತಿ ನಿಮ್ಮದು.', 'आपकी सेहत की जानकारी आपकी है।'],
  'You can ask for a woman doctor or nurse.': ['ನೀವು ಮಹಿಳಾ ವೈದ್ಯರು ಅಥವಾ ನರ್ಸ್ ಬೇಕು ಎಂದು ಕೇಳಬಹುದು.', 'आप महिला डॉक्टर या नर्स मांग सकती हैं।'],
  'Nothing you read here is saved or shared.': ['ಇಲ್ಲಿ ನೀವು ಓದುವುದು ಯಾವುದೂ ಉಳಿಯುವುದಿಲ್ಲ ಅಥವಾ ಹಂಚಿಕೊಳ್ಳಲಾಗುವುದಿಲ್ಲ.', 'यहां जो आप पढ़ती हैं वह न सेव होता है न साझा।'],

  // ---- medicines (Today) ----------------------------------------------------------
  'After lunch · 2:00 PM': ['ಮಧ್ಯಾಹ್ನದ ಊಟದ ನಂತರ · 2:00', 'दोपहर के खाने के बाद · 2:00'],
  'Calcium + Vitamin D3': ['ಕ್ಯಾಲ್ಸಿಯಂ + ವಿಟಮಿನ್ D3', 'कैल्शियम + विटामिन D3'],
  'After dinner · 8:30 PM': ['ರಾತ್ರಿ ಊಟದ ನಂತರ · 8:30', 'रात के खाने के बाद · 8:30'],

  // ---- baby size (Pregnancy) --------------------------------------------------------
  'a poppy seed': ['ಗಸಗಸೆ ಬೀಜ', 'खसखस का दाना'],
  'a rajma bean': ['ರಾಜ್ಮಾ ಕಾಳು', 'राजमा का दाना'],
  'a lime': ['ನಿಂಬೆಹಣ್ಣು', 'नींबू'],
  'a mango': ['ಮಾವಿನಹಣ್ಣು', 'आम'],
  'a banana': ['ಬಾಳೆಹಣ್ಣು', 'केला'],
  'a corn cob': ['ಮೆಕ್ಕೆಜೋಳದ ತೆನೆ', 'भुट्टा'],
  'a brinjal': ['ಬದನೆಕಾಯಿ', 'बैंगन'],
  'a cabbage': ['ಎಲೆಕೋಸು', 'पत्ता गोभी'],
  'a coconut': ['ತೆಂಗಿನಕಾಯಿ', 'नारियल'],
  'a pineapple': ['ಅನಾನಸ್', 'अनानास'],
  'a papaya': ['ಪರಂಗಿಹಣ್ಣು', 'पपीता'],
  'a small jackfruit': ['ಸಣ್ಣ ಹಲಸಿನಹಣ್ಣು', 'छोटा कटहल'],
  'a watermelon': ['ಕಲ್ಲಂಗಡಿ', 'तरबूज़'],

  // ---- daily checklist (set by the ASHA) ------------------------------------------------
  'Iron tablet after lunch': ['ಮಧ್ಯಾಹ್ನದ ಊಟದ ನಂತರ ಕಬ್ಬಿಣ ಮಾತ್ರೆ', 'दोपहर के खाने के बाद आयरन की गोली'],
  'Calcium tablet after dinner': ['ರಾತ್ರಿ ಊಟದ ನಂತರ ಕ್ಯಾಲ್ಸಿಯಂ ಮಾತ್ರೆ', 'रात के खाने के बाद कैल्शियम की गोली'],
  'Count 10 baby kicks': ['ಮಗುವಿನ 10 ಒದೆತ ಎಣಿಸಿ', 'बच्चे की 10 लातें गिनें'],
  '30 min gentle walk': ['30 ನಿಮಿಷ ನಿಧಾನ ನಡಿಗೆ', '30 मिनट धीरे टहलें'],
  '8 glasses of water': ['8 ಲೋಟ ನೀರು', '8 गिलास पानी'],
  'One extra meal': ['ಒಂದು ಹೆಚ್ಚುವರಿ ಊಟ', 'एक अतिरिक्त भोजन'],
  'Rest 1 hour on left side': ['ಎಡಬದಿಗೆ 1 ಗಂಟೆ ವಿಶ್ರಾಂತಿ', 'बाईं करवट 1 घंटा आराम'],

  // ---- Tara ------------------------------------------------------------------------------
  'Is swelling in my feet normal?': ['ನನ್ನ ಪಾದಗಳ ಊತ ಸಾಮಾನ್ಯವೇ?', 'क्या पैरों में सूजन सामान्य है?'],
  'What should I eat this week?': ['ಈ ವಾರ ನಾನು ಏನು ತಿನ್ನಬೇಕು?', 'इस हफ़्ते मुझे क्या खाना चाहिए?'],
  'My baby is moving less': ['ನನ್ನ ಮಗು ಕಡಿಮೆ ಚಲಿಸುತ್ತಿದೆ', 'मेरा बच्चा कम हिल रहा है'],
  'When should I call 108?': ['ನಾನು ಯಾವಾಗ 108ಕ್ಕೆ ಕರೆ ಮಾಡಬೇಕು?', 'मुझे 108 पर कब कॉल करना चाहिए?'],
  'Call 108 at once if you have bleeding, a fit, severe headache, blurred vision or water leaking. Then tell your ASHA. Do not wait.': [
    'ರಕ್ತಸ್ರಾವ, ಫಿಟ್ಸ್, ತೀವ್ರ ತಲೆನೋವು, ಕಣ್ಣು ಮಂಜಾಗುವುದು ಅಥವಾ ನೀರು ಸೋರುತ್ತಿದ್ದರೆ ತಕ್ಷಣ 108ಕ್ಕೆ ಕರೆ ಮಾಡಿ. ನಂತರ ನಿಮ್ಮ ಆಶಾಗೆ ತಿಳಿಸಿ. ಕಾಯಬೇಡಿ.',
    'खून आना, दौरा, तेज़ सिरदर्द, धुंधला दिखना या पानी रिसना हो तो तुरंत 108 पर कॉल करें। फिर अपनी आशा को बताएं। इंतज़ार न करें।',
  ],
  'Lie on your left side, drink a glass of water and count the kicks for 2 hours. Fewer than 10? Call your ASHA now; she will help you reach the PHC.': [
    'ಎಡಬದಿಗೆ ಮಲಗಿ, ಒಂದು ಲೋಟ ನೀರು ಕುಡಿದು 2 ಗಂಟೆ ಒದೆತ ಎಣಿಸಿ. 10ಕ್ಕಿಂತ ಕಡಿಮೆಯೇ? ಈಗಲೇ ನಿಮ್ಮ ಆಶಾಗೆ ಕರೆ ಮಾಡಿ; ಪ್ರಾ.ಆ.ಕೇಂದ್ರಕ್ಕೆ ಹೋಗಲು ಅವರು ಸಹಾಯ ಮಾಡುತ್ತಾರೆ.',
    'बाईं करवट लेटें, एक गिलास पानी पिएं और 2 घंटे लातें गिनें। 10 से कम? अभी अपनी आशा को कॉल करें; वह PHC पहुंचने में मदद करेंगी।',
  ],
  'Some swelling of the feet late in pregnancy is common. Rest with your feet up. But if your face or hands swell, or you have a headache, tell your ASHA today.': [
    'ಗರ್ಭಾವಸ್ಥೆಯ ಕೊನೆಯಲ್ಲಿ ಪಾದಗಳಲ್ಲಿ ಸ್ವಲ್ಪ ಊತ ಸಾಮಾನ್ಯ. ಕಾಲುಗಳನ್ನು ಮೇಲೆತ್ತಿ ವಿಶ್ರಾಂತಿ ಪಡೆಯಿರಿ. ಆದರೆ ಮುಖ ಅಥವಾ ಕೈ ಊದಿದರೆ, ಅಥವಾ ತಲೆನೋವು ಇದ್ದರೆ, ಇಂದೇ ನಿಮ್ಮ ಆಶಾಗೆ ತಿಳಿಸಿ.',
    'गर्भावस्था के आख़िर में पैरों में थोड़ी सूजन आम है। पैर ऊपर रखकर आराम करें। लेकिन चेहरे या हाथों में सूजन हो, या सिरदर्द हो, तो आज ही अपनी आशा को बताएं।',
  ],
  'Add one extra meal a day: greens, dal, an egg or milk, and fruit. Take your iron tablet after food, not with tea.': [
    'ದಿನಕ್ಕೆ ಒಂದು ಹೆಚ್ಚುವರಿ ಊಟ ಸೇರಿಸಿ: ಸೊಪ್ಪು, ಬೇಳೆ, ಮೊಟ್ಟೆ ಅಥವಾ ಹಾಲು, ಮತ್ತು ಹಣ್ಣು. ಕಬ್ಬಿಣ ಮಾತ್ರೆಯನ್ನು ಊಟದ ನಂತರ ತೆಗೆದುಕೊಳ್ಳಿ, ಚಹಾದೊಂದಿಗೆ ಬೇಡ.',
    'दिन में एक अतिरिक्त भोजन जोड़ें: हरी सब्ज़ी, दाल, अंडा या दूध, और फल। आयरन की गोली खाने के बाद लें, चाय के साथ नहीं।',
  ],
  'Sleep on your left side and rest for an hour after lunch. Feeling tired is normal; feeling breathless or dizzy is not, so tell your ASHA.': [
    'ಎಡಬದಿಗೆ ಮಲಗಿ ನಿದ್ರಿಸಿ ಮತ್ತು ಮಧ್ಯಾಹ್ನದ ಊಟದ ನಂತರ ಒಂದು ಗಂಟೆ ವಿಶ್ರಾಂತಿ ಪಡೆಯಿರಿ. ಸುಸ್ತು ಸಾಮಾನ್ಯ; ಉಸಿರು ಕಟ್ಟುವುದು ಅಥವಾ ತಲೆಸುತ್ತು ಸಾಮಾನ್ಯವಲ್ಲ, ಆದ್ದರಿಂದ ನಿಮ್ಮ ಆಶಾಗೆ ತಿಳಿಸಿ.',
    'बाईं करवट सोएं और दोपहर के खाने के बाद एक घंटा आराम करें। थकान सामान्य है; सांस फूलना या चक्कर आना सामान्य नहीं, इसलिए अपनी आशा को बताएं।',
  ],
  'Take one iron tablet and one calcium tablet every day, at different times. Ask your doctor before taking any other medicine.': [
    'ಪ್ರತಿದಿನ ಒಂದು ಕಬ್ಬಿಣ ಮತ್ತು ಒಂದು ಕ್ಯಾಲ್ಸಿಯಂ ಮಾತ್ರೆಯನ್ನು ಬೇರೆ ಬೇರೆ ಸಮಯದಲ್ಲಿ ತೆಗೆದುಕೊಳ್ಳಿ. ಬೇರೆ ಯಾವುದೇ ಔಷಧಿ ತೆಗೆದುಕೊಳ್ಳುವ ಮೊದಲು ವೈದ್ಯರನ್ನು ಕೇಳಿ.',
    'रोज़ एक आयरन और एक कैल्शियम की गोली, अलग-अलग समय पर लें। कोई और दवाई लेने से पहले डॉक्टर से पूछें।',
  ],

  // ---- screening rule titles (the ASHA's report) ---------------------------------------------
  'Raised blood pressure': ['ಹೆಚ್ಚಿದ ರಕ್ತದೊತ್ತಡ', 'बढ़ा हुआ रक्तचाप'],
  'Severe blood pressure': ['ತೀವ್ರ ರಕ್ತದೊತ್ತಡ', 'गंभीर रक्तचाप'],
  'Pre-eclampsia symptoms': ['ಪ್ರಿ-ಎಕ್ಲಾಂಪ್ಸಿಯಾ ಲಕ್ಷಣಗಳು', 'प्री-एक्लेम्पसिया के लक्षण'],
  'Severe pre-eclampsia risk': ['ತೀವ್ರ ಪ್ರಿ-ಎಕ್ಲಾಂಪ್ಸಿಯಾ ಅಪಾಯ', 'गंभीर प्री-एक्लेम्पसिया का खतरा'],
  'Fits / convulsions': ['ಫಿಟ್ಸ್ / ಸೆಳೆತ', 'दौरे / झटके'],
  'Bleeding, leaking fluid or abdominal pain': ['ರಕ್ತಸ್ರಾವ, ನೀರು ಸೋರುವುದು ಅಥವಾ ಹೊಟ್ಟೆ ನೋವು', 'खून आना, पानी रिसना या पेट दर्द'],
  'Reduced baby movements': ['ಮಗುವಿನ ಚಲನೆ ಕಡಿಮೆ', 'बच्चे की हलचल कम'],
  'Fever, vomiting or breathlessness': ['ಜ್ವರ, ವಾಂತಿ ಅಥವಾ ಉಸಿರಾಟದ ತೊಂದರೆ', 'बुखार, उल्टी या सांस की तकलीफ'],
  'Anaemia or swelling signs': ['ರಕ್ತಹೀನತೆ ಅಥವಾ ಊತದ ಲಕ್ಷಣಗಳು', 'खून की कमी या सूजन के लक्षण'],

  // ---- the doctor's seeded advice --------------------------------------------------------
  'Come to the PHC today so the doctor can check your blood pressure again.': ['ವೈದ್ಯರು ನಿಮ್ಮ ರಕ್ತದೊತ್ತಡವನ್ನು ಮತ್ತೆ ಪರೀಕ್ಷಿಸಲು ಇಂದೇ ಪ್ರಾ.ಆ.ಕೇಂದ್ರಕ್ಕೆ ಬನ್ನಿ.', 'आज ही PHC आएं ताकि डॉक्टर आपका ब्लड प्रेशर फिर से जांच सकें।'],
  'Rest lying on your left side and avoid heavy work until you are seen.': ['ವೈದ್ಯರು ನೋಡುವವರೆಗೆ ಎಡಬದಿಗೆ ಮಲಗಿ ವಿಶ್ರಾಂತಿ ಪಡೆಯಿರಿ ಮತ್ತು ಭಾರದ ಕೆಲಸ ಮಾಡಬೇಡಿ.', 'डॉक्टर के देखने तक बाईं करवट लेटकर आराम करें और भारी काम न करें।'],
  'Call 108 at once if the headache gets worse, your vision gets worse, or you have a fit.': ['ತಲೆನೋವು ಅಥವಾ ದೃಷ್ಟಿ ಹೆಚ್ಚು ತೊಂದರೆಯಾದರೆ, ಅಥವಾ ಫಿಟ್ಸ್ ಬಂದರೆ ತಕ್ಷಣ 108ಕ್ಕೆ ಕರೆ ಮಾಡಿ.', 'सिरदर्द बढ़े, नज़र और बिगड़े, या दौरा पड़े तो तुरंत 108 पर कॉल करें।'],
  'Keep taking your iron and calcium tablets every day.': ['ಪ್ರತಿದಿನ ಕಬ್ಬಿಣ ಮತ್ತು ಕ್ಯಾಲ್ಸಿಯಂ ಮಾತ್ರೆಗಳನ್ನು ತೆಗೆದುಕೊಳ್ಳುತ್ತಿರಿ.', 'रोज़ आयरन और कैल्शियम की गोलियां लेती रहें।'],
  'Eat one extra meal a day with greens, dal and milk.': ['ಸೊಪ್ಪು, ಬೇಳೆ ಮತ್ತು ಹಾಲಿನೊಂದಿಗೆ ದಿನಕ್ಕೆ ಒಂದು ಹೆಚ್ಚುವರಿ ಊಟ ಮಾಡಿ.', 'हरी सब्ज़ी, दाल और दूध के साथ दिन में एक अतिरिक्त भोजन करें।'],
  'Your next check-in with Sunita is in 2 weeks.': ['ಸುನಿತಾ ಅವರೊಂದಿಗೆ ನಿಮ್ಮ ಮುಂದಿನ ತಪಾಸಣೆ 2 ವಾರಗಳಲ್ಲಿ.', 'सुनीता के साथ आपकी अगली जांच 2 हफ़्ते में है।'],
};

/** Tara's answer when nothing matched: `{asha}` is her ASHA's name. */
export const TARA_FALLBACK: Record<Lang, string> = {
  en: 'I have noted your question and shared it with {asha}, your ASHA worker. She will answer at her next visit, or sooner if it is urgent.',
  kn: 'ನಿಮ್ಮ ಪ್ರಶ್ನೆಯನ್ನು ಗಮನಿಸಿ ನಿಮ್ಮ ಆಶಾ ಕಾರ್ಯಕರ್ತೆ {asha} ಅವರಿಗೆ ತಿಳಿಸಿದ್ದೇನೆ. ಅವರು ಮುಂದಿನ ಭೇಟಿಯಲ್ಲಿ, ತುರ್ತಾಗಿದ್ದರೆ ಅದಕ್ಕೂ ಮೊದಲೇ ಉತ್ತರಿಸುತ್ತಾರೆ.',
  hi: 'मैंने आपका सवाल नोट कर लिया है और आपकी आशा कार्यकर्ता {asha} को बता दिया है। वह अगली मुलाकात में, या ज़रूरी हो तो उससे पहले जवाब देंगी।',
};

/** A content line in her language (English when it is not in the table). */
export function tx(text: string, lang: Lang): string {
  if (lang === 'en') return text;
  const row = CONTENT[text];
  return row ? row[lang === 'kn' ? 0 : 1] : text;
}

/** tx() bound to the app's language. */
export function useTx() {
  const { language } = useOnboarding();
  return (text: string) => tx(text, language);
}
