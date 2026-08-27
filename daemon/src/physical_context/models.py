from dataclasses import dataclass
from enum import StrEnum


class CaptureState(StrEnum):
    UPLOADED = "uploaded"
    PENDING = "pending"
    CAPTIONING = "captioning"
    READY = "ready"


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
    brightness: float | None = None
    is_blurry: bool | None = None
    is_dark: bool | None = None
    device_id: str | None = None
    ready_at: str | None = None
    state: CaptureState = CaptureState.PENDING
