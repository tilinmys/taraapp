import { jest } from '@jest/globals';

// Jest setup for every test file (package.json "jest.setupFiles").
// The voice log (voice/log.ts) prints every step; tests stay readable
// without it. Set SAKHI_TEST_LOG=1 to see it while debugging a test.
if (!process.env.SAKHI_TEST_LOG) {
  const log = console.log.bind(console);
  console.log = (...args: unknown[]) => {
    if (typeof args[0] === 'string' && args[0].startsWith('[sakhi')) return;
    log(...args);
  };
}

// expo-keep-awake has no native module under Jest (the session screen holds
// the screen on while a check-in runs).
jest.mock('expo-keep-awake', () => ({
  useKeepAwake: jest.fn(),
  activateKeepAwakeAsync: jest.fn(() => Promise.resolve()),
  deactivateKeepAwake: jest.fn(() => Promise.resolve()),
}));
