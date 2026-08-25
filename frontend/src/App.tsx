import { useState, useEffect } from 'react';
import ChatPanel from './components/ChatPanel';
import ClassificationDashboard from './components/ClassificationDashboard';
import UserSelector from './components/UserSelector';
import AccessLog from './components/AccessLog';

interface User {
  user_id: string;
  display_name: string;
  role: string;
  max_security_level: string;
  mnpi_cleared_entities: string[];
  pii_access: boolean;
}

interface AccessEvent {
  id: string;
  timestamp: string;
  user_id: string;
  content_id: string;
  decision: 'allow' | 'redact' | 'block';
  reason: string;
}

const API_BASE = '/api';

function App() {
  const [users, setUsers] = useState<User[]>([]);
  const [selectedUser, setSelectedUser] = useState<User | null>(null);
  const [accessLog, setAccessLog] = useState<AccessEvent[]>([]);
  const [activeTab, setActiveTab] = useState<'chat' | 'dashboard' | 'log'>('chat');
  const [sessionId, setSessionId] = useState<string>(crypto.randomUUID());

  useEffect(() => {
    fetchUsers();
  }, []);

  // Reset session when user changes (new conversation per persona)
  const handleSelectUser = (user: User) => {
    setSelectedUser(user);
    setSessionId(crypto.randomUUID());
  };

  const fetchUsers = async () => {
    try {
      const res = await fetch(`${API_BASE}/users`);
      const data = await res.json();
      setUsers(data.users || []);
      if (data.users?.length > 0) {
        setSelectedUser(data.users[0]);
      }
    } catch (err) {
      // Use default demo users if API not available
      const defaultUsers: User[] = [
        {
          user_id: 'alice-pm',
          display_name: 'Alice Chen (Portfolio Manager)',
          role: 'Portfolio Manager',
          max_security_level: 'Restricted',
          mnpi_cleared_entities: ['ACME Corp', 'GlobalTech Industries', 'NovaTech Systems'],
          pii_access: false,
        },
        {
          user_id: 'bob-analyst',
          display_name: 'Bob Martinez (Research Analyst)',
          role: 'Research Analyst',
          max_security_level: 'Confidential',
          mnpi_cleared_entities: ['ACME Corp'],
          pii_access: false,
        },
        {
          user_id: 'carol-compliance',
          display_name: 'Carol Davis (Compliance Officer)',
          role: 'Compliance Officer',
          max_security_level: 'Restricted',
          mnpi_cleared_entities: ['ACME Corp', 'GlobalTech Industries', 'NovaTech Systems'],
          pii_access: true,
        },
        {
          user_id: 'dave-intern',
          display_name: 'Dave Wilson (Summer Intern)',
          role: 'Intern',
          max_security_level: 'Internal',
          mnpi_cleared_entities: [],
          pii_access: false,
        },
      ];
      setUsers(defaultUsers);
      setSelectedUser(defaultUsers[0]);
    }
  };

  const addAccessEvent = (event: Omit<AccessEvent, 'id' | 'timestamp'>) => {
    const newEvent: AccessEvent = {
      ...event,
      id: crypto.randomUUID(),
      timestamp: new Date().toISOString(),
    };
    setAccessLog((prev) => [newEvent, ...prev].slice(0, 50));
  };

  return (
    <div className="app">
      <header className="app-header">
        <div className="header-left">
          <h1>Agentic Data Classification</h1>
          <span className="header-subtitle">Bidirectional Enforcement Demo</span>
        </div>
        <div className="header-right">
          <UserSelector
            users={users}
            selectedUser={selectedUser}
            onSelectUser={handleSelectUser}
          />
        </div>
      </header>

      <nav className="tab-nav">
        <button
          className={`tab-btn ${activeTab === 'chat' ? 'active' : ''}`}
          onClick={() => setActiveTab('chat')}
        >
          Agent Chat
        </button>
        <button
          className={`tab-btn ${activeTab === 'dashboard' ? 'active' : ''}`}
          onClick={() => setActiveTab('dashboard')}
        >
          Classification Dashboard
        </button>
        <button
          className={`tab-btn ${activeTab === 'log' ? 'active' : ''}`}
          onClick={() => setActiveTab('log')}
        >
          Access Decisions ({accessLog.length})
        </button>
      </nav>

      <main className="app-main">
        {activeTab === 'chat' && (
          <ChatPanel
            user={selectedUser}
            sessionId={sessionId}
            onSessionIdUpdate={setSessionId}
            onAccessEvent={addAccessEvent}
          />
        )}
        {activeTab === 'dashboard' && <ClassificationDashboard />}
        {activeTab === 'log' && <AccessLog events={accessLog} />}
      </main>

      {selectedUser && (
        <footer className="app-footer">
          <div className="entitlement-badge">
            <span className="badge-label">Active User:</span>
            <span className="badge-value">{selectedUser.display_name}</span>
            <span className={`level-badge level-${selectedUser.max_security_level.toLowerCase()}`}>
              {selectedUser.max_security_level}
            </span>
            {selectedUser.mnpi_cleared_entities.length > 0 && (
              <span className="cleared-badge">
                Wall-crossed: {selectedUser.mnpi_cleared_entities.join(', ')}
              </span>
            )}
            {selectedUser.pii_access && (
              <span className="pii-badge">PII Access</span>
            )}
          </div>
        </footer>
      )}
    </div>
  );
}

export default App;
