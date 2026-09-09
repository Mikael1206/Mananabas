"""
Renders a final vertical clip: crops the source to 9:16 centered on the
detected face position, trims to [start, end], and burns in the .ass
captions. Requires ffmpeg to be installed and on PATH (not a pip package).
"""
import subprocess

import cv2


def _get_dimensions(video_path: str):
    cap = cv2.VideoCapture(video_path)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    return w, h


def render_clip(
    source_path: str,
    start: float,
    end: float,
    center_x_norm: float,
    ass_path: str,
    out_path: str,
) -> str:
    width, height = _get_dimensions(source_path)
    target_ratio = 9 / 16

    crop_w = int(height * target_ratio)
    if crop_w > width:
        # Source is already narrower than 9:16 (e.g. shot vertically); skip crop.
        crop_w = width
    crop_h = height

    x_center_px = center_x_norm * width
    x = int(x_center_px - crop_w / 2)
    x = max(0, min(x, width - crop_w))

    duration = max(end - start, 0.5)

    # ass filter path needs escaping on Windows; fine as-is on Linux/Mac.
    vf = f"crop={crop_w}:{crop_h}:{x}:0,scale=1080:1920,ass={ass_path}"

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start),
        "-i", source_path,
        "-t", str(duration),
        "-vf", vf,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k",
        out_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path
