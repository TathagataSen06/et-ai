"""Ingest generated synthetic data into the database and compute hotspots.

Run from the backend/ directory:  python scripts/ingest_data.py
"""
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete  # noqa: E402

from app.database import SessionLocal, init_db  # noqa: E402
from app.models.orm import (  # noqa: E402
    AnomalyEvent,
    BankAccount,
    CallRecord,
    CitizenReport,
    Dealer,
    DeviceFingerprint,
    Distributor,
    HotspotCluster,
    ScamSession,
    ScanRecord,
    Seizure,
)
from app.services.campaign_service import CampaignIntelligence  # noqa: E402
from app.services.geospatial_service import GeospatialIntelligence  # noqa: E402
from app.services.network_service import NetworkIntelligence  # noqa: E402
from app.services.scam_detection_service import ScamDetector  # noqa: E402

DATA_DIR = Path(__file__).parent / "data"


def main() -> None:
    network_path = DATA_DIR / "synthetic_network.json"
    scans_path = DATA_DIR / "synthetic_scans.json"
    if not network_path.exists():
        raise SystemExit("Run synthetic_data_generator.py first (no data files found)")

    network = json.loads(network_path.read_text())
    scans = json.loads(scans_path.read_text()) if scans_path.exists() else []

    init_db()
    db = SessionLocal()
    try:
        # Idempotent re-seed: clear derived + seeded tables (children before parents).
        for table in (AnomalyEvent, HotspotCluster, Seizure, ScanRecord,
                      CallRecord, ScamSession, DeviceFingerprint,
                      BankAccount, Dealer, Distributor):
            db.execute(delete(table))

        for d in network["distributors"]:
            db.add(Distributor(
                id=d["distributor_id"],
                name=d["name"],
                phone=d["phone"],
                primary_city=d["primary_city"],
                lat=d["coordinates"][0],
                lon=d["coordinates"][1],
                operation_scale=d["operation_scale"],
            ))

        for d in network["dealers"]:
            db.add(Dealer(
                id=d["dealer_id"],
                distributor_id=d["distributor_id"],
                name=d["name"],
                phone=d["phone"],
                city=d["city"],
                lat=d["coordinates"][0],
                lon=d["coordinates"][1],
                operation_type=d["operation_type"],
                estimated_monthly_volume=d["estimated_monthly_volume"],
            ))

        for a in network["accounts"]:
            db.add(BankAccount(
                id=a["account_id"],
                dealer_id=a["dealer_id"],
                bank_name=a["bank_name"],
                account_number=a["account_number"],
                ifsc_code=a["ifsc_code"],
                total_inflow_inr=a["total_inflow_inr"],
                velocity_per_day=a["velocity_per_day"],
                is_verified=a["is_verified"],
            ))

        for s in network["seizures"]:
            db.add(Seizure(
                id=s["seizure_id"],
                seizure_date=datetime.fromisoformat(s["seizure_date"]),
                lat=s["lat"],
                lon=s["lon"],
                denomination=s["denomination"],
                quantity=s["quantity"],
                location_type=s["location_type"],
                linked_dealer_id=s["linked_dealer_id"],
                counterfeit_confidence=s["counterfeit_confidence"],
                seized_by_agency=s["seized_by_agency"],
            ))

        for s in scans:
            db.add(ScanRecord(
                id=s["scan_id"],
                user_type=s["user_type"],
                lat=s["lat"],
                lon=s["lon"],
                counterfeit_score=s["counterfeit_score"],
                denomination=s["denomination"],
                recommendation=s["recommendation"],
                features={},
                created_at=datetime.fromisoformat(s["timestamp"]),
            ))
        # Scam campaigns: classify every synthetic session with the REAL
        # detector so stored verdicts are genuine classifier output.
        scam = network.get("scam", {})
        detector = ScamDetector()
        device_seen: dict[tuple[str, str], DeviceFingerprint] = {}
        for s in scam.get("sessions", []):
            assessment = detector.assess(
                s["transcript"],
                caller_number=s["caller_number"],
                channel=s["channel"],
                duration_minutes=s["duration_minutes"],
            )
            created = datetime.fromisoformat(s["created_at"])
            session = ScamSession(
                caller_number=s["caller_number"],
                victim_contact=s["victim_contact"],
                channel=s["channel"],
                claimed_agency=assessment.claimed_agency,
                transcript=s["transcript"],
                duration_minutes=s["duration_minutes"],
                device_hash=s["device_hash"],
                mule_account_id=s["mule_account_id"],
                risk_score=assessment.risk_score,
                verdict=assessment.verdict,
                script_family=assessment.script_family,
                stages=assessment.stages,
                indicators=assessment.indicators,
                spoof_flags=assessment.spoof_flags,
                alerted=assessment.severity == "HIGH",
                created_at=created,
            )
            db.add(session)
            db.flush()
            db.add(CallRecord(
                session_id=session.id,
                caller_number=s["caller_number"],
                victim_contact=s["victim_contact"],
                channel=s["channel"],
                duration_minutes=s["duration_minutes"],
                spoofed=bool(assessment.spoof_flags),
                started_at=created,
            ))
            key = (s["device_hash"], s["caller_number"])
            if key in device_seen:
                device_seen[key].sessions_count += 1
            else:
                fp = DeviceFingerprint(
                    device_hash=s["device_hash"], caller_number=s["caller_number"])
                device_seen[key] = fp
                db.add(fp)

        # Victim reports carry deterministic ids -> delete-then-insert keeps
        # re-seeds idempotent without touching real citizen submissions.
        report_ids = [r["report_id"] for r in scam.get("victim_reports", [])]
        if report_ids:
            db.execute(delete(CitizenReport).where(CitizenReport.id.in_(report_ids)))
        for r in scam.get("victim_reports", []):
            db.add(CitizenReport(
                id=r["report_id"],
                channel=r["channel"],
                lat=r["lat"],
                lon=r["lon"],
                description=r["description"],
                created_at=datetime.fromisoformat(r["created_at"]),
            ))
        db.commit()

        clusters = GeospatialIntelligence(db).update_hotspots()
        NetworkIntelligence(db).sync_to_neo4j()  # no-op unless NETRA_NEO4J_URI is set
        campaigns = CampaignIntelligence(db).campaigns()
        print(f"Ingested {len(network['seizures'])} seizures, {len(scans)} scans, "
              f"{len(network['dealers'])} dealers, {len(network['accounts'])} accounts")
        print(f"Scam intel: {len(scam.get('sessions', []))} sessions -> "
              f"{len(campaigns)} campaigns, {len(report_ids)} victim reports")
        print(f"Detected {len(clusters)} hotspot clusters:")
        for c in clusters:
            print(f"  [{c.risk_level:8s}] P{c.patrol_priority} "
                  f"({c.center_lat:.4f}, {c.center_lon:.4f}) "
                  f"{c.seizure_count} seizures, {c.total_notes} notes, risk={c.risk_score:.2f}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
