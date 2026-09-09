from typing import List, Optional

from pydantic import BaseModel, HttpUrl

from app.models import JobStatus


class JobCreateRequest(BaseModel):
    youtube_url: HttpUrl


class ClipRead(BaseModel):
    id: int
    title: str
    hook: Optional[str]
    score: Optional[float]
    start: float
    end: float
    file_path: str

    class Config:
        from_attributes = True


class JobRead(BaseModel):
    id: int
    youtube_url: str
    status: JobStatus
    progress_message: Optional[str]
    error: Optional[str]
    clips: List[ClipRead] = []

    class Config:
        from_attributes = True
