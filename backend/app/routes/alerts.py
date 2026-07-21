"""Alert endpoints: recent anomalies + WebSocket stream."""
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.orm import AnomalyEvent
from app.models.schemas import AlertOut
from app.services.alert_service import manager

router = APIRouter(tags=["alerts"])


@router.get("/api/v1/alerts/recent", response_model=list[AlertOut])
def recent_alerts(limit: int = 20, db: Session = Depends(get_db)):
    limit = min(max(limit, 1), 100)
    return list(
        db.scalars(
            select(AnomalyEvent).order_by(AnomalyEvent.created_at.desc()).limit(limit)
        ).all()
    )


@router.websocket("/ws/dashboard")
async def dashboard_stream(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Keep the connection open; clients don't need to send anything,
            # but reading lets us notice disconnects promptly.
            await websocket.receive_text()
    except WebSocketDisconnect:
        await manager.disconnect(websocket)
