#!/usr/bin/env bash
# One-time (or re-run-safe) instance setup: stages setup_instance.sh +
# requirements.txt to S3, then executes it on the target instance via SSM.
# Run after provision_infra.sh and push_secrets.sh; safe to re-run if a
# system-level step (nginx config, systemd units, Node version) needs
# updating - it does not touch application code, that's deploy_sakhi.sh.
#
# The SSM command list below installs the AWS CLI itself before its first
# `aws s3 cp` - confirmed live that Canonical's bare Ubuntu 24.04 cloud
# image ships the SSM Agent (that's how send-command reaches it at all) but
# not the `aws` CLI. Without this, the very first `aws s3 cp` used to fetch
# setup_instance.sh from S3 fails with "aws: not found" before
# setup_instance.sh (which used to apt-get install a now-nonexistent
# `awscli` package, too late to help itself get downloaded anyway) ever
# runs. Confirmed live: Ubuntu 24.04/Noble dropped the classic `awscli` apt
# package entirely (Python 3.12's externally-managed-environment policy
# broke its old pip-based build) - `apt-get install awscli` 404s with "no
# installation candidate". Installs the official AWS CLI v2 zip instead.
set -euo pipefail

AWS_REGION="ap-south-1"
ACCOUNT_ID=$(aws sts get-caller-identity --query "Account" --output text)
DEPLOY_BUCKET="sakhi-deploy-${ACCOUNT_ID}"
cd "$(dirname "$0")"

INSTANCE_ID="${1:-}"
if [ -z "${INSTANCE_ID}" ]; then
  INSTANCE_ID=$(cat .deploy-state/instance-id 2>/dev/null || \
    aws ec2 describe-instances \
      --filters "Name=tag:Name,Values=sakhi-voice-agent" "Name=instance-state-name,Values=running" \
      --query "Reservations[0].Instances[0].InstanceId" --output text)
fi
if [ -z "${INSTANCE_ID}" ] || [ "${INSTANCE_ID}" == "None" ]; then
  echo "Error: no running Sakhi instance found. Pass an instance id explicitly, or run provision_infra.sh first."
  exit 1
fi

echo "Staging setup files to s3://${DEPLOY_BUCKET}/..."
aws s3 cp setup_instance.sh "s3://${DEPLOY_BUCKET}/setup_instance.sh"
aws s3 cp sakhi.nginx.conf "s3://${DEPLOY_BUCKET}/sakhi.nginx.conf"
aws s3 cp ../../requirements.txt "s3://${DEPLOY_BUCKET}/requirements.txt"

echo "Running setup on ${INSTANCE_ID} via SSM..."
COMMAND_ID=$(aws ssm send-command \
  --instance-ids "${INSTANCE_ID}" \
  --document-name "AWS-RunShellScript" \
  --comment "Sakhi instance setup" \
  --timeout-seconds 900 \
  --parameters "commands=[
    \"set -e\",
    \"mkdir -p /opt/sakhi\",
    \"command -v aws >/dev/null 2>&1 || (apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq unzip curl && curl -s https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip -o /tmp/awscliv2.zip && unzip -q -o /tmp/awscliv2.zip -d /tmp && /tmp/aws/install && rm -rf /tmp/awscliv2.zip /tmp/aws)\",
    \"aws s3 cp s3://${DEPLOY_BUCKET}/requirements.txt /opt/sakhi/requirements.txt\",
    \"aws s3 cp s3://${DEPLOY_BUCKET}/setup_instance.sh /tmp/setup_instance.sh\",
    \"aws s3 cp s3://${DEPLOY_BUCKET}/sakhi.nginx.conf /tmp/sakhi.nginx.conf\",
    \"chmod +x /tmp/setup_instance.sh\",
    \"/tmp/setup_instance.sh\"
  ]" \
  --query "Command.CommandId" --output text)

echo "Waiting for setup to finish (this installs Node, Python deps, nginx - can take a few minutes)..."
aws ssm wait command-executed --command-id "${COMMAND_ID}" --instance-id "${INSTANCE_ID}" || true

STATUS=$(aws ssm get-command-invocation --command-id "${COMMAND_ID}" --instance-id "${INSTANCE_ID}" --query "Status" --output text)
if [ "${STATUS}" == "Success" ]; then
  echo "=== Instance setup successful ==="
else
  echo "Setup failed with status: ${STATUS}"
  aws ssm get-command-invocation --command-id "${COMMAND_ID}" --instance-id "${INSTANCE_ID}" --query "StandardErrorContent" --output text
  exit 1
fi

echo "${INSTANCE_ID}" > .deploy-state/instance-id
echo "Next: ./deploy_sakhi.sh"
