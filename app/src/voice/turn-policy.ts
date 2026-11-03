// What happens after ONE answer to ONE question: a pure decision, from what
// the answer was and what this question has already been through. The
// engine (voice/engine.ts) only carries it out, so every edge case is here,
// in one table, and tested (voice/__tests__/turn-policy.test.ts).
//
//   heard               state of the question            -> action
//   complete            -                                -> next
//   partial             follow-ups left                  -> follow-up (ask ONLY what is missing)
//   partial             none left                        -> give up (record the part, flag the rest)
//   unclear             no re-ask yet                    -> re-ask ("sorry" + the question)
//   unclear             re-asked already                 -> give up ("Not clear: ...", flagged if danger)
//   silence             no nudge yet                     -> nudge ("please say it once more"), listen
//   silence             nudged already                   -> give up ("No answer")
//   repeat (spoken)     repeats left                     -> the question again (not her fault: no
//                                                           re-ask is used up)
//   wait (spoken)       waits left                       -> listen again, longer, saying nothing
//   skip (spoken / Next with nothing said)              -> skip (recorded as skipped)
//   pending (STT late)  -                                -> next (the answer is applied when it lands)
// And a hard ceiling: MAX_TURNS answers per question, whatever they were.
// The flow therefore never stalls on one question and never loops.

export type Heard = 'complete' | 'partial' | 'unclear' | 'silence' | 'repeat' | 'wait' | 'skip' | 'pending';

export type Action = 'next' | 'followUp' | 'reask' | 'nudge' | 'repeat' | 'relisten' | 'skip' | 'giveUp';

export type QuestionState = { turns: number; followUps: number; reasks: number; nudges: number; repeats: number; waits: number };

export const LIMITS = { turns: 5, followUps: 2, reasks: 1, nudges: 1, repeats: 2, waits: 2 } as const;

export const freshState = (): QuestionState => ({ turns: 0, followUps: 0, reasks: 0, nudges: 0, repeats: 0, waits: 0 });

/** The next step for this question, and its updated state. Pure. */
export function decide(state: QuestionState, heard: Heard): { action: Action; state: QuestionState } {
  const s = { ...state, turns: state.turns + 1 };
  if (heard === 'complete' || heard === 'pending') return { action: 'next', state: s };
  if (heard === 'skip') return { action: 'skip', state: s };
  const last = s.turns >= LIMITS.turns;
  switch (heard) {
    case 'partial':
      if (!last && s.followUps < LIMITS.followUps) return { action: 'followUp', state: { ...s, followUps: s.followUps + 1 } };
      return { action: 'giveUp', state: s };
    case 'unclear':
      if (!last && s.reasks < LIMITS.reasks) return { action: 'reask', state: { ...s, reasks: s.reasks + 1 } };
      return { action: 'giveUp', state: s };
    case 'silence':
      if (!last && s.nudges < LIMITS.nudges) return { action: 'nudge', state: { ...s, nudges: s.nudges + 1 } };
      return { action: 'giveUp', state: s };
    case 'repeat':
      if (!last && s.repeats < LIMITS.repeats) return { action: 'repeat', state: { ...s, repeats: s.repeats + 1 } };
      // Asked for it again and again: treat it as not understood.
      return decide({ ...state, repeats: LIMITS.repeats }, 'unclear');
    case 'wait':
      if (!last && s.waits < LIMITS.waits) return { action: 'relisten', state: { ...s, waits: s.waits + 1 } };
      return decide({ ...state, waits: LIMITS.waits }, 'silence');
  }
  return { action: 'giveUp', state: s };
}
