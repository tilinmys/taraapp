"""The Sakhi gateway's bearer token on a development PC.

The gateway refuses every speech request without SAKHI_GATEWAY_TOKEN (it
fails closed, see sakhi_gateway.authorized). For development, one random
token is made once and kept in .sakhi-dev-token at the repo root
(gitignored), so the gateway, the voice agent, the app (through
app/.env.local) and the test scripts all agree on it without anyone typing
a secret. A token set in .env or the environment always wins.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOKEN_FILE = ROOT / ".sakhi-dev-token"


def ensure_dev_token() -> str:
    """SAKHI_GATEWAY_TOKEN from the environment, else this PC's dev token
    (made on first use). Also sets it in os.environ for child processes."""
    token = os.environ.get("SAKHI_GATEWAY_TOKEN", "").strip()
    if not token:
        try:
            token = TOKEN_FILE.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            token = ""
        if not token:
            token = secrets.token_urlsafe(32)
            TOKEN_FILE.write_text(token + "\n", encoding="utf-8")
        os.environ["SAKHI_GATEWAY_TOKEN"] = token
    return token
