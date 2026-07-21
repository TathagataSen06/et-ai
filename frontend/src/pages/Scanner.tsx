import { useRef, useState } from 'react'
import { analyzeNote } from '../api'
import type { ScanResult } from '../types'

const VERDICT_STYLES: Record<ScanResult['recommendation'], { color: string; label: string }> = {
  LIKELY_COUNTERFEIT: { color: '#dc2626', label: 'LIKELY COUNTERFEIT' },
  SUSPICIOUS: { color: '#d97706', label: 'SUSPICIOUS — MANUAL REVIEW' },
  LIKELY_GENUINE: { color: '#059669', label: 'LIKELY GENUINE' },
}

const FEATURE_LABELS: Record<string, string> = {
  microprint: 'Microprint pattern',
  security_thread: 'Security thread',
  hologram: 'Hologram / colour shift',
  intaglio: 'Intaglio (raised print)',
  serial_number: 'Serial number panel',
  paper_texture: 'Paper texture',
}

export function Scanner() {
  const [result, setResult] = useState<ScanResult | null>(null)
  const [preview, setPreview] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const handleFile = async (file: File | undefined) => {
    if (!file) return
    setError(null)
    setResult(null)
    setBusy(true)
    setPreview(URL.createObjectURL(file))
    try {
      setResult(await analyzeNote(file))
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="scanner-page">
      <div className="scanner-card">
        <h1>Currency Scanner</h1>
        <p className="muted">
          Upload a photo of an Indian currency note. Six security features are checked with
          rule-based computer vision — this is a screening aid, not a certification.
        </p>

        <div
          className="dropzone"
          onClick={() => inputRef.current?.click()}
          onDragOver={(e) => e.preventDefault()}
          onDrop={(e) => {
            e.preventDefault()
            handleFile(e.dataTransfer.files[0])
          }}
        >
          {preview ? (
            <img src={preview} alt="uploaded note" className="preview" />
          ) : (
            <span>Click or drop a note image here</span>
          )}
        </div>
        <input
          ref={inputRef}
          type="file"
          accept="image/*"
          hidden
          onChange={(e) => handleFile(e.target.files?.[0])}
        />

        {busy && <div className="analyzing">Analyzing security features…</div>}
        {error && <div className="error">{error}</div>}

        {result && (
          <div className="result">
            <div
              className="verdict"
              style={{
                borderColor: `color-mix(in srgb, ${VERDICT_STYLES[result.recommendation].color} 45%, transparent)`,
                borderLeftColor: VERDICT_STYLES[result.recommendation].color,
              }}
            >
              <span
                className="verdict-label"
                style={{ color: VERDICT_STYLES[result.recommendation].color }}
              >
                {VERDICT_STYLES[result.recommendation].label}
              </span>
              <span className="score">
                score {(result.counterfeit_score * 100).toFixed(0)}%
                {result.uncertainty > 0 && ` ±${(result.uncertainty * 100).toFixed(0)}%`}
                {result.denomination !== 'UNKNOWN' && ` · ₹${result.denomination}`}
                {result.calibrated && result.genuine_percentile != null && (
                  <> · deviation pct {result.genuine_percentile.toFixed(1)}</>
                )}
                <span className="mode-tag">
                  {result.analysis_mode === 'consensus' ? 'consensus ensemble' : 'fast pass'}
                </span>
                {result.calibrated && (
                  <span className="mode-tag">conformal · genuine-referenced</span>
                )}
              </span>
            </div>
            {(result.verdict_reason.includes('instability') ||
              result.verdict_reason.includes('retake')) && (
              <div className="verdict-reason">
                ⚠ {result.verdict_reason.includes('retake')
                  ? `Capture quality issue: ${result.verdict_reason}.`
                  : 'Verdict was unstable across capture perturbations — the review band was widened. Retake in steadier, brighter conditions for a crisper result.'}
              </div>
            )}

            <h3>Feature breakdown</h3>
            {Object.entries(result.detailed_breakdown).map(([name, feature]) => (
              <div key={name} className="feature-row">
                <span className="feature-name">{FEATURE_LABELS[name] ?? name}</span>
                <div className="bar-track">
                  <div
                    className="bar-fill"
                    style={{
                      width: `${feature.confidence * 100}%`,
                      background:
                        feature.confidence > 0.5
                          ? '#059669'
                          : feature.confidence > 0.25
                            ? '#d97706'
                            : '#dc2626',
                    }}
                  />
                </div>
                <span className="feature-status">{feature.status}</span>
              </div>
            ))}

            <h3>Next steps</h3>
            <ul className="next-steps">
              {result.next_steps.map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  )
}
