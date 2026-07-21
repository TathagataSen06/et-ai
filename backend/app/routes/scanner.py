"""Currency scanner endpoints."""
import logging
from datetime import datetime, timedelta, timezone

import piexif
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models.orm import AnomalyEvent, ScanRecord
from app.models.schemas import ScanResult, ScanStatistics
from app.services.alert_service import manager
from app.services.cv_service import CounterfeitDetector, ImageDecodeError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/scanner", tags=["scanner"])
detector = CounterfeitDetector()

NEXT_STEPS = {
    "LIKELY_COUNTERFEIT": [
        "Do not return the note to circulation",
        "Note the serial number and where you received it",
        "Report to the nearest police station or bank branch",
        "A geotagged alert has been sent to the command center",
    ],
    "SUSPICIOUS": [
        "Compare against a known-genuine note of the same denomination",
        "Check the security thread and watermark under light",
        "If still in doubt, take it to a bank for verification",
    ],
    "LIKELY_GENUINE": [
        "No strong counterfeit indicators found",
        "This is a screening result, not a certification",
    ],
}


def _gps_from_exif(data: bytes) -> tuple[float, float] | None:
    """Extract decimal (lat, lon) from JPEG EXIF GPS tags, if present."""
    try:
        exif = piexif.load(data)
        gps = exif.get("GPS") or {}
        lat_ref = gps.get(piexif.GPSIFD.GPSLatitudeRef, b"N").decode()
        lon_ref = gps.get(piexif.GPSIFD.GPSLongitudeRef, b"E").decode()
        lat_dms = gps.get(piexif.GPSIFD.GPSLatitude)
        lon_dms = gps.get(piexif.GPSIFD.GPSLongitude)
        if not lat_dms or not lon_dms:
            return None

        def to_decimal(dms, ref, negative_ref):
            degrees = dms[0][0] / dms[0][1]
            minutes = dms[1][0] / dms[1][1]
            seconds = dms[2][0] / dms[2][1]
            value = degrees + minutes / 60 + seconds / 3600
            return -value if ref == negative_ref else value

        return (
            round(to_decimal(lat_dms, lat_ref, "S"), 6),
            round(to_decimal(lon_dms, lon_ref, "W"), 6),
        )
    except Exception:
        return None


@router.post("/analyze", response_model=ScanResult)
async def analyze_currency(
    file: UploadFile = File(...),
    lat: float | None = Form(default=None),
    lon: float | None = Form(default=None),
    user_type: str | None = Form(default=None),
    db: Session = Depends(get_db),
):
    """Analyze an uploaded note image; returns counterfeit score and feature breakdown."""
    settings = get_settings()
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty upload")
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="Image too large")
    if lat is not None and not -90 <= lat <= 90:
        raise HTTPException(status_code=422, detail="lat out of range")
    if lon is not None and not -180 <= lon <= 180:
        raise HTTPException(status_code=422, detail="lon out of range")

    # Spec: fall back to GPS embedded in the photo's EXIF metadata.
    if lat is None or lon is None:
        exif_coords = _gps_from_exif(data)
        if exif_coords:
            lat, lon = exif_coords

    try:
        # Two-tier assessment: fast pass, escalating borderline scores to the
        # perturbation-consensus ensemble with adaptive verdict margins.
        assessment = detector.assess_bytes(
            data,
            threshold_suspicious=settings.threshold_suspicious,
            threshold_counterfeit=settings.threshold_counterfeit,
        )
    except ImageDecodeError:
        raise HTTPException(status_code=422, detail="File is not a decodable image")

    score = assessment.counterfeit_score
    recommendation = assessment.verdict
    alert_level = {
        "LIKELY_COUNTERFEIT": "HIGH",
        "SUSPICIOUS": "MEDIUM",
        "LIKELY_GENUINE": "LOW",
    }[recommendation]

    record = ScanRecord(
        user_type=user_type,
        lat=lat,
        lon=lon,
        counterfeit_score=score,
        denomination=assessment.denomination,
        recommendation=recommendation,
        features={
            **assessment.features,
            "_meta": {
                "uncertainty": assessment.uncertainty,
                "mode": assessment.mode,
                "verdict_reason": assessment.verdict_reason,
                "ensemble_scores": assessment.ensemble_scores,
                "calibrated": assessment.calibrated,
                "genuine_percentile": assessment.genuine_percentile,
            },
        },
    )
    db.add(record)

    if alert_level in ("HIGH", "MEDIUM"):
        event = AnomalyEvent(
            event_type="HIGH_RISK_SCAN",
            lat=lat,
            lon=lon,
            severity=alert_level,
            description=(
                f"{recommendation} ₹{assessment.denomination} note scanned "
                f"(score {score:.2f})"
                + (
                    f" at ({lat:.4f}, {lon:.4f})"
                    if lat is not None and lon is not None
                    else ""
                )
            ),
        )
        db.add(event)
        db.commit()
        await manager.broadcast({
            "type": "ALERT",
            "severity": alert_level,
            "message": event.description,
            "lat": lat,
            "lon": lon,
        })
    else:
        db.commit()

    next_steps = list(NEXT_STEPS[recommendation])
    if "instability" in assessment.verdict_reason:
        next_steps.insert(
            0, "Verdict was unstable across capture perturbations — retake the photo "
               "in steadier, brighter conditions",
        )

    return ScanResult(
        scan_id=record.id,
        counterfeit_score=score,
        recommendation=recommendation,
        alert_level=alert_level,
        denomination=assessment.denomination,
        detailed_breakdown={
            name: {"confidence": f["confidence"], "detail": f["detail"], "status": f["status"]}
            for name, f in assessment.features.items()
        },
        next_steps=next_steps,
        created_at=record.created_at,
        uncertainty=assessment.uncertainty,
        analysis_mode=assessment.mode,
        verdict_reason=assessment.verdict_reason,
        effective_thresholds=assessment.thresholds,
        calibrated=assessment.calibrated,
        genuine_percentile=assessment.genuine_percentile,
    )


@router.post("/batch-analyze")
async def batch_analyze(
    files: list[UploadFile] = File(...),
    lat: float | None = Form(default=None),
    lon: float | None = Form(default=None),
    db: Session = Depends(get_db),
):
    """Analyze multiple note images in one request (spec batch endpoint).

    Returns per-file results with coordinates for geospatial ingestion;
    individual decode failures don't fail the batch.
    """
    settings = get_settings()
    if len(files) > settings.batch_max_files:
        raise HTTPException(
            status_code=413, detail=f"Maximum {settings.batch_max_files} files per batch"
        )
    if lat is not None and not -90 <= lat <= 90:
        raise HTTPException(status_code=422, detail="lat out of range")
    if lon is not None and not -180 <= lon <= 180:
        raise HTTPException(status_code=422, detail="lon out of range")

    results = []
    for upload in files:
        data = await upload.read()
        entry: dict = {"filename": upload.filename}
        if not data or len(data) > settings.max_upload_bytes:
            entry["error"] = "empty or oversized file"
            results.append(entry)
            continue
        coords = (lat, lon) if lat is not None and lon is not None else _gps_from_exif(data)
        try:
            analysis = detector.analyze_bytes(data)
        except ImageDecodeError:
            entry["error"] = "not a decodable image"
            results.append(entry)
            continue

        score = analysis.counterfeit_score
        recommendation = (
            "LIKELY_COUNTERFEIT" if score > settings.threshold_counterfeit
            else "SUSPICIOUS" if score > settings.threshold_suspicious
            else "LIKELY_GENUINE"
        )
        record = ScanRecord(
            lat=coords[0] if coords else None,
            lon=coords[1] if coords else None,
            counterfeit_score=score,
            denomination=analysis.denomination,
            recommendation=recommendation,
            features=analysis.features,
        )
        db.add(record)
        entry.update({
            "scan_id": record.id,
            "counterfeit_score": score,
            "recommendation": recommendation,
            "denomination": analysis.denomination,
            "lat": coords[0] if coords else None,
            "lon": coords[1] if coords else None,
        })
        results.append(entry)

    db.commit()
    flagged = sum(1 for r in results if r.get("recommendation") in ("LIKELY_COUNTERFEIT", "SUSPICIOUS"))
    if flagged:
        await manager.broadcast({
            "type": "ALERT",
            "severity": "MEDIUM",
            "message": f"Batch scan: {flagged}/{len(results)} notes flagged",
            "lat": lat,
            "lon": lon,
        })
    return {"count": len(results), "flagged": flagged, "results": results}


@router.get("/statistics", response_model=ScanStatistics)
def scan_statistics(days: int = 30, db: Session = Depends(get_db)):
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    scans = db.scalars(select(ScanRecord).where(ScanRecord.created_at > cutoff)).all()

    by_recommendation: dict[str, int] = {}
    by_denomination: dict[str, int] = {}
    daily: dict[str, list[float]] = {}
    for s in scans:
        by_recommendation[s.recommendation] = by_recommendation.get(s.recommendation, 0) + 1
        by_denomination[s.denomination] = by_denomination.get(s.denomination, 0) + 1
        day = s.created_at.strftime("%Y-%m-%d")
        daily.setdefault(day, []).append(s.counterfeit_score)

    return ScanStatistics(
        total_scans=len(scans),
        avg_counterfeit_score=round(
            sum(s.counterfeit_score for s in scans) / len(scans), 4
        ) if scans else 0.0,
        by_recommendation=by_recommendation,
        by_denomination=by_denomination,
        daily_counts=[
            {"date": day, "count": len(scores), "avg_score": round(sum(scores) / len(scores), 3)}
            for day, scores in sorted(daily.items())
        ],
    )
