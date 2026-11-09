// The keywords Sakhi listens for, in Kannada, Hindi and English (with the
// romanised forms an STT engine or an ASHA may produce). Each surface form
// maps to ONE concept. Clinicians can read and extend this file without
// touching any logic.
//
// Matching rules (voice/nlu/extract.ts):
//   * Indic forms are STEMS: they match at the start of a word and may run
//     on into a suffix (ತಲೆನೋವ -> ತಲೆನೋವು / ತಲೆನೋವಿದೆ / ತಲೆನೋವಿಲ್ಲ)
//   * Latin and digit forms match whole words only ("fit" never inside "benefit")
//   * `fixed: 1` forms describe a symptom although they contain a "no"
//     ("साफ नहीं दिखता" = blurred vision): their inner "no" is not a negation
//   * Cue words (NEG / AFF / NOTHING / UNKNOWN) decide polarity; PRE cues
//     (English "no headache") govern what follows, the others (Kannada
//     "ತಲೆನೋವು ಇಲ್ಲ", Hindi "सिरदर्द नहीं") what comes before.

export type Concept =
  // cues
  | 'NEG' | 'NEG_PRE' | 'AFF' | 'AFF_PRE' | 'NOTHING' | 'UNKNOWN' | 'NORMAL' | 'REDUCED' | 'DAILY' | 'SOMETIMES'
  // danger symptoms
  | 'HEADACHE' | 'VISION' | 'SWELL_FACE' | 'SWELL_FEET' | 'SWELLING' | 'PALLOR' | 'ABD_PAIN' | 'BLEEDING' | 'LEAKING'
  | 'FITS' | 'MOVEMENT' | 'FEVER' | 'VOMITING' | 'BREATHLESS' | 'DISCHARGE' | 'TIRED' | 'PALPITATIONS'
  // history / care
  | 'CSECTION' | 'NATURAL' | 'IVF' | 'IRON' | 'SCAN' | 'PLACE' | 'TT' | 'BOTH' | 'ONE_ONLY' | 'DIABETES' | 'HTN' | 'THYROID'
  | 'MEDICINE' | 'ALLERGY' | 'DOCTOR' | 'NURSE' | 'AMBULANCE'
  | 'MISCARRIAGE' | 'ABORTION' | 'STILLBIRTH' | 'TB' | 'HEART' | 'ASTHMA' | 'TRANSFUSION' | 'TOBACCO' | 'ALCOHOL'
  | 'TEST' | 'PREG_TEST' | 'HEALTH_CENTRE' | 'HOME'
  // markers for numbers
  | 'MONTH' | 'WEEK' | 'YEAR' | 'KG' | 'HB' | 'SUGAR' | 'FHR' | 'CHILD' | 'PREGNANCY' | 'NAME' | 'AGE' | 'BP';

export type Entry = { concept: Concept; forms: string[]; fixed?: 1 };

export const LEXICON: Entry[] = [
  // ---- cues -----------------------------------------------------------------
  { concept: 'NOTHING', forms: ['ಏನೂ ಇಲ್ಲ', 'ಏನು ಇಲ್ಲ', 'ಏನಿಲ್ಲ', 'ಯಾವುದೂ ಇಲ್ಲ', 'ಏನೂ ತೊಂದರೆ ಇಲ್ಲ', 'ಯಾವ ತೊಂದರೆಯೂ ಇಲ್ಲ', 'ಏನೂ ಆಗಿಲ್ಲ', 'ಎಲ್ಲಾ ಸರಿ', 'ಎಲ್ಲ ಸರಿ', 'कुछ नहीं', 'कोई नहीं', 'कोई तकलीफ नहीं', 'कोई दिक्कत नहीं', 'कोई परेशानी नहीं', 'कोई समस्या नहीं', 'सब ठीक', 'nothing', 'no problem', 'no problems', 'no issues', 'all normal', 'all fine', 'everything fine', 'everything normal', 'none', 'nil', 'yenu illa', 'enu illa', 'kuch nahi'] },
  { concept: 'UNKNOWN', forms: ['ಗೊತ್ತಿಲ್ಲ', 'ಗೊತ್ತಿಲ', 'ಗೊತ್ತಾಗಿಲ್ಲ', 'ನೆನಪಿಲ್ಲ', 'ಗೊತ್ತಿಲ್ಲಾ', 'पता नहीं', 'मालूम नहीं', 'याद नहीं', 'नहीं पता', 'नहीं मालूम', "don't know", 'dont know', 'do not know', 'not sure', 'no idea', 'gottilla', 'pata nahi'] },
  { concept: 'NEG', forms: ['ಇಲ್ಲ', 'ಇಲ್ಲಾ', 'ಇಲ್ಲವ', 'ಇಲ್ಲಪ್ಪ', 'ಇಲ್ಲಮ್ಮ', 'ಬೇಡ', 'ಆಗಿಲ್ಲ', 'ಮಾಡಿಲ್ಲ', 'ಮಾಡಿಸಿಲ್ಲ', 'ಕಂಡಿಲ್ಲ', 'ತಗೊಂಡಿಲ್ಲ', 'ತೆಗೆದುಕೊಂಡಿಲ್ಲ', 'नहीं', 'नही', 'ना', 'न', 'मत', 'नहीं है', 'नहीं हैं', 'नहीं हुआ', 'नहीं हुई', 'illa', 'illla', 'nahi', 'nahin', 'nai', 'negative', 'absent'] },
  { concept: 'NEG_PRE', forms: ['no', 'not', 'never', 'nope', 'nah', 'without', 'denies', 'neither', 'nor'] },
  { concept: 'AFF', forms: ['ಹೌದು', 'ಹೌದ', 'ಹೂಂ', 'ಹುಂ', 'ಹಾಂ', 'ಇದೆ', 'ಇದ್ದಾರೆ', 'ಇದ್ದಾಳೆ', 'ಇದ್ದಾನೆ', 'ಆಗಿದೆ', 'ಆಗಿತ್ತು', 'ಇತ್ತು', 'ಆಗುತ್ತಿದೆ', 'ಆಗ್ತಿದೆ', 'ಆಗ್ತಾ ಇದೆ', 'ಮಾಡಿದ್ದಾರೆ', 'ಮಾಡಿಸಿದ್ದಾರೆ', 'ಮಾಡಿದೆ', 'ತಗೊಂಡಿದ್ದಾರೆ', 'ತೆಗೆದುಕೊಂಡಿದ್ದಾರೆ', 'ತಿನ್ನುತ್ತಿದ್ದಾಳೆ', 'ತಿನ್ನುತ್ತಿದ್ದಾರೆ', 'ಗೊತ್ತಾಗುತ್ತಿದೆ', 'ಗೊತ್ತಾಗ್ತಿದೆ', 'ಸಿಕ್ಕಿದೆ', 'हां', 'हाँ', 'हा', 'जी', 'जी हां', 'है', 'हैं', 'था', 'थी', 'थे', 'हुआ', 'हुई', 'हुए', 'हो रहा', 'हो रही', 'करवाया', 'करवाई', 'लगवाए', 'लगवाया', 'खा रही', 'ले रही', 'आ रहा', 'आ रही', 'houdu', 'haudu', 'haan', 'haa', 'ide', 'hai'] },
  { concept: 'AFF_PRE', forms: ['yes', 'yeah', 'yep', 'ya', 'yup', 'sure', 'correct', 'present', 'positive', 'done', 'ok', 'okay'] },
  { concept: 'NORMAL', forms: ['ಸರಿಯಾಗಿ', 'ಸರಿಯಾಗಿದೆ', 'ಸರಿ ಇದೆ', 'ಚೆನ್ನಾಗಿ', 'ಚೆನ್ನಾಗಿದೆ', 'ಸಾಮಾನ್ಯ', 'ನಾರ್ಮಲ್', 'ಆರೋಗ್ಯವಾಗಿ', 'ठीक', 'सही', 'सामान्य', 'नॉर्मल', 'नार्मल', 'अच्छा', 'अच्छी', 'normal', 'fine', 'good', 'properly', 'well'] },
  { concept: 'REDUCED', forms: ['ಕಡಿಮೆ', 'ಕಮ್ಮಿ', 'ಸ್ವಲ್ಪವೇ', 'ನಿಧಾನ', 'कम', 'कम हो', 'धीमी', 'less', 'lesser', 'fewer', 'reduced', 'decreased', 'low', 'slow', 'slowly', 'little'] },
  { concept: 'DAILY', forms: ['ದಿನಾ', 'ದಿನವೂ', 'ದಿನಾಲೂ', 'ದಿನಾಲು', 'ಪ್ರತಿದಿನ', 'ಪ್ರತಿ ದಿನ', 'ತಪ್ಪದೆ', 'ತಪ್ಪದೇ', 'ರೆಗ್ಯುಲರ್', 'रोज', 'रोज़', 'रोजाना', 'हर दिन', 'हर रोज', 'बिना चूके', 'नियमित', 'daily', 'every day', 'everyday', 'regularly', 'regular', 'dina'] },
  { concept: 'SOMETIMES', forms: ['ಕೆಲವೊಮ್ಮೆ', 'ಆಗಾಗ', 'ಒಮ್ಮೊಮ್ಮೆ', 'ಮರೆತು', 'ಮರೆಯುತ್ತಾಳೆ', 'कभी कभी', 'कभी', 'भूल जाती', 'sometimes', 'irregular', 'irregularly', 'missed', 'occasionally', 'forgets'] },

  // ---- danger symptoms --------------------------------------------------------
  { concept: 'HEADACHE', forms: ['ತಲೆನೋವ', 'ತಲೆ ನೋವ', 'ತಲೆನೋ', 'ತಲೆ ಸಿಡಿತ', 'ತಲೆ ಸುತ್ತ', 'ತಲೆ ಬೇನೆ', 'ತಲೆಬೇನೆ', 'सिरदर्द', 'सिर दर्द', 'सर दर्द', 'सरदर्द', 'सिर में दर्द', 'सर में दर्द', 'सर में बहुत भारीपन', 'सर में भारीपन', 'headache', 'head ache', 'head pain', 'talenovu', 'sirdard', 'sar dard'] },
  { concept: 'VISION', forms: ['ಮಂಜಾಗ', 'ಕಣ್ಣು ಮಂಜ', 'ಕಣ್ಣುಮಂಜ', 'ಮಸುಕ', 'ದೃಷ್ಟಿ ಮಂಜ', 'ಕಣ್ಣು ಮಬ್ಬ', 'ಮಬ್ಬಾಗ', 'ಕಣ್ಣು ಕತ್ತಲ', 'ಕತ್ತಲೆ ಬರ', 'ಕತ್ತಲೆ ಆಗ್ತ', 'ಕತ್ತಲೆ ಆಗುತ್ತ', 'धुंधला', 'धुंधल', 'धुंधलापन', 'आँखों के आगे अंधेरा', 'आंखों के आगे अंधेरा', 'अंधेरा छा', 'blurred', 'blurry', 'blur', 'blurring', 'vision problem', 'eyesight', 'spots in eyes'] },
  { concept: 'VISION', fixed: 1, forms: ['ಕಣ್ಣು ಸರಿಯಾಗಿ ಕಾಣಿಸುತ್ತಿಲ್ಲ', 'ಸರಿಯಾಗಿ ಕಾಣಿಸುತ್ತಿಲ್ಲ', 'ಕಣ್ಣು ಕಾಣಿಸುತ್ತಿಲ್ಲ', 'ಕಾಣಿಸ್ತಿಲ್ಲ', 'साफ नहीं दिख', 'ठीक से नहीं दिख', 'नहीं दिख रहा', 'नहीं दिखता', 'cannot see clearly', "can't see clearly", 'not seeing clearly', 'not able to see'] },
  { concept: 'SWELL_FACE', forms: ['ಮುಖ ಊದ', 'ಮುಖದಲ್ಲಿ ಊತ', 'ಮುಖ ಊತ', 'ಕೈ ಊದ', 'ಕೈಗಳಲ್ಲಿ ಊತ', 'ಕೈಕಾಲು ಊದ', 'ಕೈ ಕಾಲು ಊದ', 'ಕೈಕಾಲುಗಳಲ್ಲಿ ಊತ', 'चेहरे पर सूजन', 'चेहरा सूज', 'चेहरे में सूजन', 'मुंह सूज', 'हाथ सूज', 'हाथों में सूजन', 'हाथ पैर सूज', 'face swelling', 'swollen face', 'puffy face', 'face swollen', 'hands swollen', 'swollen hands', 'hand swelling'] },
  { concept: 'SWELL_FEET', forms: ['ಕಾಲು ಊದ', 'ಕಾಲುಗಳು ಊದ', 'ಕಾಲಲ್ಲಿ ಊತ', 'ಕಾಲು ಊತ', 'ಪಾದ ಊದ', 'ಪಾದಗಳಲ್ಲಿ ಊತ', 'ಪಾದದಲ್ಲಿ ಊತ', 'ಪಾದ ಊತ', 'ಕಾಲು ಬಾವು', 'पैर सूज', 'पैरों में सूजन', 'पैर में सूजन', 'पांव सूज', 'पांव में सूजन', 'feet swollen', 'swollen feet', 'foot swelling', 'leg swelling', 'legs swollen', 'swollen legs', 'ankle swelling', 'pedal edema', 'pedal oedema'] },
  { concept: 'SWELLING', forms: ['ಊತ', 'ಊದಿಕೊ', 'ಊದಿದೆ', 'ಬಾವು', 'सूजन', 'सूज', 'swelling', 'swollen', 'edema', 'oedema', 'puffy'] },
  { concept: 'PALLOR', forms: ['ಬಿಳುಪ', 'ಬಿಳಿಚ', 'ಬಿಳಿಯಾಗ', 'ರಕ್ತಹೀನ', 'ರಕ್ತ ಕಡಿಮೆ', 'ರಕ್ತ ಕಮ್ಮಿ', 'ರಕ್ತ ಕಡಿಮಿ', 'ಅನೀಮಿಯ', 'पीलापन', 'पीली', 'पीला', 'खून की कमी', 'खून कम', 'एनीमिया', 'सफेद', 'pale', 'pallor', 'paleness', 'anaemia', 'anemia', 'anaemic', 'anemic'] },
  { concept: 'ABD_PAIN', forms: ['ಹೊಟ್ಟೆ ನೋವ', 'ಹೊಟ್ಟೆನೋವ', 'ಹೊಟ್ಟೆ ಹಿಂಡ', 'ಹೊಟ್ಟೆ ಸೆಳೆತ', 'ಹೊಟ್ಟೆಯಲ್ಲಿ ನೋವ', 'ಹೊಟ್ಟೆ ಬೇನೆ', 'ಹೊಟ್ಟೆಬೇನೆ', 'पेट दर्द', 'पेट में दर्द', 'पेटदर्द', 'पेट में ऐंठन', 'stomach pain', 'abdominal pain', 'tummy pain', 'belly pain', 'pain in stomach', 'stomach ache', 'stomachache', 'cramps'] },
  { concept: 'BLEEDING', forms: ['ರಕ್ತಸ್ರಾವ', 'ರಕ್ತ ಸ್ರಾವ', 'ರಕ್ತ ಹೋಗ', 'ರಕ್ತ ಬರ', 'ರಕ್ತ ಬಂದ', 'ರಕ್ತ ಕಾಣ', 'ಸ್ಪಾಟಿಂಗ್', 'ಬ್ಲೀಡಿಂಗ್', 'रक्तस्राव', 'खून आ', 'खून बह', 'खून जा', 'खून गिर', 'ब्लीडिंग', 'धब्बे', 'bleeding', 'spotting', 'blood loss', 'blood coming', 'bleed'] },
  { concept: 'LEAKING', forms: ['ನೀರು ಹೋಗ', 'ನೀರಿನಂಶ ಹೋಗ', 'ನೀರು ಸೋರ', 'ನೀರು ಬರ', 'ಸೋರಿಕೆ', 'ನೀರು ಒಡೆ', 'पानी जा', 'पानी आ रहा', 'पानी गिर', 'पानी निकल', 'पानी बह', 'leaking', 'leak', 'water breaking', 'water broke', 'water leaking', 'fluid leaking'] },
  { concept: 'FITS', forms: ['ಫಿಟ್ಸ್', 'ಫಿಟ್ಸ', 'ಫಿಟ್', 'ಸೆಳೆತ', 'ಮೂರ್ಛೆ', 'ಅಪಸ್ಮಾರ', 'ಕೈಕಾಲು ಎಳೆ', 'दौरा', 'दौरे', 'झटके', 'झटका', 'मिर्गी', 'बेहोश', 'fit', 'fits', 'seizure', 'seizures', 'convulsion', 'convulsions', 'fainted', 'faint', 'epilepsy'] },
  { concept: 'MOVEMENT', forms: ['ಚಲನೆ', 'ಚಲನ', 'ಒದೆಯ', 'ಒದೆತ', 'ಆಡುತ್ತ', 'ಅಲುಗಾಡ', 'ಓಡಾಡ', 'ಮಗು ಆಡ', 'हलचल', 'हिलना', 'हिल रहा', 'हिलता', 'हिलती', 'लात मार', 'movement', 'movements', 'moving', 'moves', 'kicks', 'kicking', 'kick'] },
  { concept: 'FEVER', forms: ['ಜ್ವರ', 'ಜ್ವರಾ', 'ಮೈ ಬಿಸಿ', 'बुखार', 'ज्वर', 'ताप', 'बदन गरम', 'fever', 'temperature', 'feverish'] },
  { concept: 'VOMITING', forms: ['ವಾಂತಿ', 'ವಾಕರಿಕೆ', 'उल्टी', 'उल्टियां', 'उलटी', 'उबकाई', 'vomit', 'vomits', 'vomiting', 'throwing up', 'nausea'] },
  { concept: 'BREATHLESS', forms: ['ಉಸಿರಾಟದ ತೊಂದರೆ', 'ಉಸಿರಾಟ ತೊಂದರೆ', 'ಉಸಿರು ಕಟ್ಟ', 'ಉಸಿರಾಡಲು ಕಷ್ಟ', 'ಉಸಿರು ಬಿಡಲು ಕಷ್ಟ', 'ದಮ್ಮು', 'ಏದುಸಿರು', 'सांस फूल', 'सांस लेने में तकलीफ', 'सांस की तकलीफ', 'सांस लेने में दिक्कत', 'दम फूल', 'breathless', 'breathlessness', 'breathing difficulty', 'difficulty breathing', 'difficulty in breathing', 'short of breath', 'shortness of breath'] },
  { concept: 'BREATHLESS', fixed: 1, forms: ['ಉಸಿರಾಡಲು ಆಗುತ್ತಿಲ್ಲ', 'ಉಸಿರಾಡೋಕೆ ಆಗ್ತಿಲ್ಲ', 'सांस नहीं ले पा', 'सांस नहीं आ', 'cannot breathe', "can't breathe", 'not able to breathe'] },
  { concept: 'DISCHARGE', forms: ['ಬಿಳಿ ಮುಟ್ಟ', 'ಬಿಳಿಮುಟ್ಟ', 'ಬಿಳಿ ಸೆರಗ', 'ಬಿಳಿ ಹೋಗ', 'ಸ್ರಾವ', 'ಡಿಸ್ಚಾರ್ಜ್', 'सफेद पानी', 'सफ़ेद पानी', 'स्राव', 'डिस्चार्ज', 'discharge', 'white discharge'] },
  { concept: 'TIRED', forms: ['ಸುಸ್ತ', 'ಆಯಾಸ', 'ದಣಿವ', 'ನಿಶ್ಶಕ್ತ', 'ಶಕ್ತಿ ಇಲ್ಲ', 'थकान', 'थकावट', 'कमजोरी', 'कमज़ोरी', 'थक', 'tired', 'tiredness', 'fatigue', 'weakness', 'exhausted'] },
  { concept: 'PALPITATIONS', forms: ['ಎದೆ ಡವಡವ', 'ಡವಡವ', 'ಎದೆ ಬಡಿತ ಜಾಸ್ತಿ', 'ಎದೆ ಬಡಿತ ಹೆಚ್ಚ', 'ಎದೆ ಹೊಡೆದುಕೊ', 'दिल की धड़कन तेज', 'धड़कन तेज', 'दिल तेज', 'घबराहट', 'palpitation', 'palpitations', 'heart racing', 'heart pounding', 'racing heart'] },

  // ---- history and care ---------------------------------------------------------
  { concept: 'CSECTION', forms: ['ಸಿ ಸೆಕ್ಷನ್', 'ಸಿ-ಸೆಕ್ಷನ್', 'ಸಿಸೆಕ್ಷನ್', 'ಸಿಜೇರಿಯನ್', 'ಸಿಸೇರಿಯನ್', 'ಆಪರೇಷನ್', 'ಆಪರೇಶನ್', 'सी सेक्शन', 'सी-सेक्शन', 'सिजेरियन', 'सीजेरियन', 'ऑपरेशन', 'आपरेशन', 'c section', 'c-section', 'csection', 'caesarean', 'cesarean', 'caesarian', 'operation', 'lscs'] },
  { concept: 'NATURAL', forms: ['ನೈಸರ್ಗಿಕ', 'ಸಹಜ', 'ತಾನಾಗಿ', 'ಸ್ವಾಭಾವಿಕ', 'प्राकृतिक', 'नेचुरल', 'अपने आप', 'कुदरती', 'natural', 'naturally', 'spontaneous'] },
  { concept: 'IVF', forms: ['ಐವಿಎಫ್', 'ಐ ವಿ ಎಫ್', 'ಟೆಸ್ಟ್ ಟ್ಯೂಬ್', 'ಚಿಕಿತ್ಸೆ ಪಡೆದ', 'आईवीएफ', 'आई वी एफ', 'टेस्ट ट्यूब', 'इलाज करवाया', 'ivf', 'i v f', 'test tube', 'treatment'] },
  { concept: 'IRON', forms: ['ಕಬ್ಬಿಣ', 'ಐರನ್', 'ಐರನ್ ಮಾತ್ರೆ', 'ಗುಳಿಗೆ', 'ಮಾತ್ರೆ', 'आयरन', 'आइरन', 'लोहे की गोली', 'गोली', 'iron', 'ifa', 'tablet', 'tablets'] },
  { concept: 'SCAN', forms: ['ಸ್ಕ್ಯಾನ್', 'ಸ್ಕ್ಯಾನಿಂಗ್', 'ಸ್ಕ್ಯಾನ', 'ಅಲ್ಟ್ರಾಸೌಂಡ್', 'ಸೋನೋಗ್ರಫಿ', 'स्कैन', 'स्कैनिंग', 'सोनोग्राफी', 'अल्ट्रासाउंड', 'scan', 'scanning', 'scanned', 'ultrasound', 'usg', 'sonography'] },
  { concept: 'PLACE', forms: ['ಬಿಆರ್ಐಎಂಎಸ್', 'ಬಿ ಆರ್ ಐ ಎಂ ಎಸ್', 'ಬ್ರಿಮ್ಸ್', 'ತಾಲೂಕು', 'ತಾಲ್ಲೂಕು', 'ಜಿಲ್ಲಾ ಆಸ್ಪತ್ರೆ', 'ಖಾಸಗಿ', 'ಪ್ರೈವೇಟ್', 'बी आर आई एम एस', 'ब्रिम्स', 'तालुक', 'तालुका', 'जिला अस्पताल', 'प्राइवेट', 'brims', 'taluk', 'taluka', 'district hospital', 'private'] },
  { concept: 'TT', forms: ['ಧನುರ್ವಾತ', 'ಟಿಟಿ', 'ಟಿ ಟಿ', 'ಟಿಡಿ', 'ಚುಚ್ಚುಮದ್ದು', 'ಇಂಜೆಕ್ಷನ್', 'ಇಂಜಕ್ಷನ್', 'धनुर्वात', 'टिटनेस', 'टीटी', 'टी टी', 'टीडी', 'इंजेक्शन', 'टीका', 'टीके', 'tetanus', 'tt', 'td', 'injection', 'injections', 'vaccine'] },
  { concept: 'BOTH', forms: ['ಎರಡೂ', 'ಎರಡು ಸಲ', 'ಎರಡೂ ಆಗಿದೆ', 'दोनों', 'दोनो', 'दो बार', 'both', 'two doses', 'twice'] },
  { concept: 'ONE_ONLY', forms: ['ಒಂದೇ', 'ಒಂದು ಮಾತ್ರ', 'ಒಂದು ಸಲ', 'सिर्फ एक', 'एक ही', 'एक बार', 'only one', 'just one', 'once', 'one dose'] },
  { concept: 'DIABETES', forms: ['ಶುಗರ್', 'ಸಕ್ಕರೆ ಕಾಯಿಲೆ', 'ಮಧುಮೇಹ', 'ಡಯಾಬಿಟಿಸ್', 'शुगर', 'मधुमेह', 'डायबिटीज', 'डायबिटीस', 'sugar', 'diabetes', 'diabetic'] },
  { concept: 'HTN', forms: ['ಬಿಪಿ', 'ಬಿ ಪಿ', 'ರಕ್ತದೊತ್ತಡ', 'ರಕ್ತ ಒತ್ತಡ', 'बीपी', 'बी पी', 'ब्लड प्रेशर', 'रक्तचाप', 'bp', 'b p', 'blood pressure', 'hypertension', 'pressure'] },
  { concept: 'THYROID', forms: ['ಥೈರಾಯ್ಡ್', 'ಥೈರಾಯಿಡ್', 'ಥೈರಾಡ್', 'थायरॉइड', 'थायराइड', 'थायरायड', 'thyroid'] },
  { concept: 'MEDICINE', forms: ['ಕ್ಯಾಲ್ಸಿಯಂ', 'ಕ್ಯಾಲ್ಸಿಯಮ್', 'ಫೋಲಿಕ್', 'ವಿಟಮಿನ್', 'ಔಷಧ', 'कैल्शियम', 'फोलिक', 'विटामिन', 'दवा', 'दवाई', 'calcium', 'folic', 'vitamin', 'medicine', 'medicines', 'tablets'] },
  { concept: 'ALLERGY', forms: ['ಅಲರ್ಜಿ', 'एलर्जी', 'allergy', 'allergic', 'allergies'] },
  { concept: 'DOCTOR', forms: ['ಡಾಕ್ಟರ್', 'ಡಾಕ್ಟರು', 'ವೈದ್ಯ', 'डॉक्टर', 'डाक्टर', 'चिकित्सक', 'doctor', 'doctors'] },
  { concept: 'NURSE', forms: ['ನರ್ಸ್', 'ನರ್ಸು', 'ದಾದಿ', 'नर्स', 'सिस्टर', 'nurse', 'nurses', 'sister'] },
  { concept: 'MISCARRIAGE', forms: ['ಗರ್ಭಪಾತ', 'ಗರ್ಭ ಪಾತ', 'ಗರ್ಭ ಹೋಗ', 'ಗರ್ಭ ಜಾರ', 'ಮಿಸ್ಕ್ಯಾರೇಜ್', 'गर्भपात', 'गर्भ गिर', 'बच्चा गिर', 'मिसकैरेज', 'miscarriage', 'miscarriages', 'miscarried'] },
  { concept: 'ABORTION', forms: ['ಗರ್ಭ ತೆಗೆಸ', 'ಗರ್ಭ ತೆಗೆದ', 'ಅಬಾರ್ಷನ್', 'ಅಬಾರ್ಶನ್', 'गर्भ गिरवा', 'अबॉर्शन', 'एबॉर्शन', 'abortion', 'abortions', 'mtp'] },
  { concept: 'STILLBIRTH', forms: ['ಸತ್ತ ಮಗು', 'ಸತ್ತು ಹುಟ್ಟ', 'ಮಗು ಸತ್ತ', 'ಹುಟ್ಟುವಾಗಲೇ ಸತ್ತ', 'मरा हुआ बच्चा', 'मृत बच्चा', 'मृत जन्म', 'बच्चा मरा', 'stillbirth', 'still birth', 'stillborn', 'dead baby'] },
  { concept: 'TB', forms: ['ಟಿಬಿ', 'ಟಿ ಬಿ', 'ಕ್ಷಯ', 'टीबी', 'टी बी', 'तपेदिक', 'क्षय', 'tb', 't b', 'tuberculosis'] },
  { concept: 'HEART', forms: ['ಹೃದಯ', 'ಹಾರ್ಟ್', 'दिल', 'हृदय', 'हार्ट', 'heart', 'cardiac'] },
  { concept: 'ASTHMA', forms: ['ಅಸ್ತಮಾ', 'ಅಸ್ತಮ', 'ಉಬ್ಬಸ', 'दमा', 'अस्थमा', 'asthma', 'wheezing'] },
  { concept: 'TRANSFUSION', forms: ['ರಕ್ತ ಹಾಕ', 'ರಕ್ತ ಕೊಟ್ಟ', 'ರಕ್ತ ವರ್ಗಾವಣೆ', 'ಬ್ಲಡ್ ಹಾಕ', 'ರಕ್ತ ಏರಿಸ', 'खून चढ़', 'खून चढा', 'ब्लड चढ़', 'transfusion', 'blood transfusion', 'blood given'] },
  { concept: 'TOBACCO', forms: ['ತಂಬಾಕ', 'ಗುಟ್ಕಾ', 'ಗುಟಕಾ', 'ಬೀಡಿ', 'ಸಿಗರೇಟ', 'ಸಿಗರೆಟ', 'ಹೊಗೆಸೊಪ್ಪ', 'ಧೂಮಪಾನ', 'तंबाकू', 'तम्बाकू', 'गुटखा', 'बीड़ी', 'बीडी', 'सिगरेट', 'खैनी', 'धूम्रपान', 'tobacco', 'gutka', 'gutkha', 'beedi', 'bidi', 'cigarette', 'cigarettes', 'smoke', 'smoking', 'khaini'] },
  { concept: 'ALCOHOL', forms: ['ಮದ್ಯ', 'ಸಾರಾಯಿ', 'ಸರಾಯಿ', 'ಕುಡಿತ', 'शराब', 'दारू', 'alcohol', 'liquor', 'drinking'] },
  { concept: 'PREG_TEST', forms: ['ಗರ್ಭ ಪರೀಕ್ಷೆ', 'ಪ್ರೆಗ್ನೆನ್ಸಿ ಟೆಸ್ಟ್', 'ಪ್ರೆಗ್ನೆನ್ಸಿ ಕಿಟ್', 'ಕಿಟ್', 'गर्भ जांच', 'गर्भ की जांच', 'प्रेगनेंसी टेस्ट', 'प्रेग्नेंसी टेस्ट', 'किट', 'pregnancy test', 'kit'] },
  { concept: 'TEST', forms: ['ಪರೀಕ್ಷೆ', 'ಟೆಸ್ಟ್', 'ಮೂತ್ರ', 'ರಕ್ತ ಪರೀಕ್ಷೆ', 'जांच', 'जाँच', 'टेस्ट', 'पेशाब', 'test', 'tests', 'urine', 'blood test'] },
  { concept: 'HEALTH_CENTRE', forms: ['ಆರೋಗ್ಯ ಕೇಂದ್ರ', 'ಆಸ್ಪತ್ರೆ', 'ಪಿಎಚ್‌ಸಿ', 'ಪಿಎಚ್ಸಿ', 'ಪಿ ಎಚ್ ಸಿ', 'ಸರ್ಕಾರಿ ಆಸ್ಪತ್ರೆ', 'स्वास्थ्य केंद्र', 'अस्पताल', 'पीएचसी', 'पी एच सी', 'सरकारी अस्पताल', 'phc', 'p h c', 'chc', 'hospital', 'health centre', 'health center', 'clinic'] },
  { concept: 'HOME', forms: ['ಮನೆಯಲ್ಲಿ', 'ಮನೆಯಲ್ಲೇ', 'ಮನೇಲಿ', 'ಮನೆಲಿ', 'घर पर', 'घर में', 'घर पे', 'home', 'at home'] },
  { concept: 'AMBULANCE', forms: ['108', 'ಆಂಬ್ಯುಲೆನ್ಸ್', 'ಆಂಬುಲೆನ್ಸ್', 'ಗಾಡಿ', 'ವಾಹನ', 'ನೂರನೆಂಟು', 'एम्बुलेंस', 'एंबुलेंस', 'गाड़ी', 'गाडी', 'वाहन', 'एक सौ आठ', 'ambulance', 'vehicle'] },

  // ---- markers that give numbers their meaning -------------------------------------
  { concept: 'MONTH', forms: ['ತಿಂಗಳ', 'ತಿಂಗಳು', 'ತಿಂಗಳಾ', 'महीन', 'महिन', 'माह', 'month', 'months', 'mon'] },
  { concept: 'WEEK', forms: ['ವಾರ', 'हफ्त', 'हफ़्त', 'सप्ताह', 'week', 'weeks', 'wks'] },
  { concept: 'YEAR', forms: ['ವರ್ಷ', 'साल', 'वर्ष', 'बरस', 'year', 'years', 'yrs', 'yr'] },
  { concept: 'KG', forms: ['ಕೆಜಿ', 'ಕೆ ಜಿ', 'ಕಿಲೋ', 'ಕಿಲೊ', 'ಕೇಜಿ', 'किलो', 'केजी', 'के जी', 'kg', 'kgs', 'kilo', 'kilos', 'kilogram', 'kilograms'] },
  { concept: 'HB', forms: ['ಹಿಮೋಗ್ಲೋಬಿನ್', 'ಹಿಮೊಗ್ಲೋಬಿನ್', 'ಎಚ್ ಬಿ', 'ಹೆಚ್ ಬಿ', 'ಎಚ್‌ಬಿ', 'ಹೆಚ್‌ಬಿ', 'हीमोग्लोबिन', 'हिमोग्लोबिन', 'एच बी', 'एचबी', 'hemoglobin', 'haemoglobin', 'hb', 'h b'] },
  { concept: 'SUGAR', forms: ['ಶುಗರ್', 'ಸಕ್ಕರೆ', 'शुगर', 'शक्कर', 'sugar', 'glucose', 'rbs', 'grbs'] },
  { concept: 'FHR', forms: ['ಹೃದಯ ಬಡಿತ', 'ಹೃದಯಬಡಿತ', 'ಎದೆ ಬಡಿತ', 'ಬಡಿತ', 'ಎಫ್ ಎಚ್ ಆರ್', 'धड़कन', 'धडकन', 'हार्ट बीट', 'heart rate', 'heartbeat', 'heart beat', 'fhr', 'beats'] },
  { concept: 'CHILD', forms: ['ಮಕ್ಕಳ', 'ಮಕ್ಕಳು', 'ಮಗು', 'ಮಗ', 'ಮಗಳು', 'बच्च', 'बच्ची', 'बेटा', 'बेटी', 'संतान', 'child', 'children', 'kid', 'kids', 'baby', 'babies', 'son', 'daughter'] },
  { concept: 'PREGNANCY', forms: ['ಗರ್ಭ', 'ಹೆರಿಗೆ', 'ಡೆಲಿವರಿ', 'गर्भ', 'प्रेगनेंसी', 'प्रसव', 'डिलीवरी', 'pregnancy', 'delivery', 'gravida'] },
  { concept: 'NAME', forms: ['ಹೆಸರು', 'ಹೆಸರ', 'नाम', 'name'] },
  { concept: 'AGE', forms: ['ವಯಸ್ಸು', 'ವಯಸ್ಸ', 'उम्र', 'उमर', 'आयु', 'age', 'aged'] },
  { concept: 'BP', forms: ['ಬಿಪಿ', 'ಬಿ ಪಿ', 'ರಕ್ತದೊತ್ತಡ', 'बीपी', 'बी पी', 'ब्लड प्रेशर', 'रक्तचाप', 'bp', 'b p', 'blood pressure'] },
];

/** Words that join two items of one list; they never end a negation's reach. */
export const CONNECTORS = new Set(['ಮತ್ತು', 'ಅಥವಾ', 'ಹಾಗೂ', 'ಮತ್ತೆ', 'और', 'या', 'तथा', 'एवं', 'and', 'or', 'nor', 'also']);

/** Words that start a new clause: "headache yes BUT no vision problem". */
export const CLAUSE_BREAKS = new Set(['ಆದರೆ', 'ಆದ್ರೆ', 'ಆದರೂ', 'ಆಮೇಲೆ', 'लेकिन', 'मगर', 'किंतु', 'परंतु', 'but', 'however', 'except', 'although', 'though']);

/** Ordinals for "which pregnancy is this". */
export const ORDINALS: [string, number][] = [
  ['ಮೊದಲ', 1], ['ಮೊದಲನೇ', 1], ['ಮೊದಲನೆ', 1], ['ಒಂದನೇ', 1], ['ಎರಡನೇ', 2], ['ಎರಡನೆ', 2], ['ಮೂರನೇ', 3], ['ಮೂರನೆ', 3], ['ನಾಲ್ಕನೇ', 4], ['ಐದನೇ', 5],
  ['पहला', 1], ['पहली', 1], ['दूसरा', 2], ['दूसरी', 2], ['तीसरा', 3], ['तीसरी', 3], ['चौथा', 4], ['चौथी', 4], ['पांचवा', 5], ['पांचवी', 5],
  ['first', 1], ['second', 2], ['third', 3], ['fourth', 4], ['fifth', 5],
];
