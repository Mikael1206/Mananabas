"""
Transcribes a video's audio track locally using faster-whisper.
No API cost, runs on CPU (slower) or CUDA GPU (fast) depending on config.
"""
from dataclasses import dataclass
from typing import List

from faster_whisper import WhisperModel

from app.config import settings

_model = None


def _get_model() -> WhisperModel:
    global _model
    if _model is None:
        _model = WhisperModel(
            settings.whisper_model_size,
            device=settings.whisper_device,
            compute_type="int8" if settings.whisper_device == "cpu" else "float16",
        )
    return _model


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
    words: List[Word]


def transcribe(video_path: str) -> List[Segment]:
    """Returns a list of segments, each with word-level timestamps."""
    model = _get_model()
    segments_iter, _info = model.transcribe(
        video_path,
        word_timestamps=True,
        vad_filter=True,  # skips silence, improves segment quality
    )

    segments: List[Segment] = []
    for seg in segments_iter:
        words = [Word(start=w.start, end=w.end, text=w.word) for w in (seg.words or [])]
        segments.append(Segment(start=seg.start, end=seg.end, text=seg.text.strip(), words=words))
    return segments


def transcript_to_plain_text(segments: List[Segment]) -> str:
    """A simple [mm:ss] text-per-second transcript, useful as LLM input."""
    lines = []
    for seg in segments:
        m, s = divmod(int(seg.start), 60)
        lines.append(f"[{m:02d}:{s:02d}] {seg.text}")
    return "\n".join(lines)
