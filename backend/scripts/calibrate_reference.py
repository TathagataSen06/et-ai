"""Build the genuine-reference calibration for conformal verdicts.

Default: synthesize a diverse population of genuine captures (varied notes,
exposure, blur, resolution, compression) and calibrate against it.

Production: photograph 100+ REAL genuine notes under normal field conditions
and run  `python scripts/calibrate_reference.py --images-dir path/to/photos`.
That re-anchors every verdict to real currency instead of the simulation.

Run from backend/:  python scripts/calibrate_reference.py
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2  # noqa: E402

from app.services import note_simulation  # noqa: E402
from app.services.calibration import (  # noqa: E402
    REFERENCE_PATH,
    build_calibration,
    measurement_vector,
)
from app.services.cv_service import CounterfeitDetector  # noqa: E402

detector = CounterfeitDetector()


def vector_from_image(img) -> dict:
    analysis = detector.analyze(img)
    return measurement_vector(analysis.features, analysis.capture_stats)


def synthetic_vectors(count: int) -> list[dict]:
    vectors = []
    for i in range(count):
        img = note_simulation.reference_capture(note_seed=i, capture_seed=1000 + i)
        vectors.append(vector_from_image(img))
        if (i + 1) % 25 == 0:
            print(f"  {i + 1}/{count} reference captures analyzed")
    return vectors


def image_dir_vectors(directory: Path) -> list[dict]:
    paths = sorted(
        p for p in directory.iterdir()
        if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".bmp", ".webp")
    )
    if not paths:
        raise SystemExit(f"No images found in {directory}")
    vectors = []
    for i, path in enumerate(paths):
        img = cv2.imread(str(path))
        if img is None:
            print(f"  skipping unreadable {path.name}")
            continue
        vectors.append(vector_from_image(img))
        if (i + 1) % 25 == 0:
            print(f"  {i + 1}/{len(paths)} photos analyzed")
    return vectors


def main() -> None:
    parser = argparse.ArgumentParser(description="Calibrate the genuine reference")
    parser.add_argument("--images-dir", type=Path, default=None,
                        help="Directory of REAL genuine-note photos (production path)")
    parser.add_argument("--count", type=int, default=200,
                        help="Synthetic reference size (default 200)")
    parser.add_argument("--out", type=Path, default=REFERENCE_PATH)
    args = parser.parse_args()

    if args.images_dir:
        print(f"Calibrating from real photos in {args.images_dir} ...")
        vectors = image_dir_vectors(args.images_dir)
        source = f"user-photos:{args.images_dir.name}"
    else:
        print(f"Calibrating from {args.count} synthetic genuine captures ...")
        vectors = synthetic_vectors(args.count)
        source = "synthetic-simulation"

    calibration = build_calibration(vectors, source=source)
    calibration.save(args.out)

    scores = calibration.scores
    print(f"\nReference written to {args.out}")
    print(f"  n={calibration.n}  source={calibration.source}  "
          f"proper split-conformal={calibration.split}")
    print(f"  held-out scores: {len(scores)}  "
          f"(p-value floor {1 / (len(scores) + 1):.4f})")
    print(f"  nonconformity range: {scores[0]:.3f} .. {scores[-1]:.3f} "
          f"(median {scores[len(scores) // 2]:.3f})")
    if not calibration.split:
        print("  WARNING: sample too small for a proper split — p-values are "
              "slightly optimistic. Use 100+ photos for the guarantee.")


if __name__ == "__main__":
    main()
