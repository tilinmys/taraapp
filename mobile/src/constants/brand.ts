// MyStree / tara brand tokens. One place for colours, so every screen (and
// NativeWind later) reads the same values.
export const Brand = {
  botanicalMidnight: '#0C161D', // splash canvas, primary text on light screens
  alabaster: '#FAF8F5', // app canvas
  cadetSlate: '#8BA4BF', // secondary text on dark, captions
  brandFlame: '#ED5B2D', // primary actions
  taraMauve: '#9E606E', // logotype on light screens
  ink70: 'rgba(12, 22, 29, 0.72)', // body text on alabaster
  ink12: 'rgba(12, 22, 29, 0.12)', // hairlines
} as const;

// Logotype assets: transparent PNGs cut from the master logo (1088 x 424).
export const LOGO_ASPECT = 1088 / 424;

// Must equal `imageWidth` of the expo-splash-screen plugin in app.json: the
// animated splash draws the logo at exactly the native splash's size and
// position, so the hand-off from native to JS is invisible.
export const SPLASH_LOGO_WIDTH = 200;

// Point on the logotype the splash zooms through: the stem of the "r",
// the widest solid stroke, as a fraction of the logo box.
export const LOGO_ZOOM_ORIGIN = { x: 0.568, y: 0.738 } as const;

export const TAGLINE = 'A little guidance. A lot of care.';
