# tara personas and what they changed

Fourteen people who will realistically pick up tara, what gets in their way,
and the design decision each one drove. Use them when reviewing any new
screen: "would Savitri manage this?"

## ASHA workers (primary users)

| # | Persona | Context | Risk if we get it wrong | Decision in the app |
|---|---|---|---|---|
| 1 | **Sunita, 38**, experienced ASHA, Kannada, budget Android | Visits 6 to 8 mothers a day, often outdoors in bright sun | Misses a danger sign; stops trusting the app | Danger = ⚠️ + bold words, never colour alone; high-risk mothers pinned first; white canvas for sunlight contrast |
| 2 | **Savitri, 52**, low confidence with phones, reads slowly | First smartphone, large font setting | Gets lost; taps the wrong thing | One primary button per screen; plain instruction "Tap a mother, then press the red button"; text scales to 150 % without breaking |
| 3 | **Asha, 29**, quick, impatient, Hindi | Wants to finish fast | Abandons long flows | Smart defaults (pre-ticked checklist, pre-selected mother); 3 short steps; progress always visible |
| 4 | **Renuka, 45**, shared family phone | Child uses the phone too | Data in the wrong hands; accidental exits | Exit and sign-out always confirmed; check-in can't be left by a stray swipe |
| 5 | **Gowri, 34**, poor network in villages | Calls drop | Loses a half-finished check-in | Every answer auto-saves the moment it is captured; exiting keeps answers |

## Mothers

| # | Persona | Context | Risk | Decision |
|---|---|---|---|---|
| 6 | **Lakshmi, 26**, second pregnancy, high BP | Anxious about results | Panic, or ignoring a real warning | Result is warm, plain and specific ("go to the clinic today; Sunita knows"); never alarmist; pull-to-open makes it a calm, deliberate moment |
| 7 | **Kavitha, 24**, first pregnancy, healthy | Wants reassurance | Feels the app is only for bad news | Normal result says "All good this week" with next steps; history shows past good check-ins |
| 8 | **Meena, 23**, cannot read well | Relies on listening | Can't use text-heavy screens | "Listen to my results" reads the result and advice aloud; Tara answers by voice; emblems carry meaning before words |
| 9 | **Roopa, 21**, lives with in-laws, little privacy | Others may see the phone | Embarrassment | Fertility and reproductive health are read-only guides: nothing is tracked, saved or shared (said on the page); no period or fertility log |

## Doctors

| # | Persona | Context | Risk | Decision |
|---|---|---|---|---|
| 10 | **Dr. Anand, 41**, PHC medical officer | 40+ patients a day, minutes per case | Misses the one urgent case | Queue sorted by risk; "Review next case" goes straight to the next unreviewed high-risk mother |
| 11 | **Dr. Fathima, 33**, wants to know *why* | Distrusts black-box AI | Overrides or ignores escalations | Deterministic rule audit (RULE #PE-04 = PE-01 AND PE-03) with the exact answers that fired it |
| 12 | **Dr. Rao, 58**, tablet user, reading glasses | Holds the tablet at arm's length | Small text, wasted space | 720 pt column on tablets, 17 pt body, patient context stays in the nav bar while scrolling |

## Supervisors and edge cases

| # | Persona | Context | Risk | Decision |
|---|---|---|---|---|
| 13 | **Prakash, 36**, block health supervisor demoing to officials | Needs every state on demand | Demo stalls on missing data | Demo controls in Profile (high-risk / normal mother, reset demo data); test-mode sign-in; testing tools in the check-in |
| 14 | **A screen-reader user** (low vision) | TalkBack / VoiceOver | Unlabelled icons, hidden actions | Every control has a role and label; back buttons are labelled; speaker buttons are reachable siblings, not nested |

## Principles distilled

1. **One screen, one job, one red button.**
2. **Say what will happen** (context line above the button).
3. **Never lose work, never trap the user** (auto-save, labelled back, confirmed exits).
4. **Warnings are words, not colours.**
5. **Voice first for those who need it, text for those who prefer it.**
