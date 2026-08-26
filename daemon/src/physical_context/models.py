from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Capture:
    id: str
    client_capture_id: str
    created_at: str
    device_ts: int | None
    image_path: str
    caption: str | None = None
    tags: tuple[str, ...] = ()
    hostname: str | None = None
    git_repo: str | None = None
    git_branch: str | None = None
    git_sha: str | None = None
    sharpness: float | None = None
    state: str = "pending"
