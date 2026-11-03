import { NextRequest, NextResponse } from "next/server";
import { workerHeaders } from "@/lib/worker";

const WORKER_STATUS_URL = process.env.WORKER_STATUS_URL || "http://localhost:8080";

// Proxies to status_server.py's /logs route - the real pipeline_event stream
// (STT/LLM/TTS/turn/tools stages, with provider+model detail) emitted by
// agent.py during a live call. The browser polls this with an incrementing
// `since` cursor.
export async function GET(req: NextRequest) {
  const since = req.nextUrl.searchParams.get("since") || "0";
  try {
    const res = await fetch(`${WORKER_STATUS_URL}/logs?since=${encodeURIComponent(since)}`, {
      cache: "no-store",
      headers: workerHeaders(),
    });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch (error: any) {
    return NextResponse.json(
      { error: `Could not reach worker status server at ${WORKER_STATUS_URL}: ${error.message}`, next: Number(since) || 0, events: [] },
      { status: 502 }
    );
  }
}
