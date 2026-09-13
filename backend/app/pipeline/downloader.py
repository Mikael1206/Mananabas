"""
Downloads a YouTube video to disk using yt-dlp.

IMPORTANT: downloading and re-publishing YouTube content can conflict with
YouTube's Terms of Service depending on how it's used. This tool is intended
for clipping videos you own or have explicit permission to repurpose.
"""
import os
import shutil
import time

import yt_dlp
from yt_dlp.utils import DownloadError

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
)


def _ffmpeg_location() -> str:
    """Return a path to a usable ffmpeg binary for yt-dlp's format merging.

    yt-dlp spawns `ffmpeg` from PATH to merge separate video+audio streams.
    On machines without a system ffmpeg, fall back to the static binary
    shipped with the imageio-ffmpeg pip package.
    """
    system = shutil.which("ffmpeg")
    if system:
        return system
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def _is_transient(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in _TRANSIENT_MARKERS)


def _ydl_opts(out_path: str) -> dict:
    return {
        # Prefer H.264 (avc1): OpenCV (face detection) and most ffmpeg builds
        # can't decode AV1/VP9 reliably. The `/best` fallback rarely triggers.
        "format": (
            "bestvideo[height<=1080][ext=mp4][vcodec^=avc1]"
            "+bestaudio[ext=m4a]/best[ext=mp4]/best"
        ),
        "outtmpl": out_path,
        "merge_output_format": "mp4",
        "ffmpeg_location": _ffmpeg_location(),
        "quiet": True,
        "no_warnings": True,
        "abort_on_error": False,
        # Mid-file CDN hostnames often fail DNS; resume + retry a new URL.
        "continuedl": True,
        "retries": 15,
        "fragment_retries": 15,
        "extractor_retries": 5,
        "file_access_retries": 5,
        "retry_sleep_functions": {"http": lambda n: min(8.0, 1.5 ** n)},
        "socket_timeout": 30,
        # Force IPv4. googlevideo AAAA records frequently fail to resolve
        # on home routers even when IPv4 works.
        "source_address": "0.0.0.0",
        "legacy_server_connect": True,
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "web"],
            }
        },
    }


def _download_once(url: str, out_path: str) -> str:
    with yt_dlp.YoutubeDL(_ydl_opts(out_path)) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
        base, _ = os.path.splitext(filename)
        mp4_path = base + ".mp4"
        return mp4_path if os.path.exists(mp4_path) else filename


def download_video(url: str, out_dir: str, attempts: int = 4) -> str:
    """Downloads `url` into `out_dir` and returns the local file path.

    YouTube CDN hostnames rotate; a DNS miss mid-download is retried so
    yt-dlp can pick a new googlevideo host.
    """
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "source.%(ext)s")
    last_error: BaseException | None = None

    for attempt in range(1, max(attempts, 1) + 1):
        try:
            return _download_once(url, out_path)
        except DownloadError as exc:
            last_error = exc
            if attempt >= attempts or not _is_transient(exc):
                raise
            delay = min(8.0, 1.5 ** attempt)
            print(
                f"Download attempt {attempt}/{attempts} failed ({exc}); "
                f"retrying in {delay:.1f}s...",
                flush=True,
            )
            time.sleep(delay)

    raise last_error  # pragma: no cover
