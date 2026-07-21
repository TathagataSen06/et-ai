from datetime import datetime, timedelta, timezone

from app.models.orm import HotspotCluster, Seizure
from app.services.geospatial_service import GeospatialIntelligence, haversine_km


def _seizure(lat, lon, days_ago=1, quantity=100):
    return Seizure(
        seizure_date=datetime.now(timezone.utc) - timedelta(days=days_ago),
        lat=lat,
        lon=lon,
        denomination="500",
        quantity=quantity,
        counterfeit_confidence=0.9,
    )


def _seed_mumbai_cluster(db, n=6):
    """n seizures within ~1 km of central Mumbai + 2 isolated noise points."""
    base = (19.0760, 72.8777)
    for i in range(n):
        db.add(_seizure(base[0] + i * 0.002, base[1] + i * 0.002))
    db.add(_seizure(28.7, 77.1))  # Delhi, isolated
    db.add(_seizure(13.08, 80.27))  # Chennai, isolated
    db.commit()


def test_haversine_known_distance():
    # Mumbai <-> Delhi is ~1150 km
    dist = haversine_km(19.0760, 72.8777, 28.7041, 77.1025)
    assert 1100 < dist < 1200


def test_clustering_finds_dense_cluster_ignores_noise(db):
    _seed_mumbai_cluster(db)
    clusters = GeospatialIntelligence(db).update_hotspots()
    assert len(clusters) == 1
    c = clusters[0]
    assert c.seizure_count == 6
    assert haversine_km(c.center_lat, c.center_lon, 19.0760, 72.8777) < 5
    assert c.risk_level in ("LOW", "MEDIUM", "HIGH", "CRITICAL")
    assert 1 <= c.patrol_priority <= 4


def test_recent_dense_cluster_gets_elevated_priority(db):
    _seed_mumbai_cluster(db, n=12)
    clusters = GeospatialIntelligence(db).update_hotspots()
    c = clusters[0]
    # Recent (< 7 days) boosts priority one level.
    assert c.patrol_priority <= 3
    assert c.risk_score > 0.5


def test_upsert_updates_instead_of_duplicating(db):
    _seed_mumbai_cluster(db)
    geo = GeospatialIntelligence(db)
    geo.update_hotspots()
    geo.update_hotspots()
    assert db.query(HotspotCluster).count() == 1


def test_stale_cluster_removed_when_seizures_age_out(db):
    _seed_mumbai_cluster(db)
    geo = GeospatialIntelligence(db)
    assert len(geo.update_hotspots()) == 1
    # Age all seizures beyond the lookback window.
    for s in db.query(Seizure).all():
        s.seizure_date = datetime.now(timezone.utc) - timedelta(days=400)
    db.commit()
    assert geo.update_hotspots() == []
    assert db.query(HotspotCluster).count() == 0


def test_too_few_seizures_yields_no_clusters(db):
    db.add(_seizure(19.0, 72.8))
    db.commit()
    assert GeospatialIntelligence(db).update_hotspots() == []


def test_heatmap_weights_bounded(db):
    _seed_mumbai_cluster(db)
    points = GeospatialIntelligence(db).heatmap_points()
    assert len(points) == 8
    assert all(0.0 < p["weight"] <= 1.0 for p in points)
