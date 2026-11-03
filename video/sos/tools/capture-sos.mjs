// Capture the real SOS flow of the Tara app (Expo web at localhost:8081) as
// phone screenshots: 412 x 915 viewport at 3x = 1236 x 2745 PNGs.
//   node tools/capture-sos.mjs            (from video/sos; the app must be running)
// A fresh browser profile each run, so the app shows its seeded demo data.
// The timings follow app/src/data/sos.ts: 3 s countdown, wave 1 alerting,
// "alerted" after 600 ms, "seen" from 1.8 s, Sunita accepts at 5.2 s.
// The film rebuilds the motion in HTML; these frames are its reference and
// the source of every word, name, distance and time it shows.
import { mkdirSync } from 'node:fs';
import puppeteer from 'puppeteer-core';

const BASE = process.env.APP_URL || 'http://localhost:8081';
const OUT = 'assets/app';
const CHROME = process.env.CHROME || 'C:/Program Files/Google/Chrome/Application/chrome.exe';
mkdirSync(OUT, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const browser = await puppeteer.launch({ executablePath: CHROME, headless: true });
try {
  const page = await browser.newPage();
  await page.setViewport({ width: 412, height: 915, deviceScaleFactor: 3, isMobile: true, hasTouch: true });
  await page.goto(`${BASE}/mother`, { waitUntil: 'networkidle2', timeout: 90000 });
  await page.evaluate(() => document.fonts.ready);
  await sleep(2500);
  await page.screenshot({ path: `${OUT}/mother-home.png` });
  console.log('captured mother-home');

  // Tap the red SOS tab on the right edge.
  const tapped = await page.evaluate(() => {
    const el = [...document.querySelectorAll('[role="button"]')].find((e) => (e.getAttribute('aria-label') || '').startsWith('SOS'));
    if (!el) return false;
    el.click();
    return true;
  });
  if (!tapped) throw new Error('SOS tab not found');
  const t0 = Date.now();
  const at = async (ms, name) => {
    const wait = ms - (Date.now() - t0);
    if (wait > 0) await sleep(wait);
    await page.screenshot({ path: `${OUT}/${name}.png` });
    console.log('captured', name, `${Date.now() - t0} ms`);
  };
  await at(450, 'sos-countdown-3');
  await at(1450, 'sos-countdown-2');
  await at(3500, 'sos-alerting');
  await at(4300, 'sos-alerted');
  await at(5600, 'sos-seen');
  await at(8700, 'sos-coming');
} finally {
  await browser.close();
}
