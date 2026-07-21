import { useEffect } from 'react'
import { useMap } from 'react-leaflet'
import L from 'leaflet'
import 'leaflet.heat'
import type { HeatmapPoint } from '../types'

// leaflet.heat has no bundled types; it augments L at runtime.
type HeatLayerFactory = (
  points: [number, number, number][],
  options?: Record<string, unknown>,
) => L.Layer

export function HeatLayer({ points }: { points: HeatmapPoint[] }) {
  const map = useMap()

  useEffect(() => {
    // leaflet.heat is not resilient to React's mount/cleanup/remount cycles
    // (StrictMode) or to add/remove racing a map teardown (e.g. logout while
    // the dashboard is mounted). Contain BOTH setup and cleanup — a missing
    // heat overlay is cosmetic, a thrown effect unmounts the whole app.
    const factory = (L as unknown as { heatLayer?: HeatLayerFactory }).heatLayer
    if (!factory) return
    let layer: L.Layer | null = null
    try {
      layer = factory(
        points.map((p) => [p.lat, p.lon, p.weight]),
        {
          radius: 24,
          blur: 20,
          maxZoom: 12,
          minOpacity: 0.15,
          // Palette-matched ramp: cyan (info) -> amber (caution) -> red (danger)
          gradient: { 0.25: '#0891b2', 0.55: '#d97706', 0.8: '#ea580c', 1.0: '#dc2626' },
        },
      )
      layer.addTo(map)
    } catch {
      layer = null // map mid-teardown or canvas unavailable — skip the overlay
    }
    return () => {
      if (!layer) return
      try {
        map.removeLayer(layer)
      } catch {
        // map already destroyed — nothing to remove
      }
    }
  }, [map, points])

  return null
}
