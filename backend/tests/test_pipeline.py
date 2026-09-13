"""
Tests for the Mananabas standalone pipeline module.

Run from the project root with the backend venv active:

    python -m pytest backend/tests/test_pipeline.py -q

The env-dependent entrypoint (`run_pipeline`) validates its configuration at
import time. To test that behaviour without depending on a live API key, the
relevant tests import / reload that module inside the test body after setting
the environment accordingly.
"""
from __future__ import annotations

import importlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

# Make both pipeline entrypoints importable regardless of the CWD we run pytest from.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The env-gated module does not validate env at import time, so it is safe to
# import once at collection time and reuse across tests.
import run_pipeline_env_gated as rpg


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _clear_llm_env() -> None:
    for var in (
        "LLM_PROVIDER",
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_MODEL",
        "GEMINI_API_KEY",
        "GEMINI_MODEL",
    ):
        os.environ.pop(var, None)


@pytest.fixture(autouse=True)
def _reset_llm_env():
    """Keep each validation test from leaking config into the others."""
    before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(before)


def _reload_run_pipeline() -> Any:
    """Import (or reload) run_pipeline under the current os.environ."""
    import run_pipeline as target

    importlib.reload(target)
    return target


# ---------------------------------------------------------------------------
# Config / env validation (run_pipeline.py validates at import time)
# ---------------------------------------------------------------------------
def test_invalid_provider_fails_fast(capsys):
    _clear_llm_env()
    os.environ["LLM_PROVIDER"] = "bedrock"
    os.environ["OPENAI_API_KEY"] = "x"
    # Importing run_pipeline re-runs top-level validation, so guard against the
    # SystemExit at import time and only assert the printed message.
    try:
        import run_pipeline as target

        importlib.reload(target)
    except SystemExit as exc:
        captured = capsys.readouterr()
        assert "Unknown LLM_PROVIDER" in str(exc)
        return
    # If validation were not at import time for some reason, check main().
    with pytest.raises(SystemExit) as exc:
        target.main()
    assert "Unknown LLM_PROVIDER" in str(exc.value)


def test_missing_key_fails_fast(capsys):
    _clear_llm_env()
    os.environ["LLM_PROVIDER"] = "openai"
    try:
        import run_pipeline as target

        importlib.reload(target)
    except SystemExit as exc:
        captured = capsys.readouterr()
        assert "OPENAI_API_KEY is not set" in str(exc)
        return
    with pytest.raises(SystemExit) as exc:
        target.main()
    assert "OPENAI_API_KEY is not set" in str(exc.value)


def test_env_gated_module_does_not_validate_on_import():
    _clear_llm_env()
    os.environ["LLM_PROVIDER"] = "openai"
    # Importing the env-gated module should not raise even without a key.
    import run_pipeline_env_gated as rpg2  # noqa: F811

    assert rpg2.LLM_PROVIDER == "openai"


# ---------------------------------------------------------------------------
# Config loading semantics (mirrors app/config.py defaults)
# ---------------------------------------------------------------------------
def test_defaults_are_set_even_without_env():
    _clear_llm_env()
    assert rpg.LLM_PROVIDER == "openai"
    assert rpg.WHISPER_MODEL_SIZE == "tiny"
    assert rpg.WHISPER_DEVICE == "cpu"
    assert rpg.MEDIA_DIR == "./media"
    assert rpg.MAX_CLIPS_PER_JOB == 5
    assert rpg.CLIP_MIN_SECONDS == 20
    assert rpg.CLIP_MAX_SECONDS == 90


# ---------------------------------------------------------------------------
# Highlight JSON extraction + cleaning
# ---------------------------------------------------------------------------
def test_extract_json_handles_prose_wrap():
    text = (
        "Here is your JSON:\n\n[\n  "
        '{"start": 1.0, "end": 5.0, "title": "x", "hook": "h", "score": 80}'
        "\n]\nGreat, done."
    )
    extracted = rpg._extract_json(text)
    parsed = json.loads(extracted)
    assert parsed == [
        {"start": 1.0, "end": 5.0, "title": "x", "hook": "h", "score": 80}
    ]


def test_extract_json_handles_fenced_markdown():
    text = "```json\n[{\"start\": 0.5, \"end\": 6.0, \"title\": \"t\", \"hook\": \"h\", \"score\": 90}]\n```"
    extracted = rpg._extract_json(text)
    parsed = json.loads(extracted)
    assert parsed == [
        {"start": 0.5, "end": 6.0, "title": "t", "hook": "h", "score": 90}
    ]


def test_extracted_json_parsed_by_select_highlights_path():
    good = [{"start": 1.0, "end": 5.0, "title": "A", "hook": "h", "score": 90}]
    bad_overlap = [{"start": 10.0, "end": 8.0}]  # end < start
    missing_key = [{"start": 1.0}]  # missing end/title

    # Cleaning logic lives inline in select_highlights; replicate here for a focused
    # unit test without touching any LLM provider.
    def _clean(highlights: List[Dict]) -> List[Dict]:
        clean: List[Dict] = []
        for h in highlights:
            try:
                if float(h["end"]) > float(h["start"]):
                    clean.append(h)
            except (KeyError, TypeError, ValueError):
                continue
        return clean

    assert _clean(good + bad_overlap + missing_key) == good


# ---------------------------------------------------------------------------
# Transcript helper
# ---------------------------------------------------------------------------
def test_transcript_to_plain_text_format():
    segments = [
        rpg.Segment(start=3.2, end=7.8, text="Hello world.", words=[]),
        rpg.Segment(start=65.0, end=68.0, text="Final line.", words=[]),
    ]
    out = rpg.transcript_to_plain_text(segments)
    assert out == "[00:03] Hello world.\n[01:05] Final line."


# ---------------------------------------------------------------------------
# Caption (.ass) generation boundaries
# ---------------------------------------------------------------------------
def _make_segments() -> List[rpg.Segment]:
    return [
        rpg.Segment(
            start=10.0,
            end=14.0,
            text="First segment.",
            words=[
                rpg.Word(start=10.0, end=10.5, text="First"),
                rpg.Word(start=10.5, end=11.0, text="segment"),
                rpg.Word(start=11.0, end=11.5, text="one"),
                rpg.Word(start=11.5, end=12.0, text="."),
                rpg.Word(start=13.0, end=13.5, text="tail"),
            ],
        ),
        rpg.Segment(
            start=20.0,
            end=25.0,
            text="Second segment.",
            words=[
                rpg.Word(start=20.0, end=20.5, text="Second"),
                rpg.Word(start=20.5, end=21.0, text="segment"),
                rpg.Word(start=21.0, end=21.5, text="two"),
                rpg.Word(start=21.5, end=22.0, text="."),
            ],
        ),
    ]


def test_ass_only_includes_words_inside_clip_window(tmp_path: Path):
    segments = _make_segments()
    out_path = tmp_path / "clip.ass"
    rpg._build_ass_for_clip(segments, clip_start=10.0, clip_end=12.0, out_path=out_path)
    text = out_path.read_text(encoding="utf-8")
    assert "[Script Info]" in text
    assert "[V4+ Styles]" in text
    assert "[Events]" in text
    # The second segment (20-25s) should be fully excluded.
    assert "Second segment" not in text
    # The first segment's first 4-word chunk (10-12s) should be present.
    assert "First segment one ." in text


def test_ass_timing_is_relative_to_clip_start(tmp_path: Path):
    segments = _make_segments()
    out_path = tmp_path / "clip.ass"
    rpg._build_ass_for_clip(segments, clip_start=10.0, clip_end=12.0, out_path=out_path)
    text = out_path.read_text(encoding="utf-8")
    # The ass header uses the ASS time format H:MM:SS.cs (no leading zeros on hours).
    assert "Dialogue: 0,0:00:00.00," in text
    # Last word of the included chunk ends at 12.0 -> relative 0:00:02.00
    assert "0:00:02.00,Default,First segment one ." in text


def test_ass_handles_partial_word_overlap_at_clip_boundaries(tmp_path: Path):
    segments = _make_segments()
    out_path = tmp_path / "clip.ass"
    # Clip starts mid-word and ends mid-word of the first segment.
    rpg._build_ass_for_clip(segments, clip_start=10.3, clip_end=11.3, out_path=out_path)
    text = out_path.read_text(encoding="utf-8")
    # With clip_start=10.3, the word "First" (10.0-10.5) starts before the clip,
    # so our current chunking starts the first chunk at word end=10.5 (seg start).
    # This means the visible caption text comes from the remaining words in that chunk.
    assert "segment" in text
    # Relative chunk start should be 10.5 - 10.3 = 0.2s.
    assert "0:00:00.20" in text


# ---------------------------------------------------------------------------
# Reframe / crop center + render filters
# ---------------------------------------------------------------------------
import numpy as np

from app.pipeline import reframe as reframe_mod
from app.pipeline import render as render_mod


def test_aggregate_centers_prefers_faces():
    assert reframe_mod.aggregate_centers([0.2, 0.4], [0.9], [0.1]) == pytest.approx(0.3)


def test_aggregate_centers_falls_back_to_motion_and_energy():
    # 0.6 * 0.8 + 0.4 * 0.2 = 0.56
    assert reframe_mod.aggregate_centers([], [0.8], [0.2]) == pytest.approx(0.56)


def test_aggregate_centers_empty_is_center():
    assert reframe_mod.aggregate_centers([], [], []) == pytest.approx(0.5)


def test_energy_center_tracks_detailed_object():
    frame = np.zeros((120, 200, 3), dtype=np.uint8)
    # High-frequency checkerboard on the right side = the "subject".
    x0, x1 = 140, 190
    patch = np.indices((80, x1 - x0)).sum(axis=0) % 2 * 255
    frame[20:100, x0:x1, :] = patch[:, :, None].astype(np.uint8)
    cx = reframe_mod.energy_center_x(frame)
    assert cx > 0.6


def test_crop_window_centers_on_subject_and_clamps():
    crop_w, crop_h, x = render_mod.crop_window(1920, 1080, 0.2)
    assert crop_h == 1080
    assert crop_w == int(1080 * 9 / 16)
    # Subject at 20% of 1920 = 384; window should start near that minus half width.
    assert x == max(0, int(0.2 * 1920 - crop_w / 2))
    _, _, x_right = render_mod.crop_window(1920, 1080, 0.99)
    assert x_right == 1920 - crop_w


def test_video_filter_includes_effects_and_animation():
    vf = render_mod.build_video_filter(608, 1080, 100, duration=20.0, ass_path="/tmp/clip.ass", fps=30)
    assert "crop=608:1080:100:0" in vf
    assert "zoompan=" in vf
    assert "eq=" in vf
    assert "unsharp=" in vf
    assert "vignette=" in vf
    assert "fade=t=in" in vf
    assert "fade=t=out" in vf
    assert "ass=" in vf


def test_audio_filter_includes_bgm_and_stings():
    af = render_mod.build_audio_filter(20.0, has_speech=True)
    assert "[0:a]" in af
    assert "amix=inputs=4" in af
    assert "[open]" in af
    assert "adelay=" in af
    assert "[end]" in af
    assert "[aout]" in af


def test_caption_animation_tags_are_applied(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-for-import")
    from app.pipeline.captions import build_ass_for_clip, _animate_caption
    from app.pipeline.transcriber import Segment, Word

    segments = [
        Segment(
            start=10.0,
            end=14.0,
            text="First segment.",
            words=[
                Word(start=10.0, end=10.5, text="First"),
                Word(start=10.5, end=11.0, text="segment"),
                Word(start=11.0, end=11.5, text="one"),
                Word(start=11.5, end=12.0, text="."),
            ],
        )
    ]
    out_path = tmp_path / "clip.ass"
    build_ass_for_clip(segments, 10.0, 12.0, str(out_path))
    text = out_path.read_text(encoding="utf-8")
    assert r"\fad(120,80)" in text
    assert "First segment one ." in text
    assert _animate_caption("hi").endswith("hi")


def test_render_clip_builds_vertical_clip_with_audio(tmp_path: Path):
    ffmpeg = shutil.which("ffmpeg") or render_mod._ffmpeg_exe()
    src = tmp_path / "src.mp4"
    ass = tmp_path / "clip.ass"
    out = tmp_path / "out.mp4"
    ass.write_text(
        """[Script Info]
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
Dialogue: 0,0:00:00.00,0:00:01.50,Default,{\\fad(120,80)}Test caption
"""
    )
    subprocess.run(
        [
            ffmpeg, "-y",
            "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=15",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
            "-t", "2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            str(src),
        ],
        check=True,
        capture_output=True,
    )
    render_mod.render_clip(str(src), 0.0, 1.6, 0.5, str(ass), str(out))
    assert out.exists() and out.stat().st_size > 0
    cap = __import__("cv2").VideoCapture(str(out))
    w = int(cap.get(__import__("cv2").CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(__import__("cv2").CAP_PROP_FRAME_HEIGHT))
    cap.release()
    assert (w, h) == (1080, 1920)


# ---------------------------------------------------------------------------
# Caption export: parse ASS events, convert to SRT / plain text
# ---------------------------------------------------------------------------
def test_parse_ass_events_strips_override_tags(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-for-import")
    from app.pipeline.captions import parse_ass_events

    # Raw string: \f / \t must stay literal backslash sequences like in a real
    # .ass file (a real form-feed char would be split on by str.splitlines()).
    ass = r"""[Script Info]
ScriptType: v4.00+

[Events]
Format: Layer, Start, End, Style, Text
Dialogue: 0,0:00:00.00,0:00:02.00,Default,{\fad(120,80)\t(0,180,\fscx112\fscy112)\t(180,360,\fscx100\fscy100)}First segment one .
Dialogue: 0,0:00:02.20,0:00:03.50,Default,{\fad(120,80)}tail words
"""
    cues = parse_ass_events(ass)
    assert cues == [
        (0.0, 2.0, "First segment one ."),
        (2.2, 3.5, "tail words"),
    ]


def test_parse_ass_events_handles_standard_10_field_header(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-for-import")
    from app.pipeline.captions import parse_ass_events

    ass = """[Events]
Format: Marked, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: M 0,0:00:01.00,0:00:02.50,Default,,0,0,0,,Hello there
"""
    cues = parse_ass_events(ass)
    assert cues == [(1.0, 2.5, "Hello there")]


def test_captions_to_srt_output(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-for-import")
    from app.pipeline.captions import captions_to_srt

    srt = captions_to_srt([(0.0, 2.0, "First segment one ."), (2.2, 3.5, "tail words")])
    assert (
        srt == "1\n00:00:00,000 --> 00:00:02,000\nFirst segment one .\n\n"
        "2\n00:00:02,200 --> 00:00:03,500\ntail words\n"
    )


def test_captions_to_srt_handles_rounding_carry_and_clamps_negative(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-for-import")
    from app.pipeline.captions import captions_to_srt

    # 59.9999s rounds up to 60000ms -> carries into the next minute/second.
    srt = captions_to_srt([(59.9999, 61.0, "carry test")])
    assert srt == "1\n00:01:00,000 --> 00:01:01,000\ncarry test\n"

    # Negative times (defensive) clamp to zero.
    srt2 = captions_to_srt([(-0.5, 1.0, "neg")])
    assert srt2 == "1\n00:00:00,000 --> 00:00:01,000\nneg\n"


def test_captions_to_plain_text_joins_cue_text_only(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-for-import")
    from app.pipeline.captions import captions_to_plain_text

    text = captions_to_plain_text([(0.0, 2.0, "a"), (2.2, 3.5, "b")])
    assert text == "a\nb"
    assert captions_to_plain_text([]) == ""


def test_export_captions_supports_txt_srt_ass(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-for-import")
    from app.pipeline.captions import export_captions

    ass_path = tmp_path / "clip_0.ass"
    ass_path.write_text(
        """[Script Info]
ScriptType: v4.00+

[Events]
Format: Layer, Start, End, Style, Text
Dialogue: 0,0:00:00.00,0:00:02.00,Default,{\\fad(120,80)}First segment one .
""",
        encoding="utf-8",
    )
    assert export_captions(str(ass_path), fmt="txt") == "First segment one ."
    assert export_captions(str(ass_path), fmt="srt").startswith("1\n00:00:00,000")
    assert "[Script Info]" in export_captions(str(ass_path), fmt="ass")
    with pytest.raises(ValueError):
        export_captions(str(ass_path), fmt="vtt")


def test_transcribe_forwards_language_to_model(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-for-import")
    from app.pipeline import transcriber as transcriber_mod

    class FakeWord:
        start = 0.0
        end = 0.5
        word = "hello"

    class FakeSeg:
        start = 0.0
        end = 0.5
        text = "hello"
        words = [FakeWord()]

    class FakeInfo:
        language = "tl"
        language_probability = 0.97

    class FakeModel:
        def __init__(self):
            self.calls = []

        def transcribe(self, video_path, **kwargs):
            self.calls.append({"video_path": video_path, **kwargs})
            return iter([FakeSeg()]), FakeInfo()

    fake = FakeModel()
    monkeypatch.setattr(transcriber_mod, "_get_model", lambda: fake)

    segments = transcriber_mod.transcribe("/tmp/video.mp4", language="tl")

    assert len(segments) == 1
    assert segments[0].text == "hello"
    assert fake.calls
    assert fake.calls[0]["language"] == "tl"
    assert fake.calls[0]["word_timestamps"] is True


def test_download_error_is_treated_as_transient():
    from app.pipeline.downloader import _is_transient

    assert _is_transient(
        RuntimeError(
            "Failed to resolve 'rr5---sn-ajhoaq-5i.googlevideo.com' "
            "([Errno -2] Name or service not known)"
        )
    )
    assert not _is_transient(RuntimeError("Private video"))


def test_download_retries_then_succeeds(monkeypatch):
    from app.pipeline import downloader as dl
    from yt_dlp.utils import DownloadError

    calls = {"n": 0}

    def _fail_twice(url, out_path):
        calls["n"] += 1
        if calls["n"] < 3:
            raise DownloadError("Failed to resolve host ([Errno -2] Name or service not known)")
        return "/tmp/source.mp4"

    monkeypatch.setattr(dl, "_download_once", _fail_twice)
    monkeypatch.setattr(dl.time, "sleep", lambda _s: None)
    assert dl.download_video("https://youtu.be/x", "/tmp/out") == "/tmp/source.mp4"
    assert calls["n"] == 3
