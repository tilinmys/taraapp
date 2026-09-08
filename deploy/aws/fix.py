import sys

content = open('provision_infra.sh', 'r').read()
part1, part2 = content.split('--vpc-id "${VPC_ID}" \\\n')

correct_middle = """  --query "GroupId" --output text 2>/dev/null || \\
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
  AMI_ID=$(aws ec2 describe-images \\
    --owners 099720109477 \\
    --filters "Name=name,Values=ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*" \\
              "Name=state,Values=available" \\
    --query "sort_by(Images, &CreationDate)[-1].ImageId" --output text)

  echo "Launching instance with AMI: ${AMI_ID}..."
  INSTANCE_ID=$(aws ec2 run-instances \\
    --image-id "${AMI_ID}" \\
    --count 1 \\
    --instance-type "t3.small" \\
    --security-group-ids "${SG_ID}" \\
    --subnet-id "${SUBNET_ID}" \\
"""

end_part = part2.split('--iam-instance-profile Name="sakhi-instance-profile" \\\n')[1]

with open('provision_infra.sh', 'w', newline='\n') as f:
    f.write(part1 + '--vpc-id "${VPC_ID}" \\\n' + correct_middle + '    --iam-instance-profile Name="sakhi-instance-profile" \\\n' + end_part)
print('Fixed')
