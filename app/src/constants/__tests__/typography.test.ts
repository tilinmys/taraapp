import { describe, expect, it } from '@jest/globals';

import { scriptForLang, splitByScript } from '../typography';

// Indic lines with numbers and punctuation stay ONE run (one <Text>, no
// nested span for Android to mis-measure); Latin words still get their own.

describe('splitByScript', () => {
  it('numbers and punctuation join the Kannada or Hindi around them', () => {
    expect(splitByScript('1. ತಾಯಿಯ ವಯಸ್ಸು 24')).toEqual([{ text: '1. ತಾಯಿಯ ವಯಸ್ಸು 24', script: 'kannada' }]);
    expect(splitByScript('बीपी 148/96 है')).toEqual([{ text: 'बीपी 148/96 है', script: 'devanagari' }]);
  });

  it('English words in a Kannada line are still their own run', () => {
    expect(splitByScript('ಬಿಪಿ 148/96 mmHg').map((r) => r.script)).toEqual(['kannada', 'latin']);
  });

  it('a number alone is Latin', () => {
    expect(splitByScript('24')).toEqual([{ text: '24', script: 'latin' }]);
  });

  it('inputs use her script', () => {
    expect(scriptForLang('kn')).toBe('kannada');
    expect(scriptForLang('hi')).toBe('devanagari');
    expect(scriptForLang('en')).toBe('latin');
  });
});
