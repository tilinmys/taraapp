import { NextResponse } from "next/server";

const WORKER_STATUS_URL = process.env.WORKER_STATUS_URL || "http://localhost:8080";

// Proxies to status_server.py's /db route: health of the SAKHI maternal store
// (row counts, journal mode, integrity check), not the legacy clinic SQLite in
// db_helper.py. Same proxy pattern as /api/config - the browser never needs the
// worker's host, and the database path never reaches the client except as a
// display string.
export async function GET() {
  try {
    const res = await fetch(`${WORKER_STATUS_URL}/db`, { cache: "no-store" });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch (error: any) {
    return NextResponse.json(
      { error: `Could not reach worker status server at ${WORKER_STATUS_URL}: ${error.message}` },
      { status: 502 }
    );
  }
}
