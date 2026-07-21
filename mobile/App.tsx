/**
 * Netra Mobile Scanner — capture a note photo, attach GPS, and screen it
 * against the Netra API. Run with `npx expo start` (set the API URL on the
 * settings row to your backend's LAN address, e.g. http://192.168.1.10:8000).
 */
import { useRef, useState } from 'react'
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native'
import { CameraView, useCameraPermissions } from 'expo-camera'
import * as Location from 'expo-location'
import { StatusBar } from 'expo-status-bar'

interface FeatureResult {
  confidence: number
  status: string
}

interface ScanResult {
  scan_id: string
  counterfeit_score: number
  recommendation: 'LIKELY_GENUINE' | 'SUSPICIOUS' | 'LIKELY_COUNTERFEIT'
  denomination: string
  detailed_breakdown: Record<string, FeatureResult>
  next_steps: string[]
}

const VERDICTS: Record<ScanResult['recommendation'], { color: string; label: string }> = {
  LIKELY_COUNTERFEIT: { color: '#dc2626', label: 'LIKELY COUNTERFEIT' },
  SUSPICIOUS: { color: '#d97706', label: 'SUSPICIOUS — MANUAL REVIEW' },
  LIKELY_GENUINE: { color: '#059669', label: 'LIKELY GENUINE' },
}

const FEATURE_LABELS: Record<string, string> = {
  microprint: 'Microprint',
  security_thread: 'Security thread',
  hologram: 'Hologram',
  intaglio: 'Intaglio print',
  serial_number: 'Serial panel',
  paper_texture: 'Paper texture',
}

export default function App() {
  const [permission, requestPermission] = useCameraPermissions()
  const [apiUrl, setApiUrl] = useState('http://192.168.1.10:8000')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<ScanResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const cameraRef = useRef<CameraView>(null)

  const captureAndAnalyze = async () => {
    if (!cameraRef.current || busy) return
    setBusy(true)
    setError(null)
    setResult(null)
    try {
      const photo = await cameraRef.current.takePictureAsync({ quality: 0.8 })
      if (!photo) throw new Error('Capture failed')

      let coords: { latitude: number; longitude: number } | null = null
      const { status } = await Location.requestForegroundPermissionsAsync()
      if (status === 'granted') {
        const position = await Location.getCurrentPositionAsync({
          accuracy: Location.Accuracy.Balanced,
        })
        coords = position.coords
      }

      const form = new FormData()
      form.append('file', {
        uri: photo.uri,
        type: 'image/jpeg',
        name: 'note.jpg',
      } as unknown as Blob)
      if (coords) {
        form.append('lat', String(coords.latitude))
        form.append('lon', String(coords.longitude))
      }
      form.append('user_type', 'Citizen')

      const response = await fetch(`${apiUrl.replace(/\/$/, '')}/api/v1/scanner/analyze`, {
        method: 'POST',
        body: form,
      })
      if (!response.ok) {
        const detail = await response.json().catch(() => null)
        throw new Error(detail?.detail ?? `HTTP ${response.status}`)
      }
      setResult((await response.json()) as ScanResult)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  if (!permission) return <View style={styles.container} />

  if (!permission.granted) {
    return (
      <View style={[styles.container, styles.center]}>
        <Text style={styles.title}>◆ Netra Scanner</Text>
        <Text style={styles.muted}>Camera access is needed to scan currency notes.</Text>
        <Pressable style={styles.primaryBtn} onPress={requestPermission}>
          <Text style={styles.primaryBtnText}>GRANT CAMERA ACCESS</Text>
        </Pressable>
      </View>
    )
  }

  return (
    <View style={styles.container}>
      <StatusBar style="dark" />
      <View style={styles.header}>
        <Text style={styles.title}>◆ Netra Scanner</Text>
        <TextInput
          style={styles.apiInput}
          value={apiUrl}
          onChangeText={setApiUrl}
          autoCapitalize="none"
          autoCorrect={false}
          placeholder="API URL"
        />
      </View>

      <View style={styles.cameraWrap}>
        <CameraView ref={cameraRef} style={styles.camera} facing="back" />
        <View style={styles.frameGuide} pointerEvents="none" />
      </View>

      <Pressable
        style={[styles.primaryBtn, busy && styles.btnDisabled]}
        onPress={captureAndAnalyze}
        disabled={busy}
      >
        {busy ? (
          <ActivityIndicator color="#ffffff" />
        ) : (
          <Text style={styles.primaryBtnText}>SCAN NOTE</Text>
        )}
      </Pressable>

      <ScrollView style={styles.results}>
        {error && <Text style={styles.error}>{error}</Text>}
        {result && (
          <View>
            <View
              style={[styles.verdict, { borderColor: VERDICTS[result.recommendation].color }]}
            >
              <Text style={[styles.verdictText, { color: VERDICTS[result.recommendation].color }]}>
                {VERDICTS[result.recommendation].label}
              </Text>
              <Text style={styles.score}>
                score {(result.counterfeit_score * 100).toFixed(0)}%
                {result.denomination !== 'UNKNOWN' ? ` · ₹${result.denomination}` : ''}
              </Text>
            </View>

            {Object.entries(result.detailed_breakdown).map(([name, feature]) => (
              <View key={name} style={styles.featureRow}>
                <Text style={styles.featureName}>{FEATURE_LABELS[name] ?? name}</Text>
                <View style={styles.barTrack}>
                  <View
                    style={[
                      styles.barFill,
                      {
                        width: `${Math.round(feature.confidence * 100)}%`,
                        backgroundColor:
                          feature.confidence > 0.5
                            ? '#059669'
                            : feature.confidence > 0.25
                              ? '#d97706'
                              : '#dc2626',
                      },
                    ]}
                  />
                </View>
                <Text style={styles.featureStatus}>{feature.status}</Text>
              </View>
            ))}

            <Text style={styles.nextTitle}>NEXT STEPS</Text>
            {result.next_steps.map((step) => (
              <Text key={step} style={styles.step}>
                • {step}
              </Text>
            ))}
          </View>
        )}
      </ScrollView>
    </View>
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#f8fafc', paddingTop: 56 },
  center: { alignItems: 'center', justifyContent: 'center', padding: 24, gap: 14 },
  header: { paddingHorizontal: 18, marginBottom: 10 },
  title: { fontSize: 22, fontWeight: '700', color: '#0f172a' },
  muted: { fontSize: 14, color: '#475569', textAlign: 'center' },
  apiInput: {
    marginTop: 8,
    borderWidth: 1,
    borderColor: 'rgba(100,116,139,0.25)',
    borderRadius: 6,
    paddingHorizontal: 10,
    paddingVertical: 6,
    fontSize: 12,
    color: '#475569',
    backgroundColor: '#ffffff',
  },
  cameraWrap: { marginHorizontal: 18, borderRadius: 12, overflow: 'hidden', height: 260 },
  camera: { flex: 1 },
  frameGuide: {
    position: 'absolute',
    left: '8%',
    right: '8%',
    top: '22%',
    bottom: '22%',
    borderWidth: 2,
    borderColor: 'rgba(255,255,255,0.75)',
    borderRadius: 8,
    borderStyle: 'dashed',
  },
  primaryBtn: {
    backgroundColor: '#0891b2',
    marginHorizontal: 18,
    marginTop: 14,
    borderRadius: 6,
    paddingVertical: 13,
    alignItems: 'center',
  },
  btnDisabled: { opacity: 0.6 },
  primaryBtnText: { color: '#ffffff', fontWeight: '700', letterSpacing: 1, fontSize: 13 },
  results: { flex: 1, marginTop: 16, paddingHorizontal: 18 },
  error: { color: '#dc2626', fontSize: 13 },
  verdict: {
    borderWidth: 1,
    borderLeftWidth: 5,
    borderRadius: 10,
    padding: 14,
    marginBottom: 14,
    backgroundColor: '#ffffff',
  },
  verdictText: { fontWeight: '700', letterSpacing: 1, fontSize: 14 },
  score: { color: '#475569', fontSize: 12, marginTop: 4 },
  featureRow: { flexDirection: 'row', alignItems: 'center', marginBottom: 8, gap: 8 },
  featureName: { width: 110, fontSize: 12, color: '#0f172a' },
  barTrack: {
    flex: 1,
    height: 8,
    backgroundColor: 'rgba(100,116,139,0.12)',
    borderRadius: 4,
    overflow: 'hidden',
  },
  barFill: { height: '100%', borderRadius: 4 },
  featureStatus: { width: 78, fontSize: 10, color: '#94a3b8', textAlign: 'right' },
  nextTitle: {
    fontSize: 11,
    fontWeight: '700',
    letterSpacing: 1.2,
    color: '#475569',
    marginTop: 14,
    marginBottom: 6,
  },
  step: { fontSize: 13, color: '#0f172a', marginBottom: 5, lineHeight: 19 },
})
