import { gestationWeeks, type Visit } from '@/data/mockScreening';
import { store } from '@/data/store';
import { STRINGS, type Lang } from '@/i18n/strings';

import { answerBody, deleteAnswer } from './answer-files';
import { voiceDb } from './db';
import { gateway } from './gateway';
import { short, vlog } from './log';
import { commandIn } from './nlu/commands';
import { Intake } from './nlu/intake';

// The global outbox: answers recorded with no network (stt_status pending /
// offline / failed, the WAV still on the phone) are transcribed when the
// phone is back online, from ANY screen (components/system/offline-banner.tsx
// calls flushOfflineOutbox on offline -> online, and Profile has "Sync now").
//
// For each owed answer, oldest first:
//   1. its WAV goes to the gateway (/stt) in the check-in's language;
//   2. the turn row gets the transcript (stt_status 'done', audio_uri cleared);
//   3. the WAV is deleted at once (DPDP: her voice is kept only until it is written);
//   4. the transcript is understood by the same NLU as a live answer and filed
//      into her record (store.updateTurnTranscript): the draft if the check-in
//      is still open, else its report (the same deterministic rules re-run).
// A check-in still running on this phone owns its own answers (the engine
// sends them itself), so the worker leaves those sessions alone.

const owned = new Set<string>();
/** The engine claims its session while it runs, so the worker never sends an answer twice. */
export const claimSession = (id: string) => owned.add(id);
export const releaseSession = (id: string) => owned.delete(id);

const isLang = (l: string): l is Lang => l === 'kn' || l === 'hi' || l === 'en';
// The conversation line an offline answer shows until it is written, in every language.
const PLACEHOLDERS = Object.values(STRINGS).map((s) => s.vSavedOffline);

let running: Promise<number> | null = null;

/**
 * Send every owed answer. Returns how many were transcribed. One flush at a
 * time: a second call while one runs gets the same promise. Stops at the
 * first network failure (the rest stay owed for the next flush).
 */
export function flushOfflineOutbox(): Promise<number> {
  running ??= drain().finally(() => {
    running = null;
  });
  return running;
}

async function drain(): Promise<number> {
  if (gateway.offline || !gateway.configured) return 0;
  let rows;
  try {
    rows = await voiceDb.outboxAll();
  } catch {
    return 0;
  }
  rows = rows.filter((r) => !owned.has(r.session_id));
  if (!rows.length) return 0;
  vlog('stt', `outbox: ${rows.length} owed answer(s) to send`);
  let sent = 0;
  for (const turn of rows) {
    if (gateway.offline) break;
    const uri = turn.audio_uri as string;
    const body = answerBody(uri);
    if (!body || !isLang(turn.lang)) {
      // Its audio is gone (an exited check-in deletes it): nothing to send, ever.
      await voiceDb.updateTurn(turn.session_id, turn.idx, { stt_status: 'skipped', audio_uri: null }).catch(() => {});
      continue;
    }
    const started = Date.now();
    let r;
    try {
      r = await gateway.stt(body, turn.lang);
    } catch (e) {
      vlog('err', `outbox stt ${turn.session_id}#${turn.idx}: ${String((e as Error)?.message ?? e)}; kept for later`);
      await voiceDb.updateTurn(turn.session_id, turn.idx, { stt_status: 'offline' }).catch(() => {});
      break;
    }
    const ms = Date.now() - started;
    await voiceDb.updateTurn(turn.session_id, turn.idx, { transcript: r.text, transcript_en: r.en, stt_status: 'done', stt_ms: ms, audio_uri: null }).catch(() => {});
    deleteAnswer(uri);
    sent++;
    vlog('stt', `outbox ok ${ms} ms ${turn.qid} "${short(r.text, 60)}"`);
    fileIt(turn.mother_id, turn.session_id, turn.qid, turn.question, (r.text || r.en).trim());
  }
  return sent;
}

/** Understand the late transcript like a live one and file it into her record. */
function fileIt(motherId: string, reportId: string, qid: string, question: string, said: string) {
  // "Say it again" / "skip" said to Sakhi is not an answer.
  if (!said || commandIn(said)) return;
  const m = store.get().find((x) => x.id === motherId);
  if (!m) return;
  const visit: Visit = m.reports.find((r) => r.id === reportId)?.visit ?? m.draftVisit ?? 1;
  const record = new Intake(visit, gestationWeeks(m)).answer(qid, said).record;
  store.updateTurnTranscript(motherId, reportId, { qid, question, text: said, record, placeholders: PLACEHOLDERS });
}
