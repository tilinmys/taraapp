aws ssm send-command \
  --instance-ids i-0b4c434a3c7647384 \
  --document-name AWS-RunShellScript \
  --region ap-south-1 \
  --parameters 'commands=[
    "cd /opt/sakhi/frontend",
    "aws s3 cp s3://sakhi-deploy-147435199438/package.json /opt/sakhi/frontend/package.json --region ap-south-1",
    "npm install --package-lock-only --ignore-scripts",
    "aws s3 cp /opt/sakhi/frontend/package-lock.json s3://sakhi-deploy-147435199438/package-lock.json --region ap-south-1",
    "echo LOCK-FILE-UPLOADED"
  ]' \
  --query 'Command.CommandId' \
  --output text
