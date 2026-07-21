import { useEffect, useMemo } from 'react'
import { MapContainer, Marker, TileLayer, useMap } from 'react-leaflet'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { HeatLayer } from './HeatLayer'
import type { Cluster, HeatmapPoint } from '../types'

/** Design brief: markers move at ~95% of map pan speed for subtle depth.
 *  During a pan the marker pane is offset by 5% of the pixel delta, then
 *  springs back to its true position on move end. */
function MarkerParallax() {
  const map = useMap()

  useEffect(() => {
    const pane = map.getPane('markerPane')
    if (!pane) return
    let start: L.Point | null = null
    let zooming = false

    const onMoveStart = () => {
      if (zooming) return
      start = map.project(map.getCenter())
      pane.style.transition = 'transform 0s'
    }
    const onMove = () => {
      if (!start || zooming) return
      const current = map.project(map.getCenter())
      const dx = (current.x - start.x) * 0.05
      const dy = (current.y - start.y) * 0.05
      pane.style.transform = `translate(${dx}px, ${dy}px)`
    }
    const settle = () => {
      start = null
      pane.style.transition = 'transform 300ms cubic-bezier(0.34, 1.56, 0.64, 1)'
      pane.style.transform = 'translate(0px, 0px)'
    }
    const onZoomStart = () => {
      zooming = true
      start = null
      pane.style.transition = 'transform 0s'
      pane.style.transform = 'translate(0px, 0px)'
    }
    const onZoomEnd = () => {
      zooming = false
    }

    map.on('movestart', onMoveStart)
    map.on('move', onMove)
    map.on('moveend', settle)
    map.on('zoomstart', onZoomStart)
    map.on('zoomend', onZoomEnd)
    return () => {
      map.off('movestart', onMoveStart)
      map.off('move', onMove)
      map.off('moveend', settle)
      map.off('zoomstart', onZoomStart)
      map.off('zoomend', onZoomEnd)
      pane.style.transform = ''
      pane.style.transition = ''
    }
  }, [map])

  return null
}

interface Props {
  clusters: Cluster[]
  heatmap: HeatmapPoint[]
  onSelectCluster: (cluster: Cluster) => void
}

const RISK_CLASS: Record<Cluster['risk_level'], string> = {
  LOW: 'risk-low',
  MEDIUM: 'risk-medium',
  HIGH: 'risk-high',
  CRITICAL: 'risk-critical',
}

/** Marker diameter by risk band, nudged by seizure volume (spec: 20–44px). */
function markerSize(cluster: Cluster): number {
  const base = { LOW: 20, MEDIUM: 28, HIGH: 34, CRITICAL: 38 }[cluster.risk_level]
  const bonus = Math.min(6, Math.floor(cluster.seizure_count / 5))
  return base + bonus
}

function buildIcon(cluster: Cluster): L.DivIcon {
  const size = markerSize(cluster)
  const label = `${cluster.risk_level} · ${cluster.seizure_count} seizures`
  return L.divIcon({
    className: '', // suppress default leaflet-div-icon styling
    html: `<div class="netra-marker ${RISK_CLASS[cluster.risk_level]}">
             <span class="marker-label">${label}</span>
           </div>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  })
}

export function MapView({ clusters, heatmap, onSelectCluster }: Props) {
  const icons = useMemo(
    () => new Map(clusters.map((c) => [c.id, buildIcon(c)])),
    [clusters],
  )

  return (
    <MapContainer
      center={[22.5, 79.0]}
      zoom={5}
      style={{ height: '100%', width: '100%' }}
      zoomControl
    >
      <TileLayer
        url="https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png"
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>'
      />
      <HeatLayer points={heatmap} />
      <MarkerParallax />
      {clusters.map((cluster) => (
        <Marker
          key={cluster.id}
          position={[cluster.center_lat, cluster.center_lon]}
          icon={icons.get(cluster.id)}
          eventHandlers={{ click: () => onSelectCluster(cluster) }}
        />
      ))}
    </MapContainer>
  )
}
