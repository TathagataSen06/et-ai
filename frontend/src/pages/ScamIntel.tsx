import { useEffect, useState } from 'react'
import { analyzeScamSession, fetchScamSessions } from '../api'
import type { ScamAnalysis, ScamSessionSummary } from '../types'

const VERDICT_STYLES: Record<ScamAnalysis['verdict'], { color: string; label: string }> = {
  ACTIVE_SCAM_LIKELY: { color: '#dc2626', label: 'ACTIVE SCAM LIKELY' },
  SUSPICIOUS: { color: '#d97706', label: 'SUSPICIOUS — VERIFY CALLER' },
  LOW_RISK: { color: '#059669', label: 'LOW RISK' },
}

const STAGE_LABELS: Record<string, string> = {
  CONTACT: 'Contact',
  AUTHORITY_CLAIM: 'Authority claim',
  ACCUSATION: 'Accusation',
  ISOLATION: 'Isolation',
  ESCALATION: 'Escalation',
  PAYMENT_DEMAND: 'Payment demand',
}

const CHANNELS = ['VOICE', 'VIDEO', 'WHATSAPP', 'SMS']

export function ScamIntel() {
  const [transcript, setTranscript] = useState('')
  const [callerNumber, setCallerNumber] = useState('')
  const [channel, setChannel] = useState('VOICE')
  const [duration, setDuration] = useState('')
  const [result, setResult] = useState<ScamAnalysis | null>(null)
  const [sessions, setSessions] = useState<ScamSessionSummary[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const loadSessions = () => {
    fetchScamSessions().then(setSessions).catch(console.error)
  }
  useEffect(loadSessions, [])

  const analyze = async () => {
    if (transcript.trim().length < 10) return
    setBusy(true)
    setError(null)
    setResult(null)
    try {
      const analysis = await analyzeScamSession({
        transcript: transcript.trim(),
        caller_number: callerNumber.trim() || null,
        channel,
        duration_minutes: duration ? Number(duration) : null,
      })
      setResult(analysis)
      loadSessions()
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="scam-page">
      <div className="scanner-card scam-card">
        <h1>Digital Arrest Detection</h1>
        <p className="muted">
          Paste a live call transcript or suspicious message. The session is screened against
          the documented digital-arrest playbook — script stages, caller-ID spoofing signatures,
          and hostage-call metadata — and high-risk sessions raise an MHA-format alert.
        </p>

        <textarea
          className="scam-transcript"
          rows={6}
          placeholder="e.g. “I am calling from CBI headquarters… a case is registered against your Aadhaar… this is a digital arrest, stay on the video call… transfer your savings to the safe custody account…”"
          value={transcript}
          onChange={(e) => setTranscript(e.target.value)}
        />
        <div className="scam-meta-row">
          <input
            placeholder="Caller number (e.g. +92 3xx…)"
            value={callerNumber}
            onChange={(e) => setCallerNumber(e.target.value)}
          />
          <select value={channel} onChange={(e) => setChannel(e.target.value)}>
            {CHANNELS.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
          <input
            type="number"
            min={0}
            placeholder="Duration (min)"
            value={duration}
            onChange={(e) => setDuration(e.target.value)}
          />
          <button className="btn btn-primary" onClick={analyze} disabled={busy}>
            {busy ? 'Screening…' : 'Analyze Session'}
          </button>
        </div>

        {error && <div className="error">{error}</div>}

        {result && (
          <div className="result">
            <div
              className="verdict"
              style={{
                borderColor: `color-mix(in srgb, ${VERDICT_STYLES[result.verdict].color} 45%, transparent)`,
                borderLeftColor: VERDICT_STYLES[result.verdict].color,
              }}
            >
              <span className="verdict-label" style={{ color: VERDICT_STYLES[result.verdict].color }}>
                {VERDICT_STYLES[result.verdict].label}
              </span>
              <span className="score">
                risk {(result.risk_score * 100).toFixed(0)}%
                {result.claimed_agency && ` · impersonating ${result.claimed_agency}`}
                {result.script_family && (
                  <span className="mode-tag">{result.script_family.replaceAll('_', ' ')}</span>
                )}
              </span>
            </div>

            <h3>Call-flow stages</h3>
            <div className="stage-track">
              {result.stages.map((s) => (
                <div key={s.stage} className={s.matched ? 'stage-chip matched' : 'stage-chip'}>
                  <span className="stage-dot" />
                  {STAGE_LABELS[s.stage] ?? s.stage}
                </div>
              ))}
            </div>

            {result.spoof_flags.length > 0 && (
              <>
                <h3>Spoofing signatures</h3>
                <ul className="next-steps danger-list">
                  {result.spoof_flags.map((f) => <li key={f}>{f}</li>)}
                </ul>
              </>
            )}

            {result.indicators.length > 0 && (
              <>
                <h3>Indicators</h3>
                <ul className="next-steps">
                  {result.indicators.slice(0, 6).map((i) => <li key={i}>{i}</li>)}
                </ul>
              </>
            )}

            <div className="shield-actions">{result.recommended_action}</div>

            {result.mha_alert && (
              <div className="mha-alert">
                <div className="mha-head">
                  <span className="mha-badge">MHA / I4C ALERT GENERATED</span>
                  <span className="mono">{result.mha_alert.reference}</span>
                </div>
                <ul>
                  {result.mha_alert.recommended_dissemination.map((d) => <li key={d}>{d}</li>)}
                </ul>
                <div className="mono muted">{result.mha_alert.citizen_guidance}</div>
              </div>
            )}
          </div>
        )}
      </div>

      <section className="scanner-card scam-card">
        <h2>Recent Sessions</h2>
        {sessions.length === 0 ? (
          <div className="empty">No sessions screened yet</div>
        ) : (
          <table className="intel-table">
            <thead>
              <tr>
                <th>Caller</th><th>Channel</th><th>Agency claimed</th>
                <th>Script family</th><th>Risk</th><th>Verdict</th>
              </tr>
            </thead>
            <tbody>
              {sessions.slice(0, 12).map((s) => (
                <tr key={s.id}>
                  <td className="mono">{s.caller_number ?? 'withheld'}</td>
                  <td>{s.channel}</td>
                  <td>{s.claimed_agency ?? '—'}</td>
                  <td>{s.script_family?.replaceAll('_', ' ') ?? '—'}</td>
                  <td className="mono danger">{(s.risk_score * 100).toFixed(0)}%</td>
                  <td>
                    <span className={`session-verdict v-${s.verdict.toLowerCase()}`}>
                      {s.verdict.replaceAll('_', ' ')}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  )
}
