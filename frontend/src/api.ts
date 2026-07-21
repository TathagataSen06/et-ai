import { authHeaders, useAuthStore } from './stores/auth'
import type {
  Alert,
  Campaign,
  Cluster,
  EvidencePackage,
  HeatmapPoint,
  NetworkGraph,
  Patrol,
  PatrolRecommendation,
  ScamAnalysis,
  ScamSessionSummary,
  ScanResult,
  ScanStatistics,
  ShieldResult,
  SuspiciousAccount,
} from './types'

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    ...init,
    headers: { ...authHeaders(), ...(init?.headers ?? {}) },
  })
  if (res.status === 401) {
    useAuthStore.getState().logout()
    throw new Error('Session expired — please sign in again')
  }
  if (!res.ok) {
    const detail = await res.json().catch(() => null)
    throw new Error(detail?.detail ?? `${url} -> HTTP ${res.status}`)
  }
  return res.json()
}

export const fetchClusters = () => request<Cluster[]>('/api/v1/clusters/active')
export const fetchAlerts = () => request<Alert[]>('/api/v1/alerts/recent')
export const fetchHeatmap = () => request<HeatmapPoint[]>('/api/v1/heatmap/data')
export const fetchPatrols = () => request<Patrol[]>('/api/v1/patrols/status')
export const fetchRecommendations = () =>
  request<PatrolRecommendation[]>('/api/v1/patrols/recommendations')
export const fetchStatistics = () => request<ScanStatistics>('/api/v1/scanner/statistics')
export const fetchNetworkGraph = () => request<NetworkGraph>('/api/v1/network/graph')
export const fetchSuspiciousAccounts = () =>
  request<SuspiciousAccount[]>('/api/v1/network/suspicious-accounts')

export const generateReport = (clusterId: string) =>
  request<{ cluster_id: string; generated_at: string; generator: string; markdown: string }>(
    `/api/v1/reports/generate/${clusterId}`,
    { method: 'POST' },
  )

export const assignPatrol = (officerName: string, hotspotId: string, notes?: string) =>
  request<Patrol>('/api/v1/patrols/assign', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ officer_name: officerName, hotspot_id: hotspotId, notes: notes || null }),
  })

export async function analyzeNote(file: File): Promise<ScanResult> {
  const form = new FormData()
  form.append('file', file)
  return request<ScanResult>('/api/v1/scanner/analyze', { method: 'POST', body: form })
}

export async function verifyMedia(file: File) {
  const form = new FormData()
  form.append('file', file)
  return request<{ tamper_score: number; verdict: string; disclaimer: string }>(
    '/api/v1/citizen/media/verify',
    { method: 'POST', body: form },
  )
}

export const analyzeScamSession = (body: {
  transcript: string
  caller_number?: string | null
  channel?: string
  duration_minutes?: number | null
}) =>
  request<ScamAnalysis>('/api/v1/scam/analyze-session', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })

export const fetchScamSessions = () =>
  request<ScamSessionSummary[]>('/api/v1/scam/sessions')

export const assessMessage = (body: { message: string; lang?: string | null }) =>
  request<ShieldResult>('/api/v1/shield/assess', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })

export const fetchShieldLanguages = () =>
  request<{ code: string; name: string }[]>('/api/v1/shield/languages')

export const fetchCampaigns = () => request<Campaign[]>('/api/v1/network/campaigns')

export const generateEvidencePackage = (campaignId: string) =>
  request<EvidencePackage>(`/api/v1/network/campaigns/${campaignId}/package`, {
    method: 'POST',
  })

export const submitCitizenReport = (body: {
  description: string
  lat?: number | null
  lon?: number | null
  reporter_name?: string | null
  contact?: string | null
  media_tamper_score?: number | null
}) =>
  request<{ report_id: string; status: string }>('/api/v1/citizen/report', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
