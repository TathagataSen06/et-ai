from app.config import get_settings


def test_login_success(client):
    r = client.post("/api/v1/auth/login",
                    data={"username": "commander", "password": "netra-demo"})
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["role"] == "COMMAND"
    assert body["expires_in"] > 0


def test_login_wrong_password(client):
    r = client.post("/api/v1/auth/login",
                    data={"username": "commander", "password": "wrong"})
    assert r.status_code == 401


def test_me_endpoint(client, auth_headers):
    r = client.get("/api/v1/auth/me", headers=auth_headers)
    assert r.status_code == 200
    assert r.json() == {"username": "commander", "role": "COMMAND"}


def test_protected_endpoints_require_auth(client):
    assert client.get("/api/v1/patrols/status").status_code == 401
    assert client.post("/api/v1/clusters/refresh").status_code == 401
    assert client.get("/api/v1/network/graph").status_code == 401
    assert client.post("/api/v1/reports/generate/x").status_code == 401
    assert client.get("/api/v1/citizen/reports").status_code == 401


def test_garbage_token_rejected(client):
    r = client.get("/api/v1/patrols/status",
                   headers={"Authorization": "Bearer not-a-token"})
    assert r.status_code == 401


def test_public_endpoints_stay_open(client):
    assert client.get("/api/v1/clusters/active").status_code == 200
    assert client.get("/api/v1/alerts/recent").status_code == 200
    assert client.get("/api/v1/heatmap/data").status_code == 200
    assert client.get("/health").status_code == 200


def test_rate_limit_returns_429(client):
    settings = get_settings()
    settings.rate_limit_enabled = True
    original_limit = settings.rate_limit_auth_per_minute
    settings.rate_limit_auth_per_minute = 3
    try:
        statuses = [
            client.post("/api/v1/auth/login",
                        data={"username": "x", "password": "y"}).status_code
            for _ in range(5)
        ]
        assert 429 in statuses
    finally:
        settings.rate_limit_enabled = False
        settings.rate_limit_auth_per_minute = original_limit


def test_metrics_endpoint(client):
    import re

    client.get("/health")
    r = client.get("/metrics", follow_redirects=False)
    # Must answer directly (no 307 to /metrics/) — scrapers may not follow.
    assert r.status_code == 200
    # And must contain real sample lines with non-zero counts, not just the
    # HELP/TYPE comments of an empty metric family.
    samples = re.findall(r'netra_http_requests_total\{[^}]*\} ([0-9.]+)', r.text)
    assert samples and sum(float(s) for s in samples) >= 1


def test_audit_log_written_for_mutations(client, db, auth_headers):
    from app.models.orm import AuditLog

    client.post("/api/v1/clusters/refresh", headers=auth_headers)
    rows = db.query(AuditLog).filter(AuditLog.path == "/api/v1/clusters/refresh").all()
    assert len(rows) >= 1
    assert rows[-1].username == "commander"
    assert rows[-1].method == "POST"
