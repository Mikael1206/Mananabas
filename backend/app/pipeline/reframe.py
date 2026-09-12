"""
Decides where to center the 9:16 crop window for a clip.

Haar face detection alone often misses the subject (side profiles, motion,
odd lighting), which leaves the crop at frame-center and clips people off.
This module combines:
  1. Frontal + both-profile face cascades
  2. Upper-body cascade as a fallback
  3. Motion centroid across sampled frames
  4. Edge-energy saliency (detail tends to sit on the speaker)

Face hits win when present; otherwise motion/energy pick the main object.
"""
from typing import List, Optional, Sequence

import cv2
import numpy as np

_FRONTAL = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
)
_PROFILE = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_profileface.xml"
)
_UPPER_BODY = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_upperbody.xml"
)


def _largest_box_center_x(boxes: Sequence, frame_w: int) -> Optional[float]:
    if boxes is None or len(boxes) == 0:
        return None
    fx, _fy, fw, fh = max(boxes, key=lambda b: int(b[2]) * int(b[3]))
    return (float(fx) + float(fw) / 2.0) / float(frame_w)


def _detect_person_center_x(gray: np.ndarray) -> Optional[float]:
    """Return a normalized x-center if a face or upper body is found."""
    h, w = gray.shape[:2]
    min_size = (max(24, w // 24), max(24, h // 24))
    equalized = cv2.equalizeHist(gray)

    for cascade in (_FRONTAL, _PROFILE):
        if cascade is None or cascade.empty():
            continue
        boxes = cascade.detectMultiScale(
            equalized, scaleFactor=1.08, minNeighbors=4, minSize=min_size
        )
        cx = _largest_box_center_x(boxes, w)
        if cx is not None:
            return cx

    if _PROFILE is not None and not _PROFILE.empty():
        flipped = cv2.flip(equalized, 1)
        boxes = _PROFILE.detectMultiScale(
            flipped, scaleFactor=1.08, minNeighbors=4, minSize=min_size
        )
        if boxes is not None and len(boxes) > 0:
            fx, _fy, fw, _fh = max(boxes, key=lambda b: int(b[2]) * int(b[3]))
            # Convert flipped-image x back to original coordinates.
            orig_center = w - (float(fx) + float(fw) / 2.0)
            return orig_center / float(w)

    if _UPPER_BODY is not None and not _UPPER_BODY.empty():
        boxes = _UPPER_BODY.detectMultiScale(
            equalized, scaleFactor=1.08, minNeighbors=3, minSize=min_size
        )
        cx = _largest_box_center_x(boxes, w)
        if cx is not None:
            return cx
    return None


def energy_center_x(frame: np.ndarray) -> float:
    """
    Column-wise Sobel energy, weighted toward the vertical middle of the
    frame (typical talking-head / presenter region). Used when detectors miss.
    """
    if frame.ndim == 3:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    else:
        gray = frame
    gray = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Sobel(gray, cv2.CV_32F, 1, 1, ksize=3)
    energy = np.abs(edges)
    h, w = energy.shape
    # Triangle weight: more mass in the middle third of the frame height.
    row_w = np.hanning(h).astype(np.float32)
    row_w = 0.25 + 0.75 * row_w
    col = (energy * row_w[:, None]).sum(axis=0)
    k = max(9, (w // 20) | 1)
    kernel = np.ones(k, dtype=np.float32) / k
    col = np.convolve(col, kernel, mode="same")
    margin = max(1, int(w * 0.06))
    col[:margin] = 0
    col[-margin:] = 0
    if float(col.sum()) <= 1e-6:
        return 0.5
    idx = int(np.argmax(col))
    return idx / float(w)


def motion_center_x(prev: np.ndarray, curr: np.ndarray) -> Optional[float]:
    """Normalized centroid of frame-to-frame motion, or None if the scene is still."""
    g0 = cv2.cvtColor(prev, cv2.COLOR_BGR2GRAY) if prev.ndim == 3 else prev
    g1 = cv2.cvtColor(curr, cv2.COLOR_BGR2GRAY) if curr.ndim == 3 else curr
    if g0.shape != g1.shape:
        return None
    diff = cv2.absdiff(g0, g1)
    _, th = cv2.threshold(diff, 18, 255, cv2.THRESH_BINARY)
    th = cv2.medianBlur(th, 5)
    col = th.sum(axis=0).astype(np.float32)
    total = float(col.sum())
    if total < 255 * 20:
        return None
    xs = np.arange(col.size, dtype=np.float32)
    return float((xs * col).sum() / total / col.size)


def aggregate_centers(
    face_centers: Sequence[float],
    motion_centers: Sequence[float],
    energy_centers: Sequence[float],
) -> float:
    """Prefer faces; mix motion + energy when the detector finds nothing."""
    if face_centers:
        return float(np.median(np.asarray(face_centers, dtype=np.float64)))
    parts: List[float] = []
    weights: List[float] = []
    if motion_centers:
        parts.append(float(np.median(np.asarray(motion_centers, dtype=np.float64))))
        weights.append(0.6)
    if energy_centers:
        parts.append(float(np.median(np.asarray(energy_centers, dtype=np.float64))))
        weights.append(0.4)
    if not parts:
        return 0.5
    w = np.asarray(weights, dtype=np.float64)
    return float(np.dot(np.asarray(parts), w / w.sum()))


def find_crop_center_x(
    video_path: str, start: float, end: float, sample_count: int = 12
) -> float:
    """
    Returns a normalized x-position (0.0-1.0) of the main subject across
    sampled frames within [start, end]. Falls back to 0.5 if nothing usable
    is found.
    """
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    face_centers: List[float] = []
    energy_centers: List[float] = []
    motion_centers: List[float] = []
    prev_frame: Optional[np.ndarray] = None

    duration = max(end - start, 0.1)
    count = max(sample_count, 2)
    for i in range(count):
        t = start + duration * (i / (count - 1))
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
        ok, frame = cap.read()
        if not ok or frame is None:
            continue

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        face_cx = _detect_person_center_x(gray)
        if face_cx is not None:
            face_centers.append(face_cx)

        energy_centers.append(energy_center_x(frame))

        if prev_frame is not None:
            mx = motion_center_x(prev_frame, frame)
            if mx is not None:
                motion_centers.append(mx)
        prev_frame = frame

    cap.release()
    return aggregate_centers(face_centers, motion_centers, energy_centers)
