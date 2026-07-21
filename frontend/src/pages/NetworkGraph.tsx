import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  forceCenter,
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from 'd3-force'
import { fetchNetworkGraph, fetchSuspiciousAccounts } from '../api'
import type { NetworkGraph as GraphData, NetworkNode, SuspiciousAccount } from '../types'

interface LayoutNode extends SimulationNodeDatum, NetworkNode {}
type LayoutLink = SimulationLinkDatum<LayoutNode> & { type: string }

const WIDTH = 960
const HEIGHT = 620
const NODE_PADDING = 40
// Browsers require finite scroll dimensions; this creates an effectively
// unbounded workspace around the computed network in every direction.
const PAN_MARGIN = 10_000

const NODE_COLORS: Record<NetworkNode['type'], string> = {
  distributor: 'var(--accent-secondary)',
  dealer: 'var(--accent-info)',
  account: 'var(--accent-caution)',
}

function nodeRadius(node: NetworkNode): number {
  if (node.type === 'distributor') return 14
  if (node.type === 'dealer') return 8 + Math.min(6, (node.seizure_count ?? 0) * 1.5)
  return 6
}

function nodeX(node: LayoutNode): number {
  return node.x ?? WIDTH / 2
}

function nodeY(node: LayoutNode): number {
  return node.y ?? HEIGHT / 2
}

interface GraphLayout {
  nodes: LayoutNode[]
  links: LayoutLink[]
  width: number
  height: number
  offsetX: number
  offsetY: number
}

/** Static force layout: run the simulation to convergence once per dataset. */
function computeLayout(graph: GraphData): GraphLayout {
  const nodes: LayoutNode[] = graph.nodes.map((n) => ({ ...n }))
  const links: LayoutLink[] = graph.edges.map((e) => ({ ...e }))
  const simulation = forceSimulation(nodes)
    .force('link', forceLink<LayoutNode, LayoutLink>(links).id((d) => d.id).distance(55).strength(0.6))
    .force('charge', forceManyBody().strength(-160))
    .force('center', forceCenter(WIDTH / 2, HEIGHT / 2))
    .force('collide', forceCollide<LayoutNode>().radius((d) => nodeRadius(d) + 6))
    .stop()
  simulation.tick(300)

  const minX = Math.min(...nodes.map((node) => nodeX(node) - nodeRadius(node)))
  const maxX = Math.max(...nodes.map((node) => nodeX(node) + nodeRadius(node)))
  const minY = Math.min(...nodes.map((node) => nodeY(node) - nodeRadius(node)))
  const maxY = Math.max(...nodes.map((node) => nodeY(node) + nodeRadius(node)))
  const nodeWidth = maxX - minX
  const nodeHeight = maxY - minY
  const contentWidth = Math.max(WIDTH, nodeWidth + NODE_PADDING * 2)
  const contentHeight = Math.max(HEIGHT, nodeHeight + NODE_PADDING * 2)

  return {
    nodes,
    links,
    width: Math.ceil(contentWidth + PAN_MARGIN * 2),
    height: Math.ceil(contentHeight + PAN_MARGIN * 2),
    // Center every computed node inside a generous pan area. This preserves
    // the layout while keeping force outliers reachable instead of clipping.
    offsetX: PAN_MARGIN + (contentWidth - nodeWidth) / 2 - minX,
    offsetY: PAN_MARGIN + (contentHeight - nodeHeight) / 2 - minY,
  }
}

export function NetworkGraphPage() {
  const [graph, setGraph] = useState<GraphData | null>(null)
  const [accounts, setAccounts] = useState<SuspiciousAccount[]>([])
  const [selected, setSelected] = useState<NetworkNode | null>(null)
  const [error, setError] = useState<string | null>(null)
  const graphViewportRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    fetchNetworkGraph().then(setGraph).catch((e) => setError((e as Error).message))
    fetchSuspiciousAccounts().then(setAccounts).catch(console.error)
  }, [])

  const layout = useMemo(() => (graph ? computeLayout(graph) : null), [graph])

  const centerNetwork = useCallback((behavior: ScrollBehavior = 'smooth') => {
    const viewport = graphViewportRef.current
    if (!viewport) return
    viewport.scrollTo({
      left: Math.max(0, (viewport.scrollWidth - viewport.clientWidth) / 2),
      top: Math.max(0, (viewport.scrollHeight - viewport.clientHeight) / 2),
      behavior,
    })
  }, [])

  useEffect(() => {
    if (!layout) return
    const frame = window.requestAnimationFrame(() => centerNetwork('auto'))
    return () => window.cancelAnimationFrame(frame)
  }, [centerNetwork, layout])

  return (
    <div className="network-page">
      <div className="network-main">
        <div className="network-head">
          <h1>Fraud Network</h1>
          {graph && (
            <div className="network-stats">
              <span><em>{graph.stats.distributors}</em> distributors</span>
              <span><em>{graph.stats.dealers}</em> dealers</span>
              <span><em>{graph.stats.accounts}</em> accounts</span>
              <span className="stat-danger">
                <em>{graph.stats.suspicious_accounts}</em> suspicious
              </span>
            </div>
          )}
        </div>

        {error && <div className="error">{error}</div>}
        {!graph && !error && <div className="muted">Loading network…</div>}

        {layout && (
          <div className="network-canvas">
            <div
              ref={graphViewportRef}
              className="network-scroll"
              role="region"
              aria-label="Open-ended, scrollable fraud network graph"
              tabIndex={0}
            >
              <svg
                viewBox={`0 0 ${layout.width} ${layout.height}`}
                className="network-svg"
                style={{ width: layout.width, height: layout.height }}
              >
                <g transform={`translate(${layout.offsetX},${layout.offsetY})`}>
                  {layout.links.map((link, i) => {
                    const s = link.source as LayoutNode
                    const t = link.target as LayoutNode
                    return (
                      <line
                        key={i}
                        x1={nodeX(s)} y1={nodeY(s)} x2={nodeX(t)} y2={nodeY(t)}
                        className={link.type === 'OWNS' ? 'edge edge-owns' : 'edge'}
                      />
                    )
                  })}
                  {layout.nodes.map((node) => (
                    <g
                      key={node.id}
                      transform={`translate(${nodeX(node)},${nodeY(node)})`}
                      className={`net-node${selected?.id === node.id ? ' selected' : ''}${node.suspicious ? ' suspicious' : ''}`}
                      onClick={() => setSelected(node)}
                    >
                      <circle r={nodeRadius(node)} style={{ fill: NODE_COLORS[node.type] }} />
                      {(node.seizure_count ?? 0) > 0 && (
                        <circle r={nodeRadius(node) + 3.5} className="seizure-ring" />
                      )}
                      <title>{node.label}</title>
                    </g>
                  ))}
                </g>
              </svg>
            </div>
            <button type="button" className="network-reset" onClick={() => centerNetwork()}>
              Return to network center
            </button>
            <div className="network-legend">
              <span><i style={{ background: 'var(--accent-secondary)' }} /> Distributor</span>
              <span><i style={{ background: 'var(--accent-info)' }} /> Dealer</span>
              <span><i style={{ background: 'var(--accent-caution)' }} /> Bank account</span>
              <span><i className="legend-ring" /> Linked seizures</span>
            </div>
          </div>
        )}

        <section className="suspicious-section">
          <h2>Suspicious Accounts</h2>
          {accounts.length === 0 ? (
            <div className="empty">No flagged accounts</div>
          ) : (
            <table className="intel-table">
              <thead>
                <tr>
                  <th>Bank</th><th>IFSC</th><th>Dealer</th><th>Inflow</th>
                  <th>Velocity</th><th>Flags</th>
                </tr>
              </thead>
              <tbody>
                {accounts.map((a) => (
                  <tr key={a.account_id}>
                    <td>{a.bank}</td>
                    <td className="mono">{a.ifsc}</td>
                    <td>{a.dealer ? `${a.dealer.name} (${a.dealer.city})` : '—'}</td>
                    <td className="mono danger">₹{a.inflow_inr.toLocaleString()}</td>
                    <td className="mono">{a.velocity_per_day}/day</td>
                    <td className="flags">{a.reasons.join('; ')}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
      </div>

      <aside className="network-detail">
        <h2>Entity Detail</h2>
        {!selected ? (
          <div className="empty">Select a node to inspect it</div>
        ) : (
          <div className="entity-card">
            <div className="entity-type" style={{ color: NODE_COLORS[selected.type] }}>
              {selected.type.toUpperCase()}
            </div>
            <div className="entity-name">{selected.label}</div>
            <table>
              <tbody>
                {selected.city && <tr><td>City</td><td>{selected.city}</td></tr>}
                {selected.scale && <tr><td>Scale</td><td>{selected.scale}</td></tr>}
                {selected.operation_type && (
                  <tr><td>Operation</td><td>{selected.operation_type}</td></tr>
                )}
                {selected.monthly_volume != null && (
                  <tr><td>Est. volume</td>
                    <td className="mono">₹{selected.monthly_volume.toLocaleString()}/mo</td></tr>
                )}
                {selected.seizure_count != null && selected.seizure_count > 0 && (
                  <>
                    <tr><td>Seizures</td>
                      <td className="mono danger">{selected.seizure_count}</td></tr>
                    <tr><td>Notes seized</td>
                      <td className="mono danger">{selected.notes_seized?.toLocaleString()}</td></tr>
                  </>
                )}
                {selected.bank && <tr><td>Bank</td><td>{selected.bank}</td></tr>}
                {selected.inflow_inr != null && (
                  <tr><td>Inflow</td>
                    <td className="mono">₹{selected.inflow_inr.toLocaleString()}</td></tr>
                )}
                {selected.velocity_per_day != null && (
                  <tr><td>Velocity</td>
                    <td className="mono">{selected.velocity_per_day} transfers/day</td></tr>
                )}
                {selected.is_verified != null && (
                  <tr><td>KYC verified</td><td>{selected.is_verified ? 'Yes' : 'No'}</td></tr>
                )}
              </tbody>
            </table>
            {selected.suspicious && (
              <div className="entity-flag">⚠ Flagged: anomalous transfer pattern</div>
            )}
          </div>
        )}
      </aside>
    </div>
  )
}
