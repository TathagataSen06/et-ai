import { useCallback, useEffect, useState } from 'react'
import {
  assignPatrol,
  fetchAlerts,
  fetchClusters,
  fetchHeatmap,
  fetchPatrols,
  fetchStatistics,
} from '../api'
import { ClusterModal } from '../components/ClusterModal'
import { MapView } from '../components/MapView'
import { Sidebar } from '../components/Sidebar'
import { TelemetryStrip } from '../components/TelemetryStrip'
import { useDashboardSocket } from '../hooks/useWebSocket'
import { useToastStore } from '../stores/toasts'
import type {
  Alert,
  Cluster,
  HeatmapPoint,
  LiveAlert,
  Patrol,
  ScanStatistics,
} from '../types'

export function CommandCenter() {
  const [clusters, setClusters] = useState<Cluster[]>([])
  const [heatmap, setHeatmap] = useState<HeatmapPoint[]>([])
  const [alerts, setAlerts] = useState<Alert[]>([])
  const [patrols, setPatrols] = useState<Patrol[]>([])
  const [stats, setStats] = useState<ScanStatistics | null>(null)
  const [selected, setSelected] = useState<Cluster | null>(null)
  const pushToast = useToastStore((s) => s.push)

  const refresh = useCallback(() => {
    fetchClusters().then(setClusters).catch(console.error)
    fetchAlerts().then(setAlerts).catch(console.error)
    fetchPatrols().then(setPatrols).catch(console.error)
    fetchStatistics().then(setStats).catch(console.error)
    fetchHeatmap().then(setHeatmap).catch(console.error)
  }, [])

  useEffect(() => {
    refresh()
    const interval = window.setInterval(refresh, 30_000)
    return () => window.clearInterval(interval)
  }, [refresh])

  const wsConnected = useDashboardSocket((msg: LiveAlert) => {
    if (msg.type === 'ALERT') {
      const coords =
        msg.lat != null && msg.lon != null
          ? `${msg.lat.toFixed(4)}°N, ${msg.lon.toFixed(4)}°E · just now`
          : 'just now'
      pushToast({
        severity: msg.severity === 'HIGH' ? 'HIGH' : 'MEDIUM',
        title: msg.severity === 'HIGH' ? 'Critical detection' : 'Suspicious activity',
        message: msg.message ?? 'New detection reported',
        meta: coords,
      })
      fetchAlerts().then(setAlerts).catch(console.error)
    } else if (msg.type === 'CLUSTER_UPDATE') {
      fetchClusters().then(setClusters).catch(console.error)
    }
  })

  const handleAssign = async (officerName: string, notes: string) => {
    if (!selected) return
    try {
      await assignPatrol(officerName, selected.id, notes)
      pushToast({
        severity: 'INFO',
        title: 'Patrol assigned',
        message: `${officerName} → sector ${selected.id.slice(0, 4).toUpperCase()} (P${selected.patrol_priority})`,
        meta: `${selected.center_lat.toFixed(4)}°N, ${selected.center_lon.toFixed(4)}°E`,
      })
      setSelected(null)
      fetchPatrols().then(setPatrols).catch(console.error)
    } catch (err) {
      pushToast({
        severity: 'MEDIUM',
        title: 'Assignment failed',
        message: (err as Error).message,
      })
    }
  }

  return (
    <div className="command-center">
      <Sidebar clusters={clusters} alerts={alerts} patrols={patrols} wsConnected={wsConnected} />
      <div className="map-column">
        <div className="map-panel">
          <MapView clusters={clusters} heatmap={heatmap} onSelectCluster={setSelected} />
          <div className="map-scrim" />
          <div className="map-watermark">NETRA</div>
        </div>
        <TelemetryStrip stats={stats} />
      </div>

      {selected && (
        <ClusterModal cluster={selected} onClose={() => setSelected(null)} onAssign={handleAssign} />
      )}
    </div>
  )
}
