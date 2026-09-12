"""
Builds a .ass subtitle file for burning captions into a clip. Takes the
full-video word-level segments and slices out only the words within the
clip's [start, end] range, re-timed to start at 0.
"""
from typing import List

from app.pipeline.transcriber import Segment

ASS_HEADER = """[Script Info]
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


def _animate_caption(text: str) -> str:
    """Pop-in scale + fade so captions feel distinct from the source video."""
    return (
        r"{\fad(120,80)\t(0,180,\fscx112\fscy112)\t(180,360,\fscx100\fscy100)}"
        + text
    )


def _fmt_time(t: float) -> str:
    if t < 0:
        t = 0
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def build_ass_for_clip(segments: List[Segment], clip_start: float, clip_end: float, out_path: str) -> str:
    lines = [ASS_HEADER]

    for seg in segments:
        if seg.end < clip_start or seg.start > clip_end:
            continue
        # Group words into ~4-word caption chunks for short-form readability
        words = seg.words or []
        chunk = []
        chunk_start = None
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
                    f"{_fmt_time(w.end - clip_start)},Default,{_animate_caption(text)}"
                )
                chunk, chunk_start = [], None
        if chunk and chunk_start is not None:
            text = " ".join(chunk).replace("\n", " ")
            lines.append(
                f"Dialogue: 0,{_fmt_time(chunk_start - clip_start)},"
                f"{_fmt_time(min(seg.end, clip_end) - clip_start)},Default,{_animate_caption(text)}"
            )

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return out_path
