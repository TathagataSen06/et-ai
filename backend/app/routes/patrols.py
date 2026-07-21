"""Patrol recommendation and assignment endpoints."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.orm import PatrolRoute
from app.models.schemas import (
    PatrolAssignRequest,
    PatrolOut,
    PatrolRecommendation,
    PatrolStatusUpdate,
)
from app.services.auth_service import get_current_user
from app.services.patrol_service import PatrolIntelligence

# All patrol operations are law-enforcement actions -> authenticated.
router = APIRouter(
    prefix="/api/v1/patrols",
    tags=["patrols"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/recommendations", response_model=list[PatrolRecommendation])
def recommendations(officers_available: int = 5, db: Session = Depends(get_db)):
    return PatrolIntelligence(db).recommendations(officers_available=officers_available)


@router.post("/assign", response_model=PatrolOut, status_code=201)
def assign(body: PatrolAssignRequest, db: Session = Depends(get_db)):
    try:
        return PatrolIntelligence(db).assign(body.officer_name, body.hotspot_id, body.notes)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/status", response_model=list[PatrolOut])
def status(db: Session = Depends(get_db)):
    return list(
        db.scalars(select(PatrolRoute).order_by(PatrolRoute.date_assigned.desc()).limit(50)).all()
    )


@router.get("/predictions")
def predictions(days_ahead: int = 7, db: Session = Depends(get_db)):
    """Forecast seizure activity per hotspot (seasonal trend + spike anomalies)."""
    from app.services.forecast_service import ForecastService

    days_ahead = min(max(days_ahead, 1), 14)
    return ForecastService(db).predict_hotspots(days_ahead=days_ahead)


@router.get("/{officer_id}/route", response_model=list[PatrolOut])
def officer_route(officer_id: str, db: Session = Depends(get_db)):
    """Current (pending/active) routes for an officer, newest first."""
    routes = list(
        db.scalars(
            select(PatrolRoute)
            .where(PatrolRoute.officer_name == officer_id)
            .where(PatrolRoute.status.in_(["PENDING", "ACTIVE"]))
            .order_by(PatrolRoute.date_assigned.desc())
        ).all()
    )
    if not routes:
        raise HTTPException(status_code=404, detail="No active route for this officer")
    return routes


@router.put("/{route_id}/status", response_model=PatrolOut)
def update_status(route_id: str, body: PatrolStatusUpdate, db: Session = Depends(get_db)):
    route = db.get(PatrolRoute, route_id)
    if route is None:
        raise HTTPException(status_code=404, detail="Patrol route not found")
    route.status = body.status
    db.commit()
    return route
