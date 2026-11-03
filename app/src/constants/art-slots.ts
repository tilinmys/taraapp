import type { ImageSourcePropType } from 'react-native';

// ART SLOTS: every place in the app that is designed to take a real image
// or illustration later. Until an image is provided, the slot renders a
// branded icon in a tinted tile (see components/system/art-slot.tsx), so the
// layout already has the right size and gap.
//
// To add an image later:
//   1. Put the file in assets/images/art/ (PNG or WebP, transparent, 2x/3x).
//   2. Replace `null` below with require('@/assets/images/art/<file>.png').
//   3. Nothing else changes: size, radius and accessibility label stay.
//
// Recommended source sizes are listed per slot (square unless noted).

export const ART_SLOTS = {
  /** Role card: ASHA worker with a medical bag. 192x192. */
  roleAsha: require('@/assets/images/icons/asha.webp'),
  /** Role card: mother caring for her baby. 192x192. */
  roleMother: require('@/assets/images/icons/mother.webp'),
  /** Role card: doctor with a stethoscope. 192x192. */
  roleDoctor: require('@/assets/images/icons/doctor.webp'),
  /** ASHA check-in prep: a calm conversation scene. 600x360 (wide). */
  checkInPrep: null,
  /** Mother home: warm greeting illustration. 600x360 (wide). */
  motherHero: null,
  /** Empty states (no mothers / no cases). 480x320 (wide). */
  emptyList: null,
  /** Check-in complete: celebration / reassurance. 480x320 (wide). */
  checkInDone: null,
  /** Pregnancy tab growth card: warm baby-in-womb illustration, transparent WebP. 400x400. */
  babyGrowth: null,
} satisfies Record<string, ImageSourcePropType | null>;

export type ArtSlotName = keyof typeof ART_SLOTS;
