import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { ScanStatistics } from '../types'

export function TelemetryStrip({ stats }: { stats: ScanStatistics | null }) {
  if (!stats || stats.daily_counts.length === 0) return null

  const flagged = Object.entries(stats.by_recommendation)
    .filter(([k]) => k !== 'LIKELY_GENUINE')
    .reduce((acc, [, v]) => acc + v, 0)

  return (
    <div className="telemetry-strip">
      <div className="telemetry-head">
        <span className="telemetry-title">Citizen Scan Telemetry · 30 days</span>
        <span className="telemetry-meta">
          <em>{stats.total_scans}</em> scans · <em>{flagged}</em> flagged · avg score{' '}
          <em>{(stats.avg_counterfeit_score * 100).toFixed(0)}%</em>
        </span>
      </div>
      <ResponsiveContainer width="100%" height={110}>
        <BarChart data={stats.daily_counts} margin={{ top: 4, right: 4, left: -26, bottom: 0 }}>
          <CartesianGrid stroke="rgba(100, 116, 139, 0.06)" vertical={false} />
          <XAxis dataKey="date" hide />
          <YAxis
            tick={{ fill: '#94a3b8', fontSize: 10, fontFamily: "'IBM Plex Mono', monospace" }}
            axisLine={false}
            tickLine={false}
          />
          <Tooltip
            cursor={{ fill: 'rgba(8, 145, 178, 0.06)' }}
            contentStyle={{
              background: 'rgba(241, 245, 249, 0.9)',
              backdropFilter: 'blur(12px)',
              border: '1px solid rgba(100, 116, 139, 0.15)',
              borderRadius: 8,
              boxShadow: '0 2px 4px rgba(15,23,42,0.12), 0 8px 16px rgba(15,23,42,0.1)',
              fontSize: 12,
              fontFamily: "'IBM Plex Mono', monospace",
            }}
            labelStyle={{ color: '#0f172a', fontWeight: 600 }}
            itemStyle={{ color: '#0891b2' }}
          />
          <Bar dataKey="count" fill="#0891b2" radius={[3, 3, 0, 0]} maxBarSize={18} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}
