"""
Classification Lambda
Triggered by SQS. Reads extracted text from S3, calls Bedrock Claude
for MNPI/PII/security classification, embeds text with Titan Embeddings,
and indexes to both DynamoDB and OpenSearch Serverless (k-NN).
"""

import json
import os
from datetime import datetime, timezone

import boto3
from opensearchpy import OpenSearch, RequestsAWSV4SignerAuth, AWSV4SignerAuth
import requests
from requests_aws4auth import AWS4Auth

s3 = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")
bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")
ssm = boto3.client("ssm", region_name="us-east-1")

PROCESSED_BUCKET = os.environ["PROCESSED_BUCKET"]
CLASSIFICATION_TABLE = os.environ["CLASSIFICATION_TABLE"]
BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6")
# SSM parameter holding the ARN of the externalized classification prompt in
# Bedrock Prompt Management. Populated post-deploy by
# scripts/seed-classification-prompt.py.
CLASSIFICATION_PROMPT_PARAM = os.environ.get("CLASSIFICATION_PROMPT_PARAM", "")
OPENSEARCH_ENDPOINT = os.environ.get("OPENSEARCH_ENDPOINT", "")
OPENSEARCH_INDEX = os.environ.get("OPENSEARCH_INDEX", "classified-content")
REGION = "us-east-1"
EMBEDDING_MODEL_ID = "amazon.titan-embed-text-v2:0"

table = dynamodb.Table(CLASSIFICATION_TABLE)

# Cache the resolved prompt ARN across warm invocations to avoid an SSM call
# on every message.
_prompt_arn_cache = None

# Trimmed, baked-in fallback prompt. This is ONLY used as a last resort if the
# externalized managed prompt cannot be resolved or rendered (e.g. SSM pointer
# missing or Bedrock Prompt Management unavailable). The authoritative,
# evolving MNPI rules live in the managed prompt (see
# scripts/seed-classification-prompt.py). This fallback must still emit the same
# JSON schema so downstream parsing works. It uses a single-brace {content}
# placeholder filled via str.replace().
FALLBACK_CLASSIFICATION_PROMPT = """You are a data classification system for a financial services firm. Analyze the content and return ONLY a JSON classification.

Assess three dimensions:
1. MNPI (Material Non-Public Information): true only if the content itself reveals undisclosed, price-sensitive financial information (unannounced earnings, undisclosed M&A/deal terms, client positions, unfiled guidance/buybacks, expert insights on undisclosed financials). Merely mentioning a company name, HR/administrative records, internal scheduling, or public news is NOT MNPI.
2. PII: detect email_address, phone_number, ssn, name, address, financial_account, date_of_birth, ip_address, credit_card.
3. Security level: Public, Internal, Confidential (HR/salary/sensitive personal or business), or Restricted (actual MNPI or client-specific deal terms).

Content to classify:
---
{content}
---

Return ONLY valid JSON with this exact structure (no markdown, no text outside JSON):
{
  "mnpi": false,
  "mnpi_confidence": 0.0,
  "mnpi_entities": [],
  "mnpi_reasoning": "",
  "pii_detected": false,
  "pii_types": [],
  "pii_entities": [],
  "security_level": "Public",
  "security_reasoning": ""
}"""


def lambda_handler(event, context):
    """Process SQS messages and classify content."""
    for record in event.get("Records", []):
        body = json.loads(record["body"])
        content_id = body["content_id"]
        processed_key = body["processed_key"]
        source_type = body.get("source_type", "unknown")
        filename = body.get("filename", "")

        print(f"Classifying content_id={content_id}, source={source_type}")

        try:
            # Read extracted text from S3
            response = s3.get_object(Bucket=PROCESSED_BUCKET, Key=processed_key)
            extracted_data = json.loads(response["Body"].read().decode("utf-8"))
            text = extracted_data["extracted_text"]

            # Truncate for classification (keep first 50K chars)
            classify_text = text
            max_chars = 50000
            if len(classify_text) > max_chars:
                classify_text = classify_text[:max_chars] + "\n\n[TRUNCATED]"

            # Call Bedrock for classification
            classification = classify_content(classify_text)

            # Write to DynamoDB
            write_classification_metadata(
                content_id=content_id,
                classification=classification,
                source_type=source_type,
                filename=filename,
                processed_key=processed_key,
                text_length=len(text),
            )

            # Embed and index to OpenSearch
            embed_and_index(
                content_id=content_id,
                text=text,
                filename=filename,
                source_type=source_type,
                classification=classification,
            )

            # Tag S3 object
            tag_s3_object(processed_key, classification)

            print(
                f"Classified content_id={content_id}: "
                f"mnpi={classification.get('mnpi')}, "
                f"pii={classification.get('pii_detected')}, "
                f"level={classification.get('security_level')}"
            )

        except Exception as e:
            print(f"Classification failed for content_id={content_id}: {e}")
            raise

    return {"statusCode": 200}


def get_prompt_arn():
    """Resolve the externalized classification prompt ARN from SSM (cached)."""
    global _prompt_arn_cache
    if _prompt_arn_cache is not None:
        return _prompt_arn_cache
    if not CLASSIFICATION_PROMPT_PARAM:
        return None
    try:
        resp = ssm.get_parameter(Name=CLASSIFICATION_PROMPT_PARAM)
        _prompt_arn_cache = resp["Parameter"]["Value"]
        return _prompt_arn_cache
    except Exception as e:
        print(f"Could not read classification prompt ARN from SSM "
              f"({CLASSIFICATION_PROMPT_PARAM}): {e}")
        return None


def _extract_response_text(response):
    """Concatenate the text blocks from a Converse response."""
    output_message = response["output"]["message"]
    response_text = ""
    for content_block in output_message["content"]:
        if "text" in content_block:
            response_text += content_block["text"]
    return response_text


def invoke_managed_prompt(prompt_arn, text):
    """Invoke the externalized prompt (DRAFT) from Bedrock Prompt Management.

    The prompt owns the model ID and inference config, so we pass neither here —
    Converse rejects inferenceConfig when a managed prompt is used.
    """
    response = bedrock.converse(
        modelId=prompt_arn,
        promptVariables={"content": {"text": text}},
    )
    return _extract_response_text(response)


def invoke_fallback_prompt(text):
    """Last-resort classification using the baked-in prompt and direct model call."""
    prompt = FALLBACK_CLASSIFICATION_PROMPT.replace("{content}", text)
    response = bedrock.converse(
        modelId=BEDROCK_MODEL_ID,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 2048, "temperature": 0.0},
    )
    return _extract_response_text(response)


def classify_content(text):
    """Classify content via the managed prompt, falling back to the baked-in prompt.

    Resolution order:
      1. Externalized managed prompt (Bedrock Prompt Management, auto-live DRAFT).
      2. Baked-in fallback prompt if the managed prompt can't be resolved/rendered.
    On a response that can't be parsed as JSON, fail closed to Restricted.
    """
    response_text = None

    prompt_arn = get_prompt_arn()
    if prompt_arn:
        try:
            response_text = invoke_managed_prompt(prompt_arn, text)
        except Exception as e:
            print(f"Managed prompt invocation failed ({e}); "
                  f"using baked-in fallback prompt")

    if response_text is None:
        response_text = invoke_fallback_prompt(text)

    return parse_classification(response_text)


def parse_classification(response_text):
    """Parse the model's JSON response; fail closed to Restricted on error."""
    try:
        response_text = response_text.strip()
        if response_text.startswith("```"):
            response_text = response_text.split("```")[1]
            if response_text.startswith("json"):
                response_text = response_text[4:]
            response_text = response_text.strip()

        classification = json.loads(response_text)
    except (json.JSONDecodeError, AttributeError, IndexError) as e:
        print(f"Failed to parse classification response: {e}")
        print(f"Raw response: {str(response_text)[:500]}")
        classification = {
            "mnpi": False,
            "mnpi_confidence": 0.0,
            "mnpi_entities": [],
            "mnpi_reasoning": "Classification parse error - defaulting to safe",
            "pii_detected": False,
            "pii_types": [],
            "pii_entities": [],
            "security_level": "Restricted",
            "security_reasoning": "Parse error - defaulting to most restrictive",
        }

    return classification


def embed_text(text):
    """Generate embedding vector using Titan Embeddings V2."""
    # Truncate to 8K tokens (~32K chars) for embedding model limit
    embed_input = text[:32000]

    response = bedrock.invoke_model(
        modelId=EMBEDDING_MODEL_ID,
        body=json.dumps({
            "inputText": embed_input,
            "dimensions": 1024,
            "normalize": True,
        }),
    )

    response_body = json.loads(response["body"].read())
    return response_body["embedding"]


def embed_and_index(content_id, text, filename, source_type, classification):
    """Embed document text and index to OpenSearch Serverless."""
    if not OPENSEARCH_ENDPOINT:
        print("OPENSEARCH_ENDPOINT not set, skipping indexing")
        return

    try:
        # Generate embedding
        embedding = embed_text(text)

        # Prepare document for indexing
        doc = {
            "content_vector": embedding,
            "content_id": content_id,
            "filename": filename,
            "source_type": source_type,
            "security_level": classification.get("security_level", "Restricted"),
            "mnpi": classification.get("mnpi", False),
            "mnpi_entities": classification.get("mnpi_entities", []),
            "pii_detected": classification.get("pii_detected", False),
            "pii_types": classification.get("pii_types", []),
            "full_text": text,
            "text_preview": text[:500],
            "indexed_at": datetime.now(timezone.utc).isoformat(),
        }

        # Index to OpenSearch using SigV4 auth
        endpoint = OPENSEARCH_ENDPOINT.replace("https://", "")
        credentials = boto3.Session().get_credentials()
        auth = AWS4Auth(
            credentials.access_key,
            credentials.secret_key,
            REGION,
            "aoss",
            session_token=credentials.token,
        )

        # Ensure index exists (create if not)
        ensure_index_exists(endpoint, auth)

        # Index the document (OpenSearch Serverless doesn't support doc IDs in path)
        url = f"https://{endpoint}/{OPENSEARCH_INDEX}/_doc"
        headers = {"Content-Type": "application/json"}
        r = requests.post(url, auth=auth, json=doc, headers=headers)

        if r.status_code in (200, 201):
            print(f"Indexed content_id={content_id} to OpenSearch")
        else:
            print(f"OpenSearch indexing failed: {r.status_code} {r.text[:200]}")

    except Exception as e:
        print(f"Embedding/indexing failed for {content_id}: {e}")
        # Don't raise — classification still succeeded, indexing is supplementary


def ensure_index_exists(endpoint, auth):
    """Create the k-NN index if it doesn't exist."""
    url = f"https://{endpoint}/{OPENSEARCH_INDEX}"
    headers = {"Content-Type": "application/json"}

    try:
        r = requests.get(url, auth=auth, headers=headers)
        if r.status_code == 200:
            return  # Index exists
    except Exception:
        pass

    # Create index with k-NN mapping
    index_body = {
        "settings": {
            "index": {
                "knn": True,
                "knn.algo_param.ef_search": 512,
            }
        },
        "mappings": {
            "properties": {
                "content_vector": {
                    "type": "knn_vector",
                    "dimension": 1024,
                    "method": {
                        "name": "hnsw",
                        "space_type": "innerproduct",
                        "engine": "faiss",
                        "parameters": {"ef_construction": 512, "m": 16},
                    },
                },
                "content_id": {"type": "keyword"},
                "filename": {"type": "text"},
                "source_type": {"type": "keyword"},
                "security_level": {"type": "keyword"},
                "mnpi": {"type": "boolean"},
                "mnpi_entities": {"type": "keyword"},
                "pii_detected": {"type": "boolean"},
                "pii_types": {"type": "keyword"},
                "full_text": {"type": "text", "index": False},
                "text_preview": {"type": "text"},
                "indexed_at": {"type": "date"},
            }
        },
    }

    r = requests.put(url, auth=auth, json=index_body, headers=headers)
    if r.status_code in (200, 201):
        print(f"Created OpenSearch index: {OPENSEARCH_INDEX}")
    else:
        print(f"Failed to create index: {r.status_code} {r.text[:200]}")


def write_classification_metadata(
    content_id, classification, source_type, filename, processed_key, text_length
):
    """Write classification metadata to DynamoDB."""
    now = datetime.now(timezone.utc).isoformat()

    item = {
        "content_id": content_id,
        "source_type": source_type,
        "filename": filename,
        "processed_s3_key": processed_key,
        "text_length": text_length,
        "classified_at": now,
        "classification_source": "bedrock-claude",
        "model_id": BEDROCK_MODEL_ID,
        "mnpi": classification.get("mnpi", False),
        "mnpi_confidence": str(classification.get("mnpi_confidence", 0.0)),
        "mnpi_entities": classification.get("mnpi_entities", []),
        "mnpi_reasoning": classification.get("mnpi_reasoning", ""),
        "pii_detected": classification.get("pii_detected", False),
        "pii_types": classification.get("pii_types", []),
        "pii_entities": json.dumps(classification.get("pii_entities", [])),
        "security_level": classification.get("security_level", "Restricted"),
        "security_reasoning": classification.get("security_reasoning", ""),
    }

    table.put_item(Item=item)


def tag_s3_object(processed_key, classification):
    """Tag the S3 object with classification summary."""
    try:
        tags = {
            "mnpi": str(classification.get("mnpi", False)).lower(),
            "security_level": classification.get("security_level", "Restricted"),
            "pii_detected": str(classification.get("pii_detected", False)).lower(),
        }

        s3.put_object_tagging(
            Bucket=PROCESSED_BUCKET,
            Key=processed_key,
            Tagging={
                "TagSet": [{"Key": k, "Value": v} for k, v in tags.items()]
            },
        )
    except Exception as e:
        print(f"Failed to tag S3 object: {e}")
