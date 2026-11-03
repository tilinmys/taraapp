// Capture the real Tara app (Expo web, running at localhost:8081) as phone
// screenshots for the film: 412 x 915 viewport at 3x = 1236 x 2745 PNGs.
//   node tools/capture-app.mjs [only-name]
// Fresh browser profile each run, so the app shows its seeded demo data
// (Lakshmi D., Sunita). Every screen the film shows comes from here.
import { mkdirSync } from 'node:fs';
import puppeteer from 'puppeteer-core';

const BASE = process.env.APP_URL || 'http://localhost:8081';
const OUT = 'assets/app';
const CHROME = process.env.CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const only = process.argv[2];
mkdirSync(OUT, { recursive: true });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function clickText(page, text) {
  const ok = await page.evaluate((t) => {
    const els = [...document.querySelectorAll('[role="button"], button, a, [tabindex]')];
    const el = els.find((e) => (e.innerText || e.getAttribute('aria-label') || '').includes(t));
    if (!el) return false;
    el.click();
    return true;
  }, text);
  if (!ok) throw new Error(`no clickable "${text}"`);
}

const shots = [
  { name: 'asha-home', path: '/asha' },
  { name: 'asha-today', path: '/asha/today' },
  { name: 'asha-lakshmi', path: '/asha/mother/lakshmi' },
  { name: 'session-intro', path: '/asha/session/lakshmi' },
  {
    name: 'session-talking',
    path: '/asha/session/lakshmi',
    act: async (page) => {
      await clickText(page, 'ಪ್ರಶ್ನೆಗಳನ್ನು ಶುರು');
      await sleep(5500);
    },
  },
  { name: 'doctor-home', path: '/doctor' },
  { name: 'doctor-case', path: '/doctor/case/lakshmi' },
  { name: 'mother-home', path: '/mother' },
  // her Home scrolled to the doctor's advice card (the app scrolls inside its own view)
  {
    name: 'mother-advice',
    path: '/mother',
    act: async (page) => {
      await page.mouse.move(206, 500);
      for (let i = 0; i < 2; i++) {
        await page.mouse.wheel({ deltaY: 280 });
        await sleep(120);
      }
      await sleep(900);
    },
  },
];

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: true,
  args: ['--use-fake-ui-for-media-stream', '--autoplay-policy=no-user-gesture-required'],
});
try {
  for (const s of shots) {
    if (only && s.name !== only) continue;
    const page = await browser.newPage();
    await page.setViewport({ width: 412, height: 915, deviceScaleFactor: 3, isMobile: true, hasTouch: true });
    await page.goto(BASE + s.path, { waitUntil: 'networkidle2', timeout: 90000 });
    await page.evaluate(() => document.fonts.ready);
    await sleep(2500); // entrance animations settle
    if (s.act) await s.act(page);
    await page.screenshot({ path: `${OUT}/${s.name}.png`, fullPage: !!s.fullPage });
    console.log('captured', s.name);
    await page.close();
  }
} finally {
  await browser.close();
}
