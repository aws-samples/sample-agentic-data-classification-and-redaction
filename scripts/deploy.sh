#!/bin/bash
# Full deployment script for the Agentic Data Classification demo.
# Single command deploys everything: infrastructure, agent, frontend, sample data.
# Usage: ./deploy.sh [environment]

set -e

# Ensure Python user-installed binaries are on PATH
export PATH="$HOME/Library/Python/3.13/bin:$PATH"

ENVIRONMENT="${1:-demo}"
PROJECT="data-classification"
REGION="us-east-1"
STACK_NAME="${PROJECT}-${ENVIRONMENT}"

echo "=========================================="
echo "Deploying Agentic Data Classification Demo"
echo "Environment: ${ENVIRONMENT}"
echo "Region: ${REGION}"
echo "Stack: ${STACK_NAME}"
echo "=========================================="

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text --region ${REGION})
LAMBDA_CODE_BUCKET="${PROJECT}-${ENVIRONMENT}-lambda-code-${ACCOUNT_ID}"

# Step 1: Package agent container image
echo ""
echo "Step 1: Building and pushing agent container to ECR..."
cd "${ROOT_DIR}/agent"

# Get ECR login
aws ecr get-login-password --region "${REGION}" | docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

# Create ECR repo if it doesn't exist (will exist after first stack deploy)
aws ecr describe-repositories --repository-names "${PROJECT}-${ENVIRONMENT}-agent" --region "${REGION}" 2>/dev/null || \
  aws ecr create-repository --repository-name "${PROJECT}-${ENVIRONMENT}-agent" --region "${REGION}" 2>/dev/null || true

AGENT_ECR_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${PROJECT}-${ENVIRONMENT}-agent:latest"

# Build and push (ARM64 required by AgentCore Runtime)
docker build --platform linux/arm64 -t "${PROJECT}-${ENVIRONMENT}-agent:latest" .
docker tag "${PROJECT}-${ENVIRONMENT}-agent:latest" "${AGENT_ECR_URI}"
docker push "${AGENT_ECR_URI}"
echo "  Agent container pushed: ${AGENT_ECR_URI}"

# Step 2: Ensure S3 bucket exists for Lambda code
echo ""
echo "Step 2: Preparing Lambda code bucket..."
if ! aws s3api head-bucket --bucket "${LAMBDA_CODE_BUCKET}" --region "${REGION}" 2>/dev/null; then
  echo "  Creating bucket: ${LAMBDA_CODE_BUCKET}"
  aws s3api create-bucket --bucket "${LAMBDA_CODE_BUCKET}" --region "${REGION}" 2>/dev/null || true
fi

# Step 3: Package and upload Lambda functions
echo ""
echo "Step 3: Packaging and uploading Lambda functions..."

LAMBDA_DIRS=("text-extraction" "classification" "data-retrieval-tool" "document-search-tool" "classification-lookup-tool" "backend-api")

for LAMBDA_NAME in "${LAMBDA_DIRS[@]}"; do
  echo "  Packaging ${LAMBDA_NAME}..."
  LAMBDA_SRC="${ROOT_DIR}/lambdas/${LAMBDA_NAME}"
  BUILD_DIR=$(mktemp -d)

  # Install dependencies if requirements.txt exists
  if [ -f "${LAMBDA_SRC}/requirements.txt" ]; then
    pip install --quiet --target "${BUILD_DIR}" -r "${LAMBDA_SRC}/requirements.txt"
  fi

  # Copy all source files into the build directory
  cp -r "${LAMBDA_SRC}/"* "${BUILD_DIR}/"

  # Create zip from the build directory
  cd "${BUILD_DIR}"
  zip -r -q "${ROOT_DIR}/lambdas/${LAMBDA_NAME}.zip" .
  cd "${ROOT_DIR}"

  # Upload to S3
  aws s3 cp "${ROOT_DIR}/lambdas/${LAMBDA_NAME}.zip" \
    "s3://${LAMBDA_CODE_BUCKET}/lambdas/${LAMBDA_NAME}.zip" \
    --region "${REGION}"

  # Cleanup
  rm -rf "${BUILD_DIR}"
  rm -f "${ROOT_DIR}/lambdas/${LAMBDA_NAME}.zip"

  echo "    Uploaded s3://${LAMBDA_CODE_BUCKET}/lambdas/${LAMBDA_NAME}.zip"
done

# Step 4: Deploy CloudFormation stack (base infrastructure)
echo ""
echo "Step 4: Deploying base infrastructure stack..."
echo "  (S3, DynamoDB, OpenSearch, Lambda, API GW, CloudFront, KMS, CloudWatch)"

cd "${ROOT_DIR}/infra"
aws cloudformation deploy \
  --template-file template.yaml \
  --stack-name "${STACK_NAME}" \
  --region "${REGION}" \
  --parameter-overrides \
    Environment="${ENVIRONMENT}" \
    ProjectName="${PROJECT}" \
    BedrockModelId="us.anthropic.claude-sonnet-4-6" \
    LambdaCodeS3Bucket="${LAMBDA_CODE_BUCKET}" \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset

# Step 4b: Deploy AgentCore stack (Phase 1: Runtime, Memory, PolicyEngine, Gateway)
echo ""
echo "Step 4b: Deploying AgentCore stack (Phase 1 - no targets)..."
DATA_RETRIEVAL_ARN=$(aws cloudformation describe-stacks --stack-name "${STACK_NAME}" --region "${REGION}" --query "Stacks[0].Outputs[?OutputKey=='DataRetrievalToolFunctionArn'].OutputValue" --output text)
DOC_SEARCH_ARN=$(aws cloudformation describe-stacks --stack-name "${STACK_NAME}" --region "${REGION}" --query "Stacks[0].Outputs[?OutputKey=='DocumentSearchToolFunctionArn'].OutputValue" --output text)
CLASS_LOOKUP_ARN=$(aws cloudformation describe-stacks --stack-name "${STACK_NAME}" --region "${REGION}" --query "Stacks[0].Outputs[?OutputKey=='ClassificationLookupToolFunctionArn'].OutputValue" --output text)

aws cloudformation deploy \
  --template-file template-agentcore.yaml \
  --stack-name "${STACK_NAME}-agentcore" \
  --region "${REGION}" \
  --parameter-overrides \
    AgentEcrUri="${AGENT_ECR_URI}" \
    DataRetrievalToolArn="${DATA_RETRIEVAL_ARN}" \
    DocumentSearchToolArn="${DOC_SEARCH_ARN}" \
    ClassificationLookupToolArn="${CLASS_LOOKUP_ARN}" \
    DeployTargets="false" \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset

# Step 4c: Update AgentCore stack (Phase 2: add GatewayTargets)
echo ""
echo "Step 4c: Adding Gateway Targets (Phase 2)..."
aws cloudformation deploy \
  --template-file template-agentcore.yaml \
  --stack-name "${STACK_NAME}-agentcore" \
  --region "${REGION}" \
  --parameter-overrides \
    AgentEcrUri="${AGENT_ECR_URI}" \
    DataRetrievalToolArn="${DATA_RETRIEVAL_ARN}" \
    DocumentSearchToolArn="${DOC_SEARCH_ARN}" \
    ClassificationLookupToolArn="${CLASS_LOOKUP_ARN}" \
    DeployTargets="true" \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset

# Step 4d: Set GATEWAY_URL on the Runtime (now that Gateway exists)
echo ""
echo "Step 4d: Configuring Runtime with Gateway URL and Memory..."
GATEWAY_URL=$(aws cloudformation describe-stacks --stack-name "${STACK_NAME}-agentcore" --region "${REGION}" --query "Stacks[0].Outputs[?OutputKey=='GatewayUrl'].OutputValue" --output text)
MEMORY_ID=$(aws cloudformation describe-stacks --stack-name "${STACK_NAME}-agentcore" --region "${REGION}" --query "Stacks[0].Outputs[?OutputKey=='MemoryId'].OutputValue" --output text)
AGENT_RUNTIME_ID=$(aws cloudformation describe-stacks --stack-name "${STACK_NAME}-agentcore" --region "${REGION}" --query "Stacks[0].Outputs[?OutputKey=='AgentRuntimeId'].OutputValue" --output text)
ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${PROJECT}-${ENVIRONMENT}-agentcore-role"

# Update the Runtime environment variables to include the Gateway URL and Memory ID
aws bedrock-agentcore-control update-agent-runtime \
  --agent-runtime-id "${AGENT_RUNTIME_ID}" \
  --region "${REGION}" \
  --agent-runtime-artifact "{\"containerConfiguration\":{\"containerUri\":\"${AGENT_ECR_URI}\"}}" \
  --role-arn "${ROLE_ARN}" \
  --network-configuration '{"networkMode":"PUBLIC"}' \
  --environment-variables "{\"REGION\":\"${REGION}\",\"GATEWAY_URL\":\"${GATEWAY_URL}\",\"MEMORY_ID\":\"${MEMORY_ID}\"}" \
  --query "status" --output text

echo "  Gateway URL: ${GATEWAY_URL}"
echo "  Memory ID: ${MEMORY_ID}"
echo "  Runtime ID: ${AGENT_RUNTIME_ID}"

# Step 4e: Wire backend Lambda to AgentCore Runtime
echo ""
echo "Step 4e: Connecting backend Lambda to AgentCore Runtime..."
AGENT_RUNTIME_ARN=$(aws cloudformation describe-stacks --stack-name "${STACK_NAME}-agentcore" --region "${REGION}" --query "Stacks[0].Outputs[?OutputKey=='AgentRuntimeArn'].OutputValue" --output text)

aws lambda update-function-configuration \
  --function-name "${PROJECT}-${ENVIRONMENT}-backend-api" \
  --region "${REGION}" \
  --environment "Variables={ENVIRONMENT=${ENVIRONMENT},PROJECT_NAME=${PROJECT},AGENT_RUNTIME_ARN=${AGENT_RUNTIME_ARN},AGENT_RUNTIME_ID=${AGENT_RUNTIME_ID},CLASSIFICATION_TABLE=${PROJECT}-${ENVIRONMENT}-classification-metadata,ENTITLEMENT_TABLE=${PROJECT}-${ENVIRONMENT}-entitlement-policies}" \
  --query "FunctionName" --output text

echo "  Backend Lambda connected to AgentCore Runtime: ${AGENT_RUNTIME_ID}"

# Step 5: Get stack outputs
echo ""
echo "Step 5: Retrieving stack outputs..."
CLOUDFRONT_DOMAIN=$(aws cloudformation describe-stacks \
  --stack-name "${STACK_NAME}" \
  --region "${REGION}" \
  --query "Stacks[0].Outputs[?OutputKey=='CloudFrontDomain'].OutputValue" \
  --output text)

API_ENDPOINT=$(aws cloudformation describe-stacks \
  --stack-name "${STACK_NAME}" \
  --region "${REGION}" \
  --query "Stacks[0].Outputs[?OutputKey=='ApiEndpoint'].OutputValue" \
  --output text)

FRONTEND_BUCKET="${PROJECT}-${ENVIRONMENT}-frontend-${ACCOUNT_ID}"

echo "  CloudFront: https://${CLOUDFRONT_DOMAIN}"
echo "  API:        ${API_ENDPOINT}"

# Step 6: Seed entitlement data
echo ""
echo "Step 6: Seeding entitlement policies..."
cd "${ROOT_DIR}"
python3 scripts/seed-entitlements.py "${ENVIRONMENT}"

# Step 7: Build and deploy frontend
echo ""
echo "Step 7: Building and deploying frontend..."
cd "${ROOT_DIR}/frontend"

# Clean install to avoid platform-specific dependency issues
rm -rf node_modules package-lock.json
npm install

npm run build

aws s3 sync dist/ "s3://${FRONTEND_BUCKET}/" \
  --region "${REGION}" \
  --delete

# Step 8: Upload sample data
echo ""
echo "Step 8: Uploading sample data to trigger classification pipeline..."
cd "${ROOT_DIR}"
bash scripts/upload-sample-data.sh "${ENVIRONMENT}"

echo ""
echo "=========================================="
echo "DEPLOYMENT COMPLETE!"
echo "=========================================="
echo ""
echo "Demo URL: https://${CLOUDFRONT_DOMAIN}"
echo "API URL:  ${API_ENDPOINT}"
echo ""
echo "What was deployed (single CloudFormation stack):"
echo "  - S3 buckets (landing, processed, frontend)"
echo "  - DynamoDB (classification metadata, entitlements)"
echo "  - OpenSearch Serverless (vector search)"
echo "  - Lambda functions (extraction, classification, tool Lambdas, backend API)"
echo "  - AgentCore Runtime (Strands agent with Claude)"
echo "  - AgentCore Gateway + Policy Engine (Guardrails enforcement)"
echo "  - AgentCore Memory (short-term session history)"
echo "  - CloudFront + API Gateway (frontend hosting)"
echo "  - KMS, CloudWatch (encryption, monitoring)"
echo ""
echo "Next steps:"
echo "  1. Wait ~90 seconds for classification pipeline to process sample PDFs"
echo "  2. Open the demo URL in your browser"
echo "  3. Select user personas and ask questions"
echo ""
echo "Demo users:"
echo "  - Alice Chen (PM): Full MNPI access, no PII"
echo "  - Bob Martinez (Analyst): MNPI only for ACME/TechStart"
echo "  - Carol Davis (Compliance): Full access including PII"
echo "  - Dave Wilson (Intern): Internal level only"
echo "  - Eve Johnson (HR): PII access, no MNPI"
echo "=========================================="
