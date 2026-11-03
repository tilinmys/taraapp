# Sakhi standalone AWS deployment

Provisions Sakhi as an isolated stack in `ap-south-1`: one `t3.medium` EC2
instance (4 GB; standard CPU credits, no burst-surplus billing), Secrets
Manager for config, S3 for deploy artifacts + call recordings, zero SSH (SSM
only). Target baseline: **about $35-40/month** (t3.medium costs roughly twice
a t3.small; the 2 GB t3.small hit the OOM killer at 3-4 concurrent calls).

## What this deploys (and what it deliberately does not)

This was adapted from a generated infra guide whose *application* code was a
generic stub - a `VoiceAssistant`/`openai.VAD` agent that doesn't match the
installed `livekit-agents` API, a from-scratch DB schema, and no clinical
logic at all. **That stub is not used here.** This deploys the real app:

| Component | What actually runs |
|---|---|
| Voice worker | `agent.py` - the real triage engine, 7-stage ANC protocol, Tier-1 danger-sign locking, language latch, Deepgram STT + Vakyam TTS (single-provider, no fallback - see `.env`'s own comments for why), Groq↔OpenRouter hedged LLM fallback |
| Health/logs API | `status_server.py`, started **inside** `agent.py`'s own process on `:8080` (loopback only) - not a second service |
| Dashboard | The real Next.js frontend (`frontend/`) - the ASHA console, Live Form Sync, risk badges, visit-summary card - built and served with `next start`, not a static HTML bundle |
| Database | SQLite at `/opt/sakhi/data/sakhi.sqlite3` (`sakhi_sql_repository.py`'s real, Postgres-portable schema) |

## Order of operations

```bash
cd deploy/aws

# 1. Provision infrastructure (S3, IAM, security group, EC2, EIP).
#    Creates the Secrets Manager secret as an EMPTY schema - no real
#    credential is ever written into this script or its shell history.
./provision_infra.sh

# 2. Push your REAL local API keys (reads .env + the parent .env the exact
#    way agent.py's own load_project_env() does). Never prints a secret.
./push_secrets.sh

# 3. Install system dependencies, systemd units, nginx + Basic Auth.
#    Re-runnable if you need to change a system-level setting later.
./run_setup_instance.sh

# 4. Ship the actual application code and start the services.
./deploy_sakhi.sh
```

Re-running step 4 alone is the normal deploy loop once the instance exists.

## Trust model - why there's no SSH

- Port 22 is never opened on the security group.
- All access (setup, deploys, diagnostics) goes through **AWS Systems
  Manager** (`aws ssm send-command`), authenticated by your own AWS IAM
  session, logged in CloudTrail, and revocable by removing IAM permissions -
  no key file to lose or rotate.
- The instance's IAM role can only: read the one `sakhi/api-keys` secret,
  read/write the recordings bucket, read the deploy bucket. It cannot touch
  anything else in the account.

## Dashboard access

The Basic Auth password is generated randomly by `provision_infra.sh` (or
`push_secrets.sh`, if the secret already existed) and stored in the same
secret under `DASHBOARD_PASSWORD` - **never** hardcoded in a script or
printed to a log you'd keep. Retrieve it with:

```bash
aws secretsmanager get-secret-value --secret-id sakhi/api-keys \
  --query SecretString --output text | grep DASHBOARD_PASSWORD
```

## Verification checklist

```bash
INSTANCE_ID=$(cat /tmp/sakhi-instance-id.txt)
PUBLIC_IP=$(aws ec2 describe-instances --instance-ids "$INSTANCE_ID" \
  --query "Reservations[0].Instances[0].PublicIpAddress" --output text)
DASH_PASS=$(aws secretsmanager get-secret-value --secret-id sakhi/api-keys \
  --query SecretString --output text | grep DASHBOARD_PASSWORD | cut -d= -f2-)
```

**1. Both real services are up (via SSM, zero SSH):**
```bash
aws ssm send-command --instance-ids "$INSTANCE_ID" \
  --document-name AWS-RunShellScript \
  --parameters 'commands=["systemctl status sakhi-agent.service sakhi-frontend.service --no-pager"]' \
  --output text
```

**2. Standard credit mode is actually in effect (no burst-surplus risk):**
```bash
aws ec2 describe-instance-credit-specifications --instance-ids "$INSTANCE_ID" \
  --query "InstanceCreditSpecifications[0].CpuCredits" --output text
# Expected: standard
```

**3. Worker health, from inside the instance (the status API is loopback-only
   by design - not reachable from the public IP, matching how the frontend
   itself reaches it):**
```bash
aws ssm send-command --instance-ids "$INSTANCE_ID" \
  --document-name AWS-RunShellScript \
  --parameters 'commands=["curl -s http://127.0.0.1:8080/health"]' --output text
```

**4. Dashboard reachable and Basic Auth actually enforced:**
```bash
curl -I "http://${PUBLIC_IP}/"                              # expect 401
curl -I -u "sakhi:${DASH_PASS}" "http://${PUBLIC_IP}/"      # expect 200
```

**5. LiveKit dispatch actually reaches this worker** (confirms the
`LIVEKIT_AGENT_NAME=sakhi-maternal-care` isolation from any other worker on
the same LiveKit project actually took effect):
```bash
aws ssm send-command --instance-ids "$INSTANCE_ID" \
  --document-name AWS-RunShellScript \
  --parameters 'commands=["journalctl -u sakhi-agent.service --no-pager | grep \"registered worker\""]' \
  --output text
```
Look for `"agent_name": "sakhi-maternal-care"` in the output.

## Known gaps, stated rather than silently left

- **Call recordings (2026-09-02, now wired).** `agent.py` starts a
  room-composite (audio-only, OGG) LiveKit Egress right after connecting to
  each call's room, uploaded straight to `sakhi-call-recordings-<account>/
  recordings/<room>.ogg`, and stops it at call teardown. Uses its own IAM
  user (`sakhi-egress-uploader`, `s3:PutObject`/`PutObjectAcl` on
  `recordings/*` only) because egress runs on LiveKit Cloud's own
  infrastructure, not this EC2 instance - it cannot see `sakhi-instance-
  role` at all. The `/reports` dashboard confirms each recording actually
  landed with a real S3 `HeadObject` (not just that egress was asked to
  start it) and serves a 1-hour presigned URL once it has - see the
  `recording_status`/`recording_key` columns on `call_reports` and
  `status_server.py`'s `_enrich_recording_status()`. A recording failure
  never blocks or ends the call itself (best-effort, same as every other
  non-clinical side effect in this codebase).
- **HTTPS (2026-09-03, now live) - and why it was not cosmetic.** The
  dashboard is now served at `https://3-109-104-193.sslip.io/` with a real,
  browser-trusted Let's Encrypt cert (certbot's nginx plugin, auto-renewing).
  `sslip.io` resolves that hostname straight back to the Elastic IP with zero
  DNS setup - no domain purchase needed. This was NOT a nice-to-have: Let's
  Encrypt refuses to issue for `*.compute.amazonaws.com` (blocked by their
  own anti-abuse policy, confirmed live), so the instance's own AWS-assigned
  public DNS name was a dead end and had to be worked around. More
  importantly, plain HTTP over a bare IP is not a secure context, so
  browsers refuse to expose `navigator.mediaDevices` at all -
  `getUserMedia` was `undefined`, meaning the microphone could never be
  captured over the old `http://<ip>/` URL, full stop, regardless of any
  frontend UI. **The plain-HTTP URL is retired - use the `https://...
  sslip.io` one.**
- **Single instance, no auto-recovery beyond systemd.** `Restart=always`
  handles a crashed process; it does not handle the instance itself going
  down. Acceptable for a $15-20/month pilot; revisit before a district-scale
  rollout the way `sakhi_schema.py`'s own Postgres-portability notes already
  anticipate.
