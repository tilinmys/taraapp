# tara — app (UI/UX)

The tara mobile app's screens and design, with no voice dependencies, so it runs
in **Expo Go** (Expo SDK 57). The voice agent (LiveKit/WebRTC, scaffolded in
`../mobile`) will be added later as a module.

## Run

From this folder (`app`), in PowerShell, one command per line:

```
npm install
npm run tunnel     # RECOMMENDED: works from any network, incl. mobile data
npm start          # Wi-Fi only (phone and PC on the same *Private* network)
npm run web        # in the browser
```

`npm run tunnel` opens a Cloudflare quick tunnel (no account) and starts Expo
through it. It waits until the tunnel address is live (about 1 minute) before
printing the QR, so the first scan always works. The address changes on every
restart: rescan the new QR. First load on the phone takes ~15-20 s; after that
edits hot-reload.

Why not plain Wi-Fi: the clinic network is a Windows *Public* network, which
blocks phones from reaching the PC. Why not `expo start --tunnel`: Expo's
built-in ngrok tunnel fails on SDK 57 ("remote gone away").

Before saying a screen is done: `npm run typecheck` and `npm run lint`.

## Layout

- `src/app/` — screens (Expo Router: one file per route)
  - `index.tsx` — brand intro, then the welcome screen
  - `welcome.tsx` — welcome screen without the intro
  - `create-account.tsx` — registration
- `src/components/` — `brand-intro`, `welcome-screen`, `welcome-hero`, `ambient-glow`, `ui/*`
- `src/constants/brand.ts` — colours, logo geometry, tagline
- `src/constants/motion.ts` — every duration, curve and spring
- `src/constants/typography.ts` — fonts and the type scale
- `assets/images/tara-logo.png` (+ `logo-layers/` for the intro build)

## Typography

Always use `<AppText variant="…">` (`src/components/ui/app-text.tsx`), never a
raw `<Text>` with font styles. It picks the right font for each script
automatically, so "ಕನ್ನಡ · हिंदी · English" renders each part in its own cut.

Two families, assigned by how much attention the text gets:

| Focus | Use for | Variant | Font |
|---|---|---|---|
| 1 Act | buttons | `button` 18 / `buttonSmall` 16 | Anek SemiBold 600 |
| 1 Act | what the user types | `input` 18 | Anek Medium 500 |
| 1 Act | critical numbers (BP, weeks, risk) | `metric` 28 | Anek Bold 700 |
| 2 Orient | hero / screen titles | `display` 32, `title` 26 | Anek Bold 700 |
| 2 Orient | section headings | `heading` 20 | Anek SemiBold 600 |
| 3 Read | body, questions (17 / 24) | `body` 17 (`bodyStrong` 700 for alerts) | Anek Regular 400 |
| 3 Read | field labels, errors | `label` 15 | Anek SemiBold 600 |
| 3 Read | line under a title | `subtitle` 15 | Anek Regular 400 |
| 4 Glance | hints, notes, dividers | `caption` 14 | Anek Regular 400 |
| 4 Glance | small uppercase labels | `overline` 12 | Anek SemiBold 600 |
| 5 Feel | decorative marks only, never readable text | `tagline` 30 | Parisienne 400 |

Rules (see `src/constants/theme.ts`):
- The script face (Parisienne) and `Brand.rose` are decorative only: never
  for readable text, including taglines.
- Body text is 17 / 24 in `Brand.ink`; supporting text in `Brand.inkMuted`.
- One 56 pt cherry primary button per screen, in the sticky bottom bar.
- No card inside a card; group with whitespace or 1 px porcelain hairlines.
- Icons: lucide only, 1.5 px stroke inactive, 2.25 px active.
- Haptics through `lib/haptics.ts`: light (typing, toggles, table rows),
  medium (choosing a role or a mother), heavy / success / warning (commit).

## Rules for this folder

- Only libraries that ship inside Expo Go. Anything needing native code
  (LiveKit, WebRTC, MMKV, …) belongs in the voice module, not here.
- Add packages with `npx expo install <pkg>` so versions match SDK 57.
