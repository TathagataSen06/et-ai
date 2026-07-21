from datetime import datetime, timedelta, timezone

from app.models.orm import Seizure
from app.services.forecast_service import ForecastService
from app.services.geospatial_service import GeospatialIntelligence


def _seed_weekly_pattern(db, days: int = 42):
    """Cluster near Mumbai with a weekly cadence: busy weekends, quiet weekdays."""
    base = (19.0760, 72.8777)
    now = datetime.now(timezone.utc)
    for day_offset in range(days):
        date = now - timedelta(days=day_offset)
        count = 3 if date.weekday() >= 5 else 1
        for k in range(count):
            db.add(Seizure(
                seizure_date=date,
                lat=base[0] + (k * 0.001),
                lon=base[1] + (k * 0.001),
                denomination="500",
                quantity=100,
                counterfeit_confidence=0.9,
            ))
    db.commit()
    GeospatialIntelligence(db).update_hotspots()


def test_predictions_from_weekly_pattern(db):
    _seed_weekly_pattern(db)
    predictions = ForecastService(db).predict_hotspots(days_ahead=7)
    assert predictions, "expected forecast output for a 6-week series"
    trend = [p for p in predictions if p["source"] == "SEASONAL_TREND"]
    assert trend
    assert all(p["predicted_seizures"] >= 0 for p in predictions)
    assert all(0 < p["confidence"] <= 1 for p in predictions)


def test_spike_anomaly_detected(db):
    _seed_weekly_pattern(db)
    # Inject a large spike three days ago.
    spike_day = datetime.now(timezone.utc) - timedelta(days=3)
    for k in range(15):
        db.add(Seizure(
            seizure_date=spike_day,
            lat=19.076 + k * 0.0005,
            lon=72.877 + k * 0.0005,
            denomination="2000",
            quantity=300,
            counterfeit_confidence=0.95,
        ))
    db.commit()
    GeospatialIntelligence(db).update_hotspots()

    predictions = ForecastService(db).predict_hotspots(days_ahead=7)
    spikes = [p for p in predictions if p["source"] == "UNUSUAL_SPIKE"]
    assert spikes, "expected the injected spike to be flagged"


def test_no_predictions_for_short_series(db):
    base = (19.0760, 72.8777)
    now = datetime.now(timezone.utc)
    for day_offset in range(5):
        db.add(Seizure(
            seizure_date=now - timedelta(days=day_offset),
            lat=base[0], lon=base[1],
            denomination="500", quantity=100, counterfeit_confidence=0.9,
        ))
    db.commit()
    GeospatialIntelligence(db).update_hotspots()
    assert ForecastService(db).predict_hotspots() == []


def test_predictions_endpoint(client, db, auth_headers):
    _seed_weekly_pattern(db)
    r = client.get("/api/v1/patrols/predictions", headers=auth_headers)
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert client.get("/api/v1/patrols/predictions").status_code == 401
