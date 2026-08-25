"""
Backend API Lambda
Thin proxy between the React frontend and AgentCore Runtime.
- /chat → invokes AgentCore Runtime (agent does all reasoning, search, redaction)
- /users, /classifications, /health → simple DynamoDB reads for dashboard
"""

import json
import os
import uuid
from decimal import Decimal

import boto3

dynamodb = boto3.resource("dynamodb")
agentcore = boto3.client("bedrock-agentcore", region_name="us-east-1")

CLASSIFICATION_TABLE = os.environ.get("CLASSIFICATION_TABLE", "")
ENTITLEMENT_TABLE = os.environ.get("ENTITLEMENT_TABLE", "")
AGENT_RUNTIME_ARN = os.environ.get("AGENT_RUNTIME_ARN", "")
AGENT_RUNTIME_ID = os.environ.get("AGENT_RUNTIME_ID", "")
REGION = "us-east-1"

classification_table = dynamodb.Table(CLASSIFICATION_TABLE) if CLASSIFICATION_TABLE else None
entitlement_table = dynamodb.Table(ENTITLEMENT_TABLE) if ENTITLEMENT_TABLE else None


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


def lambda_handler(event, context):
    """Route API requests."""
    raw_path = event.get("rawPath", "/")
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    path = raw_path.replace("/api", "", 1) if raw_path.startswith("/api") else raw_path

    print(f"Request: {method} {path}")

    if path == "/chat" and method == "POST":
        return handle_chat(event)
    elif path == "/chat/ws-url" and method == "POST":
        return handle_ws_url(event)
    elif path == "/classifications" and method == "GET":
        return handle_list_classifications(event)
    elif path.startswith("/classifications/") and method == "GET":
        content_id = path.split("/classifications/")[1]
        return handle_get_classification(content_id)
    elif path == "/entitlements" and method == "GET":
        return handle_list_entitlements(event)
    elif path == "/users" and method == "GET":
        return handle_list_users(event)
    elif path == "/health" and method == "GET":
        return api_response(200, {"status": "healthy"})
    else:
        return api_response(404, {"error": f"Not found: {method} {path}"})


# ============================================================
# Chat — Invoke AgentCore Runtime
# ============================================================

def handle_chat(event):
    """Invoke AgentCore Runtime with the user's message.

    The agent:
    - Receives the message + user context
    - Calls tools (search, retrieve, classify) via AgentCore Gateway
    - Guardrails at the gateway handle PII redaction
    - Tool Lambdas handle MNPI redaction based on entitlements
    - Agent reasons over the (redacted) results
    - Agent maintains session memory for multi-turn conversation
    - Returns a natural language response
    """
    body = parse_body(event)
    message = body.get("message", "")
    user_id = body.get("user_id", "anonymous")
    session_id = body.get("session_id", "")

    if not message:
        return api_response(400, {"error": "message is required"})

    # Generate session_id if not provided
    if not session_id:
        session_id = str(uuid.uuid4())

    # Get user entitlement for context (passed to agent so it can inform the user)
    entitlement = get_entitlement(user_id)

    # Build the prompt with user context so the agent knows who's asking
    contextualized_prompt = (
        f"[User: {user_id} | Security Level: {entitlement.get('max_security_level', 'Public')} | "
        f"MNPI Cleared: {entitlement.get('mnpi_cleared_entities', [])} | "
        f"PII Access: {entitlement.get('pii_access', False)}]\n\n"
        f"{message}"
    )

    # Invoke AgentCore Runtime
    try:
        agent_response = invoke_agent(contextualized_prompt, session_id, user_id)
    except Exception as e:
        print(f"AgentCore invocation failed: {e}")
        agent_response = f"I'm having trouble processing your request right now. Error: {str(e)}"

    return api_response(
        200,
        {
            "response": agent_response,
            "user_id": user_id,
            "session_id": session_id,
            "entitlement": {
                "max_security_level": entitlement.get("max_security_level", "Public"),
                "mnpi_cleared_entities": entitlement.get("mnpi_cleared_entities", []),
                "pii_access": entitlement.get("pii_access", False),
            },
        },
    )


def handle_ws_url(event):
    """Generate a pre-signed WebSocket URL for streaming chat.

    The frontend calls this endpoint to get a signed URL, then opens a
    WebSocket directly to AgentCore Runtime for real-time token streaming.
    """
    body = parse_body(event)
    user_id = body.get("user_id", "anonymous")
    session_id = body.get("session_id", "")

    if not session_id:
        session_id = str(uuid.uuid4())

    if not AGENT_RUNTIME_ARN:
        return api_response(503, {"error": "Agent runtime not configured"})

    try:
        # Generate a SigV4 pre-signed WebSocket URL
        ws_url = _generate_presigned_ws_url(AGENT_RUNTIME_ARN, session_id)

        # Get user entitlement for the prompt context
        entitlement = get_entitlement(user_id)

        return api_response(200, {
            "ws_url": ws_url,
            "session_id": session_id,
            "entitlement": {
                "max_security_level": entitlement.get("max_security_level", "Public"),
                "mnpi_cleared_entities": entitlement.get("mnpi_cleared_entities", []),
                "pii_access": entitlement.get("pii_access", False),
            },
        })
    except Exception as e:
        print(f"Failed to generate WebSocket URL: {e}")
        return api_response(500, {"error": f"Failed to generate streaming URL: {str(e)}"})


def _generate_presigned_ws_url(runtime_arn, session_id, expires=300):
    """Generate a SigV4 pre-signed WebSocket URL for AgentCore Runtime.

    Uses botocore's request signer to produce a correct SigV4 query-string URL.
    The URL format is:
    wss://bedrock-agentcore.<region>.amazonaws.com/runtimes/<arn>/ws?X-Amz-...
    """
    import urllib.parse
    from datetime import datetime, timezone
    from botocore.auth import SigV4QueryAuth
    from botocore.awsrequest import AWSRequest

    session = boto3.Session()
    credentials = session.get_credentials().get_frozen_credentials()

    host = f"bedrock-agentcore.{REGION}.amazonaws.com"
    encoded_arn = urllib.parse.quote(runtime_arn, safe='')
    path = f"/runtimes/{encoded_arn}/ws"

    # Build query params (session id passed as query param for WS)
    params = {}
    if session_id:
        params['X-Amzn-Bedrock-AgentCore-Runtime-Session-Id'] = session_id

    query_string = urllib.parse.urlencode(params) if params else ''
    url = f"https://{host}{path}"
    if query_string:
        url += f"?{query_string}"

    # Create an AWSRequest and sign it with SigV4 query auth
    request = AWSRequest(method='GET', url=url, headers={'host': host})

    signer = SigV4QueryAuth(credentials, 'bedrock-agentcore', REGION, expires=expires)
    signer.add_auth(request)

    # Convert https:// to wss:// for WebSocket
    signed_url = request.url.replace('https://', 'wss://', 1)
    return signed_url

def invoke_agent(prompt, session_id, user_id):
    """Invoke the AgentCore Runtime and collect the streaming response."""
    if not AGENT_RUNTIME_ARN:
        raise Exception(
            "AGENT_RUNTIME_ARN not configured. "
            "The AgentCore Runtime may still be deploying."
        )

    import json as json_lib

    payload = json_lib.dumps({
        "prompt": prompt,
        "session_id": session_id,
        "user_id": user_id,
    }).encode("utf-8")

    response = agentcore.invoke_agent_runtime(
        agentRuntimeArn=AGENT_RUNTIME_ARN,
        contentType="application/json",
        accept="application/json",
        payload=payload,
    )

    # Read the response body — the async generator entrypoint streams events
    result_body = response.get("response")
    if result_body:
        raw = result_body.read().decode("utf-8")

        # The streaming entrypoint returns newline-delimited events.
        # Extract text content from contentBlockDelta events.
        collected_text = ""
        for line in raw.split("\n"):
            line = line.strip()
            if not line or not line.startswith("data: "):
                continue
            payload_str = line[6:]  # strip "data: "
            try:
                event_data = json.loads(payload_str)
                # Look for text deltas in the stream events
                if isinstance(event_data, dict):
                    # Direct text event from stream_async
                    if "data" in event_data and isinstance(event_data["data"], str):
                        collected_text += event_data["data"]
                    # Bedrock Converse stream format
                    elif "event" in event_data:
                        evt = event_data["event"]
                        if "contentBlockDelta" in evt:
                            delta = evt["contentBlockDelta"].get("delta", {})
                            if "text" in delta:
                                collected_text += delta["text"]
            except (json.JSONDecodeError, TypeError):
                # Try parsing as a plain string event
                if isinstance(payload_str, str) and not payload_str.startswith("{"):
                    collected_text += payload_str

        if collected_text:
            return collected_text

        # Fallback: try parsing the whole response as a single JSON (non-streaming)
        try:
            data = json.loads(raw)
            agent_resp = data.get("response", data)
            if isinstance(agent_resp, dict):
                content = agent_resp.get("content", [])
                if content and isinstance(content, list):
                    texts = [c.get("text", "") for c in content if "text" in c]
                    return "\n".join(texts)
                return str(agent_resp)
            return str(agent_resp)
        except (json.JSONDecodeError, TypeError):
            return raw

    return "No response from agent."


# ============================================================
# Dashboard Endpoints (simple DynamoDB reads)
# ============================================================

def handle_list_classifications(event):
    """List all classification metadata for the dashboard."""
    try:
        result = classification_table.scan(Limit=50)
        items = result.get("Items", [])
        classifications = [
            {
                "content_id": item.get("content_id"),
                "filename": item.get("filename"),
                "source_type": item.get("source_type"),
                "classified_at": item.get("classified_at"),
                "mnpi": item.get("mnpi", False),
                "pii_detected": item.get("pii_detected", False),
                "security_level": item.get("security_level"),
                "mnpi_entities": item.get("mnpi_entities", []),
                "pii_types": item.get("pii_types", []),
            }
            for item in items
        ]
        return api_response(200, {"classifications": classifications, "total": len(classifications)})
    except Exception as e:
        print(f"Error listing classifications: {e}")
        return api_response(500, {"error": "Failed to list classifications"})


def handle_get_classification(content_id):
    """Get classification detail for a specific content item."""
    try:
        result = classification_table.get_item(Key={"content_id": content_id})
        item = result.get("Item")
        if not item:
            return api_response(404, {"error": "Classification not found"})
        return api_response(200, item)
    except Exception as e:
        return api_response(500, {"error": "Failed to get classification"})


def handle_list_entitlements(event):
    """List all entitlement policies."""
    try:
        result = entitlement_table.scan()
        return api_response(200, {"entitlements": result.get("Items", [])})
    except Exception:
        return api_response(500, {"error": "Failed to list entitlements"})


def handle_list_users(event):
    """List available demo users."""
    try:
        result = entitlement_table.scan()
        users = [
            {
                "user_id": item.get("principal_id"),
                "display_name": item.get("display_name", item.get("principal_id")),
                "role": item.get("role", ""),
                "max_security_level": item.get("max_security_level", "Public"),
                "mnpi_cleared_entities": item.get("mnpi_cleared_entities", []),
                "pii_access": item.get("pii_access", False),
            }
            for item in result.get("Items", [])
        ]
        return api_response(200, {"users": users})
    except Exception:
        return api_response(500, {"error": "Failed to list users"})


# ============================================================
# Utilities
# ============================================================

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


def parse_body(event):
    body = event.get("body", "{}")
    if isinstance(body, str):
        return json.loads(body) if body else {}
    return body


def api_response(status_code, body):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type,Authorization,X-User-Id",
        },
        "body": json.dumps(body, cls=DecimalEncoder),
    }
