import * as SQLite from 'expo-sqlite';

import type { SessionRow, TurnRow, VoiceDb } from './db-types';

// Persistence layer (phones): expo-sqlite, available in Expo Go.
// One database file (sakhi-voice.db), WAL journal (writes never block
// reads), opened once and reused. Every check-in is a session; every
// question a turn; the outbox is simply the turns whose transcript is still
// owed. Writes are small and run off the audio path.

let dbPromise: Promise<SQLite.SQLiteDatabase> | null = null;

function open() {
  dbPromise ??= (async () => {
    const db = await SQLite.openDatabaseAsync('sakhi-voice.db');
    await db.execAsync(`
      PRAGMA journal_mode = WAL;
      PRAGMA synchronous = NORMAL;
      CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY, mother_id TEXT NOT NULL, lang TEXT NOT NULL,
        started_at INTEGER NOT NULL, ended_at INTEGER, status TEXT NOT NULL,
        mode TEXT NOT NULL, pdf_uri TEXT
      );
      CREATE TABLE IF NOT EXISTS turns (
        session_id TEXT NOT NULL, idx INTEGER NOT NULL, qid TEXT NOT NULL,
        question TEXT NOT NULL, audio_uri TEXT, transcript TEXT, transcript_en TEXT,
        danger INTEGER NOT NULL DEFAULT 0, stt_status TEXT NOT NULL,
        ended_speech_at INTEGER, stt_ms INTEGER,
        PRIMARY KEY (session_id, idx)
      );
      CREATE INDEX IF NOT EXISTS turns_outbox ON turns (stt_status) WHERE stt_status IN ('pending', 'offline', 'failed');
      CREATE TABLE IF NOT EXISTS events (
        session_id TEXT NOT NULL, at INTEGER NOT NULL, kind TEXT NOT NULL, ms INTEGER NOT NULL
      );
    `);
    return db;
  })();
  return dbPromise;
}

const COLS: (keyof TurnRow)[] = ['audio_uri', 'transcript', 'transcript_en', 'danger', 'stt_status', 'ended_speech_at', 'stt_ms', 'question', 'qid'];

export const voiceDb: VoiceDb = {
  async createSession(s) {
    const db = await open();
    await db.runAsync(
      'INSERT OR REPLACE INTO sessions (id, mother_id, lang, started_at, status, mode) VALUES (?, ?, ?, ?, ?, ?)',
      s.id, s.mother_id, s.lang, Date.now(), 'live', s.mode,
    );
  },
  async endSession(id, status: SessionRow['status']) {
    const db = await open();
    await db.runAsync('UPDATE sessions SET status = ?, ended_at = ? WHERE id = ?', status, Date.now(), id);
  },
  async setPdf(id, uri) {
    const db = await open();
    await db.runAsync('UPDATE sessions SET pdf_uri = ? WHERE id = ?', uri, id);
  },
  async upsertTurn(t) {
    const db = await open();
    await db.runAsync(
      `INSERT OR REPLACE INTO turns (session_id, idx, qid, question, audio_uri, transcript, transcript_en, danger, stt_status, ended_speech_at, stt_ms)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      t.session_id, t.idx, t.qid, t.question, t.audio_uri ?? null, t.transcript ?? null, t.transcript_en ?? null,
      t.danger ?? 0, t.stt_status, t.ended_speech_at ?? null, t.stt_ms ?? null,
    );
  },
  async updateTurn(sessionId, idx, patch) {
    const keys = COLS.filter((k) => k in patch);
    if (!keys.length) return;
    const db = await open();
    const values = keys.map((k) => (patch[k] ?? null) as SQLite.SQLiteBindValue);
    await db.runAsync(`UPDATE turns SET ${keys.map((k) => `${k} = ?`).join(', ')} WHERE session_id = ? AND idx = ?`, ...values, sessionId, idx);
  },
  async outbox(sessionId) {
    const db = await open();
    return db.getAllAsync<TurnRow>("SELECT * FROM turns WHERE session_id = ? AND stt_status IN ('pending', 'offline', 'failed') ORDER BY idx", sessionId);
  },
  async turns(sessionId) {
    const db = await open();
    return db.getAllAsync<TurnRow>('SELECT * FROM turns WHERE session_id = ? ORDER BY idx', sessionId);
  },
  async logEvent(sessionId, kind, ms) {
    const db = await open();
    await db.runAsync('INSERT INTO events (session_id, at, kind, ms) VALUES (?, ?, ?, ?)', sessionId, Date.now(), kind, Math.round(ms));
  },
};
