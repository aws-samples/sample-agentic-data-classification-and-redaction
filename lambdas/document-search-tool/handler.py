"""
Document Search Tool Lambda
AgentCore Gateway target. Uses semantic search (k-NN) via OpenSearch Serverless
to find documents by meaning, with entitlement-based filtering.
"""

import json
import os
from decimal import Decimal

import boto3
import requests
from requests_aws4auth import AWS4Auth

dynamodb = boto3.resource("dynamodb")
bedrock = boto3.client("bedrock-runtime", region_name="us-east-1")

CLASSIFICATION_TABLE = os.environ["CLASSIFICATION_TABLE"]
ENTITLEMENT_TABLE = os.environ["ENTITLEMENT_TABLE"]
OPENSEARCH_ENDPOINT = os.environ.get("OPENSEARCH_ENDPOINT", "")
OPENSEARCH_INDEX = os.environ.get("OPENSEARCH_INDEX", "classified-content")
REGION = "us-east-1"
EMBEDDING_MODEL_ID = "amazon.titan-embed-text-v2:0"

classification_table = dynamodb.Table(CLASSIFICATION_TABLE)
entitlement_table = dynamodb.Table(ENTITLEMENT_TABLE)


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def lambda_handler(event, context):
    """Semantic search for documents with classification-aware filtering."""
    body = parse_request(event)

    query = body.get("query", "")
    user_id = body.get("user_id", "anonymous")
    max_results = body.get("max_results", 5)

    if not query:
        return response(400, {"error": "query is required"})

    print(f"Semantic search: query='{query}', user_id={user_id}")

    # 1. Get user entitlements
    entitlement = get_entitlement(user_id)
    user_max_level = entitlement.get("max_security_level", "Public")
    allowed_levels = get_allowed_levels(user_max_level)

    # 2. Embed the query
    query_vector = embed_text(query)

    # 3. k-NN search with security level filter
    search_results = knn_search(query_vector, allowed_levels, max_results)

    # 4. Build response with classification info
    results = []
    for hit in search_results:
        source = hit.get("_source", {})
        results.append({
            "content_id": source.get("content_id", ""),
            "filename": source.get("filename", ""),
            "source_type": source.get("source_type", ""),
            "security_level": source.get("security_level", ""),
            "mnpi": source.get("mnpi", False),
            "mnpi_entities": source.get("mnpi_entities", []),
            "pii_detected": source.get("pii_detected", False),
            "text_preview": source.get("text_preview", ""),
            "relevance_score": hit.get("_score", 0),
        })

    print(f"Semantic search returned {len(results)} results for user {user_id}")

    return response(200, {
        "query": query,
        "user_id": user_id,
        "total_results": len(results),
        "results": results,
    })


def embed_text(text):
    """Generate embedding vector using Titan Embeddings V2."""
    embed_input = text[:32000]

    resp = bedrock.invoke_model(
        modelId=EMBEDDING_MODEL_ID,
        body=json.dumps({
            "inputText": embed_input,
            "dimensions": 1024,
            "normalize": True,
        }),
    )

    response_body = json.loads(resp["body"].read())
    return response_body["embedding"]


def knn_search(query_vector, allowed_levels, k=5):
    """Perform k-NN search on OpenSearch Serverless with security level filter."""
    if not OPENSEARCH_ENDPOINT:
        print("OPENSEARCH_ENDPOINT not set, falling back to empty results")
        return []

    endpoint = OPENSEARCH_ENDPOINT.replace("https://", "")
    credentials = boto3.Session().get_credentials()
    auth = AWS4Auth(
        credentials.access_key,
        credentials.secret_key,
        REGION,
        "aoss",
        session_token=credentials.token,
    )

    # Build k-NN query with post-filter for security level (NMSLIB doesn't support inline filters)
    search_body = {
        "size": 10,
        "query": {
            "knn": {
                "content_vector": {
                    "vector": query_vector,
                    "k": 10,
                }
            }
        },
        "post_filter": {
            "terms": {
                "security_level": allowed_levels
            }
        },
        "_source": {
            "excludes": ["content_vector"]
        }
    }

    url = f"https://{endpoint}/{OPENSEARCH_INDEX}/_search"
    headers = {"Content-Type": "application/json"}

    try:
        r = requests.post(url, auth=auth, json=search_body, headers=headers)
        if r.status_code == 200:
            data = r.json()
            return data.get("hits", {}).get("hits", [])
        else:
            print(f"OpenSearch search failed: {r.status_code} {r.text[:200]}")
            return []
    except Exception as e:
        print(f"OpenSearch search error: {e}")
        return []


def get_entitlement(user_id):
    """Get user entitlement from DynamoDB."""
    try:
        result = entitlement_table.get_item(Key={"principal_id": user_id})
        return result.get("Item", default_entitlement())
    except Exception:
        return default_entitlement()


def default_entitlement():
    return {
        "principal_id": "default",
        "max_security_level": "Public",
        "mnpi_cleared_entities": [],
        "pii_access": False,
    }


SECURITY_LEVELS = {"Public": 0, "Internal": 1, "Confidential": 2, "Restricted": 3}


def get_allowed_levels(user_level):
    user_rank = SECURITY_LEVELS.get(user_level, 0)
    return [level for level, rank in SECURITY_LEVELS.items() if rank <= user_rank]


def parse_request(event):
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
