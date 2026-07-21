"""Hotspot cluster and heatmap endpoints."""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.orm import HotspotCluster, Seizure
from app.models.schemas import ClusterDetail, ClusterOut, HeatmapPoint
from app.services.alert_service import manager
from app.services.auth_service import get_current_user
from app.services.geospatial_service import GeospatialIntelligence, haversine_km

router = APIRouter(prefix="/api/v1", tags=["geospatial"])


def _to_geojson(clusters: list[HotspotCluster]) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    "coordinates": [c.center_lon, c.center_lat],
                },
                "properties": {
                    "id": c.id,
                    "radius_km": c.radius_km,
                    "seizure_count": c.seizure_count,
                    "total_notes": c.total_notes,
                    "avg_confidence": c.avg_confidence,
                    "risk_score": c.risk_score,
                    "risk_level": c.risk_level,
                    "patrol_priority": c.patrol_priority,
                    "last_seizure_date": c.last_seizure_date.isoformat()
                    if c.last_seizure_date else None,
                },
            }
            for c in clusters
        ],
    }


@router.get("/clusters/active")
def active_clusters(
    format: str = Query(default="json", pattern="^(json|geojson)$"),
    db: Session = Depends(get_db),
):
    clusters = GeospatialIntelligence(db).get_active_clusters()
    if format == "geojson":
        return _to_geojson(clusters)
    return [ClusterOut.model_validate(c) for c in clusters]


class ClusterSearchRequest(BaseModel):
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    radius_km: float = Field(default=50.0, gt=0, le=2000)
    risk_levels: list[str] | None = None
    min_risk_score: float | None = Field(default=None, ge=0, le=1)
    date_from: datetime | None = None
    date_to: datetime | None = None


@router.post("/clusters/search", response_model=list[ClusterOut])
def search_clusters(body: ClusterSearchRequest, db: Session = Depends(get_db)):
    """Filter hotspots by location radius, risk level, score, and activity window."""
    clusters = GeospatialIntelligence(db).get_active_clusters()
    results = []
    for c in clusters:
        if body.risk_levels and c.risk_level not in body.risk_levels:
            continue
        if body.min_risk_score is not None and c.risk_score < body.min_risk_score:
            continue
        if body.lat is not None and body.lon is not None:
            if haversine_km(body.lat, body.lon, c.center_lat, c.center_lon) > body.radius_km:
                continue
        if body.date_from and (c.last_seizure_date is None or c.last_seizure_date < body.date_from):
            continue
        if body.date_to and (c.last_seizure_date is None or c.last_seizure_date > body.date_to):
            continue
        results.append(c)
    return results


@router.get("/clusters/{cluster_id}/details", response_model=ClusterDetail)
def cluster_details(cluster_id: str, db: Session = Depends(get_db)):
    cluster = db.get(HotspotCluster, cluster_id)
    if cluster is None:
        raise HTTPException(status_code=404, detail="Cluster not found")
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    nearby = [
        s for s in db.scalars(select(Seizure).where(Seizure.seizure_date > cutoff)).all()
        if haversine_km(cluster.center_lat, cluster.center_lon, s.lat, s.lon)
        <= max(cluster.radius_km, 2.0)
    ]
    nearby.sort(key=lambda s: s.seizure_date, reverse=True)
    return ClusterDetail(
        **ClusterOut.model_validate(cluster).model_dump(),
        seizures=nearby[:50],
    )


@router.post(
    "/clusters/refresh",
    response_model=list[ClusterOut],
    dependencies=[Depends(get_current_user)],
)
async def refresh_clusters(db: Session = Depends(get_db)):
    """Re-run DBSCAN over recent seizures (normally done by the background job)."""
    clusters = GeospatialIntelligence(db).update_hotspots()
    await manager.broadcast({"type": "CLUSTER_UPDATE", "count": len(clusters)})
    return clusters


@router.get("/heatmap/data", response_model=list[HeatmapPoint])
def heatmap_data(days: int = 90, db: Session = Depends(get_db)):
    return GeospatialIntelligence(db).heatmap_points(lookback_days=days)
