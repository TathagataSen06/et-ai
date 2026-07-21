"""Validate the real-note calibration on held-out captures and derived fakes.

Design (capture-level hold-out):
  genuine test  = re-captures of the real notes under field conditions using
                  seeds the calibration never saw
  fake test     = photocopy / inkjet-reprint / screen-redisplay of those same
                  real notes, then camera-captured

Reporting the originals separately matters: they are IN the calibration set,
so their verdicts are in-sample and must not be read as accuracy.

Run from backend/:  python scripts/verify_real_notes.py
"""
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import note_simulation as sim  # noqa: E402
from app.services.cv_service import CounterfeitDetector  # noqa: E402

NOTES_DIR = Path(__file__).parent / "real_notes"
HELD_OUT_SEEDS = range(500, 506)  # disjoint from calibration's augmentation seeds

detector = CounterfeitDetector()
if detector.calibration is None:
    raise SystemExit("No calibration installed — run calibrate_reference.py first")
print(f"calibration: n={detector.calibration.n} source={detector.calibration.source} "
      f"split={detector.calibration.split}\n")


def load_note(path: Path):
    img = cv2.imread(str(path))
    if img is None:
        return None
    h, w = img.shape[:2]
    return cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE) if h > w else img


paths = sorted(p for p in NOTES_DIR.iterdir()
               if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".bmp", ".webp"))
notes = [(p.name, load_note(p)) for p in paths]
notes = [(n, i) for n, i in notes if i is not None]
print(f"{len(notes)} real notes loaded\n")

rows = []
for name, img in notes:
    rows.append(("original (in-sample)", name, False, img))
    for s in HELD_OUT_SEEDS:
        rows.append(("genuine re-capture", name, False,
                     sim.camera_capture(img, seed=s,
                                        exposure=0.85 + 0.3 * ((s % 4) / 3),
                                        blur_sigma=0.5 + 0.5 * ((s % 3) / 2))))
    for s in HELD_OUT_SEEDS:
        rows.append(("photocopy", name, True,
                     sim.camera_capture(sim.photocopy(img), seed=s)))
        rows.append(("inkjet reprint", name, True,
                     sim.camera_capture(sim.inkjet_reprint(img), seed=s + 1)))
        rows.append(("screen re-display", name, True,
                     sim.camera_capture(sim.screen_display(img), seed=s + 2)))

buckets: dict[str, dict] = {}
scores_gen, scores_fake = [], []

for kind, name, is_fake, img in rows:
    a = detector.assess(img)
    b = buckets.setdefault(kind, {"n": 0, "genuine": 0, "susp": 0, "fake": 0, "pcts": []})
    b["n"] += 1
    if a.verdict == "LIKELY_GENUINE":
        b["genuine"] += 1
    elif a.verdict == "SUSPICIOUS":
        b["susp"] += 1
    else:
        b["fake"] += 1
    if a.genuine_percentile is not None:
        b["pcts"].append(a.genuine_percentile)
        (scores_fake if is_fake else scores_gen).append(a.genuine_percentile)

print(f"{'population':22s} {'n':>4s} {'GENUINE':>8s} {'SUSPECT':>8s} {'COUNTERFEIT':>12s}  median pct")
print("-" * 74)
for kind in ("original (in-sample)", "genuine re-capture", "photocopy",
             "inkjet reprint", "screen re-display"):
    b = buckets.get(kind)
    if not b:
        continue
    med = f"{np.median(b['pcts']):.1f}" if b["pcts"] else "n/a"
    print(f"{kind:22s} {b['n']:4d} {b['genuine']:8d} {b['susp']:8d} {b['fake']:12d}  {med:>10s}")

held = buckets.get("genuine re-capture", {"n": 0, "fake": 0, "genuine": 0})
fakes_n = sum(buckets[k]["n"] for k in ("photocopy", "inkjet reprint", "screen re-display")
              if k in buckets)
fakes_flagged = sum(buckets[k]["susp"] + buckets[k]["fake"]
                    for k in ("photocopy", "inkjet reprint", "screen re-display")
                    if k in buckets)
fakes_hard = sum(buckets[k]["fake"]
                 for k in ("photocopy", "inkjet reprint", "screen re-display")
                 if k in buckets)

print("\n" + "=" * 74)
print("HELD-OUT RESULTS (captures the calibration never saw)")
print(f"  genuine never accused : {held['n'] - held['fake']}/{held['n']} "
      f"({(held['n'] - held['fake']) / max(held['n'], 1):.1%})")
print(f"  genuine passed clean  : {held['genuine']}/{held['n']}")
print(f"  fakes flagged         : {fakes_flagged}/{fakes_n} "
      f"({fakes_flagged / max(fakes_n, 1):.1%})")
print(f"  fakes hard-flagged    : {fakes_hard}/{fakes_n}")
if scores_gen and scores_fake:
    auc = sum((a < b) + 0.5 * (a == b) for a in scores_gen for b in scores_fake) / (
        len(scores_gen) * len(scores_fake))
    print(f"  separation AUC        : {auc:.3f}  "
          f"(genuine median pct {np.median(scores_gen):.1f} vs fake {np.median(scores_fake):.1f})")
print("=" * 74)
print(f"NOTE: note-to-note diversity is only {len(notes)} distinct notes. Capture-level "
      f"hold-out is valid;\ngeneralisation to unseen NOTES is untested at this sample size.")
