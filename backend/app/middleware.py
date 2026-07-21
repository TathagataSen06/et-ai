"""HTTP middleware: sliding-window rate limiting, audit logging, Prometheus metrics.

In-memory implementations sized for a single-node prototype; the rate limiter
and metrics would move to Redis / a metrics sidecar in a multi-replica deploy.
"""
import logging
import threading
import time
from collections import defaultdict, deque

from fastapi import Request
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Histogram
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings
from app.services.auth_service import decode_token

logger = logging.getLogger(__name__)

REQUEST_COUNT = Counter(
    "netra_http_requests_total", "HTTP requests", ["method", "path", "status"]
)
REQUEST_LATENCY = Histogram(
    "netra_http_request_duration_seconds", "Request latency", ["method", "path"]
)


def _bucket_for(path: str) -> tuple[str, int]:
    settings = get_settings()
    if path.startswith("/api/v1/auth"):
        return "auth", settings.rate_limit_auth_per_minute
    if path.startswith("/api/v1/scanner"):
        return "scanner", settings.rate_limit_scanner_per_minute
    return "default", settings.rate_limit_default_per_minute


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding one-minute window per (client IP, route bucket)."""

    def __init__(self, app):
        super().__init__(app)
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    async def dispatch(self, request: Request, call_next):
        settings = get_settings()
        if not settings.rate_limit_enabled or not request.url.path.startswith("/api/"):
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        bucket, limit = _bucket_for(request.url.path)
        now = time.monotonic()
        key = (client_ip, bucket)

        with self._lock:
            window = self._hits[key]
            while window and now - window[0] > 60.0:
                window.popleft()
            if len(window) >= limit:
                retry_after = max(1, int(61 - (now - window[0])))
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Rate limit exceeded"},
                    headers={"Retry-After": str(retry_after)},
                )
            window.append(now)

        return await call_next(request)


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        # Route template (not raw path) keeps label cardinality bounded.
        route = request.scope.get("route")
        path = getattr(route, "path", request.url.path)
        REQUEST_COUNT.labels(request.method, path, str(response.status_code)).inc()
        REQUEST_LATENCY.labels(request.method, path).observe(time.perf_counter() - start)
        return response


class AuditMiddleware(BaseHTTPMiddleware):
    """Record mutating API calls (who, what, when, outcome)."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.method in ("POST", "PUT", "DELETE") and request.url.path.startswith("/api/v1"):
            try:
                self._record(request, response.status_code)
            except Exception:
                logger.exception("Audit log write failed")
        return response

    @staticmethod
    def _record(request: Request, status_code: int) -> None:
        from app.database import SessionLocal
        from app.models.orm import AuditLog

        username = None
        auth_header = request.headers.get("authorization", "")
        if auth_header.lower().startswith("bearer "):
            payload = decode_token(auth_header[7:])
            if payload:
                username = payload.get("sub")

        db = SessionLocal()
        try:
            db.add(AuditLog(
                username=username,
                method=request.method,
                path=request.url.path[:200],
                status_code=status_code,
                client_ip=request.client.host if request.client else None,
            ))
            db.commit()
        finally:
            db.close()
