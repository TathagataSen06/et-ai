"""Digital-arrest scam session detection (rule-based, zero training).

Classifies a call/message session against the documented anatomy of Indian
"digital arrest" operations: a staged script progression (contact → authority
claim → accusation → isolation → escalation → payment demand), caller-number
spoofing signatures, and session-metadata pressure indicators. Deterministic
and fully offline, so every verdict is reproducible and explainable — the same
design constraint as the counterfeit scanner.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Script stage banks. Order matters: it mirrors the canonical call flow used
# by fraud compounds (MHA/I4C advisories describe the same progression).
# ---------------------------------------------------------------------------

STAGE_PATTERNS: list[tuple[str, float, list[str]]] = [
    ("CONTACT", 0.05, [
        r"calling from", r"this is (an? )?(urgent|official)", r"courier", r"parcel",
        r"fedex", r"dhl", r"telecom department", r"your (sim|number) (will be|is being)",
    ]),
    ("AUTHORITY_CLAIM", 0.15, [
        r"\bcbi\b", r"\bed\b", r"enforcement directorate", r"\bcustoms\b", r"\bncb\b",
        r"narcotics", r"\btrai\b", r"\brbi\b", r"cyber ?cell", r"cyber ?crime branch",
        r"income tax", r"police (station|headquarters|commissioner)", r"\bcrpf\b",
        r"interpol", r"supreme court", r"\bofficer\b.{0,40}\b(badge|id) number\b",
    ]),
    ("ACCUSATION", 0.15, [
        r"case (has been |is )?(registered|filed) (against|in) your", r"\bfir\b",
        r"money laundering", r"arrest warrant", r"non.?bailable", r"illegal (parcel|package|consignment)",
        r"drugs? (found|seized|detected)", r"your (aadhaar|pan|sim|account) (is|was|has been) (linked|used|misused)",
        r"human trafficking", r"suspicious transactions? (found|detected)",
    ]),
    ("ISOLATION", 0.15, [
        r"do not (tell|inform|contact|disclose)", r"don'?t (tell|inform) (anyone|your family)",
        r"strictly confidential", r"secrecy", r"stay on (the )?(call|line|camera|video)",
        r"do not (disconnect|hang up|cut the call)", r"keep (your )?camera on",
        r"you are under (our )?(surveillance|watch|observation)", r"cannot leave (the room|your house)",
    ]),
    ("ESCALATION", 0.10, [
        r"digital arrest", r"virtual arrest", r"house arrest", r"transferr?ed to (my )?senior",
        r"(skype|video) (call|statement|interrogation|hearing)", r"record your statement",
        r"court hearing (is |has been )?scheduled", r"connecting you to (the )?(cbi|ed|police|court)",
        r"wear your (uniform|id)", r"verification (call|process) (will|must) continue",
    ]),
    ("PAYMENT_DEMAND", 0.25, [
        r"\brtgs\b", r"\bneft\b", r"safe (custody )?account", r"government (verification )?account",
        r"verification of (your )?funds", r"refundable (security )?deposit", r"transfer (all|your) (funds|money|savings)",
        r"clear your name.{0,40}(pay|transfer|deposit)", r"gift ?cards?", r"bitcoin|crypto|usdt",
        r"security deposit.{0,30}refund", r"legaliz(e|ation) fee", r"bail amount",
    ]),
]

# High-signal single indicators outside the stage flow.
BONUS_PATTERNS: list[tuple[str, float]] = [
    (r"digital arrest", 0.05),
    (r"do not (tell|inform).{0,40}(bank|family|police)", 0.05),
    (r"(whatsapp|video) call.{0,60}(uniform|police station backdrop)", 0.05),
]

_AGENCY_RE = re.compile(
    r"\b(cbi|enforcement directorate|\bed\b|customs|ncb|narcotics|trai|rbi|police|"
    r"cyber ?cell|cyber ?crime|income tax|interpol|supreme court)\b", re.IGNORECASE)

# Country codes fraud compounds commonly originate from while impersonating
# Indian agencies (per I4C advisories): Pakistan, Vietnam, Cambodia, Myanmar,
# Laos, Hong Kong, and generic premium/unknown routes.
_FOREIGN_PREFIXES = ("+92", "+84", "+855", "+95", "+856", "+852", "+673", "+994")

_INDIA_MOBILE_RE = re.compile(r"^(\+91[\s-]?)?[6-9]\d{9}$")
_TELEMARKETING_RE = re.compile(r"^(\+91[\s-]?)?140\d{7}$")


@dataclass
class ScamAssessment:
    risk_score: float
    verdict: str  # ACTIVE_SCAM_LIKELY / SUSPICIOUS / LOW_RISK
    severity: str  # HIGH / MEDIUM / LOW
    stages: list[dict] = field(default_factory=list)
    indicators: list[str] = field(default_factory=list)
    spoof_flags: list[str] = field(default_factory=list)
    claimed_agency: str | None = None
    script_family: str | None = None
    recommended_action: str = ""


SCRIPT_FAMILIES: dict[str, list[str]] = {
    "DIGITAL_ARREST_CBI": [r"digital arrest", r"\bcbi\b", r"money laundering", r"arrest warrant"],
    "PARCEL_CUSTOMS_NCB": [r"parcel", r"courier", r"customs", r"\bncb\b", r"drugs?"],
    "TRAI_SIM_DEACTIVATION": [r"\btrai\b", r"sim.{0,30}(deactivat|block|suspend)", r"telecom department"],
    "BANK_KYC_ED": [r"kyc", r"account.{0,30}(frozen|blocked|suspended)", r"enforcement directorate|\bed\b"],
}


class ScamDetector:
    """Stateless classifier. All methods are pure; persistence lives in routes."""

    def assess(self, transcript: str, *, caller_number: str | None = None,
               channel: str = "VOICE", duration_minutes: float | None = None) -> ScamAssessment:
        text = (transcript or "").lower()
        channel = (channel or "VOICE").upper()

        # --- stage progression ---------------------------------------------
        stages: list[dict] = []
        matched_positions: list[tuple[int, int]] = []  # (stage_index, first char pos)
        score = 0.0
        indicators: list[str] = []
        for idx, (stage, weight, patterns) in enumerate(STAGE_PATTERNS):
            hits = []
            first_pos = None
            for p in patterns:
                m = re.search(p, text)
                if m:
                    hits.append(m.group(0)[:60])
                    first_pos = m.start() if first_pos is None else min(first_pos, m.start())
            matched = bool(hits)
            stages.append({"stage": stage, "matched": matched, "evidence": hits[:3]})
            if matched:
                score += weight
                matched_positions.append((idx, first_pos or 0))
                indicators.append(f"{stage}: “{hits[0]}”")

        # Canonical-order bonus: the script unfolding in sequence is itself a
        # signature (organic conversations don't follow the compound playbook).
        if len(matched_positions) >= 4:
            positions = [pos for _, pos in sorted(matched_positions)]
            if positions == sorted(positions):
                score += 0.05
                indicators.append("Script stages appear in canonical call-flow order")

        for pattern, bonus in BONUS_PATTERNS:
            if re.search(pattern, text):
                score += bonus

        # --- claimed agency -------------------------------------------------
        agency_match = _AGENCY_RE.search(transcript or "")
        claimed_agency = agency_match.group(0).upper().strip() if agency_match else None

        # --- number spoofing signatures ------------------------------------
        spoof_flags = self._spoof_signatures(caller_number, claimed_agency, channel)
        score += min(len(spoof_flags) * 0.08, 0.16)

        # --- session metadata pressure --------------------------------------
        if channel == "VIDEO" and claimed_agency:
            score += 0.05
            indicators.append("Government agencies do not conduct video-call interrogations")
        if duration_minutes and duration_minutes >= 60 and claimed_agency:
            score += 0.05
            indicators.append(f"Hostage-style call duration ({duration_minutes:.0f} min)")

        score = round(min(score, 1.0), 3)
        if score >= 0.7:
            verdict, severity = "ACTIVE_SCAM_LIKELY", "HIGH"
            action = ("Terminate contact immediately. Do NOT transfer funds. "
                      "Call 1930 (national cybercrime helpline) and report at cybercrime.gov.in.")
        elif score >= 0.4:
            verdict, severity = "SUSPICIOUS", "MEDIUM"
            action = ("Independently verify the caller through official published numbers "
                      "before any action. No agency demands payment or secrecy over a call.")
        else:
            verdict, severity = "LOW_RISK", "LOW"
            action = "No digital-arrest indicators detected. Stay alert for payment or secrecy demands."

        return ScamAssessment(
            risk_score=score,
            verdict=verdict,
            severity=severity,
            stages=stages,
            indicators=indicators,
            spoof_flags=spoof_flags,
            claimed_agency=claimed_agency,
            script_family=self._script_family(text),
            recommended_action=action,
        )

    @staticmethod
    def _spoof_signatures(caller_number: str | None, claimed_agency: str | None,
                          channel: str) -> list[str]:
        flags: list[str] = []
        number = (caller_number or "").replace(" ", "").replace("-", "")
        if not number:
            if claimed_agency:
                flags.append("Caller ID withheld while claiming government authority")
            return flags
        if any(number.startswith(p) for p in _FOREIGN_PREFIXES):
            flags.append(f"Foreign origin number ({number[:4]}…) claiming Indian jurisdiction")
        if claimed_agency and channel in ("WHATSAPP", "VIDEO") :
            flags.append(f"{claimed_agency} claimed over {channel} — agencies never use WhatsApp/video calls")
        if claimed_agency and _INDIA_MOBILE_RE.match(number):
            flags.append("Personal mobile number presented as a government line")
        if _TELEMARKETING_RE.match(number):
            flags.append("140-series telemarketing prefix masquerading as official call")
        return flags

    @staticmethod
    def _script_family(text: str) -> str | None:
        best, best_hits = None, 0
        for family, patterns in SCRIPT_FAMILIES.items():
            hits = sum(1 for p in patterns if re.search(p, text))
            if hits > best_hits:
                best, best_hits = family, hits
        return best if best_hits >= 2 else None

    # ------------------------------------------------------------------
    @staticmethod
    def mha_alert_package(session_id: str, assessment_dict: dict, *,
                          caller_number: str | None, channel: str,
                          created_at: datetime | None = None) -> dict:
        """Structured alert in the shape an MHA/I4C intake expects.

        Deterministic template — regenerating for the same session yields the
        same package (modulo generated_at), so it can be re-issued for audit.
        """
        now = (created_at or datetime.now(timezone.utc)).isoformat()
        return {
            "alert_type": "DIGITAL_ARREST_SCAM_SESSION",
            "reference": f"NETRA-DA-{session_id[:8].upper()}",
            "generated_at": now,
            "session_id": session_id,
            "caller_number": caller_number,
            "channel": channel,
            "risk_score": assessment_dict["risk_score"],
            "verdict": assessment_dict["verdict"],
            "claimed_agency": assessment_dict.get("claimed_agency"),
            "script_family": assessment_dict.get("script_family"),
            "indicators": assessment_dict.get("indicators", []),
            "spoofing_signatures": assessment_dict.get("spoof_flags", []),
            "recommended_dissemination": [
                "Telecom provider: flag/throttle originating number",
                "Victim bank: hold outbound RTGS/NEFT pending verification",
                "I4C: correlate number against national complaint corpus",
            ],
            "citizen_guidance": "Call 1930 · report at cybercrime.gov.in",
            "disclaimer": "Generated from synthetic data by Project Netra. Screening intelligence, not evidence.",
        }
