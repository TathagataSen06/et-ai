"""Application configuration loaded from environment variables (.env supported)."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="NETRA_", extra="ignore")

    app_name: str = "Project Netra API"
    version: str = "0.1.0"
    debug: bool = False

    # SQLite by default so the prototype runs anywhere; point at Postgres in prod.
    database_url: str = "sqlite:///./netra.db"

    cors_origins: list[str] = ["http://localhost:3000", "http://localhost:5173"]

    # Hotspot clustering parameters
    cluster_eps_km: float = 2.0
    cluster_min_samples: int = 3
    cluster_lookback_days: int = 90
    cluster_refresh_minutes: int = 15

    # Scanner thresholds (counterfeit_score above these values)
    threshold_counterfeit: float = 0.75
    threshold_suspicious: float = 0.50

    max_upload_bytes: int = 20 * 1024 * 1024  # 20 MB (48MP phone photos reach ~15 MB)
    batch_max_files: int = 10

    # Auth (OAuth2 password flow + JWT). Demo credentials — override in prod.
    jwt_secret: str = "netra-dev-secret-change-me-in-production-0123456789"
    jwt_algorithm: str = "HS256"
    token_ttl_minutes: int = 480
    commander_password: str = "netra-demo"
    officer_password: str = "netra-demo"

    # Rate limiting (sliding window, per client IP)
    rate_limit_enabled: bool = True
    rate_limit_auth_per_minute: int = 10
    rate_limit_scanner_per_minute: int = 30
    rate_limit_default_per_minute: int = 240

    # Celery/Redis (used by the compose deployment; local dev uses asyncio)
    redis_url: str = "redis://localhost:6379/0"

    # Optional Neo4j sync for the fraud network graph (spec Feature 5)
    neo4j_uri: str = ""
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""

    # Intelligence report generation: Claude API > Groq > Ollama > template fallback
    anthropic_api_key: str = ""
    report_model: str = "claude-opus-4-8"
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    ollama_url: str = ""
    ollama_model: str = "llama3"


@lru_cache
def get_settings() -> Settings:
    return Settings()
