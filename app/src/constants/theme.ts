// tara theme: one import for screens. Re-exports the source-of-truth token
// files (brand, typography, motion) and adds the layout and icon rules.
//
// Strict rules (enforced by review, documented here):
//   - Rose and the script face are decorative only: never for readable text.
//   - No glass / blur overlays over text.
//   - One 56 pt cherry primary action per screen, in the sticky bottom bar.
//   - Sections stack vertically; never nest a card inside a card. Group
//     details inside a card with whitespace or 1 px hairline dividers.
//   - One icon family (Phosphor, components/system/icon.tsx): regular weight
//     when inactive, fill when active.

export { Brand as Colors } from '@/constants/brand';
export {
  Curves,
  Intro,
  MoreSprings,
  Nav,
  Onboarding,
  OnboardingSprings,
  Physics,
  Press,
  Signature,
  Springs,
  Welcome,
} from '@/constants/motion';
export { Families, Type, fontFor } from '@/constants/typography';
export { Haptic, haptic } from '@/lib/haptics';
export { useContentWidth } from '@/hooks/use-content-width';

export const Layout = {
  maxWidth: 520, // phones; tablets widen to maxWidthTablet (see useContentWidth)
  maxWidthTablet: 720,
  tabletBreakpoint: 768,
  gutter: 24, // horizontal margin grid
  gap: 16, // default vertical rhythm between sections
  ctaHeight: 56, // the primary button, everywhere
  minTouch: 44, // inline icon buttons
  cardMinHeight: 72, // selectable cards
  radius: 16, // cards and buttons
  hairline: 1,
} as const;

export const Icon = {
  stroke: 1.5, // inactive / unselected
  strokeActive: 2.25, // active / selected
  size: 24,
} as const;
