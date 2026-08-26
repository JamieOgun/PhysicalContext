import json
import sqlite3
from collections.abc import Sequence

import sqlite_vec

from physical_context.database import Database
from physical_context.models import Capture

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
    state
"""


class CaptureNotFoundError(LookupError):
    pass


class CaptureRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def insert(self, capture: Capture) -> Capture:
        with self.database.connect() as connection:
            connection.execute(
                f"""
                INSERT INTO captures ({CAPTURE_COLUMNS})
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    capture.state,
                ),
            )
        return capture

    def get(self, capture_id: str) -> Capture | None:
        return self._get_by("id", capture_id)

    def get_by_client_capture_id(self, client_capture_id: str) -> Capture | None:
        return self._get_by("client_capture_id", client_capture_id)

    def update_state(self, capture_id: str, state: str) -> None:
        with self.database.connect() as connection:
            result = connection.execute(
                "UPDATE captures SET state = ? WHERE id = ?",
                (state, capture_id),
            )
            if result.rowcount == 0:
                raise CaptureNotFoundError(capture_id)

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
        state=row["state"],
    )
