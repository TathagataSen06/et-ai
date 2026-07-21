"""OAuth2 password flow with JWT bearer tokens.

Demo user store: two role-scoped accounts whose passwords come from settings
(env-overridable). Passwords are held as PBKDF2 hashes computed at startup;
a real deployment would swap `USERS` for a users table + registration flow.
"""
import hashlib
import hmac
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

from app.config import get_settings

_PBKDF2_ITERATIONS = 120_000

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)


def _hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _PBKDF2_ITERATIONS)


@dataclass(frozen=True)
class User:
    username: str
    role: str  # COMMAND | OFFICER
    salt: bytes
    password_hash: bytes

    def verify(self, password: str) -> bool:
        return hmac.compare_digest(self.password_hash, _hash_password(password, self.salt))


def _build_users() -> dict[str, User]:
    settings = get_settings()
    users = {}
    for username, password, role in (
        ("commander", settings.commander_password, "COMMAND"),
        ("officer", settings.officer_password, "OFFICER"),
    ):
        salt = os.urandom(16)
        users[username] = User(username, role, salt, _hash_password(password, salt))
    return users


USERS: dict[str, User] = _build_users()


def authenticate(username: str, password: str) -> User | None:
    user = USERS.get(username)
    if user and user.verify(password):
        return user
    return None


def create_token(user: User) -> tuple[str, int]:
    settings = get_settings()
    ttl = timedelta(minutes=settings.token_ttl_minutes)
    payload = {
        "sub": user.username,
        "role": user.role,
        "exp": datetime.now(timezone.utc) + ttl,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm), int(
        ttl.total_seconds()
    )


def decode_token(token: str) -> dict | None:
    settings = get_settings()
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.InvalidTokenError:
        return None


async def get_current_user(token: str | None = Depends(oauth2_scheme)) -> dict:
    """Dependency for endpoints restricted to authenticated law-enforcement users."""
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = decode_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload
