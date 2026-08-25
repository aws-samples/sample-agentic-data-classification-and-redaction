import { useState, useRef, useEffect, type KeyboardEvent, type ReactNode } from 'react';

interface User {
  user_id: string;
  display_name: string;
  role: string;
  max_security_level: string;
  mnpi_cleared_entities: string[];
  pii_access: boolean;
}

interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: string;
  metadata?: {
    redacted?: boolean;
    blocked?: boolean;
    classification?: Record<string, unknown>;
  };
}

interface AccessEvent {
  user_id: string;
  content_id: string;
  decision: 'allow' | 'redact' | 'block';
  reason: string;
}

interface Props {
  user: User | null;
  sessionId: string;
  onSessionIdUpdate: (id: string) => void;
  onAccessEvent: (event: AccessEvent) => void;
}

const API_BASE = '/api';

function ChatPanel({ user, sessionId, onSessionIdUpdate, onAccessEvent }: Props) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  useEffect(() => {
    // Reset chat when user changes
    setMessages([
      {
        id: 'welcome',
        role: 'assistant',
        content: user
          ? `Hello ${user.display_name.split('(')[0].trim()}! I'm the Research Assistant. I can help you search and retrieve classified documents.\n\nYour clearance: **${user.max_security_level}**\nMNPI cleared for: ${user.mnpi_cleared_entities.length > 0 ? user.mnpi_cleared_entities.join(', ') : 'None'}\nPII access: ${user.pii_access ? 'Yes' : 'No'}\n\nTry asking me to search for documents, or retrieve a specific one.`
          : 'Please select a user identity to begin.',
        timestamp: new Date().toISOString(),
      },
    ]);
  }, [user]);

  const sendMessage = async () => {
    if (!input.trim() || !user || loading) return;

    const userMessage: Message = {
      id: crypto.randomUUID(),
      role: 'user',
      content: input,
      timestamp: new Date().toISOString(),
    };

    setMessages((prev) => [...prev, userMessage]);
    const messageText = input;
    setInput('');
    setLoading(true);

    // Create a placeholder assistant message that we'll update as chunks arrive
    const assistantMsgId = crypto.randomUUID();

    try {
      // First, get a pre-signed WebSocket URL from the backend
      const wsRes = await fetch(`${API_BASE}/chat/ws-url`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          user_id: user.user_id,
          session_id: sessionId,
        }),
      });

      if (!wsRes.ok) {
        // Fallback to non-streaming HTTP if ws-url endpoint fails
        await sendMessageHttp(messageText, user, assistantMsgId);
        return;
      }

      const wsData = await wsRes.json();

      if (wsData.session_id && wsData.session_id !== sessionId) {
        onSessionIdUpdate(wsData.session_id);
      }

      const wsUrl = wsData.ws_url;
      if (!wsUrl) {
        // Fallback to HTTP
        await sendMessageHttp(messageText, user, assistantMsgId);
        return;
      }

      // Build the contextualized prompt (same as backend does for HTTP)
      const entitlement = wsData.entitlement || {};
      const contextualizedPrompt =
        `[User: ${user.user_id} | Security Level: ${entitlement.max_security_level || 'Public'} | ` +
        `MNPI Cleared: ${JSON.stringify(entitlement.mnpi_cleared_entities || [])} | ` +
        `PII Access: ${entitlement.pii_access || false}]\n\n${messageText}`;

      // Open WebSocket and stream the response
      await streamViaWebSocket(wsUrl, contextualizedPrompt, user, assistantMsgId, wsData.session_id || sessionId);

    } catch {
      // If streaming fails entirely, try HTTP fallback
      try {
        await sendMessageHttp(messageText, user, assistantMsgId);
      } catch {
        setMessages((prev) => [
          ...prev,
          {
            id: assistantMsgId,
            role: 'assistant',
            content: 'Connection error. Make sure the backend API is running.',
            timestamp: new Date().toISOString(),
          },
        ]);
      }
    } finally {
      setLoading(false);
    }
  };

  const streamViaWebSocket = (
    wsUrl: string,
    prompt: string,
    currentUser: User,
    msgId: string,
    currentSessionId: string,
  ): Promise<void> => {
    return new Promise((resolve, reject) => {
      let accumulatedText = '';
      let messageAdded = false;

      const ws = new WebSocket(wsUrl);

      ws.onopen = () => {
        // Send the prompt to the agent
        ws.send(JSON.stringify({
          prompt,
          user_id: currentUser.user_id,
          session_id: currentSessionId,
        }));
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);

          if (data.type === 'text' && data.content) {
            accumulatedText += data.content;

            if (!messageAdded) {
              // Add the message on first chunk
              messageAdded = true;
              setMessages((prev) => [
                ...prev,
                {
                  id: msgId,
                  role: 'assistant',
                  content: accumulatedText,
                  timestamp: new Date().toISOString(),
                  metadata: {
                    redacted: accumulatedText.includes('[MNPI REDACTED') || accumulatedText.includes('{EMAIL}') || accumulatedText.includes('{NAME}'),
                  },
                },
              ]);
            } else {
              // Update existing message with new content
              setMessages((prev) =>
                prev.map((msg) =>
                  msg.id === msgId
                    ? {
                        ...msg,
                        content: accumulatedText,
                        metadata: {
                          redacted: accumulatedText.includes('[MNPI REDACTED') || accumulatedText.includes('{EMAIL}') || accumulatedText.includes('{NAME}'),
                        },
                      }
                    : msg
                )
              );
            }
          } else if (data.type === 'done') {
            ws.close();
            if (accumulatedText && !messageAdded) {
              setMessages((prev) => [
                ...prev,
                {
                  id: msgId,
                  role: 'assistant',
                  content: accumulatedText,
                  timestamp: new Date().toISOString(),
                },
              ]);
            }
            // Log access events
            if (accumulatedText.includes('[MNPI REDACTED') || accumulatedText.includes('{EMAIL}') || accumulatedText.includes('{NAME}')) {
              onAccessEvent({
                user_id: currentUser.user_id,
                content_id: 'search-result',
                decision: 'redact',
                reason: 'MNPI content redacted per wall-crossing policy',
              });
            }
            resolve();
          } else if (data.type === 'error') {
            ws.close();
            if (!messageAdded) {
              setMessages((prev) => [
                ...prev,
                {
                  id: msgId,
                  role: 'assistant',
                  content: `Error: ${data.content || 'Unknown error from agent'}`,
                  timestamp: new Date().toISOString(),
                },
              ]);
            }
            resolve();
          }
        } catch {
          // Non-JSON message, treat as raw text
          accumulatedText += event.data;
          if (!messageAdded) {
            messageAdded = true;
            setMessages((prev) => [
              ...prev,
              {
                id: msgId,
                role: 'assistant',
                content: accumulatedText,
                timestamp: new Date().toISOString(),
              },
            ]);
          } else {
            setMessages((prev) =>
              prev.map((msg) =>
                msg.id === msgId ? { ...msg, content: accumulatedText } : msg
              )
            );
          }
        }
      };

      ws.onerror = () => {
        reject(new Error('WebSocket connection failed'));
      };

      ws.onclose = () => {
        if (!messageAdded && accumulatedText) {
          setMessages((prev) => [
            ...prev,
            {
              id: msgId,
              role: 'assistant',
              content: accumulatedText,
              timestamp: new Date().toISOString(),
            },
          ]);
        }
        resolve();
      };

      // Timeout after 120 seconds
      setTimeout(() => {
        if (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING) {
          ws.close();
          if (!messageAdded) {
            reject(new Error('WebSocket timeout'));
          } else {
            resolve();
          }
        }
      }, 120000);
    });
  };

  const sendMessageHttp = async (messageText: string, currentUser: User, msgId: string) => {
    // Fallback: non-streaming HTTP chat (original behavior)
    const res = await fetch(`${API_BASE}/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: messageText,
        user_id: currentUser.user_id,
        session_id: sessionId,
      }),
    });

    const data = await res.json();

    if (data.session_id && data.session_id !== sessionId) {
      onSessionIdUpdate(data.session_id);
    }

    const assistantMessage: Message = {
      id: msgId,
      role: 'assistant',
      content: data.response || 'I encountered an error processing your request.',
      timestamp: new Date().toISOString(),
      metadata: {
        redacted: data.response?.includes('[MNPI REDACTED') || data.response?.includes('{EMAIL}') || data.response?.includes('{NAME}'),
        blocked: res.status === 403,
      },
    };

    setMessages((prev) => [...prev, assistantMessage]);

    if (assistantMessage.metadata?.redacted) {
      onAccessEvent({
        user_id: currentUser.user_id,
        content_id: 'search-result',
        decision: 'redact',
        reason: 'MNPI content redacted per wall-crossing policy',
      });
    }
    if (assistantMessage.metadata?.blocked) {
      onAccessEvent({
        user_id: currentUser.user_id,
        content_id: 'request',
        decision: 'block',
        reason: data.reason || 'Access denied',
      });
    }
  };

  const handleKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  return (
    <div className="chat-panel">
      <div className="messages">
        {messages.map((msg) => (
          <div key={msg.id} className={`message message-${msg.role}`}>
            <div className="message-header">
              <span className="message-author">
                {msg.role === 'user' ? user?.display_name || 'User' : 'Research Assistant'}
              </span>
              <span className="message-time">
                {new Date(msg.timestamp).toLocaleTimeString()}
              </span>
            </div>
            <div className={`message-content ${msg.metadata?.redacted ? 'redacted' : ''} ${msg.metadata?.blocked ? 'blocked' : ''}`}>
              {formatMessage(msg.content)}
            </div>
            {msg.metadata?.redacted && (
              <div className="message-badge redact-badge">Content Redacted</div>
            )}
            {msg.metadata?.blocked && (
              <div className="message-badge block-badge">Access Blocked</div>
            )}
          </div>
        ))}
        {loading && (
          <div className="message message-assistant">
            <div className="message-content loading">
              <span className="dot"></span>
              <span className="dot"></span>
              <span className="dot"></span>
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      <div className="input-area">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={user ? 'Ask the research assistant...' : 'Select a user first'}
          disabled={!user || loading}
          rows={2}
          aria-label="Message input"
        />
        <button
          onClick={sendMessage}
          disabled={!user || loading || !input.trim()}
          aria-label="Send message"
        >
          Send
        </button>
      </div>
    </div>
  );
}

function formatMessage(content: string): ReactNode {
  // Simple markdown-like formatting
  const lines = content.split('\n');
  return lines.map((line, i) => {
    // Bold
    const formatted = line.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    // Code
    const withCode = formatted.replace(/`(.*?)`/g, '<code>$1</code>');
    // MNPI redaction highlighting
    const withRedaction = withCode.replace(
      /\[MNPI REDACTED.*?\]|\{[A-Z_]+\}/g,
      '<span class="redacted-text">$&</span>'
    );

    return (
      <span key={i}>
        <span dangerouslySetInnerHTML={{ __html: withRedaction }} />
        {i < lines.length - 1 && <br />}
      </span>
    );
  });
}

export default ChatPanel;
