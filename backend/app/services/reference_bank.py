"""Per-note reference matching and relative feature extraction.

The global-population approach compares a capture against *all* genuine notes
at once, so note-to-note variance (denomination, design, wear) inflates the
reference spread and fakes hide inside it. This module instead matches a
capture to the single reference note it depicts and scores how far it deviates
from *that* note — removing note identity as a source of variance entirely.

Relative metrics are ratios (test / reference), so they are unit-free and feed
the same conformal machinery as the absolute vector.
"""
import logging
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger(__name__)

BANK_DIR = Path(__file__).resolve().parents[1] / "data" / "reference_notes"
RELATIVE_PATH = Path(__file__).resolve().parents[1] / "data" / "relative_stats.json"
WORK_WIDTH = 900          # all comparisons happen at a fixed working width
MATCH_MIN_CONFIDENCE = 0.35

# Directions/weights live with the rest of the conformal spec.
from app.services.calibration import RELATIVE_SPEC  # noqa: E402,F401


def load_note(path: Path) -> np.ndarray | None:
    """Read a note image, normalising a portrait capture to landscape."""
    img = cv2.imread(str(path))
    if img is None:
        return None
    h, w = img.shape[:2]
    return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE) if h > w else img


def _work(img: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    if w == 0 or h == 0:
        return img
    return cv2.resize(img, (WORK_WIDTH, max(1, int(h * WORK_WIDTH / w))),
                      interpolation=cv2.INTER_AREA)


def absolute_metrics(img: np.ndarray) -> dict[str, float]:
    """Capture statistics used for both matching and relative scoring."""
    work = _work(img)
    gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0

    low = cv2.GaussianBlur(gray, (0, 0), 2.0)
    high_freq = float(np.mean((gray - low) ** 2))
    laplacian = float(cv2.Laplacian(gray, cv2.CV_32F).var())

    # Multi-scale detail gain: genuine printing keeps gaining high-frequency
    # energy from half- to full-resolution; a reprint has none left to reveal.
    half = cv2.resize(gray, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    half_lap = float(cv2.Laplacian(half, cv2.CV_32F).var())
    scale_gain = laplacian / half_lap if half_lap > 1e-9 else 0.0

    blocks = [gray[i:i + 48, j:j + 48].std()
              for i in range(0, gray.shape[0] - 47, 48)
              for j in range(0, gray.shape[1] - 47, 48)]
    texture = float(np.var(blocks)) if blocks else 0.0

    edges = cv2.Canny((gray * 255).astype(np.uint8), 60, 160)
    edge_density = float(np.count_nonzero(edges)) / edges.size

    # Print-raster periodicity: halftone dot grids and screen pixel grids show
    # up as off-centre peaks in the FFT magnitude spectrum.
    spec = np.abs(np.fft.fftshift(np.fft.fft2(gray - gray.mean())))
    cy, cx = np.array(spec.shape) // 2
    spec[cy - 4:cy + 5, cx - 4:cx + 5] = 0
    raster = float(spec.max() / (spec.mean() + 1e-9))

    saturation = float(cv2.cvtColor(work, cv2.COLOR_BGR2HSV)[:, :, 1].mean())

    return {
        "high_freq": high_freq,
        "laplacian": laplacian,
        "scale_gain": scale_gain,
        "texture": texture,
        "edge_density": edge_density,
        "raster": raster,
        "saturation": saturation,
    }


def _signature(img: np.ndarray) -> np.ndarray:
    """Colour+layout signature for denomination matching (illumination-normalised)."""
    work = cv2.resize(img, (96, 44), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(work, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [24, 8], [0, 180, 0, 256])
    hist = cv2.normalize(hist, hist).flatten()
    gray = cv2.cvtColor(work, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gray = (gray - gray.mean()) / (gray.std() + 1e-6)
    return np.concatenate([hist, gray.flatten() / 40.0])


@dataclass
class ReferenceNote:
    name: str
    metrics: dict[str, float]
    signature: np.ndarray


@dataclass
class MatchResult:
    reference: ReferenceNote
    confidence: float


class ReferenceBank:
    """Bank of genuine reference notes with nearest-note matching."""

    def __init__(self, notes: list[ReferenceNote]) -> None:
        self.notes = notes

    @classmethod
    def from_dir(cls, directory: Path = BANK_DIR) -> "ReferenceBank | None":
        if not directory.exists():
            return None
        notes: list[ReferenceNote] = []
        for path in sorted(directory.iterdir()):
            if path.suffix.lower() not in (".jpg", ".jpeg", ".png", ".bmp", ".webp"):
                continue
            img = load_note(path)
            if img is None:
                continue
            notes.append(ReferenceNote(path.name, absolute_metrics(img), _signature(img)))
        if not notes:
            return None
        logger.info("Reference bank loaded: %d genuine notes", len(notes))
        return cls(notes)

    def match(self, img: np.ndarray) -> MatchResult | None:
        """Nearest reference note by cosine similarity of the signature."""
        sig = _signature(img)
        best, best_sim = None, -1.0
        for note in self.notes:
            denom = (np.linalg.norm(sig) * np.linalg.norm(note.signature)) or 1e-9
            sim = float(np.dot(sig, note.signature) / denom)
            if sim > best_sim:
                best, best_sim = note, sim
        if best is None or best_sim < MATCH_MIN_CONFIDENCE:
            return None
        return MatchResult(best, round(best_sim, 4))


def relative_vector(test_metrics: dict[str, float],
                    reference_metrics: dict[str, float]) -> dict[str, float]:
    """Ratio of every capture metric to the matched reference's own value."""
    vec: dict[str, float] = {}
    for key, value in test_metrics.items():
        ref = reference_metrics.get(key)
        if ref is None or abs(ref) < 1e-12:
            continue
        vec[f"rel.{key}"] = float(value / ref)
    return vec
