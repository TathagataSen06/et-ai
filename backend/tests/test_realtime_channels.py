"""Live call streaming, multi-turn triage, IVR flow, and PDF evidence export."""
from app.services.conversation_service import engine as conversation
from app.services.live_call_service import tracker

# Chunks in the order a digital-arrest call actually unfolds. The early ones
# are innocuous; the alert must not fire until the script turns.
CALL_CHUNKS = [
    "Good morning sir, am I speaking with the account holder?",
    "I am calling from CBI headquarters Delhi, officer badge 4471.",
    "A case has been registered against your Aadhaar for money laundering.",
    "This is a digital arrest. Stay on the video call, do not tell your family.",
    "Transfer your savings by RTGS to the safe custody account for verification.",
]


def _reset():
    for c in tracker.active():
        tracker.close(c.call_id)


# ---------------------------------------------------------------------------
# Live telecom stream
# ---------------------------------------------------------------------------

def test_stream_alerts_mid_call_before_payment_demand(client, auth_headers):
    _reset()
    fired_at = None
    for i, chunk in enumerate(CALL_CHUNKS, start=1):
        r = client.post("/api/v1/scam/stream", json={
            "call_id": "call-live-1", "chunk": chunk,
            "caller_number": "+92 3012345678", "channel": "VIDEO",
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["chunks_received"] == i
        if body["alert_fired_now"]:
            fired_at = i
            assert body["mha_alert"] is not None
            break

    assert fired_at is not None, "call never escalated"
    # Must fire by the isolation line (4) — i.e. before the transfer demand (5).
    assert fired_at <= 4, f"alerted too late, at chunk {fired_at}"

    active = client.get("/api/v1/scam/stream/active", headers=auth_headers).json()
    assert any(c["call_id"] == "call-live-1" and c["alerted"] for c in active)


def test_stream_alerts_only_once_and_persists_session(client, auth_headers):
    _reset()
    alerts = 0
    for chunk in CALL_CHUNKS:
        body = client.post("/api/v1/scam/stream", json={
            "call_id": "call-live-2", "chunk": chunk,
            "caller_number": "+92 3011111111", "channel": "VOICE",
        }).json()
        alerts += int(body["alert_fired_now"])
    assert alerts == 1, "alert should latch, not repeat every chunk"

    sessions = client.get("/api/v1/scam/sessions", headers=auth_headers).json()
    assert any(s["caller_number"] == "+92 3011111111" and s["alerted"] for s in sessions)


def test_benign_call_stream_never_alerts(client):
    _reset()
    for chunk in ["Hello, this is Dr Iyer's clinic.",
                  "Your appointment is confirmed for Monday at six.",
                  "Please bring your previous reports. Thank you."]:
        body = client.post("/api/v1/scam/stream", json={
            "call_id": "call-benign", "chunk": chunk}).json()
        assert body["alert_fired_now"] is False
    assert body["verdict"] == "LOW_RISK"


def test_stream_final_closes_the_call(client, auth_headers):
    _reset()
    client.post("/api/v1/scam/stream", json={"call_id": "call-fin", "chunk": "hello there"})
    body = client.post("/api/v1/scam/stream", json={
        "call_id": "call-fin", "chunk": "goodbye", "final": True}).json()
    assert body["final"] is True
    active = client.get("/api/v1/scam/stream/active", headers=auth_headers).json()
    assert all(c["call_id"] != "call-fin" for c in active)


def test_stream_active_requires_auth(client):
    assert client.get("/api/v1/scam/stream/active").status_code == 401


# ---------------------------------------------------------------------------
# Multi-turn conversational triage
# ---------------------------------------------------------------------------

def test_conversation_asks_followups_then_concludes():
    conversation.reset("+911111111111")
    first = conversation.handle("+911111111111",
                                "someone called saying he is CBI officer and my aadhaar "
                                "is used in money laundering case")
    assert first["done"] is False
    assert first["questions_remaining"] >= 1

    result = first
    guard = 0
    while not result["done"] and guard < 8:
        result = conversation.handle("+911111111111", "yes")
        guard += 1
    assert result["done"] is True
    assert result["verdict"] == "HIGH_RISK"


def test_conversation_no_answers_de_escalate():
    conversation.reset("+912222222222")
    conversation.handle("+912222222222", "I got a message about my KYC being expired")
    result = None
    for _ in range(8):
        result = conversation.handle("+912222222222", "no")
        if result["done"]:
            break
    assert result["done"] is True
    assert result["verdict"] in ("LIKELY_SAFE", "SUSPICIOUS")


def test_conversation_reprompts_on_unclear_answer():
    conversation.reset("+913333333333")
    conversation.handle("+913333333333", "caller asked me to share the otp urgently")
    reply = conversation.handle("+913333333333", "maybe not sure")
    assert reply["done"] is False
    assert reply["questions_remaining"] >= 1


def test_conversation_keeps_language_across_turns():
    conversation.reset("+914444444444")
    first = conversation.handle("+914444444444",
                                "किसी ने कॉल करके कहा कि मेरा खाता बंद हो जाएगा और OTP मांगा")
    assert first["lang"] == "hi"
    second = conversation.handle("+914444444444", "haan")
    assert second["lang"] == "hi"


def test_whatsapp_help_opens_conversation_then_advances(client):
    r = client.post("/api/v1/citizen/webhooks/whatsapp",
                    data={"From": "whatsapp:+919000000001", "Body": "HELP"})
    body = r.json()
    assert body["status"] == "conversation"
    assert body["done"] is False

    r2 = client.post("/api/v1/citizen/webhooks/whatsapp", data={
        "From": "whatsapp:+919000000001",
        "Body": "a man said he is from CBI, digital arrest, do not tell anyone, "
                "transfer money to safe account"})
    assert r2.json()["status"] == "conversation"
    assert r2.json()["questions_remaining"] >= 1


def test_whatsapp_plain_report_still_works_without_conversation(client, auth_headers):
    r = client.post("/api/v1/citizen/webhooks/whatsapp", data={
        "From": "whatsapp:+919000000002",
        "Body": "19.0821,72.8416 Shopkeeper gave me two fake 500 notes"})
    assert r.json()["status"] == "received"


# ---------------------------------------------------------------------------
# IVR
# ---------------------------------------------------------------------------

def test_ivr_full_flow_reaches_verdict(client):
    r = client.post("/api/v1/shield/ivr", json={"call_id": "ivr-1", "step": "LANGUAGE"})
    assert r.json()["step"] == "LANGUAGE"

    r = client.post("/api/v1/shield/ivr",
                    json={"call_id": "ivr-1", "step": "LANGUAGE", "digits": "2"})
    body = r.json()
    assert body["step"] == "CATEGORY" and body["lang"] == "hi"

    r = client.post("/api/v1/shield/ivr",
                    json={"call_id": "ivr-1", "step": "CATEGORY", "digits": "1"})
    body = r.json()
    assert body["step"] in ("FOLLOWUP", "DONE")

    guard = 0
    while not body.get("terminal") and guard < 8:
        body = client.post("/api/v1/shield/ivr", json={
            "call_id": "ivr-1", "step": "FOLLOWUP", "digits": "1"}).json()
        guard += 1
    assert body["terminal"] is True
    assert body["verdict"] == "HIGH_RISK"
    assert "1930" in body["prompt"]


def test_ivr_invalid_digit_reprompts(client):
    body = client.post("/api/v1/shield/ivr", json={
        "call_id": "ivr-2", "step": "LANGUAGE", "digits": "0"}).json()
    assert body["step"] == "LANGUAGE" and body["terminal"] is False


def test_ivr_prompts_are_speakable_length(client):
    body = client.post("/api/v1/shield/ivr",
                       json={"call_id": "ivr-3", "step": "LANGUAGE", "digits": "1"}).json()
    assert len(body["prompt"]) <= 400


# ---------------------------------------------------------------------------
# PDF evidence export
# ---------------------------------------------------------------------------

FULL_SCRIPT = (
    "I am calling from CBI headquarters. A case has been registered against your "
    "Aadhaar for money laundering. This is a digital arrest, stay on the video call "
    "and do not tell your family. Transfer your savings by RTGS to the safe custody "
    "account as a refundable security deposit."
)


def test_evidence_pdf_downloads(client, auth_headers):
    for number in ("+92 3055550001", "+92 3055550002"):
        client.post("/api/v1/scam/analyze-session", json={
            "transcript": FULL_SCRIPT, "caller_number": number,
            "channel": "VOICE", "device_hash": "d" * 32})
    campaigns = client.get("/api/v1/network/campaigns", headers=auth_headers).json()
    assert campaigns

    r = client.post(f"/api/v1/network/campaigns/{campaigns[0]['campaign_id']}/package.pdf",
                    headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF-")
    assert len(r.content) > 2000
    assert "attachment" in r.headers.get("content-disposition", "")


def test_evidence_pdf_requires_auth(client):
    assert client.post("/api/v1/network/campaigns/abc/package.pdf").status_code == 401
