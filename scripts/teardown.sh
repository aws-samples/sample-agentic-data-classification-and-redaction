#!/bin/bash
# Full teardown script for the Agentic Data Classification demo.
# Removes all AWS resources created by deploy.sh.
# Usage: ./teardown.sh [environment]

set -e

ENVIRONMENT="${1:-demo}"
PROJECT="data-classification"
REGION="us-east-1"
STACK_NAME="${PROJECT}-${ENVIRONMENT}"

echo "=========================================="
echo "Tearing Down Agentic Data Classification Demo"
echo "Environment: ${ENVIRONMENT}"
echo "Region: ${REGION}"
echo "=========================================="
echo ""
echo "This will DELETE all resources including data. Press Ctrl+C to abort."
echo "Waiting 5 seconds..."
sleep 5

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text --region ${REGION})

# Step 1: Delete AgentCore stack (depends on base stack, so delete first)
echo ""
echo "Step 1: Deleting AgentCore stack..."
if aws cloudformation describe-stacks --stack-name "${STACK_NAME}-agentcore" --region "${REGION}" 2>/dev/null; then
  aws cloudformation delete-stack --stack-name "${STACK_NAME}-agentcore" --region "${REGION}"
  echo "  Waiting for AgentCore stack deletion..."
  aws cloudformation wait stack-delete-complete --stack-name "${STACK_NAME}-agentcore" --region "${REGION}" || true
  echo "  AgentCore stack deleted."
else
  echo "  AgentCore stack does not exist, skipping."
fi

# Step 2: Empty S3 buckets (CloudFormation can't delete non-empty buckets)
echo ""
echo "Step 2: Emptying S3 buckets (including versioned objects)..."
for BUCKET_SUFFIX in "frontend" "landing" "processed"; do
  BUCKET="${PROJECT}-${ENVIRONMENT}-${BUCKET_SUFFIX}-${ACCOUNT_ID}"
  if aws s3api head-bucket --bucket "${BUCKET}" --region "${REGION}" 2>/dev/null; then
    echo "  Emptying s3://${BUCKET}..."
    # Delete all object versions and delete markers (required for versioned buckets)
    aws s3api list-object-versions --bucket "${BUCKET}" --region "${REGION}" \
      --query "Versions[].{Key:Key,VersionId:VersionId}" --output json 2>/dev/null | \
      python3 -c "
import sys, json, boto3
objs = json.load(sys.stdin)
if objs:
    s3 = boto3.client('s3', region_name='${REGION}')
    for i in range(0, len(objs), 1000):
        batch = [{'Key': o['Key'], 'VersionId': o['VersionId']} for o in objs[i:i+1000]]
        s3.delete_objects(Bucket='${BUCKET}', Delete={'Objects': batch, 'Quiet': True})
    print(f'    Deleted {len(objs)} object versions')
" 2>/dev/null || true
    # Also delete any delete markers
    aws s3api list-object-versions --bucket "${BUCKET}" --region "${REGION}" \
      --query "DeleteMarkers[].{Key:Key,VersionId:VersionId}" --output json 2>/dev/null | \
      python3 -c "
import sys, json, boto3
objs = json.load(sys.stdin)
if objs:
    s3 = boto3.client('s3', region_name='${REGION}')
    for i in range(0, len(objs), 1000):
        batch = [{'Key': o['Key'], 'VersionId': o['VersionId']} for o in objs[i:i+1000]]
        s3.delete_objects(Bucket='${BUCKET}', Delete={'Objects': batch, 'Quiet': True})
    print(f'    Deleted {len(objs)} delete markers')
" 2>/dev/null || true
    # Final sweep for any non-versioned objects
    aws s3 rm "s3://${BUCKET}" --recursive --region "${REGION}" 2>/dev/null || true
    echo "  Done."
  fi
done

# Step 3: Delete base infrastructure stack
echo ""
echo "Step 3: Deleting base infrastructure stack..."
if aws cloudformation describe-stacks --stack-name "${STACK_NAME}" --region "${REGION}" 2>/dev/null; then
  STACK_STATUS=$(aws cloudformation describe-stacks --stack-name "${STACK_NAME}" --region "${REGION}" --query "Stacks[0].StackStatus" --output text 2>/dev/null)

  # If already in DELETE_FAILED, retry the delete
  if [ "${STACK_STATUS}" = "DELETE_FAILED" ]; then
    echo "  Stack is in DELETE_FAILED state. Retrying delete..."
  fi

  aws cloudformation delete-stack --stack-name "${STACK_NAME}" --region "${REGION}"
  echo "  Waiting for base stack deletion (this may take a few minutes)..."
  aws cloudformation wait stack-delete-complete --stack-name "${STACK_NAME}" --region "${REGION}" 2>/dev/null

  # Check if it actually deleted
  STACK_STATUS=$(aws cloudformation describe-stacks --stack-name "${STACK_NAME}" --region "${REGION}" --query "Stacks[0].StackStatus" --output text 2>/dev/null || echo "DELETED")
  if [ "${STACK_STATUS}" = "DELETE_FAILED" ]; then
    echo "  Stack still in DELETE_FAILED. Checking which resources failed..."
    FAILED=$(aws cloudformation describe-stack-events --stack-name "${STACK_NAME}" --region "${REGION}" --query "StackEvents[?ResourceStatus=='DELETE_FAILED'].LogicalResourceId" --output text 2>/dev/null | head -5)
    echo "  Failed resources: ${FAILED}"
    echo "  Retrying delete with --retain-resources for stuck resources..."
    aws cloudformation delete-stack --stack-name "${STACK_NAME}" --region "${REGION}" \
      --retain-resources ${FAILED}
    aws cloudformation wait stack-delete-complete --stack-name "${STACK_NAME}" --region "${REGION}" 2>/dev/null || true
    echo "  Stack deleted (some resources retained for manual cleanup)."
  else
    echo "  Base stack deleted."
  fi
else
  echo "  Base stack does not exist, skipping."
fi

# Step 4: Delete ECR repository
echo ""
echo "Step 4: Deleting ECR repository..."
if aws ecr describe-repositories --repository-names "${PROJECT}-${ENVIRONMENT}-agent" --region "${REGION}" 2>/dev/null; then
  aws ecr delete-repository --repository-name "${PROJECT}-${ENVIRONMENT}-agent" --region "${REGION}" --force
  echo "  ECR repository deleted."
else
  echo "  ECR repository does not exist, skipping."
fi

# Step 5: Delete Lambda code bucket (created outside CloudFormation by deploy.sh)
echo ""
echo "Step 5: Deleting Lambda code bucket..."
LAMBDA_BUCKET="${PROJECT}-${ENVIRONMENT}-lambda-code-${ACCOUNT_ID}"
# Also check the old bucket name pattern
OLD_BUCKET="${PROJECT}-${ENVIRONMENT}-agent-code-${ACCOUNT_ID}"

for BUCKET in "${LAMBDA_BUCKET}" "${OLD_BUCKET}"; do
  if aws s3api head-bucket --bucket "${BUCKET}" --region "${REGION}" 2>/dev/null; then
    echo "  Removing s3://${BUCKET}..."
    aws s3 rb "s3://${BUCKET}" --force --region "${REGION}"
    echo "  Deleted."
  fi
done

# Step 6: Verify
echo ""
echo "Step 6: Verifying cleanup..."
REMAINING=$(aws cloudformation list-stacks --region "${REGION}" --stack-status-filter CREATE_COMPLETE UPDATE_COMPLETE UPDATE_ROLLBACK_COMPLETE --query "StackSummaries[?contains(StackName,'${PROJECT}')].StackName" --output text 2>/dev/null)
if [ -n "${REMAINING}" ] && [ "${REMAINING}" != "None" ]; then
  echo "  WARNING: Some stacks still exist: ${REMAINING}"
else
  echo "  All stacks deleted successfully."
fi

echo ""
echo "=========================================="
echo "TEARDOWN COMPLETE"
echo "=========================================="
echo ""
echo "All resources removed. To redeploy, run:"
echo "  ./scripts/deploy.sh ${ENVIRONMENT}"
echo ""
