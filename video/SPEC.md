# SPEC — Sakhi, 45 s product film

GOAL:    A stranger understands in 45 s: in Bidar, an ASHA worker *speaks* a check-in with
         Sakhi in Kannada, a danger sign is caught on the spot, and the doctor gets it in one tap.

OUTPUT:  HyperFrames, 1920 x 1080, 30 fps
TIME:    45.0 s x 30 = 1350 frames, exactly. No music (brief); the clock is Sakhi's voice and the taps.

LOOK:    The medium is the product itself: real Tara app screens (captured from the running app,
         assets/app/*.png, 1236 x 2745) inside a phone, on the brand's own grounds.
         Grounds by world: (1) space + real satellite Earth (baked basemap);
         (2-7) app worlds on blush #F7E4E8 with a warm radial lift to #FBEFF2 at 60% (never a
         dark void); (8) end card on porcelain #FFFFFF.
         Ink #2B1B21, muted ink #5A464D. ONE accent: cherry #A11D44, only for the pin, the
         danger card, the one highlighted word per title, and the end card line.
         Plum #5B2A86 never; sage #2E7D5B only for "normal/ok" states the app itself shows.
         Display: Baloo 2 700, titles 76 px (7% of height), tracking -1%; one line, <= 6 words.
         Kannada: Baloo Tamma 2 600/700. Small lines: Baloo 2 500, 34 px, tracking 0.
         Phone: 520 x 1130 px screen area (scale 0.421 of the capture), radius 64 px, bezel
         14 px #1B1316, contact shadow 0 40px 80px rgba(43,27,33,0.28). No 3D tilt, ever.
         Isolated UI pieces (question card, answer bubble, danger card) are rebuilt with the
         app's real labels at 1.8x real size, never invented copy.

OBJECTS: Earth (real satellite imagery, India tinted cherry 35%); a map pin (cherry teardrop
         with white core, 64 px, dropping 90 px and settling on a spring 0.75); the phone;
         Sakhi's orb (the app's own concentric orb, from the capture); an audio waveform for
         Sunita's spoken answer (32 bars, ink #2B1B21, heights from the line's own rhythm);
         the danger card (the app's alert: cherry 2 px border, #F7E4E8 fill, warning glyph).

MOTION:  enter expo.out (0.6-0.9 s); camera / phone moves power2.inOut (0.6 s); exits
         power3.in, 40% shorter than entrances; springs damping 0.75 (pin, phone landing).
         Taps: a finger-sized touch dot (56 px, ink at 18% with a 2 px white ring) that presses
         (scale 1 -> 0.86 -> 1 in 0.18 s) on the frame the next screen starts.
         Titles: per-word blur 6 px -> 0 + opacity, 0.067 s apart, no translate; dead still after.
         Staggers 3 frames; overlap 30-50%.

SHOTS:   (frames at 30 fps)
  01  0-240   GLOBE -> BIDAR. Earth turns from Africa to India (cherry), dives to Bidar.
              Reads: "India" (2.4-4.0 s), "Karnataka" (4.4-5.8 s), "Bidar district" (6.0-8.0 s);
              the pin drops at 6.8 s. Sound: soft whoosh on the dive; pin "pop" at 6.8 s.
              Hand-off: the pin's cherry head becomes the Tara leaf.
  02  240-345 THE NAME. The leaf (from the pin) lands; t-a-r-a letters and the figure build in
              (layers, 0.1 s apart). Line under it: "A voice companion for ASHA workers" (9.2-11.3 s).
              Sound: one soft riser into the name. Hand-off: the logo shrinks into the phone's
              status area as the phone rises in from the bottom (carry across).
  03  345-480 THE ASHA. Phone: Sunita's real home screen, then tap Lakshmi D. -> her page,
              tap "Talk with Sakhi" (the cherry card). Title: "Sunita visits Lakshmi, 7 months"
              (12.0-15.6 s). Sound: tap ticks on each touch.
  04  480-750 SAKHI ASKS, SUNITA SPEAKS. Sakhi's check-in screen; the question card rebuilt at
              1.8x beside the phone: "ತಾಯಿಯ ಇಂದಿನ ಬಿಪಿ ಎಷ್ಟು ದಾಖಲಾಗಿದೆ?" with English
              "What is her BP today?". Sakhi's real voice (sakhi-q06.wav, 2.24 s) at 16.4 s; the
              orb pulses with it. Then the waveform: Sunita answers; her words type into the
              answer bubble "ಬಿ ಪಿ 148 ಬೈ 96" and resolve to "148/96 mmHg" (21.5 s).
              Title: "No typing. She just speaks" (22.4-25.0 s).
  05  750-960 THE CATCH (payoff, 60-70% in). The danger card slides into the phone and grows to
              1.8x: "ವೈದ್ಯರಿಗಾಗಿ ಗುರುತಿಸಲಾಗಿದೆ · Severe pre-eclampsia risk", rule PE-04.
              Sakhi says the real danger line (sakhi-danger.wav, 3.04 s) at 25.6 s.
              Title: "Danger sign, caught on the spot" (accent on "caught"), 27.6-31.6 s.
              Sound: one low pulse as the card lands.
  06  960-1170 TO THE DOCTOR. The card shrinks and flies (carry across) into the doctor's phone,
              landing as "Lakshmi D. · URGENT · Severe pre-eclampsia risk" at the top of
              "Needs you". Tap -> case -> "Confirm referral" pressed. Title: "First in the doctor's
              queue. One tap" (33.0-38.6 s). Sound: tap ticks, a soft confirm on the press.
  07  1170-1245 BACK TO HER. The mother's phone: the doctor's advice card ("Come to the PHC today
              ...") on Lakshmi's page. Title: "Advice reaches her phone" (39.2-41.4 s).
  08  1245-1350 END CARD. Phone lifts away; the Tara logo builds centred (25% of width);
              "Every danger sign, heard in time." lands at 42.6 s and holds to 45.0 s (2.4 s).
              Small line: "Open source · Kannada & Hindi · built for Bidar's ASHAs".

SUBTITLES: Sakhi's Kannada lines and Sunita's answer get burned-in English subtitles at the
         bottom title-safe line (44 px Baloo 2 600, ink on a 70% white scrim sized to the text).

BANNED:  a dark void with floating UI; glow, bloom, neon, particles; purple/blue/mesh gradients;
         glass cards; cream-paper serif + mono-caps labels; everything centred; everything fading
         and sliding up; titles that move after landing; corner labels / HUD readouts; three cards
         in a row; a title over a dimmed screenshot; the logo docked in a corner; a tilted 3D
         phone; invented UI or copy not in the app; a synthetic voice for Sunita; music.

QA:      2.0 s globe with India visible and tinted; 6.9 s pin on Bidar, label legible;
         10.0 s logo complete; 13.5 s Lakshmi page, tap visible; 17.5 s question card + orb +
         subtitle; 21.8 s "148/96 mmHg" in the bubble; 27.5 s danger card at full size, nothing
         covering it; 34.5 s doctor's queue with Lakshmi URGENT on top; 37.5 s Confirm referral
         pressed; 40.5 s advice card legible; 44.5 s end card with the line held, logo centred.

Data note: all patient data on screen is the app's fictional demo data (Lakshmi D., Sunita).
Imagery credit on the map: "Esri, Maxar, Earthstar Geographics" (small, 18 px, on a soft backing).
