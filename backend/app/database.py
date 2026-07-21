"""SQLAlchemy engine/session setup.

Uses plain lat/lon float columns instead of PostGIS geometry so the same schema
runs on SQLite (local prototype) and Postgres (docker-compose). Distance math is
done with the haversine formula in the service layer; swapping in PostGIS later
only requires changing the geospatial service queries.
"""
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings

settings = get_settings()

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app.models import orm  # noqa: F401 - register models

    Base.metadata.create_all(bind=engine)
