import os
from concurrent.futures import ThreadPoolExecutor
from typing import List

from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, select
from sqlalchemy.orm import selectinload

from app.auth import create_access_token, get_current_user, verify_google_id_token
from app.config import settings
from app.database import init_db, get_session
from app.models import Clip, Job, User
from app.schemas import (
    AuthResponse,
    GoogleLoginRequest,
    JobCreateRequest,
    JobRead,
    UserRead,
)

app = FastAPI(title="Mananabas API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://mananabas.vercel.app",
        "https://www.mananabas.vercel.app",
    ],
    allow_origin_regex=(
        r"https://.*\.vercel\.app"
        r"|http://localhost:\d+"
        r"|http://127\.0\.0\.1:\d+"
    ),
    allow_methods=["*"],
    allow_headers=["*"],
)

def _media_root() -> str:
    path = settings.media_dir
    try:
        os.makedirs(path, exist_ok=True)
        return path
    except OSError:
        path = "/tmp/mananabas-media"
        os.makedirs(path, exist_ok=True)
        settings.media_dir = path
        return path


# Create the media folder before mounting: Starlette StaticFiles checks the
# directory at startup, which crashes the replica if ./media is missing.
app.mount("/media", StaticFiles(directory=_media_root()), name="media")

# MVP-simple background execution. Swap for Celery + Redis when you need
# multiple workers or want jobs to survive an API restart.
_executor = ThreadPoolExecutor(max_workers=2)


@app.on_event("startup")
def on_startup():
    import os
    os.makedirs(settings.media_dir, exist_ok=True)
    init_db()


@app.get("/")
def root():
    """Browser/health check for the API process (the UI lives on the frontend)."""
    return {
        "name": "Mananabas API",
        "status": "ok",
        "docs": "/docs",
        "jobs": "/api/jobs",
    }


@app.post("/api/auth/google", response_model=AuthResponse)
def login_with_google(payload: GoogleLoginRequest, session: Session = Depends(get_session)):
    auth_err = settings.google_auth_error()
    if auth_err:
        raise HTTPException(status_code=503, detail=auth_err)

    try:
        claims = verify_google_id_token(payload.credential)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid Google credential.")

    google_sub = claims["sub"]
    email = claims.get("email", "")
    name = claims.get("name")
    picture_url = claims.get("picture")

    user = session.exec(select(User).where(User.google_sub == google_sub)).first()
    if user:
        # Refresh profile fields in case the user changed their Google name/photo.
        user.email = email
        user.name = name
        user.picture_url = picture_url
    else:
        user = User(google_sub=google_sub, email=email, name=name, picture_url=picture_url)
    session.add(user)
    session.commit()
    session.refresh(user)

    token = create_access_token(user.id)
    return AuthResponse(access_token=token, user=UserRead.model_validate(user))


@app.get("/api/me", response_model=UserRead)
def get_me(current_user: User = Depends(get_current_user)):
    return current_user


@app.post("/api/jobs", response_model=JobRead)
def create_job(
    payload: JobCreateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    llm_err = settings.llm_key_error()
    if llm_err:
        raise HTTPException(status_code=503, detail=llm_err)

    job = Job(
        user_id=current_user.id,
        youtube_url=str(payload.youtube_url),
        language=payload.language,
    )
    session.add(job)
    session.commit()
    session.refresh(job)

    from app.jobs import run_job

    _executor.submit(run_job, job.id)
    return job


@app.get("/api/clips/{clip_id}/captions", response_class=PlainTextResponse)
def get_clip_captions(
    clip_id: int,
    format: str = Query("txt", pattern="^(txt|srt|ass)$"),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """Return a clip's generated captions as text: plain lines ('txt'),
    standard subtitles ('srt'), or the raw Advanced SubStation file ('ass').
    The frontend fetches this to power its copy/download buttons."""
    clip = session.get(Clip, clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")

    job = session.get(Job, clip.job_id)
    if not job or job.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Clip not found")

    # jobs.py writes clip_{idx}.ass next to clip_{idx}.mp4 in creation order,
    # so the caption file index is this clip's position among its job's clips.
    sibling_ids = session.exec(
        select(Clip.id).where(Clip.job_id == clip.job_id).order_by(Clip.id)
    ).all()
    if clip.id not in sibling_ids:
        raise HTTPException(status_code=404, detail="Clip not found")
    idx = sibling_ids.index(clip.id)

    ass_path = os.path.join(settings.media_dir, str(clip.job_id), f"clip_{idx}.ass")
    if not os.path.exists(ass_path):
        raise HTTPException(status_code=404, detail="Captions not found for this clip")

    try:
        from app.pipeline import captions as captions_pipeline

        content = captions_pipeline.export_captions(ass_path, fmt=format)
    except ValueError:
        raise HTTPException(status_code=400, detail="Unsupported caption format")

    return PlainTextResponse(
        content,
        headers={
            "Content-Disposition": f'attachment; filename="clip_{idx}.{format}"'
        },
    )


@app.get("/api/jobs/{job_id}", response_model=JobRead)
def get_job(
    job_id: int,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    statement = select(Job).options(selectinload(Job.clips)).where(Job.id == job_id)
    job = session.exec(statement).first()
    if not job or job.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/api/jobs", response_model=List[JobRead])
def list_jobs(
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """A user's clip history: every job they've submitted, newest first."""
    statement = (
        select(Job)
        .options(selectinload(Job.clips))
        .where(Job.user_id == current_user.id)
        .order_by(Job.id.desc())
    )
    return session.exec(statement).all()
