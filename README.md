# Project Netra — Digital Public Safety Intelligence

Production-grade prototype of a digital public safety platform for law enforcement,
financial institutions, and citizens: counterfeit-currency detection, digital-arrest
scam-session screening, fraud-campaign graph intelligence, geospatial hotspot mapping,
and a multilingual citizen Fraud Shield. Rule-based OpenCV detection + DBSCAN
clustering + statsmodels forecasting + deterministic scam classification + campaign
graph analysis, running entirely on synthetic data.

| Component | Stack |
|---|---|
| API | FastAPI (Python 3.11), SQLAlchemy 2, JWT auth (OAuth2), rate limiting, audit logs, WebSocket alerts |
| Computer vision | OpenCV — six security-feature detectors + ELA/noise media forensics |
| Geospatial | scikit-learn DBSCAN (haversine), risk scoring, patrol priorities |
| Forecasting | statsmodels seasonal decomposition + IsolationForest spike detection |
| Fraud network | SQL graph (distributors → dealers → accounts → scam numbers → devices) + optional Neo4j sync |
| Scam detection | Rule-based digital-arrest classifier (script stages, spoofing signatures, MHA-format alerts) |
| Citizen Fraud Shield | Instant fraud triage in English + 12 regional languages (web, WhatsApp `CHECK`, IVR-length output) |
| Campaign intelligence | Union-find clustering over shared numbers/devices → SHA-256-hashed evidence packages |
| Reports | Claude API / Ollama / template fallback intelligence reports |
| Dashboard | React 19 + TypeScript + Vite, Leaflet, Recharts, zustand, d3-force |
| Mobile | React Native Expo scanner (camera + GPS) in `mobile/` |
| Observability | Prometheus `/metrics`, Grafana provisioning, audit-log table |
| Packaging | Docker Compose (PostGIS, Redis, Neo4j, Celery, Prometheus, Grafana), k8s manifests, GHCR CI |

## Quick start (local dev)

```bash
# Backend
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt        # Windows
.venv/Scripts/python scripts/synthetic_data_generator.py
.venv/Scripts/python scripts/ingest_data.py          # seeds DB + computes hotspots
.venv/Scripts/python -m uvicorn app.main:app --port 8000

# Frontend (second terminal)
cd frontend
npm install
npm run dev            # http://localhost:3000 (proxies /api and /ws to :8000)
```

Sign in with the demo credentials `commander` / `netra-demo` (override via
`NETRA_COMMANDER_PASSWORD`). API docs: http://localhost:8000/docs

## Quick start (Docker)

```bash
docker compose up --build
# dashboard http://localhost:3000 · API http://localhost:8000
# Neo4j browser http://localhost:7474 · Prometheus :9090 · Grafana :3001
# `seeder` populates Postgres + the Neo4j fraud graph; Celery beat refreshes hotspots
```

Kubernetes: `kubectl apply -f k8s/netra.yaml` (images pushed to GHCR by CI).

## Features

**Scanner** — `POST /api/v1/scanner/analyze` (+ `batch-analyze`): six OpenCV detectors
score genuine security features (microprint, security thread, hologram, intaglio,
serial panel, paper texture); `counterfeit_score = 1 − weighted authenticity`.
GPS comes from form fields or JPEG EXIF. High-risk scans push WebSocket alerts.

**Geospatial engine** — DBSCAN (2 km eps, haversine) hotspots with the spec risk
formula (`0.3·freq + 0.3·volume + 0.2·density + 0.2·recency`), patrol priorities,
GeoJSON output (`?format=geojson`), search (`POST /clusters/search`), heatmap data.

**Predictive patrols** — per-hotspot daily series → seasonal decomposition (weekly
period) trend forecasts + IsolationForest spike anomalies
(`GET /api/v1/patrols/predictions`), recommendations, assignment, officer routes.

**Fraud network** — distributor → dealer → bank-account graph with seizure links,
suspicious-account flagging (velocity/inflow heuristics), d3-force dashboard
visualization, optional Neo4j Community sync (`NETRA_NEO4J_URI`).

**Intelligence reports** — `POST /api/v1/reports/generate/{cluster_id}`: grounded
markdown reports via Claude API (`NETRA_ANTHROPIC_API_KEY`), Groq/Llama
(`NETRA_GROQ_API_KEY`), Ollama (`NETRA_OLLAMA_URL`), or the built-in template
(default, offline). Put keys in `backend/.env` (gitignored), never in source.

**Citizen reporting** — web form, Twilio-compatible WhatsApp webhook
(`POST /api/v1/citizen/webhooks/whatsapp`, body `"<lat>,<lon> <text>"`), and
evidence-photo tamper screening (ELA + noise inconsistency) at
`POST /api/v1/citizen/media/verify`.

**Digital-arrest scam detection** — `POST /api/v1/scam/analyze-session`: deterministic
classifier over the documented digital-arrest anatomy — six-stage script progression
(contact → authority claim → accusation → isolation → escalation → payment demand),
caller-ID spoofing signatures (foreign prefixes, agency-over-WhatsApp/video, mobile
CLI as government line), and hostage-call metadata. HIGH sessions raise a WebSocket
alert and an MHA/I4C-format dissemination package (telecom flag, bank hold, I4C
correlation). Script-family attribution: CBI digital arrest, parcel/NCB, TRAI SIM,
bank-KYC/ED.

**Citizen Fraud Shield** — `POST /api/v1/shield/assess`: instant triage of suspicious
calls/messages across ten fraud families (digital arrest, UPI collect, OTP theft,
phishing, KYC expiry, utility disconnect, lottery, parcel, job, investment, army-OLX)
with advisories in English + 12 regional languages (script auto-detected), helpline
1930 + cybercrime.gov.in guidance, and IVR-length output. On WhatsApp, prefix a
message with `CHECK:` for a triage reply instead of report intake.

**Campaign intelligence** — `GET /api/v1/network/campaigns`: scam sessions clustered
into operations via union-find over shared caller numbers and device fingerprints
(SIM-rotation signature); campaigns link victim reports and mule accounts.
`POST /api/v1/network/campaigns/{id}/package` emits a canonical-JSON evidence package
with SHA-256 integrity hash and provenance for court-grade verifiability.

**Security & ops** — OAuth2 password flow → JWT (roles COMMAND/OFFICER), protected
law-enforcement endpoints, sliding-window rate limiting, audit-log table for all
mutations, Prometheus request metrics at `/metrics`.

**Inference-time intelligence (zero training)** — training-free analogs of classic
ML training strategies, applied to the existing pipeline:

- *Perturbation consensus ensemble* (uncertainty quantification): borderline scans
  are re-analyzed under deterministic capture jitter (rotation, rescale, gamma,
  JPEG round-trip); the score spread is reported as `uncertainty`.
- *Adaptive verdict margins* (adaptive-margin triplet loss): the SUSPICIOUS review
  band widens with that uncertainty — unstable samples need more evidence.
- *Reliability-weighted fusion* (dynamic focal alpha): features the capture can't
  support (clipped exposure, low resolution) are down-weighted and renormalized,
  so "couldn't measure" stops counting as counterfeit evidence.
- *Two-tier screening* (knowledge-distillation deployment): a fast single pass
  settles clear cases; only ambiguous scores pay for the full ensemble.
- *Multi-scale detail gain* (progressive resizing): genuine microprint gains
  high-frequency energy from half→full resolution; flat reproductions don't.
- *Consensus clustering* (ensemble disagreement): DBSCAN re-runs at perturbed
  radii give every hotspot a `stability` score — fragile chain-clusters are
  visibly distinguished from solid hotspots.

## API surface

```
POST /api/v1/auth/login               OAuth2 password -> JWT     GET  /api/v1/auth/me
POST /api/v1/scanner/analyze          public                     POST /api/v1/scanner/batch-analyze
GET  /api/v1/scanner/statistics       public
GET  /api/v1/clusters/active[?format=geojson]                    POST /api/v1/clusters/search
GET  /api/v1/clusters/{id}/details                               POST /api/v1/clusters/refresh   🔒
GET  /api/v1/heatmap/data             public
GET  /api/v1/patrols/recommendations  🔒                         GET  /api/v1/patrols/predictions 🔒
POST /api/v1/patrols/assign           🔒                         GET  /api/v1/patrols/status      🔒
GET  /api/v1/patrols/{officer}/route  🔒                         PUT  /api/v1/patrols/{id}/status 🔒
GET  /api/v1/alerts/recent            public                     WS   /ws/dashboard
GET  /api/v1/network/graph            🔒                         GET  /api/v1/network/dealer/{id} 🔒
GET  /api/v1/network/suspicious-accounts 🔒                      POST /api/v1/network/sync-neo4j  🔒
POST /api/v1/reports/generate/{id}    🔒
POST /api/v1/citizen/report           public                     GET  /api/v1/citizen/reports     🔒
POST /api/v1/citizen/media/verify     public                     POST /api/v1/citizen/webhooks/whatsapp
POST /api/v1/scam/analyze-session     public                     GET  /api/v1/scam/sessions       🔒
GET  /api/v1/scam/sessions/{id}/alert 🔒
POST /api/v1/shield/assess            public                     GET  /api/v1/shield/languages    public
GET  /api/v1/network/campaigns        🔒                         POST /api/v1/network/campaigns/{id}/package 🔒
GET  /health · GET /metrics
```

## Tests

```bash
cd backend && .venv/Scripts/python -m pytest tests/ -q   # 97 tests
```

Covers CV detectors, clustering/risk scoring, forecasting, auth/rate-limit/audit,
network graph, batch + EXIF + GeoJSON + search endpoints, citizen channels, media
forensics, report generation, the WebSocket alert push, the digital-arrest
classifier (stages, spoofing, script families), Fraud Shield triage + languages,
campaign clustering, and evidence-package integrity hashing.

## Design decisions

- **SQLite locally, PostGIS-capable Postgres in Docker.** Locations are lat/lon
  columns with haversine math in the service layer; swapping to `GEOGRAPHY` columns
  touches only `geospatial_service.py`.
- **Celery + Redis in compose, asyncio locally** — same service code behind both.
- **No YOLOv8/PaddleOCR** — pre-trained COCO weights don't know currency features;
  pure-OpenCV heuristics implement all six checks behind the `CounterfeitDetector`
  interface where trained models could later slot in.
- **Media forensics instead of MediaPipe deepfakes** — still-image ELA/noise
  screening is real and testable; video/voice analysis would plug in behind
  `POST /api/v1/citizen/media/verify`.
- **Demo user store** (two role-scoped accounts, PBKDF2, env-overridable) — a users
  table + registration flow is the production swap.

## Accuracy: conformal reference calibration

Scanner verdicts are anchored to a population of genuine reference captures
(split-conformal calibration, `backend/app/data/reference_stats.json`):

- Every scan yields a physical measurement vector (detail energy, thread,
  hologram saturation/hue, intaglio ridges, serial glyphs, texture, tone,
  FFT print-raster periodicity), scored as a robust directional deviation
  from the genuine population and converted to a conformal p-value.
- **LIKELY_COUNTERFEIT requires every capture perturbation to sit beyond the
  99th percentile of genuine references** — so a genuine capture from the
  reference distribution is falsely accused with probability ≤ 1%
  (split-conformal validity guarantee).
- Captures outside the verification envelope (lighting/resolution) are never
  judged — they route to review with a retake instruction.

Measured on the 24-case battery (`scripts/verify_capture_study.py` — unseen
genuine captures + photocopies + inkjet reprints + screen re-displays):
**12/12 fakes flagged, 0/12 genuine accused**; photocopies and screen
re-displays condemned outright at the 99.3rd percentile; raster-free reprints
route to manual review (statistically inseparable from degraded-but-genuine
captures — the correct screening behavior is "never confidently wrong").

**Production calibration:** photograph 100+ real genuine notes under field
conditions and run
`python scripts/calibrate_reference.py --images-dir path/to/photos`
— every verdict re-anchors to real currency. The live audit
(`scripts/verify_realworld.py`, 26 independent checks) covers everything else.

## Disclaimer

Screening aid built entirely on synthetic data; detector thresholds are tuned on
synthetic imagery and would need calibration against reference notes under controlled
capture conditions before any operational use. Not a certification of authenticity.
