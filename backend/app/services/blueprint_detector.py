"""Blueprint counterfeit engine: alignment -> ORB template match -> OCR serial.

Implements the ensemble described in the technical blueprint:

  Step A  perspective-align the note                (note_alignment.py)
  Step B  ORB feature match against a golden image  (structural fidelity)
  Step C  OCR the serial panel + format validation  (repeat-serial detection)

Scoring is a weighted combination of the three signals. Tesseract is optional
at runtime: when the binary is absent the OCR term is dropped and the
remaining weights are renormalised, so a clone-and-run install still works.
"""
import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache

import cv2
import numpy as np

from app.services.note_alignment import align_note

logger = logging.getLogger(__name__)

# Indian serial format: 3 alphanumerics (prefix) + 6 digits, e.g. "2AF 067891".
SERIAL_RE = re.compile(r"^[0-9A-Z]{3}\s?\d{6}$")

WEIGHTS = {"template": 0.55, "serial": 0.20, "alignment": 0.25}

# A blacklist of serials seen on confirmed fakes. Real deployments would sync
# this from a law-enforcement feed; there is no public RBI serial API, so it
# stays a locally-managed set and is empty by default.
SERIAL_BLACKLIST: set[str] = set()


@dataclass
class BlueprintResult:
    counterfeit_score: float
    verdict: str
    template_score: float
    serial_number: str | None
    serial_valid: bool
    serial_blacklisted: bool
    aligned: bool
    ocr_available: bool
    reasons: list[str] = field(default_factory=list)
    features: dict = field(default_factory=dict)


@lru_cache(maxsize=1)
def _tesseract_available() -> bool:
    try:
        import pytesseract

        pytesseract.get_tesseract_version()
        return True
    except Exception:
        logger.info("Tesseract binary not available; OCR term disabled")
        return False


@lru_cache(maxsize=4)
def _reference_note(seed: int = 0) -> np.ndarray:
    """Golden image for ORB matching.

    Synthetic today — swap for a scan of a genuine note per denomination when
    reference imagery is available; nothing else in the pipeline changes.
    """
    from app.services import note_simulation as sim

    ref = sim.genuine_note(seed)
    return cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY)


class BlueprintDetector:
    """Step B + C engine. Stateless; safe to share across requests."""

    def __init__(self, nfeatures: int = 2000) -> None:
        self._orb = cv2.ORB_create(nfeatures=nfeatures)
        self._matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)

    # ---------------------------------------------------------------- Step B
    def template_match(self, note_gray: np.ndarray) -> tuple[float, dict]:
        """Fraction of ORB descriptors matching the reference within Hamming 50."""
        reference = _reference_note()
        kp1, des1 = self._orb.detectAndCompute(note_gray, None)
        kp2, des2 = self._orb.detectAndCompute(reference, None)
        # detectAndCompute returns None on featureless input; BFMatcher would
        # raise on that, so bail out to a zero score instead.
        if des1 is None or des2 is None or len(des1) < 2 or len(des2) < 2:
            return 0.0, {"keypoints": 0, "matches": 0, "good_matches": 0}

        matches = self._matcher.match(des1, des2)
        if not matches:
            return 0.0, {"keypoints": len(kp1), "matches": 0, "good_matches": 0}
        good = [m for m in matches if m.distance < 50]
        # Normalise by the reference keypoint count, not by len(matches):
        # crossCheck already filters, so matches/matches would trend to 1.0
        # even for a near-featureless crop.
        score = float(np.clip(len(good) / max(len(kp2), 1), 0.0, 1.0))
        return score, {
            "keypoints": len(kp1),
            "reference_keypoints": len(kp2),
            "matches": len(matches),
            "good_matches": len(good),
            "mean_distance": float(np.mean([m.distance for m in matches])),
        }

    # ---------------------------------------------------------------- Step C
    def extract_serial(self, note_bgr: np.ndarray) -> tuple[str | None, dict]:
        """OCR the serial panel. Indian notes carry it in the BOTTOM-right."""
        if not _tesseract_available():
            return None, {"ocr": "unavailable"}
        import pytesseract

        h, w = note_bgr.shape[:2]
        roi = note_bgr[int(h * 0.78):h, int(w * 0.60):w]
        if roi.size == 0:
            return None, {"ocr": "empty_roi"}
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC)
        thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)[1]
        try:
            raw = pytesseract.image_to_string(
                thresh,
                config="--psm 7 -c tessedit_char_whitelist="
                       "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ",
            )
        except Exception:
            logger.exception("OCR failed")
            return None, {"ocr": "error"}
        text = re.sub(r"[^A-Z0-9 ]", "", raw.upper()).strip()
        return (text or None), {"ocr": "ok", "raw": text[:32]}

    @staticmethod
    def validate_serial(serial: str | None) -> bool:
        return bool(serial and SERIAL_RE.match(serial.strip()))

    # ---------------------------------------------------------------- verdict
    @staticmethod
    def _already_note_shaped(img: np.ndarray) -> bool:
        """True when the frame is itself a cropped note (no border to find).

        Such captures must not be penalised for failing alignment — there is
        simply nothing to warp.
        """
        h, w = img.shape[:2]
        aspect = w / h if h else 0.0
        return 1.6 <= aspect <= 3.2

    def analyze(self, img: np.ndarray) -> BlueprintResult:
        flat, aligned = align_note(img)
        pre_cropped = not aligned and self._already_note_shaped(img)
        gray = cv2.cvtColor(flat, cv2.COLOR_BGR2GRAY)

        template_score, template_detail = self.template_match(gray)
        serial, serial_detail = self.extract_serial(flat)
        serial_valid = self.validate_serial(serial)
        blacklisted = bool(serial and serial.replace(" ", "") in SERIAL_BLACKLIST)

        ocr_on = _tesseract_available()
        weights = dict(WEIGHTS)
        if not ocr_on:
            weights.pop("serial")
            total = sum(weights.values())
            weights = {k: v / total for k, v in weights.items()}

        # Each term is an authenticity score in [0,1]; counterfeit = 1 - auth.
        align_term = 1.0 if (aligned or pre_cropped) else 0.35
        authenticity = template_score * weights["template"]
        authenticity += align_term * weights["alignment"]
        if ocr_on:
            authenticity += (1.0 if serial_valid else 0.25) * weights["serial"]

        score = float(np.clip(1.0 - authenticity, 0.0, 1.0))
        reasons: list[str] = []
        if blacklisted:
            score = 1.0
            reasons.append(f"Serial number {serial} is blacklisted")
        if not aligned and not pre_cropped:
            reasons.append("Note outline not found — capture flat, filling the frame")
        if template_score < 0.25:
            reasons.append(f"Structural match to reference is low ({template_score:.0%})")
        if ocr_on and not serial_valid:
            reasons.append("Serial number unreadable or malformed")

        verdict = (
            "LIKELY_COUNTERFEIT" if score > 0.75
            else "SUSPICIOUS" if score > 0.50
            else "LIKELY_GENUINE"
        )
        return BlueprintResult(
            counterfeit_score=round(score, 4),
            verdict=verdict,
            template_score=round(template_score, 4),
            serial_number=serial,
            serial_valid=serial_valid,
            serial_blacklisted=blacklisted,
            aligned=aligned,
            ocr_available=ocr_on,
            reasons=reasons,
            features={
                "template_match": {
                    "confidence": round(template_score, 4),
                    "detail": template_detail,
                    "status": "MATCH" if template_score > 0.25 else "WEAK",
                },
                "alignment": {
                    "confidence": align_term,
                    "detail": {"aligned": aligned, "pre_cropped": pre_cropped},
                    "status": ("ALIGNED" if aligned
                               else "PRE_CROPPED" if pre_cropped else "NOT_FOUND"),
                },
                "serial_number": {
                    "confidence": 1.0 if serial_valid else (0.25 if ocr_on else 0.0),
                    "detail": {**serial_detail, "serial": serial, "valid": serial_valid,
                               "blacklisted": blacklisted},
                    "status": ("BLACKLISTED" if blacklisted
                               else "VALID" if serial_valid
                               else "UNREADABLE" if ocr_on else "OCR_UNAVAILABLE"),
                },
            },
        )
