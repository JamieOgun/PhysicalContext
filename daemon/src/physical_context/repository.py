import json
import sqlite3
from collections.abc import Sequence

import sqlite_vec

from physical_context.database import Database
from physical_context.embeddings import validate_embedding
from physical_context.models import Capture, CaptureState

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

    def list_ids_by_state(self, state: CaptureState) -> tuple[str, ...]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT id FROM captures WHERE state = ? ORDER BY created_at, rowid",
                (state,),
            ).fetchall()
        return tuple(row["id"] for row in rows)

    def get_previous_caption(self, capture_id: str) -> str | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT previous.caption
                FROM captures AS current
                JOIN captures AS previous
                  ON previous.created_at < current.created_at
                 AND previous.caption IS NOT NULL
                WHERE current.id = ?
                ORDER BY previous.created_at DESC, previous.rowid DESC
                LIMIT 1
                """,
                (capture_id,),
            ).fetchone()
        return row["caption"] if row is not None else None

    def list_ids_missing_embeddings(self) -> tuple[str, ...]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT captures.id
                FROM captures
                LEFT JOIN captures_vec ON captures_vec.capture_id = captures.id
                WHERE captures.state = ?
                  AND captures.caption IS NOT NULL
                  AND captures_vec.capture_id IS NULL
                ORDER BY captures.created_at, captures.rowid
                """,
                (CaptureState.READY,),
            ).fetchall()
        return tuple(row["id"] for row in rows)

    def has_embedding(self, capture_id: str) -> bool:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM captures_vec WHERE capture_id = ?",
                (capture_id,),
            ).fetchone()
        return row is not None

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
        validated_embedding = validate_embedding(embedding) if embedding is not None else None

        with self.database.connect() as connection:
            result = connection.execute(
                "UPDATE captures SET caption = ?, tags = ? WHERE id = ?",
                (caption, _serialize_tags(tags), capture_id),
            )
            if result.rowcount == 0:
                raise CaptureNotFoundError(capture_id)

            _replace_embedding(connection, capture_id, validated_embedding)

    def write_embedding(self, capture_id: str, embedding: Sequence[float]) -> None:
        validated_embedding = validate_embedding(embedding)
        with self.database.connect() as connection:
            capture = connection.execute(
                "SELECT caption FROM captures WHERE id = ?",
                (capture_id,),
            ).fetchone()
            if capture is None:
                raise CaptureNotFoundError(capture_id)
            if capture["caption"] is None:
                raise ValueError("An embedding requires a caption")
            _replace_embedding(connection, capture_id, validated_embedding)

    def search_keyword(self, match_query: str, *, limit: int) -> tuple[tuple[str, float], ...]:
        """Rank ready captions by FTS5 bm25, best first.

        bm25 returns negative scores where lower is better, so ascending order
        is descending relevance.
        """
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT captures.id AS id, bm25(captures_fts) AS score
                FROM captures_fts
                JOIN captures ON captures.rowid = captures_fts.rowid
                WHERE captures_fts MATCH ?
                  AND captures.state = ?
                  AND captures.caption IS NOT NULL
                ORDER BY score, captures.created_at DESC
                LIMIT ?
                """,
                (match_query, CaptureState.READY, limit),
            ).fetchall()
        return tuple((row["id"], row["score"]) for row in rows)

    def search_semantic(
        self,
        embedding: Sequence[float],
        *,
        limit: int,
        max_distance: float,
    ) -> tuple[tuple[str, float], ...]:
        """Rank ready captions by cosine distance, nearest first.

        The k-nearest set is taken first and filtered afterwards, because vec0
        applies `k` to the index scan rather than to the surviving join rows.
        """
        query_vector = sqlite_vec.serialize_float32(list(validate_embedding(embedding)))
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT neighbours.capture_id AS id, neighbours.distance AS distance
                FROM (
                    SELECT capture_id, distance
                    FROM captures_vec
                    WHERE embedding MATCH ? AND k = ?
                    ORDER BY distance
                ) AS neighbours
                JOIN captures ON captures.id = neighbours.capture_id
                WHERE captures.state = ?
                  AND captures.caption IS NOT NULL
                  AND neighbours.distance <= ?
                ORDER BY neighbours.distance
                """,
                (query_vector, limit, CaptureState.READY, max_distance),
            ).fetchall()
        return tuple((row["id"], row["distance"]) for row in rows)

    def list_recent(self, *, limit: int) -> tuple[Capture, ...]:
        """Most recent captures, newest first, regardless of state.

        Unlike the search arms this does not filter to `ready`: the most recent
        capture is often still being captioned, and "what did I just do" is
        exactly the question this answers.
        """
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT {CAPTURE_COLUMNS} FROM captures
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return tuple(_capture_from_row(row) for row in rows)

    def list_by_ids(self, capture_ids: Sequence[str]) -> dict[str, Capture]:
        if not capture_ids:
            return {}

        placeholders = ",".join("?" for _ in capture_ids)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT {CAPTURE_COLUMNS} FROM captures WHERE id IN ({placeholders})",
                tuple(capture_ids),
            ).fetchall()
        return {row["id"]: _capture_from_row(row) for row in rows}

    def _get_by(self, column: str, value: str) -> Capture | None:
        with self.database.connect() as connection:
            row = connection.execute(
                f"SELECT {CAPTURE_COLUMNS} FROM captures WHERE {column} = ?",
                (value,),
            ).fetchone()
        return _capture_from_row(row) if row is not None else None


def _serialize_tags(tags: Sequence[str]) -> str:
    return json.dumps(list(tags), separators=(",", ":"))


def _replace_embedding(
    connection: sqlite3.Connection,
    capture_id: str,
    embedding: Sequence[float] | None,
) -> None:
    connection.execute("DELETE FROM captures_vec WHERE capture_id = ?", (capture_id,))
    if embedding is not None:
        connection.execute(
            "INSERT INTO captures_vec(capture_id, embedding) VALUES (?, ?)",
            (capture_id, sqlite_vec.serialize_float32(list(embedding))),
        )


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
