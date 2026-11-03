import { NextRequest, NextResponse } from "next/server";
import { workerHeaders } from "@/lib/worker";

const WORKER_STATUS_URL = process.env.WORKER_STATUS_URL || "http://localhost:8080";

// Proxies to status_server.py's /reports route - the Sakhi-specific
// call_reports table (risk_level, escalated, intake progress), not the
// legacy generic report shape.
export async function GET(req: NextRequest) {
  const limit = req.nextUrl.searchParams.get("limit") || "50";
  try {
    const res = await fetch(`${WORKER_STATUS_URL}/reports?limit=${encodeURIComponent(limit)}`, {
      cache: "no-store",
      headers: workerHeaders(),
    });
    return NextResponse.json(await res.json(), { status: res.status });
  } catch (error: any) {
    return NextResponse.json(
      { error: `Worker unreachable: ${error.message}`, reports: [] },
      { status: 502 }
    );
  }
}
