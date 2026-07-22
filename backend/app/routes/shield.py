"""Citizen Fraud Shield endpoints (public — citizens use these directly)."""
from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.services.conversation_service import engine as conversation
from app.services.fraud_shield_service import ADVISORIES, HELPLINE, REPORT_URL, FraudShield

router = APIRouter(prefix="/api/v1/shield", tags=["shield"])
shield = FraudShield()

# IVR language menu: keypad digit -> language code. Nine slots on a phone
# keypad, so the four least-common of the thirteen are reachable via the
# conversational/WhatsApp channel instead.
IVR_LANGUAGES: dict[str, str] = {
    "1": "en", "2": "hi", "3": "bn", "4": "ta", "5": "te",
    "6": "mr", "7": "kn", "8": "gu", "9": "ml",
}

IVR_CATEGORIES: dict[str, str] = {
    "1": "A caller claiming to be police, CBI, ED or customs",
    "2": "A message or call asking for OTP, PIN or payment",
    "3": "I have already sent money",
}

# Seed descriptions per category, fed to the same triage engine the chat uses
# so a phone caller and a WhatsApp user get identical risk logic.
IVR_SEEDS: dict[str, str] = {
    "1": ("caller claims to be from CBI police enforcement directorate, says case "
          "registered against my aadhaar, told me not to tell anyone and stay on the call"),
    "2": ("received message asking me to share the OTP and make a payment to verify "
          "my account or it will be blocked"),
    "3": "I already transferred money to a caller who claimed to be a government officer",
}


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


class IvrIn(BaseModel):
    call_id: str = Field(min_length=4, max_length=64)
    digits: str = Field(default="", max_length=8)
    step: str = Field(default="LANGUAGE", max_length=20)


@router.post("/ivr")
def ivr(body: IvrIn):
    """DTMF voice menu: language -> category -> yes/no follow-ups -> verdict.

    Returns short prompts sized for text-to-speech. `step` is echoed back by
    the telephony platform on the next request, so no server session is needed
    beyond the triage state keyed on call_id.
    """
    contact = f"ivr:{body.call_id}"
    digit = body.digits.strip()[:1]

    if body.step == "LANGUAGE":
        if digit not in IVR_LANGUAGES:
            options = " ".join(f"{k} {ADVISORIES[v]['name']}." for k, v in IVR_LANGUAGES.items())
            return {"step": "LANGUAGE", "terminal": False,
                    "prompt": f"Welcome to Fraud Shield. Choose a language. {options}",
                    "expects": "digit"}
        lang = IVR_LANGUAGES[digit]
        conversation.reset(contact)
        conversation.handle(contact, "", lang=lang)
        options = " ".join(f"Press {k}. {v}." for k, v in IVR_CATEGORIES.items())
        return {"step": "CATEGORY", "terminal": False, "lang": lang,
                "prompt": f"What happened? {options}", "expects": "digit"}

    if body.step == "CATEGORY":
        seed = IVR_SEEDS.get(digit)
        if seed is None:
            options = " ".join(f"Press {k}. {v}." for k, v in IVR_CATEGORIES.items())
            return {"step": "CATEGORY", "terminal": False,
                    "prompt": f"Sorry, I did not get that. {options}", "expects": "digit"}
        result = conversation.handle(contact, seed)
        return _ivr_turn(result)

    # FOLLOWUP: 1 = yes, 2 = no
    answer = {"1": "yes", "2": "no"}.get(digit)
    if answer is None:
        return {"step": "FOLLOWUP", "terminal": False,
                "prompt": "Press 1 for yes, or 2 for no.", "expects": "digit"}
    return _ivr_turn(conversation.handle(contact, answer))


def _ivr_turn(result: dict) -> dict:
    if result["done"]:
        return {"step": "DONE", "terminal": True, "lang": result["lang"],
                "verdict": result["verdict"], "risk_score": result["risk_score"],
                "prompt": f"{result['reply']} Helpline {HELPLINE}.",
                "helpline": HELPLINE, "report_url": REPORT_URL}
    return {"step": "FOLLOWUP", "terminal": False, "lang": result["lang"],
            "risk_score": result["risk_score"],
            "prompt": f"{result['reply']} Press 1 for yes, 2 for no.",
            "expects": "digit"}
