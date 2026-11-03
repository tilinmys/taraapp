#!/usr/bin/env bash
# Runs ON the EC2 instance via SSM (see run_setup_instance.sh for the wrapper
# that dispatches this). Installs the REAL Sakhi runtime - not the generic
# stub app.py/ui_server.py from the original guide this was adapted from.
#
# What's actually deployed here, and why it differs from that guide:
#
#   - The Python worker IS agent.py (this repo's real, ~6000-line
#     implementation: triage engine, guardrails, ANC protocol, language
#     latch, Sarvam TTS + Gnani STT (Deepgram fallback) + OpenAI-primary/
#     Groq/OpenRouter LLM fallback - see sakhi/api-keys in Secrets Manager
#     for the exact active pairing, not this comment, which only describes
#     shape) - not a stub using livekit.agents.voice_assistant.
#     VoiceAssistant, which does not exist in the installed livekit-agents.
#   - status_server.py is NOT a separate service. agent.py's own __main__
#     block already starts it internally (ENABLE_STATUS_SERVER=true) on
#     port 8080 - adding a second FastAPI service for the same job would be
#     two processes doing one thing.
#   - The dashboard IS the real Next.js frontend (frontend/), not a static
#     HTML/JS bundle against a from-scratch call_recordings schema. It
#     already implements the ASHA console, Live Form Sync, risk badges,
#     and the visit-summary card against the real database schema.
#   - Basic Auth password is READ from Secrets Manager (DASHBOARD_USER/
#     DASHBOARD_PASSWORD, already hydrated into /etc/sakhi.env) and hashed
#     into .htpasswd HERE, at setup time - never a precomputed hash
#     hardcoded into this script.
set -euo pipefail

echo "=== Initializing Sakhi environment on instance ==="

# --- 1. System packages ------------------------------------------------------
apt-get update
# awscli is deliberately not in this list: Ubuntu 24.04/Noble dropped the
# classic apt package, and run_setup_instance.sh's SSM command already
# installs the official AWS CLI v2 zip before this script even runs (it
# needs `aws s3 cp` to fetch this very file) - confirmed live, this line
# used to fail outright with "Package 'awscli' has no installation
# candidate" and take python3-venv/nginx/jq down with it in the same
# apt-get invocation.
apt-get install -y python3-pip python3-venv nginx apache2-utils jq curl git

# Node 20 LTS via NodeSource - Ubuntu 24.04's own repo Node is older than
# what this Next.js version wants.
if ! command -v node >/dev/null || [ "$(node -v | cut -d. -f1 | tr -d v)" -lt 20 ]; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y nodejs
fi

id -u sakhi &>/dev/null || useradd -r -s /bin/false -d /opt/sakhi sakhi

mkdir -p /opt/sakhi/data /opt/sakhi/logs

# t3.medium is 4GB RAM (older stacks: t3.small, 2GB). `next build` plus live
# Python call workers plus nginx can spike past that during a build - a swapfile is cheap insurance against an
# OOM-killed deploy rather than a performance feature this app needs day to
# day.
if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# --- 2. Python virtual environment -------------------------------------------
if [ ! -d "/opt/sakhi/venv" ]; then
  python3 -m venv /opt/sakhi/venv
fi
/opt/sakhi/venv/bin/pip install --upgrade pip
/opt/sakhi/venv/bin/pip install -r /opt/sakhi/requirements.txt

# --- 3. sakhi-hydrate-env.service: pull config from Secrets Manager ---------
cat << 'EOF' > /etc/systemd/system/sakhi-hydrate-env.service
[Unit]
Description=Hydrate Sakhi environment variables from Secrets Manager
DefaultDependencies=no
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=root
# Region is hardcoded, not read from instance metadata: confirmed live that
# an unauthenticated curl to the IMDS region endpoint returns a 401 (this
# instance's HttpTokens=required, i.e. IMDSv2-only) but curl still exits 0
# without -f, so the `|| echo "ap-south-1"` fallback never fired and REGION
# came back empty, breaking `--region $REGION` ("expected one argument").
# Every other script in this deploy stack already hardcodes ap-south-1, so
# matching that here instead of adding an IMDSv2 token round-trip.
ExecStart=/bin/bash -c '\
  REGION="ap-south-1"; \
  RAW_SECRET=$(aws secretsmanager get-secret-value --secret-id sakhi/api-keys --region $REGION --query SecretString --output text); \
  echo "$RAW_SECRET" > /etc/sakhi.env; \
  chown sakhi:sakhi /etc/sakhi.env; \
  chmod 600 /etc/sakhi.env; \
  DASH_USER=$(grep -E "^DASHBOARD_USER=" /etc/sakhi.env | cut -d= -f2-); \
  DASH_PASS=$(grep -E "^DASHBOARD_PASSWORD=" /etc/sakhi.env | cut -d= -f2-); \
  if [ -n "$DASH_USER" ] && [ -n "$DASH_PASS" ]; then \
    htpasswd -b -c /etc/nginx/.htpasswd "$DASH_USER" "$DASH_PASS"; \
  fi; \
  echo "Sakhi environment hydrated."'
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

# --- 4. sakhi-agent.service: the real LiveKit worker (also boots the ------
#         status/health API on :8080 internally - see agent.py's __main__)
cat << 'EOF' > /etc/systemd/system/sakhi-agent.service
[Unit]
Description=Sakhi LiveKit voice agent worker (real agent.py)
After=network.target sakhi-hydrate-env.service
Wants=sakhi-hydrate-env.service

[Service]
Type=simple
User=sakhi
Group=sakhi
WorkingDirectory=/opt/sakhi
EnvironmentFile=/etc/sakhi.env
ExecStart=/opt/sakhi/venv/bin/python3 /opt/sakhi/agent.py start
Restart=always
RestartSec=5s
# The worker keeps clinical readings and risk state in memory for the
# duration of a call - give it real time to finish an in-flight ANC
# screening rather than being SIGKILLed mid-call on a deploy/restart.
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOF

# --- 5. sakhi-frontend.service: the real Next.js ASHA console --------------
cat << 'EOF' > /etc/systemd/system/sakhi-frontend.service
[Unit]
Description=Sakhi ASHA console (Next.js)
After=network.target sakhi-hydrate-env.service sakhi-agent.service
Wants=sakhi-hydrate-env.service

[Service]
Type=simple
User=sakhi
Group=sakhi
WorkingDirectory=/opt/sakhi/frontend
EnvironmentFile=/etc/sakhi.env
Environment=NODE_ENV=production
# PORT is set inline on ExecStart, not via Environment=, and deliberately
# NOT relied on to override EnvironmentFile's PORT=8080 (that value is
# status_server.py's, loaded by the same shared /etc/sakhi.env via
# sakhi-agent.service's own EnvironmentFile= line) - confirmed live that a
# later Environment=PORT=3000 here did NOT win over the earlier
# EnvironmentFile's PORT=8080 (systemd's EnvironmentFile/Environment
# precedence did not behave as textual-order docs suggest in practice), so
# Next.js silently bound 8080 instead of 3000 and starved the real status
# server of its port ("Address already in use"). Binding PORT here, at the
# exec boundary, is unambiguous regardless of that ordering question.
ExecStart=/bin/bash -c 'PORT=3000 exec /usr/bin/npm start'
Restart=always
RestartSec=5s

[Install]
WantedBy=multi-user.target
EOF

# --- 6. nginx: reverse proxy + Basic Auth -----------------------------------
cat << 'EOF' > /etc/nginx/sites-available/sakhi
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;

    client_max_body_size 16M;

    # The Next.js app (ASHA console + its own /api/token, /api/logs, etc.
    # proxy routes to the worker's status server on :8080 - see
    # frontend/src/app/api/*/route.ts). Nothing else needs to be exposed on
    # this security group; :8080 stays loopback-only.
    location / {
        auth_basic "Sakhi ASHA Portal";
        auth_basic_user_file /etc/nginx/.htpasswd;
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Next.js dev/HMR websocket path isn't used in production (`npm start`
    # serves a built app, no HMR socket) - no separate /ws/ block is needed
    # the way the original guide's FastAPI dashboard required one.
}
EOF

# The HTTP-only block above is only a BOOTSTRAP (it is what lets certbot's
# HTTP challenge run on a brand-new instance). Once a TLS certificate exists,
# the repo's deploy/aws/sakhi.nginx.conf (HTTPS, auth-gated /status/) is the
# real config. Before 2026-09-24 that file was installed by hand and nothing
# here knew about it, so re-running this script silently replaced the live
# HTTPS config with HTTP-only - and browsers refuse the microphone over
# plain HTTP, which would have made every field tablet mute.
if [ -f /tmp/sakhi.nginx.conf ]; then
  CERT_PATH=$(grep -oP 'ssl_certificate\s+\K[^;]+' /tmp/sakhi.nginx.conf | head -1)
  if [ -n "${CERT_PATH}" ] && [ -f "${CERT_PATH}" ]; then
    cp /tmp/sakhi.nginx.conf /etc/nginx/sites-available/sakhi
    echo "Installed HTTPS nginx config (certificate present: ${CERT_PATH})."
  else
    echo "WARNING: TLS certificate ${CERT_PATH:-<none>} not found - keeping HTTP bootstrap config. Run certbot, then re-run this script."
  fi
fi

ln -sf /etc/nginx/sites-available/sakhi /etc/nginx/sites-enabled/default
nginx -t

# --- 7. Enable services (hydrate first, htpasswd depends on it) ------------
systemctl daemon-reload
systemctl enable sakhi-hydrate-env.service
# `restart`, not `start` (confirmed live, 2026-09-09): sakhi-hydrate-env is
# Type=oneshot with RemainAfterExit=yes, so once it has run once (e.g. from
# an earlier provisioning of this same instance), systemd considers it
# already "active" and `systemctl start` on an already-active unit is a
# documented no-op - it does NOT re-run ExecStart. That silently meant a
# later push_secrets.sh update never reached /etc/sakhi.env on a re-run of
# this script against an existing instance: the secret in Secrets Manager
# was correct, but the file this service writes was not refreshed, and the
# app kept running whatever was hydrated the first time this instance was
# ever set up. `restart` forces ExecStart to run again regardless of the
# unit's current state.
systemctl restart sakhi-hydrate-env.service
systemctl reload nginx
systemctl enable sakhi-agent.service
systemctl enable sakhi-frontend.service

chown -R sakhi:sakhi /opt/sakhi

echo "Sakhi instance setup complete. Run deploy_sakhi.sh next to ship the app code and start the services."
