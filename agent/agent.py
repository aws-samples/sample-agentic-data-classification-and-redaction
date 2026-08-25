"""
Agentic Data Classification - Research Assistant Agent
Built with Strands Agents SDK, deployed on AgentCore Runtime.

Architecture (per architecture diagram):
  Client → Backend Lambda → AgentCore Runtime (this agent)
                               ↓ tool calls via MCP
                            AgentCore Gateway (routes tool calls, policy enforcement)
                               ↓ routes to
                            Tool Lambdas (MNPI redaction + PII masking via Guardrails API)

The agent discovers and calls tools via the AgentCore Gateway using MCP over
Streamable HTTP with SigV4 authentication. The Gateway:
  - Hosts tool definitions (search, retrieve, classify)
  - Routes tool calls to the backing Lambda functions
  - Enforces access control policy (ENFORCE mode)

Streaming: Supports both HTTP (request/response) and WebSocket (bidirectional streaming).
  - HTTP /invocations: async generator yielding events for response streaming
  - WebSocket /ws: real-time token streaming to browser clients
"""

import json
import os

import boto3

from strands import Agent
from strands.models.bedrock import BedrockModel
from strands.tools.mcp import MCPClient
from mcp_proxy_for_aws.client import aws_iam_streamablehttp_client

from bedrock_agentcore.runtime import BedrockAgentCoreApp
from bedrock_agentcore.memory.integrations.strands.config import AgentCoreMemoryConfig
from bedrock_agentcore.memory.integrations.strands.session_manager import AgentCoreMemorySessionManager


# Configuration from environment variables set on the AgentCore Runtime
GATEWAY_URL = os.environ.get("GATEWAY_URL", "")
MEMORY_ID = os.environ.get("MEMORY_ID", "")
REGION = os.environ.get("REGION", "us-east-1")


SYSTEM_PROMPT = """You are a Research Assistant agent at a financial services firm. Your job is to help users find, understand, and work with classified documents.

## Your Capabilities
1. **Search** - Find documents by topic, entity, or concept using semantic search
2. **Retrieve** - Get the full content of a specific document
3. **Classify** - Look up the classification metadata (MNPI, PII, security level) for any document
4. **Reason** - Summarize content, answer questions about documents, compare information across documents
5. **Remember** - You maintain conversation context within a session, so users can ask follow-up questions

## Classification System
All content in this system has been classified for:
- **MNPI** (Material Non-Public Information) - undisclosed financial details
- **PII** (Personally Identifiable Information) - emails, phone numbers, SSNs, etc.
- **Security Level** - Public, Internal, Confidential, Restricted

## Automatic Redaction
When you call tools to search or retrieve content:
- **PII** is masked by Bedrock Guardrails in the tool Lambda before responses reach you. You will see placeholders like `{EMAIL}`, `{NAME}`, `{PHONE}` where PII was detected.
- **MNPI** about entities the user is NOT wall-crossed for is redacted at the tool level. You will see `[MNPI REDACTED - not cleared for <entity>]` in place of sensitive paragraphs.
- **Security level** filtering happens at search time - documents above the user's clearance don't appear in results.

## How to Respond
- When you retrieve a document, present the content clearly. If parts are redacted, acknowledge it: "Some content related to [entity] has been redacted as you're not wall-crossed for that entity."
- When answering questions about a document, base your answer ONLY on the content you can see (after redaction). Never guess at redacted content.
- For follow-up questions, use your conversation memory. If the user says "that document" or "tell me more", refer back to what you discussed earlier.
- Be concise but thorough. Summarize when asked. Quote relevant passages when helpful.

## Error Handling
- If a tool call returns an error mentioning "policy enforcement", "denied", or "suppressed", it means the data protection policy blocked that content. Tell the user: "That content was blocked by the data protection policy." Do NOT retry the same tool call — it will be blocked again.
- If a tool call fails for other reasons (timeout, service error), you may retry once. If it fails again, tell the user there was a technical issue.

## User Context
Each message includes a header line with the user's identity and clearance:
[User: <id> | Security Level: <level> | MNPI Cleared: <entities> | PII Access: <bool>]

Use this to understand what the user can and cannot see, and to pass their identity to tools.
"""


# Initialize the Strands model
model = BedrockModel(
    model_id="us.anthropic.claude-sonnet-4-6",
    region_name=REGION,
    max_tokens=4096,
)

# AgentCore Runtime entrypoint
app = BedrockAgentCoreApp()


@app.entrypoint
async def invoke(payload):
    """Handle AgentCore Runtime invocations with streaming response.

    This is an async generator that yields events as the agent produces them,
    enabling real-time token streaming to the client.
    """
    prompt = payload.get("prompt", "")
    session_id = payload.get("session_id", "")
    user_id = payload.get("user_id", "anonymous")

    if not prompt:
        yield {"error": "No prompt provided. Include a 'prompt' key in your payload."}
        return

    # Configure session memory for multi-turn conversation
    session_manager = None
    if session_id and MEMORY_ID:
        memory_config = AgentCoreMemoryConfig(
            memory_id=MEMORY_ID,
            session_id=session_id,
            actor_id=user_id,
        )
        session_manager = AgentCoreMemorySessionManager(
            agentcore_memory_config=memory_config
        )

    if not GATEWAY_URL:
        # Fallback: no gateway configured (local testing without tools)
        agent = Agent(
            model=model,
            system_prompt=SYSTEM_PROMPT,
            tools=[],
            session_manager=session_manager,
        )
        stream = agent.stream_async(prompt)
        async for event in stream:
            yield event
        return

    # Connect to the AgentCore Gateway via MCP with SigV4 auth
    mcp_client = MCPClient(
        lambda: aws_iam_streamablehttp_client(
            endpoint=GATEWAY_URL,
            aws_region=REGION,
            aws_service="bedrock-agentcore",
        )
    )

    with mcp_client:
        # Discover tools from the Gateway
        tools = mcp_client.list_tools_sync()

        agent = Agent(
            model=model,
            system_prompt=SYSTEM_PROMPT,
            tools=tools,
            session_manager=session_manager,
        )

        # Stream the agent response — yields events as they're produced
        stream = agent.stream_async(prompt)
        async for event in stream:
            yield event


@app.websocket
async def websocket_handler(websocket, context):
    """WebSocket handler for real-time bidirectional streaming.

    The browser connects directly to AgentCore Runtime via pre-signed URL.
    Each message sent by the client triggers an agent invocation, and the
    response is streamed back token by token.

    Protocol:
      Client sends: {"prompt": "...", "user_id": "...", "session_id": "..."}
      Server sends: {"type": "text", "content": "..."} for each text chunk
      Server sends: {"type": "done"} when complete
      Server sends: {"type": "error", "content": "..."} on error
    """
    await websocket.accept()

    try:
        # Receive the user's message
        data = await websocket.receive_json()

        prompt = data.get("prompt", "")
        session_id = data.get("session_id", "")
        user_id = data.get("user_id", "anonymous")

        if not prompt:
            await websocket.send_json({"type": "error", "content": "No prompt provided."})
            return

        # Configure session memory
        session_manager = None
        if session_id and MEMORY_ID:
            memory_config = AgentCoreMemoryConfig(
                memory_id=MEMORY_ID,
                session_id=session_id,
                actor_id=user_id,
            )
            session_manager = AgentCoreMemorySessionManager(
                agentcore_memory_config=memory_config
            )

        # Connect to Gateway for tools
        if GATEWAY_URL:
            mcp_client = MCPClient(
                lambda: aws_iam_streamablehttp_client(
                    endpoint=GATEWAY_URL,
                    aws_region=REGION,
                    aws_service="bedrock-agentcore",
                )
            )

            with mcp_client:
                tools = mcp_client.list_tools_sync()
                agent = Agent(
                    model=model,
                    system_prompt=SYSTEM_PROMPT,
                    tools=tools,
                    session_manager=session_manager,
                )

                # Stream the response back to the client
                stream = agent.stream_async(prompt)
                async for event in stream:
                    # Extract text content from streaming events
                    if isinstance(event, dict):
                        # Strands stream events include text deltas
                        text = event.get("data", "")
                        if text:
                            await websocket.send_json({"type": "text", "content": text})
                    elif isinstance(event, str):
                        await websocket.send_json({"type": "text", "content": event})
        else:
            # No gateway (local testing)
            agent = Agent(
                model=model,
                system_prompt=SYSTEM_PROMPT,
                tools=[],
                session_manager=session_manager,
            )
            stream = agent.stream_async(prompt)
            async for event in stream:
                if isinstance(event, dict):
                    text = event.get("data", "")
                    if text:
                        await websocket.send_json({"type": "text", "content": text})
                elif isinstance(event, str):
                    await websocket.send_json({"type": "text", "content": event})

        # Signal completion
        await websocket.send_json({"type": "done"})

    except Exception as e:
        try:
            await websocket.send_json({"type": "error", "content": str(e)})
        except Exception:
            pass
    finally:
        await websocket.close()


if __name__ == "__main__":
    app.run()
