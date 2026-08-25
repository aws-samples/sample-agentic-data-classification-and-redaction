#!/bin/bash
# Upload sample PDF data to the S3 landing bucket to trigger the classification pipeline.
# The pipeline: S3 upload → EventBridge → Text Extraction (Textract) → SQS → Classification (Bedrock) → DynamoDB
# Usage: ./upload-sample-data.sh [environment]

set -e

ENVIRONMENT="${1:-demo}"
PROJECT="data-classification"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
LANDING_BUCKET="${PROJECT}-${ENVIRONMENT}-landing-${ACCOUNT_ID}"
REGION="us-east-1"

echo "=========================================="
echo "Uploading sample PDF documents to: ${LANDING_BUCKET}"
echo "Environment: ${ENVIRONMENT}"
echo "Region: ${REGION}"
echo "=========================================="
echo ""
echo "Pipeline: S3 Upload -> EventBridge -> Textract Extraction -> SQS -> Bedrock Classification -> DynamoDB"
echo ""

SAMPLE_DIR="$(dirname "$0")/../sample-data"

# Generate PDFs if they don't exist
if [ ! -f "${SAMPLE_DIR}/email-mnpi-acme.pdf" ]; then
  echo "Generating sample PDFs..."
  python3 "$(dirname "$0")/generate-sample-pdfs.py"
  echo ""
fi

# Upload email with MNPI (PDF)
echo "1. Uploading MNPI email PDF (ACME Corp Q3 earnings preview)..."
aws s3 cp "${SAMPLE_DIR}/email-mnpi-acme.pdf" \
  "s3://${LANDING_BUCKET}/raw/email/2026-07-15/email-mnpi-acme.pdf" \
  --region "${REGION}" \
  --content-type "application/pdf"

# Upload expert call transcript (PDF)
echo "2. Uploading expert call transcript PDF (GlobalTech/NovaTech MNPI)..."
aws s3 cp "${SAMPLE_DIR}/transcript-expert-call.pdf" \
  "s3://${LANDING_BUCKET}/raw/transcript/2026-07-20/transcript-expert-call.pdf" \
  --region "${REGION}" \
  --content-type "application/pdf"

# Upload public web article (PDF)
echo "3. Uploading public news article PDF (AWS announcement - no sensitive data)..."
aws s3 cp "${SAMPLE_DIR}/web-article-public.pdf" \
  "s3://${LANDING_BUCKET}/raw/web/2026-07-22/web-article-public.pdf" \
  --region "${REGION}" \
  --content-type "application/pdf"

# Upload HR document with PII (PDF)
echo "4. Uploading HR onboarding record PDF (heavy PII - SSN, address, accounts)..."
aws s3 cp "${SAMPLE_DIR}/hr-document-pii.pdf" \
  "s3://${LANDING_BUCKET}/raw/document/2026-07-25/hr-document-pii.pdf" \
  --region "${REGION}" \
  --content-type "application/pdf"

# Upload Slack export (PDF)
echo "5. Uploading Slack channel export PDF (internal comms - no MNPI)..."
aws s3 cp "${SAMPLE_DIR}/slack-internal.pdf" \
  "s3://${LANDING_BUCKET}/raw/slack/2026-07-18/slack-internal.pdf" \
  --region "${REGION}" \
  --content-type "application/pdf"

echo ""
echo "=========================================="
echo "All 5 PDF documents uploaded successfully!"
echo ""
echo "The pipeline will now:"
echo "  1. EventBridge detects the S3 uploads"
echo "  2. Text Extraction Lambda uses Textract to extract text from PDFs"
echo "  3. Extracted text is queued via SQS"
echo "  4. Classification Lambda calls Bedrock Claude to classify content"
echo "  5. Classification metadata is stored in DynamoDB"
echo ""
echo "Check DynamoDB table for results in ~60-90 seconds."
echo "=========================================="
