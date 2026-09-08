import { AccessToken, AgentDispatchClient } from "livekit-server-sdk";
import { randomUUID } from "crypto";
import { NextRequest, NextResponse } from "next/server";

const WORKER_STATUS_URL = process.env.WORKER_STATUS_URL || "http://127.0.0.1:8080";
// Default matches .env.local's LIVEKIT_AGENT_NAME - only reached if that var
// is somehow unset. NOT "mystree-care": a separate worker under that name
// runs on this same LiveKit Cloud project, and a silent fallback would
// dispatch this app's calls to it instead of the Sakhi worker (2026-09-02).
const AGENT_NAME = process.env.LIVEKIT_AGENT_NAME || "sakhi-maternal-care";

async function fetchWorkerStatus(): Promise<{ ready: boolean; reason?: string }> {
  try {
    const res = await fetch(`${WORKER_STATUS_URL}/health`, { cache: "no-store" });
    return await res.json();
  } catch (error: any) {
    return { ready: false, reason: `Could not reach worker status server: ${error.message}` };
  }
}

export async function GET(req: NextRequest) {
  // A unique room per call guarantees a fresh agent job is dispatched every time.
  // Reusing a fixed room name meant a second call could join a stale room whose
  // agent had already greeted (or was shutting down), producing a silent call.
  //
  // "sakhi-room-", not the old "mystree-room-" prefix (2026-09-02): a
  // separate "mystree-care" worker (this project's own pre-Sakhi ancestor)
  // is running on this same LiveKit Cloud project, and "mystree-room-*"
  // rooms read as ambiguous in shared dashboards/logs between the two. Room
  // uniqueness itself was never the bug - AGENT_NAME below is what actually
  // routes explicit dispatch, see LIVEKIT_AGENT_NAME's comment in .env - this
  // is about not confusing a human reading the room list.
  const room =
    req.nextUrl.searchParams.get("room") ||
    `sakhi-room-${Date.now().toString(36)}-${randomUUID().replace(/-/g, "").slice(0, 12)}`;
  const participant = `clinic-user-${randomUUID().replace(/-/g, "").slice(0, 12)}`;
  const provider = (req.nextUrl.searchParams.get("provider") || "").trim().toLowerCase();
  const voice = (req.nextUrl.searchParams.get("voice") || "").trim();
  const phone = (req.nextUrl.searchParams.get("phone") || "").trim();
  const temperature = (req.nextUrl.searchParams.get("temperature") || "").trim();
  const silenceThreshold = (req.nextUrl.searchParams.get("silenceThreshold") || "").trim();
  const ttsSpeed = (req.nextUrl.searchParams.get("ttsSpeed") || "").trim();
  const sttMinSilence = (req.nextUrl.searchParams.get("sttMinSilence") || "").trim();
  const sttMaxSilence = (req.nextUrl.searchParams.get("sttMaxSilence") || "").trim();
  const sttInterruptionDelay = (req.nextUrl.searchParams.get("sttInterruptionDelay") || "").trim();
  const language = (req.nextUrl.searchParams.get("language") || "").trim();

  const apiKey = process.env.LIVEKIT_API_KEY;
  const apiSecret = process.env.LIVEKIT_API_SECRET;
  const wsUrl = process.env.LIVEKIT_URL;

  if (!apiKey || !apiSecret || !wsUrl) {
    return NextResponse.json(
      { error: "Server misconfigured: missing LiveKit credentials" },
      { status: 500 }
    );
  }

  const status = await fetchWorkerStatus();
  if (!status.ready) {
    return NextResponse.json(
      { error: "Worker not ready yet. Wait a few seconds and start the call again.", worker: status },
      { status: 503 }
    );
  }

  // Dispatch metadata is how the worker's provider_and_voice_from_metadata()
  // picks the TTS provider/voice for this specific call (see agent.py) - the
  // UI's picker only means anything if it actually reaches the worker here.
  const metadataPayload: Record<string, string> = {};
  if (provider) metadataPayload.tts_provider = provider;
  if (voice) metadataPayload.voice_id = voice;
  if (phone) metadataPayload.caller_phone = phone;
  if (temperature) metadataPayload.temperature = temperature;
  if (silenceThreshold) metadataPayload.silence_threshold = silenceThreshold;
  if (ttsSpeed) metadataPayload.tts_speed = ttsSpeed;
  if (sttMinSilence) metadataPayload.stt_min_silence = sttMinSilence;
  if (sttMaxSilence) metadataPayload.stt_max_silence = sttMaxSilence;
  if (sttInterruptionDelay) metadataPayload.stt_interruption_delay = sttInterruptionDelay;
  if (language) metadataPayload.language = language;
  const metadata = Object.keys(metadataPayload).length ? JSON.stringify(metadataPayload) : undefined;

  try {
    let dispatchId: string | null = null;
    if ((process.env.LIVEKIT_EXPLICIT_DISPATCH || "true").toLowerCase() !== "false") {
      const dispatchClient = new AgentDispatchClient(wsUrl.replace(/^wss:/, "https:").replace(/^ws:/, "http:"), apiKey, apiSecret);
      const dispatch = await dispatchClient.createDispatch(room, AGENT_NAME, { metadata });
      dispatchId = dispatch.id;
    }

    const at = new AccessToken(apiKey, apiSecret, { identity: participant, name: participant });
    at.addGrant({
      roomJoin: true,
      room: room,
      canPublish: true,
      canSubscribe: true,
      canPublishData: true,
    });
    const token = await at.toJwt();

    return NextResponse.json({
      token,
      url: wsUrl,
      participant,
      provider: provider || "smallest",
      voice: voice || "default",
      dispatch_id: dispatchId,
      worker: status,
      // page.tsx reads this to open a direct EventSource to the worker's
      // /events SSE stream (status_server.py's _handle_sse) once connected -
      // that effect has always guarded on `!workerUrl` and this field was
      // never actually returned, so the live-event-stream path silently
      // never ran; every session fell back to /api/logs polling with no
      // visible error. Same-origin-reachable address the BROWSER can open
      // directly, not just the Next.js server - matches WORKER_STATUS_URL's
      // existing use for the server-side proxy routes in this same file.
      worker_url: WORKER_STATUS_URL,
    });
  } catch (error: any) {
    return NextResponse.json(
      { error: `Failed to generate token: ${error.message}` },
      { status: 500 }
    );
  }
}
