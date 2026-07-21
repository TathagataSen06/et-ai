import numpy as np
import pytest

from app.services.cv_service import CounterfeitDetector, FEATURE_WEIGHTS, ImageDecodeError
from tests.conftest import encode_png, make_genuine_like_note

detector = CounterfeitDetector()


def test_feature_weights_sum_to_one():
    assert abs(sum(FEATURE_WEIGHTS.values()) - 1.0) < 1e-9


def test_flat_image_scores_highly_counterfeit():
    flat = np.full((530, 1200, 3), 128, dtype=np.uint8)
    analysis = detector.analyze(flat)
    assert analysis.counterfeit_score > 0.85


def test_feature_rich_image_scores_lower_than_flat():
    rich = detector.analyze(make_genuine_like_note())
    flat = detector.analyze(np.full((530, 1200, 3), 128, dtype=np.uint8))
    assert rich.counterfeit_score < flat.counterfeit_score - 0.2


def test_all_confidences_bounded():
    analysis = detector.analyze(make_genuine_like_note())
    for name, feature in analysis.features.items():
        assert 0.0 <= feature["confidence"] <= 1.0, name
    assert 0.0 <= analysis.counterfeit_score <= 1.0


def test_security_thread_detected_on_synthetic_note():
    analysis = detector.analyze(make_genuine_like_note())
    assert analysis.features["security_thread"]["confidence"] > 0.4


def test_serial_glyphs_detected():
    analysis = detector.analyze(make_genuine_like_note())
    assert analysis.features["serial_number"]["detail"]["aligned_glyphs"] >= 6


def test_bad_bytes_raise_decode_error():
    with pytest.raises(ImageDecodeError):
        detector.analyze_bytes(b"definitely not an image")


def test_analyze_bytes_roundtrip():
    data = encode_png(make_genuine_like_note())
    analysis = detector.analyze_bytes(data)
    assert set(analysis.features) == set(FEATURE_WEIGHTS)
