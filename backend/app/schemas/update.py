from pydantic import BaseModel


class UpdateStatusRead(BaseModel):
    git_available: bool
    current_commit: str | None
    latest_commit: str | None
    commits_behind: int
    checked_at: str | None
    stage: str
    error: str | None
    # Commit subject lines, newest first as git log itself orders them.
    # pending: what "Update now" would bring in, refreshed alongside
    # commits_behind. last_update: what the most recently applied update
    # actually contained, persisted so it's still readable days later,
    # not just the instant the update finished.
    pending_changelog: list[str]
    last_update_changelog: list[str]
