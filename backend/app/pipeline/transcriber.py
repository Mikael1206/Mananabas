"""
Transcribes a video's audio track locally using faster-whisper.
No API cost, runs on CPU (slower) or CUDA GPU (fast) depending on config.
"""
from dataclasses import dataclass
from typing import List, Optional

from app.config import settings

_model = None


def _get_model():
    """Load Whisper on first transcription, not at API import (saves RAM)."""
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

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


def transcribe(video_path: str, language: Optional[str] = None) -> List[Segment]:
    """Returns a list of segments, each with word-level timestamps.

    Runs with VAD (Silero) to skip silence. If VAD filters out *all* audio
    (common with music-heavy or cartoon audio that Silero doesn't recognize
    as speech, which also crashes faster-whisper's language detection with
    "max() iterable argument is empty"), retries with a lower VAD threshold
    and finally without VAD at all.
    """
    model = _get_model()
    resolved_language = language or (settings.whisper_language or None)

    # Silero's default 0.5 threshold misses quiet/heavily-mixed speech
    # (cartoons, music under dialogue). Fall back down the chain until
    # some audio survives VAD.
    for vad_threshold in (None, 0.3):
        try:
            segments = _transcribe(model, video_path, vad_threshold, language=resolved_language)
            if segments:
                return segments
        except ValueError:
            # Empty VAD-filtered audio crashes faster-whisper's language
            # detection; fall through to the next attempt.
            continue

    return _transcribe(model, video_path, None, force_no_vad=True, language=resolved_language)


def _transcribe(
    model: WhisperModel,
    video_path: str,
    vad_threshold: Optional[float],
    force_no_vad: bool = False,
    language: Optional[str] = None,
) -> List[Segment]:
    if force_no_vad:
        vad_filter, vad_parameters = False, None
    elif vad_threshold is None:
        vad_filter, vad_parameters = True, None
    else:
        vad_filter, vad_parameters = True, {"threshold": vad_threshold}

    segments_iter, info = model.transcribe(
        video_path,
        word_timestamps=True,
        vad_filter=vad_filter,
        vad_parameters=vad_parameters,
        language=language,
    )
    print(
        f"Whisper language={info.language} "
        f"probability={info.language_probability}"
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
