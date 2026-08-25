import { useState, useEffect } from 'react';

interface Classification {
  content_id: string;
  filename: string;
  source_type: string;
  classified_at: string;
  mnpi: boolean;
  pii_detected: boolean;
  security_level: string;
  mnpi_entities: string[];
  pii_types: string[];
}

const API_BASE = '/api';

function ClassificationDashboard() {
  const [classifications, setClassifications] = useState<Classification[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    fetchClassifications();
  }, []);

  const fetchClassifications = async () => {
    try {
      const res = await fetch(`${API_BASE}/classifications`);
      const data = await res.json();
      setClassifications(data.classifications || []);
      setError('');
    } catch {
      setError('Unable to fetch classifications. Backend may not be deployed yet.');
      // Show sample data for demo purposes
      setClassifications([
        {
          content_id: 'sample-1',
          filename: 'email-mnpi-acme.txt',
          source_type: 'email',
          classified_at: '2026-07-15T10:30:00Z',
          mnpi: true,
          pii_detected: true,
          security_level: 'Restricted',
          mnpi_entities: ['ACME Corp'],
          pii_types: ['email_address', 'phone_number'],
        },
        {
          content_id: 'sample-2',
          filename: 'transcript-expert-call.txt',
          source_type: 'transcript',
          classified_at: '2026-07-20T14:00:00Z',
          mnpi: true,
          pii_detected: true,
          security_level: 'Restricted',
          mnpi_entities: ['GlobalTech Industries', 'NovaTech Systems'],
          pii_types: ['email_address', 'phone_number', 'name'],
        },
        {
          content_id: 'sample-3',
          filename: 'web-article-public.txt',
          source_type: 'web',
          classified_at: '2026-07-22T09:00:00Z',
          mnpi: false,
          pii_detected: false,
          security_level: 'Public',
          mnpi_entities: [],
          pii_types: [],
        },
        {
          content_id: 'sample-4',
          filename: 'hr-document-pii.txt',
          source_type: 'document',
          classified_at: '2026-07-25T11:00:00Z',
          mnpi: false,
          pii_detected: true,
          security_level: 'Confidential',
          mnpi_entities: [],
          pii_types: ['ssn', 'address', 'phone_number', 'email_address', 'financial_account'],
        },
        {
          content_id: 'sample-5',
          filename: 'slack-internal.txt',
          source_type: 'slack',
          classified_at: '2026-07-18T16:00:00Z',
          mnpi: false,
          pii_detected: false,
          security_level: 'Internal',
          mnpi_entities: [],
          pii_types: [],
        },
      ]);
    } finally {
      setLoading(false);
    }
  };

  const stats = {
    total: classifications.length,
    mnpi: classifications.filter((c) => c.mnpi).length,
    pii: classifications.filter((c) => c.pii_detected).length,
    byLevel: classifications.reduce(
      (acc, c) => {
        acc[c.security_level] = (acc[c.security_level] || 0) + 1;
        return acc;
      },
      {} as Record<string, number>
    ),
  };

  if (loading) {
    return <div className="dashboard loading-state">Loading classifications...</div>;
  }

  return (
    <div className="dashboard">
      {error && <div className="dashboard-notice">{error}</div>}

      <div className="stats-grid">
        <div className="stat-card">
          <div className="stat-value">{stats.total}</div>
          <div className="stat-label">Total Classified</div>
        </div>
        <div className="stat-card stat-mnpi">
          <div className="stat-value">{stats.mnpi}</div>
          <div className="stat-label">MNPI Flagged</div>
        </div>
        <div className="stat-card stat-pii">
          <div className="stat-value">{stats.pii}</div>
          <div className="stat-label">PII Detected</div>
        </div>
        <div className="stat-card">
          <div className="stat-value">{Object.keys(stats.byLevel).length}</div>
          <div className="stat-label">Security Levels</div>
        </div>
      </div>

      <div className="level-breakdown">
        {Object.entries(stats.byLevel).map(([level, count]) => (
          <div key={level} className={`level-bar level-${level.toLowerCase()}`}>
            <span className="level-name">{level}</span>
            <div className="level-progress">
              <div
                className="level-fill"
                style={{ width: `${(count / stats.total) * 100}%` }}
              ></div>
            </div>
            <span className="level-count">{count}</span>
          </div>
        ))}
      </div>

      <h3>Classified Content</h3>
      <div className="classification-table">
        <table>
          <thead>
            <tr>
              <th>Filename</th>
              <th>Source</th>
              <th>Security</th>
              <th>MNPI</th>
              <th>PII</th>
              <th>Classified</th>
            </tr>
          </thead>
          <tbody>
            {classifications.map((item) => (
              <tr key={item.content_id}>
                <td className="filename-cell">{item.filename}</td>
                <td>
                  <span className={`source-badge source-${item.source_type}`}>
                    {item.source_type}
                  </span>
                </td>
                <td>
                  <span className={`level-badge level-${item.security_level.toLowerCase()}`}>
                    {item.security_level}
                  </span>
                </td>
                <td>
                  {item.mnpi ? (
                    <span className="flag flag-mnpi" title={item.mnpi_entities.join(', ')}>
                      Yes ({item.mnpi_entities.join(', ')})
                    </span>
                  ) : (
                    <span className="flag flag-clear">No</span>
                  )}
                </td>
                <td>
                  {item.pii_detected ? (
                    <span className="flag flag-pii" title={item.pii_types.join(', ')}>
                      Yes ({item.pii_types.length} types)
                    </span>
                  ) : (
                    <span className="flag flag-clear">No</span>
                  )}
                </td>
                <td className="date-cell">
                  {new Date(item.classified_at).toLocaleDateString()}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default ClassificationDashboard;
