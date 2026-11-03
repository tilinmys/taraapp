import { beforeEach, describe, expect, it, jest } from '@jest/globals';

import { STRINGS } from '@/i18n/strings';
import { kv } from '@/lib/kv';

import { ME_ASHA } from '../care-network';
import { CONSENT_NOTICE_VERSION, store } from '../store';

// DPDP Act 2023 s.6: a voice check-in starts only after the mother heard the
// notice in her language and agreed. The record says who took it, when, in
// which language and against which notice version, and it survives a restart.

jest.mock('expo-router', () => ({ router: { canGoBack: jest.fn(), back: jest.fn(), replace: jest.fn(), push: jest.fn() } }));

const lakshmi = () => store.get().find((m) => m.id === 'lakshmi')!;

beforeEach(() => store.resetDemo());

describe('consent to voice check-ins', () => {
  it('there is no consent until the ASHA records it (never a default)', () => {
    expect(lakshmi().consent).toBeUndefined();
  });

  it('records who took it, when, in which language, against which notice', () => {
    const before = Date.now();
    store.recordConsent('lakshmi', { granted: true, by: ME_ASHA, lang: 'kn' });
    const c = lakshmi().consent!;
    expect(c).toMatchObject({ granted: true, by: ME_ASHA, lang: 'kn', notice: CONSENT_NOTICE_VERSION });
    expect(c.at).toBeGreaterThanOrEqual(before);
    expect(CONSENT_NOTICE_VERSION).toBe('v1.0'); // the same version as sakhi.patient_consents
  });

  it('an un-tick before starting withdraws it', () => {
    store.recordConsent('lakshmi', { granted: true, by: ME_ASHA, lang: 'kn' });
    store.recordConsent('lakshmi', { granted: false, by: ME_ASHA, lang: 'kn' });
    expect(lakshmi().consent?.granted).toBe(false);
  });

  it('is saved with her record (survives an app restart)', () => {
    store.recordConsent('lakshmi', { granted: true, by: ME_ASHA, lang: 'hi' });
    const saved = kv.get<{ patches: Record<string, { consent?: unknown }> }>('tara.mothers.v1');
    expect(saved?.patches.lakshmi?.consent).toMatchObject({ granted: true, lang: 'hi' });
  });

  it('the notice names the purpose, who sees it, the speech services, and the right to refuse', () => {
    for (const lang of ['en', 'kn', 'hi'] as const) {
      const notice = STRINGS[lang].cNotice;
      expect(notice).toContain('Gnani');
      expect(notice).toContain('LiveKit');
    }
    expect(STRINGS.en.cNotice).toMatch(/doctor/);
    expect(STRINGS.en.cNotice).toMatch(/say no, or stop at any time/);
  });
});
