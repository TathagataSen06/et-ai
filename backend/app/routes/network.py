"""Fraud network graph endpoints (law-enforcement intel -> authenticated)."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.auth_service import get_current_user
from app.services.campaign_service import CampaignIntelligence
from app.services.network_service import NetworkIntelligence

router = APIRouter(
    prefix="/api/v1/network",
    tags=["network"],
    dependencies=[Depends(get_current_user)],
)


@router.get("/graph")
def network_graph(db: Session = Depends(get_db)):
    return NetworkIntelligence(db).graph()


@router.get("/dealer/{dealer_id}")
def dealer_network(dealer_id: str, db: Session = Depends(get_db)):
    result = NetworkIntelligence(db).dealer_network(dealer_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Dealer not found")
    return result


@router.get("/suspicious-accounts")
def suspicious_accounts(db: Session = Depends(get_db)):
    return NetworkIntelligence(db).suspicious_accounts()


@router.get("/campaigns")
def campaigns(db: Session = Depends(get_db)):
    return CampaignIntelligence(db).campaigns()


@router.post("/campaigns/{campaign_id}/package")
def evidence_package(campaign_id: str, db: Session = Depends(get_db),
                     user=Depends(get_current_user)):
    package = CampaignIntelligence(db).evidence_package(
        campaign_id, generated_by=user.get("sub", "unknown"))
    if package is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return package


@router.post("/sync-neo4j")
def sync_neo4j(db: Session = Depends(get_db)):
    synced = NetworkIntelligence(db).sync_to_neo4j()
    return {"synced": synced, "detail": "Synced to Neo4j" if synced else "Neo4j not configured"}
