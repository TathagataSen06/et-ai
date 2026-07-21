"""Synthetic note + capture simulation (calibration & verification tooling).

Produces print-like genuine notes, realistic phone-capture pipelines, and the
common fake-production methods (photocopy, inkjet reprint, screen display).
Used by scripts/calibrate_reference.py to build the default reference
population and by scripts/verify_capture_study.py for the accuracy battery.
"""
import cv2
import numpy as np

W, H = 1300, 574


def genuine_note(seed: int = 0) -> np.ndarray:
    """Print-like genuine note: textured cotton paper + fine security features."""
    rng = np.random.default_rng(seed)
    img = np.full((H, W, 3), (196, 203, 208), dtype=np.uint8)

    # Cotton-rag texture: spatially varying noise amplitude
    for i in range(0, H, 48):
        for j in range(0, W, 48):
            amp = rng.integers(4, 34)
            block = rng.normal(0, amp, (min(48, H - i), min(48, W - j), 3))
            region = img[i:i + 48, j:j + 48].astype(np.int16) + block.astype(np.int16)
            img[i:i + 48, j:j + 48] = np.clip(region, 0, 255).astype(np.uint8)

    # Guilloche-style engraving line-work (intaglio-like ridges)
    xs = np.arange(W)
    for k in range(40):
        ys = (H / 2 + (H / 3) * np.sin(xs / (18 + k) + k + seed * 0.13)).astype(int) % H
        img[ys, xs] = np.clip(img[ys, xs].astype(np.int16) - 55, 0, 255).astype(np.uint8)

    # Microprint band: fine 2px-period line field
    for y in range(60, 180, 2):
        img[y, 100:600] = np.clip(img[y, 100:600].astype(np.int16) - 45, 0, 255).astype(np.uint8)

    # Windowed security thread (dashed dark band)
    x = int(W * 0.56)
    for y0 in range(0, H, 46):
        img[y0:y0 + 30, x:x + 7] = (38, 36, 42)

    # Colour-shifting hologram patch
    patch = np.zeros((130, 110, 3), dtype=np.uint8)
    for r in range(130):
        hue = int(180 * r / 130)
        patch[r, :] = cv2.cvtColor(np.uint8([[[hue, 210, 205]]]), cv2.COLOR_HSV2BGR)[0, 0]
    img[70:200, W - 260:W - 150] = patch

    # Serial number glyphs
    x0, y0 = int(W * 0.66), int(H * 0.86)
    for k in range(9):
        cv2.rectangle(img, (x0 + k * 30, y0), (x0 + k * 30 + 18, y0 + 32), (25, 22, 28), -1)

    tint = np.zeros_like(img, dtype=np.int16)
    tint[:, :, 0] += 6
    tint[:, :, 1] += 4
    return np.clip(img.astype(np.int16) - tint, 0, 255).astype(np.uint8)


def camera_capture(
    img: np.ndarray,
    seed: int = 0,
    *,
    exposure: float = 1.0,
    blur_sigma: float = 0.7,
    jpeg_q: int = 88,
    width: int | None = None,
) -> np.ndarray:
    """Phone-photo pipeline: perspective, vignette, exposure, noise, optics, JPEG."""
    rng = np.random.default_rng(seed)
    h, w = img.shape[:2]
    jitter = lambda a: float(rng.uniform(-a, a))  # noqa: E731

    src = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
    dst = np.float32([
        [10 + jitter(8), 7 + jitter(5)],
        [w - 8 + jitter(6), 4 + jitter(4)],
        [w - 12 + jitter(8), h - 9 + jitter(6)],
        [6 + jitter(5), h - 5 + jitter(4)],
    ])
    img = cv2.warpPerspective(img, cv2.getPerspectiveTransform(src, dst), (w, h),
                              borderMode=cv2.BORDER_REPLICATE)

    ky = cv2.getGaussianKernel(h, h * 0.9)
    kx = cv2.getGaussianKernel(w, w * 0.9)
    mask = ky @ kx.T
    mask = 0.82 + 0.18 * mask / mask.max()
    img = np.clip(img.astype(np.float32) * mask[..., None] * exposure, 0, 255).astype(np.uint8)

    if blur_sigma > 0:
        img = cv2.GaussianBlur(img, (0, 0), blur_sigma)
    img = np.clip(
        img.astype(np.float32) + rng.normal(0, 3.5, img.shape).astype(np.float32), 0, 255
    ).astype(np.uint8)

    if width and width != w:
        img = cv2.resize(img, (width, int(h * width / w)), interpolation=cv2.INTER_AREA)

    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, jpeg_q])
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def reference_capture(note_seed: int, capture_seed: int) -> np.ndarray:
    """One genuine capture from the VERIFICATION-GRADE condition envelope.

    Deliberately excludes atrocious captures (near-dark, sub-640px, heavy
    shake): those are handled by the capture guard, which routes them to
    manual review instead of judging them. Keeping the reference tight is what
    lets detail-poor fakes stand out — a reference that tolerates any capture
    quality also tolerates any fake.
    """
    rng = np.random.default_rng(capture_seed)
    return camera_capture(
        genuine_note(note_seed),
        seed=capture_seed,
        exposure=float(rng.uniform(0.52, 1.15)),
        blur_sigma=float(rng.uniform(0.4, 1.5)),
        jpeg_q=int(rng.integers(70, 95)),
        width=int(rng.integers(720, 1400)),
    )


def photocopy(img: np.ndarray) -> np.ndarray:
    """Colour photocopy: flattened tone, lost fine detail, toner desaturation."""
    out = cv2.bilateralFilter(img, 11, 60, 60)
    out = cv2.bilateralFilter(out, 11, 60, 60)
    hsv = cv2.cvtColor(out, cv2.COLOR_BGR2HSV).astype(np.int16)
    hsv[:, :, 1] = np.clip(hsv[:, :, 1] * 0.45, 0, 255)
    out = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
    out = np.clip(out.astype(np.float32) * 1.12 + 14, 0, 255).astype(np.uint8)
    ok, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 82])
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def inkjet_reprint(img: np.ndarray) -> np.ndarray:
    """Home-printer reprint: dot-pitch downsample, no intaglio depth."""
    h, w = img.shape[:2]
    small = cv2.resize(img, (w // 3, h // 3), interpolation=cv2.INTER_AREA)
    out = cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)
    out = cv2.GaussianBlur(out, (3, 3), 0)
    ok, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


def screen_display(img: np.ndarray) -> np.ndarray:
    """Note shown on a phone screen: backlight lift, pixel grid, glare."""
    h, w = img.shape[:2]
    out = np.clip(img.astype(np.float32) * 1.18 + 22, 0, 255).astype(np.uint8)
    out[::3, :] = np.clip(out[::3, :].astype(np.int16) - 12, 0, 255).astype(np.uint8)
    cv2.ellipse(out, (w // 3, h // 4), (260, 90), 25, 0, 360, (255, 255, 255), -1)
    out = cv2.GaussianBlur(out, (3, 3), 0)
    ok, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)
