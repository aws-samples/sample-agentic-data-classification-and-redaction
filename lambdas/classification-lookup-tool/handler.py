"""
Classification Lookup Tool Lambda
AgentCore Gateway target. Returns classification metadata for a given content item.
"""

import json
import os
from decimal import Decimal

import boto3

dynamodb = boto3.resource("dynamodb")

CLASSIFICATION_TABLE = os.environ["CLASSIFICATION_TABLE"]
table = dynamodb.Table(CLASSIFICATION_TABLE)


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def lambda_handler(event, context):
    """Look up classification metadata for a content item."""
    body = parse_request(event)
    content_id = body.get("content_id")

    if not content_id:
        return response(400, {"error": "content_id is required"})

    print(f"Classification lookup: content_id={content_id}")

    try:
        result = table.get_item(Key={"content_id": content_id})
        item = result.get("Item")

        if not item:
            return response(
                404,
                {
                    "error": "Classification not found",
                    "content_id": content_id,
                    "status": "unclassified",
                },
            )

        # Return classification metadata (excluding internal fields)
        classification = {
            "content_id": item.get("content_id"),
            "source_type": item.get("source_type"),
            "filename": item.get("filename"),
            "classified_at": item.get("classified_at"),
            "classification_source": item.get("classification_source"),
            "mnpi": item.get("mnpi", False),
            "mnpi_confidence": float(item.get("mnpi_confidence", 0)),
            "mnpi_entities": item.get("mnpi_entities", []),
            "mnpi_reasoning": item.get("mnpi_reasoning", ""),
            "pii_detected": item.get("pii_detected", False),
            "pii_types": item.get("pii_types", []),
            "security_level": item.get("security_level", "Restricted"),
            "security_reasoning": item.get("security_reasoning", ""),
            "text_length": item.get("text_length", 0),
        }

        return response(200, classification)

    except Exception as e:
        print(f"Lookup error: {e}")
        return response(500, {"error": "Internal error during classification lookup"})


def parse_request(event):
    """Parse request body."""
    if "body" in event:
        body = event["body"]
        if isinstance(body, str):
            return json.loads(body)
        return body
    return event


def response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, cls=DecimalEncoder),
    }
