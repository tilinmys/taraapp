# Review: Point-of-care biomarker screening (10 s, 1920×1080, 30 fps)

## What it is
Two scenes on one cream ground (a mauve sweep top-left, a blush wave bottom-right, after the reference still):

| Time | Beat | Title |
| --- | --- | --- |
| 0.0–2.4 s | The device settles on the counter; a dropper places one drop on the cartridge's sample well | POINT-OF-CARE / **BIOMARKER** / SCREENING |
| 2.4–3.5 s | The screen reads the sample (progress bar) | (title 1 holds) |
| 3.6–5.2 s | Result card: sFlt-1 / PlGF ratio, marker in the high zone, "Elevated risk · refer", one soft ring (the subtle alert) | EARLY RISK / **DETECTION** |
| 5.25–6.75 s | The result card leaves the device, comes to the viewer, then settles into a doctor's monitor (over-the-shoulder) | |
| 6.55–7.6 s | | PROMPT / **REFERRAL** |
| 6.95–8.0 s | A referral line runs from the monitor to a calm, generic hospital (no emblem, no name) | |
| 7.8–10.0 s | Held | TIMELY MEDICAL / **INTERVENTION** (2.1 s hold) |

## Creative calls
- Everything is drawn (SVG/CSS), not taken from the reference image: the reference has its title baked in and is too small to upscale cleanly. Its look is kept: cream/plum device on a counter, spaced caps + one serif word per title (Montserrat + Playfair Display).
- The biomarker named on the card is the sFlt-1/PlGF ratio, the real pre-eclampsia marker. **No value is shown**: the gauge marker position is illustrative.
- Transition: the result card is the object that carries the film from the device to the doctor (no crossfade between worlds).
- The "alert" is a rose pill and a single expanding ring: no red, no flashing, no repeat.
- No sound (10 s, no narration asked). Music or a soft UI tick can be added on request.

## Measured / looked at
- `check`: passes; layout, motion, contrast clean.
- Looked at: contact sheets at 11 instants, the seam strip at 6 s (card identical across the cut), the phone-width sheet (all four titles readable at 360 px).
- Kept on purpose: `nested_structure_needs_subcomposition` on `#ground` (a static decorative background with one SVG; splitting it into a scene adds nothing). The card's small UI text is marked `data-layout-ignore`: it is screen texture, the titles carry the message.

## Weakest shot / with more time
- The doctor is a simple back-of-head silhouette; a more detailed illustrated figure (or a matched illustration asset) would raise scene 2.
- The hospital is small next to the monitor; a slow 3–4% camera drift toward it as the line arrives would give the ending more pull.
- Around the 6 s seam the card briefly comes to rest (it turns direction there); it reads as a beat, but a faster handoff is possible.
