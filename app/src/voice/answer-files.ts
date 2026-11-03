import { Directory, File, Paths } from 'expo-file-system';

// Answer audio on the phone: one WAV per answer in cache/sakhi-audio/.
// Written once, uploaded straight from the file (the upload streams from
// disk), deleted as soon as its transcript is saved. Offline answers keep
// their file until the outbox sends it.

const dir = new Directory(Paths.cache, 'sakhi-audio');

/**
 * Write one answer. `uri` is known at once; `ready` settles when the bytes
 * are on disk (File.write is asynchronous since SDK 58: uploading before it
 * settles could send a half-written WAV). false = could not be written.
 */
export function saveAnswer(sessionId: string, idx: number, wav: Uint8Array): { uri: string; ready: Promise<boolean> } {
  if (!dir.exists) dir.create({ intermediates: true, idempotent: true });
  const file = new File(dir, `${sessionId}-${idx}.wav`);
  try {
    if (file.exists) file.delete();
    file.create();
  } catch {
    return { uri: file.uri, ready: Promise.resolve(false) };
  }
  return { uri: file.uri, ready: file.write(wav).then(() => true, () => false) };
}

/** The upload body: the file itself (a Blob), not its bytes. */
export function answerBody(uri: string): Blob | null {
  const file = new File(uri);
  return file.exists ? file : null;
}

export function deleteAnswer(uri: string | null) {
  if (!uri) return;
  try {
    const file = new File(uri);
    if (file.exists) file.delete();
  } catch {
    // already gone
  }
}
