aws ssm send-command \
  --instance-ids i-0b4c434a3c7647384 \
  --document-name AWS-RunShellScript \
  --region ap-south-1 \
  --parameters 'commands=[
    "systemctl restart sakhi-app 2>/dev/null || systemctl restart sakhi 2>/dev/null",
    "sleep 5",
    "systemctl is-active sakhi-app 2>/dev/null || systemctl is-active sakhi 2>/dev/null",
    "systemctl is-active nginx",
    "curl -s -o /dev/null -w \"%{http_code}\" http://localhost"
  ]' \
  --query 'Command.CommandId' \
  --output text
