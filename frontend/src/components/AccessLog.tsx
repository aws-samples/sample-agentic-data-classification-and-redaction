interface AccessEvent {
  id: string;
  timestamp: string;
  user_id: string;
  content_id: string;
  decision: 'allow' | 'redact' | 'block';
  reason: string;
}

interface Props {
  events: AccessEvent[];
}

function AccessLog({ events }: Props) {
  if (events.length === 0) {
    return (
      <div className="access-log empty">
        <div className="empty-state">
          <h3>No Access Decisions Yet</h3>
          <p>
            Interact with the agent in the Chat tab to generate access decisions.
            Each time the agent retrieves or searches content, the system evaluates
            entitlements and logs the decision here.
          </p>
          <div className="legend">
            <div className="legend-item">
              <span className="decision-dot decision-allow"></span>
              <span>Allow — Content delivered in full</span>
            </div>
            <div className="legend-item">
              <span className="decision-dot decision-redact"></span>
              <span>Redact — Content delivered with sensitive data removed</span>
            </div>
            <div className="legend-item">
              <span className="decision-dot decision-block"></span>
              <span>Block — Access denied entirely</span>
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="access-log">
      <div className="log-header">
        <h3>Access Decision Log</h3>
        <span className="log-count">{events.length} events</span>
      </div>
      <div className="log-entries">
        {events.map((event) => (
          <div key={event.id} className={`log-entry decision-${event.decision}`}>
            <div className="log-entry-header">
              <span className={`decision-badge decision-${event.decision}`}>
                {event.decision.toUpperCase()}
              </span>
              <span className="log-time">
                {new Date(event.timestamp).toLocaleTimeString()}
              </span>
            </div>
            <div className="log-entry-body">
              <div className="log-detail">
                <span className="log-label">User:</span>
                <span>{event.user_id}</span>
              </div>
              <div className="log-detail">
                <span className="log-label">Content:</span>
                <span>{event.content_id}</span>
              </div>
              <div className="log-detail">
                <span className="log-label">Reason:</span>
                <span>{event.reason}</span>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default AccessLog;
