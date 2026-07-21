export interface Cluster {
  id: string
  center_lat: number
  center_lon: number
  radius_km: number
  seizure_count: number
  total_notes: number
  avg_confidence: number
  risk_score: number
  stability: number
  risk_level: 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL'
  patrol_priority: number
  last_seizure_date: string | null
  updated_at: string
}

export interface Alert {
  id: string
  event_type: string
  lat: number | null
  lon: number | null
  severity: string
  description: string
  created_at: string
}

export interface LiveAlert {
  type: string
  severity?: string
  message?: string
  lat?: number | null
  lon?: number | null
  sent_at: string
}

export interface HeatmapPoint {
  lat: number
  lon: number
  weight: number
}

export interface PatrolRecommendation {
  hotspot_id: string
  center_lat: number
  center_lon: number
  risk_level: string
  patrol_priority: number
  seizure_count: number
  predicted_intensity: number
  estimated_coverage_km2: number
  expected_duration_hours: number
}

export interface Patrol {
  id: string
  officer_name: string
  hotspot_id: string | null
  priority: number
  status: 'PENDING' | 'ACTIVE' | 'COMPLETED'
  date_assigned: string
  notes: string | null
}

export interface FeatureResult {
  confidence: number
  detail: Record<string, unknown>
  status: string
}

export interface ScanResult {
  scan_id: string
  counterfeit_score: number
  recommendation: 'LIKELY_GENUINE' | 'SUSPICIOUS' | 'LIKELY_COUNTERFEIT'
  alert_level: 'LOW' | 'MEDIUM' | 'HIGH'
  denomination: string
  detailed_breakdown: Record<string, FeatureResult>
  next_steps: string[]
  created_at: string
  uncertainty: number
  analysis_mode: 'fast' | 'consensus'
  verdict_reason: string
  effective_thresholds: Record<string, number>
  calibrated: boolean
  genuine_percentile: number | null
}

export interface ScanStatistics {
  total_scans: number
  avg_counterfeit_score: number
  by_recommendation: Record<string, number>
  by_denomination: Record<string, number>
  daily_counts: { date: string; count: number; avg_score: number }[]
}

export const RISK_COLORS: Record<string, string> = {
  CRITICAL: '#dc2626',
  HIGH: '#ea580c',
  MEDIUM: '#d97706',
  LOW: '#0891b2',
}

export interface Toast {
  id: number
  severity: 'INFO' | 'MEDIUM' | 'HIGH'
  title: string
  message: string
  meta?: string
  leaving?: boolean
}

export interface NetworkNode {
  id: string
  type: 'distributor' | 'dealer' | 'account' | 'phone' | 'device'
  label: string
  city?: string
  scale?: string
  operation_type?: string
  monthly_volume?: number
  seizure_count?: number
  notes_seized?: number
  bank?: string
  inflow_inr?: number
  velocity_per_day?: number
  is_verified?: boolean
  suspicious?: boolean
  sessions?: number
  max_risk_score?: number
}

export interface NetworkEdge {
  source: string
  target: string
  type: 'DISTRIBUTES_TO' | 'OWNS' | 'LINKED_TO' | 'FUNNELS_TO' | 'OPERATES'
}

export interface NetworkGraph {
  nodes: NetworkNode[]
  edges: NetworkEdge[]
  stats: {
    distributors: number
    dealers: number
    accounts: number
    phones: number
    devices: number
    linked_seizures: number
    suspicious_accounts: number
  }
}

export interface SuspiciousAccount {
  account_id: string
  bank: string
  ifsc: string
  inflow_inr: number
  velocity_per_day: number
  is_verified: boolean
  dealer: { id: string; name: string; city: string } | null
  reasons: string[]
}

export interface ScamStage {
  stage: string
  matched: boolean
  evidence: string[]
}

export interface MhaAlert {
  alert_type: string
  reference: string
  generated_at: string
  claimed_agency: string | null
  recommended_dissemination: string[]
  citizen_guidance: string
}

export interface ScamAnalysis {
  session_id: string
  risk_score: number
  verdict: 'ACTIVE_SCAM_LIKELY' | 'SUSPICIOUS' | 'LOW_RISK'
  severity: 'HIGH' | 'MEDIUM' | 'LOW'
  stages: ScamStage[]
  indicators: string[]
  spoof_flags: string[]
  claimed_agency: string | null
  script_family: string | null
  recommended_action: string
  mha_alert: MhaAlert | null
}

export interface ScamSessionSummary {
  id: string
  caller_number: string | null
  channel: string
  claimed_agency: string | null
  script_family: string | null
  risk_score: number
  verdict: string
  spoof_flags: string[]
  alerted: boolean
  created_at: string
}

export interface ShieldResult {
  verdict: 'HIGH_RISK' | 'SUSPICIOUS' | 'LIKELY_SAFE'
  risk_score: number
  fraud_type: string | null
  indicators: string[]
  lang: string
  advisory: string
  actions: string
  ivr_text: string
  helpline: string
  report_url: string
}

export interface Campaign {
  campaign_id: string
  label: string
  script_family: string | null
  risk_level: 'LOW' | 'MEDIUM' | 'HIGH'
  max_risk_score: number
  session_count: number
  caller_numbers: string[]
  device_hashes: string[]
  linked_report_count: number
  mule_account_ids: string[]
  first_activity: string | null
  last_activity: string | null
}

export interface EvidencePackage {
  package_type: string
  reference: string
  campaign: Omit<Campaign, 'session_ids'>
  call_timeline: unknown[]
  sessions: unknown[]
  victim_reports: unknown[]
  mule_accounts: unknown[]
  integrity_sha256: string
  provenance: { generated_by: string; methodology: string }
}
