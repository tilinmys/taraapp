# Design spec — Sakhi intro

Brand values taken directly from the real product (`frontend/src/app/page.module.css`), not invented.

## Palette

- Background: `#fdfaf6` (warm off-white)
- Surface / card: `#ffffff`
- Ink: `#1f2937`, ink-soft: `#52606d`
- Primary accent (teal): `#0f766e`, primary-soft: `#ccfbf1`
- Secondary mint: `#dcfce7`
- Line: `#e7e2db`

Light background — this is a warm healthcare-trust product, not tech/finance/cinema.

## Typography

Sans + mono pairing, chosen for a real content tension rather than decoration:
Montserrat (her voice — warm, human, geometric-friendly) against IBM Plex Mono
(the clinical record she creates — every reading becomes real, structured
data). Both are on HyperFrames' pre-bundled/embedded font list.

- Display / headlines: Montserrat 700–900
- Captions / small labels: IBM Plex Mono 400–700, tracked slightly wide, uppercase for short tags

## Focal element

Her real on-screen avatar (namaskara / talk_loop_1 / talk_loop_2 / idle_loop,
640x640 square source) centered in a soft rounded card, matching the app's
own `.avatarCard` treatment (28px radius, soft shadow) rather than full-bleed
video, so the piece reads as "this is the product," not a generic stock clip.

## Background treatment

Soft mint/teal radial glow behind the avatar card, slow ambient breathing
(scale 1 → 1.03, ~6s cycle) — never static, never distracting. A few thin
accent hairlines drifting slowly at low opacity for depth.

## Motion register

Calm, unhurried. Crossfades only (0.5–0.7s) between avatar clip segments and
between caption cards — no shader transitions, no hard cuts, no snap/slam
text entries. This is a rural-healthcare trust product, not a launch hype
reel.
