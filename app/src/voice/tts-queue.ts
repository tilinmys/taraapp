// Sakhi's voice downloads, best priority first (phone AND web speakers).
//
// The voice provider (Gnani) takes ONE request at a time per key, shared by
// STT and TTS, so background downloads go one by one through a single lane:
//   now   the line Sakhi must say this moment: skips the lane entirely
//   next  the next question (look-ahead)
//   warm  every other line, in speaking order (pre-download on the intro)
// A line already queued is PROMOTED when it is needed sooner. The gateway
// applies the same order to everything it sends to the provider.

export type TtsPriority = 'now' | 'next' | 'warm';

const RANK: Record<TtsPriority, number> = { now: 0, next: 1, warm: 2 };

type Job<T> = { key: string; text: string; prio: TtsPriority; started: boolean; done: (v: T | null) => void };

export class TtsQueue<T> {
  private jobs = new Map<string, { job: Job<T>; promise: Promise<T | null> }>();
  private waiting: Job<T>[] = [];
  private lane = 0;
  private closed = false;

  constructor(private readonly load: (text: string, prio: TtsPriority) => Promise<T | null>) {}

  /** Background work left (queued + in flight). */
  get pending() {
    return this.waiting.length + this.lane;
  }

  /** The audio for `text` (one load per key, whoever asks). */
  get(key: string, text: string, prio: TtsPriority): Promise<T | null> {
    if (this.closed) return Promise.resolve(null);
    const known = this.jobs.get(key);
    if (known) {
      if (RANK[prio] < RANK[known.job.prio] && !known.job.started) {
        this.waiting = this.waiting.filter((j) => j !== known.job);
        known.job.prio = prio;
        if (prio === 'now') this.run(known.job);
        else this.enqueue(known.job);
      }
      return known.promise;
    }
    let done!: (v: T | null) => void;
    const promise = new Promise<T | null>((r) => (done = r));
    const job: Job<T> = { key, text, prio, started: false, done };
    this.jobs.set(key, { job, promise });
    if (prio === 'now') this.run(job);
    else this.enqueue(job);
    return promise;
  }

  close() {
    this.closed = true;
    this.waiting.forEach((j) => j.done(null));
    this.waiting = [];
  }

  private enqueue(job: Job<T>) {
    const at = this.waiting.findIndex((j) => RANK[j.prio] > RANK[job.prio]);
    if (at < 0) this.waiting.push(job);
    else this.waiting.splice(at, 0, job);
    this.pump();
  }

  private pump() {
    while (this.lane < 1 && this.waiting.length && !this.closed) {
      const job = this.waiting.shift() as Job<T>;
      this.lane++;
      this.run(job).finally(() => {
        this.lane--;
        this.pump();
      });
    }
  }

  private async run(job: Job<T>) {
    job.started = true;
    try {
      job.done(await this.load(job.text, job.prio));
    } catch {
      job.done(null);
    } finally {
      this.jobs.delete(job.key);
    }
  }
}
