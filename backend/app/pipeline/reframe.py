"""
Decides where to center the 9:16 crop window for a clip by sampling frames
and running Haar-cascade face detection. This is a simple MVP approach —
swap for MediaPipe or a tracked-crop model later for smoother results.
"""
import cv2

_face_cascade = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
)


def find_crop_center_x(video_path: str, start: float, end: float, sample_count: int = 6) -> float:
    """
    Returns a normalized x-position (0.0-1.0) of where faces tend to be,
    across a handful of sampled frames within [start, end]. Falls back to
    0.5 (dead center) if no faces are found.
    """
    cap = cv2.VideoCapture(video_path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    width = cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 1920

    centers = []
    duration = max(end - start, 0.1)
    for i in range(sample_count):
        t = start + duration * (i / max(sample_count - 1, 1))
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
        ok, frame = cap.read()
        if not ok:
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = _face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5)
        if len(faces) > 0:
            # use the largest face found in this frame
            fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])
            face_center_x = (fx + fw / 2) / frame.shape[1]
            centers.append(face_center_x)

    cap.release()
    if not centers:
        return 0.5
    return sum(centers) / len(centers)
