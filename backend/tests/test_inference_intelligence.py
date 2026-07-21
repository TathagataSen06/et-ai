"""Tests for the training-free inference-time intelligence layer."""
from datetime import datetime, timedelta, timezone

import cv2
import numpy as np

from app.models.orm import Seizure
from app.services.cv_service import CounterfeitDetector, MAX_ADAPTIVE_SHIFT
from app.services.geospatial_service import GeospatialIntelligence
from tests.conftest import encode_png, make_genuine_like_note

detector = CounterfeitDetector()


# ---------- adaptive-margin verdict (pure logic) ----------

def test_verdict_crisp_scores_use_base_thresholds():
    verdict, _, thr = CounterfeitDetector.resolve_verdict(0.80, 0.0)
    assert verdict == "LIKELY_COUNTERFEIT"
    assert thr["adaptive_shift"] == 0.0

    verdict, _, _ = CounterfeitDetector.resolve_verdict(0.49, 0.0)
    assert verdict == "LIKELY_GENUINE"

    verdict, reason, _ = CounterfeitDetector.resolve_verdict(0.60, 0.0)
    assert verdict == "SUSPICIOUS"
    assert reason == "score inside the suspicious band"


def test_verdict_uncertainty_widens_review_band():
    # 0.80 is counterfeit when stable...
    assert CounterfeitDetector.resolve_verdict(0.80, 0.0)[0] == "LIKELY_COUNTERFEIT"
    # ...but only SUSPICIOUS when the ensemble disagrees (shift 1.5*0.08 = 0.12)
    verdict, reason, thr = CounterfeitDetector.resolve_verdict(0.80, 0.08)
    assert verdict == "SUSPICIOUS"
    assert "instability" in reason
    assert thr["counterfeit_above"] > 0.80

    # Same on the genuine side: 0.45 flips from genuine to review
    assert CounterfeitDetector.resolve_verdict(0.45, 0.0)[0] == "LIKELY_GENUINE"
    assert CounterfeitDetector.resolve_verdict(0.45, 0.08)[0] == "SUSPICIOUS"


def test_verdict_adaptive_shift_is_capped():
    _, _, thr = CounterfeitDetector.resolve_verdict(0.95, 0.5)
    assert thr["adaptive_shift"] == MAX_ADAPTIVE_SHIFT
    # Even maximal widening cannot rescue a 0.95 score
    assert CounterfeitDetector.resolve_verdict(0.95, 0.5)[0] == "LIKELY_COUNTERFEIT"


# ---------- two-tier screening + consensus ensemble ----------

def test_flat_image_settles_on_fast_path():
    flat = np.full((530, 1200, 3), 128, dtype=np.uint8)
    assessment = detector.assess(flat)
    assert assessment.mode == "fast"
    assert assessment.uncertainty == 0.0
    assert assessment.verdict == "LIKELY_COUNTERFEIT"
    assert len(assessment.ensemble_scores) == 1


def test_borderline_image_escalates_to_consensus():
    # A degraded genuine-like note lands between the fast gates (~0.38-0.41).
    note = cv2.GaussianBlur(make_genuine_like_note(), (13, 13), 0)
    base_score = detector.analyze(note).counterfeit_score
    assert 0.35 < base_score < 0.90, f"fixture drifted out of the borderline band: {base_score}"

    assessment = detector.assess(note)
    assert assessment.mode == "consensus"
    assert len(assessment.ensemble_scores) >= 5
    assert 0.0 <= assessment.uncertainty <= 0.5
    assert set(assessment.features) == {
        "microprint", "security_thread", "hologram", "intaglio",
        "serial_number", "paper_texture",
    }
    # Ensemble metadata is attached to every feature
    for feature in assessment.features.values():
        assert "ensemble_std" in feature["detail"]


# ---------- reliability-weighted fusion ----------

def test_blown_highlights_reduce_hologram_reliability():
    note = make_genuine_like_note()
    overexposed = np.clip(note.astype(np.int16) + 170, 0, 255).astype(np.uint8)
    analysis = detector.analyze(overexposed)
    assert analysis.features["hologram"]["detail"]["reliability"] < 1.0
    # Effective weights always renormalize to ~1
    total = sum(f["detail"]["effective_weight"] for f in analysis.features.values())
    assert abs(total - 1.0) < 0.01


def test_low_resolution_reduces_fine_detail_reliability():
    note = make_genuine_like_note()
    tiny = cv2.resize(note, (350, 155), interpolation=cv2.INTER_AREA)
    analysis = detector.analyze(tiny)
    assert analysis.features["microprint"]["detail"]["reliability"] == 0.5
    assert analysis.features["intaglio"]["detail"]["reliability"] == 0.5
    # Exposure-based features keep full reliability
    assert analysis.features["security_thread"]["detail"]["reliability"] == 1.0


def test_clean_capture_keeps_full_reliability():
    flat = np.full((530, 1200, 3), 128, dtype=np.uint8)
    analysis = detector.analyze(flat)
    assert all(
        f["detail"]["reliability"] == 1.0 for f in analysis.features.values()
    )
    # Behavior identical to the unweighted detector on clean captures
    assert analysis.counterfeit_score > 0.85


# ---------- multi-scale detail gain ----------

def test_genuine_fine_print_gains_detail_across_scales():
    note = make_genuine_like_note()
    analysis = detector.analyze(note)
    assert analysis.features["microprint"]["detail"]["scale_gain"] > 1.0


def test_flat_image_has_neutral_scale_gain():
    flat = np.full((530, 1200, 3), 128, dtype=np.uint8)
    analysis = detector.analyze(flat)
    assert analysis.features["microprint"]["detail"]["scale_gain"] == 1.0


# ---------- API surface ----------

def test_analyze_response_reports_confidence_internals(client):
    flat = np.full((530, 1200, 3), 128, dtype=np.uint8)
    body = client.post(
        "/api/v1/scanner/analyze",
        files={"file": ("note.png", encode_png(flat), "image/png")},
    ).json()
    assert body["analysis_mode"] == "fast"
    assert body["uncertainty"] == 0.0
    assert body["verdict_reason"]
    assert "counterfeit_above" in body["effective_thresholds"]


# ---------- consensus clustering stability ----------

def _seizure(lat, lon):
    return Seizure(
        seizure_date=datetime.now(timezone.utc) - timedelta(days=1),
        lat=lat, lon=lon, denomination="500", quantity=100,
        counterfeit_confidence=0.9,
    )


def test_tight_cluster_is_fully_stable(db):
    base = (19.0760, 72.8777)
    for i in range(6):  # points within ~1.5 km — survives every eps perturbation
        db.add(_seizure(base[0] + i * 0.002, base[1] + i * 0.002))
    db.commit()
    clusters = GeospatialIntelligence(db).update_hotspots()
    assert len(clusters) == 1
    assert clusters[0].stability == 1.0


def test_borderline_chain_cluster_is_fragile(db):
    # Chain spaced ~1.9 km: clusters at eps 2.0 and 2.5, dissolves at eps 1.5.
    step = 1.9 / 111.0
    for i in range(4):
        db.add(_seizure(19.0 + i * step, 72.8))
    db.commit()
    clusters = GeospatialIntelligence(db).update_hotspots()
    assert len(clusters) == 1
    assert 0.3 <= clusters[0].stability <= 0.7  # persists in 1 of 2 perturbed runs
