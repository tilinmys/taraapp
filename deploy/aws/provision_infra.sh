#!/usr/bin/env bash
# Provisions the AWS infrastructure for a standalone Sakhi deployment in
# ap-south-1 (Mumbai): S3 (deploy + recordings), Secrets Manager, IAM role/
# instance profile, security group, and a t3.small EC2 instance with an
# Elastic IP. Zero SSH - access is SSM-only.
#
# Adapted from a generated infra guide (2026-09-02). What changed from that
# guide, and why:
#
#   - Secrets Manager gets a REAL secret schema matching what agent.py and
#     the Next.js frontend actually read (see the vars this script writes
#     below) - the guide's SMALLEST_API_KEY/generic placeholder set does not
#     match a single env var this codebase reads.
#   - The secret is created EMPTY here (a schema of blank keys), not with
#     real credentials inlined in this script. push_secrets.sh (same
#     directory) pushes the real values by reading your local .env files
#     directly - so no credential ever appears in shell history or in this
#     script's own text.
#   - No hardcoded dashboard password. A random one is generated here and
#     written into the SAME secret; setup_instance.sh hashes it into
#     .htpasswd on the instance rather than shipping a precomputed hash.
#
# Run this from your administrative workstation with AWS CLI v2 and an
# authenticated session (`aws sts get-caller-identity` must succeed first).
set -euo pipefail

cd "$(dirname "$0")"

AWS_REGION="ap-south-1"
export AWS_DEFAULT_REGION="${AWS_REGION}"

ACCOUNT_ID=$(aws sts get-caller-identity --query "Account" --output text)
echo "=== Provisioning Sakhi Standalone Stack on AWS Account: ${ACCOUNT_ID} (${AWS_REGION}) ==="

DEPLOY_BUCKET="sakhi-deploy-${ACCOUNT_ID}"
RECORDINGS_BUCKET="sakhi-call-recordings-${ACCOUNT_ID}"

# --- 1. S3 buckets ----------------------------------------------------------
echo "Creating S3 buckets..."
aws s3api create-bucket \
  --bucket "${DEPLOY_BUCKET}" \
  --region "${AWS_REGION}" \
  --create-bucket-configuration LocationConstraint="${AWS_REGION}" || true

aws s3api put-bucket-versioning \
  --bucket "${DEPLOY_BUCKET}" \
  --versioning-configuration Status=Enabled

aws s3api put-bucket-encryption \
  --bucket "${DEPLOY_BUCKET}" \
  --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'

aws s3api create-bucket \
  --bucket "${RECORDINGS_BUCKET}" \
  --region "${AWS_REGION}" \
  --create-bucket-configuration LocationConstraint="${AWS_REGION}" || true

aws s3api put-public-access-block \
  --bucket "${RECORDINGS_BUCKET}" \
  --public-access-block-configuration "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"

aws s3api put-bucket-encryption \
  --bucket "${RECORDINGS_BUCKET}" \
  --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'

# Call recordings are not currently produced by agent.py (no egress
# configured) - this rule is provisioned for when that ships, so storage
# never accumulates unbounded in the meantime.
aws s3api put-bucket-lifecycle-configuration \
  --bucket "${RECORDINGS_BUCKET}" \
  --lifecycle-configuration '{
    "Rules": [
      {
        "ID": "ExpireAudioAfter30Days",
        "Status": "Enabled",
        "Filter": { "Prefix": "recordings/" },
        "Expiration": { "Days": 30 }
      }
    ]
  }'

# --- 2. Secrets Manager: schema only, no real values here -------------------
#
# Matches exactly what agent.py / sakhi_tools.py / *_wrappers.py read via
# os.getenv() (confirmed 2026-09-02) plus the config knobs this session
# tuned and pinned as the pilot's chosen single-provider/fallback shape:
#
#   STT: Deepgram only (VOICE_STT_FALLBACK_ENABLED=false - Sarvam is
#        credit-exhausted, confirmed live; see .env's own comment history)
#   TTS: Vakyam only  (VOICE_TTS_FALLBACK_ENABLED=false)
#   LLM: Groq hedged against OpenRouter (kept as a fallback chain)
DASHBOARD_PASSWORD=$(openssl rand -base64 18 | tr -d '=+/' | cut -c1-20)

echo "Creating Secrets Manager secret: sakhi/api-keys (schema only)..."
aws secretsmanager create-secret \
  --name "sakhi/api-keys" \
  --description "Sakhi Voice Platform - runtime configuration and API keys" \
  --secret-string "$(cat <<SECRETS
LIVEKIT_URL=
LIVEKIT_API_KEY=
LIVEKIT_API_SECRET=
LIVEKIT_AGENT_NAME=sakhi-maternal-care
LIVEKIT_EXPLICIT_DISPATCH=true

DEEPGRAM_API_KEY=
STT_PRIMARY=deepgram
VOICE_STT_FALLBACK_ENABLED=false

VAKYAM_API_KEY=
TTS_PROVIDER=vakyam
VOICE_TTS_FALLBACK_ENABLED=false
# Kept configured but inactive - re-enabling the fallback chain (flip the
# flag above) needs these present, not re-integrated from scratch.
GNANI_API_KEY=
SARVAM_API_KEY=

GROQ_API_KEYS=
OPENROUTER_API_KEY=
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_LLM_MODEL=meta-llama/llama-3.3-70b-instruct
OPENROUTER_BACKUP_MODEL=meta-llama/llama-3.1-8b-instruct
GROQ_LLM_MODEL=openai/gpt-oss-120b

SAKHI_AVATAR=none
NOISE_CANCELLATION=telephony
ENABLE_STATUS_SERVER=true
PORT=8080
WORKER_STATUS_URL=http://127.0.0.1:8080
SAKHI_DB_PATH=/opt/sakhi/data/sakhi.sqlite3
PIPELINE_LOG_PATH=/opt/sakhi/logs/pipeline_events.jsonl

DASHBOARD_USER=sakhi
DASHBOARD_PASSWORD=${DASHBOARD_PASSWORD}
SECRETS
)" || echo "Secret sakhi/api-keys already exists - run push_secrets.sh to update values, not this script."

echo ""
echo ">>> Dashboard password generated: ${DASHBOARD_PASSWORD}"
echo ">>> Also stored in the secret above (DASHBOARD_PASSWORD) - retrieve it later with:"
echo ">>>   aws secretsmanager get-secret-value --secret-id sakhi/api-keys --query SecretString --output text | grep DASHBOARD_PASSWORD"
echo ""

# --- 3. IAM role & instance profile -----------------------------------------
echo "Configuring IAM role and policies..."

# `aws` on this workstation is a NATIVE WINDOWS Python executable, not an
# MSYS/Git-Bash binary - it does not get the automatic /tmp -> C:/Users/...
# path rewrite Git Bash applies to plain path arguments, and a `file://...`
# URI is passed as one opaque string, so the embedded /tmp/ substring is
# never rewritten either way. The AWS CLI then fails with "No such file or
# directory" looking for a literal backslash-tmp path under whatever its
# own CWD drive is. Fix: write paramfiles to a local directory next to this script
# and build the file:// URI with `cygpath -m` (Windows-style forward-slash
# path with a drive letter), which the native aws.exe CAN resolve. Confirmed
# live - this is exactly the failure this script hit on its first run.
STATE_DIR=".deploy-state"
mkdir -p "${STATE_DIR}"
TRUST_POLICY_PATH="${STATE_DIR}/sakhi-trust-policy.json"
APP_POLICY_PATH="${STATE_DIR}/sakhi-app-policy.json"

cat << 'EOF' > "${TRUST_POLICY_PATH}"
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Principal": { "Service": "ec2.amazonaws.com" },
      "Action": "sts:AssumeRole"
    }
  ]
}
EOF

aws iam create-role \
  --role-name sakhi-instance-role \
  --assume-role-policy-document "file://$(cygpath -m "${TRUST_POLICY_PATH}" 2>/dev/null || echo "${TRUST_POLICY_PATH}")" || true

aws iam attach-role-policy \
  --role-name sakhi-instance-role \
  --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore

# DeployBucketList (s3:ListBucket on the bucket ARN, not the object ARN) is
# required separately from DeployBucketAccess's s3:GetObject: confirmed live
# that deploy_sakhi.sh's `aws s3 sync` run on the instance needs to list the
# bucket to enumerate what to sync, and failed with AccessDenied on
# s3:ListBucket before this was added - object-level GetObject alone isn't
# enough for a sync/list operation.
cat << EOF > "${APP_POLICY_PATH}"
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "SecretsAccess",
      "Effect": "Allow",
      "Action": ["secretsmanager:GetSecretValue"],
      "Resource": "arn:aws:secretsmanager:${AWS_REGION}:${ACCOUNT_ID}:secret:sakhi/api-keys*"
    },
    {
      "Sid": "RecordingsBucketAccess",
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:PutObject"],
      "Resource": "arn:aws:s3:::${RECORDINGS_BUCKET}/*"
    },
    {
      "Sid": "DeployBucketAccess",
      "Effect": "Allow",
      "Action": ["s3:GetObject"],
      "Resource": "arn:aws:s3:::${DEPLOY_BUCKET}/*"
    },
    {
      "Sid": "DeployBucketList",
      "Effect": "Allow",
      "Action": ["s3:ListBucket"],
      "Resource": "arn:aws:s3:::${DEPLOY_BUCKET}"
    }
  ]
}
EOF

aws iam put-role-policy \
  --role-name sakhi-instance-role \
  --policy-name sakhi-app-permissions \
  --policy-document "file://$(cygpath -m "${APP_POLICY_PATH}" 2>/dev/null || echo "${APP_POLICY_PATH}")"

aws iam create-instance-profile --instance-profile-name sakhi-instance-profile || true
aws iam add-role-to-instance-profile --instance-profile-name sakhi-instance-profile --role-name sakhi-instance-role || true

# --- 3b. Egress-recording IAM user (2026-09-02) -----------------------------
# A real AWS_ACCESS_KEY_ID/SECRET pair, not a role: LiveKit Cloud's egress
# workers run on LiveKit's own infrastructure, not this EC2 instance, so
# they cannot assume sakhi-instance-role via IMDS the way the instance
# itself does - room-composite egress needs its own credentials to upload
# the finished recording to S3. Scoped tightly (s3:PutObject/PutObjectAcl
# on recordings/* in the recordings bucket only) precisely because it is a
# real, long-lived credential, not a temporary STS session like everything
# else in this script.
EGRESS_POLICY_PATH="${STATE_DIR}/sakhi-egress-policy.json"
cat << EOF > "${EGRESS_POLICY_PATH}"
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "EgressRecordingsPutOnly",
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:PutObjectAcl"],
      "Resource": "arn:aws:s3:::${RECORDINGS_BUCKET}/recordings/*"
    }
  ]
}
EOF
if ! aws iam get-user --user-name sakhi-egress-uploader >/dev/null 2>&1; then
  aws iam create-user --user-name sakhi-egress-uploader --tags Key=Purpose,Value=sakhi-livekit-egress-only
fi
aws iam put-user-policy --user-name sakhi-egress-uploader --policy-name egress-put-only \
  --policy-document "file://$(cygpath -m "${EGRESS_POLICY_PATH}" 2>/dev/null || echo "${EGRESS_POLICY_PATH}")"
echo "sakhi-egress-uploader IAM user ready. Its access key is minted by push_secrets.sh (not here - a key must never be generated more than once without a reason, and push_secrets.sh already knows how to preserve or mint one)."

echo "Awaiting IAM propagation (10s)..."
sleep 10

# --- 4. Networking: VPC & security group ------------------------------------
VPC_ID=$(aws ec2 describe-vpcs --filters "Name=isDefault,Values=true" --query "Vpcs[0].VpcId" --output text)
SUBNET_ID=$(aws ec2 describe-subnets --filters "Name=vpc-id,Values=${VPC_ID}" --query "Subnets[0].SubnetId" --output text)

echo "Creating security group sakhi-sg in VPC: ${VPC_ID}..."
SG_ID=$(aws ec2 create-security-group \
  --group-name "sakhi-sg" \
  --description "Sakhi voice agent + ASHA dashboard - HTTP/S in, no SSH" \
  --vpc-id "${VPC_ID}" \
  --query "GroupId" --output text 2>/dev/null || \
  aws ec2 describe-security-groups --filters "Name=group-name,Values=sakhi-sg" --query "SecurityGroups[0].GroupId" --output text)

# 80/443 only. Direct WebRTC to LiveKit Cloud means the media path is
# outbound HTTPS/WSS from this instance - no inbound SIP (5060) and no
# inbound WebRTC media ports are needed here at all. Port 22 is
# intentionally never opened - see the trust model note in the README.
aws ec2 authorize-security-group-ingress --group-id "${SG_ID}" --protocol tcp --port 80 --cidr 0.0.0.0/0 || true
aws ec2 authorize-security-group-ingress --group-id "${SG_ID}" --protocol tcp --port 443 --cidr 0.0.0.0/0 || true

# --- 5. Compute --------------------------------------------------------------
# Reuse an existing instance rather than launching a duplicate
echo "Checking for an existing Sakhi instance..."
EXISTING_INSTANCE_ID="None"
if [ -f "${STATE_DIR}/instance-id" ]; then
  SAVED_ID=$(cat "${STATE_DIR}/instance-id")
  STATE=$(aws ec2 describe-instances --instance-ids "${SAVED_ID}" --query "Reservations[0].Instances[0].State.Name" --output text 2>/dev/null || echo "None")
  if [ "${STATE}" == "running" ] || [ "${STATE}" == "pending" ]; then
    EXISTING_INSTANCE_ID="${SAVED_ID}"
  fi
fi

if [ "${EXISTING_INSTANCE_ID}" != "None" ]; then
  INSTANCE_ID="${EXISTING_INSTANCE_ID}"
  echo "EC2 instance already exists: ${INSTANCE_ID}"
else
  echo "Looking up Ubuntu 24.04 LTS AMI (arm64, cheaper t3.small-equivalent Graviton path not used here since t3 is x86 - amd64 AMI)..."
  AMI_ID=$(aws ec2 describe-images \
    --owners 099720109477 \
    --filters "Name=name,Values=ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*" \
              "Name=state,Values=available" \
    --query "sort_by(Images, &CreationDate)[-1].ImageId" --output text)

  echo "Launching instance with AMI: ${AMI_ID}..."
  INSTANCE_ID=$(aws ec2 run-instances \
    --image-id "${AMI_ID}" \
    --count 1 \
    --instance-type "t3.small" \
    --security-group-ids "${SG_ID}" \
    --subnet-id "${SUBNET_ID}" \
    --iam-instance-profile Name="sakhi-instance-profile" \
    --credit-specification "CpuCredits=standard" \
    --block-device-mappings '[
      {
        "DeviceName": "/dev/sda1",
        "Ebs": {
          "VolumeSize": 20,
          "VolumeType": "gp3",
          "DeleteOnTermination": true,
          "Iops": 3000,
          "Throughput": 125,
          "Encrypted": true
        }
      }
    ]' \
    --metadata-options "HttpTokens=required" \
    --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=sakhi-voice-agent}]' \
    --query "Instances[0].InstanceId" --output text)

  echo "Instance launched: ${INSTANCE_ID}"
fi

echo "Waiting for instance to be running..."
aws ec2 wait instance-running --instance-ids "${INSTANCE_ID}"

echo "Waiting for SSM agent to check in (needed before setup_instance.sh can run)..."
for i in $(seq 1 20); do
  STATUS=$(aws ssm describe-instance-information \
    --filters "Key=InstanceIds,Values=${INSTANCE_ID}" \
    --query "InstanceInformationList[0].PingStatus" --output text 2>/dev/null || echo "None")
  [ "${STATUS}" == "Online" ] && break
  sleep 10
done

# --- 6. Elastic IP -----------------------------------------------------------
# Same idempotency issue as the instance above: only allocate/associate a
# new EIP if this instance doesn't already have a public IP.
EXISTING_PUBLIC_IP=$(aws ec2 describe-instances --instance-ids "${INSTANCE_ID}" \
  --query "Reservations[0].Instances[0].PublicIpAddress" --output text 2>/dev/null || echo "None")
if [ -n "${EXISTING_PUBLIC_IP}" ] && [ "${EXISTING_PUBLIC_IP}" != "None" ]; then
  echo "Instance already has a public IP (${EXISTING_PUBLIC_IP}) - reusing it."
  PUBLIC_IP="${EXISTING_PUBLIC_IP}"
else
  echo "Allocating Elastic IP..."
  ALLOC_ID=$(aws ec2 allocate-address --domain vpc --query "AllocationId" --output text)
  aws ec2 associate-address --instance-id "${INSTANCE_ID}" --allocation-id "${ALLOC_ID}"
  PUBLIC_IP=$(aws ec2 describe-addresses --allocation-ids "${ALLOC_ID}" --query "Addresses[0].PublicIp" --output text)
fi

cat <<SUMMARY
==============================================================
 Sakhi standalone infrastructure provisioned
 Instance ID:    ${INSTANCE_ID}
 Elastic IP:     ${PUBLIC_IP}
 Deploy bucket:  ${DEPLOY_BUCKET}
 Recordings:     ${RECORDINGS_BUCKET}
 Access model:   SSM only - no SSH key, port 22 closed

 Next steps:
   1. ./push_secrets.sh                (pushes your REAL local .env values)
   2. ./run_setup_instance.sh ${INSTANCE_ID}   (installs + starts services)
   3. ./deploy_sakhi.sh                 (ships the real app code)
   4. See README.md's verification checklist
==============================================================
SUMMARY

echo "${INSTANCE_ID}" > "${STATE_DIR}/instance-id"
