"""Celery app + scheduled tasks (spec: Celery task queue).

Used by the docker-compose deployment (worker + beat services with a Redis
broker). Single-node local dev keeps the asyncio loop in main.py instead —
both paths call the same GeospatialIntelligence service.

Run:  celery -A app.tasks worker --loglevel=info
      celery -A app.tasks beat --loglevel=info
"""
from celery import Celery

from app.config import get_settings

settings = get_settings()

celery_app = Celery("netra", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    beat_schedule={
        "refresh-hotspots": {
            "task": "app.tasks.refresh_hotspots",
            "schedule": settings.cluster_refresh_minutes * 60.0,
        },
    },
)


@celery_app.task(name="app.tasks.refresh_hotspots")
def refresh_hotspots() -> int:
    from app.database import SessionLocal, init_db
    from app.services.geospatial_service import GeospatialIntelligence

    init_db()
    db = SessionLocal()
    try:
        clusters = GeospatialIntelligence(db).update_hotspots()
        return len(clusters)
    finally:
        db.close()


@celery_app.task(name="app.tasks.sync_network_to_neo4j")
def sync_network_to_neo4j() -> bool:
    from app.database import SessionLocal, init_db
    from app.services.network_service import NetworkIntelligence

    init_db()
    db = SessionLocal()
    try:
        return NetworkIntelligence(db).sync_to_neo4j()
    finally:
        db.close()
