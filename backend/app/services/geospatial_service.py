"""Hotspot detection: DBSCAN clustering of seizures with haversine distance.

Risk score = f(frequency, volume, density, recency) per the Netra spec.
"""
import logging
import math
from datetime import datetime, timedelta, timezone

import numpy as np
from sklearn.cluster import DBSCAN
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.orm import AnomalyEvent, HotspotCluster, Seizure

logger = logging.getLogger(__name__)

EARTH_RADIUS_KM = 6371.0088

RISK_LEVELS = [(0.85, "CRITICAL"), (0.70, "HIGH"), (0.50, "MEDIUM"), (0.0, "LOW")]
PRIORITY_MAP = {"CRITICAL": 1, "HIGH": 2, "MEDIUM": 3, "LOW": 4}


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _as_utc(dt: datetime) -> datetime:
    """SQLite returns naive datetimes; treat them as UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class GeospatialIntelligence:
    def __init__(self, db: Session):
        self.db = db
        self.settings = get_settings()

    def update_hotspots(self, lookback_days: int | None = None) -> list[HotspotCluster]:
        """Re-cluster recent seizures and upsert hotspot rows. Returns active clusters."""
        lookback = lookback_days or self.settings.cluster_lookback_days
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback)
        seizures = list(
            self.db.scalars(select(Seizure).where(Seizure.seizure_date > cutoff)).all()
        )
        if len(seizures) < self.settings.cluster_min_samples:
            logger.info("Not enough seizures (%d) to cluster", len(seizures))
            # A prior run may have materialized clusters that are no longer
            # supported by the current lookback window. Leaving them in place
            # would present stale hotspots as live intelligence.
            for cluster in self.db.scalars(select(HotspotCluster)).all():
                self.db.delete(cluster)
            self.db.commit()
            return []

        coords = np.radians([[s.lat, s.lon] for s in seizures])
        eps = self.settings.cluster_eps_km / EARTH_RADIUS_KM
        labels = DBSCAN(
            eps=eps, min_samples=self.settings.cluster_min_samples, metric="haversine"
        ).fit_predict(coords)

        # Consensus clustering (training-free ensemble disagreement): re-run
        # DBSCAN at perturbed radii; hotspots whose membership survives the
        # perturbations are stable, borderline chains are flagged as fragile.
        alt_labelings = [
            DBSCAN(
                eps=eps * factor,
                min_samples=self.settings.cluster_min_samples,
                metric="haversine",
            ).fit_predict(coords)
            for factor in (0.75, 1.25)
        ]

        clusters: list[dict] = []
        for label in sorted(set(labels)):
            if label == -1:  # noise
                continue
            member_idx = {i for i, lb in enumerate(labels) if lb == label}
            members = [seizures[i] for i in member_idx]
            summary = self._summarize(members)
            summary["stability"] = self._consensus_stability(member_idx, alt_labelings)
            clusters.append(summary)

        new_clusters = self._upsert(clusters)
        self._record_anomalies(new_clusters)
        self.db.commit()
        return self.get_active_clusters()

    def _summarize(self, members: list[Seizure]) -> dict:
        center_lat = float(np.mean([s.lat for s in members]))
        center_lon = float(np.mean([s.lon for s in members]))
        radius_km = max(
            (haversine_km(center_lat, center_lon, s.lat, s.lon) for s in members), default=0.0
        )
        last_seizure = max(_as_utc(s.seizure_date) for s in members)
        seizure_count = len(members)
        total_notes = sum(s.quantity for s in members)
        risk = self._risk_score(seizure_count, total_notes, radius_km, last_seizure)
        risk_level = next(level for threshold, level in RISK_LEVELS if risk >= threshold)
        return {
            "center_lat": center_lat,
            "center_lon": center_lon,
            "radius_km": round(radius_km, 3),
            "seizure_count": seizure_count,
            "total_notes": total_notes,
            "avg_confidence": round(float(np.mean([s.counterfeit_confidence for s in members])), 3),
            "risk_score": round(risk, 3),
            "risk_level": risk_level,
            "patrol_priority": self._patrol_priority(risk_level, last_seizure),
            "last_seizure_date": last_seizure,
        }

    @staticmethod
    def _consensus_stability(member_idx: set[int], alt_labelings: list) -> float:
        """Mean best-Jaccard overlap of this cluster against each perturbed run."""
        overlaps = []
        for alt_labels in alt_labelings:
            alt_clusters: dict[int, set[int]] = {}
            for i, lb in enumerate(alt_labels):
                if lb != -1:
                    alt_clusters.setdefault(lb, set()).add(i)
            best = 0.0
            for alt_members in alt_clusters.values():
                union = len(member_idx | alt_members)
                if union:
                    best = max(best, len(member_idx & alt_members) / union)
            overlaps.append(best)
        return round(float(np.mean(overlaps)), 3) if overlaps else 1.0

    @staticmethod
    def _risk_score(seizure_count: int, total_notes: int, radius_km: float,
                    last_seizure: datetime) -> float:
        freq_score = min(seizure_count / 20.0, 1.0)
        volume_score = min(total_notes / 5000.0, 1.0)
        density_score = max(1.0 - radius_km / 10.0, 0.0)
        days_ago = (datetime.now(timezone.utc) - last_seizure).days
        recency_score = 1.0 if days_ago < 7 else 0.7 if days_ago < 14 else 0.4 if days_ago < 30 else 0.2
        return freq_score * 0.30 + volume_score * 0.30 + density_score * 0.20 + recency_score * 0.20

    @staticmethod
    def _patrol_priority(risk_level: str, last_seizure: datetime) -> int:
        priority = PRIORITY_MAP.get(risk_level, 4)
        if (datetime.now(timezone.utc) - last_seizure).days < 7:
            priority = max(1, priority - 1)
        return priority

    def _upsert(self, clusters: list[dict]) -> list[HotspotCluster]:
        """Match each computed cluster to an existing row within 2 km, else insert.

        Returns the rows that were newly created (used for anomaly alerts).
        """
        existing = list(self.db.scalars(select(HotspotCluster)).all())
        created: list[HotspotCluster] = []
        matched_ids: set[str] = set()

        for cluster in clusters:
            nearest = None
            nearest_dist = 2.0  # km
            for row in existing:
                if row.id in matched_ids:
                    continue
                dist = haversine_km(
                    cluster["center_lat"], cluster["center_lon"], row.center_lat, row.center_lon
                )
                if dist < nearest_dist:
                    nearest, nearest_dist = row, dist
            if nearest is not None:
                for key, value in cluster.items():
                    setattr(nearest, key, value)
                matched_ids.add(nearest.id)
            else:
                row = HotspotCluster(**cluster)
                self.db.add(row)
                created.append(row)

        # Remove stale clusters that no longer have supporting seizures.
        for row in existing:
            if row.id not in matched_ids:
                self.db.delete(row)
        return created

    def _record_anomalies(self, new_clusters: list[HotspotCluster]) -> None:
        for cluster in new_clusters:
            severity = "HIGH" if cluster.risk_level in ("HIGH", "CRITICAL") else "MEDIUM"
            self.db.add(AnomalyEvent(
                event_type="CLUSTER_FORMATION",
                lat=cluster.center_lat,
                lon=cluster.center_lon,
                severity=severity,
                description=(
                    f"New {cluster.risk_level} counterfeit cluster: "
                    f"{cluster.seizure_count} seizures, {cluster.total_notes} notes"
                ),
            ))

    def get_active_clusters(self) -> list[HotspotCluster]:
        return list(
            self.db.scalars(
                select(HotspotCluster).order_by(HotspotCluster.patrol_priority,
                                                HotspotCluster.risk_score.desc())
            ).all()
        )

    def heatmap_points(self, lookback_days: int = 90) -> list[dict]:
        """Seizure locations weighted by note volume, for the dashboard heat layer."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        seizures = self.db.scalars(select(Seizure).where(Seizure.seizure_date > cutoff)).all()
        max_qty = max((s.quantity for s in seizures), default=1) or 1
        return [
            {"lat": s.lat, "lon": s.lon, "weight": round(0.3 + 0.7 * s.quantity / max_qty, 3)}
            for s in seizures
        ]
