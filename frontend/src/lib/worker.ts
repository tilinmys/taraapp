// Server-only: the bearer token status_server.py asks for on every route
// that returns patient data, and on every write. Read from the server's
// environment (never NEXT_PUBLIC_*), so it is added here, in the Next.js
// proxy routes, and never reaches the browser.
export function workerHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const token = (process.env.SAKHI_DASHBOARD_TOKEN || process.env.SAKHI_GATEWAY_TOKEN || "").trim();
  return token ? { ...extra, Authorization: `Bearer ${token}` } : extra;
}
