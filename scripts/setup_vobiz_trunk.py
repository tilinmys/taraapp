"""One-shot Vobiz <-> LiveKit SIP wiring.

Run this ONCE (or whenever your Vobiz trunk credentials change) to create the
LiveKit-side SIP trunks and dispatch rule that let a real Vobiz phone number
land in GracyAgent exactly like a browser call does today.

This does NOT touch agent.py's call logic - it only creates infrastructure
objects in your LiveKit project (outbound trunk = LiveKit's leg to Vobiz,
inbound trunk = accepts Vobiz's incoming SIP, dispatch rule = routes an
inbound call into a room and dispatches your worker's agent).

Usage:
    python scripts/setup_vobiz_trunk.py

Required env vars (see .env.example):
    LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET   (already set for the worker)
    VOBIZ_SIP_DOMAIN     e.g. yourname.sip.vobiz.ai
    VOBIZ_SIP_USERNAME
    VOBIZ_SIP_PASSWORD
    VOBIZ_PHONE_NUMBER   e.g. +9180XXXXXXX (your Vobiz DID)
    LIVEKIT_AGENT_NAME   defaults to "sakhi-maternal-care" (must match
                          agent.py's WorkerOptions(agent_name=...) or the
                          call rings but no worker ever answers it - NOT
                          "mystree-care", the separate pre-Sakhi worker
                          running on this same LiveKit Cloud project)

After this script prints the inbound trunk's SIP URI, paste it into the
Vobiz console (or via Vobiz's PATCH /trunks/{id} API) as that trunk's
inbound_destination - Vobiz docs: https://docs.vobiz.ai/integrations/livekit
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from livekit import api

# Same env-file search order as agent.py's load_project_env() - LIVEKIT_URL/
# API_KEY/SECRET live in frontend/.env.local in this repo, not a root .env.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
for _candidate in (
    _PROJECT_ROOT / ".env",
    _PROJECT_ROOT.parent / ".env",
    _PROJECT_ROOT / "frontend" / ".env.local",
):
    if _candidate.exists():
        load_dotenv(_candidate, override=False)


def _require(name: str) -> str:
    val = os.getenv(name)
    if not val:
        print(f"Missing required env var: {name}", file=sys.stderr)
        sys.exit(1)
    return val


async def main() -> None:
    livekit_url = _require("LIVEKIT_URL")
    api_key = _require("LIVEKIT_API_KEY")
    api_secret = _require("LIVEKIT_API_SECRET")
    vobiz_domain = _require("VOBIZ_SIP_DOMAIN")
    vobiz_username = _require("VOBIZ_SIP_USERNAME")
    vobiz_password = _require("VOBIZ_SIP_PASSWORD")
    vobiz_number = _require("VOBIZ_PHONE_NUMBER")
    agent_name = os.getenv("LIVEKIT_AGENT_NAME", "sakhi-maternal-care")

    lk = api.LiveKitAPI(livekit_url, api_key, api_secret)
    try:
        # Outbound trunk: LiveKit's leg TO Vobiz (needed for the SIP handshake
        # even though we only care about inbound calls here).
        outbound = await lk.sip.create_sip_outbound_trunk(
            api.CreateSIPOutboundTrunkRequest(
                trunk=api.SIPOutboundTrunkInfo(
                    name="Vobiz Outbound",
                    address=vobiz_domain,
                    auth_username=vobiz_username,
                    auth_password=vobiz_password,
                    numbers=[vobiz_number],
                )
            )
        )
        print(f"Created outbound trunk: {outbound.sip_trunk_id}")

        # Inbound trunk: accepts calls Vobiz forwards to LiveKit for this number.
        inbound = await lk.sip.create_sip_inbound_trunk(
            api.CreateSIPInboundTrunkRequest(
                trunk=api.SIPInboundTrunkInfo(
                    name="Vobiz Inbound",
                    numbers=[vobiz_number],
                    allowed_addresses=["0.0.0.0/0"],
                )
            )
        )
        print(f"Created inbound trunk: {inbound.sip_trunk_id}")

        # Dispatch rule: inbound call -> new room "call-<random>" -> dispatch
        # this worker's agent, same as the frontend's explicit dispatch today.
        rule = await lk.sip.create_sip_dispatch_rule(
            api.CreateSIPDispatchRuleRequest(
                dispatch_rule=api.SIPDispatchRuleInfo(
                    rule=api.SIPDispatchRule(
                        dispatch_rule_individual=api.SIPDispatchRuleIndividual(
                            room_prefix="call-"
                        )
                    ),
                    trunk_ids=[inbound.sip_trunk_id],
                    name="Vobiz Inbound Dispatch",
                    room_config=api.RoomConfiguration(
                        agents=[api.RoomAgentDispatch(agent_name=agent_name)]
                    ),
                ),
            )
        )
        print(f"Created dispatch rule: {rule.sip_dispatch_rule_id}")

        print()
        print("=" * 70)
        print("Trunks and dispatch rule created. Last step - find your project's")
        print("SIP URI (NOT derived from LIVEKIT_URL - it's project-ID-based):")
        print("  LiveKit Cloud dashboard -> Settings -> Project -> 'SIP URI'")
        print("  or: lk project list --json   (strip the 'p_' prefix from")
        print("  ProjectId, URI is '<that>.sip.livekit.cloud')")
        print()
        print("Paste that URI into Vobiz as the trunk's inbound destination")
        print("(Vobiz console -> your trunk -> Inbound Routing).")
        print()
        print("A call to your Vobiz number will then land in GracyAgent.")
        print("=" * 70)
    finally:
        await lk.aclose()


if __name__ == "__main__":
    asyncio.run(main())
