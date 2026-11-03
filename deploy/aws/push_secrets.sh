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
                v = v.strip()
                # Strip a trailing inline comment the way python-dotenv does
                # when agent.py loads the real .env locally - confirmed live
                # 2026-09-09 this was NOT happening here: a real .env line
                # ("GROQ_ATTEMPT_TIMEOUT=1.5        # groq max observed
                # 1113ms") got pushed to Secrets Manager with the comment
                # still attached, and float(os.getenv(...)) on the deployed
                # instance crashed LLM prewarm outright the moment
                # GROQ_ATTEMPT_TIMEOUT/OPENROUTER_ATTEMPT_TIMEOUT/
                # LLM_GROUP_TIMEOUT were added to KEEP below. Simple rule,
                # not full dotenv-quote-string parsing: an unquoted value's
                # inline comment starts at whitespace followed by '#'.
                if not (v.startswith('"') or v.startswith("'")):
                    v = re.split(r"\s+#", v, maxsplit=1)[0].strip()
                vals[k.strip()] = v
    except FileNotFoundError:
        pass
    return vals

project = load(".env")
parent = load(parent_path)
merged = dict(parent)
merged.update(project)   # project .env wins, matching load_dotenv(override=False) load order

# Only the keys the deployed instance actually needs - see
# provision_infra.sh's secret schema for the full list and rationale.
#
# UPDATED 2026-09-09: this list, and the fixed overrides below, had drifted
# from the actual, live-validated pilot configuration - confirmed directly
# against the real local .env before this deploy ran, not assumed. Vakyam
# TTS is out of credits (confirmed live: "Your account has 7 credits
# remaining but this request requires 60") and Deepgram-only STT was
# superseded by Gnani days earlier - deploying the old values here would
# have shipped a non-functional TTS provider and skipped the tuned STT
# pairing. The VAD/pacing/LLM tuning vars below were entirely absent from
# this list before, meaning the deployed instance silently fell back to
# code defaults for all of them regardless of what was tuned locally.
KEEP = [
    "LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET",
    "DEEPGRAM_API_KEY", "VAKYAM_API_KEY", "GNANI_API_KEY", "SARVAM_API_KEY",
    "GROQ_API_KEYS", "GROQ_API_KEY", "GROQ_API_KEY_1", "GROQ_API_KEY_2",
    "OPENROUTER_API_KEY", "OPENROUTER_BASE_URL",
    "OPENROUTER_LLM_MODEL", "OPENROUTER_BACKUP_MODEL", "GROQ_LLM_MODEL",
    # OPENAI_API_KEY was missing from this list entirely (confirmed live,
    # 2026-09-09): this script predates OpenAI becoming the LLM primary, so
    # the deployed instance's build_llm() silently fell through to
    # groq+openrouter only - not a reordering, OpenAI's tier was never
    # constructed at all for lack of a key. OPENAI_LLM_MODEL is optional -
    # build_llm() defaults it to "gpt-4o-mini" (agent.py) if unset.
    "OPENAI_API_KEY", "OPENAI_LLM_MODEL",
    # PostgreSQL/Supabase cutover.  Kept in Secrets Manager and hydrated into
    # /etc/sakhi.env like the provider credentials; never printed by this
    # script.  When absent, the current SQLite pilot behavior is unchanged.
    "DATABASE_URL", "POSTGRES_URL", "DATABASE_CONNECT_TIMEOUT",
    # Sarvam TTS/STT tuning (active provider pair as of 2026-09-08/09).
    "SARVAM_TTS_MODEL", "SARVAM_SPEAKER", "SARVAM_TTS_HTTP_TIMEOUT",
    "SARVAM_STT_MODEL", "SARVAM_STT_MODE",
    "GNANI_VOICE",
    # VAD / turn-pacing tuning - each value here is a live-call-verified
    # correction documented in .env's own comment history; omitting them
    # silently reverts the deployed instance to code defaults, which do not
    # match what was actually field-tested. NOISE_CANCELLATION deliberately
    # NOT in this list - it is its own fixed override below (bvc/webrtc are
    # synonyms in _noise_filter(), so listing it here too would just write
    # the same setting under both names into the same secret string).
    "VAD_ACTIVATION_THRESHOLD", "VAD_MIN_SILENCE",
    "MIN_ENDPOINTING_DELAY", "VAD_ENDPOINT_SLOW_S", "TTS_EAGER_MIN_CHUNK_CHARS",
    "LATENT_FILLER_DELAY",
    # LLM tuning (OpenAI-primary-with-fallback, 2026-09-09).
    "LLM_PROVIDER_ORDER", "SAKHI_LLM_HEDGE", "LLM_MAX_RETRY_PER_PROVIDER",
    "LLM_TEMPERATURE", "LLM_MAX_COMPLETION_TOKENS", "LLM_TOP_P",
    "LLM_CONTEXT_MAX_ITEMS", "GROQ_ATTEMPT_TIMEOUT", "OPENROUTER_ATTEMPT_TIMEOUT",
    "LLM_GROUP_TIMEOUT", "SIXTYDB_NAME_MATCH_THRESHOLD",
]
with open(out_path, "w", encoding="utf-8") as f:
    # Fixed production overrides, always written regardless of local .env -
    # these are pilot decisions (single-provider, no fallback for STT/TTS;
    # this worker's own distinct agent name), not values to inherit from
    # whatever a local dev session happened to have set.
    #
    # STT_PRIMARY/TTS_PROVIDER corrected 2026-09-09 (were deepgram/vakyam -
    # stale relative to the actual, currently-working pilot pairing; see the
    # KEEP list comment above for why). SAKHI_SCOPED_TOOLS added the same
    # day, explicitly enabled per the user's own choice: the duplicate-
    # function-name bug that motivated disabling it (18 Aug 2026) is fixed
    # and covered by tests/test_scoped_tools.py's rescoping-merge test as of
    # this deployment - shipping it disabled would silently drop the
    # register_mother-duplicate-record guard and the pre-triage
    # connect_smfmi_specialist gate in production.
    f.write("LIVEKIT_AGENT_NAME=sakhi-maternal-care\n")
    f.write("LIVEKIT_EXPLICIT_DISPATCH=true\n")
    f.write("STT_PRIMARY=gnani\n")
    f.write("VOICE_STT_FALLBACK_ENABLED=true\n")
    f.write("TTS_PROVIDER=sarvam\n")
    f.write("VOICE_TTS_FALLBACK_ENABLED=false\n")
    f.write("SAKHI_SCOPED_TOOLS=true\n")
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
                        "SARVAM_API_KEY", "GNANI_API_KEY")
           if k not in merged or not merged.get(k)]
if missing:
    print(f"WARNING: these required keys were not found locally: {', '.join(missing)}", file=sys.stderr)
PYEOF

# --- Preserve the dashboard password already in the secret (do not clobber) -
# Explicit override (2026-09-09): set SAKHI_DASHBOARD_USER/
# SAKHI_DASHBOARD_PASSWORD in the environment to force specific credentials
# for one push, e.g. `SAKHI_DASHBOARD_USER=sakhi SAKHI_DASHBOARD_PASSWORD=...
# ./push_secrets.sh`. Deliberately an env override, not a value hardcoded
# into this script - a plaintext password belongs in nobody's source
# control, and the default preserve-or-random behavior below stays intact
# for every normal run that doesn't set these.
if [ -n "${SAKHI_DASHBOARD_USER:-}" ] && [ -n "${SAKHI_DASHBOARD_PASSWORD:-}" ]; then
  echo "DASHBOARD_USER=${SAKHI_DASHBOARD_USER}" >> "${TMP_MERGED}"
  echo "DASHBOARD_PASSWORD=${SAKHI_DASHBOARD_PASSWORD}" >> "${TMP_MERGED}"
  echo ">>> Dashboard credentials set from SAKHI_DASHBOARD_USER/SAKHI_DASHBOARD_PASSWORD override."
else
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
fi

# --- Preserve (or mint) the egress-recording S3 credentials ---------------
# These belong to the sakhi-egress-uploader IAM user (s3:PutObject/
# PutObjectAcl on recordings/* in the recordings bucket only - see
# README.md), used by LiveKit Cloud's own egress workers to write the call
# recording. Egress runs on LiveKit's infrastructure and cannot use this
# instance's IAM role (sakhi-instance-role), so a key pair is unavoidable;
# its ONLY home is the sakhi/api-keys secret in AWS Secrets Manager.
#
# Never written to disk (ISO 27001 A.9.4): no .deploy-state JSON is read or
# written. An existing key is carried over from Secrets Manager; when there
# is none, one is minted and piped straight into the temp merge file that
# the EXIT trap above deletes. To rotate: SAKHI_ROTATE_EGRESS=1 mints a new
# key even when the secret already holds one (then delete the old key in IAM).
EXISTING_EGRESS=""
if [ "${SAKHI_ROTATE_EGRESS:-}" != "1" ]; then
  EXISTING_EGRESS=$(aws secretsmanager get-secret-value --secret-id sakhi/api-keys \
    --region "${AWS_REGION}" --query SecretString --output text 2>/dev/null \
    | grep -E "^RECORDING_AWS_(ACCESS_KEY_ID|SECRET_ACCESS_KEY)=" || true)
fi
if [ -n "${EXISTING_EGRESS}" ]; then
  echo "${EXISTING_EGRESS}" >> "${TMP_MERGED}"
  echo ">>> Egress-recording credential carried over from Secrets Manager (value not shown)."
else
  echo ">>> No egress-recording credential to keep (or SAKHI_ROTATE_EGRESS=1) - minting a new access key for the"
  echo ">>> sakhi-egress-uploader IAM user (must already exist). It goes into the secret only, never to disk."
  aws iam create-access-key --user-name sakhi-egress-uploader --region "${AWS_REGION}" \
    --query 'AccessKey.[AccessKeyId,SecretAccessKey]' --output text \
    | awk '{ print "RECORDING_AWS_ACCESS_KEY_ID=" $1; print "RECORDING_AWS_SECRET_ACCESS_KEY=" $2 }' >> "${TMP_MERGED}"
fi

echo "Pushing merged configuration to Secrets Manager (values not shown)..."
aws secretsmanager put-secret-value \
  --secret-id "sakhi/api-keys" \
  --region "${AWS_REGION}" \
  --secret-string "file://$(cygpath -m "${TMP_MERGED}" 2>/dev/null || echo "${TMP_MERGED}")" \
  > /dev/null

echo "Done. $(wc -l < "${TMP_MERGED}") lines pushed. Re-run run_setup_instance.sh (or just the hydrate step) to pick this up on the instance:"
echo "  aws ssm send-command --instance-ids \$(cat ${STATE_DIR}/instance-id) --document-name AWS-RunShellScript --parameters 'commands=[\"systemctl restart sakhi-hydrate-env sakhi-agent sakhi-frontend\"]'"
