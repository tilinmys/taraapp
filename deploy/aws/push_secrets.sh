#!/usr/bin/env bash
# Pushes your REAL local API keys into AWS Secrets Manager's sakhi/api-keys
# secret. Run this from the repo root on your own workstation, never on the
# EC2 instance and never with its output piped anywhere logged.
#
# Deliberately separate from provision_infra.sh: that script only writes a
# blank schema, so a `git diff`/screen-share of this repo's deploy scripts
# never has to worry about a real credential having been typed into a shell
# history or a script file. This script reads your two real .env files the
# exact same way agent.py's own load_project_env() does - project .env
# first, parent .env filling only what's missing - so what gets pushed is
# exactly what's running locally right now, not a hand-retyped copy that can
# drift from it.
set -euo pipefail

# Local .deploy-state dir, not mktemp's /tmp: aws.exe on this workstation is
# a native Windows binary and does not resolve a file:// URI built from an
# MSYS /tmp path (see the matching fix and comment in provision_infra.sh -
# same bug, confirmed live on that script's first run). Captured from $0
# before the cd below so it still points at deploy/aws/.deploy-state after.
STATE_DIR="deploy/aws/.deploy-state"

cd "$(dirname "$0")/../.."   # repo root
mkdir -p "${STATE_DIR}"
PARENT_ENV="../.env"          # mirrors load_project_env()'s second candidate

AWS_REGION="ap-south-1"
TMP_MERGED="${STATE_DIR}/sakhi-secrets-merged.env"
trap 'rm -f "${TMP_MERGED}"' EXIT

# --- Merge project .env (wins) with parent .env (fills gaps only) ----------
# python3 on the deploy target's shell, python on this workstation's Windows
# Git Bash. Existence alone isn't enough to pick between them here: this
# workstation's PATH has a `python3` that resolves to the Windows Store's
# broken execution-alias stub (prints "Python was not found..." and exits
# nonzero instead of running), while the real interpreter is only reachable
# as `python` (3.10) - confirmed live. So each candidate is actually
# invoked with --version and the first one that works wins, rather than
# trusting `command -v` existence order.
PY=""
for candidate in python3 python; do
  if p=$(command -v "$candidate" 2>/dev/null) && "$p" --version >/dev/null 2>&1; then
    PY="$p"
    break
  fi
done
if [ -z "$PY" ]; then
  echo "Error: no working Python interpreter found (tried python3, python)." >&2
  exit 1
fi
"$PY" - "$TMP_MERGED" "$PARENT_ENV" <<'PYEOF'
import sys, re

out_path, parent_path = sys.argv[1], sys.argv[2]

def load(path):
    vals = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return vals

project = load(".env")
parent = load(parent_path)
merged = dict(parent)
merged.update(project)   # project .env wins, matching load_dotenv(override=False) load order

# Only the keys the deployed instance actually needs - see
# provision_infra.sh's secret schema for the full list and rationale.
KEEP = [
    "LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET",
    "DEEPGRAM_API_KEY", "VAKYAM_API_KEY", "GNANI_API_KEY", "SARVAM_API_KEY",
    "GROQ_API_KEYS", "GROQ_API_KEY", "GROQ_API_KEY_1", "GROQ_API_KEY_2",
    "OPENROUTER_API_KEY", "OPENROUTER_BASE_URL",
    "OPENROUTER_LLM_MODEL", "OPENROUTER_BACKUP_MODEL", "GROQ_LLM_MODEL",
]
with open(out_path, "w", encoding="utf-8") as f:
    # Fixed production overrides, always written regardless of local .env -
    # these are pilot decisions (single-provider, no fallback for STT/TTS;
    # this worker's own distinct agent name), not values to inherit from
    # whatever a local dev session happened to have set.
    f.write("LIVEKIT_AGENT_NAME=sakhi-maternal-care\n")
    f.write("LIVEKIT_EXPLICIT_DISPATCH=true\n")
    f.write("STT_PRIMARY=deepgram\n")
    f.write("VOICE_STT_FALLBACK_ENABLED=false\n")
    f.write("TTS_PROVIDER=vakyam\n")
    f.write("VOICE_TTS_FALLBACK_ENABLED=false\n")
    f.write("SAKHI_AVATAR=none\n")
    # bvc, not the SIP-trunk "telephony" default: agent.py's own
    # _noise_filter() docstring is explicit that BVCTelephony expects
    # narrowband PSTN/SIP audio and BVC expects wideband WebRTC - this pilot
    # deployment is reached ONLY through the browser ASHA console (no SIP
    # trunk wired to it, per this file's own README), so "telephony" here
    # was applying the wrong noise-cancellation model to every single call,
    # degrading the mic audio badly enough that STT/VAD never picked up
    # speech at all. Confirmed live (2026-09-03): every real test call
    # through the deployed dashboard ended "without a completed caller
    # transcript" until this was fixed. Revisit if/when a real SIP trunk is
    # wired to this same worker.
    f.write("NOISE_CANCELLATION=bvc\n")
    f.write("ENABLE_STATUS_SERVER=true\n")
    f.write("PORT=8080\n")
    f.write("WORKER_STATUS_URL=http://127.0.0.1:8080\n")
    f.write("SAKHI_DB_PATH=/opt/sakhi/data/sakhi.sqlite3\n")
    f.write("PIPELINE_LOG_PATH=/opt/sakhi/logs/pipeline_events.jsonl\n")
    f.write("RECORDING_ENABLED=true\n")
    f.write("RECORDING_S3_BUCKET=sakhi-call-recordings-147435199438\n")
    f.write("RECORDING_S3_REGION=ap-south-1\n")
    for k in KEEP:
        if k in merged and merged[k]:
            f.write(f"{k}={merged[k]}\n")

missing = [k for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET",
                        "DEEPGRAM_API_KEY", "VAKYAM_API_KEY") if k not in merged or not merged.get(k)]
if missing:
    print(f"WARNING: these required keys were not found locally: {', '.join(missing)}", file=sys.stderr)
PYEOF

# --- Preserve the dashboard password already in the secret (do not clobber) -
EXISTING_DASH=$(aws secretsmanager get-secret-value --secret-id sakhi/api-keys \
  --region "${AWS_REGION}" --query SecretString --output text 2>/dev/null \
  | grep -E "^DASHBOARD_(USER|PASSWORD)=" || true)
if [ -n "${EXISTING_DASH}" ]; then
  echo "${EXISTING_DASH}" >> "${TMP_MERGED}"
else
  echo "DASHBOARD_USER=sakhi" >> "${TMP_MERGED}"
  DASHBOARD_PASSWORD=$(openssl rand -base64 18 | tr -d '=+/' | cut -c1-20)
  echo "DASHBOARD_PASSWORD=${DASHBOARD_PASSWORD}" >> "${TMP_MERGED}"
  echo ">>> No existing dashboard password found - generated a new one: ${DASHBOARD_PASSWORD}"
fi

# --- Preserve (or mint) the egress-recording S3 credentials, same pattern -
# these belong to the sakhi-egress-uploader IAM user (s3:PutObject/
# PutObjectAcl on recordings/* in the recordings bucket only - see
# README.md), used by LiveKit Cloud's own egress workers to write the call
# recording, since egress runs on LiveKit's infra and can't see this
# instance's IAM role at all.
EXISTING_EGRESS=$(aws secretsmanager get-secret-value --secret-id sakhi/api-keys \
  --region "${AWS_REGION}" --query SecretString --output text 2>/dev/null \
  | grep -E "^RECORDING_AWS_(ACCESS_KEY_ID|SECRET_ACCESS_KEY)=" || true)
if [ -n "${EXISTING_EGRESS}" ]; then
  echo "${EXISTING_EGRESS}" >> "${TMP_MERGED}"
elif [ -f "${STATE_DIR}/egress-access-key.json" ]; then
  "$PY" - "${STATE_DIR}/egress-access-key.json" >> "${TMP_MERGED}" <<'PYEOF'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
ak = d["AccessKey"]
print(f"RECORDING_AWS_ACCESS_KEY_ID={ak['AccessKeyId']}")
print(f"RECORDING_AWS_SECRET_ACCESS_KEY={ak['SecretAccessKey']}")
PYEOF
  echo ">>> Using the sakhi-egress-uploader access key created earlier this session (.deploy-state/egress-access-key.json)."
else
  echo ">>> No existing egress-recording credential found and no local .deploy-state/egress-access-key.json -"
  echo ">>> minting a new access key for the sakhi-egress-uploader IAM user (must already exist)."
  aws iam create-access-key --user-name sakhi-egress-uploader --region "${AWS_REGION}" --output json > "${STATE_DIR}/egress-access-key.json"
  "$PY" - "${STATE_DIR}/egress-access-key.json" >> "${TMP_MERGED}" <<'PYEOF'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
ak = d["AccessKey"]
print(f"RECORDING_AWS_ACCESS_KEY_ID={ak['AccessKeyId']}")
print(f"RECORDING_AWS_SECRET_ACCESS_KEY={ak['SecretAccessKey']}")
PYEOF
fi

echo "Pushing merged configuration to Secrets Manager (values not shown)..."
aws secretsmanager put-secret-value \
  --secret-id "sakhi/api-keys" \
  --region "${AWS_REGION}" \
  --secret-string "file://$(cygpath -m "${TMP_MERGED}" 2>/dev/null || echo "${TMP_MERGED}")" \
  > /dev/null

echo "Done. $(wc -l < "${TMP_MERGED}") lines pushed. Re-run run_setup_instance.sh (or just the hydrate step) to pick this up on the instance:"
echo "  aws ssm send-command --instance-ids \$(cat ${STATE_DIR}/instance-id) --document-name AWS-RunShellScript --parameters 'commands=[\"systemctl restart sakhi-hydrate-env sakhi-agent sakhi-frontend\"]'"
