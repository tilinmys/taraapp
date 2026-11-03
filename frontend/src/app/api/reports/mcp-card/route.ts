import { NextRequest, NextResponse } from "next/server";
import { workerHeaders } from "@/lib/worker";

const WORKER_STATUS_URL = process.env.WORKER_STATUS_URL || "http://localhost:8080";

// Proxies to status_server.py's /reports/mcp-card route - the MCP/RCH-shaped
// end-of-visit card (Module C, 2026-08-30). Accepts either `id` (a
// call_reports row id, used by the /reports dashboard) or `session_id` (the
// LiveKit room name, which is what the live call page already knows right
// after end_call - see page.tsx's "View visit summary" button).
export async function GET(req: NextRequest) {
  const id = req.nextUrl.searchParams.get("id");
  const sessionId = req.nextUrl.searchParams.get("session_id");
  if (!id && !sessionId) {
    return NextResponse.json({ error: "id or session_id is required" }, { status: 400 });
  }
  const params = new URLSearchParams();
  if (id) params.set("id", id);
  if (sessionId) params.set("session_id", sessionId);
  try {
    const res = await fetch(`${WORKER_STATUS_URL}/reports/mcp-card?${params.toString()}`, {
      cache: "no-store",
      headers: workerHeaders(),
    });
    return NextResponse.json(await res.json(), { status: res.status });
  } catch (error: any) {
    return NextResponse.json(
      { error: `Worker unreachable: ${error.message}` },
      { status: 502 }
    );
  }
}
