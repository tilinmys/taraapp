// A WebSocket stand-in for the network-layer tests: the test plays the
// server (open, send JSON / binary, drop) and reads what the app sent.

type Handler<E> = ((e: E) => void) | null;

export class FakeSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  static all: FakeSocket[] = [];
  static reset() {
    FakeSocket.all = [];
  }
  static last() {
    return FakeSocket.all[FakeSocket.all.length - 1];
  }

  readyState = FakeSocket.CONNECTING;
  binaryType = 'blob';
  sent: (string | ArrayBuffer)[] = [];
  closedWith: { code: number; reason: string } | null = null;
  onopen: Handler<unknown> = null;
  onmessage: Handler<{ data: unknown }> = null;
  onerror: Handler<unknown> = null;
  onclose: Handler<{ code: number; reason: string }> = null;

  constructor(readonly url = 'ws://test/ws') {
    FakeSocket.all.push(this);
  }

  // ---- what the app calls
  send(data: string | ArrayBuffer) {
    if (this.readyState !== FakeSocket.OPEN) throw new Error('send on a socket that is not open');
    this.sent.push(data);
  }
  close(code = 1000, reason = '') {
    if (this.readyState === FakeSocket.CLOSED) return;
    this.readyState = FakeSocket.CLOSED;
    this.closedWith = { code, reason };
    this.onclose?.({ code, reason });
  }

  // ---- what the test (the "server") does
  accept() {
    this.readyState = FakeSocket.OPEN;
    this.onopen?.({});
  }
  json(msg: Record<string, unknown>) {
    this.onmessage?.({ data: JSON.stringify(msg) });
  }
  binary(bytes: Uint8Array) {
    this.onmessage?.({ data: bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) });
  }
  drop(code = 1006) {
    this.readyState = FakeSocket.CLOSED;
    this.onclose?.({ code, reason: '' });
  }
  fail() {
    this.onerror?.({});
  }

  /** JSON messages the app sent, oldest first. */
  messages(): { t: string; [k: string]: unknown }[] {
    return this.sent.filter((x): x is string => typeof x === 'string').map((x) => JSON.parse(x));
  }
  binaries(): ArrayBuffer[] {
    return this.sent.filter((x): x is ArrayBuffer => typeof x !== 'string');
  }
}

/** A tiny valid WAV (44-byte header + n samples). */
export function wav(samples = 160) {
  const out = new Uint8Array(44 + samples * 2);
  out.set([0x52, 0x49, 0x46, 0x46], 0); // RIFF
  return out;
}
