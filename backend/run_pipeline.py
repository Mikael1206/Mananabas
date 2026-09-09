#!/usr/bin/env python3
"""
Pungol pipeline — single file, zero external dependencies beyond stdlib + the
packages already pinned in backend/requirements.txt.

Run from backend/ with the venv active:

    python run_pipeline.py "https://www.youtube.com/watch?v=aqz-KE-bpKQ"

What it does, in order:
  1. Download the source video with yt-dlp.
  2. Transcribe with faster-whisper (word-level timestamps).
  3. Send the transcript to the configured LLM and pick highlight moments.
  4. For each highlight:
     - Find a face-centered crop x-position (OpenCV Haar cascades).
     - Build a .ass caption file for the clip.
     - ffmpeg: crop to 9:16, trim, burn captions -> final .mp4.
  5. Print a short summary of the finished clips.

Set environment variables (or use an .env / pungol.env) before running:

    LLM_PROVIDER=openai
    OPENAI_API_KEY=sk-...
    WHISPER_MODEL_SIZE=tiny          # tiny|base|small|medium|large-v3
    WHISPER_DEVICE=cpu               # cpu|cuda
    MEDIA_DIR=./media
    MAX_CLIPS_PER_JOB=5
    CLIP_MIN_SECONDS=20
    CLIP_MAX_SECONDS=90
    DATABASE_URL=sqlite:///./pungol.db   # optional; used only if you want the
                                          # same SQLModel schema. Not required
                                          # for this standalone runner.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Config (mirrors app/config.py semantics so the same .env works)
# ---------------------------------------------------------------------------
_PROVIDER_KEYS = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
}

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai").lower()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "tiny")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
MEDIA_DIR = os.getenv("MEDIA_DIR", "./media")
MAX_CLIPS_PER_JOB = int(os.getenv("MAX_CLIPS_PER_JOB", "5"))
CLIP_MIN_SECONDS = int(os.getenv("CLIP_MIN_SECONDS", "20"))
CLIP_MAX_SECONDS = int(os.getenv("CLIP_MAX_SECONDS", "90"))

if LLM_PROVIDER not in _PROVIDER_KEYS:
    raise SystemExit(
        f"Unknown LLM_PROVIDER '{LLM_PROVIDER}'. Expected one of: "
        f"{', '.join(_PROVIDER_KEYS)}."
    )
if not os.getenv(_PROVIDER_KEYS[LLM_PROVIDER]):
    raise SystemExit(
        f"{_PROVIDER_KEYS[LLM_PROVIDER]} is not set. Add your API key, or switch "
        f"LLM_PROVIDER to one of: {', '.join(_PROVIDER_KEYS)}."
    )

# ---------------------------------------------------------------------------
# Tiny Job / Clip model (mirrors app/models.py) so the probe log and the
# schema-stuffed SQLite path stay consistent if you want that later.
# ---------------------------------------------------------------------------
class JobStatus:
    queued = "queued"
    downloading = "downloading"
    transcribing = "transcribing"
    ranking = "ranking"
    rendering = "rendering"
    done = "done"
    failed = "failed"


@dataclass
class Clip:
    title: str
    hook: Optional[str]
    score: Optional[float]
    start: float
    end: float
    file_path: str


@dataclass
class Job:
    id: int
    youtube_url: str
    status: str = JobStatus.queued
    progress_message: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    clips: List[Clip] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "youtube_url": self.youtube_url,
            "status": self.status,
            "progress_message": self.progress_message,
            "error": self.error,
            "clips": [
                {
                    "id": i,
                    "title": c.title,
                    "hook": c.hook,
                    "score": c.score,
                    "start": c.start,
                    "end": c.end,
                    "file_path": c.file_path,
                }
                for i, c in enumerate(self.clips)
            ],
        }


# ---------------------------------------------------------------------------
# ffmpeg location helper (mirrors downloader.py + render.py)
# ---------------------------------------------------------------------------
def _ffmpeg_exe() -> str:
    system = shutil.which("ffmpeg")
    if system:
        return system
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


# ---------------------------------------------------------------------------
# Step 1: download
# ---------------------------------------------------------------------------
def _download(url: str, out_dir: Path) -> Path:
    out_path = out_dir / "source.%(ext)s"

    ydl_opts: Dict[str, Any] = {
        "format": (
            "bestvideo[height<=1080][ext=mp4][vcodec^=avc1]"
            "+bestaudio[ext=m4a]/best[ext=mp4]/best"
        ),
        "outtmpl": str(out_path),
        "merge_output_format": "mp4",
        "ffmpeg_location": _ffmpeg_exe(),
        "quiet": True,
        "no_warnings": True,
    }

    import yt_dlp

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
        base, _ = os.path.splitext(filename)
        mp4_path = Path(base + ".mp4")
        return mp4_path if mp4_path.exists() else Path(filename)


# ---------------------------------------------------------------------------
# Step 2: transcribe (mirrors transcriber.py, but returns dataclasses inline)
# ---------------------------------------------------------------------------
@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: List[Word] = field(default_factory=list)


def _transcribe(video_path: Path) -> List[Segment]:
    from faster_whisper import WhisperModel

    device = WHISPER_DEVICE
    compute_type = "int8" if device == "cpu" else "float16"
    model = WhisperModel(WHISPER_MODEL_SIZE, device=device, compute_type=compute_type)

    # VAD fallback chain (mirrors transcriber.py)
    for vad_threshold in (None, 0.3):
        try:
            segments = _transcribe_inner(model, video_path, vad_threshold)
            if segments:
                return segments
        except ValueError:
            continue

    return _transcribe_inner(model, video_path, None, force_no_vad=True)


def _transcribe_inner(
    model: WhisperModel,
    video_path: Path,
    vad_threshold: Optional[float],
    force_no_vad: bool = False,
) -> List[Segment]:
    if force_no_vad:
        vad_filter, vad_parameters = False, None
    elif vad_threshold is None:
        vad_filter, vad_parameters = True, None
    else:
        vad_filter, vad_parameters = True, {"threshold": vad_threshold}

    segments_iter, _info = model.transcribe(
        str(video_path),
        word_timestamps=True,
        vad_filter=vad_filter,
        vad_parameters=vad_parameters,
    )

    segments: List[Segment] = []
    for seg in segments_iter:
        words = [Word(start=w.start, end=w.end, text=w.word) for w in (seg.words or [])]
        segments.append(Segment(start=seg.start, end=seg.end, text=seg.text.strip(), words=words))
    return segments


def transcript_to_plain_text(segments: List[Segment]) -> str:
    lines: List[str] = []
    for seg in segments:
        m, s = divmod(int(seg.start), 60)
        lines.append(f"[{m:02d}:{s:02d}] {seg.text}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Step 3: LLM highlight selection (mirrors highlighter.py)
# ---------------------------------------------------------------------------
_PROMPT_TEMPLATE = """You are an expert short-form video editor who finds viral-worthy
moments in long videos for TikTok/Reels/Shorts.

Below is a timestamped transcript. Pick the {max_clips} best standalone moments.
Each moment must:
- be between {min_sec} and {max_sec} seconds long
- work on its own without needing earlier context
- have a strong hook in the first 3 seconds (a question, bold claim, or surprising statement)

Return ONLY valid JSON, no markdown fences, no commentary, in this exact shape:
[
  {{"start": <seconds float>, "end": <seconds float>, "title": "<short catchy title>",
"hook": "<the hook line/idea>", "score": <0-100 virality score>}}
]

TRANSCRIPT:
{transcript}
"""


def _extract_json(text: str) -> str:
    match = re.search(r"\[.*\]", text, re.DOTALL)
    return match.group(0) if match else text


def _call_openai(prompt: str) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=OPENAI_API_KEY)
    resp = client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4,
    )
    return resp.choices[0].message.content


def _call_anthropic(prompt: str) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    resp = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(block.text for block in resp.content if block.type == "text")


def _call_gemini(prompt: str) -> str:
    import google.generativeai as genai

    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel(GEMINI_MODEL)
    resp = model.generate_content(prompt)
    return resp.text


def select_highlights(transcript_text: str) -> List[Dict]:
    prompt = _PROMPT_TEMPLATE.format(
        max_clips=MAX_CLIPS_PER_JOB,
        min_sec=CLIP_MIN_SECONDS,
        max_sec=CLIP_MAX_SECONDS,
        transcript=transcript_text,
    )

    if LLM_PROVIDER == "openai":
        raw = _call_openai(prompt)
    elif LLM_PROVIDER == "anthropic":
        raw = _call_anthropic(prompt)
    elif LLM_PROVIDER == "gemini":
        raw = _call_gemini(prompt)
    else:
        raise SystemExit(f"Unknown LLM_PROVIDER: {LLM_PROVIDER}")

    json_str = _extract_json(raw)
    highlights = json.loads(json_str)

    clean: List[Dict] = []
    for h in highlights:
        try:
            if float(h["end"]) > float(h["start"]):
                clean.append(h)
        except (KeyError, TypeError, ValueError):
            continue
    return clean


# ---------------------------------------------------------------------------
# Step 4a: reframe / crop center (mirrors reframe.py)
# ---------------------------------------------------------------------------
def _find_crop_center_x(video_path: Path, start: float, end: float, sample_count: int = 6) -> float:
    import cv2

    face_cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )

    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 1920

    centers: List[float] = []
    duration = max(end - start, 0.1)
    for i in range(sample_count):
        t = start + duration * (i / max(sample_count - 1, 1))
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(t * fps))
        ok, frame = cap.read()
        if not ok:
            continue
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5)
        if len(faces) > 0:
            fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])
            face_center_x = (fx + fw / 2) / frame.shape[1]
            centers.append(face_center_x)

    cap.release()
    if not centers:
        return 0.5
    return sum(centers) / len(centers)


# ---------------------------------------------------------------------------
# Step 4b: captions (mirrors captions.py)
# ---------------------------------------------------------------------------
_ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, Outline, Shadow, Alignment, MarginL, MarginR, MarginV
Style: Default,Arial Black,72,&H00FFFFFF,&H00000000,&H00000000,1,4,0,2,60,60,120

[Events]
Format: Layer, Start, End, Style, Text
"""


def _fmt_time(t: float) -> str:
    if t < 0:
        t = 0
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def _build_ass_for_clip(
    segments: List[Segment], clip_start: float, clip_end: float, out_path: Path
) -> Path:
    lines = [_ASS_HEADER]

    for seg in segments:
        if seg.end < clip_start or seg.start > clip_end:
            continue
        words = seg.words or []
        chunk: List[str] = []
        chunk_start: Optional[float] = None
        for w in words:
            if w.start < clip_start or w.end > clip_end:
                continue
            if chunk_start is None:
                chunk_start = w.start
            chunk.append(w.text.strip())
            if len(chunk) >= 4:
                text = " ".join(chunk).replace("\n", " ")
                lines.append(
                    f"Dialogue: 0,{_fmt_time(chunk_start - clip_start)},"
                    f"{_fmt_time(w.end - clip_start)},Default,{text}"
                )
                chunk, chunk_start = [], None
        if chunk and chunk_start is not None:
            text = " ".join(chunk).replace("\n", " ")
            lines.append(
                f"Dialogue: 0,{_fmt_time(chunk_start - clip_start)},"
                f"{_fmt_time(min(seg.end, clip_end) - clip_start)},Default,{text}"
            )

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


# ---------------------------------------------------------------------------
# Step 4c: render (mirrors render.py)
# ---------------------------------------------------------------------------
def _render_clip(
    source_path: Path,
    start: float,
    end: float,
    center_x_norm: float,
    ass_path: Path,
    out_path: Path,
) -> Path:
    import cv2

    cap = cv2.VideoCapture(str(source_path))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()

    target_ratio = 9 / 16
    crop_w = int(height * target_ratio)
    if crop_w > width:
        crop_w = width
    crop_h = height

    x_center_px = center_x_norm * width
    x = int(x_center_px - crop_w / 2)
    x = max(0, min(x, width - crop_w))

    duration = max(end - start, 0.5)

    vf = f"crop={crop_w}:{crop_h}:{x}:0,scale=1080:1920,ass={ass_path}"

    cmd = [
        _ffmpeg_exe(),
        "-y",
        "-ss",
        str(start),
        "-i",
        str(source_path),
        "-t",
        str(duration),
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python run_pipeline.py <youtube_url>", file=sys.stderr)
        sys.exit(1)

    url = sys.argv[1]
    job_id = int(datetime.now(timezone.utc).timestamp()) % 1_000_000
    job_dir = Path(MEDIA_DIR) / str(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)

    job = Job(id=job_id, youtube_url=url)

    def _update(msg: str, status: Optional[str] = None) -> None:
        job.progress_message = msg
        if status is not None:
            job.status = status
        print(f"[{job_id}] {status or job.status}: {msg}")

    try:
        # 1. Download
        _update("Downloading source video...", JobStatus.downloading)
        video_path = _download(url, job_dir)
        _update(f"Downloaded -> {video_path}")

        # 2. Transcribe
        _update("Transcribing audio...", JobStatus.transcribing)
        segments = _transcribe(video_path)
        transcript_text = transcript_to_plain_text(segments)
        _update(f"Transcribed {len(segments)} segments")

        # 3. Rank highlights
        _update("Selecting best moments...", JobStatus.ranking)
        highlights = select_highlights(transcript_text)
        _update(f"LLM picked {len(highlights)} candidates")

        # 4. Render each highlight
        _update("Rendering clips...", JobStatus.rendering)
        for idx, h in enumerate(highlights[:MAX_CLIPS_PER_JOB]):
            _update(f"Rendering clip {idx + 1}/{len(highlights)}...", JobStatus.rendering)

            start, end = float(h["start"]), float(h["end"])
            center_x = _find_crop_center_x(video_path, start, end)

            ass_path = job_dir / f"clip_{idx}.ass"
            _build_ass_for_clip(segments, start, end, ass_path)

            out_path = job_dir / f"clip_{idx}.mp4"
            _render_clip(video_path, start, end, center_x, ass_path, out_path)

            clip = Clip(
                title=h.get("title", f"Clip {idx + 1}"),
                hook=h.get("hook"),
                score=h.get("score"),
                start=start,
                end=end,
                file_path=str(out_path),
            )
            job.clips.append(clip)

        job.status = JobStatus.done
        job.progress_message = "Done."
        _update("Done.", JobStatus.done)

    except Exception as e:
        job.status = JobStatus.failed
        job.error = f"{e}\n{traceback.format_exc()}"
        _update(f"Failed: {e}", JobStatus.failed)
        print(job.error, file=sys.stderr)

    # Summary
    print("\n=== SUMMARY ===")
    print(json.dumps(job.to_dict(), indent=2))
    print(f"\nMedia dir: {job_dir}")


if __name__ == "__main__":
    main()
