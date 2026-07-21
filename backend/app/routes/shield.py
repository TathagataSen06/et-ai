"""Citizen Fraud Shield endpoints (public — citizens use these directly)."""
from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.fraud_shield_service import HELPLINE, REPORT_URL, FraudShield

router = APIRouter(prefix="/api/v1/shield", tags=["shield"])
shield = FraudShield()


class AssessIn(BaseModel):
    message: str = Field(min_length=5, max_length=10000)
    caller_number: str | None = Field(default=None, max_length=30)
    channel: str = Field(default="WEB", max_length=20)
    lang: str | None = Field(default=None, max_length=5)


@router.post("/assess")
def assess(body: AssessIn):
    a = shield.assess(
        body.message,
        caller_number=body.caller_number,
        channel=body.channel,
        lang=body.lang,
    )
    return {
        "verdict": a.verdict,
        "risk_score": a.risk_score,
        "fraud_type": a.fraud_type,
        "indicators": a.indicators,
        "lang": a.lang,
        "advisory": a.advisory,
        "actions": a.actions,
        "ivr_text": a.ivr_text,
        "helpline": HELPLINE,
        "report_url": REPORT_URL,
    }


@router.get("/languages")
def languages():
    return FraudShield.languages()
