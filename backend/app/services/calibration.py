"""Conformal reference calibration for the counterfeit detector.

Instead of fixed thresholds, verdicts are anchored to a population of GENUINE
reference captures:

1. Every analysis yields a measurement vector of raw physical metrics.
2. Each metric gets a robust z-score against the reference population
   (median/MAD), directional: a *deficit* of security-feature structure is
   counterfeit evidence, an *excess* of print raster is counterfeit evidence,
   tone shifts are two-sided.
3. The weighted deviation ("nonconformity") is converted to a split-conformal
   p-value against held-out reference scores: p = (1 + #{ref >= obs}) / (n + 1).

Statistical guarantee (exchangeability): a genuine capture drawn from the same
distribution as the reference set is flagged LIKELY_COUNTERFEIT with
probability <= COUNTERFEIT_P (1%). That is the honest basis for "99%-grade"
accuracy — specificity is guaranteed by construction; sensitivity is measured
by scripts/verify_capture_study.py.

Recalibrate against real notes with scripts/calibrate_reference.py --images-dir.
"""
import json
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

REFERENCE_PATH = Path(__file__).resolve().parents[1] / "data" / "reference_stats.json"

# Conformal decision bands
COUNTERFEIT_P = 0.01   # beyond the 99th percentile of genuine references
REVIEW_P = 0.10        # 90th-99th percentile -> manual review

# metric name -> (direction, weight)
# direction: "deficit" (missing structure = evidence), "excess", "two_sided"
METRIC_SPEC: dict[str, tuple[str, float]] = {
    "microprint.laplacian_variance": ("deficit", 1.0),
    "microprint.scale_gain": ("deficit", 0.6),
    "security_thread.confidence": ("deficit", 1.0),
    "hologram.saturated_fraction": ("deficit", 0.8),
    "hologram.hue_diversity": ("deficit", 0.6),
    "intaglio.ridge_strength": ("deficit", 1.0),
    "serial_number.aligned_glyphs": ("deficit", 0.8),
    "paper_texture.block_std_variance": ("deficit", 1.0),
    "capture.brightness_mean": ("two_sided", 0.4),
    "capture.saturation_mean": ("two_sided", 0.5),
    "capture.raster_periodicity": ("excess", 1.2),
}

# Reprints lose all detail beyond their dot pitch, so the half->full resolution
# gain collapses — weight it like a primary structural metric.
METRIC_SPEC["microprint.scale_gain"] = ("deficit", 1.0)

Z_CLIP = 10.0


def measurement_vector(features: dict, capture_stats: dict) -> dict[str, float]:
    """Flatten an Analysis into the calibrated metric vector."""
    vec: dict[str, float] = {}
    for name in METRIC_SPEC:
        group, key = name.split(".", 1)
        if group == "capture":
            value = capture_stats.get(key)
        elif group in features:
            feature = features[group]
            value = feature.get(key) if key == "confidence" else feature["detail"].get(key)
        else:
            value = None
        if value is not None:
            vec[name] = float(value)
    # Carried for the capture guard; not a scored metric (absent from METRIC_SPEC).
    if capture_stats.get("source_width") is not None:
        vec["capture.source_width"] = float(capture_stats["source_width"])
    return vec


@dataclass
class ReferenceCalibration:
    stats: dict[str, dict]          # metric -> {median, mad}
    scores: list[float]             # sorted held-out reference nonconformities
    n: int
    source: str
    split: bool
    guards: dict | None = None      # capture-quality envelope (brightness, width)

    def capture_guard_reason(self, capture_stats: dict) -> str | None:
        """Content-independent capture-quality gate.

        Captures outside the verification envelope (lighting, resolution) are
        never judged — structural deficits under bad capture conditions are
        unreliable evidence in either direction. Returns a reason string when
        the capture must be routed to manual review.
        """
        if not self.guards:
            return None
        brightness = capture_stats.get("brightness_mean")
        if brightness is not None and not (
            self.guards["brightness_lo"] <= brightness <= self.guards["brightness_hi"]
        ):
            return ("capture lighting outside the verification envelope — "
                    "retake with even, moderate lighting")
        width = capture_stats.get("source_width")
        if width is not None and width < self.guards["min_width"]:
            return ("capture resolution below the verification envelope — "
                    "retake closer / at higher resolution")
        return None

    def nonconformity(self, vector: dict[str, float]) -> float:
        """Weighted mean of directional robust z-scores vs the reference."""
        total, weight_sum = 0.0, 0.0
        for name, value in vector.items():
            spec = METRIC_SPEC.get(name)
            ref = self.stats.get(name)
            if spec is None or ref is None:
                continue
            direction, weight = spec
            scale = 1.4826 * ref["mad"]
            scale = max(scale, 0.05 * abs(ref["median"]), 1e-6)
            z = (value - ref["median"]) / scale
            if direction == "deficit":
                deviation = max(0.0, -z)
            elif direction == "excess":
                deviation = max(0.0, z)
            else:  # two_sided
                deviation = abs(z)
            total += weight * min(deviation, Z_CLIP)
            weight_sum += weight
        return round(total / weight_sum, 4) if weight_sum else 0.0

    def p_value(self, nonconformity: float) -> float:
        """Split-conformal p-value: fraction of reference scores >= observed."""
        greater_equal = sum(1 for s in self.scores if s >= nonconformity)
        return round((1 + greater_equal) / (len(self.scores) + 1), 4)

    def assess_vectors(self, vectors: list[dict[str, float]]) -> dict:
        ps = [self.p_value(self.nonconformity(v)) for v in vectors]
        return {
            "p_values": ps,
            "p_min": min(ps),
            "p_max": max(ps),
            "p_median": float(np.median(ps)),
        }

    def save(self, path: Path = REFERENCE_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "stats": self.stats,
            "scores": self.scores,
            "n": self.n,
            "source": self.source,
            "split": self.split,
            "guards": self.guards,
        }, indent=1))


def build_calibration(
    vectors: list[dict[str, float]], source: str
) -> ReferenceCalibration:
    """Split-conformal build: stats from the first ~30%, scores from the rest.

    Falls back to full-set stats + full-set scores (slightly optimistic) when
    the sample is too small to split — flagged via ``split: false``.
    """
    n = len(vectors)
    split = n >= 100
    stats_pool = vectors[: max(40, int(n * 0.3))] if split else vectors
    score_pool = vectors[max(40, int(n * 0.3)):] if split else vectors

    stats: dict[str, dict] = {}
    for name in METRIC_SPEC:
        values = np.array([v[name] for v in stats_pool if name in v], dtype=float)
        if len(values) == 0:
            continue
        median = float(np.median(values))
        mad = float(np.median(np.abs(values - median)))
        stats[name] = {"median": round(median, 5), "mad": round(mad, 5)}

    # Capture-quality guards from the observed reference envelope.
    brightness = np.array(
        [v["capture.brightness_mean"] for v in vectors if "capture.brightness_mean" in v]
    )
    widths = np.array(
        [v["capture.source_width"] for v in vectors if "capture.source_width" in v]
    )
    guards = None
    if len(brightness):
        guards = {
            "brightness_lo": round(float(brightness.min()) * 0.85, 2),
            "brightness_hi": round(min(float(brightness.max()) * 1.10, 250.0), 2),
            "min_width": int(widths.min() * 0.88) if len(widths) else 640,
        }

    calibration = ReferenceCalibration(
        stats=stats, scores=[], n=n, source=source, split=split, guards=guards
    )
    calibration.scores = sorted(round(calibration.nonconformity(v), 4) for v in score_pool)
    return calibration


def load_calibration(path: Path = REFERENCE_PATH) -> ReferenceCalibration | None:
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text())
        return ReferenceCalibration(
            stats=raw["stats"], scores=raw["scores"], n=raw["n"],
            source=raw.get("source", "unknown"), split=raw.get("split", False),
            guards=raw.get("guards"),
        )
    except Exception:
        logger.exception("Failed to load reference calibration; falling back to thresholds")
        return None
