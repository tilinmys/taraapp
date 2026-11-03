import { describe, expect, it } from '@jest/globals';

import { TtsQueue, type TtsPriority } from '../tts-queue';

// The provider takes ONE request at a time: the queue must keep a single
// background lane, put "now" ahead of everything, never load a line twice,
// and promote a queued line that is suddenly needed.

type Call = { text: string; prio: TtsPriority; resolve: (v: string | null) => void };

function harness() {
  const calls: Call[] = [];
  const q = new TtsQueue<string>((text, prio) => new Promise((resolve) => calls.push({ text, prio, resolve })));
  return { q, calls };
}

const flush = () => new Promise<void>((r) => setTimeout(r, 0));

describe('TtsQueue', () => {
  it('runs background work one at a time, in priority order', async () => {
    const { q, calls } = harness();
    q.get('a', 'a', 'warm');
    q.get('b', 'b', 'warm');
    q.get('c', 'c', 'next');
    await flush();
    expect(calls.map((c) => c.text)).toEqual(['a']); // one lane
    calls[0].resolve('A');
    await flush();
    // "next" jumps ahead of the remaining "warm"
    expect(calls.map((c) => c.text)).toEqual(['a', 'c']);
    calls[1].resolve('C');
    await flush();
    expect(calls.map((c) => c.text)).toEqual(['a', 'c', 'b']);
  });

  it('"now" skips the lane entirely', async () => {
    const { q, calls } = harness();
    q.get('a', 'a', 'warm');
    q.get('now', 'now', 'now');
    await flush();
    expect(calls.map((c) => c.text)).toEqual(['a', 'now']);
  });

  it('never loads the same key twice', async () => {
    const { q, calls } = harness();
    const p1 = q.get('k', 'line', 'warm');
    const p2 = q.get('k', 'line', 'warm');
    await flush();
    calls[0].resolve('AUDIO');
    await expect(p1).resolves.toBe('AUDIO');
    await expect(p2).resolves.toBe('AUDIO');
    expect(calls).toHaveLength(1);
  });

  it('promotes a queued line to "now" when it is needed', async () => {
    const { q, calls } = harness();
    q.get('busy', 'busy', 'warm');
    const later = q.get('q5', 'q5', 'warm');
    await flush();
    expect(calls.map((c) => c.text)).toEqual(['busy']);
    q.get('q5', 'q5', 'now');
    await flush();
    expect(calls.map((c) => c.text)).toEqual(['busy', 'q5']);
    expect(calls[1].prio).toBe('now');
    calls[1].resolve('Q5');
    await expect(later).resolves.toBe('Q5');
  });

  it('a failed load resolves null and frees the lane', async () => {
    const calls: string[] = [];
    const q = new TtsQueue<string>(async (text) => {
      calls.push(text);
      if (text === 'bad') throw new Error('429');
      return text.toUpperCase();
    });
    await expect(q.get('bad', 'bad', 'warm')).resolves.toBeNull();
    await expect(q.get('ok', 'ok', 'warm')).resolves.toBe('OK');
    // a failed key can be asked again (no poisoned cache)
    await expect(q.get('bad', 'bad', 'now')).resolves.toBeNull();
    expect(calls).toEqual(['bad', 'ok', 'bad']);
  });

  it('close() settles everything still waiting and refuses new work', async () => {
    const { q, calls } = harness();
    q.get('a', 'a', 'warm');
    const waiting = q.get('b', 'b', 'warm');
    q.close();
    await expect(waiting).resolves.toBeNull();
    await expect(q.get('c', 'c', 'now')).resolves.toBeNull();
    expect(calls).toHaveLength(1);
    expect(q.pending).toBe(1); // the one in flight
  });
});
