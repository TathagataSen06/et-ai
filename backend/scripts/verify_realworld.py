"""Real-world accuracy audit against the LIVE Netra system.

Recomputes analytical outputs independently and cross-checks API responses
against the raw database, real geography, and format specifications.
"""
import math
from pathlib import Path
import re
import sqlite3
from datetime import datetime, timedelta, timezone

import httpx

API = "http://127.0.0.1:8000"
DB = str(Path(__file__).resolve().parents[1] / "netra.db")

CITIES = {
    "Mumbai": (19.0760, 72.8777), "Delhi": (28.7041, 77.1025),
    "Bangalore": (12.9716, 77.5946), "Hyderabad": (17.3850, 78.4867),
    "Chennai": (13.0827, 80.2707), "Pune": (18.5204, 73.8567),
    "Kolkata": (22.5726, 88.3639), "Jaipur": (26.9124, 75.7873),
    "Lucknow": (26.8467, 80.9462), "Ahmedabad": (23.0225, 72.5714),
    "Surat": (21.1458, 72.8336), "Chandigarh": (30.7333, 76.7794),
}

results = []


def check(name: str, ok: bool, detail: str = ""):
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))


def haversine(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(a))


client = httpx.Client(timeout=90)
db = sqlite3.connect(DB)

# ---- auth ----
r = client.post(f"{API}/api/v1/auth/login",
                data={"username": "commander", "password": "netra-demo"})
token = r.json()["access_token"]
auth = {"Authorization": f"Bearer {token}"}
check("auth: login issues JWT", r.status_code == 200 and len(token.split(".")) == 3)

# ---- known geography sanity (haversine vs published city distances) ----
known = [
    ("Mumbai-Delhi", 19.0760, 72.8777, 28.7041, 77.1025, 1150, 100),
    ("Mumbai-Pune", 19.0760, 72.8777, 18.5204, 73.8567, 120, 30),
    ("Delhi-Chandigarh", 28.7041, 77.1025, 30.7333, 76.7794, 230, 40),
]
for name, a, b, c, d, expected, tol in known:
    dist = haversine(a, b, c, d)
    check(f"geo: {name} distance â‰ˆ {expected} km", abs(dist - expected) < tol,
          f"computed {dist:.0f} km")

# ---- clusters: cross-check vs DB + real cities + risk formula ----
clusters = client.get(f"{API}/api/v1/clusters/active").json()
cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
db_seizures = db.execute("SELECT COUNT(*) FROM seizures").fetchone()[0]
clustered = sum(c["seizure_count"] for c in clusters)
check("clusters: clustered seizures <= total seizures",
      0 < clustered <= db_seizures, f"{clustered}/{db_seizures} in {len(clusters)} clusters")

bad_city, worst = 0, 0.0
for c in clusters:
    nearest = min(haversine(c["center_lat"], c["center_lon"], la, lo)
                  for la, lo in CITIES.values())
    worst = max(worst, nearest)
    if nearest > 25:
        bad_city += 1
check("clusters: every center within 25 km of a real Indian city",
      bad_city == 0, f"worst offset {worst:.1f} km")

risk_mismatch = []
for c in clusters:
    freq = min(c["seizure_count"] / 20, 1.0)
    vol = min(c["total_notes"] / 5000, 1.0)
    dens = max(1 - c["radius_km"] / 10, 0.0)
    days = (datetime.now(timezone.utc)
            - datetime.fromisoformat(c["last_seizure_date"]).replace(tzinfo=timezone.utc)).days
    rec = 1.0 if days < 7 else 0.7 if days < 14 else 0.4 if days < 30 else 0.2
    expected = 0.3 * freq + 0.3 * vol + 0.2 * dens + 0.2 * rec
    if abs(expected - c["risk_score"]) > 0.02:
        risk_mismatch.append((c["id"][:4], expected, c["risk_score"]))
check("clusters: risk scores match spec formula (independent recompute)",
      not risk_mismatch, f"{len(clusters)} clusters checked; mismatches: {risk_mismatch}")

check("clusters: stability values in [0,1]",
      all(0 <= c["stability"] <= 1 for c in clusters),
      f"range {min(c['stability'] for c in clusters)}-{max(c['stability'] for c in clusters)}")

levels = {c["risk_level"] for c in clusters}
check("clusters: risk levels use the defined vocabulary",
      levels <= {"LOW", "MEDIUM", "HIGH", "CRITICAL"}, str(sorted(levels)))

# ---- GeoJSON spec compliance ----
geo = client.get(f"{API}/api/v1/clusters/active?format=geojson").json()
coords_ok = all(
    68 < f["geometry"]["coordinates"][0] < 98 and 6 < f["geometry"]["coordinates"][1] < 37
    for f in geo["features"]
)
check("geojson: FeatureCollection with [lon, lat] order (RFC 7946)",
      geo["type"] == "FeatureCollection" and len(geo["features"]) == len(clusters) and coords_ok)

# ---- heatmap vs raw seizures (rolling 90-day window) ----
heat = client.get(f"{API}/api/v1/heatmap/data").json()
window_cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%d %H:%M:%S")
db_in_window = db.execute(
    "SELECT COUNT(*) FROM seizures WHERE seizure_date > ?", (window_cutoff,)
).fetchone()[0]
check("heatmap: one weighted point per in-window seizure, weights in (0,1]",
      len(heat) == db_in_window and all(0 < p["weight"] <= 1 for p in heat),
      f"{len(heat)} points / {db_in_window} in window / {db_seizures} total")

# ---- scanner statistics vs raw DB ----
stats = client.get(f"{API}/api/v1/scanner/statistics").json()
cutoff30 = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
db_scans = db.execute(
    "SELECT COUNT(*) FROM scan_records WHERE created_at > ?", (cutoff30,)
).fetchone()[0]
sum_reco = sum(stats["by_recommendation"].values())
check("statistics: totals equal raw scan_records count (30d)",
      stats["total_scans"] == db_scans == sum_reco,
      f"api {stats['total_scans']} / db {db_scans} / by_reco {sum_reco}")

# ---- forecasts: plausible, future-dated, tied to real clusters ----
preds = client.get(f"{API}/api/v1/patrols/predictions", headers=auth).json()
cluster_ids = {c["id"] for c in clusters}
today = datetime.now(timezone.utc).date()
max_daily = db.execute(
    "SELECT MAX(n) FROM (SELECT COUNT(*) n FROM seizures GROUP BY DATE(seizure_date))"
).fetchone()[0]
pred_ok = all(
    p["cluster_id"] in cluster_ids
    and datetime.fromisoformat(p["date"]).date() > today
    and 0 <= p["predicted_seizures"] <= max_daily * 3
    and 0 < p["confidence"] <= 1
    for p in preds
)
check("forecast: future-dated, bounded by history, tied to live clusters",
      pred_ok, f"{len(preds)} predictions, max daily ever {max_daily}")

# ---- network graph vs DB + heuristic audit ----
graph = client.get(f"{API}/api/v1/network/graph", headers=auth).json()
db_counts = {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
             for t in ("distributors", "dealers", "bank_accounts")}
check("network: node counts equal raw tables",
      graph["stats"]["distributors"] == db_counts["distributors"]
      and graph["stats"]["dealers"] == db_counts["dealers"]
      and graph["stats"]["accounts"] == db_counts["bank_accounts"],
      str(db_counts))

node_ids = {n["id"] for n in graph["nodes"]}
check("network: every edge references existing nodes",
      all(e["source"] in node_ids and e["target"] in node_ids for e in graph["edges"]),
      f"{len(graph['edges'])} edges")

flagged = client.get(f"{API}/api/v1/network/suspicious-accounts", headers=auth).json()
rule_ok = all(
    a["velocity_per_day"] > 5 or (a["inflow_inr"] > 2_000_000 and not a["is_verified"])
    for a in flagged
)
db_should_flag = db.execute(
    "SELECT COUNT(*) FROM bank_accounts WHERE velocity_per_day > 5 "
    "OR (total_inflow_inr > 2000000 AND is_verified = 0)"
).fetchone()[0]
check("network: suspicious flags exactly match the stated heuristics",
      rule_ok and len(flagged) == db_should_flag,
      f"{len(flagged)} flagged, db says {db_should_flag}")

# ---- LLM report grounding (no hallucinated key numbers) ----
top = clusters[0]
report = client.post(f"{API}/api/v1/reports/generate/{top['id']}", headers=auth).json()
md = report["markdown"]
grounded = str(top["seizure_count"]) in md
notes_variants = [str(top["total_notes"]), f"{top['total_notes']:,}"]
grounded_notes = any(v in md for v in notes_variants)
fabricated_names = bool(re.search(r"(Officer|Inspector|Insp\.)\s+[A-Z][a-z]+", md))
check(f"report ({report['generator']}): seizure count grounded in facts", grounded)
check(f"report ({report['generator']}): note volume grounded in facts", grounded_notes)
check(f"report ({report['generator']}): no fabricated officer names", not fabricated_names)
check("report: synthetic-data disclosure present", "synthetic" in md.lower())

# ---- citizen channels: realistic messages ----
r = client.post(f"{API}/api/v1/citizen/webhooks/whatsapp", data={
    "From": "whatsapp:+919812345678",
    "Body": "19.0821,72.8416 Shopkeeper near Bandra station gave me two fake 500 notes",
})
reports_list = client.get(f"{API}/api/v1/citizen/reports", headers=auth).json()
newest = reports_list[0]
check("whatsapp: coords parsed and Bandra report stored",
      r.status_code == 200 and abs(newest["lat"] - 19.0821) < 1e-4
      and "Bandra" in newest["description"] and newest["channel"] == "WHATSAPP")
near_bandra = haversine(newest["lat"], newest["lon"], 19.0596, 72.8295) < 5
check("whatsapp: reported location is geographically real (near Bandra)", near_bandra)

# ---- audit trail ----
audit = db.execute(
    "SELECT username, method, path FROM audit_logs ORDER BY created_at DESC LIMIT 5"
).fetchall()
check("audit: mutations recorded with acting user",
      any(row[0] == "commander" for row in audit) and len(audit) > 0,
      f"latest: {audit[0] if audit else None}")

# ---- alert timestamps sane ----
alerts = client.get(f"{API}/api/v1/alerts/recent").json()
now = datetime.now(timezone.utc)
ts_ok = all(
    timedelta(0) <= now - datetime.fromisoformat(a["created_at"]).replace(tzinfo=timezone.utc)
    <= timedelta(days=2)
    for a in alerts[:10]
)
check("alerts: timestamps recent and never in the future", ts_ok, f"{len(alerts)} alerts")

# ---- metrics reflect real traffic (direct 200, real samples) ----
mr = client.get(f"{API}/metrics")
m = re.findall(r'netra_http_requests_total\{[^}]*\} ([0-9.]+)', mr.text)
total_counted = sum(float(x) for x in m)
check("metrics: /metrics answers 200 directly with live request counters",
      mr.status_code == 200 and total_counted > 10,
      f"{total_counted:.0f} requests counted across {len(m)} series")

# ---- rate limiting fires under burst (last: locks auth for 60s) ----
statuses = [
    client.post(f"{API}/api/v1/auth/login",
                data={"username": "x", "password": "y"}).status_code
    for _ in range(12)
]
check("rate limit: burst of 12 logins hits 429 (limit 10/min)",
      429 in statuses, f"statuses {sorted(set(statuses))}")

print("\n" + "=" * 60)
passed = sum(1 for _, ok, _ in results if ok)
print(f"SCORECARD: {passed}/{len(results)} checks passed")
for name, ok, detail in results:
    if not ok:
        print(f"  FAILED: {name} {detail}")
