// The shape of the voice store (SQLite on phones, memory on web).

export type SttStatus = 'pending' | 'done' | 'offline' | 'failed' | 'skipped' | 'demo' | 'prefilled';

export type TurnRow = {
  session_id: string;
  idx: number;
  qid: string;
  question: string;
  audio_uri: string | null;
  transcript: string | null;
  transcript_en: string | null;
  danger: number;
  stt_status: SttStatus;
  ended_speech_at: number | null;
  stt_ms: number | null;
};

export type SessionRow = {
  id: string;
  mother_id: string;
  lang: string;
  started_at: number;
  ended_at: number | null;
  status: 'live' | 'done' | 'abandoned';
  mode: 'live' | 'demo';
  pdf_uri: string | null;
};

/** An owed answer across every check-in, with what the outbox needs to send it. */
export type OutboxRow = TurnRow & { lang: string; mother_id: string };

/** Field diagnostics (no answers, no audio: counts and timings only). */
export type Diagnostics = { totalSessions: number; completedTurns: number; avgLatencyMs: number; owedOutbox: number };

export type VoiceDb = {
  createSession(s: Pick<SessionRow, 'id' | 'mother_id' | 'lang' | 'mode'>): Promise<void>;
  endSession(id: string, status: SessionRow['status']): Promise<void>;
  setPdf(id: string, uri: string): Promise<void>;
  upsertTurn(t: Omit<TurnRow, 'transcript' | 'transcript_en' | 'danger' | 'stt_ms'> & Partial<TurnRow>): Promise<void>;
  updateTurn(sessionId: string, idx: number, patch: Partial<TurnRow>): Promise<void>;
  /** Answers whose transcript is still owed (offline outbox). */
  outbox(sessionId: string): Promise<TurnRow[]>;
  /** Every owed answer that still has its audio, oldest first (the global outbox). */
  outboxAll(): Promise<OutboxRow[]>;
  turns(sessionId: string): Promise<TurnRow[]>;
  getDiagnostics(): Promise<Diagnostics>;
  logEvent(sessionId: string, kind: string, ms: number): Promise<void>;
};
