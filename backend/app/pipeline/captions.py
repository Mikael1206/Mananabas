"""
Builds a .ass subtitle file for burning captions into a clip. Takes the
full-video word-level segments and slices out only the words within the
clip's [start, end] range, re-timed to start at 0.
"""
import re
from typing import List, Tuple

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


# ---------------------------------------------------------------------------
# Export helpers: turn a rendered clip's .ass file back into copyable text
# (plain lines) or a standard .srt file.
# ---------------------------------------------------------------------------

_TIME_RE = re.compile(r"(\d+):(\d{1,2}):(\d{1,2})[.,](\d{1,3})")
# ASS override blocks like {\fad(120,80)...} carry the burn-in styling only.
_OVERRIDE_TAGS_RE = re.compile(r"\{[^}]*\}")


def _ass_time_to_seconds(raw: str) -> float:
    m = _TIME_RE.fullmatch(raw.strip())
    if not m:
        raise ValueError(f"Unrecognized ASS timestamp: {raw!r}")
    h, mnt, s, frac = (int(g) for g in m.groups())
    return h * 3600 + mnt * 60 + s + frac / (10 ** len(m.group(4)))


def parse_ass_events(ass_text: str) -> List[Tuple[float, float, str]]:
    """Extract (start, end, text) cues from ASS subtitle text.

    Reads the [Events] Format header to locate the Text field, so both this
    repo's 5-field header and standard 10-field ASS files parse correctly.
    Override tags are stripped from the text.
    """
    lines = ass_text.splitlines()

    text_field_idx = 4  # this repo's header: Layer, Start, End, Style, Text
    in_events = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            in_events = stripped.lower().startswith("[events]")
            continue
        if in_events and stripped.startswith("Format:"):
            fields = [f.strip().lower() for f in stripped[len("Format:"):].split(",")]
            if "text" in fields:
                text_field_idx = fields.index("text")

    cues: List[Tuple[float, float, str]] = []
    in_events = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            in_events = stripped.lower().startswith("[events]")
            continue
        if not in_events or not stripped.startswith("Dialogue:"):
            continue
        payload = stripped[len("Dialogue:"):].strip()
        parts = payload.split(",", text_field_idx)
        if len(parts) <= text_field_idx:
            continue
        text = _OVERRIDE_TAGS_RE.sub("", ",".join(parts[text_field_idx:])).strip()
        if not text:
            continue
        cues.append(
            (_ass_time_to_seconds(parts[1]), _ass_time_to_seconds(parts[2]), text)
        )
    return cues


def captions_to_srt(cues: List[Tuple[float, float, str]]) -> str:
    blocks = []
    for i, (start, end, text) in enumerate(cues, start=1):
        blocks.append(f"{i}\n{_srt_time(start)} --> {_srt_time(end)}\n{text}")
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def _srt_time(t: float) -> str:
    if t < 0:
        t = 0
    # Round to whole milliseconds first so carries propagate correctly
    # (59.9995s -> 00:01:00,000, not 00:00:60,000).
    total_ms = int(round(t * 1000))
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def captions_to_plain_text(cues: List[Tuple[float, float, str]]) -> str:
    return "\n".join(text for _, _, text in cues)


def export_captions(ass_path: str, fmt: str = "txt") -> str:
    """Return the clip's captions as 'txt' (plain lines), 'srt', or raw 'ass'."""
    with open(ass_path, "r", encoding="utf-8") as f:
        ass_text = f.read()
    fmt = fmt.lower()
    if fmt == "ass":
        return ass_text
    cues = parse_ass_events(ass_text)
    if fmt == "srt":
        return captions_to_srt(cues)
    if fmt == "txt":
        return captions_to_plain_text(cues)
    raise ValueError(f"Unsupported caption format: {fmt}")
