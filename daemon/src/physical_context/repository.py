import json
import sqlite3
from collections.abc import Sequence

import sqlite_vec

from physical_context.database import Database
from physical_context.models import Capture, CaptureState

EMBEDDING_DIMENSIONS = 512

CAPTURE_COLUMNS = """
    id,
    client_capture_id,
    created_at,
    device_ts,
    image_path,
    caption,
    tags,
    hostname,
    git_repo,
    git_branch,
    git_sha,
    sharpness,
    brightness,
    is_blurry,
    is_dark,
    state
"""

ALLOWED_STATE_TRANSITIONS = {
    CaptureState.UPLOADED: {CaptureState.PENDING},
    CaptureState.PENDING: {CaptureState.CAPTIONING},
    CaptureState.CAPTIONING: {CaptureState.PENDING, CaptureState.READY},
    CaptureState.READY: set(),
}


class CaptureNotFoundError(LookupError):
    pass


class InvalidCaptureStateError(RuntimeError):
    pass


class CaptureRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def insert(self, capture: Capture) -> Capture:
        with self.database.connect() as connection:
            connection.execute(
                f"""
                INSERT INTO captures ({CAPTURE_COLUMNS})
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    capture.id,
                    capture.client_capture_id,
                    capture.created_at,
                    capture.device_ts,
                    capture.image_path,
                    capture.caption,
                    _serialize_tags(capture.tags),
                    capture.hostname,
                    capture.git_repo,
                    capture.git_branch,
                    capture.git_sha,
                    capture.sharpness,
                    capture.brightness,
                    capture.is_blurry,
                    capture.is_dark,
                    capture.state,
                ),
            )
        return capture

    def get(self, capture_id: str) -> Capture | None:
        return self._get_by("id", capture_id)

    def get_by_client_capture_id(self, client_capture_id: str) -> Capture | None:
        return self._get_by("client_capture_id", client_capture_id)

    def transition_state(self, capture_id: str, state: CaptureState) -> None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT state FROM captures WHERE id = ?", (capture_id,)
            ).fetchone()
            if row is None:
                raise CaptureNotFoundError(capture_id)

            current_state = CaptureState(row["state"])
            if state not in ALLOWED_STATE_TRANSITIONS[current_state]:
                raise InvalidCaptureStateError(f"Cannot transition {current_state} to {state}")

            connection.execute(
                "UPDATE captures SET state = ? WHERE id = ?",
                (state, capture_id),
            )

    def record_quality(
        self,
        capture_id: str,
        *,
        sharpness: float,
        brightness: float,
        is_blurry: bool | None,
        is_dark: bool | None,
    ) -> None:
        with self.database.connect() as connection:
            result = connection.execute(
                """
                UPDATE captures
                SET sharpness = ?, brightness = ?, is_blurry = ?, is_dark = ?, state = ?
                WHERE id = ? AND state = ?
                """,
                (
                    sharpness,
                    brightness,
                    is_blurry,
                    is_dark,
                    CaptureState.PENDING,
                    capture_id,
                    CaptureState.UPLOADED,
                ),
            )
            if result.rowcount == 0:
                row = connection.execute(
                    "SELECT state FROM captures WHERE id = ?", (capture_id,)
                ).fetchone()
                if row is None:
                    raise CaptureNotFoundError(capture_id)
                raise InvalidCaptureStateError(
                    f"Quality cannot be recorded while capture is {row['state']}"
                )

    def requeue_captioning(self) -> int:
        with self.database.connect() as connection:
            result = connection.execute(
                "UPDATE captures SET state = ? WHERE state = ?",
                (CaptureState.PENDING, CaptureState.CAPTIONING),
            )
            return result.rowcount

    def delete(self, capture_id: str) -> None:
        with self.database.connect() as connection:
            connection.execute("DELETE FROM captures_vec WHERE capture_id = ?", (capture_id,))
            connection.execute("DELETE FROM captures WHERE id = ?", (capture_id,))

    def write_search_indexes(
        self,
        capture_id: str,
        *,
        caption: str | None,
        tags: Sequence[str],
        embedding: Sequence[float] | None,
    ) -> None:
        if embedding is not None and caption is None:
            raise ValueError("An embedding requires a caption")
        if embedding is not None and len(embedding) != EMBEDDING_DIMENSIONS:
            raise ValueError(f"Embedding must contain {EMBEDDING_DIMENSIONS} values")

        with self.database.connect() as connection:
            result = connection.execute(
                "UPDATE captures SET caption = ?, tags = ? WHERE id = ?",
                (caption, _serialize_tags(tags), capture_id),
            )
            if result.rowcount == 0:
                raise CaptureNotFoundError(capture_id)

            connection.execute(
                "DELETE FROM captures_vec WHERE capture_id = ?",
                (capture_id,),
            )
            if embedding is not None:
                connection.execute(
                    "INSERT INTO captures_vec(capture_id, embedding) VALUES (?, ?)",
                    (capture_id, sqlite_vec.serialize_float32(list(embedding))),
                )

    def _get_by(self, column: str, value: str) -> Capture | None:
        with self.database.connect() as connection:
            row = connection.execute(
                f"SELECT {CAPTURE_COLUMNS} FROM captures WHERE {column} = ?",
                (value,),
            ).fetchone()
        return _capture_from_row(row) if row is not None else None


def _serialize_tags(tags: Sequence[str]) -> str:
    return json.dumps(list(tags), separators=(",", ":"))


def _capture_from_row(row: sqlite3.Row) -> Capture:
    return Capture(
        id=row["id"],
        client_capture_id=row["client_capture_id"],
        created_at=row["created_at"],
        device_ts=row["device_ts"],
        image_path=row["image_path"],
        caption=row["caption"],
        tags=tuple(json.loads(row["tags"])),
        hostname=row["hostname"],
        git_repo=row["git_repo"],
        git_branch=row["git_branch"],
        git_sha=row["git_sha"],
        sharpness=row["sharpness"],
        brightness=row["brightness"],
        is_blurry=_optional_bool(row["is_blurry"]),
        is_dark=_optional_bool(row["is_dark"]),
        state=CaptureState(row["state"]),
    )


def _optional_bool(value: int | None) -> bool | None:
    return bool(value) if value is not None else None
