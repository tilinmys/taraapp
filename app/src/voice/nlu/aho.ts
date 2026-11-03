// Aho–Corasick multi-pattern matcher. Built once from the whole lexicon
// (a few hundred Kannada / Hindi / English keywords); then ONE pass over an
// answer finds every keyword in it: O(text length + matches), however many
// keywords there are. Regex alternation over the same list would re-scan
// the text per alternative; this never backtracks.
//
// Code-unit based (JS strings), which is exact for Kannada and Devanagari
// (all in the Basic Multilingual Plane).

export type AcMatch<P> = { start: number; end: number; pattern: string; payload: P };

type Node<P> = { next: Map<string, number>; fail: number; out: { pattern: string; payload: P }[] };

export class AhoCorasick<P> {
  private nodes: Node<P>[] = [{ next: new Map(), fail: 0, out: [] }];

  constructor(entries: Iterable<[string, P]>) {
    for (const [pattern, payload] of entries) this.add(pattern, payload);
    this.link();
  }

  private add(pattern: string, payload: P) {
    if (!pattern) return;
    let n = 0;
    for (const ch of pattern) {
      let to = this.nodes[n].next.get(ch);
      if (to === undefined) {
        to = this.nodes.length;
        this.nodes.push({ next: new Map(), fail: 0, out: [] });
        this.nodes[n].next.set(ch, to);
      }
      n = to;
    }
    this.nodes[n].out.push({ pattern, payload });
  }

  /** Failure links by breadth-first search; outputs are merged along them. */
  private link() {
    const queue: number[] = [];
    for (const to of this.nodes[0].next.values()) {
      this.nodes[to].fail = 0;
      queue.push(to);
    }
    for (let qi = 0; qi < queue.length; qi++) {
      const n = queue[qi];
      for (const [ch, to] of this.nodes[n].next) {
        let f = this.nodes[n].fail;
        while (f !== 0 && !this.nodes[f].next.has(ch)) f = this.nodes[f].fail;
        const via = this.nodes[f].next.get(ch);
        this.nodes[to].fail = via !== undefined && via !== to ? via : 0;
        this.nodes[to].out.push(...this.nodes[this.nodes[to].fail].out);
        queue.push(to);
      }
    }
  }

  /** Every occurrence of every pattern in `text` (overlaps included), by end position. */
  search(text: string): AcMatch<P>[] {
    const found: AcMatch<P>[] = [];
    let n = 0;
    let i = 0;
    for (const ch of text) {
      i += ch.length;
      while (n !== 0 && !this.nodes[n].next.has(ch)) n = this.nodes[n].fail;
      n = this.nodes[n].next.get(ch) ?? 0;
      for (const o of this.nodes[n].out) found.push({ start: i - o.pattern.length, end: i, pattern: o.pattern, payload: o.payload });
    }
    return found;
  }

  get size() {
    return this.nodes.length;
  }
}

/**
 * Within one edit (insert, delete, substitute or swap of neighbours) of each
 * other? Banded, early exit: O(length). For STT misspellings of Latin
 * keywords ("headace", "convultion").
 */
export function withinOneEdit(a: string, b: string): boolean {
  if (a === b) return true;
  const la = a.length;
  const lb = b.length;
  if (Math.abs(la - lb) > 1) return false;
  let i = 0;
  while (i < la && i < lb && a[i] === b[i]) i++;
  if (la === lb) {
    if (a.slice(i + 1) === b.slice(i + 1)) return true; // substitute
    return a[i] === b[i + 1] && a[i + 1] === b[i] && a.slice(i + 2) === b.slice(i + 2); // swap
  }
  return la > lb ? a.slice(i + 1) === b.slice(i) : a.slice(i) === b.slice(i + 1); // delete / insert
}
