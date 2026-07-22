"""Stateful multi-turn fraud triage for WhatsApp and IVR.

A single-shot verdict is often not enough: "someone called about my account" is
ambiguous until you know whether they demanded an OTP, insisted the victim stay
on the line, or already moved money. This walks the citizen through targeted
follow-ups, escalating or de-escalating the verdict as answers arrive, and
finishes with guidance in their language.

State lives in memory keyed by contact (a conversation is short-lived); it is
never a system of record — anything worth keeping is written as a CitizenReport
or ScamSession by the caller.
"""
import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.services.fraud_shield_service import ADVISORIES, HELPLINE, REPORT_URL, FraudShield
from app.services.scam_detection_service import ScamDetector

logger = logging.getLogger(__name__)

CONVERSATION_TTL = timedelta(minutes=30)

# Follow-up bank. Each question carries the risk delta applied when the answer
# is yes — all derived from how the fraud families actually operate.
FOLLOWUPS: dict[str, list[dict]] = {
    "_common": [
        {"id": "money_sent",
         "q": {"en": "Have you already sent any money or shared card/UPI details? Reply YES or NO.",
               "hi": "क्या आपने पहले ही पैसे भेजे या कार्ड/UPI जानकारी साझा की? हाँ या नहीं में उत्तर दें।"},
         "yes_delta": 0.15, "no_delta": 0.0, "urgent_if_yes": True},
    ],
    "DIGITAL_ARREST": [
        {"id": "stay_on_call",
         "q": {"en": "Did they tell you to stay on the call and not inform anyone? Reply YES or NO.",
               "hi": "क्या उन्होंने कॉल पर बने रहने और किसी को न बताने को कहा? हाँ या नहीं।"},
         "yes_delta": 0.25, "no_delta": -0.05},
        {"id": "video_call",
         "q": {"en": "Are they demanding a video call or showing a uniform/office? Reply YES or NO.",
               "hi": "क्या वे वीडियो कॉल या वर्दी/कार्यालय दिखाने की मांग कर रहे हैं? हाँ या नहीं।"},
         "yes_delta": 0.2, "no_delta": -0.05},
    ],
    "OTP_THEFT": [
        {"id": "asked_otp",
         "q": {"en": "Did they ask you to read out an OTP or PIN? Reply YES or NO.",
               "hi": "क्या उन्होंने OTP या PIN बताने को कहा? हाँ या नहीं।"},
         "yes_delta": 0.3, "no_delta": -0.15},
    ],
    "UPI_COLLECT_FRAUD": [
        {"id": "collect_request",
         "q": {"en": "Did you get a payment REQUEST (not a credit) in your UPI app? Reply YES or NO.",
               "hi": "क्या आपके UPI ऐप में भुगतान अनुरोध आया (जमा नहीं)? हाँ या नहीं।"},
         "yes_delta": 0.25, "no_delta": -0.1},
    ],
    "KYC_EXPIRY": [
        {"id": "link_click",
         "q": {"en": "Did they send a link to update KYC? Reply YES or NO.",
               "hi": "क्या उन्होंने KYC अपडेट करने के लिए लिंक भेजा? हाँ या नहीं।"},
         "yes_delta": 0.2, "no_delta": -0.1},
    ],
}

PROMPTS: dict[str, dict[str, str]] = {
    "opening": {
        "en": "Fraud Shield here. Tell me what happened — what did the caller or message say?",
        "hi": "फ्रॉड शील्ड। बताइए क्या हुआ — कॉल या संदेश में क्या कहा गया?",
    },
    "too_short": {
        "en": "Please describe it in a few more words so I can assess the risk.",
        "hi": "कृपया थोड़ा और विस्तार से बताएं ताकि मैं जोखिम का आकलन कर सकूं।",
    },
    "unclear": {
        "en": "Please reply YES or NO.",
        "hi": "कृपया हाँ या नहीं में उत्तर दें।",
    },
    "urgent": {
        "en": "Money already moved — call 1930 NOW and tell your bank to freeze the account. "
              "The first hours matter most for recovery.",
        "hi": "पैसे भेजे जा चुके हैं — तुरंत 1930 पर कॉल करें और बैंक से खाता फ्रीज करवाएं। "
              "शुरुआती घंटे सबसे महत्वपूर्ण हैं।",
    },
}

_YES = re.compile(r"^\s*(y|yes|yeah|yep|haan|haa|ha|हाँ|हा|ok|okay|sent|done)\b", re.I)
_NO = re.compile(r"^\s*(n|no|nope|nahi|nahin|नहीं|ना|never)\b", re.I)


def _text(bank: dict, key: str, lang: str) -> str:
    entry = bank.get(key, {})
    return entry.get(lang) or entry.get("en", "")


@dataclass
class Conversation:
    contact: str
    lang: str = "en"
    stage: str = "OPENING"        # OPENING -> ASSESSING -> FOLLOWUP -> DONE
    description: str = ""
    fraud_type: str | None = None
    risk: float = 0.0
    pending: list[dict] = field(default_factory=list)
    answers: dict[str, bool] = field(default_factory=dict)
    urgent: bool = False
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class ConversationEngine:
    def __init__(self) -> None:
        self._convos: dict[str, Conversation] = {}
        self._lock = threading.Lock()
        self._shield = FraudShield()
        self._detector = ScamDetector()

    def _reap(self) -> None:
        cutoff = datetime.now(timezone.utc) - CONVERSATION_TTL
        for key in [k for k, c in self._convos.items() if c.updated_at < cutoff]:
            self._convos.pop(key, None)

    def reset(self, contact: str) -> None:
        with self._lock:
            self._convos.pop(contact, None)

    def active(self, contact: str) -> bool:
        """True when a triage is mid-flight (opened, not yet concluded)."""
        with self._lock:
            self._reap()
            convo = self._convos.get(contact)
            return convo is not None and convo.stage in ("OPENING", "ASSESSING", "FOLLOWUP")

    def handle(self, contact: str, message: str, *, lang: str | None = None) -> dict:
        """Advance the conversation one turn and return the reply."""
        message = (message or "").strip()
        with self._lock:
            self._reap()
            convo = self._convos.get(contact)
            if convo is None:
                convo = Conversation(contact=contact)
                self._convos[contact] = convo
            convo.updated_at = datetime.now(timezone.utc)

        if lang in ADVISORIES:
            convo.lang = lang

        if convo.stage in ("OPENING", "DONE"):
            return self._start(convo, message)
        if convo.stage == "FOLLOWUP":
            return self._answer(convo, message)
        return self._start(convo, message)

    # ------------------------------------------------------------------
    def _start(self, convo: Conversation, message: str) -> dict:
        if len(message) < 5:
            convo.stage = "OPENING"
            return self._reply(convo, _text(PROMPTS, "opening", convo.lang), done=False)

        convo.description = message
        convo.lang = convo.lang if convo.lang != "en" else FraudShield.detect_language(message)
        assessment = self._shield.assess(message, channel="WHATSAPP", lang=convo.lang)
        convo.fraud_type = assessment.fraud_type
        convo.risk = assessment.risk_score
        convo.answers.clear()

        # Pick follow-ups from the *signals* present, not only the final label:
        # a citizen paraphrasing "he said he was from CBI" often scores below
        # the digital-arrest threshold, yet those are exactly the questions
        # that resolve the case.
        scam = self._detector.assess(message, channel="WHATSAPP")
        families: list[str] = []
        if convo.fraud_type and convo.fraud_type != "DIGITAL_ARREST":
            families.append(convo.fraud_type)
        arrest_signal = bool(scam.claimed_agency) or any(
            s["matched"] for s in scam.stages
            if s["stage"] in ("ACCUSATION", "ISOLATION", "ESCALATION", "PAYMENT_DEMAND")
        )
        if convo.fraud_type == "DIGITAL_ARREST" or arrest_signal:
            families.insert(0, "DIGITAL_ARREST")
            convo.fraud_type = convo.fraud_type or "DIGITAL_ARREST"

        convo.pending = [q for fam in families for q in FOLLOWUPS.get(fam, [])]
        convo.pending += FOLLOWUPS["_common"]
        if not convo.pending:
            return self._finish(convo)

        convo.stage = "FOLLOWUP"
        return self._ask(convo)

    def _answer(self, convo: Conversation, message: str) -> dict:
        if not convo.pending:
            return self._finish(convo)
        question = convo.pending[0]
        if _YES.match(message):
            yes = True
        elif _NO.match(message):
            yes = False
        else:
            restated = question["q"].get(convo.lang) or question["q"].get("en", "")
            return self._reply(
                convo,
                f"{_text(PROMPTS, 'unclear', convo.lang)}\n{restated}",
                done=False)

        convo.answers[question["id"]] = yes
        convo.risk = max(0.0, min(1.0, convo.risk + (question["yes_delta"] if yes
                                                     else question["no_delta"])))
        if yes and question.get("urgent_if_yes"):
            convo.urgent = True
        convo.pending.pop(0)

        if convo.pending:
            return self._ask(convo)
        return self._finish(convo)

    def _ask(self, convo: Conversation) -> dict:
        q = convo.pending[0]["q"]
        text = q.get(convo.lang) or q.get("en", "")
        return self._reply(convo, text, done=False)

    def _finish(self, convo: Conversation) -> dict:
        convo.stage = "DONE"
        pack = ADVISORIES.get(convo.lang, ADVISORIES["en"])
        if convo.risk >= 0.6:
            verdict, level = "HIGH_RISK", "high"
        elif convo.risk >= 0.4:
            verdict, level = "SUSPICIOUS", "medium"
        else:
            verdict, level = "LIKELY_SAFE", "safe"
        lines = [pack[level], pack["actions"]]
        if convo.urgent:
            lines.insert(0, _text(PROMPTS, "urgent", convo.lang))
        return self._reply(convo, "\n".join(lines), done=True,
                           verdict=verdict, fraud_type=convo.fraud_type)

    def _reply(self, convo: Conversation, text: str, *, done: bool,
               verdict: str | None = None, fraud_type: str | None = None) -> dict:
        return {
            "reply": text,
            "stage": convo.stage,
            "done": done,
            "lang": convo.lang,
            "risk_score": round(convo.risk, 3),
            "fraud_type": fraud_type if done else convo.fraud_type,
            "verdict": verdict,
            "questions_remaining": len(convo.pending),
            "helpline": HELPLINE,
            "report_url": REPORT_URL,
        }


engine = ConversationEngine()
