from datetime import datetime, timedelta, timezone

import numpy as np
import piexif

from app.models.orm import ScanRecord, Seizure
from tests.conftest import encode_png, make_genuine_like_note


def _seed_cluster(db, lat=19.0760, lon=72.8777):
    for i in range(6):
        db.add(Seizure(
            seizure_date=datetime.now(timezone.utc) - timedelta(days=i),
            lat=lat + i * 0.002, lon=lon + i * 0.002,
            denomination="500", quantity=100, counterfeit_confidence=0.9,
        ))
    db.commit()


def test_batch_analyze(client):
    flat = encode_png(np.full((530, 1200, 3), 128, dtype=np.uint8))
    rich = encode_png(make_genuine_like_note())
    r = client.post(
        "/api/v1/scanner/batch-analyze",
        files=[
            ("files", ("a.png", flat, "image/png")),
            ("files", ("b.png", rich, "image/png")),
            ("files", ("c.png", b"garbage", "image/png")),
        ],
        data={"lat": "19.07", "lon": "72.87"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 3
    assert body["flagged"] >= 1
    errors = [x for x in body["results"] if "error" in x]
    assert len(errors) == 1 and errors[0]["filename"] == "c.png"
    ok = [x for x in body["results"] if "scan_id" in x]
    assert all(x["lat"] == 19.07 for x in ok)


def test_batch_analyze_rejects_bad_coordinates(client):
    flat = encode_png(np.full((530, 1200, 3), 128, dtype=np.uint8))
    response = client.post(
        "/api/v1/scanner/batch-analyze",
        files=[("files", ("note.png", flat, "image/png"))],
        data={"lat": "95", "lon": "72.87"},
    )
    assert response.status_code == 422


def test_alert_description_retains_zero_coordinates(client):
    flat = encode_png(np.full((530, 1200, 3), 128, dtype=np.uint8))
    response = client.post(
        "/api/v1/scanner/analyze",
        files={"file": ("note.png", flat, "image/png")},
        data={"lat": "0", "lon": "0"},
    )
    assert response.status_code == 200
    alerts = client.get("/api/v1/alerts/recent").json()
    assert "at (0.0000, 0.0000)" in alerts[0]["description"]


def test_exif_gps_extraction(client, db):
    """JPEG with GPS EXIF and no form coords -> scan stored with EXIF location."""
    img = np.full((530, 1200, 3), 128, dtype=np.uint8)
    import cv2

    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    gps_ifd = {
        piexif.GPSIFD.GPSLatitudeRef: b"N",
        piexif.GPSIFD.GPSLatitude: ((19, 1), (4, 1), (3360, 100)),   # 19°4'33.6" ≈ 19.076
        piexif.GPSIFD.GPSLongitudeRef: b"E",
        piexif.GPSIFD.GPSLongitude: ((72, 1), (52, 1), (3960, 100)),  # 72°52'39.6" ≈ 72.877
    }
    exif_bytes = piexif.dump({"GPS": gps_ifd})
    import io

    output = io.BytesIO()
    piexif.insert(exif_bytes, buf.tobytes(), output)
    jpeg_with_gps = output.getvalue()

    r = client.post(
        "/api/v1/scanner/analyze",
        files={"file": ("note.jpg", jpeg_with_gps, "image/jpeg")},
    )
    assert r.status_code == 200
    record = db.query(ScanRecord).order_by(ScanRecord.created_at.desc()).first()
    assert record is not None
    assert abs(record.lat - 19.076) < 0.01
    assert abs(record.lon - 72.877) < 0.01


def test_clusters_geojson_format(client, db, auth_headers):
    _seed_cluster(db)
    client.post("/api/v1/clusters/refresh", headers=auth_headers)
    geo = client.get("/api/v1/clusters/active?format=geojson").json()
    assert geo["type"] == "FeatureCollection"
    assert len(geo["features"]) == 1
    feature = geo["features"][0]
    assert feature["geometry"]["type"] == "Point"
    lon, lat = feature["geometry"]["coordinates"]
    assert 72 < lon < 74 and 18 < lat < 20
    assert feature["properties"]["risk_level"] in ("LOW", "MEDIUM", "HIGH", "CRITICAL")


def test_clusters_search(client, db, auth_headers):
    _seed_cluster(db)                       # Mumbai
    _seed_cluster(db, lat=28.70, lon=77.10)  # Delhi
    client.post("/api/v1/clusters/refresh", headers=auth_headers)

    near_mumbai = client.post("/api/v1/clusters/search", json={
        "lat": 19.07, "lon": 72.87, "radius_km": 100,
    }).json()
    assert len(near_mumbai) == 1

    all_clusters = client.post("/api/v1/clusters/search", json={}).json()
    assert len(all_clusters) == 2

    none = client.post("/api/v1/clusters/search", json={
        "risk_levels": ["CRITICAL"], "min_risk_score": 0.99,
    }).json()
    assert none == []
