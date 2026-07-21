"""Fraud campaign intelligence: clustering scam sessions into operations.

Sessions are linked into campaigns when they share caller numbers or device
fingerprints (union-find over both signals) — the classic mule-ring signature
of one compound rotating SIMs across a handset pool. Campaign IDs are content-
derived (hash of member numbers), so recomputation is stable across requests
without a materialized table.

Evidence packages are canonical-JSON snapshots with a SHA-256 integrity hash
and explicit provenance, i.e. the shape a prosecutor can verify byte-for-byte.
"""
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.orm import (
    BankAccount,
    CallRecord,
    CitizenReport,
    Dealer,
    DeviceFingerprint,
    ScamSession,
)


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def _aware(dt: datetime) -> datetime:
    """SQLite returns naive datetimes; session-fresh rows are tz-aware. Normalize."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _risk_level(max_score: float) -> str:
    if max_score >= 0.7:
        return "HIGH"
    if max_score >= 0.4:
        return "MEDIUM"
    return "LOW"


class CampaignIntelligence:
    def __init__(self, db: Session):
        self.db = db

    def campaigns(self) -> list[dict]:
        sessions = self.db.scalars(select(ScamSession)).all()
        devices = self.db.scalars(select(DeviceFingerprint)).all()
        if not sessions:
            return []

        uf = _UnionFind()
        for s in sessions:
            if s.caller_number:
                uf.find(s.caller_number)
        by_device: dict[str, list[str]] = defaultdict(list)
        for d in devices:
            if d.caller_number:
                by_device[d.device_hash].append(d.caller_number)
        for s in sessions:
            if s.device_hash and s.caller_number:
                by_device[s.device_hash].append(s.caller_number)
        for numbers in by_device.values():
            for other in numbers[1:]:
                uf.union(numbers[0], other)

        groups: dict[str, list[ScamSession]] = defaultdict(list)
        for s in sessions:
            key = uf.find(s.caller_number) if s.caller_number else f"session:{s.id}"
            groups[key].append(s)

        reports = self.db.scalars(select(CitizenReport)).all()
        out = []
        for members in groups.values():
            out.append(self._summarize(members, devices, reports))
        out.sort(key=lambda c: (-c["max_risk_score"], -c["session_count"]))
        return out

    def _summarize(self, members: list[ScamSession],
                   devices: list[DeviceFingerprint],
                   reports: list[CitizenReport]) -> dict:
        numbers = sorted({s.caller_number for s in members if s.caller_number})
        campaign_id = hashlib.sha1("|".join(numbers or [members[0].id]).encode()).hexdigest()[:12]
        device_hashes = sorted(
            {s.device_hash for s in members if s.device_hash}
            | {d.device_hash for d in devices if d.caller_number in numbers}
        )
        families = Counter(s.script_family for s in members if s.script_family)
        family = families.most_common(1)[0][0] if families else None

        linked_reports = [
            r for r in reports
            if any(n and n in (r.description or "") for n in numbers)
        ]
        account_ids = sorted({s.mule_account_id for s in members if s.mule_account_id})
        max_score = max((s.risk_score for s in members), default=0.0)
        dates = sorted(_aware(s.created_at) for s in members)

        return {
            "campaign_id": campaign_id,
            "label": (family or "UNATTRIBUTED").replace("_", " ").title(),
            "script_family": family,
            "risk_level": _risk_level(max_score),
            "max_risk_score": round(max_score, 3),
            "session_count": len(members),
            "caller_numbers": numbers,
            "device_hashes": device_hashes,
            "linked_report_count": len(linked_reports),
            "linked_report_ids": [r.id for r in linked_reports],
            "mule_account_ids": account_ids,
            "first_activity": dates[0].isoformat() if dates else None,
            "last_activity": dates[-1].isoformat() if dates else None,
            "session_ids": [s.id for s in members],
        }

    # ------------------------------------------------------------------
    def evidence_package(self, campaign_id: str, *, generated_by: str) -> dict | None:
        campaign = next((c for c in self.campaigns() if c["campaign_id"] == campaign_id), None)
        if campaign is None:
            return None

        calls = self.db.scalars(
            select(CallRecord)
            .where(CallRecord.caller_number.in_(campaign["caller_numbers"] or [""]))
            .order_by(CallRecord.started_at)
        ).all()
        accounts = []
        for aid in campaign["mule_account_ids"]:
            a = self.db.get(BankAccount, aid)
            if a is None:
                continue
            dealer = self.db.get(Dealer, a.dealer_id) if a.dealer_id else None
            accounts.append({
                "account_id": a.id,
                "bank": a.bank_name,
                "ifsc": a.ifsc_code,
                "account_number_masked": f"····{(a.account_number or '')[-4:]}",
                "inflow_inr": a.total_inflow_inr,
                "velocity_per_day": a.velocity_per_day,
                "kyc_verified": a.is_verified,
                "linked_dealer": {"id": dealer.id, "name": dealer.name, "city": dealer.city}
                if dealer else None,
            })
        sessions = [self.db.get(ScamSession, sid) for sid in campaign["session_ids"]]
        reports = [self.db.get(CitizenReport, rid) for rid in campaign["linked_report_ids"]]

        payload = {
            "package_type": "FRAUD_CAMPAIGN_EVIDENCE",
            "reference": f"NETRA-EV-{campaign_id.upper()}",
            "campaign": {k: v for k, v in campaign.items() if k != "session_ids"},
            "call_timeline": [
                {
                    "caller_number": c.caller_number,
                    "victim_contact": c.victim_contact,
                    "channel": c.channel,
                    "duration_minutes": c.duration_minutes,
                    "spoofed": c.spoofed,
                    "started_at": c.started_at.isoformat(),
                }
                for c in calls
            ],
            "sessions": [
                {
                    "session_id": s.id,
                    "verdict": s.verdict,
                    "risk_score": s.risk_score,
                    "claimed_agency": s.claimed_agency,
                    "indicators": s.indicators,
                    "spoof_flags": s.spoof_flags,
                    "created_at": s.created_at.isoformat(),
                }
                for s in sessions if s is not None
            ],
            "victim_reports": [
                {
                    "report_id": r.id,
                    "channel": r.channel,
                    "excerpt": (r.description or "")[:200],
                    "created_at": r.created_at.isoformat(),
                }
                for r in reports if r is not None
            ],
            "mule_accounts": accounts,
            "provenance": {
                "generated_by": generated_by,
                "system": "Project Netra",
                "source_tables": ["scam_sessions", "call_records", "device_fingerprints",
                                  "citizen_reports", "bank_accounts"],
                "methodology": "Union-find clustering over shared caller numbers and device fingerprints; "
                               "rule-based session classification (deterministic, reproducible).",
            },
            "disclaimer": "Generated from synthetic data by Project Netra. Screening intelligence, not evidence.",
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        payload["integrity_sha256"] = hashlib.sha256(canonical.encode()).hexdigest()
        return payload
