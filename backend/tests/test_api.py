from datetime import datetime, timedelta, timezone

import numpy as np

from app.models.orm import Seizure
from tests.conftest import encode_png, make_genuine_like_note


def _seed_cluster(db):
    base = (19.0760, 72.8777)
    for i in range(6):
        db.add(Seizure(
            seizure_date=datetime.now(timezone.utc) - timedelta(days=i),
            lat=base[0] + i * 0.002,
            lon=base[1] + i * 0.002,
            denomination="500",
            quantity=100,
            counterfeit_confidence=0.9,
        ))
    db.commit()


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_analyze_flat_image_flags_counterfeit(client):
    flat = np.full((530, 1200, 3), 128, dtype=np.uint8)
    r = client.post(
        "/api/v1/scanner/analyze",
        files={"file": ("note.png", encode_png(flat), "image/png")},
        data={"lat": "19.07", "lon": "72.87", "user_type": "Merchant"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["recommendation"] == "LIKELY_COUNTERFEIT"
    assert body["alert_level"] == "HIGH"
    assert set(body["detailed_breakdown"]) == {
        "microprint", "security_thread", "hologram", "intaglio", "serial_number", "paper_texture"
    }
    # High-risk scan must generate an alert.
    alerts = client.get("/api/v1/alerts/recent").json()
    assert any(a["event_type"] == "HIGH_RISK_SCAN" for a in alerts)


def test_analyze_rich_image_scores_lower(client):
    rich = client.post(
        "/api/v1/scanner/analyze",
        files={"file": ("note.png", encode_png(make_genuine_like_note()), "image/png")},
    ).json()
    assert rich["counterfeit_score"] < 0.75


def test_analyze_rejects_garbage(client):
    r = client.post(
        "/api/v1/scanner/analyze",
        files={"file": ("x.png", b"not an image", "image/png")},
    )
    assert r.status_code == 422


def test_analyze_rejects_bad_coordinates(client):
    flat = np.full((100, 100, 3), 128, dtype=np.uint8)
    r = client.post(
        "/api/v1/scanner/analyze",
        files={"file": ("note.png", encode_png(flat), "image/png")},
        data={"lat": "95.0", "lon": "72.87"},
    )
    assert r.status_code == 422


def test_cluster_endpoints(client, db, auth_headers):
    _seed_cluster(db)
    refreshed = client.post("/api/v1/clusters/refresh", headers=auth_headers)
    assert refreshed.status_code == 200
    clusters = client.get("/api/v1/clusters/active").json()
    assert len(clusters) == 1

    detail = client.get(f"/api/v1/clusters/{clusters[0]['id']}/details")
    assert detail.status_code == 200
    assert len(detail.json()["seizures"]) == 6

    assert client.get("/api/v1/clusters/nope/details").status_code == 404


def test_heatmap(client, db):
    _seed_cluster(db)
    points = client.get("/api/v1/heatmap/data").json()
    assert len(points) == 6
    assert {"lat", "lon", "weight"} <= set(points[0])


def test_patrol_flow(client, db, auth_headers):
    _seed_cluster(db)
    client.post("/api/v1/clusters/refresh", headers=auth_headers)

    recs = client.get("/api/v1/patrols/recommendations", headers=auth_headers).json()
    assert len(recs) == 1
    hotspot_id = recs[0]["hotspot_id"]

    assigned = client.post("/api/v1/patrols/assign", json={
        "officer_name": "Insp. Rao", "hotspot_id": hotspot_id, "notes": "Evening sweep",
    }, headers=auth_headers)
    assert assigned.status_code == 201
    route_id = assigned.json()["id"]

    # Assigned hotspot no longer recommended.
    assert client.get("/api/v1/patrols/recommendations", headers=auth_headers).json() == []

    updated = client.put(
        f"/api/v1/patrols/{route_id}/status", json={"status": "ACTIVE"}, headers=auth_headers
    )
    assert updated.json()["status"] == "ACTIVE"

    status = client.get("/api/v1/patrols/status", headers=auth_headers).json()
    assert status[0]["officer_name"] == "Insp. Rao"

    # Spec endpoint: GET /patrols/{officer_id}/route
    route = client.get("/api/v1/patrols/Insp. Rao/route", headers=auth_headers)
    assert route.status_code == 200
    assert route.json()[0]["id"] == route_id
    assert client.get(
        "/api/v1/patrols/Nobody/route", headers=auth_headers
    ).status_code == 404

    assert client.post("/api/v1/patrols/assign", json={
        "officer_name": "X", "hotspot_id": "missing",
    }, headers=auth_headers).status_code == 404


def test_scanner_statistics(client):
    flat = np.full((530, 1200, 3), 128, dtype=np.uint8)
    client.post("/api/v1/scanner/analyze",
                files={"file": ("note.png", encode_png(flat), "image/png")})
    stats = client.get("/api/v1/scanner/statistics").json()
    assert stats["total_scans"] == 1
    assert "LIKELY_COUNTERFEIT" in stats["by_recommendation"]


def test_websocket_receives_alert_on_high_risk_scan(client):
    flat = np.full((530, 1200, 3), 128, dtype=np.uint8)
    with client.websocket_connect("/ws/dashboard") as ws:
        client.post("/api/v1/scanner/analyze",
                    files={"file": ("note.png", encode_png(flat), "image/png")})
        message = ws.receive_json()
        assert message["type"] == "ALERT"
        assert message["severity"] == "HIGH"
