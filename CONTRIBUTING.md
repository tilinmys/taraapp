# Contributing to Sakhi (Tara)

Sakhi is a voice check-in that helps ASHA workers screen pregnant women for
danger signs, in Kannada and Hindi. It handles maternal health data, so a few
rules here are stricter than in most projects. Please read them before you
open a pull request.

By contributing you agree that your contribution is licensed under the
Apache License 2.0 (see [LICENSE](LICENSE)).

## Reporting a security vulnerability

**Do not open a public issue for a security problem.** Report it privately
through the repository's **Security** tab, using **Report a vulnerability**
(GitHub private vulnerability reporting). Include:

- what an attacker could do, and which data or component is affected;
- steps to reproduce, or a proof of concept;
- the commit or release you tested.

What happens next:

- We acknowledge the report within **3 working days**.
- We give a first assessment and a fix plan within **10 working days**.
- We agree a disclosure date with you once a fix is released. We credit
  reporters who want to be credited.

Treat anything that could expose a mother's health record, an ASHA's
identity, a call recording, or a provider credential as **critical**. If you
come across real personal data while testing, stop, do not keep a copy, and
tell us in the report.

## Reporting a bug

For anything that is not a security problem, open a GitHub issue. Include:

- what you did, what you expected, and what happened instead;
- where: the app (Android, iOS or web, and the app version), the phone
  line, the dashboard, or the gateway;
- the language of the check-in (Kannada, Hindi or English);
- logs or screenshots, **with every name, phone number, village and health
  reading removed**. Sakhi's logs record lengths, not words; if you see a
  mother's words in a log, that is itself a bug, and a privacy one.

A bug that could make Sakhi miss or mis-report a danger sign (a wrong
reading, a skipped question, a flag that does not reach the doctor) is
**clinical-safety critical**: put `clinical-safety` in the title so it is
triaged first.

## Rules that are not negotiable

1. **Clinical triage logic** (`sakhi_triage.py`, and the danger-sign rules
   the app shares with it) is clinically reviewed. Do not change how vitals
   or answers are evaluated without a clinical sign-off recorded in the pull
   request. Refactors must leave every evaluation result identical, and
   the existing tests must prove it.
2. **Secrets never enter the repository or the app.** Provider keys
   (LiveKit, Gnani, Sarvam, OpenRouter, Exotel and others) live in the
   local `.env` (gitignored) and, in production, in AWS Secrets Manager.
   Never put a key in source, in a test fixture, in a log line, or in any
   `EXPO_PUBLIC_*` variable: everything `EXPO_PUBLIC_*` is shipped inside
   the app to every phone.
3. **Database changes are additive.** Migrations use `IF NOT EXISTS` and
   never drop, rename or narrow a column that holds data. The schema in
   `sakhi_schema.py` must keep working for existing data.
4. **No real personal data in tests, fixtures, screenshots or issues.**
   Use the demo mothers in `app/src/data` or clearly synthetic records.
5. **The live call lifecycle** in `agent.py` (LiveKit room events) is load
   bearing for the phone line. Change it only with a test that covers the
   new behaviour.
6. **Development agents never use production names.** Local voice agents
   register as `sakhi-app-dev` (see `scripts/sakhi_agent_dev.py`), so a
   laptop can never take a call meant for production.

## Git workflow

- `master` is the release branch. Do not commit to it directly.
- Branch from `master` with a short, descriptive name, for example
  `sakhi/silence-handling` or `fix/gateway-timeout`.
- Keep each pull request to one change. Write the commit message as what the
  change does and why (`Hold the check-in after two silent questions`).
- Rebase on `master` (or merge it in) before asking for review, and make
  sure all checks below pass.
- Every pull request needs one approving review. A change to anything listed
  under "Rules that are not negotiable" needs a second reviewer who owns
  that area.
- Do not force-push a branch someone else is reviewing.

## Local setup and tests

You need Python 3.10+, Node.js 20+ and npm.

**Backend (gateway, voice agents, triage):**

```bash
pip install -r requirements.txt
cp .env.example .env    # then fill in your own development keys
python -m pytest tests/ -q
```

**App (Expo, in `app/`):**

```bash
cd app
npm install
npx jest
npx tsc --noEmit
npx expo lint
```

**Running Sakhi end to end on your machine** (three terminals, repo root
unless noted):

```bash
python scripts/sakhi_gateway_dev.py     # speech gateway on localhost:8787
python scripts/sakhi_agent_dev.py       # live voice agent (sakhi-app-dev)
cd app && npm run tunnel                # the app in Expo Go, gateway on the same tunnel
```

With the gateway and agent running, `python scripts/test_live_agent.py`
checks the live voice path end to end (speaking, listening, silence, a pause
inside an answer, and Next).

Every pull request must pass the full Python suite and the app's jest,
typecheck and lint. Add or update tests for any behaviour you change. A
voice or triage change without a test will not be merged.
