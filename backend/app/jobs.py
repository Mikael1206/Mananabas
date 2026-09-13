import os
import re
import traceback

from sqlmodel import Session

from app.config import settings
from app.database import engine
from app.models import Job, JobStatus, Clip

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _public_error(exc: BaseException) -> str:
    """One clean line for the UI; full traceback stays in the server log."""
    text = _ANSI_RE.sub("", str(exc)).replace("\r", "\n")
    if "exit -9" in text or "(-9)" in text or "137" in text[:80]:
        return (
            "ffmpeg ran out of memory on the server (killed with signal 9). "
            "Redeploy after the lite-render fix, or raise Railway RAM."
        )
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("ERROR:"):
            return line[:500]
        if line.startswith("ERROR:"):
            cleaned = line[len("ERROR:") :].strip()
            if cleaned:
                return cleaned[:500]
    return type(exc).__name__


def run_job(job_id: int) -> None:
    """
    Runs the full pipeline for a job. Called in a background thread from the
    API layer. Each stage updates job.status so the frontend can poll progress.

    NOTE: for production scale, replace the background-thread call site in
    main.py with a Celery task using this same function body — nothing here
    needs to change.
    """
    with Session(engine) as session:
        job = session.get(Job, job_id)
        if not job:
            return

        job_dir = os.path.join(settings.media_dir, str(job_id))
        os.makedirs(job_dir, exist_ok=True)

        try:
            settings.require_llm()
            # Import pipeline only when a job runs so `uvicorn app.main:app`
            # does not load OpenCV / yt-dlp / Whisper at boot (Railway OOM /
            # missing .so crashes).
            from app.pipeline import captions, downloader, highlighter, reframe, render, transcriber

            # 1. Download
            job.status = JobStatus.downloading
            job.progress_message = "Downloading source video..."
            session.add(job)
            session.commit()

            video_path = downloader.download_video(job.youtube_url, job_dir)
            job.video_path = video_path
            session.add(job)
            session.commit()

            # 2. Transcribe
            job.status = JobStatus.transcribing
            job.progress_message = "Transcribing audio..."
            session.add(job)
            session.commit()

            segments = transcriber.transcribe(video_path, language=job.language)
            transcript_text = transcriber.transcript_to_plain_text(segments)

            # 3. Rank highlights via LLM
            job.status = JobStatus.ranking
            job.progress_message = "Selecting best moments..."
            session.add(job)
            session.commit()

            highlights = highlighter.select_highlights(transcript_text)

            # 4. Reframe + caption + render each highlight
            job.status = JobStatus.rendering
            session.add(job)
            session.commit()

            for idx, h in enumerate(highlights[: settings.max_clips_per_job]):
                job.progress_message = f"Rendering clip {idx + 1}/{len(highlights)}..."
                session.add(job)
                session.commit()

                start, end = float(h["start"]), float(h["end"])
                center_x = reframe.find_crop_center_x(video_path, start, end)

                ass_path = os.path.join(job_dir, f"clip_{idx}.ass")
                captions.build_ass_for_clip(segments, start, end, ass_path)

                out_path = os.path.join(job_dir, f"clip_{idx}.mp4")
                render.render_clip(video_path, start, end, center_x, ass_path, out_path)

                clip = Clip(
                    job_id=job.id,
                    title=h.get("title", f"Clip {idx + 1}"),
                    hook=h.get("hook"),
                    score=h.get("score"),
                    start=start,
                    end=end,
                    file_path=out_path,
                )
                session.add(clip)
                session.commit()

            job.status = JobStatus.done
            job.progress_message = "Done."
            session.add(job)
            session.commit()

        except Exception as e:  # noqa: BLE001 - MVP: surface any failure to the UI
            job.status = JobStatus.failed
            job.error = _public_error(e)
            print(
                f"Job {job_id} failed: {e}\n{traceback.format_exc()}",
                file=__import__("sys").stderr,
            )
            try:
                session.add(job)
                session.commit()
            except Exception as commit_error:
                # If we can't even persist the failure, log it and re-raise
                # so the thread/executor reports a problem.
                print(
                    f"Failed to persist job failure for job {job_id}: {commit_error}",
                    file=__import__("sys").stderr,
                )
                raise commit_error from e
