---
workflow: general-video
flow: automation
storyboard: yes
message: "An ASHA in Bidar speaks with Sakhi in Kannada, and a danger sign is caught on the spot."
length: 45s (exactly)
aspect: 16:9
format: 1920x1080
fps: 30
language: en (titles and subtitles), kn (Sakhi's voice and app UI)
audience: UNICEF Femtech Ventures reviewers and anyone outside the project
destination: the product-demo segment in the middle of the 2-minute pitch film
---

## Intent

The user's plan, in their words: "show a globe rotate and show india map and come to
karnataka area bidar district and pin there and then directly show our apps use case
show some clicks and some talks of sakhi, with the aasha just a demo it should have an
nice impact, so that someone viewing from outside also gets an idea about the product".
"45 second sharp". "the animation should be very nice and realistic". "use latest tara
logo for animation". "the person viewing it in 45 second must get an idea of what we
have built".

Source plan: `Tara_UNICEF_Femtech_Pitch_Script.pdf` (Downloads). From it: Lakshmi D.,
7 months, BP 148/96; Sunita the ASHA; Sakhi asks the BP in Kannada; rule PE-04 fires;
one tap to the doctor. Brand line "Every danger sign, heard in time." (end card only).
All patient data shown is fictional demo data.

## Assets

- Logo: `app/assets/images/tara-logo.webp` (latest, 2026-10-21) and its layers in
  `app/assets/images/logo-layers/` (t, a1, r, a2, figure, leaf) for a built logo.
- Brand tokens: `app/src/constants/brand.ts` (cherry #A11D44, ink #2B1B21, plum
  #5B2A86, cherryMist #F7E4E8, porcelain #FFFFFF).
- Sakhi's voice: the app's recorded Kannada lines, `app/assets/voice/kn/` (Gnani Kaveri).
- App screens: captured from the running Tara app (Expo web, localhost:8081).
- Every asset the video uses is copied into `video/assets/` (user requirement).

## Customizations

- Real map: globe to Bidar on real satellite imagery (bake-basemap), real coordinates for the pin.
- Logo built from its layered pieces.
- Audio: Sakhi's real voice + soft UI sound effects; burned-in English subtitles; no music.

## Notes

- Storyboard first: plan + stills, approval before animating.
- The ASHA's spoken answer is shown as text + waveform, not a synthetic voice (no fake recording).
