"""
Downloads a YouTube video to disk using yt-dlp.

IMPORTANT: downloading and re-publishing YouTube content can conflict with
YouTube's Terms of Service depending on how it's used. This tool is intended
for clipping videos you own or have explicit permission to repurpose.
"""
import os

import yt_dlp


def download_video(url: str, out_dir: str) -> str:
    """Downloads `url` into `out_dir` and returns the local file path."""
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "source.%(ext)s")

    ydl_opts = {
        "format": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "outtmpl": out_path,
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
        # merge_output_format forces .mp4 even if prepare_filename guesses otherwise
        base, _ = os.path.splitext(filename)
        mp4_path = base + ".mp4"
        return mp4_path if os.path.exists(mp4_path) else filename
