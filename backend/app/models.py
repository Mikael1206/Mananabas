import enum
from datetime import datetime
from typing import Optional

from sqlmodel import SQLModel, Field


class JobStatus(str, enum.Enum):
    queued = "queued"
    downloading = "downloading"
    transcribing = "transcribing"
    ranking = "ranking"
    rendering = "rendering"
    done = "done"
    failed = "failed"


class Job(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    youtube_url: str
    status: JobStatus = Field(default=JobStatus.queued)
    progress_message: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    video_path: Optional[str] = None


class Clip(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="job.id")
    title: str
    hook: Optional[str] = None
    score: Optional[float] = None
    start: float
    end: float
    file_path: str
