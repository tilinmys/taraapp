"""Build the Tara x UNICEF Femtech Ventures pitch-video script PDF.

    python docs/pitch/build_pitch_script.py

Writes docs/pitch/Tara_UNICEF_Femtech_Pitch_Script.html and prints it to
PDF with headless Microsoft Edge (Chromium shapes Kannada / Hindi script
correctly). Every spoken line lives in SCRIPT below; word counts per act
are computed, never typed, so the 260-280 word budget is always true.
"""

from __future__ import annotations

import base64
import html
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
OUT_HTML = HERE / "Tara_UNICEF_Femtech_Pitch_Script.html"
OUT_PDF = HERE / "Tara_UNICEF_Femtech_Pitch_Script.pdf"
EDGE = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
LOGO = ROOT / "app" / "assets" / "images" / "tara-logo.png"

# Act budgets from the playbook (words) and their time windows.
ACTS = [
    ("1", "Problem", "0:00 – 0:25", (55, 60), "Emotional hook and a mother-level risk, stated plainly."),
    ("2", "Solution", "0:25 – 0:45", (45, 50), "What Tara is, in one breath, and who it serves."),
    ("3", "Live demo", "0:45 – 1:30", (90, 100), "One danger sign, followed across three phones."),
    ("4", "Scale & ask", "1:30 – 2:00", (50, 55), "Open source, low bandwidth, safety by design, the ask."),
]

# (act, time, marker, visual cues, speaker, spoken words, app audio / on-screen text)
SCRIPT = [
    (
        "1",
        "0:00 – 0:07",
        "COLD OPEN",
        "Black frame. A small white timer appears top-right at 00:00 and starts running (it runs for the whole film). "
        "Fade up: dawn, a village lane in Bidar. An ASHA worker walks with a cloth bag and a paper register.",
        "Founder (VO)",
        "Watch the clock in the corner. Before this film ends, one danger sign will travel from a village to a doctor.",
        "Lower third: “Bidar district, Karnataka, India”",
    ),
    (
        "1",
        "0:07 – 0:25",
        "PROBLEM",
        "Close-up: a handwritten register line “BP 148/96 — headache, blurred vision”. "
        "Text builds over it: HIGH BP + HEADACHE + BLURRED VISION = SEVERE PRE-ECLAMPSIA RISK. The register closes.",
        "Founder (VO)",
        "Sunita is an ASHA worker. Her mothers live across three villages; their records live on paper. "
        "Lakshmi's blood pressure reads one forty-eight over ninety-six, with headache and blurred vision: severe pre-eclampsia. "
        "That warning can wait days for a doctor.",
        "On-screen: “The warning exists. It just doesn’t travel.”",
    ),
    (
        "2",
        "0:25 – 0:35",
        "SOLUTION",
        "Logo resolve: Tara leaf grows into the wordmark. Match-cut to Sunita’s phone: the ASHA home screen (villages, “Today’s visits”).",
        "Founder (on camera)",
        "This is Tara: an open-source, voice-first maternal care network, built for the frontline health workers who already reach every mother.",
        "Super: “Tara — open-source maternal care network”",
    ),
    (
        "2",
        "0:35 – 0:45",
        "SOLUTION",
        "Sakhi’s orb pulses on screen. Three badges pop in, one per beat: KANNADA · HINDI | NO TYPING | BASIC ANDROID PHONE.",
        "Founder (on camera)",
        "Its AI companion, Sakhi, talks with the ASHA in Kannada or Hindi, with no typing, and turns a spoken check-in into a screened clinical report the doctor can trust.",
        "Badges as above",
    ),
    (
        "3",
        "0:45 – 0:58",
        "[DEMO START]",
        "Screen recording, ASHA phone: [TAP “Sakhi Direct”] → [TAP “Lakshmi D.”] → [TAP “Start questions”]. "
        "Sakhi speaks; subtitle shows her Kannada line with English underneath. Sunita answers aloud. Duck the VO under Sakhi’s line (~2 s).",
        "Founder (VO) + Sakhi (app audio)",
        "Sakhi asks one question at a time, in a warm, familiar voice, and Sunita simply answers in her own words.",
        "Sakhi: “ತಾಯಿಯ ಇಂದಿನ ಬಿಪಿ ಎಷ್ಟು ದಾಖಲಾಗಿದೆ?” (What is her BP today?) · Sunita: “ಬಿ ಪಿ 148 ಬೈ 96”",
    ),
    (
        "3",
        "0:58 – 1:10",
        "[DEMO FEATURE]",
        "The answer bubble fills: “ಬಿ ಪಿ 148 ಬೈ 96” → “148/96 mmHg”. A red danger card slides in. "
        "[ZOOM INTO DANGER CARD] → [TAP “Why this risk”] shows the rule audit: PE-01 AND PE-03 → PE-04.",
        "Founder (VO)",
        "Her spoken words become text in about a second. Transparent clinical rules, not a black box, flag severe pre-eclampsia on the spot, and every rule can be audited.",
        "Super: “Speech → text ≈ 1 s · rule PE-04 fired”",
    ),
    (
        "3",
        "1:10 – 1:20",
        "[DEMO ACTION]",
        "Cut to the doctor’s phone, home “Today”: Lakshmi D. at the top of “Needs you”, tagged URGENT. "
        "[TAP CASE] → summary banner + vitals (BP HIGH) → [TAP “Confirm referral”]. Haptic flash on the button.",
        "Founder (VO)",
        "When the check-in ends, Lakshmi is already first in the doctor's queue, with her vitals and the whole conversation. One tap confirms the referral.",
        "Super: “Doctor: 1 tap”",
    ),
    (
        "3",
        "1:20 – 1:30",
        "[DEMO ACTION]",
        "Cut to Lakshmi’s phone: the doctor’s advice arrives in Kannada. Then [PRESS SOS]: the screen turns red, ripples, "
        "“Reporting ASHA workers nearby”, a radar sweep, then “Sunita · 1.2 km · On the way”. The corner timer FREEZES.",
        "Founder (VO)",
        "Clear, simple advice reaches Lakshmi in her own language. And if she is ever in danger at home, one SOS alerts the nearest ASHAs, her own ASHA first.",
        "Timer freezes · Super: “Village → doctor → mother, in this demo: 01:30”",
    ),
    (
        "4",
        "1:30 – 1:45",
        "SCALE",
        "Motion graphic, three columns building left to right: OPEN SOURCE (repository page) | LOW BANDWIDTH · WORKS OFFLINE | SAFETY BY DESIGN (consent, minimal data, auditable rules).",
        "Founder (on camera)",
        "Tara is fully open source. It runs on low bandwidth and keeps working offline, and it is safe by design: consent first, minimal data, and auditable rules.",
        "Three column titles as above",
    ),
    (
        "4",
        "1:45 – 2:00",
        "ASK",
        "Sunita and Lakshmi (consented footage or illustration) on a doorstep, phone between them. End card: Tara logo, "
        "“Every danger sign, heard in time.”, “Applying to UNICEF Femtech Ventures · Pillar 1: Healthcare access for women and girls”.",
        "Founder (on camera)",
        "With UNICEF Femtech Ventures, we will pilot with two hundred ASHA workers in Bidar district, then open Sakhi to every Indian language. Every danger sign, heard in time.",
        "End card text as above (no UNICEF logo unless UNICEF has approved its use)",
    ),
]


def words(text: str) -> int:
    return len(re.findall(r"[\w’'-]+", text))


def act_words(act: str) -> int:
    return sum(words(r[5]) for r in SCRIPT if r[0] == act)


TOTAL = sum(words(r[5]) for r in SCRIPT)


def esc(s: str) -> str:
    return html.escape(s, quote=False)


def logo_data_uri() -> str:
    if not LOGO.exists():
        return ""
    return "data:image/png;base64," + base64.b64encode(LOGO.read_bytes()).decode()


# ---------------------------------------------------------------------------------
# Document sections
# ---------------------------------------------------------------------------------


def section_cover() -> str:
    logo = logo_data_uri()
    img = f'<img class="logo" src="{logo}" alt="Tara">' if logo else '<div class="logo-text">tara</div>'
    return f"""
<section class="cover">
  {img}
  <p class="kicker">UNICEF FEMTECH VENTURES · SECOND CALL · 2026</p>
  <h1>“Watch the clock.”</h1>
  <p class="sub">The 2-minute pitch-video script for <b>Tara</b>: an open-source, voice-first maternal care network
  for frontline health workers, in Kannada and Hindi.</p>
  <div class="cover-grid">
    <div><span>Pillar</span><b>1 · Healthcare access for women and girls</b><em>Supporting: Pillar 2 (AI in underserved languages) and Pillar 3 (safety)</em></div>
    <div><span>Runtime</span><b>2:00</b><em>{TOTAL} spoken words at ~135 words a minute</em></div>
    <div><span>Format</span><b>Act 1–4 · two-column A/V script</b><em>Problem → Solution → Live demo → Scale &amp; ask</em></div>
    <div><span>Deadline</span><b>4 November 2026</b><em>Apply through the call page’s “Apply now” form</em></div>
  </div>
  <p class="note">Built from the UNICEF Venture Fund pitch playbook (4-act, 260–280 words, dual-column, explicit demo markers)
  and the Femtech Ventures call text. Bracketed or highlighted items are for the team to confirm before recording.</p>
</section>"""


def section_contents() -> str:
    items = [
        "The call, decoded: why Pillar 1",
        "The creative idea: a film that is its own proof",
        "What UNICEF’s best demos do, and how Tara does it",
        "The 4-act plan and word budget",
        "The master script (timed, two columns)",
        "Read-through script (presenter copy)",
        "Shot list and screen-recording runbook",
        "On-screen text, subtitles and audio",
        "Production, safeguarding and delivery specs",
        "Fact-check sheet: say only what is true",
        "Submission checklist",
    ]
    lis = "".join(f"<li><span>{i + 1:02d}</span>{esc(t)}</li>" for i, t in enumerate(items))
    return f'<section class="page"><h2>Contents</h2><ol class="toc">{lis}</ol></section>'


def section_call() -> str:
    return """
<section class="page">
  <h2><span>01</span> The call, decoded: why Pillar 1</h2>
  <p class="lead">UNICEF Femtech Ventures backs companies “using frontier technologies to build open-source solutions for
  the health, wellbeing, and sexual and reproductive health and rights of girls and women”: up to <b>US$100,000
  equity-free</b>, 12 months of mentorship and access to UNICEF’s network, including governments.</p>

  <h3>The three pillars, and where Tara stands</h3>
  <table class="grid">
    <tr><th style="width:24%">Pillar</th><th>What the call asks for</th><th style="width:30%">Tara’s fit</th></tr>
    <tr class="hl"><td><b>1 · Healthcare access for women and girls</b><br><span class="tag">PRIMARY</span></td>
      <td>“The continuum of care, from antenatal and postnatal support…” · point-of-care tools with <b>automated
      interpretation</b> for frontline health workers · platforms <b>linking screening, referral and follow-up</b> ·
      continuity of care “from conception through the first 1,000 days”, including <b>remote care in low-bandwidth environments</b> ·
      tools covering community health worker workload.</td>
      <td>Exact match. A 24-question antenatal check-in, interpreted automatically by auditable rules, linked to the doctor’s
      referral and the mother’s follow-up, on a basic phone and a weak network.</td></tr>
    <tr><td><b>2 · Informed, safe and accessible reproductive health</b><br><span class="tag t2">SUPPORTING</span></td>
      <td>“AI-powered accessible platforms… in <b>underserved languages</b>, especially for low-literacy and low-bandwidth
      environments” · CHW platforms for case management, referral and follow-up · consent- and privacy-preserving data.</td>
      <td>Sakhi is a voice AI in Kannada and Hindi: no typing, no English, no reading required.</td></tr>
    <tr><td><b>3 · Mental wellbeing, safety and protection</b><br><span class="tag t2">SUPPORTING</span></td>
      <td>Decision support for CHWs on risk identification and specialist referral · tools for frontline workers.</td>
      <td>Risk flags + referral for the ASHA, and an SOS that reaches the nearest on-duty ASHAs.</td></tr>
  </table>

  <div class="callout">
    <b>Positioning line (use it everywhere: video, form, deck):</b><br>
    Tara is a Pillar 1 point-of-care platform: a voice AI that lets frontline health workers screen pregnant women in their own
    language, and links that screening to referral and follow-up, even on low bandwidth.
  </div>

  <h3>The call’s own words, the script mirrors them</h3>
  <table class="grid compact">
    <tr><th style="width:38%">Call language</th><th>Where it is heard or seen in the film</th></tr>
    <tr><td>frontier technologies · generative and agentic AI</td><td>Act 2: “Its AI companion, Sakhi…”; Act 3 shows the AI conducting the check-in</td></tr>
    <tr><td>open-source licensing and principles</td><td>Act 2 “open-source”; Act 4 “Tara is open source” + repository on screen</td></tr>
    <tr><td>automated interpretation · frontline health workers</td><td>Act 3: speech → text → rules flag severe pre-eclampsia “on the spot”</td></tr>
    <tr><td>screening, referral and follow-up</td><td>Act 3: ASHA screening → doctor’s one-tap referral → advice on the mother’s phone</td></tr>
    <tr><td>underserved languages · low literacy</td><td>Kannada on screen with subtitles; “in her own words”, “in her own language”</td></tr>
    <tr><td>low-bandwidth · low-resource settings</td><td>Act 2 badge “basic Android phone”; Act 4 “runs on low bandwidth and keeps working offline”</td></tr>
    <tr><td>safety-by-design · consent · privacy</td><td>Act 4: “safe by design: consent first, minimal data, and auditable rules”</td></tr>
    <tr><td>hyperlocal · designed within communities</td><td>Bidar named on screen; the ASHA and the mother are the heroes, not the app</td></tr>
  </table>

  <h3>Eligibility, as the call states it</h3>
  <ul class="checks">
    <li class="ok">Working with a functional prototype — Tara runs today (ASHA, doctor and mother apps; voice check-in; SOS).</li>
    <li class="warn">Committed to open-source licensing — <b>add a LICENSE file to the repository</b> (none exists yet) before you submit or show the repo.</li>
    <li class="warn">Registered in a UNICEF programme country — confirm India on the programme-country list linked from the call page, and that the company is registered.</li>
    <li class="ok">Safety-by-design is a condition of investment — the film states it; be ready to show consent screens and data handling in the application.</li>
    <li class="info">Women-led startups and founders under 35 are strongly encouraged — if that is you, say it in the form (and optionally in Act 4’s on-screen text).</li>
  </ul>
</section>"""


def section_idea() -> str:
    return """
<section class="page">
  <h2><span>02</span> The creative idea: a film that is its own proof</h2>
  <p class="lead">Most pitch videos <i>describe</i> speed. This one <i>shows</i> it. A small timer starts at 00:00 in the first
  frame and runs in the corner for the whole film. In that time the audience watches <b>one</b> danger sign, Lakshmi’s blood
  pressure, travel from a village register to a doctor and back to her phone. At 1:30 the timer freezes on the SOS screen.
  The film’s runtime <i>is</i> the argument.</p>

  <div class="cards">
    <div class="card"><b>One mother, not a market</b><p>UNICEF’s strongest demos are child- or facility-specific. Ours is
    mother-specific: Lakshmi D., 7 months, BP 148/96. Every screen in the demo is about her.</p></div>
    <div class="card"><b>Three phones, one story</b><p>ASHA → doctor → mother. Each cut changes phone and colour of the UI,
    so a viewer with the sound off still follows the journey.</p></div>
    <div class="card"><b>The ASHA is the hero</b><p>Sakhi helps; Sunita decides and acts. This answers “who uses it?” in the
    first 25 seconds and fits the call’s “designed within communities”.</p></div>
    <div class="card"><b>Proof over promise</b><p>Every demo beat is a real screen from the working prototype, with real
    Kannada audio and real rule codes. No mock-ups in Act 3.</p></div>
  </div>

  <h3>The emotional line, in four beats</h3>
  <table class="grid compact">
    <tr><th style="width:18%">Beat</th><th>The viewer feels</th><th style="width:36%">Carried by</th></tr>
    <tr><td>Act 1</td><td>Unease: the warning is real and written down, but it stays in a notebook.</td><td>“That warning can wait days for a doctor.”</td></tr>
    <tr><td>Act 2</td><td>Relief: something simple, in her language, already in her hand.</td><td>Logo resolve; three badges</td></tr>
    <tr><td>Act 3</td><td>Momentum, then safety: each tap moves Lakshmi closer to care.</td><td>Fast cuts, timer, SOS radar</td></tr>
    <tr><td>Act 4</td><td>Confidence: it is open, safe and ready to scale with UNICEF.</td><td>“Every danger sign, heard in time.”</td></tr>
  </table>

  <h3>Brand line</h3>
  <p class="big-quote">Every danger sign, heard in time.</p>
  <p class="small">Used once, as the last spoken words, and on the end card. Do not use it earlier: the film earns it.</p>
</section>"""


def section_benchmark() -> str:
    return """
<section class="page">
  <h2><span>03</span> What UNICEF’s best demos do, and how Tara does it</h2>
  <p class="lead">The playbook’s analysis of shortlisted pitches (ClimaSalud, ClimaSense, EAGLE AI, SCP ChildShield) found four
  things every strong demo shows. The script hits all four, on screen, in Act 3.</p>
  <table class="grid">
    <tr><th style="width:26%">Winning signal</th><th>How they showed it</th><th style="width:40%">How Tara shows it (time)</th></tr>
    <tr><td><b>1 · Specificity</b> (one child / one facility)</td><td>A named school on a risk map</td>
      <td>One named mother, one reading: Lakshmi D., BP 148/96 (0:07 onward)</td></tr>
    <tr><td><b>2 · Real-time automated alerts</b></td><td>Alerts in under 30 s</td>
      <td>Rule PE-04 fires as she answers; the case is first in the doctor’s queue (0:58–1:20)</td></tr>
    <tr><td><b>3 · Low-bandwidth / offline access</b></td><td>SMS, WhatsApp, USSD, basic phones</td>
      <td>Voice on a basic Android phone, low bandwidth, keeps working offline (0:35, 1:30)</td></tr>
    <tr><td><b>4 · Open-source sustainability</b></td><td>Public repository, adaptable by governments</td>
      <td>“Tara is open source” with the repository on screen (1:30)</td></tr>
  </table>
  <div class="callout"><b>And what they avoided:</b> tech-stack jargon. The script never says WebSocket, VAD, LiveKit or STT.
  It says “her words become text in about a second” and “one tap confirms the referral”: people doing things.</div>

  <h3>Closest benchmark: EAGLE AI (Kenya)</h3>
  <p>Voice-first AI in local languages, connected to real workflows. Tara goes one step further, and the film must make the
  difference visible: <b>the voice does not end in a chat</b>. It ends in a clinical rule, a doctor’s referral and a mother’s
  phone. That closed loop, screening → referral → follow-up, is literally a Pillar 1 bullet.</p>
</section>"""


def section_plan() -> str:
    rows = ""
    for act, name, window, (lo, hi), purpose in ACTS:
        n = act_words(act)
        ok = lo <= n <= hi
        rows += (
            f"<tr><td><b>Act {act}</b><br>{esc(name)}</td><td>{window}</td><td>{esc(purpose)}</td>"
            f"<td class='num'>{lo}–{hi}</td><td class='num {'good' if ok else 'bad'}'>{n}</td></tr>"
        )
    lo_all, hi_all = 260, 280
    ok_all = lo_all <= TOTAL <= hi_all
    rows += f"<tr class='total'><td colspan='3'><b>Total spoken words</b> (presenter only; Sakhi’s app audio is extra)</td><td class='num'>260–280</td><td class='num {'good' if ok_all else 'bad'}'>{TOTAL}</td></tr>"
    return f"""
<section class="page">
  <h2><span>04</span> The 4-act plan and word budget</h2>
  <p class="lead">Problem → Solution → Live demo → Scale &amp; ask, as the playbook prescribes. Word counts below are
  computed from the script itself.</p>
  <table class="grid">
    <tr><th style="width:15%">Act</th><th style="width:14%">Time</th><th>Purpose</th><th style="width:11%">Budget</th><th style="width:11%">Script</th></tr>
    {rows}
  </table>
  <h3>Pace</h3>
  <p>{TOTAL} words in 120 seconds is about {TOTAL / 2:.0f} words a minute: the playbook’s 130–140 band, with breathing room
  for Sakhi’s two Kannada lines (about 4 seconds) and the frozen timer. Read slightly slower in Act 1 and faster in Act 3.</p>
  <h3>Who speaks when</h3>
  <ul>
    <li><b>Founder (VO)</b>: Acts 1 and 3, over footage and screen recordings.</li>
    <li><b>Founder (on camera)</b>: Acts 2 and 4. Mid-shot, eye level, the phone in hand in Act 2.</li>
    <li><b>Sakhi (app audio)</b>: the real app voice in Act 3, never re-voiced. Duck the VO under it.</li>
    <li><b>Sunita (ASHA)</b>: one real spoken answer, “ಬಿ ಪಿ 148 ಬೈ 96”, recorded with consent.</li>
  </ul>
</section>"""


def section_script() -> str:
    blocks = ""
    for act, name, window, _, _ in ACTS:
        rows = ""
        for a, time, marker, visual, speaker, spoken, extra in SCRIPT:
            if a != act:
                continue
            rows += f"""<tr>
  <td class="time"><b>{time}</b><span class="marker">{esc(marker)}</span></td>
  <td class="visual">{esc(visual)}<div class="extra">{esc(extra)}</div></td>
  <td class="spoken"><span class="who">{esc(speaker)}</span>“{esc(spoken)}”<span class="wc">{words(spoken)} words</span></td>
</tr>"""
        # Each act is one unit: its bar never sits alone at the foot of a page.
        blocks += f"""<div class="act-block">
  <div class="act-h">ACT {act} · {esc(name.upper())} · {window} · {act_words(act)} words</div>
  <table class="script">
    <tr><th style="width:13%">Time</th><th style="width:44%">Visual &amp; demo cues (on screen)</th><th>Spoken script</th></tr>
    {rows}
  </table>
</div>"""
    return f"""
<section class="page">
  <h2><span>05</span> The master script</h2>
  <p class="lead">Left: what is on screen, with explicit demo markers ([TAP], [ZOOM INTO], [PRESS]) for the editor. Right: every
  spoken word. Record the screen first, then read the voice-over against it.</p>
  {blocks}
</section>"""


def section_readthrough() -> str:
    paras = ""
    for act, name, window, _, _ in ACTS:
        lines = " ".join(r[5] for r in SCRIPT if r[0] == act)
        paras += f"<h3>Act {act} · {esc(name)} <small>{window}</small></h3><p class='read'>{esc(lines)}</p>"
    return f"""
<section class="page">
  <h2><span>06</span> Read-through script (presenter copy)</h2>
  <p class="lead">The spoken words only, act by act, for rehearsal and the teleprompter. {TOTAL} words.</p>
  {paras}
  <h3>Delivery notes</h3>
  <ul>
    <li>Say numbers the way you would to a friend: “one forty-eight over ninety-six”.</li>
    <li>Pause one beat after “That warning can wait days for a doctor.” Let the register close in silence.</li>
    <li>“ASHA”: say it as a word (AA-sha), never as letters.</li>
    <li>Smile on “Every danger sign, heard in time.” and hold eye contact for two seconds after it.</li>
  </ul>
</section>"""


def section_runbook() -> str:
    return """
<section class="page">
  <h2><span>07</span> Shot list and screen-recording runbook</h2>
  <p class="lead">Act 3 must be the real app. Record each take on a clean demo build, at 1080 × 1920, with Do Not Disturb on.
  Reset demo data first (Profile → Reset demo data) so Lakshmi’s record matches the script.</p>
  <table class="grid compact">
    <tr><th style="width:9%">Shot</th><th style="width:15%">Phone / role</th><th>Exact steps (taps)</th><th style="width:26%">Must be visible</th></tr>
    <tr><td>S1</td><td>—</td><td>Live action: village lane at dawn; ASHA walking with register. 6–8 s, handheld, natural light.</td><td>No identifiable patient faces without consent</td></tr>
    <tr><td>S2</td><td>—</td><td>Macro: register page with “BP 148/96, headache, blurred vision” (a prop written for the film).</td><td>Handwriting legible for 2 s</td></tr>
    <tr><td>S3</td><td>ASHA</td><td>Open app as ASHA → home screen. Slow scroll to show villages and “Today’s visits”.</td><td>Kannada UI, Sunita’s name</td></tr>
    <tr><td>S4</td><td>ASHA</td><td>Home → <b>Sakhi Direct</b> → <b>Lakshmi D.</b> → wait for the green “connected” line → <b>Start questions</b>.
      Let Sakhi greet; skip ahead (Next) to the BP question; answer aloud “ಬಿ ಪಿ 148 ಬೈ 96”.</td><td>Sakhi orb, Kannada subtitle, the answer bubble</td></tr>
    <tr><td>S5</td><td>ASHA</td><td>Hold on the red danger card; open “Why this risk”.</td><td>PE-01 · PE-03 · PE-04 lines</td></tr>
    <tr><td>S6</td><td>Doctor</td><td>Switch role to Doctor → home “Today” → Lakshmi D. (URGENT) → case page → <b>Confirm referral</b>.</td><td>“Needs you”, BP HIGH tile, the button press</td></tr>
    <tr><td>S7</td><td>Mother</td><td>Switch role to Mother (Lakshmi) → the doctor’s advice notification → open it.</td><td>Kannada advice text</td></tr>
    <tr><td>S8</td><td>Mother</td><td>Tap the red <b>SOS</b> tab on the right edge (or press the side keys up, down, up, down) → let the countdown run →
      “Reporting ASHA workers nearby” → wait for “On the way”.</td><td>Red screen, radar, Sunita · km · On the way</td></tr>
    <tr><td>S9</td><td>—</td><td>Screen capture of the public repository page (README + LICENSE visible).</td><td>“open source” licence name</td></tr>
    <tr><td>S10</td><td>—</td><td>Founder on camera: Act 2 and Act 4, two takes each, one with phone in hand.</td><td>Eye level, quiet room</td></tr>
  </table>
  <div class="callout"><b>Editing rule:</b> cut on the tap. Each [TAP] marker in the script is a cut point; trim the waiting
  before each tap so Act 3 fits 45 seconds without speeding up the voice. Never speed up or fake Sakhi’s audio.</div>
</section>"""


def section_text_audio() -> str:
    return """
<section class="page">
  <h2><span>08</span> On-screen text, subtitles and audio</h2>
  <h3>Burned-in subtitles (always on: many viewers watch muted)</h3>
  <table class="grid compact">
    <tr><th style="width:14%">Time</th><th style="width:43%">Original</th><th>English subtitle</th></tr>
    <tr><td>0:47</td><td class="indic">ತಾಯಿಯ ಇಂದಿನ ಬಿಪಿ ಎಷ್ಟು ದಾಖಲಾಗಿದೆ?</td><td>Sakhi: “What is the mother’s blood pressure today?”</td></tr>
    <tr><td>0:51</td><td class="indic">ಬಿ ಪಿ 148 ಬೈ 96</td><td>Sunita: “BP 148 by 96.”</td></tr>
    <tr><td>1:22</td><td class="indic">ಇಂದೇ PHC ಗೆ ಬನ್ನಿ</td><td>Advice: “Come to the PHC today.” (use the exact text the app shows)</td></tr>
  </table>
  <h3>Supers and lower thirds</h3>
  <table class="grid compact">
    <tr><th style="width:14%">Time</th><th>Text</th></tr>
    <tr><td>0:00–2:00</td><td>Running timer, top right, white, tabular figures: 00:00 → freezes at 01:30</td></tr>
    <tr><td>0:02</td><td>Bidar district, Karnataka, India</td></tr>
    <tr><td>0:15</td><td>HIGH BP + HEADACHE + BLURRED VISION = SEVERE PRE-ECLAMPSIA RISK</td></tr>
    <tr><td>0:23</td><td>The warning exists. It just doesn’t travel.</td></tr>
    <tr><td>0:28</td><td>Tara — open-source maternal care network</td></tr>
    <tr><td>0:38</td><td>KANNADA · HINDI | NO TYPING | BASIC ANDROID PHONE</td></tr>
    <tr><td>1:02</td><td>Speech → text ≈ 1 s · rule PE-04 fired</td></tr>
    <tr><td>1:15</td><td>Doctor: 1 tap</td></tr>
    <tr><td>1:28</td><td>Village → doctor → mother, in this demo: 01:30</td></tr>
    <tr><td>1:33</td><td>OPEN SOURCE | LOW BANDWIDTH · WORKS OFFLINE | SAFETY BY DESIGN</td></tr>
    <tr><td>1:50</td><td>Every danger sign, heard in time. · Applying to UNICEF Femtech Ventures · Pillar 1</td></tr>
  </table>
  <h3>Sound</h3>
  <ul>
    <li>Music: a soft, warm bed (acoustic, no vocals) at −24 LUFS under voice; it drops out for Sakhi’s first line, returns at the logo.</li>
    <li>Act 3: a single soft tick on each [TAP]; one low pulse when the danger card appears; the SOS ripple has its own sound.</li>
    <li>Final mix: −14 LUFS integrated, voice always intelligible on a phone speaker.</li>
  </ul>
</section>"""


def section_production() -> str:
    return """
<section class="page">
  <h2><span>09</span> Production, safeguarding and delivery specs</h2>
  <h3>Safeguarding (a condition of UNICEF’s investment, so it is also in the film)</h3>
  <ul class="checks">
    <li class="ok">All data on screen is synthetic demo data (Lakshmi D., Sunita). Say so in the description: “All patient data shown is fictional.”</li>
    <li class="ok">Anyone filmed signs a consent and release form, in Kannada, read aloud to them. No minors on camera.</li>
    <li class="ok">No real patient registers, faces of patients, phone numbers or addresses anywhere in frame.</li>
    <li class="ok">The SOS sequence is labelled as a prototype demonstration; it must not suggest it replaces 108 emergency services.</li>
    <li class="warn">Do not use the UNICEF logo on the end card unless UNICEF has approved it; use the text line instead.</li>
  </ul>
  <h3>Delivery</h3>
  <table class="grid compact">
    <tr><th style="width:30%">Item</th><th>Spec</th></tr>
    <tr><td>Master</td><td>1920 × 1080, 25 or 30 fps, H.264 MP4, ≤ 2:00, stereo AAC 48 kHz</td></tr>
    <tr><td>Vertical cut</td><td>1080 × 1920 for social / mobile reviewers: same audio, phone recordings full screen</td></tr>
    <tr><td>Captions</td><td>Burned-in English + a separate .srt file</td></tr>
    <tr><td>Hosting</td><td>Unlisted YouTube or Vimeo link (works on low bandwidth, reviewers can lower quality)</td></tr>
    <tr><td>File name</td><td>Tara_UNICEF_Femtech_Pitch_2026.mp4</td></tr>
  </table>
  <h3>Schedule to the deadline (4 November 2026)</h3>
  <table class="grid compact">
    <tr><th style="width:22%">When</th><th>What</th></tr>
    <tr><td>Day 1</td><td>Confirm placeholders (pilot size, district, licence), add LICENSE, reset demo data, rehearse script</td></tr>
    <tr><td>Day 2</td><td>Screen recordings S3–S9; on-camera S10</td></tr>
    <tr><td>Day 3</td><td>Field footage S1–S2 with consent; voice-over recording</td></tr>
    <tr><td>Day 4</td><td>Edit, subtitles, mix; internal review against the fact-check sheet</td></tr>
    <tr><td>Day 5</td><td>Final export; submit with at least 48 hours to spare</td></tr>
  </table>
</section>"""


def section_facts() -> str:
    return """
<section class="page">
  <h2><span>10</span> Fact-check sheet: say only what is true</h2>
  <p class="lead">Reviewers check claims. Every factual statement in the film is listed here with its status. Change the script,
  not the facts.</p>
  <table class="grid compact">
    <tr><th style="width:36%">Claim in the film</th><th style="width:16%">Status</th><th>Basis / what to do</th></tr>
    <tr><td>Sakhi talks in Kannada or Hindi</td><td class="good">True (prototype)</td><td>Both languages in the check-in today.</td></tr>
    <tr><td>Asks one question at a time; 24 antenatal questions</td><td class="good">True</td><td>The check-in engine asks the 24 questions in order, one per turn.</td></tr>
    <tr><td>Her words become text in about a second</td><td class="good">True (measured)</td><td>Speech-to-text measured at about 0.4–1.2 s per answer in testing.</td></tr>
    <tr><td>Transparent, auditable rules flag severe pre-eclampsia</td><td class="good">True</td><td>Deterministic rules PE-01/PE-03 → PE-04, shown in the rule audit.</td></tr>
    <tr><td>First in the doctor’s queue; one tap confirms the referral</td><td class="good">True</td><td>Doctor home lists urgent cases first; “Confirm referral” sends the advice.</td></tr>
    <tr><td>Advice reaches the mother in her language</td><td class="warn">Check</td><td>Make sure the advice shown on the mother’s phone in the take is in Kannada.</td></tr>
    <tr><td>SOS alerts the nearest ASHAs, her own first</td><td class="warn">Prototype</td><td>Works in the app as a demonstration with demo locations; label it as such.</td></tr>
    <tr><td>Runs on low bandwidth; keeps working offline</td><td class="good">True</td><td>Answers are kept on the phone and sent when the network returns.</td></tr>
    <tr><td>Open source</td><td class="bad">Action</td><td>Add a LICENSE (for example Apache-2.0 or MIT) and make the repository public before submitting.</td></tr>
    <tr><td>Consent first, minimal data</td><td class="warn">Design claim</td><td>Have the consent flow and data policy ready to show in the application.</td></tr>
    <tr><td>Pilot with two hundred ASHAs in Bidar</td><td class="warn">Placeholder</td><td>Replace with the team’s real plan before recording (number and district).</td></tr>
  </table>
  <div class="callout"><b>Kept out on purpose:</b> national maternal-mortality statistics. They are powerful but easy to
  misquote. If you add one, take it from the latest WHO or Government of India source and cite it on screen.</div>
</section>"""


def section_checklist() -> str:
    return """
<section class="page">
  <h2><span>11</span> Submission checklist</h2>
  <ul class="checks big">
    <li class="todo">Placeholders confirmed: pilot size, district, licence name</li>
    <li class="todo">LICENSE file added; repository public; README explains how to run Tara</li>
    <li class="todo">Company registration in a UNICEF programme country confirmed (India on the list)</li>
    <li class="todo">Demo data reset; every Act 3 screen recorded from the real app</li>
    <li class="todo">Consent forms signed for everyone on camera</li>
    <li class="todo">Film ≤ 2:00; spoken words within 260–280; burned-in subtitles</li>
    <li class="todo">Fact-check sheet signed off by two people</li>
    <li class="todo">No UNICEF logo unless approved</li>
    <li class="todo">Unlisted link tested on a phone over mobile data</li>
    <li class="todo">Application submitted through the call page before 4 November 2026</li>
  </ul>
  <p class="big-quote">Every danger sign, heard in time.</p>
</section>"""


CSS = """
@page { size: A4; margin: 16mm 15mm 18mm; }
* { box-sizing: border-box; }
body { font-family: 'Segoe UI', 'Nirmala UI', sans-serif; color: #2B1B21; font-size: 10.2pt; line-height: 1.45; margin: 0; }
.indic, .spoken, .extra { font-family: 'Segoe UI', 'Nirmala UI', sans-serif; }
h1 { font-size: 40pt; line-height: 1.05; margin: 8mm 0 4mm; color: #A11D44; letter-spacing: -0.5pt; }
h2 { font-size: 17pt; margin: 0 0 4mm; color: #2B1B21; border-bottom: 2px solid #A11D44; padding-bottom: 2mm; }
h2 span { color: #A11D44; font-weight: 700; margin-right: 6px; }
h3 { font-size: 11.5pt; margin: 6mm 0 2mm; color: #A11D44; }
h3 small { color: #5A464D; font-weight: 400; margin-left: 6px; }
p { margin: 0 0 2.5mm; }
.lead { font-size: 10.8pt; color: #3d2a31; }
.small { font-size: 9pt; color: #5A464D; }
.page { page-break-before: always; }
.cover { min-height: 255mm; display: flex; flex-direction: column; justify-content: center; }
.logo { width: 46mm; margin-bottom: 6mm; }
.logo-text { font-size: 30pt; color: #A11D44; font-weight: 700; }
.kicker { letter-spacing: 2pt; font-size: 9pt; color: #5A464D; font-weight: 600; }
.sub { font-size: 13pt; max-width: 150mm; }
.cover-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 4mm; margin: 8mm 0; }
.cover-grid div { border: 1px solid #ECE4E7; border-left: 4px solid #A11D44; border-radius: 3px; padding: 3mm 4mm; }
.cover-grid span { display: block; font-size: 8pt; letter-spacing: 1.2pt; text-transform: uppercase; color: #5A464D; }
.cover-grid b { display: block; font-size: 12pt; margin: 1mm 0; }
.cover-grid em { font-style: normal; font-size: 9pt; color: #5A464D; }
.note { font-size: 9pt; color: #5A464D; border-top: 1px solid #ECE4E7; padding-top: 3mm; }
.toc { list-style: none; padding: 0; font-size: 12pt; }
.toc li { padding: 2.6mm 0; border-bottom: 1px solid #ECE4E7; }
.toc span { display: inline-block; width: 12mm; color: #A11D44; font-weight: 700; }
table { width: 100%; border-collapse: collapse; margin: 2mm 0 3mm; }
th { background: #F7E4E8; color: #2B1B21; text-align: left; font-size: 8.8pt; letter-spacing: 0.3pt; padding: 2mm; border: 1px solid #E8D3D9; }
td { border: 1px solid #ECE4E7; padding: 2mm; vertical-align: top; }
tr { page-break-inside: avoid; }
.compact td, .compact th { padding: 1.5mm 2mm; font-size: 9.2pt; }
tr.hl td { background: #FBF3F5; }
.tag { display: inline-block; margin-top: 1.5mm; font-size: 7.5pt; font-weight: 700; letter-spacing: 1pt; color: #fff; background: #A11D44; padding: 0.5mm 2mm; border-radius: 2px; }
.tag.t2 { background: #8F3500; }
.num { text-align: center; font-weight: 700; }
.good { color: #2F6B4F; font-weight: 700; }
.bad { color: #A11D44; font-weight: 700; }
.warn { color: #8F3500; font-weight: 700; }
tr.total td { background: #F9F6F7; }
.callout { background: #F9F6F7; border-left: 4px solid #A11D44; padding: 3mm 4mm; margin: 4mm 0; font-size: 10pt; }
.cards { display: grid; grid-template-columns: 1fr 1fr; gap: 3mm; margin: 4mm 0; }
.card { border: 1px solid #ECE4E7; border-top: 3px solid #A11D44; padding: 3mm; border-radius: 3px; }
.card p { margin: 1mm 0 0; font-size: 9.4pt; color: #3d2a31; }
.big-quote { font-size: 22pt; color: #A11D44; font-weight: 700; text-align: center; margin: 8mm 0 3mm; }
.script th { font-size: 9pt; }
.script td { font-size: 9.4pt; }
.act-block { break-inside: avoid; page-break-inside: avoid; margin-bottom: 4mm; }
.act-h { background: #A11D44; color: #fff; font-weight: 700; letter-spacing: 1pt; font-size: 8.8pt; padding: 1.6mm 2mm; break-after: avoid; }
.act-block table { margin-top: 0; }
.script tr.act td { background: #A11D44; color: #fff; font-weight: 700; letter-spacing: 1pt; font-size: 8.8pt; padding: 1.6mm 2mm; }
.time b { display: block; font-size: 10pt; }
.marker { display: inline-block; margin-top: 1.5mm; font-size: 7.6pt; font-weight: 700; color: #A11D44; letter-spacing: 0.5pt; }
.visual { color: #3d2a31; }
.extra { margin-top: 2mm; padding-top: 1.5mm; border-top: 1px dashed #E8D3D9; font-size: 8.8pt; color: #5A464D; }
.spoken { font-size: 10.6pt !important; line-height: 1.5; }
.who { display: block; font-size: 7.6pt; letter-spacing: 1pt; text-transform: uppercase; color: #5A464D; font-weight: 700; margin-bottom: 1mm; }
.wc { display: block; margin-top: 1.5mm; font-size: 7.6pt; color: #8a7a80; }
.read { font-size: 12.5pt; line-height: 1.7; background: #FBF3F5; padding: 3mm 4mm; border-radius: 3px; }
ul { margin: 1mm 0 3mm; padding-left: 5mm; }
li { margin: 1mm 0; }
.checks { list-style: none; padding-left: 0; }
.checks li { padding-left: 7mm; position: relative; }
.checks li::before { position: absolute; left: 0; font-weight: 700; }
.checks .ok::before { content: '✓'; color: #2F6B4F; }
.checks .warn { color: inherit; font-weight: 400; }
.checks .warn::before { content: '!'; color: #8F3500; }
.checks .info::before { content: 'i'; color: #5A464D; }
.checks .todo::before { content: '☐'; color: #A11D44; }
.checks.big li { font-size: 12pt; padding-top: 1.5mm; padding-bottom: 1.5mm; border-bottom: 1px solid #ECE4E7; }
"""


def build_html() -> str:
    body = "".join(
        [
            section_cover(),
            section_contents(),
            section_call(),
            section_idea(),
            section_benchmark(),
            section_plan(),
            section_script(),
            section_readthrough(),
            section_runbook(),
            section_text_audio(),
            section_production(),
            section_facts(),
            section_checklist(),
        ]
    )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>Tara — UNICEF Femtech Ventures pitch-video script</title><style>{CSS}</style></head><body>{body}</body></html>"""


def main() -> None:
    for act, name, _, (lo, hi), _ in ACTS:
        n = act_words(act)
        print(f"Act {act} {name}: {n} words (budget {lo}-{hi}) {'OK' if lo <= n <= hi else 'OUT OF BUDGET'}")
    print(f"Total: {TOTAL} words (budget 260-280) {'OK' if 260 <= TOTAL <= 280 else 'OUT OF BUDGET'}")
    OUT_HTML.write_text(build_html(), encoding="utf-8")
    profile = Path(tempfile.mkdtemp(prefix="edge-pdf-"))
    try:
        subprocess.run(
            [str(EDGE), "--headless=new", "--disable-gpu", f"--user-data-dir={profile}", "--no-pdf-header-footer",
             f"--print-to-pdf={OUT_PDF}", OUT_HTML.as_uri()],
            check=True, timeout=120, capture_output=True,
        )
    finally:
        shutil.rmtree(profile, ignore_errors=True)  # the disk is nearly full: leave nothing behind
    print(f"PDF: {OUT_PDF} ({OUT_PDF.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
