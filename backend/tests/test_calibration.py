"""Conformal reference calibration tests."""
import numpy as np

from app.services import note_simulation
from app.services.calibration import (
    COUNTERFEIT_P,
    REVIEW_P,
    ReferenceCalibration,
    build_calibration,
    load_calibration,
    measurement_vector,
)
from app.services.cv_service import CounterfeitDetector

detector = CounterfeitDetector()


def _toy_calibration() -> ReferenceCalibration:
    # 220 synthetic genuine vectors around known centers (deterministic).
    # Sized so the held-out score pool's p-value floor sits below COUNTERFEIT_P.
    rng = np.random.default_rng(0)
    vectors = [
        {
            "microprint.laplacian_variance": float(rng.normal(1000, 120)),
            "capture.raster_periodicity": float(rng.normal(20, 4)),
            "capture.brightness_mean": float(rng.normal(170, 12)),
        }
        for _ in range(220)
    ]
    return build_calibration(vectors, source="toy")


def test_nonconformity_directions():
    cal = _toy_calibration()
    typical = {"microprint.laplacian_variance": 1000.0,
               "capture.raster_periodicity": 20.0,
               "capture.brightness_mean": 170.0}
    deficit = {**typical, "microprint.laplacian_variance": 100.0}   # structure missing
    surplus = {**typical, "microprint.laplacian_variance": 3000.0}  # extra detail = fine
    rastered = {**typical, "capture.raster_periodicity": 90.0}      # halftone = evidence

    assert cal.nonconformity(typical) < 0.5
    assert cal.nonconformity(deficit) > cal.nonconformity(typical)
    # Deficit direction only: more structure than reference is NOT evidence
    assert cal.nonconformity(surplus) <= cal.nonconformity(typical) + 0.05
    assert cal.nonconformity(rastered) > cal.nonconformity(typical)


def test_p_value_monotone_and_bounded():
    cal = _toy_calibration()
    p_low = cal.p_value(0.0)     # perfectly typical
    p_high = cal.p_value(50.0)   # wildly anomalous
    assert p_low > 0.9
    assert p_high == cal.p_value(999.0)  # saturates at the conformal floor
    assert 0 < p_high <= 1 / (len(cal.scores) + 1) + 1e-4  # p_value rounds to 4 dp
    assert p_high < COUNTERFEIT_P < REVIEW_P < p_low


def test_shipped_reference_is_valid_split_conformal():
    cal = load_calibration()
    assert cal is not None, "reference_stats.json missing — run scripts/calibrate_reference.py"
    assert cal.split, "shipped reference must be a proper split-conformal build"
    assert len(cal.scores) >= 100
    # The p-value floor must make the counterfeit band reachable
    assert 1 / (len(cal.scores) + 1) <= COUNTERFEIT_P
    assert set(cal.stats) >= {"microprint.laplacian_variance", "capture.raster_periodicity"}


def test_flat_image_condemned_by_conformal():
    flat = np.full((530, 1200, 3), 128, dtype=np.uint8)
    a = detector.assess(flat)
    assert a.calibrated
    assert a.verdict == "LIKELY_COUNTERFEIT"
    assert a.genuine_percentile is not None and a.genuine_percentile > 99


def test_unseen_genuine_capture_not_accused():
    """Conformal guarantee check: fresh genuine captures (seeds outside the
    reference set) must not be flagged LIKELY_COUNTERFEIT."""
    for seed in (5001, 5002, 5003):
        img = note_simulation.reference_capture(note_seed=seed, capture_seed=seed + 77)
        a = detector.assess(img)
        assert a.calibrated
        assert a.verdict != "LIKELY_COUNTERFEIT", (
            f"false accusation on genuine capture seed={seed}, "
            f"percentile={a.genuine_percentile}"
        )


def test_photocopy_condemned_screen_flagged():
    note = note_simulation.genuine_note(seed=6001)
    copy = note_simulation.camera_capture(note_simulation.photocopy(note), seed=42)
    a = detector.assess(copy)
    assert a.calibrated
    assert a.verdict in ("LIKELY_COUNTERFEIT", "SUSPICIOUS")

    screen = note_simulation.camera_capture(note_simulation.screen_display(note), seed=43)
    b = detector.assess(screen)
    assert b.verdict in ("LIKELY_COUNTERFEIT", "SUSPICIOUS")


def test_measurement_vector_extraction():
    img = note_simulation.reference_capture(note_seed=1, capture_seed=2)
    analysis = detector.analyze(img)
    vec = measurement_vector(analysis.features, analysis.capture_stats)
    assert "microprint.laplacian_variance" in vec
    assert "capture.raster_periodicity" in vec
    assert all(isinstance(v, float) for v in vec.values())
