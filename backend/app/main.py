"""Project Netra API entry point."""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.config import get_settings
from app.database import SessionLocal, init_db
from app.middleware import AuditMiddleware, MetricsMiddleware, RateLimitMiddleware
from app.routes import alerts, auth, citizen, clusters, network, patrols, reports, scanner
from app.services.geospatial_service import GeospatialIntelligence

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("netra")
settings = get_settings()


async def _periodic_hotspot_refresh() -> None:
    """Stand-in for the Celery beat job when running single-node."""
    interval = settings.cluster_refresh_minutes * 60
    while True:
        await asyncio.sleep(interval)
        try:
            db = SessionLocal()
            try:
                clusters_found = GeospatialIntelligence(db).update_hotspots()
                logger.info("Hotspot refresh: %d active clusters", len(clusters_found))
            finally:
                db.close()
        except Exception:
            logger.exception("Hotspot refresh failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    db = SessionLocal()
    try:
        GeospatialIntelligence(db).update_hotspots()
    except Exception:
        logger.exception("Initial hotspot computation failed")
    finally:
        db.close()
    task = asyncio.create_task(_periodic_hotspot_refresh())
    yield
    task.cancel()


app = FastAPI(title=settings.app_name, version=settings.version, lifespan=lifespan)

# Middleware runs bottom-up: rate limit first, then metrics, then audit.
app.add_middleware(AuditMiddleware)
app.add_middleware(MetricsMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(scanner.router)
app.include_router(clusters.router)
app.include_router(patrols.router)
app.include_router(alerts.router)
app.include_router(network.router)
app.include_router(reports.router)
app.include_router(citizen.router)

# Prometheus scrape endpoint. Served as a direct route (not a Mount) so
# GET /metrics answers 200 immediately instead of 307-redirecting to /metrics/,
# which non-redirect-following scrapers treat as an empty target.
@app.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/health")
def health():
    return {"status": "ok", "service": settings.app_name, "version": settings.version}
