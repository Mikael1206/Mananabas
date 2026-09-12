"""
Renders a final vertical clip: crops the source to 9:16 on the detected
subject, applies short-form grade/animation, burns captions, and mixes
opening/ending stings plus a generated royalty-free music bed under speech.

Requires ffmpeg. Uses a system ffmpeg if one is on PATH, otherwise falls
back to the static binary bundled with `imageio-ffmpeg`.
"""
from __future__ import annotations

import shutil
import subprocess
from typing import List, Tuple

import cv2

OUTPUT_W = 1080
OUTPUT_H = 1920


def _ffmpeg_exe() -> str:
    """Return a usable ffmpeg binary: system one if present, else the
    static build shipped with imageio-ffmpeg."""
    system = shutil.which("ffmpeg")
    if system:
        return system
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def _get_video_info(video_path: str) -> Tuple[int, int, float]:
    cap = cv2.VideoCapture(video_path)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1920
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 1080
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    cap.release()
    return w, h, fps


def has_audio_stream(video_path: str) -> bool:
    proc = subprocess.run(
        [_ffmpeg_exe(), "-i", video_path],
        capture_output=True,
        text=True,
    )
    return "Audio:" in (proc.stderr or "")


def crop_window(
    width: int,
    height: int,
    center_x_norm: float,
    target_ratio: float = 9 / 16,
) -> Tuple[int, int, int]:
    """Return (crop_w, crop_h, x) for a 9:16 window centered on the subject."""
    crop_w = int(height * target_ratio)
    if crop_w > width:
        crop_w = width
    crop_h = height
    x_center_px = center_x_norm * width
    x = int(x_center_px - crop_w / 2)
    x = max(0, min(x, width - crop_w))
    return crop_w, crop_h, x


def escape_filter_path(path: str) -> str:
    """Escape a filesystem path for use inside an ffmpeg filter graph."""
    return (
        path.replace("\\", "/")
        .replace(":", r"\:")
        .replace("'", r"\'")
        .replace(",", r"\,")
        .replace("[", r"\[")
        .replace("]", r"\]")
    )


def fade_times(duration: float) -> Tuple[float, float, float]:
    """Return (fade_in, fade_out, fade_out_start) in seconds."""
    fade_in = min(0.35, max(0.12, duration * 0.06))
    fade_out = min(0.55, max(0.18, duration * 0.1))
    fade_out_st = max(duration - fade_out, 0.0)
    return fade_in, fade_out, fade_out_st


def build_video_filter(
    crop_w: int,
    crop_h: int,
    x: int,
    duration: float,
    ass_path: str,
    fps: float = 30.0,
) -> str:
    """
    Crop → scale → slow Ken Burns zoom → grade → vignette → fades → captions.

    zoompan d=1 keeps 1:1 frame mapping so clip duration is unchanged.
    """
    fade_in, fade_out, fade_out_st = fade_times(duration)
    fps = max(fps, 12.0)
    ass = escape_filter_path(ass_path)
    zoom = (
        f"zoompan=z='min(1.16,1.04+0.00055*on)'"
        f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d=1:s={OUTPUT_W}x{OUTPUT_H}:fps={fps:.4f}"
    )
    return ",".join(
        [
            f"crop={crop_w}:{crop_h}:{x}:0",
            f"scale={OUTPUT_W}:{OUTPUT_H}",
            zoom,
            "eq=contrast=1.08:brightness=0.02:saturation=1.18:gamma=1.04",
            "unsharp=5:5:0.65:5:5:0.0",
            "vignette=PI/5",
            f"fade=t=in:st=0:d={fade_in:.3f}",
            f"fade=t=out:st={fade_out_st:.3f}:d={fade_out:.3f}",
            f"ass={ass}",
        ]
    )


def build_audio_filter(duration: float, has_speech: bool = True) -> str:
    """
    Mix original speech with a generated pad/texture bed, an opening whoosh,
    and an ending sting.

    lavfi inputs after the source video are expected in this order:
      [1] pad A  [2] pad B  [3] pad C  [4] pink-noise texture
      [5] opening whoosh  [6] ending sting
    """
    fade_in, fade_out, fade_out_st = fade_times(duration)
    end_ms = max(int((duration - 0.48) * 1000), 0)
    bgm_fade_out_st = max(duration - 1.3, 0.0)
    if has_speech:
        speech = (
            "[0:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,"
            f"volume=1.12,afade=t=in:st=0:d={fade_in:.3f},"
            f"afade=t=out:st={fade_out_st:.3f}:d={fade_out:.3f}[speech]"
        )
    else:
        speech = (
            f"anullsrc=r=44100:cl=stereo,atrim=0:{duration:.3f},"
            "aformat=sample_fmts=fltp:channel_layouts=stereo[speech]"
        )
    return ";".join(
        [
            speech,
            (
                "[1:a][2:a][3:a][4:a]amix=inputs=4:duration=first:dropout_transition=0,"
                "volume=0.85,highpass=f=120,lowpass=f=3800,"
                f"afade=t=in:st=0:d=1.1,afade=t=out:st={bgm_fade_out_st:.3f}:d=1.2[bgm]"
            ),
            "[speech][bgm]amix=inputs=2:duration=first:dropout_transition=0:weights=1 0.26[mix]",
            "[5:a]aformat=sample_fmts=fltp:channel_layouts=stereo,volume=0.55[open]",
            (
                "[6:a]aformat=sample_fmts=fltp:channel_layouts=stereo,"
                f"adelay={end_ms}|{end_ms},volume=0.5[end]"
            ),
            (
                "[mix][open][end]amix=inputs=3:duration=first:dropout_transition=0,"
                "alimiter=limit=0.95[aout]"
            ),
        ]
    )


def lavfi_audio_inputs(duration: float) -> List[str]:
    """Royalty-free generated bed + stings; no third-party audio files needed."""
    d = f"{max(duration, 0.5):.3f}"
    return [
        "-f", "lavfi", "-t", d, "-i", "sine=frequency=220:sample_rate=44100",
        "-f", "lavfi", "-t", d, "-i", "sine=frequency=277.18:sample_rate=44100",
        "-f", "lavfi", "-t", d, "-i", "sine=frequency=329.63:sample_rate=44100",
        "-f", "lavfi", "-t", d, "-i",
        f"anoisesrc=d={d}:c=pink:r=44100,lowpass=f=500,volume=0.18",
        "-f", "lavfi", "-t", "0.45", "-i",
        (
            "anoisesrc=d=0.45:c=white:r=44100,highpass=f=500,lowpass=f=4500,"
            "afade=t=in:st=0:d=0.04,afade=t=out:st=0.14:d=0.30,volume=0.7"
        ),
        "-f", "lavfi", "-t", "0.50", "-i",
        (
            "sine=frequency=659.25:sample_rate=44100,"
            "afade=t=in:st=0:d=0.02,afade=t=out:st=0.12:d=0.36,volume=0.45"
        ),
    ]


def _run_ffmpeg(cmd: List[str]) -> None:
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        tail = (exc.stderr or exc.stdout or "")[-4000:]
        raise RuntimeError(f"ffmpeg failed ({exc.returncode}): {tail}") from exc


def render_clip(
    source_path: str,
    start: float,
    end: float,
    center_x_norm: float,
    ass_path: str,
    out_path: str,
) -> str:
    width, height, fps = _get_video_info(source_path)
    crop_w, crop_h, x = crop_window(width, height, center_x_norm)
    duration = max(end - start, 0.5)
    vf = build_video_filter(crop_w, crop_h, x, duration, ass_path, fps=fps)
    af = build_audio_filter(duration, has_speech=has_audio_stream(source_path))

    cmd = [
        _ffmpeg_exe(),
        "-y",
        "-ss", str(start),
        "-t", str(duration),
        "-i", source_path,
        *lavfi_audio_inputs(duration),
        "-filter_complex", f"[0:v]{vf}[vout];{af}",
        "-map", "[vout]",
        "-map", "[aout]",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-c:a", "aac",
        "-b:a", "160k",
        "-shortest",
        "-movflags", "+faststart",
        out_path,
    ]
    _run_ffmpeg(cmd)
    return out_path
