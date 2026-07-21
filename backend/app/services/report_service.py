"""Intelligence report generation (spec: LLM report service).

Backend selection, in order:
1. Groq (OpenAI-compatible endpoint, serves Llama) when NETRA_GROQ_API_KEY is set
2. Ollama when NETRA_OLLAMA_URL is set
3. Deterministic template — always available, zero cost, offline

The LLM paths receive the same structured facts the template renders, so the
report content is grounded either way.
"""
import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.orm import HotspotCluster, PatrolRoute, Seizure
from app.services.geospatial_service import haversine_km

logger = logging.getLogger(__name__)


class ReportService:
    def __init__(self, db: Session):
        self.db = db
        self.settings = get_settings()

    def generate_cluster_report(self, cluster_id: str) -> dict | None:
        cluster = self.db.get(HotspotCluster, cluster_id)
        if cluster is None:
            return None
        facts = self._collect_facts(cluster)

        generator = "TEMPLATE"
        markdown = self._template_report(facts)
        if self.settings.groq_api_key:
            llm_report = self._groq_report(facts)
            if llm_report:
                markdown, generator = llm_report, "GROQ"
        elif self.settings.ollama_url:
            llm_report = self._ollama_report(facts)
            if llm_report:
                markdown, generator = llm_report, "OLLAMA"

        return {
            "cluster_id": cluster_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "generator": generator,
            "markdown": markdown,
        }

    def _collect_facts(self, cluster: HotspotCluster) -> dict:
        cutoff = datetime.now(timezone.utc) - timedelta(days=90)
        seizures = [
            s for s in self.db.scalars(select(Seizure).where(Seizure.seizure_date > cutoff)).all()
            if haversine_km(cluster.center_lat, cluster.center_lon, s.lat, s.lon)
            <= max(cluster.radius_km, 2.0)
        ]
        seizures.sort(key=lambda s: s.seizure_date, reverse=True)
        patrols = self.db.scalars(
            select(PatrolRoute).where(PatrolRoute.hotspot_id == cluster.id)
        ).all()

        by_type: dict[str, int] = {}
        by_agency: dict[str, int] = {}
        for s in seizures:
            if s.location_type:
                by_type[s.location_type] = by_type.get(s.location_type, 0) + 1
            if s.seized_by_agency:
                by_agency[s.seized_by_agency] = by_agency.get(s.seized_by_agency, 0) + 1

        return {
            "sector": cluster.id[:4].upper(),
            "center": f"{cluster.center_lat:.4f}, {cluster.center_lon:.4f}",
            "risk_level": cluster.risk_level,
            "risk_score": cluster.risk_score,
            "patrol_priority": cluster.patrol_priority,
            "radius_km": cluster.radius_km,
            "seizure_count": cluster.seizure_count,
            "total_notes": cluster.total_notes,
            "avg_confidence": cluster.avg_confidence,
            "last_seizure": cluster.last_seizure_date.isoformat()
            if cluster.last_seizure_date else None,
            "location_types": by_type,
            "agencies": by_agency,
            "recent_seizures": [
                {
                    "date": s.seizure_date.strftime("%Y-%m-%d"),
                    "denomination": s.denomination,
                    "quantity": s.quantity,
                    "location_type": s.location_type,
                }
                for s in seizures[:10]
            ],
            "patrols": [
                {"officer": p.officer_name, "status": p.status} for p in patrols
            ],
        }

    @staticmethod
    def _template_report(f: dict) -> str:
        top_type = max(f["location_types"], key=f["location_types"].get) if f["location_types"] else "unknown"
        lines = [
            f"# Intelligence Report — Sector {f['sector']}",
            "",
            f"**Risk level:** {f['risk_level']} (score {f['risk_score']:.2f}) · "
            f"**Patrol priority:** P{f['patrol_priority']}",
            f"**Center:** {f['center']} · **Radius:** {f['radius_km']:.1f} km",
            "",
            "## Summary",
            f"{f['seizure_count']} seizures totalling {f['total_notes']:,} counterfeit notes "
            f"were recorded in this cluster over the last 90 days "
            f"(mean detection confidence {f['avg_confidence']:.0%}). "
            f"Activity concentrates around **{top_type}** locations."
            + (f" Most recent seizure: {f['last_seizure'][:10]}." if f["last_seizure"] else ""),
            "",
            "## Seizure breakdown",
        ]
        for loc_type, count in sorted(f["location_types"].items(), key=lambda x: -x[1]):
            lines.append(f"- {loc_type}: {count} seizures")
        if f["agencies"]:
            lines.append("")
            lines.append("## Reporting agencies")
            for agency, count in sorted(f["agencies"].items(), key=lambda x: -x[1]):
                lines.append(f"- {agency}: {count}")
        lines += [
            "",
            "## Recent seizures",
            "| Date | Denomination | Notes | Location |",
            "|---|---|---|---|",
        ]
        for s in f["recent_seizures"]:
            lines.append(
                f"| {s['date']} | ₹{s['denomination']} | {s['quantity']} | {s['location_type'] or '—'} |"
            )
        lines += [
            "",
            "## Patrol status",
        ]
        if f["patrols"]:
            for p in f["patrols"]:
                lines.append(f"- {p['officer']} — {p['status']}")
        else:
            lines.append("- No patrol currently assigned")
        lines += [
            "",
            "## Recommended actions",
            f"1. {'Maintain' if f['patrols'] else 'Assign'} patrol coverage at priority P{f['patrol_priority']}.",
            f"2. Increase scrutiny at {top_type} locations inside the {f['radius_km']:.1f} km radius.",
            "3. Cross-reference linked dealers against the fraud network graph for "
            "distributor-level action.",
            "",
            "---",
            "*Generated from synthetic data by Project Netra. Screening intelligence, "
            "not evidence.*",
        ]
        return "\n".join(lines)

    _ANALYST_SYSTEM_PROMPT = (
        "You are an intelligence analyst for a counterfeit-currency task force. "
        "Write a concise markdown intelligence report from the structured facts "
        "provided. Use only the given facts — never invent numbers, names, or "
        "events. The Summary must quote the seizure_count and total_notes values "
        "as exact integers (e.g. '7,555 notes', never 'over 7,500' or '~7.5k'). "
        "Sections: Summary, Pattern Analysis, Risk Assessment, "
        "Recommended Actions. End with a note that the data is synthetic."
    )

    def _groq_report(self, facts: dict) -> str | None:
        """Groq's OpenAI-compatible chat endpoint (serves Llama models)."""
        try:
            response = httpx.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.settings.groq_api_key}"},
                json={
                    "model": self.settings.groq_model,
                    "max_tokens": 2000,
                    "messages": [
                        {"role": "system", "content": self._ANALYST_SYSTEM_PROMPT},
                        {"role": "user", "content": f"Cluster facts:\n{facts}"},
                    ],
                },
                timeout=45.0,
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
        except Exception:
            logger.exception("Groq report generation failed; falling back to template")
            return None

    def _ollama_report(self, facts: dict) -> str | None:
        try:
            response = httpx.post(
                f"{self.settings.ollama_url.rstrip('/')}/api/generate",
                json={
                    "model": self.settings.ollama_model,
                    "prompt": (
                        "Write a concise markdown intelligence report for a counterfeit-"
                        "currency task force from these facts (do not invent data; quote "
                        "seizure_count and total_notes as exact integers in the summary):"
                        f"\n{facts}"
                    ),
                    "stream": False,
                },
                timeout=60.0,
            )
            response.raise_for_status()
            return response.json().get("response")
        except Exception:
            logger.exception("Ollama report generation failed; falling back to template")
            return None
