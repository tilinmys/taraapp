// One-command Expo Go tunnel: `npm run tunnel`.
//
// Why: the clinic Wi-Fi is a Windows "Public" network (inbound blocked) and
// Expo's ngrok tunnel is unreliable on SDK 57, so phones could not load the
// app. This opens a Cloudflare quick tunnel (no account, no firewall change)
// to Metro and starts Expo with EXPO_PACKAGER_PROXY_URL pointing at it, so the
// QR code and every bundle/asset URL go through the tunnel. Works from any
// network, including mobile data.
import { spawn } from 'node:child_process';
import { existsSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const PORT = Number(process.env.TUNNEL_PORT || 8081);
const bin = join(root, 'node_modules', 'cloudflared', 'bin', process.platform === 'win32' ? 'cloudflared.exe' : 'cloudflared');

if (!existsSync(bin)) {
  console.error('cloudflared is missing. Run `npm install` in the app folder first.');
  process.exit(1);
}

console.log(`\n  Opening a secure tunnel to Metro on port ${PORT}...\n`);
const tunnel = spawn(bin, ['tunnel', '--no-autoupdate', '--url', `http://localhost:${PORT}`], {
  stdio: ['ignore', 'pipe', 'pipe'],
});

let expo = null;
let publicHost = null;
let registered = false;
let starting = false;
const URL_RE = /https:\/\/([a-z0-9-]+\.trycloudflare\.com)/;

// The hostname is printed before the edge has registered it, and phones that
// look it up too early cache "does not exist" for a minute. So: wait for the
// edge registration, then until the name resolves publicly, then start Expo.
const onTunnelOutput = (chunk) => {
  const text = String(chunk);
  if (!publicHost) publicHost = text.match(URL_RE)?.[1] ?? null;
  if (/Registered tunnel connection/.test(text)) registered = true;
  if (/\bERR\b/.test(text) && !expo) process.stderr.write(`  cloudflared: ${text.trim()}\n`);
  if (publicHost && registered && !starting) {
    starting = true;
    waitUntilLive(publicHost).then(startExpo);
  }
};
tunnel.stdout.on('data', onTunnelOutput);
tunnel.stderr.on('data', onTunnelOutput);

// Two public DNS-over-HTTPS resolvers, asked in turn. Asking one resolver
// too early makes it cache "no such name" for minutes (negative caching),
// which used to stall this wait for ~10 minutes; alternating avoids that.
const RESOLVERS = [
  (host) => `https://dns.google/resolve?name=${host}&type=A`,
  (host) => `https://1.1.1.1/dns-query?name=${host}&type=A`,
];

async function resolvesPublicly(host, attempt) {
  try {
    // Short timeout: one slow lookup must not stall the whole wait.
    const res = await fetch(RESOLVERS[attempt % RESOLVERS.length](host), {
      headers: { accept: 'application/dns-json' },
      signal: AbortSignal.timeout(3000),
    });
    const body = await res.json();
    return body.Status === 0 && Array.isArray(body.Answer) && body.Answer.length > 0;
  } catch {
    return false;
  }
}

async function waitUntilLive(host) {
  process.stdout.write('  Waiting for the tunnel address to go live');
  // Give Cloudflare's edge a moment before the first lookup, then check
  // every 2 s for at most ~45 s. Expo's own start-up takes longer than any
  // remaining DNS delay, and phones use their own (uncached) resolvers.
  await new Promise((r) => setTimeout(r, 6000));
  for (let i = 0; i < 18; i++) {
    if (await resolvesPublicly(host, i)) break;
    process.stdout.write('.');
    await new Promise((r) => setTimeout(r, 2000));
  }
  process.stdout.write('\n');
}

function startExpo() {
  if (closing) return;
  // Plain http keeps Expo Go's exp:// URL on port 80, which it loads directly.
  const publicUrl = `http://${publicHost}`;
  console.log(`  Tunnel ready: ${publicUrl}\n`);
  expo = spawn('npx', ['expo', 'start', '--go', '--port', String(PORT), ...process.argv.slice(2)], {
    cwd: root,
    stdio: 'inherit',
    shell: process.platform === 'win32',
    // The Sakhi gateway rides this same tunnel (metro.config.js forwards
    // /api/sakhi/* to localhost:8787), so the app is built with THIS address:
    // it cannot outlive or disagree with the address the app is loaded from.
    // (Set here, it wins over the value in .env.local.)
    env: { ...process.env, EXPO_PACKAGER_PROXY_URL: publicUrl, EXPO_PUBLIC_SAKHI_GATEWAY: `https://${publicHost}/api/sakhi` },
  });
  expo.on('exit', (code) => shutdown(code ?? 0));
  prewarm();
}

// A cold bundle build can take longer than Cloudflare's 100 s edge timeout,
// which shows on the phone as a Cloudflare error. Build both phone bundles
// locally first, then say when it is safe to scan.
async function prewarm() {
  const local = `http://localhost:${PORT}`;
  for (let i = 0; i < 120; i++) {
    try {
      if ((await (await fetch(`${local}/status`, { signal: AbortSignal.timeout(2000) })).text()).includes('running')) break;
    } catch {}
    await new Promise((r) => setTimeout(r, 1000));
  }
  const q =
    'dev=true&hot=false&lazy=true&transform.engine=hermes&transform.bytecode=1&transform.routerRoot=src%2Fapp&transform.reactCompiler=true&unstable_transformProfile=hermes-stable';
  for (const platform of ['android', 'ios']) {
    try {
      await (await fetch(`${local}/node_modules/expo-router/entry.bundle?platform=${platform}&${q}`, { signal: AbortSignal.timeout(600_000) })).arrayBuffer();
    } catch {
      // Not fatal: the phone will simply trigger the build itself.
    }
  }
  if (closing) return;
  // Every restart gets a NEW random address; the old one answers HTTP 530.
  // Save the current one, and prove the public path works end to end.
  const link = `exp://${publicHost}`;
  writeFileSync(join(root, 'tunnel-url.txt'), `${link}\n`);
  let live = false;
  try {
    live = (await (await fetch(`http://${publicHost}/status`, { signal: AbortSignal.timeout(15_000) })).text()).includes('running');
  } catch {}
  console.log(
    live
      ? `\n  Ready. Scan the QR code now, or open ${link}\n  (saved in tunnel-url.txt). Do NOT use Expo Go's "Recently opened": old addresses give HTTP 530.\n`
      : `\n  Bundles are built, but ${link} did not answer yet. Wait a minute, then scan the QR code.\n`,
  );
}

const giveUp = setTimeout(() => {
  if (!expo) {
    console.error('\n  Tunnel did not come up within 90 s. Check the internet connection and retry.');
    shutdown(1);
  }
}, 90_000);

tunnel.on('exit', (code) => {
  if (!expo) {
    console.error(`  Tunnel exited early (code ${code}).`);
    process.exit(1);
  }
  console.error('\n  Tunnel closed. Restart with `npm run tunnel`.');
  shutdown(1);
});

let closing = false;
function shutdown(code) {
  if (closing) return;
  closing = true;
  clearTimeout(giveUp);
  tunnel.kill();
  if (expo && expo.exitCode === null) expo.kill();
  process.exit(code);
}
process.on('SIGINT', () => shutdown(0));
process.on('SIGTERM', () => shutdown(0));
