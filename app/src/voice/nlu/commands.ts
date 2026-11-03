import { normalize } from './normalize';

// Things the ASHA says TO SAKHI rather than as an answer: "say it again",
// "skip it", "wait a minute (I am asking her)". Matched on the normalised
// text (number words are digits there: "ಒಂದು ನಿಮಿಷ" -> "1 ನಿಮಿಷ", "छोड़ दो" ->
// "छोड 2"), only in a short
// utterance that is mostly the command, so an answer that happens to
// contain "again" ("the pain came again") is never mistaken for one.

export type Command = 'repeat' | 'skip' | 'wait';

const RULES: { cmd: Command; re: RegExp }[] = [
  {
    cmd: 'repeat',
    re: /ಮತ್ತೊಮ್ಮೆ|ಇನ್ನೊಮ್ಮೆ|ಮತ್ತೆ ಹೇಳಿ|ಮತ್ತೆ ಕೇಳಿ|ಏನು ಕೇಳಿದ್ರಿ|ಏನಂದ್ರಿ|ಏನು ಅಂದ್ರಿ|ಕೇಳಿಸಲಿಲ್ಲ|ಕೇಳಲಿಲ್ಲ|ಅರ್ಥ ಆಗಲಿಲ್ಲ|ಅರ್ಥವಾಗಲಿಲ್ಲ|फिर से|दोबारा|क्या कहा|सुनाई नहीं|समझ नहीं|repeat|say (?:it |that )?again|once more|pardon|what did you say|didn'?t hear/,
  },
  {
    cmd: 'skip',
    re: /ಮುಂದಿನ ಪ್ರಶ್ನೆ|ಮುಂದೆ ಹೋಗಿ|ಬಿಟ್ಟು ಬಿಡಿ|ಬಿಟ್ಟುಬಿಡಿ|ಸ್ಕಿಪ್|अगला सवाल|आगे बढो|आगे चलो|छोड (?:दो|2)|छोड दीजिए|skip|next question|move on/,
  },
  {
    cmd: 'wait',
    re: /(?:ಒಂದು|ಒಂದ್|1) ?ನಿಮಿಷ|ಸ್ವಲ್ಪ ತಡಿ|ತಡೀರಿ|ತಡಿರಿ|ಕೇಳಿ ಹೇಳ್ತೀನಿ|ಕೇಳಿ ಹೇಳುತ್ತೇನೆ|(?:एक|1) ?मिनट|रुको|रुकिए|पूछ कर बताती|wait|(?:one|1) minute|hold on|just a minute/,
  },
];

const MAX_WORDS = 6;

/** The command in this transcript, if it is one (and not an answer). */
export function commandIn(transcript: string): Command | null {
  const text = normalize(transcript).toLowerCase();
  if (!text || text.split(/\s+/).length > MAX_WORDS) return null;
  for (const r of RULES) if (r.re.test(text)) return r.cmd;
  return null;
}
