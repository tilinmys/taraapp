"""STT end-to-end test set: every ASHA intake question (app/src/voice/nlu/intake.ts,
VISIT_QUESTIONS visits 1-3), answered in Kannada, Hindi and Indian English.

Each case: id, qid (the question), visit, lang, text (what is SAID: the reference),
style (clean / numbers / dontknow / codemix / rural). The runner also renders a
subset FAST (+40 % rate) and NOISY (background noise at ~12 dB SNR).

The texts are written the way people answer an ASHA, not to suit the parser:
numbers as words, "don't know", English health words inside Kannada / Hindi,
and North Karnataka / rural Hindi phrasing.
"""

from __future__ import annotations

C = []


def add(qid: str, visit: int, lang: str, text: str, style: str = "clean") -> None:
    C.append({"id": f"{qid}-{lang}-{sum(1 for c in C if c['qid'] == qid and c['lang'] == lang) + 1}", "qid": qid, "visit": visit, "lang": lang, "text": text, "style": style})


# ---- visit 1 (registration) ---------------------------------------------------
add("name", 1, "kn", "ನನ್ನ ಹೆಸರು ಕವಿತಾ")
add("name", 1, "hi", "मेरा नाम सुनीता देवी है")
add("name", 1, "en", "My name is Lakshmi")

add("age", 1, "kn", "ನನಗೆ ಇಪ್ಪತ್ನಾಲ್ಕು ವರ್ಷ", "numbers")
add("age", 1, "kn", "ಇಪ್ಪತ್ತೆರಡು", "numbers")
add("age", 1, "hi", "मेरी उम्र पच्चीस साल है", "numbers")
add("age", 1, "hi", "बाईस साल", "numbers")
add("age", 1, "en", "I am twenty six years old", "numbers")

add("village", 1, "kn", "ನಾವು ಕಮಠಾಣ ಊರಿನವರು")
add("village", 1, "hi", "हम रामपुर गाँव में रहते हैं")
add("village", 1, "en", "I live in Chitta village")

add("firstPregnancy", 1, "kn", "ಹೌದು ಇದು ಮೊದಲನೇ ಸಲ")
add("firstPregnancy", 1, "kn", "ಇಲ್ಲ ಇದು ಎರಡನೇದು", "numbers")
add("firstPregnancy", 1, "hi", "हाँ यह पहली बार है")
add("firstPregnancy", 1, "en", "No, this is my second pregnancy")

add("prevPregnancies", 1, "kn", "ಒಂದು ಸಲ ಆಗಿತ್ತು", "numbers")
add("prevPregnancies", 1, "hi", "दो बार पहले हुआ है", "numbers")
add("prevPregnancies", 1, "en", "Two times before", "numbers")

add("lmp", 1, "kn", "ಮೂರು ತಿಂಗಳ ಹಿಂದೆ ಆಗಿತ್ತು", "numbers")
add("lmp", 1, "kn", "ನೆನಪಿಲ್ಲ", "dontknow")
add("lmp", 1, "hi", "पता नहीं, याद नहीं है", "dontknow")
add("lmp", 1, "hi", "पंद्रह जुलाई को", "numbers")
add("lmp", 1, "en", "I don't remember the date", "dontknow")

add("regularPeriods", 1, "kn", "ಹೌದು ಸರಿಯಾಗಿ ಬರ್ತಿತ್ತು", "rural")
add("regularPeriods", 1, "hi", "हाँ समय पर आते थे")
add("regularPeriods", 1, "en", "No, they were irregular")

add("confirmed", 1, "kn", "ಹೌದು ಆಸ್ಪತ್ರೆಯಲ್ಲಿ test ಮಾಡಿಸಿದೆ", "codemix")
add("confirmed", 1, "hi", "हाँ पीएचसी में जाँच हुई थी", "codemix")
add("confirmed", 1, "en", "Yes, the doctor confirmed with a urine test")

add("deliveries", 1, "kn", "ಒಂದು ಹೆರಿಗೆ ಆಗಿದೆ", "numbers")
add("deliveries", 1, "hi", "एक डिलीवरी हुई है", "codemix")
add("deliveries", 1, "en", "One normal delivery", "numbers")

add("losses", 1, "kn", "ಇಲ್ಲ ಯಾವುದೂ ಇಲ್ಲ")
add("losses", 1, "hi", "एक बार बच्चा गिर गया था", "rural")
add("losses", 1, "en", "I had one miscarriage")

add("pastProblems", 1, "kn", "ಹಿಂದಿನ ಹೆರಿಗೆಯಲ್ಲಿ ತುಂಬಾ ರಕ್ತಸ್ರಾವ ಆಗಿತ್ತು")
add("pastProblems", 1, "hi", "कोई दिक्कत नहीं हुई थी")
add("pastProblems", 1, "en", "Last time my BP was high", "codemix")

add("csection", 1, "kn", "ಹೌದು ಆಪರೇಷನ್ ಆಗಿತ್ತು", "codemix")
add("csection", 1, "hi", "नहीं नॉर्मल डिलीवरी हुई थी", "codemix")
add("csection", 1, "en", "No, it was a normal delivery")

add("conditions", 1, "kn", "ಇಲ್ಲ ಏನೂ ಇಲ್ಲ")
add("conditions", 1, "kn", "ನನಗೆ sugar ಇದೆ", "codemix")
add("conditions", 1, "hi", "मुझे थायराइड है", "codemix")
add("conditions", 1, "en", "I have high BP", "codemix")

add("longIllness", 1, "kn", "ಇಲ್ಲ")
add("longIllness", 1, "hi", "नहीं कोई बीमारी नहीं")
add("longIllness", 1, "en", "No, nothing like that")

add("medicines", 1, "kn", "ಕಬ್ಬಿಣ ಮತ್ತು ಕ್ಯಾಲ್ಸಿಯಂ ಮಾತ್ರೆ ತಗೊಳ್ತೀನಿ", "rural")
add("medicines", 1, "hi", "कोई दवाई नहीं ले रही")
add("medicines", 1, "en", "Only iron tablets")

add("allergy", 1, "kn", "ಯಾವುದೇ allergy ಇಲ್ಲ", "codemix")
add("allergy", 1, "hi", "नहीं कोई एलर्जी नहीं है", "codemix")
add("allergy", 1, "en", "No allergies")

add("transfusion", 1, "kn", "ಇಲ್ಲ ಯಾವತ್ತೂ ರಕ್ತ ಹಾಕಿಲ್ಲ")
add("transfusion", 1, "hi", "हाँ पिछली बार खून चढ़ाया था", "rural")
add("transfusion", 1, "en", "No, never")

add("bleedDischarge", 1, "kn", "ಇಲ್ಲ ಏನೂ ಆಗಿಲ್ಲ")
add("bleedDischarge", 1, "hi", "हाँ थोड़ा खून आया था")
add("bleedDischarge", 1, "en", "No bleeding")

add("feverPain", 1, "kn", "ಎರಡು ದಿನದಿಂದ ಜ್ವರ ಇದೆ", "numbers")
add("feverPain", 1, "kn", "ಹೊಟ್ಟೆ ಬೇನೆ ಐತಿ", "rural")
add("feverPain", 1, "hi", "नहीं कुछ नहीं")
add("feverPain", 1, "hi", "बुखार है और उल्टी हो रही है")
add("feverPain", 1, "en", "I have fever since yesterday")

add("tobacco", 1, "kn", "ಇಲ್ಲ ಏನೂ ಇಲ್ಲ")
add("tobacco", 1, "hi", "नहीं, गुटखा नहीं खाती", "rural")
add("tobacco", 1, "en", "No, I don't use tobacco")

add("concerns", 1, "kn", "ಏನೂ ಇಲ್ಲ")
add("concerns", 1, "hi", "मुझे कमर में दर्द रहता है")
add("concerns", 1, "en", "I feel very tired")

# ---- visit 2 -------------------------------------------------------------------
add("newProblems", 2, "kn", "ಇಲ್ಲ ಹೊಸದಾಗಿ ಏನೂ ಇಲ್ಲ")
add("newProblems", 2, "hi", "हाँ पैरों में सूजन है")
add("newProblems", 2, "en", "No new problems")

add("bleedFluid", 2, "kn", "ಇಲ್ಲ")
add("bleedFluid", 2, "hi", "नहीं, पानी नहीं आया")
add("bleedFluid", 2, "en", "Yes, there was some spotting", "codemix")

add("warning", 2, "kn", "ಎರಡು ದಿನದಿಂದ ತುಂಬಾ ತಲೆನೋವಿದೆ", "numbers")
add("warning", 2, "kn", "ಕಣ್ಣು ಕತ್ತಲೆ ಆಗ್ತಿದೆ", "rural")
add("warning", 2, "kn", "ಇಲ್ಲ ಏನೂ ತೊಂದರೆ ಇಲ್ಲ")
add("warning", 2, "hi", "सिर में बहुत दर्द है और आँखों के आगे अंधेरा आता है", "rural")
add("warning", 2, "hi", "नहीं ऐसा कुछ नहीं")
add("warning", 2, "en", "Yes, headache and blurred vision")
add("warning", 2, "en", "My face is swollen")

add("movementFelt", 2, "kn", "ಹೌದು ಮಗು ಆಡ್ತಿದೆ", "rural")
add("movementFelt", 2, "hi", "हाँ बच्चा हिलता है")
add("movementFelt", 2, "en", "Not yet")

add("ifa", 2, "kn", "ಹೌದು ದಿನಾ ತಿನ್ನುತ್ತಿದ್ದೇನೆ")
add("ifa", 2, "hi", "कभी कभी भूल जाती हूँ")
add("ifa", 2, "en", "Yes, every day after food")

add("calcium", 2, "kn", "ಹೌದು")
add("calcium", 2, "hi", "हाँ रोज़ लेती हूँ")
add("calcium", 2, "en", "No, I stopped taking it")

add("testsDone", 2, "kn", "ಹೌದು ಮಾಡಿಸಿದ್ದೇನೆ")
add("testsDone", 2, "hi", "अभी नहीं करवाया")
add("testsDone", 2, "en", "Yes, blood and urine tests are done", "codemix")

add("followedAdvice", 2, "kn", "ಹೌದು ಡಾಕ್ಟರ್ ಹೇಳಿದ ಹಾಗೆ ಮಾಡಿದ್ದೇನೆ", "codemix")
add("followedAdvice", 2, "hi", "हाँ डॉक्टर की बात मानी", "codemix")
add("followedAdvice", 2, "en", "No, I could not go to the PHC", "codemix")

# ---- visit 3 -------------------------------------------------------------------
add("movement", 3, "kn", "ಹೌದು ಸರಿಯಾಗಿ ಆಡುತ್ತಿದೆ")
add("movement", 3, "hi", "हाँ ठीक से हिल रहा है")
add("movement", 3, "en", "Yes, the baby is moving well")

add("movementLess", 3, "kn", "ಇಲ್ಲ")
add("movementLess", 3, "kn", "ಹೌದು ನಿನ್ನೆಯಿಂದ ಕಡಿಮೆ ಆಡ್ತಿದೆ", "rural")
add("movementLess", 3, "hi", "हाँ कल से कम हिल रहा है")
add("movementLess", 3, "en", "No, it is the same")

add("tummySize", 3, "kn", "ಇಲ್ಲ ಏನೂ ಬದಲಾವಣೆ ಇಲ್ಲ")
add("tummySize", 3, "hi", "हाँ पेट बढ़ रहा है")
add("tummySize", 3, "en", "Yes, it is growing")

add("bleedLeak", 3, "kn", "ಇಲ್ಲ")
add("bleedLeak", 3, "hi", "हाँ सुबह से पानी जा रहा है", "rural")
add("bleedLeak", 3, "en", "No bleeding and no water leaking")

add("breathing", 3, "kn", "ಇಲ್ಲ")
add("breathing", 3, "hi", "हाँ सांस फूलती है", "rural")
add("breathing", 3, "en", "I get breathless when I walk")

add("ironCalcium", 3, "kn", "ಹೌದು ದಿನಾ")
add("ironCalcium", 3, "hi", "हाँ दोनों गोली रोज़ लेती हूँ")
add("ironCalcium", 3, "en", "Sometimes I miss them")

add("deliveryPlace", 3, "kn", "ತಾಲೂಕು ಆಸ್ಪತ್ರೆ")
add("deliveryPlace", 3, "kn", "ಬೀದರ್ ಜಿಲ್ಲಾ ಆಸ್ಪತ್ರೆಯಲ್ಲಿ", "rural")
add("deliveryPlace", 3, "hi", "सरकारी अस्पताल में")
add("deliveryPlace", 3, "en", "At the district hospital")

add("support", 3, "kn", "ಹೌದು ಗಂಡ ಇದ್ದಾರೆ")
add("support", 3, "hi", "हाँ मेरी सास साथ जाएगी")
add("support", 3, "en", "Yes, my husband will come with me")

# The subset rendered fast and with noise: a danger answer, a number and a yes / no per language.
STRESS = [c["id"] for c in C if (c["qid"], c["lang"]) in {
    ("warning", "kn"), ("warning", "hi"), ("warning", "en"),
    ("age", "kn"), ("age", "hi"), ("age", "en"),
    ("ifa", "kn"), ("ifa", "hi"), ("ifa", "en"),
    ("bleedLeak", "hi"), ("movementLess", "kn"), ("feverPain", "hi"),
}]

CASES = C
