"""Citizen reporting: web form, WhatsApp webhook adapter, media verification.

Submission endpoints are public (citizens); the report list is law-enforcement.
The WhatsApp endpoint accepts the Twilio webhook form shape (`From`, `Body`) so a
Twilio number can be pointed at it without an adapter service.
"""
import logging
import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models.orm import AnomalyEvent, CitizenReport
from app.services.alert_service import manager
from app.services.auth_service import get_current_user
from app.services.cv_service import ImageDecodeError
from app.services.fraud_shield_service import HELPLINE, FraudShield
from app.services.media_service import MediaForensics

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/citizen", tags=["citizen"])
forensics = MediaForensics()
shield = FraudShield()

# "CHECK <suspicious message>" (any case) routes to Fraud Shield triage
# instead of report intake, so one WhatsApp number serves both channels.
_CHECK_RE = re.compile(r"^\s*check\s*[:\-]?\s*(.+)$", re.IGNORECASE | re.DOTALL)

# "19.0760,72.8777 suspicious notes at the market" -> coords + text
_COORDS_RE = re.compile(r"^\s*(-?\d{1,2}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)\s*(.*)$", re.DOTALL)


class CitizenReportIn(BaseModel):
    description: str = Field(min_length=5, max_length=2000)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    reporter_name: str | None = Field(default=None, max_length=100)
    contact: str | None = Field(default=None, max_length=100)
    media_tamper_score: float | None = Field(default=None, ge=0, le=1)


async def _store_report(db: Session, *, channel: str, description: str,
                        lat: float | None, lon: float | None,
                        reporter_name: str | None = None, contact: str | None = None,
                        media_tamper_score: float | None = None) -> CitizenReport:
    report = CitizenReport(
        channel=channel,
        description=description,
        lat=lat,
        lon=lon,
        reporter_name=reporter_name,
        contact=contact,
        media_tamper_score=media_tamper_score,
    )
    db.add(report)
    db.add(AnomalyEvent(
        event_type="CITIZEN_REPORT",
        lat=lat,
        lon=lon,
        severity="MEDIUM",
        description=f"Citizen report ({channel}): {description[:140]}",
    ))
    db.commit()
    await manager.broadcast({
        "type": "ALERT",
        "severity": "MEDIUM",
        "message": f"Citizen report via {channel}: {description[:100]}",
        "lat": lat,
        "lon": lon,
    })
    return report


@router.post("/report", status_code=201)
async def submit_report(body: CitizenReportIn, db: Session = Depends(get_db)):
    report = await _store_report(
        db,
        channel="WEB",
        description=body.description,
        lat=body.lat,
        lon=body.lon,
        reporter_name=body.reporter_name,
        contact=body.contact,
        media_tamper_score=body.media_tamper_score,
    )
    return {"report_id": report.id, "status": report.status}


@router.post("/media/verify")
async def verify_media(file: UploadFile = File(...)):
    """Screen an evidence photo for tampering indicators (ELA + noise analysis)."""
    settings = get_settings()
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty upload")
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="Image too large")
    try:
        return forensics.verify_bytes(data)
    except ImageDecodeError:
        raise HTTPException(status_code=422, detail="File is not a decodable image")


@router.post("/webhooks/whatsapp")
async def whatsapp_webhook(
    From: str = Form(default=""),
    Body: str = Form(default=""),
    db: Session = Depends(get_db),
):
    """Twilio-compatible WhatsApp webhook.

    'CHECK <text>' -> Fraud Shield triage reply (no report stored).
    '<lat>,<lon> <text>' or free text -> citizen report intake.
    """
    if not Body.strip():
        return {"status": "ignored", "detail": "empty message"}

    check = _CHECK_RE.match(Body)
    if check and len(check.group(1).strip()) >= 5:
        a = shield.assess(check.group(1).strip(), channel="WHATSAPP")
        return {
            "status": "assessed",
            "verdict": a.verdict,
            "risk_score": a.risk_score,
            "fraud_type": a.fraud_type,
            "lang": a.lang,
            "reply": f"{a.advisory}\n{a.actions}",
            "helpline": HELPLINE,
        }

    lat = lon = None
    description = Body.strip()
    match = _COORDS_RE.match(description)
    if match:
        try:
            candidate_lat, candidate_lon = float(match.group(1)), float(match.group(2))
            if -90 <= candidate_lat <= 90 and -180 <= candidate_lon <= 180:
                lat, lon = candidate_lat, candidate_lon
                description = match.group(3).strip() or "Location report"
        except ValueError:
            pass

    report = await _store_report(
        db,
        channel="WHATSAPP",
        description=description[:2000],
        lat=lat,
        lon=lon,
        contact=From or None,
    )
    return {"status": "received", "report_id": report.id}


@router.get("/reports", dependencies=[Depends(get_current_user)])
def list_reports(limit: int = 50, db: Session = Depends(get_db)):
    limit = min(max(limit, 1), 200)
    reports = db.scalars(
        select(CitizenReport).order_by(CitizenReport.created_at.desc()).limit(limit)
    ).all()
    return [
        {
            "id": r.id,
            "channel": r.channel,
            "description": r.description,
            "lat": r.lat,
            "lon": r.lon,
            "reporter_name": r.reporter_name,
            "media_tamper_score": r.media_tamper_score,
            "status": r.status,
            "created_at": r.created_at,
        }
        for r in reports
    ]
