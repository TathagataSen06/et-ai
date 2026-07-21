"""Evidence-media authenticity screening (spec: citizen media verification).

Pure-OpenCV image forensics for citizen-submitted evidence photos:

- Error Level Analysis (ELA): recompress as JPEG and measure residual differences.
  Spliced/edited regions recompress differently from the rest of the image.
- Noise-inconsistency: sensor noise should be roughly uniform; pasted regions
  show block-level noise variance out of line with the rest.

Screening heuristics only — video/voice deepfake detection (MediaPipe/TFLite in
the full spec) would plug in behind the same endpoint.
"""
import cv2
import numpy as np

from app.services.cv_service import ImageDecodeError

ANALYSIS_WIDTH = 1024


class MediaForensics:
    def verify_bytes(self, data: bytes) -> dict:
        arr = np.frombuffer(data, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            raise ImageDecodeError("Could not decode image data")

        h, w = img.shape[:2]
        if w > ANALYSIS_WIDTH:
            scale = ANALYSIS_WIDTH / w
            img = cv2.resize(img, (ANALYSIS_WIDTH, max(1, int(h * scale))),
                             interpolation=cv2.INTER_AREA)

        ela = self._ela_score(img)
        noise = self._noise_inconsistency(img)
        tamper_score = round(min(1.0, ela["score"] * 0.5 + noise["score"] * 0.5), 3)

        verdict = (
            "LIKELY_TAMPERED" if tamper_score > 0.65
            else "SUSPICIOUS" if tamper_score > 0.4
            else "NO_TAMPER_INDICATORS"
        )
        return {
            "tamper_score": tamper_score,
            "verdict": verdict,
            "ela": ela,
            "noise": noise,
            "disclaimer": (
                "Heuristic screening of still images only; not forensic proof. "
                "Video/voice deepfake analysis requires the full media pipeline."
            ),
        }

    @staticmethod
    def _ela_score(img: np.ndarray) -> dict:
        """Recompress at quality 90; localized high residuals suggest edits."""
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if not ok:
            return {"score": 0.0, "detail": "encode failed"}
        recompressed = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        residual = cv2.absdiff(img, recompressed).astype(np.float32).mean(axis=2)

        # Uniform residual = normal; concentrated hotspots = suspicious.
        mean = float(residual.mean())
        block = 32
        block_means = [
            float(residual[i:i + block, j:j + block].mean())
            for i in range(0, residual.shape[0] - block + 1, block)
            for j in range(0, residual.shape[1] - block + 1, block)
        ]
        if not block_means or mean < 1e-6:
            return {"score": 0.0, "mean_residual": round(mean, 3), "hotspot_ratio": 0.0}
        hotspot_ratio = max(block_means) / (mean + 1e-6)
        score = float(np.clip((hotspot_ratio - 3.0) / 7.0, 0.0, 1.0))
        return {
            "score": round(score, 3),
            "mean_residual": round(mean, 3),
            "hotspot_ratio": round(hotspot_ratio, 2),
        }

    @staticmethod
    def _noise_inconsistency(img: np.ndarray) -> dict:
        """Block-level noise variance; pasted content breaks noise uniformity."""
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
        noise = gray - cv2.medianBlur(gray.astype(np.uint8), 3).astype(np.float32)

        block = 64
        stds = [
            float(noise[i:i + block, j:j + block].std())
            for i in range(0, noise.shape[0] - block + 1, block)
            for j in range(0, noise.shape[1] - block + 1, block)
        ]
        if len(stds) < 4:
            return {"score": 0.0, "detail": "image too small"}
        # Genuine sensor noise is roughly uniform across blocks; pasted or
        # synthetic regions collapse toward zero noise, so a large high/low
        # percentile ratio indicates mixed sources.
        p95 = float(np.percentile(stds, 95))
        p5 = float(np.percentile(stds, 5))
        ratio = (p95 + 0.1) / (p5 + 0.1)
        score = float(np.clip((ratio - 3.0) / 17.0, 0.0, 1.0))
        return {"score": round(score, 3), "percentile_ratio": round(ratio, 2)}
