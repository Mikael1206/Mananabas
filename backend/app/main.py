from concurrent.futures import ThreadPoolExecutor
from typing import List

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import init_db, get_session
from app.jobs import run_job
from app.models import Job
from app.schemas import JobCreateRequest, JobRead

app = FastAPI(title="Mananabas API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten this before deploying publicly
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve rendered clips directly, e.g. GET /media/3/clip_0.mp4
app.mount("/media", StaticFiles(directory=settings.media_dir), name="media")

# MVP-simple background execution. Swap for Celery + Redis when you need
# multiple workers or want jobs to survive an API restart.
_executor = ThreadPoolExecutor(max_workers=2)


@app.on_event("startup")
def on_startup():
    import os
    os.makedirs(settings.media_dir, exist_ok=True)
    init_db()


@app.post("/api/jobs", response_model=JobRead)
def create_job(payload: JobCreateRequest, session: Session = Depends(get_session)):
    job = Job(youtube_url=str(payload.youtube_url))
    session.add(job)
    session.commit()
    session.refresh(job)

    _executor.submit(run_job, job.id)
    return job


@app.get("/api/jobs/{job_id}", response_model=JobRead)
def get_job(job_id: int, session: Session = Depends(get_session)):
    statement = select(Job).options(selectinload(Job.clips)).where(Job.id == job_id)
    job = session.exec(statement).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/api/jobs", response_model=List[JobRead])
def list_jobs(session: Session = Depends(get_session)):
    statement = select(Job).options(selectinload(Job.clips)).order_by(Job.id.desc())
    return session.exec(statement).all()
