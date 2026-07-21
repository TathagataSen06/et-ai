from datetime import datetime, timedelta, timezone

import cv2
import numpy as np

from app.models.orm import Seizure
from tests.conftest import encode_png


def test_citizen_web_report_creates_alert(client, db, auth_headers):
    r = client.post("/api/v1/citizen/report", json={
        "description": "Received fake ₹500 notes at the vegetable market",
        "lat": 19.076, "lon": 72.877,
        "reporter_name": "A. Citizen",
    })
    assert r.status_code == 201
    report_id = r.json()["report_id"]

    reports = client.get("/api/v1/citizen/reports", headers=auth_headers).json()
    assert reports[0]["id"] == report_id
    assert reports[0]["channel"] == "WEB"

    alerts = client.get("/api/v1/alerts/recent").json()
    assert any(a["event_type"] == "CITIZEN_REPORT" for a in alerts)


def test_citizen_report_validation(client):
    assert client.post("/api/v1/citizen/report", json={"description": "hi"}).status_code == 422
    assert client.post("/api/v1/citizen/report", json={
        "description": "valid text here", "lat": 95.0,
    }).status_code == 422


def test_whatsapp_webhook_with_coords(client, db, auth_headers):
    r = client.post("/api/v1/citizen/webhooks/whatsapp", data={
        "From": "whatsapp:+919999999999",
        "Body": "19.0760,72.8777 Fake notes being distributed near the station",
    })
    assert r.status_code == 200
    assert r.json()["status"] == "received"

    reports = client.get("/api/v1/citizen/reports", headers=auth_headers).json()
    assert reports[0]["channel"] == "WHATSAPP"
    assert abs(reports[0]["lat"] - 19.076) < 0.001
    assert "station" in reports[0]["description"]


def test_whatsapp_webhook_free_text(client, db, auth_headers):
    r = client.post("/api/v1/citizen/webhooks/whatsapp", data={
        "From": "whatsapp:+918888888888",
        "Body": "someone gave me fake notes",
    })
    assert r.status_code == 200
    reports = client.get("/api/v1/citizen/reports", headers=auth_headers).json()
    assert reports[0]["lat"] is None
    assert reports[0]["description"] == "someone gave me fake notes"


def _noisy_photo(seed=3):
    rng = np.random.default_rng(seed)
    img = rng.normal(120, 18, (600, 800, 3)).clip(0, 255).astype(np.uint8)
    return cv2.GaussianBlur(img, (3, 3), 0)


def test_media_verify_clean_vs_tampered(client):
    clean = _noisy_photo()

    tampered = clean.copy()
    tampered[200:400, 250:550] = 180  # pasted flat region breaks noise uniformity

    r_clean = client.post("/api/v1/citizen/media/verify",
                          files={"file": ("c.png", encode_png(clean), "image/png")})
    r_tampered = client.post("/api/v1/citizen/media/verify",
                             files={"file": ("t.png", encode_png(tampered), "image/png")})
    assert r_clean.status_code == 200 and r_tampered.status_code == 200

    clean_score = r_clean.json()["tamper_score"]
    tampered_score = r_tampered.json()["tamper_score"]
    assert 0.0 <= clean_score <= 1.0
    assert 0.0 <= tampered_score <= 1.0
    assert tampered_score > clean_score

    assert client.post(
        "/api/v1/citizen/media/verify",
        files={"file": ("x.png", b"junk", "image/png")},
    ).status_code == 422


def test_report_generation_template(client, db, auth_headers):
    base = (19.0760, 72.8777)
    for i in range(6):
        db.add(Seizure(
            seizure_date=datetime.now(timezone.utc) - timedelta(days=i),
            lat=base[0] + i * 0.002, lon=base[1] + i * 0.002,
            denomination="500", quantity=100, location_type="ATM",
            seized_by_agency="RBI", counterfeit_confidence=0.9,
        ))
    db.commit()
    clusters = client.post("/api/v1/clusters/refresh", headers=auth_headers).json()
    cluster_id = clusters[0]["id"]

    r = client.post(f"/api/v1/reports/generate/{cluster_id}", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["generator"] == "TEMPLATE"  # no LLM configured in tests
    assert f"Sector {cluster_id[:4].upper()}" in body["markdown"]
    assert "Recommended actions" in body["markdown"]
    assert "ATM" in body["markdown"]

    assert client.post(
        "/api/v1/reports/generate/missing", headers=auth_headers
    ).status_code == 404
