#!/usr/bin/env bash
# Ships the REAL Sakhi application (every runtime *.py module, requirements,
# and the full Next.js frontend source) to the instance and restarts the two
# real services. Run from the repo root's deploy/aws directory whenever app
# code changes.
#
# Differs from the original guide's deploy script in exactly one structural
# way: it syncs the whole codebase (this app is ~30 interdependent modules -
# triage/guardrails/prompts/repository/wrappers - not two files) and it also
# builds the Next.js frontend on the instance, since the dashboard is the
# real ASHA console, not a static HTML bundle.
#
# `agent.py download-files` (2026-09-03): livekit-agents' standard
# model-prefetch subcommand for the semantic turn-detector (MultilingualModel)
# and Silero VAD's ONNX weights. Confirmed live this was missing entirely -
# every deployed call ended "without a completed caller transcript" because
# the turn-detector crashed at session start with "Could not find file
# model_q8.onnx" (it was never downloaded, and evidently doesn't fall back to
# a lazy fetch that succeeds unattended). Must run AFTER agent.py exists on
# disk (this script is the first point in the whole deploy where it does -
# setup_instance.sh runs before any app code is even synced) and as the
# `sakhi` user with HOME=/opt/sakhi explicitly set, so the cache lands where
# the systemd service (User=sakhi) will actually look for it, not root's own
# home. Also confirmed live: it must run while sakhi-agent.service is
# STOPPED, not just before restarting it - agent.py's own singleton lock
# (WORKER_SINGLETON_PORT) refuses to run a second copy, and download-files
# still goes through that same module-level guard even though it is not
# actually starting a worker, so it collided with the live service and
# aborted the whole deploy (`set -e`) the first time this ran against an
# already-running instance.
set -euo pipefail

AWS_REGION="ap-south-1"
ACCOUNT_ID=$(aws sts get-caller-identity --query "Account" --output text)
DEPLOY_BUCKET="sakhi-deploy-${ACCOUNT_ID}"
cd "$(dirname "$0")/../.."   # repo root

INSTANCE_ID=$(cat deploy/aws/.deploy-state/instance-id 2>/dev/null || \
  aws ec2 describe-instances \
    --filters "Name=tag:Name,Values=sakhi-voice-agent" "Name=instance-state-name,Values=running" \
    --query "Reservations[0].Instances[0].InstanceId" --output text)

if [ -z "${INSTANCE_ID}" ] || [ "${INSTANCE_ID}" == "None" ]; then
  echo "Error: no running Sakhi EC2 instance found."
  exit 1
fi

echo "Staging application code to s3://${DEPLOY_BUCKET}/..."

# Every runtime Python module this app actually imports at startup or
# on a call - not test_*.py, not scripts/ (SIP trunk setup, one-off
# benchmarks), not the throwaway dev-preview scaffolding.
# Exactly the modules reachable from agent.py/status_server.py (2026-09-25:
# faq_cache, gnani_wrappers, mock_sixtydb, murf_wrappers, vercel_common were
# never imported and were removed from the server; Gnani STT comes from the
# livekit plugin). tests/test_deploy_manifest.py fails if one goes missing.
PY_FILES=(
  agent.py clinical_terms.py db_helper.py gemini_wrappers.py
  guardrails.py kitten_tts_provider.py llm_hedge.py
  rumik_wrappers.py sakhi_avatar.py
  sakhi_aws_costs.py sakhi_dashboard_store.py sakhi_domain.py sakhi_intake.py sakhi_kannada.py
  sakhi_normalizer.py sakhi_pacing.py sakhi_question_lock.py
  sakhi_prompts.py sakhi_repository.py sakhi_schema.py sakhi_sql_repository.py
  sakhi_telemetry.py sakhi_tools.py sakhi_triage.py sarvam_wrappers.py sixtydb_wrappers.py
  skillies_wrappers.py smallest_wrappers.py status_server.py tts_audio_cache.py
  vakyam_wrappers.py voice_catalog.py
)
# tests/test_deploy_manifest.py walks agent.py's real import graph and fails
# if a reachable local module is missing here. It caught tts_audio_cache.py
# on its first run (2026-09-24): present on the live server only because it
# had been copied there by hand, so a clean deploy would have crashed at import.
# clinical_terms.py added 2026-09-09: agent.py imports it
# (CLINICAL_KEYWORDS, canonicalise) and this hand-maintained list had not
# been updated since that module was added earlier this session - confirmed
# by diffing agent.py's actual `from X import` statements against this list
# before this deploy ran, not assumed. Missing it would have crashed
# sakhi-agent.service at import time on every deploy.
for f in "${PY_FILES[@]}"; do
  [ -f "$f" ] && aws s3 cp "$f" "s3://${DEPLOY_BUCKET}/app/$f" --only-show-errors
done
aws s3 cp requirements.txt "s3://${DEPLOY_BUCKET}/app/requirements.txt" --only-show-errors
[ -f mock_sixtydb.json ] && aws s3 cp mock_sixtydb.json "s3://${DEPLOY_BUCKET}/app/mock_sixtydb.json" --only-show-errors

# Ship only the production operational scripts. Broad-syncing scripts/ would
# place ad-hoc benchmarks and live provider probes on the server. The
# migration remains dry-run unless --apply is supplied; the keepalive writes
# only its isolated, explicitly test-only maintenance row.
OPERATIONAL_SCRIPTS=(
  scripts/migrate_sakhi_sqlite_to_postgres.py
  scripts/supabase_keepalive.py
)
for f in "${OPERATIONAL_SCRIPTS[@]}"; do
  aws s3 cp "$f" "s3://${DEPLOY_BUCKET}/app/$f" --only-show-errors
done

SYSTEMD_FILES=(
  deploy/aws/sakhi-supabase-keepalive.service
  deploy/aws/sakhi-supabase-keepalive.timer
)
for f in "${SYSTEMD_FILES[@]}"; do
  aws s3 cp "$f" "s3://${DEPLOY_BUCKET}/app/$f" --only-show-errors
done

echo "Syncing frontend source (excluding node_modules/.next)..."
aws s3 sync frontend/ "s3://${DEPLOY_BUCKET}/app/frontend/" \
  --exclude "node_modules/*" --exclude ".next*" --exclude ".next*/*" \
  --exclude ".env.local" \
  --delete --only-show-errors

echo "Deploying to ${INSTANCE_ID} via SSM (this runs npm ci && npm run build - can take a few minutes)..."
COMMAND_ID=$(aws ssm send-command \
  --instance-ids "${INSTANCE_ID}" \
  --document-name "AWS-RunShellScript" \
  --comment "Deploying Sakhi application code" \
  --timeout-seconds 900 \
  --parameters "commands=[
    \"set -e\",
    \"DEPLOY_BUCKET=${DEPLOY_BUCKET}\",
    \"aws s3 sync s3://\$DEPLOY_BUCKET/app/ /opt/sakhi/ --exclude 'frontend/*'\",
    \"aws s3 sync s3://\$DEPLOY_BUCKET/app/frontend/ /opt/sakhi/frontend/ --delete --exclude 'node_modules/*' --exclude '.next*' --exclude '.next*/*'\",
    \"/opt/sakhi/venv/bin/pip install -q -r /opt/sakhi/requirements.txt\",
    \"/opt/sakhi/venv/bin/python3 -m py_compile /opt/sakhi/agent.py /opt/sakhi/sakhi_domain.py /opt/sakhi/sakhi_tools.py /opt/sakhi/sakhi_triage.py /opt/sakhi/sakhi_intake.py /opt/sakhi/sakhi_prompts.py /opt/sakhi/status_server.py\",
    # Keep these as separate simple commands.  During the 2026-09-24
    # Supabase cutover, `npm ci` failed because package-lock.json was stale,
    # but Bash's `set -e` exception for commands inside an `&&` list allowed
    # the SSM script to continue.  The old .next directory was then served and
    # /admin returned 404 while the deploy falsely reported success.  Simple
    # commands make any install/build failure terminate the remote script.
    \"cd /opt/sakhi/frontend\",
    \"npm ci --no-audit --no-fund\",
    \"npm run build\",
    \"chown -R sakhi:sakhi /opt/sakhi\",
    \"install -o root -g root -m 0644 /opt/sakhi/deploy/aws/sakhi-supabase-keepalive.service /etc/systemd/system/sakhi-supabase-keepalive.service\",
    \"install -o root -g root -m 0644 /opt/sakhi/deploy/aws/sakhi-supabase-keepalive.timer /etc/systemd/system/sakhi-supabase-keepalive.timer\",
    \"systemctl daemon-reload\",
    \"systemctl stop sakhi-agent.service\",
    \"sudo -u sakhi HOME=/opt/sakhi /opt/sakhi/venv/bin/python3 /opt/sakhi/agent.py download-files\",
    \"systemctl start sakhi-agent.service\",
    \"systemctl restart sakhi-frontend.service\",
    \"if grep -Eq '^(DATABASE_URL|POSTGRES_URL)=' /etc/sakhi.env; then systemctl enable --now sakhi-supabase-keepalive.timer; else systemctl disable --now sakhi-supabase-keepalive.timer 2>/dev/null || true; fi\",
    \"sleep 3\",
    \"systemctl is-active sakhi-agent.service\",
    \"systemctl is-active sakhi-frontend.service\"
  ]" \
  --query "Command.CommandId" --output text)

echo "Waiting for deployment (Command ID: ${COMMAND_ID})..."
aws ssm wait command-executed --command-id "${COMMAND_ID}" --instance-id "${INSTANCE_ID}" || true

STATUS=$(aws ssm get-command-invocation --command-id "${COMMAND_ID}" --instance-id "${INSTANCE_ID}" --query "Status" --output text)
if [ "${STATUS}" == "Success" ]; then
  echo "=== Deployment successful ==="
else
  echo "Deployment failed with status: ${STATUS}"
  aws ssm get-command-invocation --command-id "${COMMAND_ID}" --instance-id "${INSTANCE_ID}" --query "StandardErrorContent" --output text
  exit 1
fi
