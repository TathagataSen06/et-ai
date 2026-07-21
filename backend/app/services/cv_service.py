"""Rule-based counterfeit detection using pure OpenCV (no model training).

Each ``_detect_*`` method returns a confidence in [0, 1] that the corresponding
GENUINE security feature is present. The weighted sum is an authenticity score;
``counterfeit_score = 1 - authenticity``. (The original spec summed genuine-feature
confidences but labelled the result a counterfeit score — inverted here on purpose.)

Inference-time intelligence (training-free analogs of classic training tricks):

- **Reliability-weighted fusion** (dynamic focal alpha): features that cannot be
  measured well on this capture (clipped exposure, low resolution) are
  down-weighted and the remaining weights renormalized, so "couldn't measure"
  stops masquerading as "counterfeit evidence".
- **Perturbation consensus ensemble** (uncertainty quantification / ensemble
  disagreement): borderline images are re-analyzed under small deterministic
  perturbations (rotation, rescale, gamma, JPEG round-trip); the score variance
  is an uncertainty estimate.
- **Adaptive decision margin** (adaptive-margin triplet loss): the SUSPICIOUS
  band widens in proportion to that uncertainty — unstable samples need more
  evidence before a hard verdict.
- **Two-tier screening** (knowledge-distillation deployment): a single fast pass
  ("student") settles clear-cut cases; only ambiguous scores escalate to the
  full consensus ensemble ("teacher").
- **Multi-scale detail gain** (progressive resizing): genuine microprint gains
  high-frequency energy from half- to full-resolution; flat reproductions don't.

Heuristics only: this is a screening tool, not forensic proof. Thresholds were
tuned on synthetic imagery; real deployment would calibrate against RBI reference
notes under controlled capture conditions.
"""
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field

import cv2
import numpy as np

logger = logging.getLogger(__name__)

FEATURE_WEIGHTS: dict[str, float] = {
    "microprint": 0.25,
    "security_thread": 0.20,
    "hologram": 0.20,
    "intaglio": 0.15,
    "serial_number": 0.10,
    "paper_texture": 0.10,
}

ANALYSIS_WIDTH = 1200  # normalize input width so variance thresholds are comparable

# Adaptive margin: how far the review band can widen under ensemble disagreement.
ADAPTIVE_MARGIN_SCALE = 1.5  # band shift per unit of ensemble std
MAX_ADAPTIVE_SHIFT = 0.15

# Reliability floors — a feature is never fully silenced, only attenuated.
MIN_RELIABILITY = 0.3
FINE_DETAIL_MIN_WIDTH = 700  # below this capture width, fine-print features degrade


class ImageDecodeError(ValueError):
    """Raised when the uploaded bytes cannot be decoded as an image."""


@dataclass
class Analysis:
    features: dict[str, dict]
    denomination: str
    counterfeit_score: float
    capture_stats: dict = field(default_factory=dict)


@dataclass
class Assessment:
    """Full inference-time result: score + uncertainty + adaptive verdict."""

    counterfeit_score: float
    uncertainty: float
    mode: str  # "fast" (single pass) | "consensus" (perturbation ensemble)
    verdict: str  # LIKELY_GENUINE | SUSPICIOUS | LIKELY_COUNTERFEIT
    verdict_reason: str
    thresholds: dict[str, float]
    features: dict[str, dict]
    denomination: str
    ensemble_scores: list[float] = field(default_factory=list)
    # Conformal calibration outputs (None when no reference is installed)
    calibrated: bool = False
    genuine_percentile: float | None = None


class CounterfeitDetector:
    def __init__(self) -> None:
        # Conformal reference calibration (see services/calibration.py).
        # Loaded lazily so a missing/broken reference degrades to thresholds.
        self._calibration = None
        self._calibration_loaded = False
        self._bank = None
        self._bank_loaded = False
        self._relative = None
        self._relative_loaded = False

    @property
    def calibration(self):
        if not self._calibration_loaded:
            from app.services.calibration import load_calibration

            self._calibration = load_calibration()
            self._calibration_loaded = True
            if self._calibration:
                logger.info(
                    "Conformal reference loaded: n=%d source=%s split=%s",
                    self._calibration.n, self._calibration.source, self._calibration.split,
                )
        return self._calibration

    def analyze_bytes(self, data: bytes) -> Analysis:
        return self.analyze(self._decode(data))

    def assess_bytes(
        self,
        data: bytes,
        threshold_suspicious: float = 0.50,
        threshold_counterfeit: float = 0.75,
    ) -> Assessment:
        return self.assess(self._decode(data), threshold_suspicious, threshold_counterfeit)

    @staticmethod
    def _decode(data: bytes) -> np.ndarray:
        arr = np.frombuffer(data, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            raise ImageDecodeError("Could not decode image data")
        return img

    def analyze(self, img: np.ndarray) -> Analysis:
        original_width = img.shape[1]
        img = self._normalize(img)
        capture_stats = self._global_capture_stats(img)
        capture_stats["source_width"] = original_width
        features = {
            "microprint": self._detect_microprint(img),
            "security_thread": self._detect_security_thread(img),
            "hologram": self._detect_hologram(img),
            "intaglio": self._detect_intaglio(img),
            "serial_number": self._detect_serial_number(img),
            "paper_texture": self._detect_texture(img),
        }

        # Reliability-weighted fusion: attenuate features the capture can't
        # support, renormalize the rest (training-free "dynamic alpha").
        reliability = self._capture_reliability(img, original_width)
        effective = {
            name: FEATURE_WEIGHTS[name] * reliability[name] for name in FEATURE_WEIGHTS
        }
        total_weight = sum(effective.values())
        authenticity = sum(
            features[name]["confidence"] * effective[name] for name in FEATURE_WEIGHTS
        ) / total_weight
        for name in FEATURE_WEIGHTS:
            features[name]["detail"]["reliability"] = round(reliability[name], 3)
            features[name]["detail"]["effective_weight"] = round(
                effective[name] / total_weight, 3
            )

        return Analysis(
            features=features,
            denomination=self._classify_denomination(img),
            counterfeit_score=round(1.0 - authenticity, 4),
            capture_stats=capture_stats,
        )

    @staticmethod
    def _global_capture_stats(img: np.ndarray) -> dict:
        """Whole-image tone + print-raster statistics for conformal calibration.

        raster_periodicity: median per-block FFT peak-to-median ratio. Genuine
        intaglio printing has no regular raster, so only isolated microprint
        blocks show periodic peaks and the median stays low. Photocopier
        halftones, inkjet dithering, and phone-screen pixel grids are periodic
        across the whole image, pushing the median up.
        """
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

        block = 128
        ratios = []
        for i in range(0, gray.shape[0] - block + 1, block):
            for j in range(0, gray.shape[1] - block + 1, block):
                tile = gray[i:i + block, j:j + block]
                spectrum = np.abs(np.fft.fftshift(np.fft.fft2(tile - tile.mean())))
                cy, cx = block // 2, block // 2
                ys, xs = np.ogrid[:block, :block]
                radius = np.sqrt((ys - cy) ** 2 + (xs - cx) ** 2)
                ring = spectrum[(radius > 4) & (radius < block // 2 - 2)]
                if ring.size:
                    median = float(np.median(ring)) + 1e-6
                    ratios.append(float(ring.max()) / median)
        return {
            "brightness_mean": round(float(gray.mean()), 2),
            "saturation_mean": round(float(hsv[:, :, 1].mean()), 2),
            "raster_periodicity": round(float(np.median(ratios)), 2) if ratios else 0.0,
        }

    def assess(
        self,
        img: np.ndarray,
        threshold_suspicious: float = 0.50,
        threshold_counterfeit: float = 0.75,
    ) -> Assessment:
        """Two-tier screening with perturbation-consensus uncertainty.

        Fast pass first; scores that even maximal band-widening could not flip
        are settled immediately. Borderline scores escalate to the consensus
        ensemble, whose spread drives the adaptive verdict margin.
        """
        relative = self._relative_assessment(img)
        if relative is not None:
            return relative

        base = self.analyze(img)
        fast_low = threshold_suspicious - MAX_ADAPTIVE_SHIFT
        fast_high = threshold_counterfeit + MAX_ADAPTIVE_SHIFT

        if base.counterfeit_score <= fast_low or base.counterfeit_score >= fast_high:
            verdict, reason, thresholds = self.resolve_verdict(
                base.counterfeit_score, 0.0, threshold_suspicious, threshold_counterfeit
            )
            assessment = Assessment(
                counterfeit_score=base.counterfeit_score,
                uncertainty=0.0,
                mode="fast",
                verdict=verdict,
                verdict_reason=reason,
                thresholds=thresholds,
                features=base.features,
                denomination=base.denomination,
                ensemble_scores=[base.counterfeit_score],
            )
            return self._apply_conformal(assessment, [base])

        # Consensus ensemble: same detector, deterministically perturbed inputs.
        analyses = [base]
        scores = [base.counterfeit_score]
        feature_conf: dict[str, list[float]] = {
            name: [f["confidence"]] for name, f in base.features.items()
        }
        for variant in self._perturbations(img):
            result = self.analyze(variant)
            analyses.append(result)
            scores.append(result.counterfeit_score)
            for name, f in result.features.items():
                feature_conf[name].append(f["confidence"])

        mean_score = round(float(np.mean(scores)), 4)
        uncertainty = round(float(np.std(scores)), 4)
        features = base.features
        for name in features:
            confs = feature_conf[name]
            features[name]["confidence"] = round(float(np.mean(confs)), 4)
            features[name]["detail"]["ensemble_std"] = round(float(np.std(confs)), 4)

        verdict, reason, thresholds = self.resolve_verdict(
            mean_score, uncertainty, threshold_suspicious, threshold_counterfeit
        )
        assessment = Assessment(
            counterfeit_score=mean_score,
            uncertainty=uncertainty,
            mode="consensus",
            verdict=verdict,
            verdict_reason=reason,
            thresholds=thresholds,
            features=features,
            denomination=base.denomination,
            ensemble_scores=[round(s, 4) for s in scores],
        )
        return self._apply_conformal(assessment, analyses)

    # ------------------------------------------------------------------
    # Reference-relative mode: when a bank of genuine notes is installed and
    # the capture matches one of them, score the deviation from *that* note.
    # Removing note identity as a variance source measured AUC 0.973 vs 0.610
    # for the population-relative path (scripts/calibrate_relative.py).
    # ------------------------------------------------------------------
    @property
    def reference_bank(self):
        if not self._bank_loaded:
            from app.services.reference_bank import ReferenceBank

            try:
                self._bank = ReferenceBank.from_dir()
            except Exception:
                logger.exception("Reference bank failed to load")
                self._bank = None
            self._bank_loaded = True
        return self._bank

    @property
    def relative_calibration(self):
        if not self._relative_loaded:
            from app.services.calibration import load_calibration
            from app.services.reference_bank import RELATIVE_PATH

            self._relative = load_calibration(RELATIVE_PATH)
            self._relative_loaded = True
        return self._relative

    def _relative_assessment(self, img: np.ndarray) -> Assessment | None:
        bank, calib = self.reference_bank, self.relative_calibration
        if bank is None or calib is None:
            return None
        match = bank.match(img)
        if match is None:
            return None

        from app.services.calibration import COUNTERFEIT_P, REVIEW_P
        from app.services.reference_bank import absolute_metrics, relative_vector

        vec = relative_vector(absolute_metrics(img), match.reference.metrics)
        if not vec:
            return None
        p = calib.p_value(calib.nonconformity(vec))

        if p <= COUNTERFEIT_P:
            verdict = "LIKELY_COUNTERFEIT"
            reason = (f"deviates from genuine reference {match.reference.name} beyond the "
                      f"99th percentile of genuine captures (p={p:.3f})")
        elif p <= REVIEW_P:
            verdict = "SUSPICIOUS"
            reason = (f"deviates from genuine reference {match.reference.name} "
                      f"(p={p:.3f}) — manual review")
        else:
            verdict = "LIKELY_GENUINE"
            reason = (f"consistent with genuine reference {match.reference.name} "
                      f"(p={p:.3f})")

        features = {
            name.replace("rel.", ""): {
                "confidence": round(float(min(max(value, 0.0), 1.0)), 4),
                "detail": {"ratio_to_reference": round(float(value), 4)},
                "status": "MATCH" if value >= 0.6 else "DEVIATION",
            }
            for name, value in vec.items()
        }
        return Assessment(
            counterfeit_score=round(1.0 - p, 4),
            uncertainty=0.0,
            mode="reference-relative",
            verdict=verdict,
            verdict_reason=reason,
            thresholds={"counterfeit_p": float(COUNTERFEIT_P), "review_p": float(REVIEW_P)},
            features=features,
            denomination="UNKNOWN",
            ensemble_scores=[round(1.0 - p, 4)],
            calibrated=True,
            genuine_percentile=round(100.0 * (1.0 - p), 1),
        )

    def _apply_conformal(self, assessment: Assessment, analyses: list[Analysis]) -> Assessment:
        """Re-anchor the verdict to the genuine reference population.

        LIKELY_COUNTERFEIT only when every perturbation is beyond the 99th
        percentile of genuine references (p_max <= 1%): a genuine capture from
        the reference distribution is falsely accused with probability <= 1%.
        LIKELY_GENUINE only when every perturbation sits comfortably inside the
        reference envelope; everything in between routes to manual review.
        """
        calibration = self.calibration
        if calibration is None:
            return assessment

        from app.services.calibration import COUNTERFEIT_P, REVIEW_P, measurement_vector

        # Capture guard: out-of-envelope captures (lighting/resolution) are
        # routed to review, never judged — deficits measured under conditions
        # the reference never saw are unreliable evidence in either direction.
        guard_reason = calibration.capture_guard_reason(analyses[0].capture_stats)
        if guard_reason is not None:
            assessment.verdict = "SUSPICIOUS"
            assessment.verdict_reason = guard_reason
            assessment.calibrated = True
            assessment.genuine_percentile = None
            assessment.thresholds = {
                **assessment.thresholds,
                "capture_guard": 1.0,
            }
            return assessment

        vectors = [measurement_vector(a.features, a.capture_stats) for a in analyses]
        conformal = calibration.assess_vectors(vectors)

        if conformal["p_max"] <= COUNTERFEIT_P:
            verdict = "LIKELY_COUNTERFEIT"
            reason = ("beyond the 99th percentile of the genuine reference "
                      "population in every perturbation")
        elif conformal["p_min"] > REVIEW_P:
            verdict = "LIKELY_GENUINE"
            reason = "consistent with the genuine reference population"
        else:
            verdict = "SUSPICIOUS"
            if conformal["p_max"] <= COUNTERFEIT_P or conformal["p_min"] <= COUNTERFEIT_P:
                reason = "straddles the genuine reference envelope under perturbation"
            else:
                reason = "outside the typical genuine reference envelope"

        assessment.verdict = verdict
        assessment.verdict_reason = reason
        assessment.calibrated = True
        assessment.genuine_percentile = round((1.0 - conformal["p_median"]) * 100, 2)
        assessment.thresholds = {
            **assessment.thresholds,
            "conformal_counterfeit_p": COUNTERFEIT_P,
            "conformal_review_p": REVIEW_P,
            "conformal_p_min": conformal["p_min"],
            "conformal_p_max": conformal["p_max"],
        }
        return assessment

    @staticmethod
    def resolve_verdict(
        score: float,
        uncertainty: float,
        threshold_suspicious: float = 0.50,
        threshold_counterfeit: float = 0.75,
    ) -> tuple[str, str, dict[str, float]]:
        """Adaptive-margin verdict: the review band widens with instability."""
        shift = min(MAX_ADAPTIVE_SHIFT, ADAPTIVE_MARGIN_SCALE * uncertainty)
        hi = threshold_counterfeit + shift
        lo = threshold_suspicious - shift
        thresholds = {
            "genuine_below": round(lo, 4),
            "counterfeit_above": round(hi, 4),
            "adaptive_shift": round(shift, 4),
        }
        if score > hi:
            return "LIKELY_COUNTERFEIT", "score above adaptive counterfeit threshold", thresholds
        if score < lo:
            return "LIKELY_GENUINE", "score below adaptive genuine threshold", thresholds
        if score > threshold_counterfeit or score < threshold_suspicious:
            reason = "perturbation instability widened the review band"
        else:
            reason = "score inside the suspicious band"
        return "SUSPICIOUS", reason, thresholds

    @staticmethod
    def _capture_reliability(img: np.ndarray, original_width: int) -> dict[str, float]:
        """Per-feature measurement reliability from unambiguous capture defects.

        Only capture-quality signals that cannot be confused with counterfeit
        evidence are used: exposure clipping and source resolution. (Blur is
        deliberately excluded — a flat forgery and a blurry capture look alike,
        and that ambiguity belongs to the uncertainty estimate, not here.)
        """
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        clip_hi = float(np.mean(gray >= 250))
        clip_lo = float(np.mean(gray <= 5))
        exposure = float(np.clip(1.0 - 2.0 * (clip_hi + clip_lo), MIN_RELIABILITY, 1.0))
        highlights = float(np.clip(1.0 - 3.0 * clip_hi, MIN_RELIABILITY, 1.0))
        resolution = float(
            np.clip(original_width / FINE_DETAIL_MIN_WIDTH, MIN_RELIABILITY, 1.0)
        )
        return {
            "microprint": resolution,
            "security_thread": exposure,
            "hologram": highlights,  # blown highlights destroy saturation signal
            "intaglio": resolution,
            "serial_number": min(resolution, exposure),
            "paper_texture": exposure,
        }

    @staticmethod
    def _perturbations(img: np.ndarray) -> Iterator[np.ndarray]:
        """Deterministic capture-jitter simulations for the consensus ensemble."""
        h, w = img.shape[:2]
        center = (w / 2, h / 2)
        for angle in (2.0, -2.0):
            matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
            yield cv2.warpAffine(img, matrix, (w, h), borderMode=cv2.BORDER_REPLICATE)
        # Mild down-up rescale: information loss of a slightly farther capture.
        small = cv2.resize(img, (int(w * 0.92), int(h * 0.92)), interpolation=cv2.INTER_AREA)
        yield cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)
        # Gamma shift: lighting jitter.
        lut = np.array([np.clip((i / 255.0) ** 0.9 * 255.0, 0, 255) for i in range(256)],
                       dtype=np.uint8)
        yield cv2.LUT(img, lut)
        # JPEG round-trip: compression artifacts of a re-shared photo.
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if ok:
            yield cv2.imdecode(buf, cv2.IMREAD_COLOR)

    @staticmethod
    def _normalize(img: np.ndarray) -> np.ndarray:
        h, w = img.shape[:2]
        if w != ANALYSIS_WIDTH:
            scale = ANALYSIS_WIDTH / w
            img = cv2.resize(img, (ANALYSIS_WIDTH, max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
        return img

    def _detect_microprint(self, img: np.ndarray) -> dict:
        """Microprint is high-frequency detail -> high Laplacian variance.

        Multi-scale check (progressive-resizing analog): genuine fine print
        gains Laplacian energy going from half to full resolution, because the
        detail exists beyond the coarse scale. Flat reproductions gain little.
        """
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        sharpen = np.array([[-1, -1, -1], [-1, 9, -1], [-1, -1, -1]], dtype=np.float32)
        sharpened = cv2.filter2D(gray, -1, sharpen)
        variance = float(cv2.Laplacian(sharpened, cv2.CV_64F).var())

        half = cv2.resize(gray, (gray.shape[1] // 2, gray.shape[0] // 2),
                          interpolation=cv2.INTER_AREA)
        half_sharp = cv2.filter2D(half, -1, sharpen)
        variance_half = float(cv2.Laplacian(half_sharp, cv2.CV_64F).var())

        if variance < 100.0:  # no meaningful detail at any scale
            scale_gain = 1.0
        else:
            scale_gain = variance / (variance_half + 1e-6)
        scale_signal = float(np.clip((scale_gain - 1.0) / 2.5, 0.0, 1.0))

        base_confidence = float(np.clip(variance / 1500.0, 0.0, 1.0))
        confidence = float(np.clip(0.8 * base_confidence + 0.2 * scale_signal, 0.0, 1.0))
        return {
            "confidence": round(confidence, 4),
            "detail": {
                "laplacian_variance": round(variance, 2),
                "scale_gain": round(scale_gain, 3),
            },
            "status": "DETECTED" if confidence > 0.6 else "WEAK",
        }

    def _detect_security_thread(self, img: np.ndarray) -> dict:
        """Look for a tall, thin, dark/metallic vertical band."""
        h, w = img.shape[:2]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        # The windowed thread reads darker than surrounding paper; adaptive threshold
        # then morphological opening with a tall vertical kernel isolates vertical bands.
        binary = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 51, 10
        )
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, max(15, h // 8)))
        vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
        contours, _ = cv2.findContours(vertical, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        best = 0.0
        position = "NOT_FOUND"
        for c in contours:
            x, y, cw, ch = cv2.boundingRect(c)
            if cw == 0:
                continue
            aspect = ch / cw
            height_coverage = ch / h
            if aspect > 4 and height_coverage > 0.35:
                score = min(1.0, 0.5 * min(aspect / 12.0, 1.0) + 0.5 * min(height_coverage / 0.8, 1.0))
                if score > best:
                    best = score
                    position = "VERTICAL"
        return {
            "confidence": round(best, 4),
            "detail": {"position": position, "candidates": len(contours)},
            "status": "DETECTED" if best > 0.5 else "NOT_FOUND",
        }

    def _detect_hologram(self, img: np.ndarray) -> dict:
        """Iridescent patches show locally saturated, hue-diverse regions."""
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        sat = hsv[:, :, 1]
        high_sat = sat > 190
        frac = float(np.count_nonzero(high_sat)) / sat.size
        # Hue diversity inside the saturated region distinguishes iridescence from flat ink.
        hue_diversity = 0.0
        if np.count_nonzero(high_sat) > 100:
            hues = hsv[:, :, 0][high_sat]
            hist, _ = np.histogram(hues, bins=18, range=(0, 180))
            occupied = np.count_nonzero(hist > hues.size * 0.01)
            hue_diversity = occupied / 18.0
        confidence = float(np.clip(min(frac / 0.02, 1.0) * 0.6 + hue_diversity * 0.4, 0.0, 1.0))
        return {
            "confidence": round(confidence, 4),
            "detail": {"saturated_fraction": round(frac, 4), "hue_diversity": round(hue_diversity, 3)},
            "status": "DETECTED" if confidence > 0.5 else "WEAK",
        }

    def _detect_intaglio(self, img: np.ndarray) -> dict:
        """Raised intaglio print produces strong oriented ridges (Gabor response)."""
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        responses = []
        for theta in (0, np.pi / 4, np.pi / 2, 3 * np.pi / 4):
            kernel = cv2.getGaborKernel((21, 21), 3.0, theta, 6.0, 0.5, 0, ktype=cv2.CV_32F)
            filtered = cv2.filter2D(gray.astype(np.float32), cv2.CV_32F, kernel)
            responses.append(float(filtered.std()))
        ridge_strength = max(responses)
        confidence = float(np.clip(ridge_strength / 900.0, 0.0, 1.0))
        return {
            "confidence": round(confidence, 4),
            "detail": {"ridge_strength": round(ridge_strength, 2)},
            "status": "DETECTED" if confidence > 0.5 else "WEAK",
        }

    def _detect_serial_number(self, img: np.ndarray) -> dict:
        """Check the bottom-right serial panel for a row of character-like glyphs.

        OCR-free: counts character-sized contours roughly aligned on a baseline.
        Indian serials are 9 glyphs (e.g. '2AF 067891'); 6+ aligned glyphs scores well.
        """
        h, w = img.shape[:2]
        roi = img[int(h * 0.72):h, int(w * 0.55):w]
        if roi.size == 0:
            return {"confidence": 0.0, "detail": {}, "status": "NOT_FOUND"}
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        binary = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 25, 12
        )
        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        rh = roi.shape[0]
        glyphs = []
        for c in contours:
            x, y, cw, ch = cv2.boundingRect(c)
            if 0.08 * rh < ch < 0.6 * rh and 0.2 < cw / max(ch, 1) < 1.5:
                glyphs.append((x, y, cw, ch))
        aligned = 0
        if len(glyphs) >= 3:
            ys = np.array([g[1] + g[3] / 2 for g in glyphs])
            median_y = float(np.median(ys))
            band = max(4.0, 0.15 * rh)
            aligned = int(np.count_nonzero(np.abs(ys - median_y) < band))
        confidence = float(np.clip(aligned / 8.0, 0.0, 1.0))
        return {
            "confidence": round(confidence, 4),
            "detail": {"glyphs_found": len(glyphs), "aligned_glyphs": aligned},
            "status": "DETECTED" if aligned >= 6 else ("PARTIAL" if aligned >= 3 else "NOT_FOUND"),
        }

    def _detect_texture(self, img: np.ndarray) -> dict:
        """Genuine cotton-rag paper has spatially varied texture; copies are uniform."""
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        stds = []
        for i in range(0, gray.shape[0] - 63, 64):
            for j in range(0, gray.shape[1] - 63, 64):
                stds.append(float(gray[i:i + 64, j:j + 64].std()))
        texture_variance = float(np.var(stds)) if stds else 0.0
        confidence = float(np.clip(texture_variance / 250.0, 0.0, 1.0))
        return {
            "confidence": round(confidence, 4),
            "detail": {"block_std_variance": round(texture_variance, 2), "blocks": len(stds)},
            "status": "GOOD" if confidence > 0.5 else "SUSPICIOUS",
        }

    def _classify_denomination(self, img: np.ndarray) -> str:
        """Dominant-color heuristic: stone grey ₹500, magenta ₹2000, lavender ₹100."""
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        total = img.shape[0] * img.shape[1]

        checks = [
            ("2000", np.array([135, 60, 60]), np.array([170, 255, 255])),   # magenta/pink
            ("500", np.array([10, 40, 60]), np.array([30, 180, 220])),      # stone grey-olive
            ("100", np.array([115, 30, 60]), np.array([135, 160, 230])),    # lavender
            ("200", np.array([15, 100, 100]), np.array([30, 255, 255])),    # bright yellow-orange
        ]
        best_name, best_frac = "UNKNOWN", 0.10  # require >10% coverage
        for name, lo, hi in checks:
            frac = float(np.count_nonzero(cv2.inRange(hsv, lo, hi))) / total
            if frac > best_frac:
                best_name, best_frac = name, frac
        return best_name
