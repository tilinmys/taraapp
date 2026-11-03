# tara UI patterns

The rules every screen follows. They are encoded in shared components, so a new
screen gets them by using the components instead of re-styling.

## 1. Screen frame: `components/system/screen.tsx`

```
┌──────────────────────────────────────────────┐  ← top safe area (notch / Dynamic Island)
│ ‹ Mothers          Question 7 of 24       ⓤ  │  nav bar, 52 pt
├──────────────────────────────────────────────┤  hairline appears on scroll
│ Eyebrow · place                               │
│ Large title, wraps, never truncated           │  ← morphs into the nav bar on scroll
│ Optional subtitle                             │
│ [accessory: progress, filters]                │
│ … content column (520 pt / 720 pt tablets) …  │
├──────────────────────────────────────────────┤
│ [dock: live status]                           │
│ Context line: who / what the button acts on   │
│ [■■■■■■■■  Start check-in  ■■■■■■■■]  56 pt   │
│ optional quiet text link                      │
└──────────────────────────────────────────────┘  ← bottom safe area (home indicator)
```

- **Back is always labelled** with where it goes: "‹ Mothers", "‹ Queue", "‹ Back".
  Never a bare arrow. Ending something is "✕ Exit" plus a confirmation sheet.
- **Right action**: one icon only (Profile), 44 pt target.
- **Root screens** (Mothers, Case queue, Mother home) have no back. Leaving the
  workspace is Profile → Sign out (confirmed).
- **Safe areas**: top, bottom *and* left / right (landscape notches) are
  applied by the frame and the CTA bar. Never pad for the notch by hand.

## 2. Buttons

| Kind | Look | Use |
|---|---|---|
| Primary | cherry fill, white text, **56 pt**, radius 16 | exactly one per screen, in the sticky bar |
| Secondary | white, outline, 56 pt | a real alternative (e.g. "I have an account") |
| Link | cherry text, 44 pt | low-emphasis ("Undo this answer", "Stay here") |
| Chip / pill | 44 to 52 pt, shell / cherry-mist | filters, presets, choices |

**Copy rules (most important):**
- Label = verb + object, **one line, at most ~20 characters**:
  "Start check-in", "Confirm referral", "Tap to answer", "Finish".
- Specifics never go in the label. They go on the **context line** above it:
  "Lakshmi D. · 7 months · High risk".
- Long labels are a bug. The label shrinks a little (to 85 %) rather than
  wrap, but copy should never need it.
- State changes morph in place (mic → stop → check) instead of swapping.

## 3. Grid and spacing

- 24 pt side gutter (more in landscape to clear the notch).
- Content column max 520 pt on phones, 720 pt on tablets, always centred.
- Vertical rhythm: 20 pt between sections, 12 pt between cards, 8 pt
  between a label and its control.
- One direction per section (vertical stacks; filters wrap).
- **No card inside a card**: group inside a card with 1 pt white dividers.

## 4. Colour

- Canvas: premium white `#FFFFFF`. Cards / groups: shell `#FBF4F1`.
- Cherry `#A11D44`: primary buttons, links, back buttons, icons, active
  borders, progress. Cherry mist `#F7E4E8`: selected and flagged fills
  (always with a 2 pt cherry border when flagged).
- Rose / blush: decorative only. Never text.
- Pumpkin `#C84B00`: the second warm accent (duotone icon fills, Sakhi's
  orb, the ASHA's topics). Plum `#5B2A86` and sage `#2F6B4F`: reproductive
  health and "all good" accents. Icons and tiles only.
- Bento tints, one hue per cell: orange `#FFF5F0`, rose `#FDF0F5`, lilac
  `#F4EEFA`, sage `#EEF6F1`; each cell has a hairline in its own hue.
- Workspace header band `#FAF1EC` closed by a `#E5D8D0` hairline.
- Danger is never colour-only: ⚠️ + bold words.

## 5. Type: `<AppText variant>`

title 26/34 · heading 19/26 · body 17/25 · label 15 · caption 14/20 ·
button 18 · overline 12. Never truncate titles or body copy; truncate only
compact nav titles and single-line metadata.

## 6. Icons: `components/system/icon.tsx`

One family (Phosphor). `regular` weight when inactive, `fill` when active,
`duotone` (`tone` prop: inner fill at 30 %, usually pumpkin) for feature
tiles and the active tab. Add new glyphs to the `ICONS` map; never import
icons ad hoc. **No star or heart shapes anywhere** (no Sparkle, no
HandHeart, no Heartbeat).

Emblems (`components/system/emblem.tsx`) are the app's own illustrated
tiles: tinted squircle + petal arc + orbit dot + duotone glyph, drawn in
code. Tones: pumpkin (ASHA), cherry (mother / maternity), plum (doctor /
reproductive health), sage (fertility).

## 7. Image slots: `constants/art-slots.ts`

Places designed for future artwork render an Emblem of the final size. Drop a PNG in `assets/images/art/` and point the slot at it;
no screen code changes.

## 7b. Bento grids: `components/report/bento.tsx`

`BentoRow` (equal-share cells), `BentoStack` (cells sized by content, used
beside a tall cell), `BentoCell` (tone, icon, label, value, note). Most
important cell first and largest. Short numbers use the metric size; words
wrap as a heading; `compact` for 3-across rows. Labels drop under the icon
on narrow phones instead of breaking words. Tablets: vitals go 4-across.

Edge states: `StateView` (leaf mascot or emblem + one next action) for
empty lists, not-found (`+not-found.tsx`) and the root `ErrorBoundary`;
`OfflineBanner` ("saved on this phone, will sync") from `expo-network`.

## 8. Motion (all on the UI thread, all honour Reduce Motion)

| # | Pattern | Where |
|---|---|---|
| 1 | Parallax page slide (35 %, 0.96, spring 20/180) | onboarding steps; `ios_from_right` pushes |
| 2 | Sheet + background scale 0.96 | case row details, exit / sign-out confirmations |
| 3 | Press physics 0.97 spring + calibrated haptics | every button and card |
| 4 | Morphing button icon | sticky CTA (mic → stop → check, send → check) |
| 5 | Dynamic-Island status pill | check-in summary processing |
| 6 | Drag-to-reveal unboxing | mother's new result |
| 7 | Long-press focus overlay (dim scrim, no blur) | doctor case queue |
| 8 | Breathing idle 1.0 ↔ 1.12 / 1600 ms | voice disc when idle |
| 9 | Large title → nav bar morph | every Screen |
| 10 | Pulse rings 1.0 → 1.25, 0.4 → 0, + 3-bar equaliser | check-in prep mic test |
| 11 | Sakhi rangoli orb: 12 kolam dots + 3 rings (1.0 → 1.35, 0.8 → 0.2); dots wave while listening, turn while speaking | ASHA conversation |
| 12 | Tara leaf halo (breathes while speaking, ripples while listening) | mother's Talk tab |
| 13 | Gliding jump-bar pill + sticky section bar, spring 250 / 25 | doctor case record |
| 14 | Tab capsule spring 250 / 25, duotone active icon | mother's bottom bar |
| 15 | In-place expand (layout spring) | transcript, topic guides, 24 answers |

## 9. Haptics: `lib/haptics.ts`

light: typing milestones, toggles, rows · medium: choosing a mother / role,
answering · heavy: primary buttons, long-press · success / warning: completed
milestones and danger alerts.

## 10. Flows

```
Welcome → Language → Role → Sign in (test mode: anything works) →
  ASHA:   Mothers → Before you start → Talk with Sakhi (tap to start) →
          Report (vitals, rules, live transcript, PDF) → Finish → Mothers
  Doctor: Case queue → Case record (Triage · Vitals · Voice report · Answers;
          same PDF) → Confirm referral → Queue
  Mother: tabs Home · Pregnancy · Talk (Tara) · History (filter: History / Prescriptions)
          Home → Maternity / Fertility / Reproductive health guides
  Sakhi = the ASHA's check-in voice. Tara = the mother's companion. Never mixed.
  Every role: Profile (language, demo controls, sign out)
```
