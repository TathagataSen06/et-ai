"""Database models. Locations are stored as (lat, lon) WGS84 floats — see database.py."""
import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ScanRecord(Base):
    __tablename__ = "scan_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_type: Mapped[str | None] = mapped_column(String(50))
    lat: Mapped[float | None] = mapped_column(Float, index=True)
    lon: Mapped[float | None] = mapped_column(Float, index=True)
    counterfeit_score: Mapped[float] = mapped_column(Float)
    denomination: Mapped[str] = mapped_column(String(10), default="UNKNOWN")
    recommendation: Mapped[str] = mapped_column(String(50))
    features: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Seizure(Base):
    __tablename__ = "seizures"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    seizure_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    lat: Mapped[float] = mapped_column(Float, index=True)
    lon: Mapped[float] = mapped_column(Float, index=True)
    denomination: Mapped[str] = mapped_column(String(10))
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    location_type: Mapped[str | None] = mapped_column(String(50))  # ATM, Bank, Shop, Street
    linked_dealer_id: Mapped[str | None] = mapped_column(String(36), index=True)
    counterfeit_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    seized_by_agency: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class HotspotCluster(Base):
    __tablename__ = "hotspot_clusters"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    center_lat: Mapped[float] = mapped_column(Float)
    center_lon: Mapped[float] = mapped_column(Float)
    radius_km: Mapped[float] = mapped_column(Float, default=0.0)
    seizure_count: Mapped[int] = mapped_column(Integer, default=0)
    total_notes: Mapped[int] = mapped_column(Integer, default=0)
    avg_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    # Consensus-clustering stability: fraction of eps-perturbed DBSCAN runs in
    # which this cluster's membership persists (1.0 = fully stable hotspot).
    stability: Mapped[float] = mapped_column(Float, default=1.0)
    risk_level: Mapped[str] = mapped_column(String(20), default="LOW")  # LOW/MEDIUM/HIGH/CRITICAL
    patrol_priority: Mapped[int] = mapped_column(Integer, default=4, index=True)  # 1 = highest
    last_seizure_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PatrolRoute(Base):
    __tablename__ = "patrol_routes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    officer_name: Mapped[str] = mapped_column(String(100))
    hotspot_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("hotspot_clusters.id"))
    priority: Mapped[int] = mapped_column(Integer, default=4)
    status: Mapped[str] = mapped_column(String(20), default="PENDING")  # PENDING/ACTIVE/COMPLETED
    date_assigned: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    notes: Mapped[str | None] = mapped_column(Text)


class AnomalyEvent(Base):
    __tablename__ = "anomaly_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    event_type: Mapped[str] = mapped_column(String(50))  # CLUSTER_FORMATION, HIGH_RISK_SCAN, VELOCITY_SPIKE
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    severity: Mapped[str] = mapped_column(String(20), default="LOW")
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Distributor(Base):
    __tablename__ = "distributors"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    phone: Mapped[str | None] = mapped_column(String(50))
    primary_city: Mapped[str | None] = mapped_column(String(50))
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    operation_scale: Mapped[str | None] = mapped_column(String(20))  # LOCAL/REGIONAL/NATIONAL


class Dealer(Base):
    __tablename__ = "dealers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    distributor_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("distributors.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    phone: Mapped[str | None] = mapped_column(String(50))
    city: Mapped[str | None] = mapped_column(String(50))
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    operation_type: Mapped[str | None] = mapped_column(String(50))
    estimated_monthly_volume: Mapped[int] = mapped_column(Integer, default=0)


class BankAccount(Base):
    __tablename__ = "bank_accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    dealer_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("dealers.id"), index=True)
    bank_name: Mapped[str | None] = mapped_column(String(50))
    account_number: Mapped[str | None] = mapped_column(String(20))
    ifsc_code: Mapped[str | None] = mapped_column(String(20))
    total_inflow_inr: Mapped[int] = mapped_column(Integer, default=0)
    velocity_per_day: Mapped[int] = mapped_column(Integer, default=0)
    is_verified: Mapped[bool] = mapped_column(default=True)


class CitizenReport(Base):
    __tablename__ = "citizen_reports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    reporter_name: Mapped[str | None] = mapped_column(String(100))
    contact: Mapped[str | None] = mapped_column(String(100))
    channel: Mapped[str] = mapped_column(String(20), default="WEB")  # WEB / WHATSAPP / MOBILE
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    description: Mapped[str] = mapped_column(Text, default="")
    media_tamper_score: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="NEW")  # NEW / REVIEWED
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class ScamSession(Base):
    """An analyzed call/message session screened for digital-arrest patterns."""

    __tablename__ = "scam_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    caller_number: Mapped[str | None] = mapped_column(String(30), index=True)
    victim_contact: Mapped[str | None] = mapped_column(String(100))
    channel: Mapped[str] = mapped_column(String(20), default="VOICE")  # VOICE/VIDEO/WHATSAPP/SMS
    claimed_agency: Mapped[str | None] = mapped_column(String(50))
    transcript: Mapped[str] = mapped_column(Text, default="")
    duration_minutes: Mapped[float | None] = mapped_column(Float)
    device_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    mule_account_id: Mapped[str | None] = mapped_column(String(36), index=True)
    risk_score: Mapped[float] = mapped_column(Float, default=0.0)
    verdict: Mapped[str] = mapped_column(String(30), default="LOW_RISK")
    script_family: Mapped[str | None] = mapped_column(String(50))
    stages: Mapped[list] = mapped_column(JSON, default=list)
    indicators: Mapped[list] = mapped_column(JSON, default=list)
    spoof_flags: Mapped[list] = mapped_column(JSON, default=list)
    alerted: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class CallRecord(Base):
    """Call/session metadata used for cross-linking numbers into campaigns."""

    __tablename__ = "call_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    session_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("scam_sessions.id"), index=True)
    caller_number: Mapped[str | None] = mapped_column(String(30), index=True)
    victim_contact: Mapped[str | None] = mapped_column(String(100))
    channel: Mapped[str] = mapped_column(String(20), default="VOICE")
    duration_minutes: Mapped[float | None] = mapped_column(Float)
    spoofed: Mapped[bool] = mapped_column(default=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class DeviceFingerprint(Base):
    """A device observed operating one or more caller numbers (mule-ring signal)."""

    __tablename__ = "device_fingerprints"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    device_hash: Mapped[str] = mapped_column(String(64), index=True)
    caller_number: Mapped[str | None] = mapped_column(String(30), index=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    sessions_count: Mapped[int] = mapped_column(Integer, default=1)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    username: Mapped[str | None] = mapped_column(String(100))
    method: Mapped[str] = mapped_column(String(10))
    path: Mapped[str] = mapped_column(String(200))
    status_code: Mapped[int] = mapped_column(Integer)
    client_ip: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
