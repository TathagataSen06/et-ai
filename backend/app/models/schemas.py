"""Pydantic API schemas."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class FeatureResult(BaseModel):
    confidence: float = Field(ge=0.0, le=1.0)
    detail: dict = Field(default_factory=dict)
    status: str


class ScanResult(BaseModel):
    scan_id: str
    counterfeit_score: float
    recommendation: str  # LIKELY_GENUINE | SUSPICIOUS | LIKELY_COUNTERFEIT
    alert_level: str  # LOW | MEDIUM | HIGH
    denomination: str
    detailed_breakdown: dict[str, FeatureResult]
    next_steps: list[str]
    created_at: datetime
    # Inference-time confidence internals (perturbation-consensus ensemble)
    uncertainty: float = 0.0
    analysis_mode: str = "fast"  # fast (single pass) | consensus (ensemble)
    verdict_reason: str = ""
    effective_thresholds: dict[str, float] = Field(default_factory=dict)
    # Conformal calibration against the genuine reference population
    calibrated: bool = False
    genuine_percentile: float | None = None


class ScanStatistics(BaseModel):
    total_scans: int
    avg_counterfeit_score: float
    by_recommendation: dict[str, int]
    by_denomination: dict[str, int]
    daily_counts: list[dict]  # [{date, count, avg_score}]


class ClusterOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    center_lat: float
    center_lon: float
    radius_km: float
    seizure_count: int
    total_notes: int
    avg_confidence: float
    risk_score: float
    stability: float = 1.0
    risk_level: str
    patrol_priority: int
    last_seizure_date: datetime | None
    updated_at: datetime


class SeizureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    seizure_date: datetime
    lat: float
    lon: float
    denomination: str
    quantity: int
    location_type: str | None
    counterfeit_confidence: float
    seized_by_agency: str | None


class ClusterDetail(ClusterOut):
    seizures: list[SeizureOut]


class HeatmapPoint(BaseModel):
    lat: float
    lon: float
    weight: float


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    event_type: str
    lat: float | None
    lon: float | None
    severity: str
    description: str
    created_at: datetime


class PatrolRecommendation(BaseModel):
    hotspot_id: str
    center_lat: float
    center_lon: float
    risk_level: str
    patrol_priority: int
    seizure_count: int
    predicted_intensity: float
    estimated_coverage_km2: float
    expected_duration_hours: float


class PatrolAssignRequest(BaseModel):
    officer_name: str = Field(min_length=1, max_length=100)
    hotspot_id: str
    notes: str | None = None


class PatrolOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    officer_name: str
    hotspot_id: str | None
    priority: int
    status: str
    date_assigned: datetime
    notes: str | None


class PatrolStatusUpdate(BaseModel):
    status: str = Field(pattern="^(PENDING|ACTIVE|COMPLETED)$")
