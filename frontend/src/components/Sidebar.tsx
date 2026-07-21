import {
  RISK_COLORS,
  type Alert,
  type Cluster,
  type Patrol,
} from '../types'

interface Props {
  clusters: Cluster[]
  alerts: Alert[]
  patrols: Patrol[]
  wsConnected: boolean
}

const SEVERITY_COLORS: Record<string, string> = {
  CRITICAL: 'var(--accent-danger)',
  HIGH: 'var(--accent-warning)',
  MEDIUM: 'var(--accent-caution)',
  LOW: 'var(--accent-success)',
}

export function Sidebar({ clusters, alerts, patrols, wsConnected }: Props) {
  const countBy = (level: string) => clusters.filter((c) => c.risk_level === level).length
  const totalSeizures = clusters.reduce((acc, c) => acc + c.seizure_count, 0)
  const totalNotes = clusters.reduce((acc, c) => acc + c.total_notes, 0)

  return (
    <aside className="sidebar">
      <div className="ws-status">
        <span className={wsConnected ? 'dot dot-on' : 'dot dot-off'} />
        {wsConnected ? 'LIVE FEED · CONNECTED' : 'LIVE FEED · RECONNECTING'}
      </div>

      <section>
        <h2>Situation Overview</h2>
        <div className="stat-grid">
          <div className="stat-card" style={{ '--stat-color': 'var(--accent-info)' } as React.CSSProperties}>
            <div className="stat-value">{clusters.length}</div>
            <div className="stat-label">Active clusters</div>
          </div>
          <div className="stat-card" style={{ '--stat-color': RISK_COLORS.CRITICAL } as React.CSSProperties}>
            <div className="stat-value">{countBy('CRITICAL')}</div>
            <div className="stat-label">Critical</div>
          </div>
          <div className="stat-card" style={{ '--stat-color': RISK_COLORS.HIGH } as React.CSSProperties}>
            <div className="stat-value">{countBy('HIGH')}</div>
            <div className="stat-label">High risk</div>
          </div>
          <div className="stat-card" style={{ '--stat-color': 'var(--accent-success)' } as React.CSSProperties}>
            <div className="stat-value">{totalSeizures}</div>
            <div className="stat-label">Seizures 90d</div>
          </div>
        </div>
        <div className="note-total">
          <strong>{totalNotes.toLocaleString()}</strong> counterfeit notes seized in active
          clusters
        </div>
      </section>

      <section>
        <h2>Recent Alerts</h2>
        <div className="alert-list">
          {alerts.length === 0 && <div className="empty">No alerts</div>}
          {alerts.map((alert) => (
            <div
              key={alert.id}
              className="alert-item"
              style={{ '--item-accent': SEVERITY_COLORS[alert.severity] ?? 'var(--accent-info)' } as React.CSSProperties}
            >
              <span
                className="severity-tag"
                style={{ color: SEVERITY_COLORS[alert.severity] ?? 'var(--text-secondary)' }}
              >
                {alert.severity}
              </span>
              <div className="alert-msg">{alert.description}</div>
              <div className="alert-time">{new Date(alert.created_at).toLocaleString()}</div>
            </div>
          ))}
        </div>
      </section>

      <section>
        <h2>Patrol Teams</h2>
        <div className="alert-list">
          {patrols.length === 0 && <div className="empty">No patrols assigned</div>}
          {patrols.map((patrol) => (
            <div
              key={patrol.id}
              className="alert-item"
              style={{ '--item-accent': 'var(--accent-secondary)' } as React.CSSProperties}
            >
              <div className="patrol-row">
                <strong>{patrol.officer_name}</strong>
                <span className={`patrol-status status-${patrol.status.toLowerCase()}`}>
                  {patrol.status}
                </span>
              </div>
              <div className="alert-time">
                P{patrol.priority} · {new Date(patrol.date_assigned).toLocaleDateString()}
              </div>
            </div>
          ))}
        </div>
      </section>
    </aside>
  )
}
