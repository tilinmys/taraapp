// Learn more https://docs.expo.dev/guides/customizing-metro
const fs = require('fs');
const { getDefaultConfig } = require('expo/metro-config');

// Windows fix: "EMFILE: too many open files" from the Metro cache.
// Metro reads thousands of cache files at once (Promise.all over the module
// graph). On Windows the process runs out of file handles; one failed read
// fails the whole bundle, and the phone gets a broken JS bundle (LogBox
// "undefined is not a function"). Cap concurrent fs.promises opens and retry
// EMFILE with backoff instead of failing. Metro looks these up at call time
// (fs.promises.readFile), so patching the shared object covers it.
const MAX_OPEN = 64;
let active = 0;
const queue = [];
const pump = () => {
  while (active < MAX_OPEN && queue.length) queue.shift()();
};
const throttle = (fn) =>
  function throttled(...args) {
    return new Promise((resolve, reject) => {
      const attempt = (n) => {
        active++;
        fn.apply(fs.promises, args)
          .then(resolve, (err) => {
            if (err && (err.code === 'EMFILE' || err.code === 'ENFILE') && n < 8) {
              setTimeout(() => queue.push(() => attempt(n + 1)) && pump(), 40 * (n + 1));
            } else {
              reject(err);
            }
          })
          .finally(() => {
            active--;
            pump();
          });
      };
      queue.push(() => attempt(0));
      pump();
    });
  };
if (!fs.promises.__taraThrottled) {
  for (const name of ['readFile', 'writeFile', 'open', 'readdir', 'stat', 'lstat']) {
    fs.promises[name] = throttle(fs.promises[name]);
  }
  fs.promises.__taraThrottled = true;
}

/** @type {import('expo/metro-config').MetroConfig} */
const config = getDefaultConfig(__dirname);

// Dev only: the Sakhi gateway (scripts/sakhi_gateway_dev.py, localhost:8787)
// rides Metro's own tunnel at /api/sakhi/*. The phone then reaches the
// gateway at the SAME address it loads the app from (scripts/tunnel.mjs sets
// EXPO_PUBLIC_SAKHI_GATEWAY to it), so it can never hold a dead second tunnel
// address and silently fall back to the demo. Streams both ways: audio
// uploads and downloads are never buffered here.
const http = require('http');
const GATEWAY_PORT = Number(process.env.SAKHI_GATEWAY_PORT || 8787);
config.server = {
  ...config.server,
  enhanceMiddleware: (middleware) => (req, res, next) => {
    if (!req.url || !req.url.startsWith('/api/sakhi')) return middleware(req, res, next);
    const headers = { ...req.headers, host: `localhost:${GATEWAY_PORT}` };
    const upstream = http.request({ host: '127.0.0.1', port: GATEWAY_PORT, path: req.url, method: req.method, headers }, (up) => {
      res.writeHead(up.statusCode || 502, up.headers);
      up.pipe(res);
    });
    upstream.setTimeout(60_000, () => upstream.destroy(new Error('gateway timeout')));
    upstream.on('error', (err) => {
      if (res.headersSent) return res.destroy();
      res.writeHead(502, { 'content-type': 'application/json' });
      res.end(JSON.stringify({ error: `Sakhi gateway not reachable on this PC (${err.code || err.message}). Start: python scripts/sakhi_gateway_dev.py` }));
    });
    req.pipe(upstream);
  },
};

module.exports = config;
