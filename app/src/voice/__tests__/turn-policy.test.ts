import { describe, expect, it } from '@jest/globals';

import { commandIn } from '../nlu/commands';
import { allFollowUps, signsLine } from '../nlu/followup';
import { Intake } from '../nlu/intake';
import { decide, freshState, LIMITS, type Heard } from '../turn-policy';

// The per-question state machine, case by case: every row of its table.

const run = (heard: Heard[]) => {
  let state = freshState();
  return heard.map((h) => {
    const step = decide(state, h);
    state = step.state;
    return step.action;
  });
};

describe('turn policy', () => {
  it('complete / pending move on; skip skips', () => {
    expect(run(['complete'])).toEqual(['next']);
    expect(run(['pending'])).toEqual(['next']);
    expect(run(['skip'])).toEqual(['skip']);
  });
  it('partial: up to two follow-ups, then recorded as it stands', () => {
    expect(run(['partial', 'partial', 'partial'])).toEqual(['followUp', 'followUp', 'giveUp']);
    expect(run(['partial', 'complete'])).toEqual(['followUp', 'next']);
  });
  it('unclear: one re-ask; silence: one nudge', () => {
    expect(run(['unclear', 'unclear'])).toEqual(['reask', 'giveUp']);
    expect(run(['silence', 'silence'])).toEqual(['nudge', 'giveUp']);
    expect(run(['silence', 'unclear', 'unclear'])).toEqual(['nudge', 'reask', 'giveUp']);
  });
  it('repeat does not use up the re-ask, but cannot loop', () => {
    expect(run(['repeat', 'unclear', 'complete'])).toEqual(['repeat', 'reask', 'next']);
    expect(run(['repeat', 'repeat', 'repeat', 'repeat'])).toEqual(['repeat', 'repeat', 'reask', 'giveUp']);
  });
  it('wait listens again (twice at most), then counts as silence', () => {
    expect(run(['wait', 'complete'])).toEqual(['relisten', 'next']);
    expect(run(['wait', 'wait', 'wait', 'silence'])).toEqual(['relisten', 'relisten', 'nudge', 'giveUp']);
  });
  it('hard ceiling: any mix ends within the turn limit', () => {
    const mixes: Heard[][] = [
      ['repeat', 'wait', 'partial', 'repeat', 'partial', 'wait', 'unclear', 'silence', 'partial'],
      ['partial', 'repeat', 'partial', 'repeat', 'wait', 'wait', 'unclear', 'unclear'],
      ['wait', 'wait', 'repeat', 'repeat', 'partial', 'partial', 'silence', 'unclear'],
    ];
    for (const mix of mixes) {
      const actions = run(mix);
      const end = actions.findIndex((a) => a === 'next' || a === 'giveUp' || a === 'skip');
      expect(end).toBeGreaterThanOrEqual(0);
      expect(end).toBeLessThan(LIMITS.turns);
    }
  });
});

describe('spoken commands', () => {
  it.each([
    ['ಮತ್ತೊಮ್ಮೆ ಹೇಳಿ', 'repeat'],
    ['ಕೇಳಿಸಲಿಲ್ಲ', 'repeat'],
    ['फिर से बोलिए', 'repeat'],
    ['say that again', 'repeat'],
    ['ಮುಂದಿನ ಪ್ರಶ್ನೆ', 'skip'],
    ['छोड़ दो', 'skip'],
    ['ಒಂದು ನಿಮಿಷ', 'wait'],
    ['एक मिनट रुकिए', 'wait'],
  ])('"%s" is %s', (said, cmd) => {
    expect(commandIn(said)).toBe(cmd);
  });
  it('an answer that contains such a word is still an answer', () => {
    expect(commandIn('ಇಲ್ಲ')).toBeNull();
    expect(commandIn('ಬಿಪಿ 140 ಬೈ 90')).toBeNull();
    expect(commandIn('the pain came again in the night and she could not sleep at all')).toBeNull();
  });
});

describe('danger-sign and slot follow-ups', () => {
  it('denied signs leave the rest to ask; a present sign completes the question', () => {
    const a = new Intake(2);
    expect(a.answer('feverPain', 'ಜ್ವರ ಇಲ್ಲ').complete).toBe(false);
    expect(a.missing('feverPain')).toEqual({ signs: ['vomiting', 'abdPain'] });
    expect(a.answer('feverPain', 'ಇಲ್ಲ').complete).toBe(true);
    expect(a.record('feverPain')).toEqual({ value: 'No', danger: false });
    const b = new Intake(3);
    expect(b.answer('bleedLeak', 'ರಕ್ತಸ್ರಾವ ಆಗುತ್ತಿದೆ').complete).toBe(true);
    expect(b.record('bleedLeak')).toEqual({ value: 'Bleeding', danger: true, signs: ['bleeding'] });
  });
  it('a bare number to a follow-up lands in the slot it asked for', () => {
    const a = new Intake(1);
    a.answer('firstPregnancy', 'ಇಲ್ಲ');
    a.answer('deliveries', 'ಹೌದು');
    expect(a.missing('deliveries')).toEqual({ key: 'vAskHowMany', expect: 'count' });
    expect(a.answer('deliveries', '2', 'count').complete).toBe(true);
    expect(a.record('deliveries').value).toBe('2 previous deliveries');
  });
  it('every follow-up line exists in every language (all are recorded)', () => {
    for (const lang of ['kn', 'hi', 'en'] as const) {
      const lines = allFollowUps(lang);
      // 2 fixed lines + the sign subsets (bleeding / discharge / leaking: 3; three 3-sign questions: 6 each)
      expect(lines.length).toBe(2 + 3 + 6 + 6 + 6);
      expect(lines.every((l) => l && !l.includes('{'))).toBe(true);
    }
    expect(signsLine('kn', ['vision', 'swellFace'])).toBe('ಮತ್ತು ಕಣ್ಣು ಮಂಜಾಗುವುದು ಅಥವಾ ಮುಖ ಕೈಗಳಲ್ಲಿ ಊತ ಇದೆಯಾ?');
  });
});
