"use client";

/*
 * Sakhi's video avatar — four pre-rendered clips, crossfaded on a live call.
 *
 * WHAT THIS IS AND ISN'T
 * -----------------------
 * There is no real-time avatar API here (Tavus/Bey/D-ID) and no per-turn
 * lip-sync inference. Both cost money per minute or per turn, which is the
 * exact thing this replaces. Instead: four short clips, generated once,
 * bundled as static assets, swapped client-side by CALL STATE
 * (listening/thinking/speaking) rather than by matching actual words.
 *
 * That means the mouth movement is generic, not phoneme-accurate. It moves
 * WHEN she is speaking, not shaped to WHAT she is saying. This is a stated
 * trade-off, not an oversight — true lip-sync would need the API cost this
 * whole approach exists to avoid. In a voice-primary UI where the video is
 * peripheral to the audio, this reads as "alive," not as "wrong."
 *
 * THE ONE THING THAT IS GENUINELY SYNCED
 * ----------------------------------------
 * The namaskara gesture's ONSET. It plays exactly when LiveKit reports the
 * agent has started producing audio for the first time this call — not on a
 * client-side timer guessing when the greeting will start.
 *
 * WHY "FRONT SLOT" IS A REF, NOT REACT STATE (this was a real, live bug)
 * ------------------------------------------------------------------------
 * The first version tracked which of the two <video> elements is currently
 * visible with `useState`, and read it back inside crossfadeTo() to decide
 * which element is "front" and which is "back". That is unsafe: React state
 * updates are asynchronous, so two crossfadeTo() calls landing close
 * together — which happens routinely, e.g. listening -> thinking ->
 * listening inside one second — can BOTH read the same stale "front"
 * value before either has re-rendered. Both then animate toward the same
 * back element and each flips the front/back bookkeeping once; two flips
 * cancel out. The net result, confirmed by inspecting the live DOM after
 * triggering exactly this sequence: the element the CSS says is visible
 * (opacity 1) is paused and ended on its last frame, while the element that
 * is actually still playing forward sits at opacity 0, invisible. That is
 * the exact "getting stuck" symptom reported from a real call.
 *
 * The fix is to take React out of the hot path entirely. `frontSlotRef` is a
 * plain ref, flipped SYNCHRONOUSLY the instant a crossfade is dispatched —
 * not after an async `loadeddata` wait, and not through a re-render. Any
 * call arriving before the previous one's visual reveal, even in the same
 * tick, sees the updated assignment immediately. Opacity is written directly
 * via `el.style.opacity`, never through a React-controlled `style` prop, so
 * there is no render-cycle latency between "decide" and "visible" either.
 * React state still exists in this file (none, actually — see below) only
 * where nothing time-critical depends on it.
 *
 * ENGINEERING CONSTRAINTS (deliberate, not incidental)
 * ------------------------------------------------------
 * - Exactly TWO <video> DOM elements exist, ever, for the whole component's
 *   life. Never four. Some Android WebViews cap concurrent live video
 *   decoder instances in the single digits; staying at 2 is a hard,
 *   permanent ceiling regardless of device.
 * - The four source files are prefetched into the HTTP cache (plain fetch(),
 *   not <video> elements) as soon as this component mounts, so that by the
 *   time a role is actually needed, `.load()` on the real <video> element
 *   resolves from cache rather than from the network. Prefetching is
 *   fire-and-forget and never blocks anything on the call's critical path.
 * - Every role switch — including a clip looping back to its own start —
 *   goes through ONE function, crossfadeTo(). A self-loop is just a
 *   transition whose target happens to equal its source. This is
 *   deliberate: native `<video loop>` can show a visible decoder-restart
 *   hitch on lower-end hardware, and one crossfade primitive that handles
 *   both cases is simpler and safer than maintaining two.
 * - All transitions animate `opacity` only (GPU-composited, no layout, no
 *   paint of surrounding content) — never a property that triggers layout.
 * - Every video is `muted` — required for autoplay on mobile Chrome/Safari
 *   regardless of whether the source has audio, and these have none: the
 *   real voice is the WebRTC audio track, mixed by LiveKit exactly as before
 *   this component existed. This is 100% client-side and touches nothing in
 *   the STT/LLM/TTS pipeline.
 * - Any failure (a clip 404s, `.play()` rejects, decode error) reports up via
 *   `onFatalError` so the caller can fall back to the static idle card,
 *   rather than showing a frozen or broken video mid-call.
 */

import { useCallback, useEffect, useRef } from "react";
import styles from "../app/page.module.css";

export type AgentVoiceState = "listening" | "thinking" | "speaking" | "idle" | "connecting" | string;

type Role = "namaskara" | "idle" | "talk1" | "talk2";
type Slot = "A" | "B";

const SOURCES: Record<Role, string> = {
  namaskara: "/sakhi/namaskara.mp4",
  idle: "/sakhi/idle_loop.mp4",
  talk1: "/sakhi/talk_loop_1.mp4",
  talk2: "/sakhi/talk_loop_2.mp4",
};

// namaskara is a one-shot gesture (rising into namaste) - looping it would
// look like a nervous tic, not a greeting. The other three are seamless
// loops by construction (cut from a settled resting pose / a repeating
// speech cadence) and self-loop via crossfadeTo, not the `loop` attribute.
const LOOPS: Record<Role, boolean> = {
  namaskara: false,
  idle: true,
  talk1: true,
  talk2: true,
};

const CROSSFADE_MS = 220;
// Start the next loop iteration this long before the current one's natural
// end, so the crossfade completes at (or just before) the seam rather than
// starting only once playback has already hit the end - starting late is
// what produces a visible freeze-then-jump.
const LOOP_LEAD_S = 0.28;

function nextTalkRole(previous: Role | null): Role {
  return previous === "talk1" ? "talk2" : "talk1";
}

export function SakhiVideoAvatar({
  voiceState,
  onFatalError,
}: {
  voiceState: AgentVoiceState;
  onFatalError: () => void;
}) {
  const slotARef = useRef<HTMLVideoElement | null>(null);
  const slotBRef = useRef<HTMLVideoElement | null>(null);

  // Flipped SYNCHRONOUSLY at crossfade dispatch, before any wait. Used only
  // by handleTimeUpdate/handleEnded's isFront checks below, so the outgoing
  // element's own timeupdate events stop being treated as "front" the
  // instant a new crossfade is dispatched - not only once it finishes. Not
  // used to pick which element a crossfade targets any more; see
  // visibleSlotRef for that. Deliberately a ref, not React state - see the
  // file header for why.
  const frontSlotRef = useRef<Slot>("A");

  // The slot that is actually PAINTED on screen right now. Unlike
  // frontSlotRef, this only updates once a crossfade's commit() actually
  // runs (after a real painted frame is confirmed - see crossfadeTo). This
  // is what crossfadeTo reads to decide which element is "front" (visible,
  // fading out) and which is "back" (the new target) - so a second
  // crossfadeTo() landing while an earlier one is still mid-wait-for-paint
  // computes the same back element as the first call, instead of racing it
  // for a different one. See the white-flash fix comment above crossfadeTo.
  const visibleSlotRef = useRef<Slot>("A");

  // Bumped on every crossfadeTo() dispatch. A commit() captures the token at
  // dispatch time and checks it still matches before flipping opacity - if a
  // later crossfadeTo() call has redirected the same target element to a
  // different role in the meantime, this commit is stale and becomes a
  // no-op instead of revealing content that no longer matches what was
  // requested.
  const revealTokenRef = useRef(0);

  const currentRoleRef = useRef<Role | null>(null);
  const pendingRoleRef = useRef<Role | null>(null);
  const lastTalkRoleRef = useRef<Role | null>(null);
  const hasGreetedRef = useRef(false);
  const erroredRef = useRef(false);

  const elFor = useCallback((slot: Slot) => (slot === "A" ? slotARef.current : slotBRef.current), []);

  const reportFatal = useCallback(() => {
    if (erroredRef.current) return;
    erroredRef.current = true;
    onFatalError();
  }, [onFatalError]);

  // --- prefetch: warm the HTTP cache for all four clips, once -------------
  // Plain fetch(), no <video> element involved, so this never touches the
  // "at most two live decoders" budget below. Errors are swallowed - a
  // failed prefetch just means the later .load() falls back to a normal
  // network fetch instead of a cache hit, not a broken avatar.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      for (const src of Object.values(SOURCES)) {
        if (cancelled) return;
        try {
          await fetch(src, { cache: "force-cache" });
        } catch {
          // best-effort only
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // --- the one crossfade primitive ----------------------------------------
  //
  // WHY THIS WAITS FOR AN ACTUALLY-PAINTED FRAME (the white-flash fix)
  // ---------------------------------------------------------------------
  // `readyState >= 2` / the `loadeddata` event both mean decoded frame DATA
  // exists for the current playback position - neither guarantees the
  // compositor has actually PAINTED that frame to the screen yet. Right
  // after `.src`/`currentTime = 0` reset the element, there is a real gap
  // where it paints nothing (transparent), exposing `.avatarCard`'s
  // near-white background gradient through it - read as "a white flash",
  // most often during the talk1<->talk2 self-loop restart that fires every
  // ~3.5-3.9s throughout any spoken response. Confirmed via direct frame
  // extraction that the source clips themselves contain no white frames -
  // this is a reveal-timing race, not an asset defect.
  //
  // The fix waits for `requestVideoFrameCallback` (a real "this frame was
  // painted" signal, Chromium/Android WebView - this file's stated primary
  // target) before flipping opacity, with a double-`requestAnimationFrame`
  // fallback where that API doesn't exist.
  //
  // THE SECOND BUG THIS INTRODUCES, AND HOW visibleSlotRef/revealTokenRef FIX IT
  // ---------------------------------------------------------------------------
  // Before this fix, when a clip is already prefetched (the common case),
  // the whole reveal ran SYNCHRONOUSLY in one JS tick - a second
  // crossfadeTo() call physically could not interleave. Waiting for a
  // painted frame makes EVERY crossfade async, including that common case -
  // which reopens the exact double-crossfade race this file's header
  // already describes fixing once before (see the top-of-file comment),
  // now on a routine cadence instead of a rare one.
  //
  // frontSlotRef alone is not enough to prevent it: it flips synchronously
  // at DISPATCH time (still needed, unchanged, by handleTimeUpdate/
  // handleEnded's isFront checks), so a second call landing mid-wait would
  // read an already-flipped "front" that is not what is actually painted,
  // and target the wrong element. visibleSlotRef instead only updates at
  // COMMIT time (after paint is confirmed), so crossfadeTo always computes
  // "back" as whichever slot is genuinely not on screen yet - a second call
  // landing mid-wait converges on the same target as the first, rather than
  // racing it for a different one. revealTokenRef then makes a superseded
  // commit a no-op instead of flipping opacity onto stale content.
  const crossfadeTo = useCallback((role: Role) => {
    if (erroredRef.current) return;
    // Only guard against a transition already in flight to this exact
    // target. Redundant same-role requests from ordinary voice-state churn
    // (e.g. listening -> thinking, both mapping to "idle") are filtered by
    // the CALLER before reaching here - see the voice-state effect below -
    // so this primitive stays unconditional for the loop-restart scheduler,
    // which must always be able to fire.
    if (pendingRoleRef.current === role) return;

    const front = visibleSlotRef.current;
    const frontEl = elFor(front);
    const backSlot: Slot = front === "A" ? "B" : "A";
    const backEl = elFor(backSlot);
    if (!backEl) return;

    pendingRoleRef.current = role;
    frontSlotRef.current = backSlot;
    const myToken = ++revealTokenRef.current;

    backEl.src = SOURCES[role];
    backEl.currentTime = 0;
    backEl.muted = true;

    const commit = () => {
      if (revealTokenRef.current !== myToken) return; // superseded - no-op
      // Direct DOM writes, not a React-controlled style prop - see the
      // mount-effect comment below for why a JSX style prop here would
      // actively fight this.
      backEl.style.opacity = "1";
      if (frontEl) frontEl.style.opacity = "0";
      visibleSlotRef.current = backSlot;
      currentRoleRef.current = role;
      pendingRoleRef.current = null;

      window.setTimeout(() => {
        try {
          frontEl?.pause();
        } catch {
          /* element may already be gone */
        }
      }, CROSSFADE_MS + 30);
    };

    const waitForPaintedFrame = () => {
      if (revealTokenRef.current !== myToken) return; // superseded already
      const withRvfc = backEl as HTMLVideoElement & {
        requestVideoFrameCallback?: (cb: () => void) => number;
      };
      if (typeof withRvfc.requestVideoFrameCallback === "function") {
        withRvfc.requestVideoFrameCallback(() => commit());
      } else {
        // Firefox / older Safari fallback: no real paint-confirmation API,
        // so wait two animation frames after play() resolves as the closest
        // approximation of "at least one paint cycle has happened".
        requestAnimationFrame(() => requestAnimationFrame(() => commit()));
      }
    };

    const afterLoaded = () => {
      const playAttempt = backEl.play();
      const played =
        playAttempt && typeof playAttempt.then === "function"
          ? playAttempt
          : Promise.resolve();
      played.then(waitForPaintedFrame).catch(() => reportFatal());
    };

    if (backEl.readyState >= 2) {
      afterLoaded();
    } else {
      backEl.addEventListener(
        "loadeddata",
        () => {
          afterLoaded();
        },
        { once: true }
      );
      backEl.load();
    }
  }, [elFor, reportFatal]);

  // --- self-looping: schedule the next crossfade before the seam ----------
  // "isFront" is resolved fresh from the ref at the moment the event fires,
  // never captured from a render - the same staleness class of bug as the
  // frontSlot state it replaced would otherwise reappear here.
  const handleTimeUpdate = useCallback((el: HTMLVideoElement) => {
    if (erroredRef.current) return;
    const isFront = el === elFor(frontSlotRef.current);
    if (!isFront) return;
    const role = currentRoleRef.current;
    if (!role || !LOOPS[role]) return;
    if (!Number.isFinite(el.duration) || el.duration <= 0) return;
    if (el.duration - el.currentTime > LOOP_LEAD_S) return;

    // No separate "armed" flag here on purpose - a version that set one
    // before calling crossfadeTo() had a real bug: if that call was rejected
    // by crossfadeTo's own in-flight guard (which happens whenever a
    // voice-state transition lands in the same window as a loop boundary),
    // the flag never got cleared, because its reset lived on the other side
    // of that same guard. The role then stopped self-looping for the rest of
    // the session - the clip played once more, hit `ended`, and froze on its
    // last frame. That was "getting stuck," reproduced and confirmed live.
    //
    // crossfadeTo's own `pendingRoleRef.current === role` check already
    // makes repeated calls in this 0.28s window free once the first one is
    // in flight, and frontSlotRef flips synchronously at dispatch time so
    // the outgoing element's own timeupdate events fail the isFront check
    // above immediately - between the two, nothing else needs to gate this.
    crossfadeTo(role);
  }, [crossfadeTo, elFor]);

  const handleEnded = useCallback((el: HTMLVideoElement) => {
    if (erroredRef.current) return;
    const isFront = el === elFor(frontSlotRef.current);
    if (!isFront) return;
    if (currentRoleRef.current !== "namaskara") return;
    // The greeting gesture finished. Hand off to whatever the CURRENT voice
    // state actually calls for right now, not to a value captured when the
    // gesture started three seconds ago - she may already be listening.
    const role = voiceState === "speaking" ? nextTalkRole(lastTalkRoleRef.current) : "idle";
    if (voiceState === "speaking") lastTalkRoleRef.current = role;
    crossfadeTo(role);
  }, [crossfadeTo, elFor, voiceState]);

  // --- voice-state -> role -------------------------------------------------
  const prevVoiceStateRef = useRef<AgentVoiceState>(voiceState);
  useEffect(() => {
    const prev = prevVoiceStateRef.current;
    prevVoiceStateRef.current = voiceState;

    if (voiceState === "speaking") {
      if (prev !== "speaking") {
        // Edge-triggered: the talk1/talk2 choice is made once per turn, not
        // once per internal loop restart, so a long turn doesn't visibly
        // swap rhythm mid-sentence.
        if (!hasGreetedRef.current) {
          hasGreetedRef.current = true;
          crossfadeTo("namaskara");
          return;
        }
        const role = nextTalkRole(lastTalkRoleRef.current);
        lastTalkRoleRef.current = role;
        crossfadeTo(role);
      }
      return;
    }

    if (currentRoleRef.current === "namaskara") return; // let it finish; handleEnded takes over

    // Redundant-target guard lives HERE, not inside crossfadeTo, precisely
    // so it can never suppress the loop-restart scheduler in
    // handleTimeUpdate. This effect fires on every voiceState change, and
    // several states map to the same "idle" target - listening -> thinking
    // -> listening would otherwise request three redundant fades for a role
    // already on screen.
    if (currentRoleRef.current === "idle" && pendingRoleRef.current === null) return;
    crossfadeTo("idle");
  }, [voiceState, crossfadeTo]);

  // --- initial mount: come up on idle, muted, silent ----------------------
  useEffect(() => {
    // Opacity for both elements is owned ENTIRELY by imperative DOM writes
    // from here on, never by a React `style` prop. If it were a JSX style
    // prop, React would re-apply its literal value on every re-render - and
    // this component re-renders on every voiceState change, i.e.
    // constantly - silently reverting whatever crossfadeTo() had just set
    // and undoing every transition the instant the next render landed. The
    // CSS class supplies a same-as-slot-A default (see .avatarVideoLayer)
    // purely so slot A does not flash blank before this effect runs; slot B
    // is hidden here, once, and neither is ever touched by JSX again.
    const front = slotARef.current;
    const back = slotBRef.current;
    if (back) back.style.opacity = "0";
    if (!front) return;
    front.src = SOURCES.idle;
    front.muted = true;
    front.style.opacity = "1";
    currentRoleRef.current = "idle";
    const onReady = () => {
      front.play().catch(() => reportFatal());
    };
    if (front.readyState >= 2) onReady();
    else front.addEventListener("loadeddata", onReady, { once: true });
    return () => front.removeEventListener("loadeddata", onReady);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className={`${styles.avatarCard} ${styles.avatarCardVideo}`}>
      {/* No `style` prop on either element - opacity is imperative-only,
          set by the mount effect and by crossfadeTo(). See the comment on
          the mount effect for why a React-controlled style prop here would
          actively break every transition. */}
      <video
        ref={slotARef}
        className={styles.avatarVideoLayer}
        muted
        playsInline
        preload="auto"
        onTimeUpdate={(e) => handleTimeUpdate(e.currentTarget)}
        onEnded={(e) => handleEnded(e.currentTarget)}
        onError={reportFatal}
      />
      <video
        ref={slotBRef}
        className={styles.avatarVideoLayer}
        muted
        playsInline
        preload="auto"
        onTimeUpdate={(e) => handleTimeUpdate(e.currentTarget)}
        onEnded={(e) => handleEnded(e.currentTarget)}
        onError={reportFatal}
      />
    </div>
  );
}
