import { NextRequest, NextResponse } from "next/server";
import { workerHeaders } from "@/lib/worker";

const WORKER_STATUS_URL = process.env.WORKER_STATUS_URL || "http://localhost:8080";

// Proxies to status_server.py's /reports/flag route. Body:
// { report_id: number, field: "manual_risk_flag" | "flagged_to_doctor", value: boolean, flagged_by?: string }
export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const res = await fetch(`${WORKER_STATUS_URL}/reports/flag`, {
      method: "POST",
      headers: workerHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    });
    return NextResponse.json(await res.json(), { status: res.status });
  } catch (error: any) {
    return NextResponse.json({ error: `Worker unreachable: ${error.message}` }, { status: 502 });
  }
}
