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
from app.services.live_call_service import tracker
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


class StreamChunkIn(BaseModel):
    call_id: str = Field(min_length=4, max_length=64)
    chunk: str = Field(default="", max_length=8000)
    caller_number: str | None = Field(default=None, max_length=30)
    victim_contact: str | None = Field(default=None, max_length=100)
    channel: str = Field(default="VOICE", max_length=20)
    device_hash: str | None = Field(default=None, max_length=64)
    final: bool = False


def _persist_live_call(db: Session, call, assessment, a: dict) -> ScamSession:
    """Write (or update) the durable row for a live call."""
    session = db.get(ScamSession, call.session_id) if call.session_id else None
    if session is None:
        session = ScamSession(
            caller_number=call.caller_number,
            victim_contact=call.victim_contact,
            channel=call.channel,
            device_hash=call.device_hash,
        )
        db.add(session)
        db.flush()
        call.session_id = session.id
        db.add(CallRecord(
            session_id=session.id,
            caller_number=call.caller_number,
            victim_contact=call.victim_contact,
            channel=call.channel,
            duration_minutes=round(call.duration_minutes, 1),
            spoofed=bool(assessment.spoof_flags),
        ))
        _upsert_device(db, call.device_hash, call.caller_number)
    session.claimed_agency = assessment.claimed_agency
    session.transcript = call.transcript
    session.duration_minutes = round(call.duration_minutes, 1)
    session.risk_score = assessment.risk_score
    session.verdict = assessment.verdict
    session.script_family = assessment.script_family
    session.stages = assessment.stages
    session.indicators = assessment.indicators
    session.spoof_flags = assessment.spoof_flags
    session.alerted = call.alerted
    return session


@router.post("/stream")
async def stream_chunk(body: StreamChunkIn, db: Session = Depends(get_db)):
    """Ingest one transcript chunk of a call that is still in progress.

    Telecom-provider shaped: post chunks as they are transcribed. The response
    carries the running risk, and the first chunk that crosses HIGH raises the
    alert immediately — before the victim is talked into transferring funds.
    """
    call, assessment = tracker.ingest(
        body.call_id, body.chunk,
        caller_number=body.caller_number,
        victim_contact=body.victim_contact,
        channel=body.channel,
        device_hash=body.device_hash,
    )
    a = _assessment_dict(assessment)
    newly_alerted = assessment.severity == "HIGH" and not call.alerted
    if newly_alerted:
        call.alerted = True
        call.alerted_at_chunk = len(call.chunks)

    mha_alert = None
    if newly_alerted or body.final:
        session = _persist_live_call(db, call, assessment, a)
        if newly_alerted:
            db.add(AnomalyEvent(
                event_type="SCAM_SESSION_LIVE",
                severity="HIGH",
                description=(
                    f"LIVE digital-arrest call flagged mid-session at chunk "
                    f"{call.alerted_at_chunk} (score {assessment.risk_score:.2f}) "
                    f"from {call.caller_number or 'withheld number'}"
                ),
            ))
            mha_alert = ScamDetector.mha_alert_package(
                session.id, a, caller_number=call.caller_number, channel=call.channel)
        db.commit()

    if newly_alerted:
        await manager.broadcast({
            "type": "ALERT",
            "severity": "HIGH",
            "message": (
                f"LIVE CALL IN PROGRESS — digital-arrest script detected at chunk "
                f"{call.alerted_at_chunk} from {call.caller_number or 'withheld number'} "
                f"(score {assessment.risk_score:.2f}). Intervene before transfer."
            ),
        })

    if body.final:
        tracker.close(body.call_id)

    return {
        "call_id": call.call_id,
        "chunks_received": len(call.chunks),
        "alert_fired_now": newly_alerted,
        "alerted": call.alerted,
        "alerted_at_chunk": call.alerted_at_chunk,
        "session_id": call.session_id,
        "final": body.final,
        **a,
        "mha_alert": mha_alert,
    }


@router.get("/stream/active", dependencies=[Depends(get_current_user)])
def active_calls():
    """Calls currently in flight, most recently updated first."""
    return [
        {
            "call_id": c.call_id,
            "caller_number": c.caller_number,
            "channel": c.channel,
            "chunks": len(c.chunks),
            "alerted": c.alerted,
            "alerted_at_chunk": c.alerted_at_chunk,
            "duration_minutes": round(c.duration_minutes, 1),
            "started_at": c.started_at,
        }
        for c in tracker.active()
    ]


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
