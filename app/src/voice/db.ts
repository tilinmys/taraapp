import type { SessionRow, TurnRow, VoiceDb } from './db-types';

// Persistence on web (the browser preview): the same interface as
// db.native.ts, kept in memory. Phones use SQLite.

const sessions = new Map<string, SessionRow>();
const turns = new Map<string, TurnRow>();
const key = (s: string, i: number) => `${s}#${i}`;
const owed = new Set(['pending', 'offline', 'failed']);

export const voiceDb: VoiceDb = {
  async createSession(s) {
    sessions.set(s.id, { ...s, started_at: Date.now(), ended_at: null, status: 'live', pdf_uri: null });
  },
  async endSession(id, status) {
    const s = sessions.get(id);
    if (s) sessions.set(id, { ...s, status, ended_at: Date.now() });
  },
  async setPdf(id, uri) {
    const s = sessions.get(id);
    if (s) sessions.set(id, { ...s, pdf_uri: uri });
  },
  async upsertTurn(t) {
    turns.set(key(t.session_id, t.idx), { transcript: null, transcript_en: null, danger: 0, stt_ms: null, ...t } as TurnRow);
  },
  async updateTurn(sessionId, idx, patch) {
    const t = turns.get(key(sessionId, idx));
    if (t) turns.set(key(sessionId, idx), { ...t, ...patch });
  },
  async outbox(sessionId) {
    return [...turns.values()].filter((t) => t.session_id === sessionId && owed.has(t.stt_status)).sort((a, b) => a.idx - b.idx);
  },
  async turns(sessionId) {
    return [...turns.values()].filter((t) => t.session_id === sessionId).sort((a, b) => a.idx - b.idx);
  },
  async logEvent() {},
};
