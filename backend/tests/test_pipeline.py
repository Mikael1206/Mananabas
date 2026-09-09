"""
Tests for the Pungol standalone pipeline module.

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
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

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
# Reframe / crop center (no real video required)
# ---------------------------------------------------------------------------
def _patch_run_pipeline_env_gated(monkeypatch):
    """Install lightweight, import-time-safe patches for cv2 and subprocess.run
    inside the env-gated pipeline module under test."""
    import run_pipeline_env_gated as target

    importlib.reload(target)
    mock_cv2 = MagicMock()
    mock_run = MagicMock()
    target.cv2 = mock_cv2
    target.subprocess = MagicMock(run=mock_run)
    return target, mock_cv2, mock_run


def test_find_crop_center_returns_0_5_when_no_faces_detected(monkeypatch):
    fake_video = Path(tempfile.mktemp(suffix=".mp4"))
    try:
        target, cv2, _ = _patch_run_pipeline_env_gated(monkeypatch)
        mock_cap = MagicMock()
        mock_cap.get.return_value = 0  # not 30; we just need a float for fps
        mock_cap.read.return_value = (False, None)  # no frames at all
        mock_cap.release = MagicMock()
        cv2.VideoCapture.return_value = mock_cap

        center = target._find_crop_center_x(fake_video, 0.0, 5.0)
        assert center == pytest.approx(0.5)
    finally:
        if fake_video.exists():
            fake_video.unlink()


def test_find_crop_center_uses_largest_face(monkeypatch):
    fake_video = Path(tempfile.mktemp(suffix=".mp4"))
    try:
        target, cv2, _ = _patch_run_pipeline_env_gated(monkeypatch)

        cv2.CAP_PROP_FPS = 0
        cv2.CAP_PROP_FRAME_WIDTH = 1
        cv2.CAP_PROP_FRAME_HEIGHT = 2

        mock_cap = MagicMock()

        def _cap_get(property_):
            if property_ == cv2.CAP_PROP_FPS:
                return 30.0
            if property_ == cv2.CAP_PROP_FRAME_WIDTH:
                return 1920.0
            if property_ == cv2.CAP_PROP_FRAME_HEIGHT:
                return 1080.0
            return 0.0

        mock_cap.get.side_effect = _cap_get
        cv2.VideoCapture.return_value = mock_cap

        frame1 = MagicMock()
        frame1.shape = (1080, 1920, 3)
        frame2 = MagicMock()
        frame2.shape = (1080, 1920, 3)

        mock_cap.read.side_effect = [(True, frame1), (True, frame2)] + [(False, None)] * 60

        gray1 = MagicMock()
        gray2 = MagicMock()
        cv2.cvtColor.side_effect = [gray1, gray2]

        cascade = MagicMock()
        cascade.detectMultiScale.side_effect = [
            [(100, 100, 200, 200)],  # center 200/1920
            [(300, 300, 500, 500)],  # center 550/1920
        ]
        cv2.CascadeClassifier.return_value = cascade

        import run_pipeline_env_gated as real_target
        import importlib
        importlib.reload(real_target)
        _test_cv2 = MagicMock()
        _test_cv2.CAP_PROP_FRAME_WIDTH = 1
        _test_cv2.CAP_PROP_FRAME_HEIGHT = 2
        _test_cv2.CAP_PROP_FPS = 0

        _cap = MagicMock()
        def _get(prop):
            if prop == _test_cv2.CAP_PROP_FPS:
                return 30.0
            if prop == _test_cv2.CAP_PROP_FRAME_WIDTH:
                return 1920.0
            if prop == _test_cv2.CAP_PROP_FRAME_HEIGHT:
                return 1080.0
            return 0.0
        _cap.get.side_effect = _get
        _cap.set.return_value = None
        _cap.release = MagicMock()
        _test_cv2.VideoCapture.return_value = _cap

        _frame1 = MagicMock()
        _frame1.shape = (1080, 1920, 3)
        _frame2 = MagicMock()
        _frame2.shape = (1080, 1920, 3)
        _cap.read.side_effect = [(True, _frame1), (True, _frame2)] + [(False, None)] * 60

        _gray1 = MagicMock()
        _gray2 = MagicMock()
        _test_cv2.cvtColor.side_effect = [_gray1, _gray2]

        _cascade = MagicMock()
        _cascade.detectMultiScale.side_effect = [
            [(100, 100, 200, 200)],  # center 200/1920
            [(300, 300, 500, 500)],  # center 550/1920
        ]
        _test_cv2.CascadeClassifier.return_value = _cascade

        import sys
        _saved_cv2 = sys.modules.get('cv2', None)
        sys.modules['cv2'] = _test_cv2
        try:
            actual = real_target._find_crop_center_x(fake_video, 0.0, 2.0, sample_count=2)
        finally:
            sys.modules['cv2'] = _saved_cv2
        # Per-frame face geometry configured above:
        #   frame 0: fx=100, fw=200 -> center 200/1920
        #   frame 1: fx=300, fw=500 -> center 550/1920
        expected = (200 / 1920 + 550 / 1920) / 2
        print("crop test actual:", actual, "expected:", expected)
        assert actual == pytest.approx(expected)
    finally:
        if fake_video.exists():
            fake_video.unlink()


def _patch_run_pipeline_env_gated_no_monkeypatch():
    import run_pipeline_env_gated as target
    importlib.reload(target)
    mock_cv2 = MagicMock()
    mock_run = MagicMock()
    mock_subprocess = MagicMock(run=mock_run)
    target.cv2 = mock_cv2
    target.subprocess = mock_subprocess
    return target, mock_subprocess, mock_cv2
