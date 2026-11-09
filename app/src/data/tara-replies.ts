// Speak to Tara (mothers only; Sakhi is the ASHA's check-in voice). Until
// the assistant service is connected, Tara answers from this small,
// clinically safe set: match by keywords, otherwise pass the question to the
// ASHA. Anything that sounds like a danger sign always gets "call 108 / ASHA".

export type TaraReply = { keys: string[]; answer: string; urgent?: boolean };

export const SUGGESTED = [
  'Is swelling in my feet normal?',
  'What should I eat this week?',
  'My baby is moving less',
  'When should I call 108?',
];

const REPLIES: TaraReply[] = [
  {
    keys: ['bleed', 'fit', 'convuls', 'blur', 'severe headache', 'water leak', '108', 'emergency', 'ರಕ್ತ', 'ಫಿಟ್ಸ್', 'ಮಂಜ', 'ತುರ್ತು', 'खून', 'दौरा', 'धुंधला', 'आपात'],
    answer: 'Call 108 at once if you have bleeding, a fit, severe headache, blurred vision or water leaking. Then tell your ASHA. Do not wait.',
    urgent: true,
  },
  {
    keys: ['moving less', 'kick', 'movement', 'not moving', 'ಚಲನೆ', 'ಚಲಿಸು', 'ಒದೆ', 'हलचल', 'हिल', 'लात'],
    answer: 'Lie on your left side, drink a glass of water and count the kicks for 2 hours. Fewer than 10? Call your ASHA now; she will help you reach the PHC.',
    urgent: true,
  },
  {
    keys: ['swell', 'feet', 'oedema', 'edema', 'ಊತ', 'ಊದ', 'ಪಾದ', 'सूजन', 'पैर'],
    answer: 'Some swelling of the feet late in pregnancy is common. Rest with your feet up. But if your face or hands swell, or you have a headache, tell your ASHA today.',
  },
  {
    keys: ['eat', 'food', 'diet', 'meal', 'hungry', 'ತಿನ್ನ', 'ಊಟ', 'ಆಹಾರ', 'खा', 'भोजन', 'आहार'],
    answer: 'Add one extra meal a day: greens, dal, an egg or milk, and fruit. Take your iron tablet after food, not with tea.',
  },
  {
    keys: ['sleep', 'tired', 'rest', 'ನಿದ್ರೆ', 'ಸುಸ್ತು', 'ವಿಶ್ರಾಂತಿ', 'नींद', 'थकान', 'आराम'],
    answer: 'Sleep on your left side and rest for an hour after lunch. Feeling tired is normal; feeling breathless or dizzy is not, so tell your ASHA.',
  },
  {
    keys: ['tablet', 'iron', 'calcium', 'medicine', 'ಮಾತ್ರೆ', 'ಕಬ್ಬಿಣ', 'ಕ್ಯಾಲ್ಸಿಯಂ', 'ಔಷಧ', 'गोली', 'आयरन', 'कैल्शियम', 'दवा'],
    answer: 'Take one iron tablet and one calcium tablet every day, at different times. Ask your doctor before taking any other medicine.',
  },
];

export function taraAnswer(question: string, ashaName: string): TaraReply {
  const q = question.toLowerCase();
  return (
    REPLIES.find((r) => r.keys.some((k) => q.includes(k))) ?? {
      keys: [],
      answer: `I have noted your question and shared it with ${ashaName}, your ASHA worker. She will answer at her next visit, or sooner if it is urgent.`,
    }
  );
}
