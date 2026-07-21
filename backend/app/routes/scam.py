"""Digital-arrest scam session analysis endpoints.

`analyze-session` is public (telecom providers / victim-side apps submit live
sessions); the session archive and alert re-issue are law-enforcement only.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.orm import AnomalyEvent, CallRecord, DeviceFingerprint, ScamSession, utcnow
from app.services.alert_service import manager
from app.services.auth_service import get_current_user
from app.services.scam_detection_service import ScamDetector

router = APIRouter(prefix="/api/v1/scam", tags=["scam"])
detector = ScamDetector()


class SessionIn(BaseModel):
    transcript: str = Field(min_length=10, max_length=20000)
    caller_number: str | None = Field(default=None, max_length=30)
    victim_contact: str | None = Field(default=None, max_length=100)
    channel: str = Field(default="VOICE", max_length=20)
    duration_minutes: float | None = Field(default=None, ge=0, le=24 * 60)
    device_hash: str | None = Field(default=None, max_length=64)


def _assessment_dict(a) -> dict:
    return {
        "risk_score": a.risk_score,
        "verdict": a.verdict,
        "severity": a.severity,
        "stages": a.stages,
        "indicators": a.indicators,
        "spoof_flags": a.spoof_flags,
        "claimed_agency": a.claimed_agency,
        "script_family": a.script_family,
        "recommended_action": a.recommended_action,
    }


def _upsert_device(db: Session, device_hash: str | None, caller_number: str | None) -> None:
    if not device_hash:
        return
    existing = db.scalars(
        select(DeviceFingerprint).where(
            DeviceFingerprint.device_hash == device_hash,
            DeviceFingerprint.caller_number == caller_number,
        )
    ).first()
    if existing:
        existing.last_seen = utcnow()
        existing.sessions_count += 1
    else:
        db.add(DeviceFingerprint(device_hash=device_hash, caller_number=caller_number))


@router.post("/analyze-session")
async def analyze_session(body: SessionIn, db: Session = Depends(get_db)):
    assessment = detector.assess(
        body.transcript,
        caller_number=body.caller_number,
        channel=body.channel,
        duration_minutes=body.duration_minutes,
    )
    a = _assessment_dict(assessment)

    session = ScamSession(
        caller_number=body.caller_number,
        victim_contact=body.victim_contact,
        channel=body.channel.upper(),
        claimed_agency=assessment.claimed_agency,
        transcript=body.transcript[:20000],
        duration_minutes=body.duration_minutes,
        device_hash=body.device_hash,
        risk_score=assessment.risk_score,
        verdict=assessment.verdict,
        script_family=assessment.script_family,
        stages=assessment.stages,
        indicators=assessment.indicators,
        spoof_flags=assessment.spoof_flags,
        alerted=assessment.severity == "HIGH",
    )
    db.add(session)
    db.flush()  # allocate session.id before building the linked call record
    db.add(CallRecord(
        session_id=session.id,
        caller_number=body.caller_number,
        victim_contact=body.victim_contact,
        channel=body.channel.upper(),
        duration_minutes=body.duration_minutes,
        spoofed=bool(assessment.spoof_flags),
    ))
    _upsert_device(db, body.device_hash, body.caller_number)

    mha_alert = None
    if assessment.severity == "HIGH":
        db.add(AnomalyEvent(
            event_type="SCAM_SESSION",
            severity="HIGH",
            description=(
                f"Active digital-arrest session flagged (score {assessment.risk_score:.2f}) "
                f"from {body.caller_number or 'withheld number'} via {body.channel}"
            ),
        ))
        mha_alert = ScamDetector.mha_alert_package(
            session.id, a, caller_number=body.caller_number, channel=body.channel.upper())
    db.commit()

    if assessment.severity == "HIGH":
        await manager.broadcast({
            "type": "ALERT",
            "severity": "HIGH",
            "message": (
                f"DIGITAL ARREST session in progress — {body.caller_number or 'withheld number'} "
                f"({assessment.claimed_agency or 'agency unknown'}), score {assessment.risk_score:.2f}"
            ),
        })

    return {"session_id": session.id, **a, "mha_alert": mha_alert}


@router.get("/sessions", dependencies=[Depends(get_current_user)])
def list_sessions(limit: int = 50, db: Session = Depends(get_db)):
    limit = min(max(limit, 1), 200)
    sessions = db.scalars(
        select(ScamSession).order_by(ScamSession.created_at.desc()).limit(limit)
    ).all()
    return [
        {
            "id": s.id,
            "caller_number": s.caller_number,
            "channel": s.channel,
            "claimed_agency": s.claimed_agency,
            "script_family": s.script_family,
            "risk_score": s.risk_score,
            "verdict": s.verdict,
            "spoof_flags": s.spoof_flags,
            "alerted": s.alerted,
            "created_at": s.created_at,
        }
        for s in sessions
    ]


@router.get("/sessions/{session_id}/alert", dependencies=[Depends(get_current_user)])
def reissue_alert(session_id: str, db: Session = Depends(get_db)):
    session = db.get(ScamSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    a = {
        "risk_score": session.risk_score,
        "verdict": session.verdict,
        "claimed_agency": session.claimed_agency,
        "script_family": session.script_family,
        "indicators": session.indicators,
        "spoof_flags": session.spoof_flags,
    }
    return ScamDetector.mha_alert_package(
        session.id, a, caller_number=session.caller_number,
        channel=session.channel, created_at=session.created_at)
