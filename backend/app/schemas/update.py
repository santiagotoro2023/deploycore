from pydantic import BaseModel


class UpdateStatusRead(BaseModel):
    git_available: bool
    current_commit: str | None
    latest_commit: str | None
    commits_behind: int
    checked_at: str | None
    stage: str
    error: str | None
