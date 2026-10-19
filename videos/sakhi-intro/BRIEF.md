---
workflow: general-video
flow: automation
storyboard: no
message: "Sakhi is a warm, careful voice co-pilot that helps ASHA workers screen mothers for pregnancy risk over the phone - she listens, records real clinical data, and escalates calmly when something looks serious, but she never replaces a doctor."
destination: showcase
aspect: 1920x1080
language: en
audience: "people being introduced to or evaluating the product (a funder, a health-dept partner, a new ASHA worker) - warm and human, not feature-listy or technical"
length: 60s
angle: concept
---

## Intent

An introduction video for Sakhi, a maternal-health voice AI co-pilot for ASHA
workers in rural Bidar, Karnataka, India. Calm, warm, trustworthy healthcare
tone - no fast cuts, no upbeat corporate-explainer energy. This is a real
product demo, not a mockup.

## Assets

- C:\Users\TILINB~1\AppData\Local\Temp\claude\C--Users-Tilin-Bijoy-Desktop-mystreevoiceagent-mystreevoiceagentiog\ac75ba51-2659-448d-a04e-fadb48940bfb\scratchpad\sakhi_intro.wav — real production narration, synthesized via the app's actual Gnani TTS voice (~46s, mono 22050Hz). This is the audio track; its real duration drives the video's total length.
- frontend/public/sakhi/namaskara.mp4 — her real on-screen avatar, greeting gesture (one-shot, not a loop). Opening shot.
- frontend/public/sakhi/talk_loop_1.mp4 — her real on-screen avatar, speaking loop 1 (seamless loop, 3.8s).
- frontend/public/sakhi/talk_loop_2.mp4 — her real on-screen avatar, speaking loop 2 (seamless loop, 3.7s).
- frontend/public/sakhi/idle_loop.mp4 — her real on-screen avatar, resting/listening loop (seamless loop, 3.9s).
- All four clips are 640x640 square, h264, 24fps. They are the only footage of her that exists - reuse/loop them across the full runtime, matching how the live product itself works.

## Customizations

- Light on-screen captions/title cards at a few key moments only (not full burned-in subtitles for every word): "For ASHA workers in rural Karnataka", "Kannada · Hindi · English", "Never guesses. Always escalates." (this last one gets extra visual weight - it's the trust-building claim).
- Closing title card: clean text lockup "Sakhi — a voice companion for ASHA workers." No logo file exists; do not invent one.
- Visual beat structure (loosely synced to the narration transcript, real audio timing overrides any estimate):
  1. namaskara.mp4 under the greeting ("Namaskara. I am Sakhi...one home visit at a time.")
  2. talk_loop (alternating 1/2) under "When a health worker calls me...gently guide the conversation" and the intake-details beat ("her name, her age...the symptoms that matter")
  3. talk_loop continues under the safety/escalation beat ("I never rush her...before it becomes an emergency") - the most important trust line, reinforced with a title-card overlay, not just talking-head
  4. talk_loop or idle_loop under "not a replacement for a doctor...hardest job in the room"
  5. idle_loop (calmer) under "My name means friend...one call at a time", into the closing title card

## Notes

- Palette: primary teal #0f766e, warm off-white background #fdfaf6, soft mint accents #ccfbf1 / #dcfce7 - matches the real app's own design tokens, not an invented brand.
- Do not regenerate the narration audio or the avatar clips - both are real production assets, used as-is.
