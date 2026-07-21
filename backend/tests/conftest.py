"""Shared fixtures. Points the app at a throwaway SQLite DB before import."""
import os
import sys
from pathlib import Path

os.environ["NETRA_DATABASE_URL"] = "sqlite:///./test_netra.db"
os.environ["NETRA_RATE_LIMIT_ENABLED"] = "false"  # re-enabled inside the rate-limit test
# Keep tests offline/deterministic: env vars override any local .env LLM keys.
os.environ["NETRA_ANTHROPIC_API_KEY"] = ""
os.environ["NETRA_GROQ_API_KEY"] = ""
os.environ["NETRA_OLLAMA_URL"] = ""
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, engine, SessionLocal  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture()
def db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(db):
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def auth_headers(client):
    """Bearer token for the demo COMMAND user."""
    response = client.post(
        "/api/v1/auth/login",
        data={"username": "commander", "password": "netra-demo"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def make_genuine_like_note(width: int = 1200, height: int = 530) -> np.ndarray:
    """Synthetic image with the texture/features the detectors look for."""
    rng = np.random.default_rng(7)
    # Varied paper texture: per-block noise amplitude differs across the note.
    img = np.full((height, width, 3), 200, dtype=np.uint8)
    for i in range(0, height, 64):
        for j in range(0, width, 64):
            amp = rng.integers(3, 45)
            block = rng.normal(0, amp, (min(64, height - i), min(64, width - j), 3))
            region = img[i:i + 64, j:j + 64].astype(np.int16) + block.astype(np.int16)
            img[i:i + 64, j:j + 64] = np.clip(region, 0, 255).astype(np.uint8)

    # Fine "microprint" line pattern.
    for y in range(0, height, 3):
        img[y, :, :] = np.clip(img[y, :, :].astype(np.int16) - 40, 0, 255).astype(np.uint8)

    # Dark vertical security thread at ~55% width.
    x = int(width * 0.55)
    img[:, x:x + 6] = (40, 40, 45)

    # Saturated, hue-varied "hologram" patch.
    patch = np.zeros((120, 120, 3), dtype=np.uint8)
    for k in range(120):
        hue = int(180 * k / 120)
        patch[k, :] = cv2.cvtColor(
            np.uint8([[[hue, 230, 220]]]), cv2.COLOR_HSV2BGR
        )[0, 0]
    img[40:160, 60:180] = patch

    # Serial-number glyph row in the bottom-right panel.
    x0, y0 = int(width * 0.62), int(height * 0.85)
    for k in range(9):
        cv2.rectangle(img, (x0 + k * 28, y0), (x0 + k * 28 + 16, y0 + 30), (20, 20, 20), -1)

    return img


def encode_png(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()
