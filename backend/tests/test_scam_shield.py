"""Digital-arrest detection, Citizen Fraud Shield, and campaign intelligence."""
import hashlib
import json

from app.services.fraud_shield_service import ADVISORIES, FraudShield
from app.services.scam_detection_service import ScamDetector

detector = ScamDetector()
shield = FraudShield()

FULL_SCRIPT = (
    "Hello, I am calling from CBI headquarters Delhi. A case has been registered "
    "against your Aadhaar for money laundering. An arrest warrant is issued — this "
    "is a digital arrest, stay on the video call and keep your camera on. Do not "
    "tell your family, this is strictly confidential. You are transferred to my "
    "senior officer for the Skype video statement. To verify your funds, transfer "
    "your savings by RTGS to the safe custody account, it is a refundable security deposit."
)


# ---------------------------------------------------------------------------
# Scam classifier
# ---------------------------------------------------------------------------

def test_full_digital_arrest_script_flagged_high():
    a = detector.assess(FULL_SCRIPT, caller_number="+92 3012345678",
                        channel="VIDEO", duration_minutes=120)
    assert a.verdict == "ACTIVE_SCAM_LIKELY"
    assert a.severity == "HIGH"
    assert a.risk_score >= 0.7
    assert a.claimed_agency and "CBI" in a.claimed_agency
    matched = [s["stage"] for s in a.stages if s["matched"]]
    assert {"AUTHORITY_CLAIM", "ACCUSATION", "ISOLATION", "PAYMENT_DEMAND"} <= set(matched)


def test_benign_call_low_risk():
    a = detector.assess(
        "Hi beta, this is mom. Dinner is at 8, pick up some curd on the way home. "
        "Also your cousin's wedding invite arrived today.")
    assert a.verdict == "LOW_RISK"
    assert a.risk_score < 0.2
    assert not a.spoof_flags


def test_partial_pressure_is_suspicious():
    # Partial script: authority + accusation + payment, but no isolation/escalation.
    a = detector.assess(
        "Sir I am calling from the bank head office. The Enforcement Directorate has "
        "flagged suspicious transactions found in your account. To keep it active you "
        "must transfer the balance for verification of your funds.",
        caller_number="+91 2261234567")  # landline-style CLI -> no spoof flag
    assert a.verdict == "SUSPICIOUS"
    assert 0.4 <= a.risk_score < 0.7


def test_plain_kyc_scam_is_shields_job_not_detectors():
    # A bare KYC-expiry scam has no digital-arrest anatomy -> detector stays low,
    # but the Fraud Shield catches it as KYC_EXPIRY.
    msg = ("Your KYC is expired and the account will be blocked today, "
           "click the link to update immediately.")
    assert detector.assess(msg).verdict == "LOW_RISK"
    a = shield.assess(msg)
    assert a.verdict in ("HIGH_RISK", "SUSPICIOUS")
    assert a.fraud_type in ("KYC_EXPIRY", "PHISHING_LINK")


def test_spoof_signatures():
    a = detector.assess("I am from the CBI cyber cell.", caller_number="+92 3001234567",
                        channel="WHATSAPP")
    flags = " ".join(a.spoof_flags)
    assert "Foreign origin" in flags
    assert "WHATSAPP" in flags


def test_withheld_number_with_agency_claim_flagged():
    a = detector.assess("This is the Enforcement Directorate calling about your account.")
    assert any("withheld" in f.lower() for f in a.spoof_flags)


def test_script_family_attribution():
    a = detector.assess(FULL_SCRIPT)
    assert a.script_family == "DIGITAL_ARREST_CBI"


def test_mha_alert_package_shape():
    a = detector.assess(FULL_SCRIPT, caller_number="+92 3012345678", channel="VIDEO")
    package = ScamDetector.mha_alert_package(
        "abc123def456", {
            "risk_score": a.risk_score, "verdict": a.verdict,
            "claimed_agency": a.claimed_agency, "script_family": a.script_family,
            "indicators": a.indicators, "spoof_flags": a.spoof_flags,
        }, caller_number="+92 3012345678", channel="VIDEO")
    assert package["alert_type"] == "DIGITAL_ARREST_SCAM_SESSION"
    assert package["reference"].startswith("NETRA-DA-")
    assert "synthetic" in package["disclaimer"].lower()


# ---------------------------------------------------------------------------
# Scam API
# ---------------------------------------------------------------------------

def test_analyze_session_persists_and_alerts(client, auth_headers):
    r = client.post("/api/v1/scam/analyze-session", json={
        "transcript": FULL_SCRIPT,
        "caller_number": "+92 3012345678",
        "channel": "VIDEO",
        "duration_minutes": 95,
        "device_hash": "f" * 32,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["verdict"] == "ACTIVE_SCAM_LIKELY"
    assert body["mha_alert"] is not None

    sessions = client.get("/api/v1/scam/sessions", headers=auth_headers).json()
    assert len(sessions) == 1 and sessions[0]["alerted"] is True

    alerts = client.get("/api/v1/alerts/recent").json()
    assert any(a["event_type"] == "SCAM_SESSION" for a in alerts)

    reissued = client.get(
        f"/api/v1/scam/sessions/{body['session_id']}/alert", headers=auth_headers).json()
    assert reissued["reference"] == body["mha_alert"]["reference"]


def test_scam_sessions_require_auth(client):
    assert client.get("/api/v1/scam/sessions").status_code == 401


# ---------------------------------------------------------------------------
# Citizen Fraud Shield
# ---------------------------------------------------------------------------

def test_shield_otp_theft_high_risk(client):
    r = client.post("/api/v1/shield/assess", json={
        "message": "Your parcel is on hold, please share the OTP you received to release it."})
    body = r.json()
    assert body["verdict"] in ("HIGH_RISK", "SUSPICIOUS")
    assert body["fraud_type"] == "OTP_THEFT"
    assert body["helpline"] == "1930"


def test_shield_benign_message_safe(client):
    r = client.post("/api/v1/shield/assess", json={
        "message": "Reminder: society maintenance meeting on Sunday at the clubhouse."})
    assert r.json()["verdict"] == "LIKELY_SAFE"


def test_shield_negated_otp_advisory_not_flagged(client):
    r = client.post("/api/v1/shield/assess", json={
        "message": "Security tip from your bank: never share your OTP or PIN with anyone. "
                   "Bank officials will never ask for it."})
    assert r.json()["verdict"] == "LIKELY_SAFE"


def test_shield_language_autodetect_hindi(client):
    r = client.post("/api/v1/shield/assess", json={
        "message": "आपका खाता बंद हो जाएगा, OTP भेजें और verification के लिए पैसे transfer करें"})
    body = r.json()
    assert body["lang"] == "hi"
    assert "1930" in body["actions"]


def test_shield_flags_hindi_scam_patterns(client):
    r = client.post("/api/v1/shield/assess", json={
        "message": "आपका बिजली बिल बकाया है, आज रात कनेक्शन कट जाएगा। तुरंत इस लिंक पर "
                   "क्लिक करके भुगतान करें और OTP शेयर करें"})
    body = r.json()
    assert body["verdict"] in ("HIGH_RISK", "SUSPICIOUS")
    assert body["fraud_type"] in ("OTP_THEFT", "PHISHING_LINK", "UTILITY_DISCONNECT")
    assert body["lang"] == "hi"


def test_shield_explicit_language_wins(client):
    r = client.post("/api/v1/shield/assess", json={
        "message": "share the otp to receive your refund", "lang": "ta"})
    assert r.json()["lang"] == "ta"


def test_shield_twelve_regional_languages_plus_english(client):
    langs = client.get("/api/v1/shield/languages").json()
    assert len(langs) == 13
    assert {l["code"] for l in langs} >= {"en", "hi", "bn", "ta", "te", "mr",
                                          "kn", "gu", "pa", "ml", "or", "as", "ur"}
    for code, pack in ADVISORIES.items():
        assert pack["high"] and pack["actions"], code


def test_shield_ivr_text_is_short(client):
    r = client.post("/api/v1/shield/assess", json={"message": FULL_SCRIPT})
    assert len(r.json()["ivr_text"]) <= 200


def test_whatsapp_check_prefix_triages_without_storing(client, auth_headers):
    r = client.post("/api/v1/citizen/webhooks/whatsapp", data={
        "From": "whatsapp:+919812345678",
        "Body": "CHECK: caller says he is CBI officer, my aadhaar used for money laundering, "
                "asking rtgs transfer to safe account",
    })
    body = r.json()
    assert body["status"] == "assessed"
    assert body["verdict"] in ("HIGH_RISK", "SUSPICIOUS")
    assert body["reply"]
    reports = client.get("/api/v1/citizen/reports", headers=auth_headers).json()
    assert reports == []  # triage must not create a report


def test_whatsapp_plain_message_still_creates_report(client, auth_headers):
    r = client.post("/api/v1/citizen/webhooks/whatsapp", data={
        "From": "whatsapp:+919812345678",
        "Body": "19.0821,72.8416 Shopkeeper gave me two fake 500 notes",
    })
    assert r.json()["status"] == "received"
    reports = client.get("/api/v1/citizen/reports", headers=auth_headers).json()
    assert len(reports) == 1


# ---------------------------------------------------------------------------
# Campaign clustering + evidence packages
# ---------------------------------------------------------------------------

def _seed_two_linked_sessions(client):
    """Two sessions, different numbers, same device -> one campaign."""
    for number in ("+92 3011111111", "+92 3022222222"):
        client.post("/api/v1/scam/analyze-session", json={
            "transcript": FULL_SCRIPT,
            "caller_number": number,
            "channel": "VOICE",
            "device_hash": "a" * 32,
        })


def test_shared_device_links_numbers_into_one_campaign(client, auth_headers):
    _seed_two_linked_sessions(client)
    campaigns = client.get("/api/v1/network/campaigns", headers=auth_headers).json()
    assert len(campaigns) == 1
    c = campaigns[0]
    assert c["session_count"] == 2
    assert set(c["caller_numbers"]) == {"+92 3011111111", "+92 3022222222"}
    assert c["risk_level"] == "HIGH"


def test_evidence_package_integrity_hash(client, auth_headers):
    _seed_two_linked_sessions(client)
    campaigns = client.get("/api/v1/network/campaigns", headers=auth_headers).json()
    r = client.post(
        f"/api/v1/network/campaigns/{campaigns[0]['campaign_id']}/package",
        headers=auth_headers)
    assert r.status_code == 200
    package = r.json()
    stated = package.pop("integrity_sha256")
    canonical = json.dumps(package, sort_keys=True, separators=(",", ":"), default=str)
    assert hashlib.sha256(canonical.encode()).hexdigest() == stated
    assert package["provenance"]["generated_by"] == "commander"
    assert len(package["call_timeline"]) == 2


def test_campaigns_require_auth(client):
    assert client.get("/api/v1/network/campaigns").status_code == 401


def test_graph_includes_phone_and_device_nodes(client, auth_headers):
    _seed_two_linked_sessions(client)
    graph = client.get("/api/v1/network/graph", headers=auth_headers).json()
    types = {n["type"] for n in graph["nodes"]}
    assert {"phone", "device"} <= types
    assert graph["stats"]["phones"] == 2
    assert graph["stats"]["devices"] == 1
    assert any(e["type"] == "OPERATES" for e in graph["edges"])
