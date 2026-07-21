"""Patrol intelligence: predicted hotspot intensity and route recommendations."""
import math
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.orm import HotspotCluster, PatrolRoute, Seizure
from app.services.geospatial_service import _as_utc, haversine_km


class PatrolIntelligence:
    def __init__(self, db: Session):
        self.db = db

    def predicted_intensity(self, cluster: HotspotCluster, days_ahead: int = 7) -> float:
        """Recency-weighted seizure intensity near the cluster center.

        Exponential decay (half-life 14 days) over the last 60 days of seizures
        within 1.5x the cluster radius. Serves as a lightweight stand-in for the
        seasonal-decomposition forecast in the full spec.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=60)
        seizures = self.db.scalars(select(Seizure).where(Seizure.seizure_date > cutoff)).all()
        radius = max(cluster.radius_km * 1.5, 3.0)
        now = datetime.now(timezone.utc)
        intensity = 0.0
        for s in seizures:
            if haversine_km(cluster.center_lat, cluster.center_lon, s.lat, s.lon) <= radius:
                age_days = max((now - _as_utc(s.seizure_date)).days, 0)
                intensity += math.exp(-age_days * math.log(2) / 14.0)
        return round(intensity, 3)

    def recommendations(self, officers_available: int = 5) -> list[dict]:
        """Top-priority hotspots without an active patrol, ranked for assignment."""
        assigned_ids = {
            r.hotspot_id
            for r in self.db.scalars(
                select(PatrolRoute).where(PatrolRoute.status.in_(["PENDING", "ACTIVE"]))
            ).all()
            if r.hotspot_id
        }
        hotspots = self.db.scalars(
            select(HotspotCluster).order_by(
                HotspotCluster.patrol_priority, HotspotCluster.risk_score.desc()
            )
        ).all()

        recs = []
        for hotspot in hotspots:
            if hotspot.id in assigned_ids:
                continue
            coverage_km2 = math.pi * (max(hotspot.radius_km, 1.0) ** 2) * 1.5
            recs.append({
                "hotspot_id": hotspot.id,
                "center_lat": hotspot.center_lat,
                "center_lon": hotspot.center_lon,
                "risk_level": hotspot.risk_level,
                "patrol_priority": hotspot.patrol_priority,
                "seizure_count": hotspot.seizure_count,
                "predicted_intensity": self.predicted_intensity(hotspot),
                "estimated_coverage_km2": round(coverage_km2, 1),
                "expected_duration_hours": 4.0,
            })
            if len(recs) >= officers_available:
                break
        return recs

    def assign(self, officer_name: str, hotspot_id: str, notes: str | None = None) -> PatrolRoute:
        hotspot = self.db.get(HotspotCluster, hotspot_id)
        if hotspot is None:
            raise ValueError(f"Hotspot {hotspot_id} not found")
        route = PatrolRoute(
            officer_name=officer_name,
            hotspot_id=hotspot_id,
            priority=hotspot.patrol_priority,
            status="PENDING",
            notes=notes,
        )
        self.db.add(route)
        self.db.commit()
        return route
