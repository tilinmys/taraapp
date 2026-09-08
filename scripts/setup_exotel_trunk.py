"""One-shot Exotel <-> LiveKit SIP wiring.

Sibling to scripts/setup_vobiz_trunk.py - same end result (a real phone
number lands in GracyAgent exactly like a browser test call), different
provider-side API. Exotel's SIP trunking is REST-configured (no console
click-through like Vobiz), and auth is IP-based rather than
username/password, so this script does more work than the Vobiz one: it
creates the Exotel trunk, maps your DID to it, and points its destination
URI at LiveKit - THEN creates the LiveKit-side inbound trunk + dispatch rule.

Usage:
    python scripts/setup_exotel_trunk.py --sip-uri yourproject.sip.livekit.cloud

The SIP URI is project-ID-based, NOT derived from LIVEKIT_URL - find it on
the LiveKit Cloud dashboard: Settings -> Project -> "SIP URI". Passed as a
flag (not a prompt) so this script can run non-interactively.

Required env vars (see .env.example):
    LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET   (already set for the worker)
    EXOTEL_API_KEY, EXOTEL_API_TOKEN, EXOTEL_ACCOUNT_SID, EXOTEL_SUBDOMAIN
    EXOTEL_PHONE_NUMBER   your Exotel DID, e.g. +9180XXXXXXXX
    LIVEKIT_AGENT_NAME    defaults to "sakhi-maternal-care" (must match
                           agent.py's WorkerOptions(agent_name=...) or the
                           call rings but no worker ever answers it - NOT
                           "mystree-care", the separate pre-Sakhi worker
                           running on this same LiveKit Cloud project)

Exotel SIP trunking must be enabled on your account first (email
hello@exotel.com + KYC) before any of these API calls will succeed - this
script does not do that part. See:
https://support.exotel.com/support/solutions/articles/3000133452
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import requests
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


def _exotel_request(method: str, path: str, auth: tuple[str, str], subdomain: str, **kwargs) -> dict:
    url = f"https://{subdomain}{path}"
    resp = requests.request(method, url, auth=auth, timeout=15, **kwargs)
    if not resp.ok:
        print(f"Exotel API error {resp.status_code} on {method} {path}:", file=sys.stderr)
        print(resp.text, file=sys.stderr)
        sys.exit(1)
    return resp.json() if resp.content else {}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sip-uri", required=True,
        help="LiveKit project SIP URI, e.g. yourproject.sip.livekit.cloud "
             "(dashboard -> Settings -> Project -> SIP URI)",
    )
    args = parser.parse_args()
    livekit_sip_uri = args.sip_uri.strip()

    livekit_url = _require("LIVEKIT_URL")
    lk_api_key = _require("LIVEKIT_API_KEY")
    lk_api_secret = _require("LIVEKIT_API_SECRET")
    exotel_api_key = _require("EXOTEL_API_KEY")
    exotel_api_token = _require("EXOTEL_API_TOKEN")
    exotel_sid = _require("EXOTEL_ACCOUNT_SID")
    exotel_subdomain = _require("EXOTEL_SUBDOMAIN")
    exotel_number = _require("EXOTEL_PHONE_NUMBER")
    agent_name = os.getenv("LIVEKIT_AGENT_NAME", "sakhi-maternal-care")

    auth = (exotel_api_key, exotel_api_token)
    base = f"/v2/accounts/{exotel_sid}"

    lk = api.LiveKitAPI(livekit_url, lk_api_key, lk_api_secret)
    try:
        # --- LiveKit side first: need the inbound trunk + dispatch rule so
        # we know the SIP URI to hand to Exotel in step 3 below.
        inbound = await lk.sip.create_sip_inbound_trunk(
            api.CreateSIPInboundTrunkRequest(
                trunk=api.SIPInboundTrunkInfo(
                    name="Exotel Inbound",
                    numbers=[exotel_number],
                    allowed_addresses=["0.0.0.0/0"],
                )
            )
        )
        print(f"Created LiveKit inbound trunk: {inbound.sip_trunk_id}")

        rule = await lk.sip.create_sip_dispatch_rule(
            api.CreateSIPDispatchRuleRequest(
                dispatch_rule=api.SIPDispatchRuleInfo(
                    rule=api.SIPDispatchRule(
                        dispatch_rule_individual=api.SIPDispatchRuleIndividual(
                            room_prefix="call-"
                        )
                    ),
                    trunk_ids=[inbound.sip_trunk_id],
                    name="Exotel Inbound Dispatch",
                    room_config=api.RoomConfiguration(
                        agents=[api.RoomAgentDispatch(agent_name=agent_name)]
                    ),
                ),
            )
        )
        print(f"Created LiveKit dispatch rule: {rule.sip_dispatch_rule_id}")
    finally:
        await lk.aclose()

    # --- Exotel side: create trunk, map the DID, point destination at LiveKit.
    trunk = _exotel_request(
        "POST", f"{base}/trunks", auth, exotel_subdomain,
        json={
            "trunk_name": "livekit-trunk",
            "nso_code": "any_any",
            "domain_name": f"{exotel_sid}.pstn.exotel.com",
        },
    )
    trunk_sid = trunk.get("response", [{}])[0].get("data", {}).get("sid") or trunk.get("sid")
    print(f"Created Exotel trunk: {trunk_sid}")

    _exotel_request(
        "POST", f"{base}/trunks/{trunk_sid}/phone_numbers", auth, exotel_subdomain,
        json={"phone_number": exotel_number},
    )
    print(f"Mapped {exotel_number} to the trunk")

    _exotel_request(
        "POST", f"{base}/trunks/{trunk_sid}/destination_uris", auth, exotel_subdomain,
        json={"destinations": [{"destination": f"{livekit_sip_uri}:5061;transport=tls"}]},
    )
    print(f"Set destination URI to {livekit_sip_uri}:5061;transport=tls")

    print()
    print("=" * 70)
    print("Done. Last manual step - in Exotel's App Bazaar Flow builder,")
    print(f"add a Connect Applet with Dial Whom = sip:{trunk_sid}")
    print("routing your inbound number to that flow.")
    print()
    print("A call to your Exotel number will then land in GracyAgent.")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
