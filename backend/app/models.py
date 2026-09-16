import enum
from datetime import datetime
from typing import Optional

from typing import List

from sqlmodel import SQLModel, Field, Relationship


class JobStatus(str, enum.Enum):
    queued = "queued"
    downloading = "downloading"
    transcribing = "transcribing"
    ranking = "ranking"
    rendering = "rendering"
    done = "done"
    failed = "failed"


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    google_sub: str = Field(unique=True, index=True)
    email: str
    name: Optional[str] = None
    picture_url: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    jobs: List["Job"] = Relationship(back_populates="user")


class Job(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    youtube_url: str
    language: Optional[str] = None
    status: JobStatus = Field(default=JobStatus.queued)
    progress_message: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    video_path: Optional[str] = None

    user: User = Relationship(back_populates="jobs")
    clips: List["Clip"] = Relationship(back_populates="job")


class Clip(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="job.id")
    title: str
    hook: Optional[str] = None
    score: Optional[float] = None
    start: float
    end: float
    file_path: str

    job: Job = Relationship(back_populates="clips")
