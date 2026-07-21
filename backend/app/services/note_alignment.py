"""Step A — note alignment (perspective transform).

Finds the rectangular note in a photo and warps it flat before any feature
extraction, so measurements are taken in note-space rather than camera-space.
Every function degrades gracefully: if no convincing quadrilateral is found the
original frame is returned, because refusing to guess beats warping to a wrong
rectangle.
"""
import logging

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# A note quad must cover a meaningful part of the frame and look like a
# banknote; these bounds reject table edges, book spines and paper stacks.
MIN_AREA_FRACTION = 0.15
MIN_ASPECT, MAX_ASPECT = 1.6, 3.2  # INR notes run ~2.0-2.5 : 1


def order_points(pts: np.ndarray) -> np.ndarray:
    """Order 4 points as top-left, top-right, bottom-right, bottom-left.

    Uses the coordinate-sum/diff trick: TL has the smallest x+y, BR the
    largest; TR has the smallest y-x, BL the largest.
    """
    pts = np.asarray(pts, dtype="float32").reshape(4, 2)
    ordered = np.zeros((4, 2), dtype="float32")
    s = pts.sum(axis=1)
    ordered[0] = pts[np.argmin(s)]  # top-left
    ordered[2] = pts[np.argmax(s)]  # bottom-right
    d = np.diff(pts, axis=1).ravel()  # y - x
    ordered[1] = pts[np.argmin(d)]  # top-right
    ordered[3] = pts[np.argmax(d)]  # bottom-left
    return ordered


def find_note_quad(img: np.ndarray) -> np.ndarray | None:
    """Return the 4 ordered corners of the note, or None if not confidently found."""
    if img is None or img.size == 0:
        return None
    h, w = img.shape[:2]
    frame_area = float(h * w)

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # Blur before Canny: suppresses the note's own microprint/texture edges so
    # the dominant contour is the note boundary, not its printed content.
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blurred, 50, 150)
    # Close small gaps so a slightly broken border still forms one contour.
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:5]:
        if cv2.contourArea(contour) < frame_area * MIN_AREA_FRACTION:
            break  # sorted desc: everything after this is smaller too
        peri = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.02 * peri, True)
        if len(approx) != 4 or not cv2.isContourConvex(approx):
            continue
        quad = order_points(approx)
        if _plausible_note(quad):
            return quad
    return None


def _plausible_note(quad: np.ndarray) -> bool:
    tl, tr, br, bl = quad
    width = max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl))
    height = max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl))
    if width < 40 or height < 20:
        return False
    aspect = width / height if height else 0.0
    return MIN_ASPECT <= aspect <= MAX_ASPECT


def align_note(img: np.ndarray) -> tuple[np.ndarray, bool]:
    """Flatten the note via perspective transform.

    Returns (image, aligned) — `aligned` is False when the original frame was
    returned unchanged because no confident quad was found.
    """
    quad = find_note_quad(img)
    if quad is None:
        return img, False

    tl, tr, br, bl = quad
    width = int(round(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl))))
    height = int(round(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl))))
    if width < 2 or height < 2:
        return img, False

    dst = np.array(
        [[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]],
        dtype="float32",
    )
    matrix = cv2.getPerspectiveTransform(quad, dst)
    flat = cv2.warpPerspective(img, matrix, (width, height))
    return flat, True
