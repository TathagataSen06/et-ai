from datetime import datetime, timezone

from app.models.orm import BankAccount, Dealer, Distributor, Seizure


def _seed_network(db):
    db.add(Distributor(id="dist-1", name="Distributor One", primary_city="Mumbai",
                       lat=19.0, lon=72.8, operation_scale="REGIONAL"))
    db.add(Dealer(id="deal-1", distributor_id="dist-1", name="Dealer A", city="Mumbai",
                  lat=19.05, lon=72.85, operation_type="ATM",
                  estimated_monthly_volume=50_000))
    db.add(Dealer(id="deal-2", distributor_id="dist-1", name="Dealer B", city="Pune",
                  lat=18.52, lon=73.85, operation_type="Shop",
                  estimated_monthly_volume=20_000))
    # Suspicious: high velocity
    db.add(BankAccount(id="acc-1", dealer_id="deal-1", bank_name="HDFC",
                       account_number="123456789012", ifsc_code="HDFC0123456",
                       total_inflow_inr=3_000_000, velocity_per_day=9, is_verified=True))
    # Clean: low velocity, verified
    db.add(BankAccount(id="acc-2", dealer_id="deal-2", bank_name="SBI",
                       account_number="999456789012", ifsc_code="SBIN0123456",
                       total_inflow_inr=600_000, velocity_per_day=2, is_verified=True))
    db.add(Seizure(seizure_date=datetime.now(timezone.utc), lat=19.05, lon=72.85,
                   denomination="500", quantity=200, linked_dealer_id="deal-1",
                   counterfeit_confidence=0.9))
    db.commit()


def test_graph_structure(client, db, auth_headers):
    _seed_network(db)
    graph = client.get("/api/v1/network/graph", headers=auth_headers).json()

    types = {n["type"] for n in graph["nodes"]}
    assert types == {"distributor", "dealer", "account"}
    assert graph["stats"] == {
        "distributors": 1, "dealers": 2, "accounts": 2,
        "linked_seizures": 1, "suspicious_accounts": 1,
    }
    edge_types = {e["type"] for e in graph["edges"]}
    assert edge_types == {"DISTRIBUTES_TO", "OWNS"}

    dealer_a = next(n for n in graph["nodes"] if n["id"] == "deal-1")
    assert dealer_a["seizure_count"] == 1
    assert dealer_a["notes_seized"] == 200


def test_suspicious_accounts(client, db, auth_headers):
    _seed_network(db)
    flagged = client.get("/api/v1/network/suspicious-accounts", headers=auth_headers).json()
    assert len(flagged) == 1
    assert flagged[0]["account_id"] == "acc-1"
    assert "high transfer velocity" in flagged[0]["reasons"][0]
    assert flagged[0]["dealer"]["name"] == "Dealer A"


def test_dealer_network(client, db, auth_headers):
    _seed_network(db)
    detail = client.get("/api/v1/network/dealer/deal-1", headers=auth_headers).json()
    assert detail["dealer"]["name"] == "Dealer A"
    assert detail["distributor"]["name"] == "Distributor One"
    assert [s["name"] for s in detail["sibling_dealers"]] == ["Dealer B"]
    assert len(detail["accounts"]) == 1
    assert len(detail["seizures"]) == 1

    assert client.get(
        "/api/v1/network/dealer/nope", headers=auth_headers
    ).status_code == 404


def test_neo4j_sync_noop_without_config(client, db, auth_headers):
    _seed_network(db)
    r = client.post("/api/v1/network/sync-neo4j", headers=auth_headers).json()
    assert r["synced"] is False
