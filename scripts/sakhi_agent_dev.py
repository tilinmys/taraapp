"""Run Live Sakhi's voice agent (sakhi_app_agent.py) on this PC.

    python scripts/sakhi_agent_dev.py

The app's live check-in talks to THIS agent: a small voice executor (speak
one line, listen for one answer) driven by the app's own question engine. It
is NOT agent.py (the phone-call agent, where an LLM writes every sentence).

Registered as "sakhi-app-dev", never as the production name, so a laptop can
never take calls meant for a production worker on the same LiveKit project.
Runs in "start" mode: "dev" mode restarts on every file change in the repo
and LiveKit then held dispatches ~30 s. The worker's HTTP server is on 8790
(its default, 8081, is Metro's); the gateway reads http://localhost:8790/ to
tell the app whether Sakhi is up.

Needs in .env: LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET. Speech goes
through the Sakhi gateway (scripts/sakhi_gateway_dev.py must be running).
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env(path: Path) -> None:
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = re.split(r"\s+#", value, maxsplit=1)[0].strip().strip('"').strip("'")
        if key.strip() and value:
            os.environ.setdefault(key.strip(), value)


if __name__ == "__main__":
    os.environ.setdefault("SAKHI_LIVE_AGENT_NAME", "sakhi-app-dev")
    os.environ.setdefault("SAKHI_AGENT_HTTP_PORT", "8790")
    os.environ.setdefault("SAKHI_GATEWAY_INTERNAL", "http://localhost:8787/api/sakhi")
    load_env(ROOT / ".env")
    # The gateway refuses requests without its token: the same one it uses.
    from dev_token import ensure_dev_token

    ensure_dev_token()
    print(f"  Live Sakhi voice agent as '{os.environ['SAKHI_LIVE_AGENT_NAME']}', HTTP :{os.environ['SAKHI_AGENT_HTTP_PORT']}, speech via {os.environ['SAKHI_GATEWAY_INTERNAL']}")
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    sys.argv = ["sakhi_app_agent.py", *(sys.argv[1:] or ["start"])]
    # Imported as a module, not run as a script: each check-in runs in its
    # own process (spawned on Windows), which must be able to import the
    # entrypoint again by name (sakhi_app_agent.entrypoint).
    import sakhi_app_agent

    sakhi_app_agent.main()
