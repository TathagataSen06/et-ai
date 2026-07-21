import { useRef, useState } from 'react'
import { submitCitizenReport, verifyMedia } from '../api'
import { useToastStore } from '../stores/toasts'

interface MediaCheck {
  tamper_score: number
  verdict: string
}

interface Coordinates {
  lat: number
  lon: number
}

function parseCoordinates(latText: string, lonText: string): Coordinates | null {
  if (!latText.trim() && !lonText.trim()) return null
  const lat = Number(latText)
  const lon = Number(lonText)
  if (!Number.isFinite(lat) || !Number.isFinite(lon) || lat < -90 || lat > 90 || lon < -180 || lon > 180) {
    return null
  }
  return { lat, lon }
}

function locationErrorMessage(error: GeolocationPositionError): string {
  if (error.code === error.PERMISSION_DENIED) {
    return 'Location permission is blocked. Allow location access for this site, then try again.'
  }
  if (error.code === error.POSITION_UNAVAILABLE) {
    return 'Your device could not determine a location. Check that location services are enabled.'
  }
  if (error.code === error.TIMEOUT) {
    return 'Location request timed out. Try again or enter the coordinates manually.'
  }
  return 'Location could not be determined. Try again or enter the coordinates manually.'
}

export function CitizenReportPage() {
  const pushToast = useToastStore((s) => s.push)
  const [description, setDescription] = useState('')
  const [name, setName] = useState('')
  const [contact, setContact] = useState('')
  const [coords, setCoords] = useState<Coordinates | null>(null)
  const [latitude, setLatitude] = useState('')
  const [longitude, setLongitude] = useState('')
  const [locationError, setLocationError] = useState<string | null>(null)
  const [media, setMedia] = useState<MediaCheck | null>(null)
  const [checkingMedia, setCheckingMedia] = useState(false)
  const [busy, setBusy] = useState(false)
  const [locating, setLocating] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const manualCoords = parseCoordinates(latitude, longitude)
  const hasManualLocation = Boolean(latitude.trim() || longitude.trim())
  const reportCoords = coords ?? manualCoords

  const locate = () => {
    setLocationError(null)
    if (!window.isSecureContext) {
      setLocationError('Location requires HTTPS or localhost. Open the dashboard from a secure address and try again.')
      return
    }
    if (!navigator.geolocation) {
      setLocationError('This browser does not support location services. Enter the coordinates manually.')
      return
    }
    setLocating(true)
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const detected = { lat: pos.coords.latitude, lon: pos.coords.longitude }
        setCoords(detected)
        setLatitude(detected.lat.toFixed(6))
        setLongitude(detected.lon.toFixed(6))
        setLocating(false)
      },
      (error) => {
        setLocationError(locationErrorMessage(error))
        setLocating(false)
      },
      { enableHighAccuracy: true, maximumAge: 60_000, timeout: 15_000 },
    )
  }

  const editCoordinate = (setter: (value: string) => void, value: string) => {
    setCoords(null)
    setLocationError(null)
    setter(value)
  }

  const checkMedia = async (file: File | undefined) => {
    if (!file) return
    setCheckingMedia(true)
    setMedia(null)
    try {
      const result = await verifyMedia(file)
      setMedia(result)
    } catch (err) {
      pushToast({ severity: 'MEDIUM', title: 'Media check failed', message: (err as Error).message })
    } finally {
      setCheckingMedia(false)
    }
  }

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (busy) return
    if (hasManualLocation && !manualCoords) {
      setLocationError('Enter both valid coordinates: latitude from -90 to 90 and longitude from -180 to 180.')
      return
    }
    setBusy(true)
    try {
      const result = await submitCitizenReport({
        description: description.trim(),
        lat: reportCoords?.lat ?? null,
        lon: reportCoords?.lon ?? null,
        reporter_name: name.trim() || null,
        contact: contact.trim() || null,
        media_tamper_score: media?.tamper_score ?? null,
      })
      pushToast({
        severity: 'INFO',
        title: 'Report submitted',
        message: `Reference ${result.report_id.slice(0, 8).toUpperCase()} — the command center has been alerted`,
      })
      setDescription('')
      setMedia(null)
      if (fileRef.current) fileRef.current.value = ''
    } catch (err) {
      pushToast({ severity: 'MEDIUM', title: 'Submission failed', message: (err as Error).message })
    } finally {
      setBusy(false)
    }
  }

  const verdictColor =
    media == null ? undefined
    : media.tamper_score > 0.65 ? 'var(--accent-danger)'
    : media.tamper_score > 0.4 ? 'var(--accent-caution)'
    : 'var(--accent-success)'

  return (
    <div className="scanner-page">
      <div className="scanner-card">
        <h1>Report Counterfeit Activity</h1>
        <p className="muted">
          Citizen reports feed the command center directly. Reports can also be sent via
          WhatsApp — message your location and description to the task-force number.
        </p>

        <form className="report-form modal-form" onSubmit={submit}>
          <label htmlFor="rep-desc">What did you observe?</label>
          <textarea
            id="rep-desc"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="e.g. Received suspicious ₹500 notes as change at the market…"
            required
            minLength={5}
          />

          <div className="report-grid">
            <div>
              <label htmlFor="rep-name">Your name (optional)</label>
              <input id="rep-name" value={name} onChange={(e) => setName(e.target.value)} />
            </div>
            <div>
              <label htmlFor="rep-contact">Contact (optional)</label>
              <input id="rep-contact" value={contact} onChange={(e) => setContact(e.target.value)} />
            </div>
          </div>

          <label>Location</label>
          <div className="report-row">
            <button type="button" className="btn btn-secondary" onClick={locate} disabled={locating}>
              {locating ? 'Locating…' : 'Use my location'}
            </button>
            <span className="mono muted-inline">
              {reportCoords ? `${reportCoords.lat.toFixed(4)}°, ${reportCoords.lon.toFixed(4)}°` : 'not set'}
            </span>
          </div>
          <div className="location-coordinates">
            <input
              aria-label="Latitude"
              inputMode="decimal"
              type="number"
              min="-90"
              max="90"
              step="any"
              placeholder="Latitude (optional)"
              value={latitude}
              onChange={(e) => editCoordinate(setLatitude, e.target.value)}
            />
            <input
              aria-label="Longitude"
              inputMode="decimal"
              type="number"
              min="-180"
              max="180"
              step="any"
              placeholder="Longitude (optional)"
              value={longitude}
              onChange={(e) => editCoordinate(setLongitude, e.target.value)}
            />
          </div>
          {locationError && <div className="location-error" role="alert">{locationError}</div>}

          <label htmlFor="rep-media">Evidence photo (optional — screened for tampering)</label>
          <input
            id="rep-media"
            ref={fileRef}
            type="file"
            accept="image/*"
            onChange={(e) => checkMedia(e.target.files?.[0])}
          />
          {checkingMedia && <div className="analyzing">Screening media authenticity…</div>}
          {media && (
            <div className="media-verdict" style={{ borderLeftColor: verdictColor }}>
              <strong style={{ color: verdictColor }}>{media.verdict.replaceAll('_', ' ')}</strong>
              <span className="mono"> · tamper score {(media.tamper_score * 100).toFixed(0)}%</span>
            </div>
          )}

          <div className="modal-actions">
            <button
              className="btn btn-primary"
              type="submit"
              disabled={busy || description.trim().length < 5}
            >
              {busy ? 'Submitting…' : 'Submit Report'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
