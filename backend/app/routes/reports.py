"""Intelligence report generation endpoints (authenticated)."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.auth_service import get_current_user
from app.services.report_service import ReportService

router = APIRouter(
    prefix="/api/v1/reports",
    tags=["reports"],
    dependencies=[Depends(get_current_user)],
)


@router.post("/generate/{cluster_id}")
def generate_report(cluster_id: str, db: Session = Depends(get_db)):
    report = ReportService(db).generate_cluster_report(cluster_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Cluster not found")
    return report
