import { Easing } from 'react-native-reanimated';

// tara motion tokens. One source of truth for the brand intro and every
// entrance, so timings stay consistent across screens. Durations in ms.
//
// Character: botanical. Things grow and settle; nothing snaps or bounces
// hard. Springs are soft (low stiffness, damping ratio ~0.6-0.7), so a
// shape overshoots by a few percent and comes to rest like a leaf.

export const Curves = {
  /** Gentle breathing for the pre-loader pulse. */
  breathe: Easing.bezier(0.37, 0, 0.63, 1), // easeInOutSine
  /** Things arriving and settling: entrances, draw-on reveals. */
  settle: Easing.bezier(0.22, 1, 0.36, 1), // easeOutQuint
  /** Expanding masks: slow start, decisive middle, soft finish. */
  bloom: Easing.bezier(0.65, 0, 0.35, 1), // easeInOutCubic
  /** Fading something away. */
  exit: Easing.bezier(0.33, 0, 0.67, 1),
  /** Page-to-page slides: effortless start, long soft landing. */
  page: Easing.bezier(0.16, 1, 0.3, 1), // easeOutExpo-like
} as const;

export const Springs = {
  /** The leaf "seed" travelling into the logo: the main gesture. */
  leaf: { mass: 1, stiffness: 140, damping: 15 },
  /** The seed's acknowledgement pop when loading completes. */
  pop: { mass: 0.7, stiffness: 260, damping: 13 },
  /** Each letter of the wordmark rising into place. */
  letter: { mass: 1, stiffness: 190, damping: 18 },
} as const;

export const Intro = {
  // Phase 1: pre-loader
  pulseMs: 1100, // one half-breath (in or out)
  pulseScale: 0.94, // seed breathes between 94% and 100%
  pulseOpacity: 0.72,
  minPreloadMs: 300, // the seed is always seen "planted" for one breath

  // Phase 2: kinetic build (t = 0 when loading completes)
  popScale: 1.16,
  popMs: 160, // pop holds this long before the seed travels
  letterDelay: 120, // first letter starts here
  letterStagger: 70, // t -> a -> r -> a
  letterRise: 22, // pt of upward travel per letter
  letterFrom: 0.72, // letters grow from 72% scale
  letterFadeMs: 240,
  figureDelay: 260,
  figureDrawMs: 760, // the woman's figure draws on upward from the shoulders

  // Hold: full logo on screen until t = holdUntil
  holdUntil: 1200,

  // Phase 3: bloom wipe + dissolve
  bloomMs: 720,
  dissolveMs: 420,

  // Phase 4: interface entrance (starts as the dissolve begins)
  entranceDelay: 120,
  entranceStagger: 180,
  entranceMs: 520,
  entranceRise: 16,

  // Reduced motion: no travel, no scaling, just cross-fades.
  reducedFadeMs: 320,
} as const;

// Welcome screen choreography, starting when the intro's bloom covers the
// screen (t = 0).
export const Welcome = {
  heroDelay: 0,
  heroFrom: 0.95, // hero scales 0.95 -> 1.0 on Springs.hero (~600 ms settle)
  heroFadeMs: 450,
  textDelay: 150,
  textMs: 400,
  textRise: 20,
  primaryDelay: 300,
  primaryRise: 20,
  primaryFadeMs: 250,
  secondaryDelay: 400,
  secondaryMs: 320,
  altDelay: 500,
  altMs: 320,

  // Hero idle: logo floats -6 pt and back, 4 s per cycle; the orb breathes.
  floatRise: 6,
  floatHalfMs: 2000,
  orbHalfMs: 3200,
  orbScale: 1.05,

  // Sign-in morph: welcome buttons cross-fade into the login fields.
  morphOutMs: 180,
  morphInMs: 280,
  morphLayoutMs: 320,

  // Create-account push: screens slide across (native stack).
  pushMs: 350,
} as const;

export const Press = {
  downScale: 0.97,
  downMs: 100, // snappy ease-out on touch
  lift: -2, // magnetic lift on hover / keyboard focus
  liftMs: 160,
} as const;

export const MoreSprings = {
  /** Hero settling from 95%: soft, ~600 ms. */
  hero: { mass: 1, stiffness: 120, damping: 17 },
  /** Primary CTA arriving with a small, friendly bounce. */
  cta: { mass: 1, stiffness: 240, damping: 13 },
  /** Buttons releasing after a press. */
  release: { mass: 0.6, stiffness: 320, damping: 20 },
} as const;

// Onboarding stack (language -> role -> details).
export const Onboarding = {
  pageMs: 400, // horizontal parallax, Curves.page
  parallax: 0.35, // the receding page moves 35% of the screen width
  recedeScale: 0.96,
  recedeOpacity: 0.8,
  progressMs: 600, // progress bar fill, Curves.settle

  // Sticky CTA
  ctaDisabledOpacity: 0.5,
  ctaLabelOutMs: 110,
  ctaLabelInMs: 180,

  // Role card selection pulse 0.98 -> 1.02 -> 1.0
  pulseDown: 0.98,
  pulseUp: 1.02,
  pulseDownMs: 70,
  pulseUpMs: 120,

  // Speaking equaliser: three bars, staggered
  eqBarMs: 260,
  eqStagger: 90,

  // Language bottom sheet
  sheetBackdropMs: 220,
} as const;

export const OnboardingSprings = {
  /** CTA waking up when a choice is made. */
  ctaWake: { mass: 0.8, stiffness: 300, damping: 18 },
  /** Card settling back to 1.0 after the pulse. */
  cardSettle: { mass: 0.6, stiffness: 300, damping: 16 },
  /** Bottom sheet rising. */
  sheet: { mass: 1, stiffness: 300, damping: 30 },
} as const;

// App-wide physics from the interaction spec.
export const Physics = {
  /**
   * Page and step slides (role -> auth, phone -> OTP). Critically damped
   * (damping 27 at stiffness 180): a smooth glide that never overshoots or
   * wobbles at the end.
   */
  page: { damping: 27, stiffness: 180, mass: 1 },
  /** Press-in of any card or the primary button, and its release. */
  pressScale: 0.97,
  press: { stiffness: 300, damping: 25 },
  /** Radio check badge popping in (scale 0.4 -> 1, rotate -15deg -> 0). */
  check: { damping: 12, stiffness: 220, mass: 1 },
  checkFrom: 0.4,
  checkRotateFrom: -15,
  /** Card selection colour cross-fade. */
  selectMs: 200,
  /** Status text swaps (Listening... / Playing...). */
  status: { damping: 16, stiffness: 200, mass: 0.8 },
  statusRise: 10,
  /** Voice pulse ring. */
  pulseMs: 1600,
  pulseScale: 1.9,
  /** Sheet background scale. */
  sheetBackScale: 0.96,
} as const;

// Signature motion set (applied app-wide).
export const Signature = {
  /** 8. Breathing idle: voice icons / orb, 1.0 <-> 1.12 over 1600 ms. */
  breatheScale: 1.12,
  breatheMs: 1600,
  /** 10. Concentric pulse rings while audio is live. */
  ringScale: 1.25,
  ringOpacity: 0.4,
  ringMs: 1400,
  /** 4. Icon morph inside buttons (spring rotate + scale). */
  morph: { damping: 14, stiffness: 220, mass: 0.8 },
  /** 7. Long-press focus: lift + scale of the held card. */
  focusScale: 1.03,
  longPressMs: 380,
  /** 6. Drag-to-reveal threshold (pt) before the card unboxes. */
  revealDistance: 90,
  /** 9. Large title collapses into the nav bar over this scroll distance. */
  titleCollapse: 56,
} as const;

/**
 * Page motion: quick and quiet. Moving between pages is something she does,
 * not something she watches, so nothing travels far or lingers:
 *   push      220 ms native slide for every stack screen
 *   tab       180 ms, 8 pt glide when a tab comes into focus
 *   rise      blocks settle 10 pt in 200 ms, 30 ms apart, at most 4 steps
 *             (the last block has landed by ~320 ms)
 */
export const Nav = { push: 220, tab: 180, tabTravel: 8, rise: 200, riseTravel: 10, stagger: 30, maxStagger: 4 } as const;
