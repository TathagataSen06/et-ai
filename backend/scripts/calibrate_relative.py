"""Build and validate the reference-relative calibration from real notes.

Copies the supplied genuine notes into the reference bank, calibrates the
conformal reference on *relative* deviations of genuine field captures from
their own reference note, then validates on held-out captures and on fakes
derived from those same notes.

Capture seeds used for calibration and for validation are disjoint, so the
reported numbers are out-of-sample at the capture level. Note-level
generalisation is bounded by how many distinct notes are in the bank.

Run from backend/:  python scripts/calibrate_relative.py --images-dir scripts/real_notes
"""
import argparse
import shutil
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import note_simulation as sim  # noqa: E402
from app.services.calibration import build_calibration  # noqa: E402
from app.services.reference_bank import (  # noqa: E402
    BANK_DIR,
    ReferenceBank,
    absolute_metrics,
    load_note,
    relative_vector,
)

RELATIVE_PATH = Path(__file__).resolve().parents[1] / "app" / "data" / "relative_stats.json"

CAL_SEEDS = range(0, 40)        # calibration capture seeds
# Disjoint from CAL_SEEDS and from the seeds used while tuning metric weights,
# so the reported numbers are not the ones the weights were chosen on.
TEST_SEEDS = range(2000, 2012)


def field_capture(img, seed: int):
    """Genuine note re-photographed under varied field conditions."""
    return sim.camera_capture(
        img, seed=seed,
        exposure=0.80 + 0.40 * ((seed % 5) / 4.0),
        blur_sigma=0.35 + 0.75 * ((seed % 4) / 3.0),
        jpeg_q=76 + 7 * (seed % 4),
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--images-dir", type=Path, default=Path("scripts/real_notes"))
    ap.add_argument("--out", type=Path, default=RELATIVE_PATH)
    args = ap.parse_args()

    paths = sorted(p for p in args.images_dir.iterdir()
                   if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".bmp", ".webp"))
    if not paths:
        raise SystemExit(f"No images in {args.images_dir}")

    # --- populate the reference bank -------------------------------------
    BANK_DIR.mkdir(parents=True, exist_ok=True)
    for old in BANK_DIR.iterdir():
        old.unlink()
    for p in paths:
        shutil.copy2(p, BANK_DIR / p.name)
    bank = ReferenceBank.from_dir()
    if bank is None:
        raise SystemExit("Reference bank failed to load")
    print(f"reference bank: {len(bank.notes)} genuine notes\n")

    notes = [(p.name, load_note(p)) for p in paths]
    notes = [(n, i) for n, i in notes if i is not None]

    # --- calibration set: genuine captures vs their own reference ---------
    cal_vectors = []
    for name, img in notes:
        for s in CAL_SEEDS:
            shot = field_capture(img, s)
            match = bank.match(shot)
            if match is None:
                continue
            cal_vectors.append(relative_vector(absolute_metrics(shot), match.reference.metrics))
    print(f"calibration vectors: {len(cal_vectors)}")

    calibration = build_calibration(cal_vectors, source=f"relative:{args.images_dir.name}")
    calibration.save(args.out)
    print(f"written to {args.out}  (n={calibration.n}, split={calibration.split})\n")

    # --- validation -------------------------------------------------------
    def evaluate(img, transform=None, seeds=TEST_SEEDS):
        out = []
        for s in seeds:
            base = transform(img) if transform else img
            shot = field_capture(base, s) if transform is None else sim.camera_capture(base, seed=s)
            match = bank.match(shot)
            if match is None:
                out.append(None)
                continue
            vec = relative_vector(absolute_metrics(shot), match.reference.metrics)
            out.append(calibration.p_value(calibration.nonconformity(vec)))
        return out

    groups = {
        "genuine re-capture": (None, []),
        "photocopy": (sim.photocopy, []),
        "inkjet reprint": (sim.inkjet_reprint, []),
        "screen re-display": (sim.screen_display, []),
    }
    for name, img in notes:
        for label, (tf, acc) in groups.items():
            acc.extend(p for p in evaluate(img, tf) if p is not None)

    print(f"{'population':22s} {'n':>4s} {'GENUINE':>8s} {'REVIEW':>7s} {'COUNTERFEIT':>12s} {'med p':>8s}")
    print("-" * 68)
    summary = {}
    for label, (_, ps) in groups.items():
        gen = sum(1 for p in ps if p > 0.10)
        rev = sum(1 for p in ps if 0.01 < p <= 0.10)
        fake = sum(1 for p in ps if p <= 0.01)
        summary[label] = (len(ps), gen, rev, fake)
        print(f"{label:22s} {len(ps):4d} {gen:8d} {rev:7d} {fake:12d} {np.median(ps):8.4f}")

    g_ps = groups["genuine re-capture"][1]
    f_ps = [p for k in ("photocopy", "inkjet reprint", "screen re-display") for p in groups[k][1]]
    n_g, _, _, g_fake = summary["genuine re-capture"]
    n_f = len(f_ps)
    flagged = sum(1 for p in f_ps if p <= 0.10)
    hard = sum(1 for p in f_ps if p <= 0.01)
    auc = (sum((a > b) + 0.5 * (a == b) for a in g_ps for b in f_ps)
           / (len(g_ps) * len(f_ps))) if g_ps and f_ps else float("nan")

    print("\n" + "=" * 68)
    print("HELD-OUT RESULTS (capture seeds unseen by the calibration)")
    print(f"  genuine never accused : {n_g - g_fake}/{n_g} ({(n_g - g_fake)/max(n_g,1):.1%})")
    print(f"  fakes flagged         : {flagged}/{n_f} ({flagged/max(n_f,1):.1%})")
    print(f"  fakes hard-flagged    : {hard}/{n_f} ({hard/max(n_f,1):.1%})")
    print(f"  separation AUC        : {auc:.3f}")
    print("=" * 68)
    print(f"note-level diversity: {len(notes)} distinct notes")


if __name__ == "__main__":
    main()
