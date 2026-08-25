"""
Data Retrieval Tool Lambda
AgentCore Gateway target. Retrieves content from OpenSearch with:
- Security level access control (fail-closed)
- MNPI entitlement enforcement (paragraph-level redaction)
- PII masking via Bedrock Guardrails ApplyGuardrail API (ANONYMIZE action)
  Replaces PII with {EMAIL}, {NAME}, {PHONE}, {SSN}, {ADDRESS} placeholders
"""

import json
import os
from decimal import Decimal

import boto3
import requests
from requests_aws4auth import AWS4Auth

dynamodb = boto3.resource("dynamodb")

CLASSIFICATION_TABLE = os.environ["CLASSIFICATION_TABLE"]
ENTITLEMENT_TABLE = os.environ["ENTITLEMENT_TABLE"]
OPENSEARCH_ENDPOINT = os.environ.get("OPENSEARCH_ENDPOINT", "")
OPENSEARCH_INDEX = os.environ.get("OPENSEARCH_INDEX", "classified-content")
GUARDRAIL_ID = os.environ.get("GUARDRAIL_ID", "")
GUARDRAIL_VERSION = os.environ.get("GUARDRAIL_VERSION", "DRAFT")
REGION = "us-east-1"

classification_table = dynamodb.Table(CLASSIFICATION_TABLE)
entitlement_table = dynamodb.Table(ENTITLEMENT_TABLE)


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def lambda_handler(event, context):
    """Retrieve content with MNPI entitlement enforcement."""
    body = parse_request(event)

    content_id = body.get("content_id")
    user_id = body.get("user_id", "anonymous")

    if not content_id:
        return response(400, {"error": "content_id is required"})

    print(f"Data retrieval: content_id={content_id}, user_id={user_id}")

    # 1. Get classification metadata (fail-closed)
    classification = get_classification(content_id)
    if classification is None:
        print(f"BLOCKED: No classification metadata for content_id={content_id}")
        return response(
            403,
            {
                "error": "Access denied",
                "reason": "Content not classified (fail-closed policy)",
                "content_id": content_id,
            },
        )

    # 2. Get user entitlements
    entitlement = get_entitlement(user_id)

    # 3. Check security level access
    user_max_level = entitlement.get("max_security_level", "Public")
    content_level = classification.get("security_level", "Restricted")

    if not has_security_access(user_max_level, content_level):
        print(
            f"BLOCKED: User {user_id} (level={user_max_level}) "
            f"cannot access content at level={content_level}"
        )
        return response(
            403,
            {
                "error": "Access denied",
                "reason": f"User clearance ({user_max_level}) insufficient for content ({content_level})",
                "content_id": content_id,
            },
        )

    # 4. Retrieve content from OpenSearch
    text = get_content_from_opensearch(content_id)
    if not text:
        return response(404, {"error": "Content not found in index"})

    # 5. Apply MNPI redaction based on user wall-crossing
    if classification.get("mnpi", False):
        mnpi_entities = classification.get("mnpi_entities", [])
        user_cleared_entities = entitlement.get("mnpi_cleared_entities", [])

        blocked_entities = [
            e for e in mnpi_entities if e not in user_cleared_entities
        ]

        if blocked_entities:
            text = redact_mnpi(text, blocked_entities)
            print(
                f"REDACTED MNPI: User {user_id} not cleared for {blocked_entities}"
            )

    # 6. Apply PII masking via Bedrock Guardrails (ANONYMIZE action)
    #    Replaces PII tokens with {EMAIL}, {NAME}, {PHONE} etc. placeholders
    if classification.get("pii_detected", False):
        has_pii_access = get_entitlement(user_id).get("pii_access", False)
        if not has_pii_access:
            text = apply_pii_guardrail(text)

    # 7. Return content (the 'text' field is what Guardrails evaluates for PII)
    return response(
        200,
        {
            "content_id": content_id,
            "text": text,
            "classification": {
                "mnpi": classification.get("mnpi", False),
                "security_level": content_level,
                "pii_detected": classification.get("pii_detected", False),
            },
            "access_decision": "allow",
            "user_id": user_id,
        },
    )


def get_content_from_opensearch(content_id):
    """Fetch full_text for a content_id from OpenSearch."""
    if not OPENSEARCH_ENDPOINT:
        return ""

    endpoint = OPENSEARCH_ENDPOINT.replace("https://", "")
    credentials = boto3.Session().get_credentials()
    auth = AWS4Auth(
        credentials.access_key,
        credentials.secret_key,
        REGION,
        "aoss",
        session_token=credentials.token,
    )

    search_body = {
        "query": {"term": {"content_id": content_id}},
        "_source": ["full_text"],
    }

    url = f"https://{endpoint}/{OPENSEARCH_INDEX}/_search"
    headers = {"Content-Type": "application/json"}

    try:
        r = requests.post(url, auth=auth, json=search_body, headers=headers)
        if r.status_code == 200:
            hits = r.json().get("hits", {}).get("hits", [])
            if hits:
                return hits[0].get("_source", {}).get("full_text", "")
        return ""
    except Exception as e:
        print(f"OpenSearch fetch error: {e}")
        return ""


def parse_request(event):
    """Parse request body from various invocation methods."""
    if "body" in event:
        body = event["body"]
        if isinstance(body, str):
            return json.loads(body)
        return body
    return event


def get_classification(content_id):
    """Get classification metadata from DynamoDB."""
    try:
        result = classification_table.get_item(Key={"content_id": content_id})
        return result.get("Item")
    except Exception as e:
        print(f"Error getting classification: {e}")
        return None


def get_entitlement(user_id):
    """Get user entitlement policy from DynamoDB."""
    try:
        result = entitlement_table.get_item(Key={"principal_id": user_id})
        return result.get("Item", default_entitlement())
    except Exception as e:
        print(f"Error getting entitlement: {e}")
        return default_entitlement()


def default_entitlement():
    """Return the most restrictive default entitlement."""
    return {
        "principal_id": "default",
        "max_security_level": "Public",
        "mnpi_cleared_entities": [],
        "pii_access": False,
    }


SECURITY_LEVELS = {"Public": 0, "Internal": 1, "Confidential": 2, "Restricted": 3}


def has_security_access(user_level, content_level):
    """Check if user's clearance level is sufficient for the content."""
    user_rank = SECURITY_LEVELS.get(user_level, 0)
    content_rank = SECURITY_LEVELS.get(content_level, 3)
    return user_rank >= content_rank


def redact_mnpi(text, blocked_entities):
    """Redact MNPI-bearing paragraphs for entities the user is not cleared for."""
    import re

    lines = text.split("\n")
    paragraphs = []
    current_block = []

    for line in lines:
        stripped = line.strip()
        is_boundary = (
            stripped == "" or
            stripped.startswith("ANALYST:") or
            stripped.startswith("EXPERT:") or
            bool(re.match(r'^\d+\.', stripped)) or
            stripped.startswith("---")
        )
        if is_boundary and current_block:
            paragraphs.append("\n".join(current_block))
            current_block = []
        if stripped:
            current_block.append(line)

    if current_block:
        paragraphs.append("\n".join(current_block))

    redacted_paragraphs = []
    for para in paragraphs:
        redacted = False
        for entity in blocked_entities:
            entity_lower = entity.lower()
            variants = [entity_lower]
            first_word = entity_lower.split()[0]
            if first_word != entity_lower and len(first_word) > 3:
                variants.append(first_word)
            for suffix in [" inc.", " inc", " corp", " corp.", " industries", " systems"]:
                if entity_lower.endswith(suffix):
                    variants.append(entity_lower[: -len(suffix)])

            if any(v in para.lower() for v in variants):
                redacted_paragraphs.append(f"[MNPI REDACTED - not cleared for {entity}]")
                redacted = True
                break

        if not redacted:
            redacted_paragraphs.append(para)

    return "\n".join(redacted_paragraphs)


def apply_pii_guardrail(text):
    """Mask PII in text using Bedrock Guardrails ApplyGuardrail API.

    The Guardrail is configured with ANONYMIZE action for PII entities,
    replacing detected PII with placeholders like {EMAIL}, {NAME}, {PHONE}, etc.
    This is ML-based detection (not regex) and catches context-dependent PII.

    Fail-closed: if the Guardrail is not configured or the API call fails,
    returns an error message instead of unmasked text to prevent PII leakage.
    """
    if not GUARDRAIL_ID:
        print("ERROR: GUARDRAIL_ID not configured - refusing to return unmasked PII")
        return "[Content withheld: PII protection service unavailable]"

    try:
        bedrock_runtime = boto3.client("bedrock-runtime", region_name=REGION)
        response = bedrock_runtime.apply_guardrail(
            guardrailIdentifier=GUARDRAIL_ID,
            guardrailVersion=GUARDRAIL_VERSION,
            source="OUTPUT",
            content=[{"text": {"text": text}}],
        )

        # Extract the masked output
        action = response.get("action", "NONE")
        outputs = response.get("outputs", [])

        if outputs:
            # The first output contains the masked text
            masked_text = outputs[0].get("text", text)
            print(f"Guardrail action: {action}, PII entities masked")
            return masked_text
        else:
            # No outputs means guardrail didn't modify (unlikely with ANONYMIZE)
            return text

    except Exception as e:
        print(f"ERROR: Guardrail API failed: {e}. Refusing to return unmasked PII.")
        return "[Content withheld: PII protection service error]"


def response(status_code, body):
    """Format Lambda response."""
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, cls=DecimalEncoder),
    }
