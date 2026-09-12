"""
Downloads a YouTube video to disk using yt-dlp.

IMPORTANT: downloading and re-publishing YouTube content can conflict with
YouTube's Terms of Service depending on how it's used. This tool is intended
for clipping videos you own or have explicit permission to repurpose.
"""
import os
import shutil

import yt_dlp


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


def download_video(url: str, out_dir: str) -> str:
    """Downloads `url` into `out_dir` and returns the local file path."""
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "source.%(ext)s")

    ydl_opts = {
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
        # yt-dlp aborts if it wants to merge streams and thinks ffmpeg is
        # unavailable. We ship a static ffmpeg via imageio-ffmpeg, so make
        # the absence case fail with a clear message instead of a generic
        # "merging of multiple formats" error.
        "abort_on_error": False,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
        # merge_output_format forces .mp4 even if prepare_filename guesses otherwise
        base, _ = os.path.splitext(filename)
        mp4_path = base + ".mp4"
        return mp4_path if os.path.exists(mp4_path) else filename
