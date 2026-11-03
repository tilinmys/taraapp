import { NextRequest, NextResponse } from "next/server";
import { workerHeaders } from "@/lib/worker";

const WORKER_STATUS_URL = process.env.WORKER_STATUS_URL || "http://localhost:8080";

// Only these status_server /dashboard/* endpoints are reachable from the
// browser. An allowlist rather than a blind pass-through, so this proxy can
// never be used to reach any other worker route.
const ALLOWED = new Set([
  "summary", "health", "patients", "patient", "turns", "events",
  "costs", "costs/service", // AWS spend (Cost Explorer, cached server-side)
]);

// Proxies the admin dashboard's reads to status_server.py, which reads the
// same database the voice worker writes (sakhi_dashboard_store). Patient
// data flows through here, so the whole app must stay behind the nginx
// login - see deploy/aws/sakhi.nginx.conf.
export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ path: string[] }> }
) {
  const { path } = await params;
  const endpoint = path.join("/");
  if (!ALLOWED.has(endpoint)) {
    return NextResponse.json({ error: "unknown dashboard endpoint" }, { status: 404 });
  }
  const query = req.nextUrl.searchParams.toString();
  try {
    const res = await fetch(
      `${WORKER_STATUS_URL}/dashboard/${endpoint}${query ? `?${query}` : ""}`,
      { cache: "no-store", headers: workerHeaders() }
    );
    return NextResponse.json(await res.json(), { status: res.status });
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return NextResponse.json({ error: `Worker unreachable: ${message}` }, { status: 502 });
  }
}
