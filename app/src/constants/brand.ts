// tara brand tokens. Built from the logo itself: its cream paper, its rose
// wordmark and its blush leaf, with one deep cherry reserved for actions.
// Every screen reads colours from here (NativeWind later too).
export const Brand = {
  // Surfaces
  porcelain: '#FFFFFF', // app canvas: clean white (the warm #FEFAF4 read as orange on phones)
  shell: '#F9F6F7', // cards and grouped surfaces: a neutral, barely-there tint on white
  cherryMist: '#F7E4E8', // selected / tinted backgrounds, cherry at ~10%
  surface: '#FFFFFF', // button and field fills: crisp, tactile, never washed-out glass
  outline: '#9A858C', // control borders: 3.3:1 on porcelain (meets WCAG 1.4.11 non-text 3:1)

  // Brand
  rose: '#A9636D', // the wordmark and the voice orb; decorative only, NEVER text
  blush: '#E8B4BC', // the Sakhi voice orb and decorative accents only (never text)
  cherry: '#A11D44', // primary actions. White text on it is 7.6:1 (AAA)
  cherryPressed: '#861636',
  pumpkin: '#C84B00', // warm second accent: duotone icon fills, the Sakhi orb, maternity. 4.9:1 on white
  pumpkinInk: '#8F3500', // pumpkin for TEXT on tints (orangeTint 7.3:1); pumpkin itself is 4.4:1 there
  plum: '#5B2A86', // reproductive-health accent (icons/tiles only)
  sage: '#2F6B4F', // fertility / "all good" accent, 6.3:1 on white

  // Bento tints (one hue per topic, never more than one per cell)
  orangeTint: '#FFF5F0',
  roseTint: '#FDF0F5',
  lilacTint: '#F4EEFA',
  sageTint: '#EEF6F1',
  band: '#F8F5F6', // workspace header band: neutral near-white, never orange
  bandLine: '#ECE4E7', // hairline under the band

  // Text
  ink: '#2B1B21', // headings and body: warm plum-black, 15.8:1 on porcelain
  inkMuted: '#5A464D', // secondary text: 8.7:1 on porcelain, 7.1:1 on cherryMist (AAA on every surface)
  inkSubtle: '#A3929A', // disabled states and decoration only (2.8:1, not for readable text)
  hairline: 'rgba(43, 27, 33, 0.10)',
  onCherry: '#FFFFFF',
} as const;

// Logo asset: transparent PNG cut from the master logo (922 x 554).
export const LOGO_SOURCE_WIDTH = 922;
export const LOGO_ASPECT = 922 / 554;

// Size the logo is built at during the brand intro.
export const SPLASH_LOGO_WIDTH = 240;

// The blush leaf: the intro's focus anchor. Its box inside the 922 x 554 logo
// (assets/images/logo-layers/tara-leaf.png is exactly this crop).
export const LEAF_BOX = { x: 832, y: 205, width: 63, height: 165 } as const;

// Must equal `imageWidth` of the expo-splash-screen plugin in app.json: the
// native splash shows the leaf alone, and the JS pre-loader draws it at
// exactly this size and position, so the hand-off is invisible.
export const LEAF_MARK_WIDTH = 22;

export const TAGLINE = 'A little guidance. A lot of care.';

// Four soft feminine surfaces, used ONLY for the mother's 2x2 weekly grid.
// Readable text on them is always Brand.ink / Brand.inkMuted (AA+).
export const FeminineGrid = {
  blossomPink: '#FCEEF2', // baby's senses
  peachNectar: '#FDF0E6', // mother's body & comfort
  lavenderMilk: '#F5EFFB', // daily nourishment
  pistachioCream: '#EEF6F0', // doctor & ASHA care
} as const;
