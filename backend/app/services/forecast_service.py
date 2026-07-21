"""Predictive patrol forecasting (spec Feature 4).

Per active hotspot: build a daily seizure-count series, decompose it with
statsmodels (weekly seasonality), and project trend + seasonal component
`days_ahead` forward. IsolationForest flags anomalous daily spikes across
all cluster series.
"""
import logging
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sqlalchemy import select
from sqlalchemy.orm import Session
from statsmodels.tsa.seasonal import seasonal_decompose

from app.models.orm import HotspotCluster, Seizure
from app.services.geospatial_service import _as_utc, haversine_km

logger = logging.getLogger(__name__)

MIN_SERIES_DAYS = 14
PREDICTION_THRESHOLD = 0.5  # predicted seizures/day worth reporting


class ForecastService:
    def __init__(self, db: Session):
        self.db = db

    def _daily_series(self, cluster: HotspotCluster, lookback_days: int = 90) -> pd.Series:
        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        seizures = self.db.scalars(select(Seizure).where(Seizure.seizure_date > cutoff)).all()
        radius = max(cluster.radius_km * 1.5, 3.0)
        dates = [
            _as_utc(s.seizure_date).date()
            for s in seizures
            if haversine_km(cluster.center_lat, cluster.center_lon, s.lat, s.lon) <= radius
        ]
        if not dates:
            return pd.Series(dtype=float)
        index = pd.date_range(min(dates), datetime.now(timezone.utc).date(), freq="D")
        counts = pd.Series(0.0, index=index)
        for d in dates:
            counts[pd.Timestamp(d)] += 1
        return counts

    def predict_hotspots(self, days_ahead: int = 7) -> list[dict]:
        """Forecast expected seizure activity per hotspot for the next N days."""
        clusters = self.db.scalars(select(HotspotCluster)).all()
        predictions: list[dict] = []
        all_series: list[tuple[HotspotCluster, pd.Series]] = []

        for cluster in clusters:
            series = self._daily_series(cluster)
            if len(series) < MIN_SERIES_DAYS:
                continue
            all_series.append((cluster, series))

            try:
                decomposition = seasonal_decompose(
                    series, model="additive", period=7, extrapolate_trend="freq"
                )
            except Exception:
                logger.exception("Decomposition failed for cluster %s", cluster.id)
                continue

            trend = decomposition.trend.dropna()
            seasonal = decomposition.seasonal
            if len(trend) < 8:
                continue
            last_trend = float(trend.iloc[-1])
            trend_slope = float(trend.iloc[-1] - trend.iloc[-8]) / 7.0

            for offset in range(1, days_ahead + 1):
                target_date = datetime.now(timezone.utc).date() + timedelta(days=offset)
                seasonal_component = float(seasonal.iloc[-7 + (offset - 1) % 7])
                predicted = max(0.0, last_trend + trend_slope * offset + seasonal_component)
                if predicted < PREDICTION_THRESHOLD:
                    continue
                predictions.append({
                    "cluster_id": cluster.id,
                    "center_lat": cluster.center_lat,
                    "center_lon": cluster.center_lon,
                    "risk_level": cluster.risk_level,
                    "date": target_date.isoformat(),
                    "predicted_seizures": round(predicted, 2),
                    "confidence": 0.7 if predicted >= 1.5 else 0.5,
                    "source": "SEASONAL_TREND",
                })

        predictions.extend(self._detect_spike_anomalies(all_series))
        predictions.sort(key=lambda p: (p["confidence"], p["predicted_seizures"]), reverse=True)
        return predictions[:30]

    @staticmethod
    def _detect_spike_anomalies(
        all_series: list[tuple[HotspotCluster, pd.Series]],
    ) -> list[dict]:
        """IsolationForest over pooled daily counts; flags recent unusual spikes."""
        rows = []
        for cluster, series in all_series:
            for date, count in series.items():
                rows.append((cluster, date, count))
        if len(rows) <= 10:
            return []

        X = np.array([[count] for _, _, count in rows])
        labels = IsolationForest(contamination=0.15, random_state=42).fit_predict(X)

        recent_cutoff = pd.Timestamp(datetime.now(timezone.utc).date() - timedelta(days=14))
        anomalies = []
        for (cluster, date, count), label in zip(rows, labels):
            if label == -1 and count > 0 and date >= recent_cutoff:
                anomalies.append({
                    "cluster_id": cluster.id,
                    "center_lat": cluster.center_lat,
                    "center_lon": cluster.center_lon,
                    "risk_level": cluster.risk_level,
                    "date": (datetime.now(timezone.utc).date() + timedelta(days=3)).isoformat(),
                    "predicted_seizures": float(count),
                    "confidence": 0.6,
                    "source": "UNUSUAL_SPIKE",
                    "spike_date": date.date().isoformat(),
                })
        # Keep the strongest spike per cluster.
        best: dict[str, dict] = {}
        for a in anomalies:
            if a["cluster_id"] not in best or a["predicted_seizures"] > best[a["cluster_id"]]["predicted_seizures"]:
                best[a["cluster_id"]] = a
        return list(best.values())
