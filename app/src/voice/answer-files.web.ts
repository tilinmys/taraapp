// Answer audio on web (preview): kept in memory, same interface as
// answer-files.ts. Freed as soon as its transcript is saved.

const store = new Map<string, Uint8Array>();

export function saveAnswer(sessionId: string, idx: number, wav: Uint8Array): { uri: string; ready: Promise<boolean> } {
  const uri = `mem://${sessionId}-${idx}.wav`;
  store.set(uri, wav);
  return { uri, ready: Promise.resolve(true) };
}

export function answerBody(uri: string): Blob | null {
  const bytes = store.get(uri);
  return bytes ? new Blob([bytes as BlobPart], { type: 'audio/wav' }) : null;
}

export function deleteAnswer(uri: string | null) {
  if (uri) store.delete(uri);
}
