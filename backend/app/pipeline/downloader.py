"""
Downloads a YouTube video to disk using yt-dlp.

IMPORTANT: downloading and re-publishing YouTube content can conflict with
YouTube's Terms of Service depending on how it's used. This tool is intended
for clipping videos you own or have explicit permission to repurpose.
"""
import base64
import os
import shutil
import time
from typing import Optional

import yt_dlp
from yt_dlp.utils import DownloadError

from app.config import settings

_TRANSIENT_MARKERS = (
    "name or service not known",
    "failed to resolve",
    "temporary failure in name resolution",
    "connection reset",
    "timed out",
    "timeout",
    "network is unreachable",
    "connection refused",
    "http error 403",
    "http error 429",
    "http error 5",
    "unable to download",
    "gave up after",
    "sign in to confirm",
    "not a bot",
    "requested format is not available",
    "page needs to be reloaded",
)

# Ordered list of fallback client + format strategies.
# Mobile and TV clients bypass many bot-detection / PO-token prompts.
_CLIENT_STRATEGIES = [
    {
        "clients": ["mweb", "android", "ios"],
        "format": "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
    },
    {
        "clients": ["tv", "mweb", "android"],
        "format": "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
    },
    {
        "clients": ["ios", "mweb"],
        "format": "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
    },
    {
        "clients": ["android", "web"],
        "format": "bestvideo[height<=1080][ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]/best[ext=mp4]/best",
    },
]


def _ffmpeg_location() -> str:
    system = shutil.which("ffmpeg")
    if system:
        return system
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def _is_transient(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _TRANSIENT_MARKERS)


def _get_cookies_file() -> Optional[str]:
    """Return a path to a cookies file if configured via settings or env vars."""
    cookie_file_path = (
        settings.youtube_cookies_file
        or os.environ.get("YOUTUBE_COOKIES_FILE", "")
        or os.environ.get("COOKIE_FILE", "")
    )
    if cookie_file_path and os.path.exists(cookie_file_path):
        return cookie_file_path

    cookies_content = (
        settings.youtube_cookies
        or os.environ.get("YOUTUBE_COOKIES", "")
        or os.environ.get("YOUTUBE_COOKIES_TXT", "")
    )
    if not cookies_content:
        return None

    # Handle base64-encoded cookie text if provided
    if cookies_content.startswith("base64:"):
        try:
            cookies_content = base64.b64decode(cookies_content[7:]).decode("utf-8")
        except Exception:
            pass

    try:
        temp_dir = os.path.join(settings.media_dir, ".cookies")
        os.makedirs(temp_dir, exist_ok=True)
        temp_file = os.path.join(temp_dir, "yt_cookies.txt")
        with open(temp_file, "w", encoding="utf-8") as f:
            f.write(cookies_content)
        return temp_file
    except Exception:
        return None


def _ydl_opts(out_path: str, strategy_idx: int = 0) -> dict:
    strat = _CLIENT_STRATEGIES[min(strategy_idx, len(_CLIENT_STRATEGIES) - 1)]
    opts = {
        "format": strat["format"],
        "outtmpl": out_path,
        "merge_output_format": "mp4",
        "ffmpeg_location": _ffmpeg_location(),
        "quiet": True,
        "no_warnings": True,
        "abort_on_error": False,
        "continuedl": True,
        "retries": 15,
        "fragment_retries": 15,
        "extractor_retries": 5,
        "file_access_retries": 5,
        "retry_sleep_functions": {"http": lambda n: min(8.0, 1.5**n)},
        "socket_timeout": 30,
        "source_address": "0.0.0.0",
        "legacy_server_connect": True,
        "extractor_args": {
            "youtube": {
                "player_client": strat["clients"],
            }
        },
    }

    cookies_path = _get_cookies_file()
    if cookies_path:
        opts["cookiefile"] = cookies_path

    return opts


def _download_once(url: str, out_path: str, strategy_idx: int = 0) -> str:
    with yt_dlp.YoutubeDL(_ydl_opts(out_path, strategy_idx)) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
        base, _ = os.path.splitext(filename)
        mp4_path = base + ".mp4"
        return mp4_path if os.path.exists(mp4_path) else filename


def download_video(url: str, out_dir: str, attempts: int = 4) -> str:
    """Downloads `url` into `out_dir` and returns the local file path."""
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "source.%(ext)s")
    last_error: BaseException | None = None

    for attempt in range(1, max(attempts, 1) + 1):
        strategy_idx = (attempt - 1) % len(_CLIENT_STRATEGIES)
        try:
            return _download_once(url, out_path, strategy_idx=strategy_idx)
        except DownloadError as exc:
            last_error = exc
            if attempt >= attempts or not _is_transient(exc):
                raise
            delay = min(8.0, 1.5**attempt)
            print(
                f"Download attempt {attempt}/{attempts} (strategy {strategy_idx + 1}) "
                f"failed ({exc}); retrying in {delay:.1f}s...",
                flush=True,
            )
            time.sleep(delay)

    raise last_error  # pragma: no cover
