"""Emit demo note images for exercising the scanner UI.

Writes a genuine capture plus three fake productions (photocopy, inkjet
reprint, screen re-display) to scripts/data/ — upload them on the Scanner
page to see the full verdict range. These come from the same simulation the
conformal reference was calibrated on, so verdicts are meaningful out of the
box (real-note photos route to review until you calibrate with
`calibrate_reference.py --images-dir <photos-of-real-notes>`).

Run from backend/:  python scripts/make_demo_notes.py
"""
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import note_simulation as sim  # noqa: E402

OUT = Path(__file__).parent / "data"
OUT.mkdir(parents=True, exist_ok=True)

note_seed, capture_seed = 4242, 4743
images = {
    "demo_genuine.jpg": sim.reference_capture(note_seed=note_seed, capture_seed=capture_seed),
    "demo_photocopy_fake.jpg": sim.camera_capture(
        sim.photocopy(sim.genuine_note(note_seed + 1)), seed=capture_seed + 1),
    "demo_inkjet_fake.jpg": sim.camera_capture(
        sim.inkjet_reprint(sim.genuine_note(note_seed + 2)), seed=capture_seed + 2),
    "demo_screen_fake.jpg": sim.camera_capture(
        sim.screen_display(sim.genuine_note(note_seed + 3)), seed=capture_seed + 3),
}
for name, img in images.items():
    cv2.imwrite(str(OUT / name), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    print(f"wrote {OUT / name}")
print("\nUpload these on the Scanner page: genuine should pass, "
      "photocopy/screen should be flagged, inkjet routes to review.")
