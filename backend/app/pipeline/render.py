"""
Renders a final vertical clip: crops the source to 9:16 on the detected
subject, applies short-form grade/animation, burns captions, and mixes
opening/ending stings plus a generated royalty-free music bed under speech.

On Railway (or RENDER_LITE=1) a lighter graph is used: zoompan and extra
lavfi inputs are skipped so ffmpeg is not SIGKILL'd (exit -9) by the OOM
killer.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from typing import List, Tuple

import cv2

OUTPUT_W = 1080
OUTPUT_H = 1920
LITE_W = 720
LITE_H = 1280


def _constrained() -> bool:
    return bool(os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RENDER_LITE"))


def _out_size(lite: bool) -> Tuple[int, int]:
    return (LITE_W, LITE_H) if lite else (OUTPUT_W, OUTPUT_H)


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
    lite: bool = False,
) -> str:
    """
    Crop → scale → (optional Ken Burns) → grade → fades → captions.

    zoompan is omitted in lite mode; it buffers full frames and often gets
    the process SIGKILL'd on small Railway instances.
    """
    fade_in, fade_out, fade_out_st = fade_times(duration)
    ass = escape_filter_path(ass_path)
    out_w, out_h = _out_size(lite)
    parts = [
        f"crop={crop_w}:{crop_h}:{x}:0",
        f"scale={out_w}:{out_h}",
    ]
    if not lite:
        fps = max(fps, 12.0)
        parts.append(
            f"zoompan=z='min(1.16,1.04+0.00055*on)'"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d=1:s={out_w}x{out_h}:fps={fps:.4f}"
        )
        parts.extend(
            [
                "eq=contrast=1.08:brightness=0.02:saturation=1.18:gamma=1.04",
                "unsharp=5:5:0.65:5:5:0.0",
                "vignette=PI/5",
            ]
        )
    else:
        parts.append("eq=contrast=1.06:saturation=1.12")
    parts.extend(
        [
            f"fade=t=in:st=0:d={fade_in:.3f}",
            f"fade=t=out:st={fade_out_st:.3f}:d={fade_out:.3f}",
            f"ass={ass}",
        ]
    )
    return ",".join(parts)


def build_audio_filter(
    duration: float, has_speech: bool = True, lite: bool = False
) -> str:
    """
    Mix original speech with a generated pad/texture bed, an opening whoosh,
    and an ending sting.

    Full lavfi order: [1][2][3] pads [4] noise [5] whoosh [6] sting.
    Lite lavfi order: [1] single pad only.
    """
    fade_in, fade_out, fade_out_st = fade_times(duration)
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
    if lite:
        return ";".join(
            [
                speech,
                "[1:a]aformat=sample_fmts=fltp:channel_layouts=stereo,volume=0.12,"
                f"afade=t=in:st=0:d=1.0,afade=t=out:st={max(duration-1.0, 0):.3f}:d=1.0[bgm]",
                "[speech][bgm]amix=inputs=2:duration=first:dropout_transition=0:weights=1 0.22[aout]",
            ]
        )
    end_ms = max(int((duration - 0.48) * 1000), 0)
    bgm_fade_out_st = max(duration - 1.3, 0.0)
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


def lavfi_audio_inputs(duration: float, lite: bool = False) -> List[str]:
    """Royalty-free generated bed + stings; no third-party audio files needed."""
    d = f"{max(duration, 0.5):.3f}"
    if lite:
        return ["-f", "lavfi", "-t", d, "-i", "sine=frequency=220:sample_rate=22050"]
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


def _ffmpeg_error_message(exc: subprocess.CalledProcessError) -> str:
    if exc.returncode in (-9, 137):
        return (
            "ffmpeg was killed (exit -9/137): the host ran out of memory. "
            "Railway trial RAM is often too small for 1080p zoompan; "
            "the next retry uses a lighter encode, or set RENDER_LITE=1."
        )
    tail = (exc.stderr or exc.stdout or "")[-4000:]
    return f"ffmpeg failed ({exc.returncode}): {tail}"


def _run_ffmpeg(cmd: List[str]) -> None:
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(_ffmpeg_error_message(exc)) from exc


def _encode(
    source_path: str,
    start: float,
    duration: float,
    crop_w: int,
    crop_h: int,
    x: int,
    ass_path: str,
    out_path: str,
    fps: float,
    lite: bool,
) -> None:
    vf = build_video_filter(crop_w, crop_h, x, duration, ass_path, fps=fps, lite=lite)
    af = build_audio_filter(duration, has_speech=has_audio_stream(source_path), lite=lite)
    cmd = [
        _ffmpeg_exe(),
        "-y",
        "-threads", "1",
        "-filter_threads", "1",
        "-ss", str(start),
        "-t", str(duration),
        "-i", source_path,
        *lavfi_audio_inputs(duration, lite=lite),
        "-filter_complex", f"[0:v]{vf}[vout];{af}",
        "-map", "[vout]",
        "-map", "[aout]",
        "-c:v", "libx264",
        "-preset", "ultrafast" if lite else "veryfast",
        "-crf", "23" if lite else "20",
        "-c:a", "aac",
        "-b:a", "128k" if lite else "160k",
        "-shortest",
        "-movflags", "+faststart",
        out_path,
    ]
    _run_ffmpeg(cmd)


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
    lite = _constrained()
    try:
        _encode(
            source_path, start, duration, crop_w, crop_h, x, ass_path, out_path, fps, lite
        )
    except RuntimeError:
        if lite:
            raise
        _encode(
            source_path, start, duration, crop_w, crop_h, x, ass_path, out_path, fps, True
        )
    return out_path
