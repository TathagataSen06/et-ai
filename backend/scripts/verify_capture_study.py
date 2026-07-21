"""Accuracy battery for the conformally calibrated scanner.

Runs a battery of unseen genuine captures (varied field conditions) and three
fake-production methods against the calibrated detector, then reports:

- decision accuracy   (genuine -> LIKELY_GENUINE, fake -> flagged)
- safety accuracy     (no genuine capture ever LIKELY_COUNTERFEIT)
- detection rate      (every fake at least SUSPICIOUS)

The conformal reference guarantees <=1% false-accusation on genuine captures
from the reference distribution; this battery measures everything else.

Run from backend/:  python scripts/verify_capture_study.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import note_simulation as sim  # noqa: E402
from app.services.cv_service import CounterfeitDetector  # noqa: E402

detector = CounterfeitDetector()
if detector.calibration is None:
    raise SystemExit("No reference calibration — run scripts/calibrate_reference.py first")

BATTERY: list[tuple[str, bool, object]] = []  # (label, is_fake, image)

# 12 genuine captures at unseen seeds across the field-condition envelope
for i, seed in enumerate(range(9000, 9012)):
    BATTERY.append((f"genuine #{i + 1}", False,
                    sim.reference_capture(note_seed=seed, capture_seed=seed + 501)))

# 12 fakes: three production methods x four different source notes
for i, seed in enumerate(range(9100, 9104)):
    note = sim.genuine_note(seed)
    BATTERY.append((f"photocopy #{i + 1}", True,
                    sim.camera_capture(sim.photocopy(note), seed=seed)))
    BATTERY.append((f"inkjet reprint #{i + 1}", True,
                    sim.camera_capture(sim.inkjet_reprint(note), seed=seed + 1)))
    BATTERY.append((f"screen display #{i + 1}", True,
                    sim.camera_capture(sim.screen_display(note), seed=seed + 2)))

print(f"{'case':22s} {'truth':8s} {'verdict':20s} {'pct':7s} {'score':>6s} {'mode':10s}")
print("-" * 84)

strict_correct = 0
false_accusations = 0
missed_fakes = 0
hard_flagged_fakes = 0
n_genuine = n_fake = 0

for label, is_fake, img in BATTERY:
    a = detector.assess(img)
    pct = f"{a.genuine_percentile:.1f}" if a.genuine_percentile is not None else "-"
    print(f"{label:22s} {'FAKE' if is_fake else 'genuine':8s} {a.verdict:20s} "
          f"{pct:7s} {a.counterfeit_score:6.3f} {a.mode:10s}")

    if is_fake:
        n_fake += 1
        if a.verdict in ("SUSPICIOUS", "LIKELY_COUNTERFEIT"):
            strict_correct += 1
            if a.verdict == "LIKELY_COUNTERFEIT":
                hard_flagged_fakes += 1
        else:
            missed_fakes += 1
    else:
        n_genuine += 1
        if a.verdict == "LIKELY_GENUINE":
            strict_correct += 1
        if a.verdict == "LIKELY_COUNTERFEIT":
            false_accusations += 1

total = n_genuine + n_fake
print("\n" + "=" * 84)
print(f"decision accuracy : {strict_correct}/{total} "
      f"({100 * strict_correct / total:.1f}%)  "
      f"[genuine->GENUINE, fake->flagged]")
print(f"safety            : {n_genuine - false_accusations}/{n_genuine} genuine never "
      f"accused ({100 * (n_genuine - false_accusations) / n_genuine:.1f}%)")
print(f"detection rate    : {n_fake - missed_fakes}/{n_fake} fakes flagged "
      f"({100 * (n_fake - missed_fakes) / n_fake:.1f}%)")
print(f"hard flags        : {hard_flagged_fakes}/{n_fake} fakes LIKELY_COUNTERFEIT outright")
print(f"reference         : n={detector.calibration.n} source={detector.calibration.source}")

ok = false_accusations == 0 and missed_fakes == 0
print("\nALL BATTERY REQUIREMENTS MET" if ok else "\nBATTERY REQUIREMENTS NOT MET")
sys.exit(0 if ok else 1)
