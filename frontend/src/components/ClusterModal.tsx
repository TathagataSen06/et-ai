import { useEffect, useState } from 'react'
import { generateReport } from '../api'
import { RISK_COLORS, type Cluster } from '../types'

interface Props {
  cluster: Cluster
  onClose: () => void
  onAssign: (officerName: string, notes: string) => Promise<void>
}

type View = 'stats' | 'assign' | 'report'

function formatCoords(lat: number, lon: number): string {
  const ns = lat >= 0 ? 'N' : 'S'
  const ew = lon >= 0 ? 'E' : 'W'
  return `${Math.abs(lat).toFixed(4)}°${ns}, ${Math.abs(lon).toFixed(4)}°${ew}`
}

function timeAgo(iso: string | null): string {
  if (!iso) return '—'
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000)
  if (days <= 0) return 'today'
  return days === 1 ? '1 day ago' : `${days} days ago`
}

export function ClusterModal({ cluster, onClose, onAssign }: Props) {
  const [view, setView] = useState<View>('stats')
  const [officer, setOfficer] = useState('')
  const [notes, setNotes] = useState('')
  const [busy, setBusy] = useState(false)
  const [report, setReport] = useState<string | null>(null)
  const [reportGenerator, setReportGenerator] = useState<string>('')

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const color = RISK_COLORS[cluster.risk_level]
  const sector = cluster.id.slice(0, 4).toUpperCase()

  const submit = async () => {
    if (!officer.trim() || busy) return
    setBusy(true)
    try {
      await onAssign(officer.trim(), notes.trim())
    } finally {
      setBusy(false)
    }
  }

  const runReport = async () => {
    setBusy(true)
    setView('report')
    try {
      const result = await generateReport(cluster.id)
      setReport(result.markdown)
      setReportGenerator(result.generator)
    } catch (err) {
      setReport(`Report generation failed: ${(err as Error).message}`)
    } finally {
      setBusy(false)
    }
  }

  const downloadReport = () => {
    if (!report) return
    const blob = new Blob([report], { type: 'text/markdown' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `netra-report-sector-${sector}.md`
    a.click()
    URL.revokeObjectURL(url)
  }

  // Spec stat set: Risk Score, Seizure Count, Frequency/Day, Last Activity, Avg Volume, Density
  const freqPerDay = cluster.seizure_count / 90
  const avgVolume = cluster.seizure_count
    ? Math.round(cluster.total_notes / cluster.seizure_count)
    : 0
  const areaKm2 = Math.max(Math.PI * cluster.radius_km ** 2, 0.5)
  const density = cluster.seizure_count / areaKm2

  const stats: { label: string; value: string; color?: string }[] = [
    { label: 'Risk Score', value: `${Math.round(cluster.risk_score * 100)}%`, color },
    { label: 'Seizures (90d)', value: String(cluster.seizure_count) },
    { label: 'Frequency / Day', value: freqPerDay.toFixed(2) },
    { label: 'Avg Volume', value: `${avgVolume.toLocaleString()} notes`, color: 'var(--accent-danger)' },
    { label: 'Density', value: `${density.toFixed(1)}/km²` },
    { label: 'Last Activity', value: timeAgo(cluster.last_seizure_date), color: 'var(--accent-success)' },
  ]

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <div className="modal-title">Sector {sector}</div>
          <button className="modal-close" onClick={onClose} aria-label="Close">×</button>
        </div>
        <div className="modal-coords">
          {formatCoords(cluster.center_lat, cluster.center_lon)}
          <span className="stability-note">
            · consensus stability {Math.round((cluster.stability ?? 1) * 100)}%
          </span>
        </div>
        <span
          className="risk-badge"
          style={{ color, borderColor: color, background: `color-mix(in srgb, ${color} 8%, transparent)` }}
        >
          {cluster.risk_level} risk
        </span>
        <span className="risk-badge priority-badge">P{cluster.patrol_priority} priority</span>

        {view === 'stats' && (
          <>
            <div className="modal-stats">
              {stats.map((s) => (
                <div className="modal-stat" key={s.label}>
                  <div className="modal-stat-label">{s.label}</div>
                  <div
                    className="modal-stat-value"
                    style={s.color ? ({ '--modal-stat-color': s.color } as React.CSSProperties) : undefined}
                  >
                    {s.value}
                  </div>
                </div>
              ))}
            </div>
            <div className="modal-actions">
              <button className="btn btn-primary" onClick={() => setView('assign')}>
                Assign Patrol
              </button>
              <button className="btn btn-secondary" onClick={runReport}>
                Generate Report
              </button>
            </div>
          </>
        )}

        {view === 'assign' && (
          <div className="modal-form">
            <label htmlFor="officer">Officer name</label>
            <input
              id="officer"
              autoFocus
              value={officer}
              onChange={(e) => setOfficer(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && submit()}
              placeholder="e.g. Insp. Meera Rao"
            />
            <label htmlFor="notes">Notes (optional)</label>
            <textarea
              id="notes"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="Evening sweep, focus on transport hubs…"
            />
            <div className="modal-actions">
              <button className="btn btn-primary" onClick={submit} disabled={!officer.trim() || busy}>
                {busy ? 'Assigning…' : 'Confirm Assignment'}
              </button>
              <button className="btn btn-secondary" onClick={() => setView('stats')}>
                Back
              </button>
            </div>
          </div>
        )}

        {view === 'report' && (
          <div className="report-view">
            {busy && <div className="analyzing">Generating intelligence report…</div>}
            {report && (
              <>
                <div className="report-meta mono">
                  generator: {reportGenerator || '—'}
                </div>
                <pre className="report-md">{report}</pre>
              </>
            )}
            <div className="modal-actions">
              <button className="btn btn-primary" onClick={downloadReport} disabled={!report || busy}>
                Download .md
              </button>
              <button className="btn btn-secondary" onClick={() => setView('stats')}>
                Back
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
