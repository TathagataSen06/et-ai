"""Fraud network graph intelligence (spec Feature 5).

Primary store is SQL (Distributor -> Dealer -> BankAccount + seizure links) so the
graph works everywhere. When NETRA_NEO4J_URI is configured, `sync_to_neo4j`
mirrors the network into Neo4j Community with the spec's node/relationship model
(DISTRIBUTES_TO / OWNS / LINKED_TO) for Cypher-based analysis.
"""
import logging
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.orm import BankAccount, Dealer, Distributor, Seizure

logger = logging.getLogger(__name__)

SUSPICIOUS_VELOCITY_PER_DAY = 5
SUSPICIOUS_INFLOW_INR = 2_000_000


class NetworkIntelligence:
    def __init__(self, db: Session):
        self.db = db

    def graph(self) -> dict:
        """Nodes + edges for the dashboard's force-directed visualization."""
        distributors = self.db.scalars(select(Distributor)).all()
        dealers = self.db.scalars(select(Dealer)).all()
        accounts = self.db.scalars(select(BankAccount)).all()
        seizures = self.db.scalars(select(Seizure)).all()

        seizure_counts: dict[str, int] = defaultdict(int)
        seizure_notes: dict[str, int] = defaultdict(int)
        for s in seizures:
            if s.linked_dealer_id:
                seizure_counts[s.linked_dealer_id] += 1
                seizure_notes[s.linked_dealer_id] += s.quantity

        nodes = []
        edges = []

        for d in distributors:
            nodes.append({
                "id": d.id,
                "type": "distributor",
                "label": d.name,
                "city": d.primary_city,
                "scale": d.operation_scale,
            })

        for d in dealers:
            nodes.append({
                "id": d.id,
                "type": "dealer",
                "label": d.name,
                "city": d.city,
                "operation_type": d.operation_type,
                "monthly_volume": d.estimated_monthly_volume,
                "seizure_count": seizure_counts.get(d.id, 0),
                "notes_seized": seizure_notes.get(d.id, 0),
            })
            if d.distributor_id:
                edges.append({
                    "source": d.distributor_id,
                    "target": d.id,
                    "type": "DISTRIBUTES_TO",
                })

        for a in accounts:
            nodes.append({
                "id": a.id,
                "type": "account",
                "label": f"{a.bank_name} ····{(a.account_number or '')[-4:]}",
                "bank": a.bank_name,
                "inflow_inr": a.total_inflow_inr,
                "velocity_per_day": a.velocity_per_day,
                "is_verified": a.is_verified,
                "suspicious": self._is_suspicious(a),
            })
            if a.dealer_id:
                edges.append({"source": a.dealer_id, "target": a.id, "type": "OWNS"})

        return {
            "nodes": nodes,
            "edges": edges,
            "stats": {
                "distributors": len(distributors),
                "dealers": len(dealers),
                "accounts": len(accounts),
                "linked_seizures": sum(seizure_counts.values()),
                "suspicious_accounts": sum(1 for a in accounts if self._is_suspicious(a)),
            },
        }

    def dealer_network(self, dealer_id: str) -> dict | None:
        """Spec query 1: the distribution network around one dealer."""
        dealer = self.db.get(Dealer, dealer_id)
        if dealer is None:
            return None
        siblings = self.db.scalars(
            select(Dealer).where(Dealer.distributor_id == dealer.distributor_id)
        ).all() if dealer.distributor_id else [dealer]
        distributor = (
            self.db.get(Distributor, dealer.distributor_id) if dealer.distributor_id else None
        )
        accounts = self.db.scalars(
            select(BankAccount).where(BankAccount.dealer_id == dealer_id)
        ).all()
        seizures = self.db.scalars(
            select(Seizure).where(Seizure.linked_dealer_id == dealer_id)
        ).all()
        return {
            "dealer": {"id": dealer.id, "name": dealer.name, "city": dealer.city,
                       "operation_type": dealer.operation_type},
            "distributor": {"id": distributor.id, "name": distributor.name,
                            "scale": distributor.operation_scale} if distributor else None,
            "sibling_dealers": [
                {"id": s.id, "name": s.name, "city": s.city} for s in siblings if s.id != dealer_id
            ],
            "accounts": [
                {"id": a.id, "bank": a.bank_name, "inflow_inr": a.total_inflow_inr,
                 "suspicious": self._is_suspicious(a)} for a in accounts
            ],
            "seizures": [
                {"id": s.id, "date": s.seizure_date.isoformat(), "quantity": s.quantity,
                 "denomination": s.denomination} for s in seizures
            ],
        }

    def suspicious_accounts(self) -> list[dict]:
        """Spec query 3: accounts with anomalous transfer patterns."""
        accounts = self.db.scalars(select(BankAccount)).all()
        flagged = []
        for a in accounts:
            if not self._is_suspicious(a):
                continue
            dealer = self.db.get(Dealer, a.dealer_id) if a.dealer_id else None
            reasons = []
            if a.velocity_per_day > SUSPICIOUS_VELOCITY_PER_DAY:
                reasons.append(f"high transfer velocity ({a.velocity_per_day}/day)")
            if a.total_inflow_inr > SUSPICIOUS_INFLOW_INR and not a.is_verified:
                reasons.append(
                    f"unverified with ₹{a.total_inflow_inr:,} inflow"
                )
            flagged.append({
                "account_id": a.id,
                "bank": a.bank_name,
                "ifsc": a.ifsc_code,
                "inflow_inr": a.total_inflow_inr,
                "velocity_per_day": a.velocity_per_day,
                "is_verified": a.is_verified,
                "dealer": {"id": dealer.id, "name": dealer.name, "city": dealer.city}
                if dealer else None,
                "reasons": reasons,
            })
        flagged.sort(key=lambda x: x["inflow_inr"], reverse=True)
        return flagged

    @staticmethod
    def _is_suspicious(account: BankAccount) -> bool:
        return account.velocity_per_day > SUSPICIOUS_VELOCITY_PER_DAY or (
            account.total_inflow_inr > SUSPICIOUS_INFLOW_INR and not account.is_verified
        )

    def sync_to_neo4j(self) -> bool:
        """Mirror the relational network into Neo4j (no-op unless configured)."""
        settings = get_settings()
        if not settings.neo4j_uri:
            return False
        try:
            from neo4j import GraphDatabase
        except ImportError:
            logger.warning("neo4j driver not installed; skipping graph sync")
            return False

        graph = self.graph()
        driver = GraphDatabase.driver(
            settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
        )
        try:
            with driver.session() as session:
                session.run(
                    "CREATE CONSTRAINT unique_entity_id IF NOT EXISTS "
                    "FOR (n:Entity) REQUIRE n.entity_id IS UNIQUE"
                )
                for node in graph["nodes"]:
                    session.run(
                        "MERGE (n:Entity {entity_id: $id}) "
                        "SET n.type = $type, n.label = $label",
                        id=node["id"], type=node["type"], label=node["label"],
                    )
                for edge in graph["edges"]:
                    session.run(
                        "MATCH (a:Entity {entity_id: $src}), (b:Entity {entity_id: $dst}) "
                        f"MERGE (a)-[:{edge['type']}]->(b)",
                        src=edge["source"], dst=edge["target"],
                    )
            logger.info("Synced %d nodes / %d edges to Neo4j",
                        len(graph["nodes"]), len(graph["edges"]))
            return True
        finally:
            driver.close()
