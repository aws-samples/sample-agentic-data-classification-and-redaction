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

PROCESSED_BUCKET = os.environ["PROCESSED_BUCKET"]
CLASSIFICATION_TABLE = os.environ["CLASSIFICATION_TABLE"]
BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6")
OPENSEARCH_ENDPOINT = os.environ.get("OPENSEARCH_ENDPOINT", "")
OPENSEARCH_INDEX = os.environ.get("OPENSEARCH_INDEX", "classified-content")
REGION = "us-east-1"
EMBEDDING_MODEL_ID = "amazon.titan-embed-text-v2:0"

table = dynamodb.Table(CLASSIFICATION_TABLE)

CLASSIFICATION_PROMPT = """You are a data classification system for a financial services firm. Analyze the following content and return a JSON classification.

You MUST assess three dimensions:

1. **MNPI Assessment** — Is this Material Non-Public Information?
   MNPI means the content itself contains material, non-public financial information that could move a stock price or violate securities regulations if traded upon.
   
   IS MNPI:
   - Specific earnings figures not yet publicly announced
   - Undisclosed M&A activity, deal terms, or acquisition targets
   - Client portfolio positions or upcoming allocation changes
   - Revenue guidance, buyback plans, or financial projections not yet filed/disclosed
   - Expert insights revealing undisclosed company financials or strategic plans
   
   IS NOT MNPI:
   - Administrative references to compliance processes (e.g., "employee needs wall-crossing", "complete compliance training")
   - General HR records, onboarding documents, or personnel files — even if they mention company names in an administrative context
   - Internal scheduling, team standups, or process discussions that don't reveal financial details
   - Publicly available news articles, press releases, or published research
   
   Key distinction: A document that MENTIONS a company name is not MNPI. A document that reveals UNDISCLOSED FINANCIAL DETAILS about that company is MNPI.
   
   - mnpi: boolean (true ONLY if the content itself contains material non-public financial information)
   - mnpi_confidence: float 0.0-1.0 (how confident you are)
   - mnpi_entities: list of strings (companies/entities the MNPI relates to — only include if actual MNPI about them is present)
   - mnpi_reasoning: string (brief explanation)

2. **PII Detection** — What personally identifiable information is present?
   Types to detect: email_address, phone_number, ssn, name, address, financial_account,
   date_of_birth, ip_address, credit_card
   - pii_detected: boolean (true if any PII found)
   - pii_types: list of strings (types of PII found)
   - pii_entities: list of objects with {type, value, location} for each PII item found

3. **Security Level** — What is the appropriate security classification?
   - Public: No restrictions, publicly available information
   - Internal: Firm employees only, general business information
   - Confidential: Need-to-know basis, sensitive business or personal details (e.g., HR records, salary info)
   - Restricted: Named individuals only, highly sensitive (contains actual MNPI, client-specific deal terms)
   - security_level: string (one of Public, Internal, Confidential, Restricted)
   - security_reasoning: string (brief explanation)

Content to classify:
---
{content}
---

Return ONLY valid JSON with this exact structure (no markdown, no explanation outside JSON):
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


def classify_content(text):
    """Call Bedrock Claude to classify the content."""
    prompt = CLASSIFICATION_PROMPT.replace("{content}", text)

    response = bedrock.converse(
        modelId=BEDROCK_MODEL_ID,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 2048, "temperature": 0.0},
    )

    output_message = response["output"]["message"]
    response_text = ""
    for content_block in output_message["content"]:
        if "text" in content_block:
            response_text += content_block["text"]

    try:
        response_text = response_text.strip()
        if response_text.startswith("```"):
            response_text = response_text.split("```")[1]
            if response_text.startswith("json"):
                response_text = response_text[4:]
            response_text = response_text.strip()

        classification = json.loads(response_text)
    except json.JSONDecodeError as e:
        print(f"Failed to parse classification response: {e}")
        print(f"Raw response: {response_text[:500]}")
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
