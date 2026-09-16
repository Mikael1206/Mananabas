from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, HttpUrl

from app.models import JobStatus


class JobCreateRequest(BaseModel):
    youtube_url: HttpUrl
    language: Optional[str] = None


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
    language: Optional[str]
    status: JobStatus
    progress_message: Optional[str]
    error: Optional[str]
    created_at: datetime
    clips: List[ClipRead] = []

    class Config:
        from_attributes = True


class GoogleLoginRequest(BaseModel):
    credential: str


class UserRead(BaseModel):
    id: int
    email: str
    name: Optional[str]
    picture_url: Optional[str]

    class Config:
        from_attributes = True


class AuthResponse(BaseModel):
    access_token: str
    user: UserRead
