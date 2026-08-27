import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from physical_context.database import Database
from physical_context.models import Capture, CaptureState
from physical_context.repository import (
    CaptureNotFoundError,
    CaptureRepository,
    InvalidCaptureStateError,
)


def make_repository(tmp_path: Path) -> tuple[Database, CaptureRepository]:
    database = Database(tmp_path / "physical_context.db")
    database.migrate()
    return database, CaptureRepository(database)


def make_capture() -> Capture:
    return Capture(
        id="cap_1234",
        client_capture_id="device-uuid-1",
        created_at="2026-08-26T12:00:00Z",
        device_ts=1_777_000_000,
        image_path="/tmp/cap_1234.jpg",
        hostname="jamie-laptop",
        git_repo="PhysicalContext",
        git_branch="main",
        git_sha="abc123",
        state=CaptureState.PENDING,
    )


def test_insert_and_dedupe_lookup(tmp_path: Path) -> None:
    _, repository = make_repository(tmp_path)
    capture = make_capture()

    repository.insert(capture)

    assert repository.get(capture.id) == capture
    assert repository.get_by_client_capture_id(capture.client_capture_id) == capture
    assert repository.get_by_client_capture_id("missing") is None

    with pytest.raises(sqlite3.IntegrityError):
        repository.insert(replace(capture, id="cap_other"))


def test_update_state(tmp_path: Path) -> None:
    _, repository = make_repository(tmp_path)
    capture = replace(make_capture(), state=CaptureState.UPLOADED)
    repository.insert(capture)

    repository.transition_state(capture.id, CaptureState.PENDING)
    repository.transition_state(capture.id, CaptureState.CAPTIONING)
    repository.transition_state(capture.id, CaptureState.READY)

    assert repository.get(capture.id).state == CaptureState.READY
    with pytest.raises(InvalidCaptureStateError):
        repository.transition_state(capture.id, CaptureState.PENDING)
    with pytest.raises(CaptureNotFoundError):
        repository.transition_state("missing", CaptureState.READY)


def test_record_quality_and_requeue_captioning(tmp_path: Path) -> None:
    _, repository = make_repository(tmp_path)
    uploaded = replace(make_capture(), state=CaptureState.UPLOADED)
    captioning = replace(
        make_capture(),
        id="cap_captioning",
        client_capture_id="device-uuid-2",
        state=CaptureState.CAPTIONING,
    )
    repository.insert(uploaded)
    repository.insert(captioning)

    repository.record_quality(
        uploaded.id,
        sharpness=42.5,
        brightness=87.0,
        is_blurry=None,
        is_dark=None,
    )
    requeued = repository.requeue_captioning()

    measured = repository.get(uploaded.id)
    assert measured.sharpness == 42.5
    assert measured.brightness == 87.0
    assert measured.is_blurry is None
    assert measured.is_dark is None
    assert measured.state == CaptureState.PENDING
    assert requeued == 1
    assert repository.get(captioning.id).state == CaptureState.PENDING


def test_lists_pending_ids_and_finds_previous_caption(tmp_path: Path) -> None:
    _, repository = make_repository(tmp_path)
    previous = replace(
        make_capture(),
        id="cap_previous",
        client_capture_id="device-uuid-previous",
        created_at="2026-08-26T11:00:00Z",
        caption="A soldering iron rests beside a circuit board.",
        state=CaptureState.READY,
    )
    current = replace(
        make_capture(),
        id="cap_current",
        client_capture_id="device-uuid-current",
        created_at="2026-08-26T12:00:00Z",
    )
    repository.insert(previous)
    repository.insert(current)

    assert repository.list_ids_by_state(CaptureState.PENDING) == (current.id,)
    assert repository.get_previous_caption(current.id) == previous.caption
    assert repository.get_previous_caption(previous.id) is None


def test_write_search_indexes(tmp_path: Path) -> None:
    database, repository = make_repository(tmp_path)
    capture = make_capture()
    repository.insert(capture)
    embedding = [0.25] * 512

    repository.write_search_indexes(
        capture.id,
        caption="Red resistor connected near header J4",
        tags=("resistor", "J4"),
        embedding=embedding,
    )

    indexed_capture = repository.get(capture.id)
    assert indexed_capture.caption == "Red resistor connected near header J4"
    assert indexed_capture.tags == ("resistor", "J4")

    with database.connect() as connection:
        fts_matches = connection.execute(
            """
            SELECT captures.id
            FROM captures_fts
            JOIN captures ON captures.rowid = captures_fts.rowid
            WHERE captures_fts MATCH ?
            """,
            ("resistor",),
        ).fetchall()
        vector_row = connection.execute(
            "SELECT capture_id, vec_length(embedding) FROM captures_vec"
        ).fetchone()

    assert [row[0] for row in fts_matches] == [capture.id]
    assert tuple(vector_row) == (capture.id, 512)


def test_search_index_write_requires_matching_capture_and_embedding(tmp_path: Path) -> None:
    _, repository = make_repository(tmp_path)

    with pytest.raises(CaptureNotFoundError):
        repository.write_search_indexes(
            "missing",
            caption="caption",
            tags=(),
            embedding=None,
        )

    with pytest.raises(ValueError, match="requires a caption"):
        repository.write_search_indexes(
            "missing",
            caption=None,
            tags=(),
            embedding=[0.0] * 512,
        )

    with pytest.raises(ValueError, match="512"):
        repository.write_search_indexes(
            "missing",
            caption="caption",
            tags=(),
            embedding=[0.0],
        )


def make_ready_capture(suffix: str, caption: str | None) -> Capture:
    return replace(
        make_capture(),
        id=f"cap_{suffix}",
        client_capture_id=f"device-uuid-{suffix}",
        image_path=f"/tmp/cap_{suffix}.jpg",
        caption=caption,
        state=CaptureState.READY,
    )


def test_backfill_discovery_lists_only_ready_captions_without_vectors(tmp_path: Path) -> None:
    _, repository = make_repository(tmp_path)
    missing = make_ready_capture("missing", "A soldering iron rests on the bench.")
    embedded = make_ready_capture("embedded", "A circuit board sits under a lamp.")
    uncaptioned = make_ready_capture("uncaptioned", None)
    still_pending = replace(
        make_capture(),
        id="cap_pending",
        client_capture_id="device-uuid-pending",
        caption="A caption written before the row reached ready.",
    )
    for capture in (missing, embedded, uncaptioned, still_pending):
        repository.insert(capture)
    repository.write_embedding(embedded.id, [0.25] * 512)

    assert repository.list_ids_missing_embeddings() == (missing.id,)
    assert repository.has_embedding(missing.id) is False
    assert repository.has_embedding(embedded.id) is True


def test_write_embedding_replaces_a_previous_vector(tmp_path: Path) -> None:
    database, repository = make_repository(tmp_path)
    capture = make_ready_capture("rewrite", "A mug sits beside an open notebook.")
    repository.insert(capture)

    repository.write_embedding(capture.id, [0.25] * 512)
    repository.write_embedding(capture.id, [0.75] * 512)

    with database.connect() as connection:
        rows = connection.execute(
            "SELECT capture_id FROM captures_vec WHERE capture_id = ?",
            (capture.id,),
        ).fetchall()

    assert len(rows) == 1
    assert repository.list_ids_missing_embeddings() == ()


def test_write_embedding_requires_an_existing_captioned_capture(tmp_path: Path) -> None:
    _, repository = make_repository(tmp_path)
    uncaptioned = make_ready_capture("blank", None)
    repository.insert(uncaptioned)

    with pytest.raises(CaptureNotFoundError):
        repository.write_embedding("missing", [0.0] * 512)

    with pytest.raises(ValueError, match="requires a caption"):
        repository.write_embedding(uncaptioned.id, [0.0] * 512)

    with pytest.raises(ValueError, match="512"):
        repository.write_embedding(uncaptioned.id, [0.0])


def test_ready_at_is_stamped_only_when_the_capture_becomes_searchable(tmp_path: Path) -> None:
    _, repository = make_repository(tmp_path)
    capture = make_capture()
    repository.insert(capture)

    assert repository.get(capture.id).ready_at is None

    repository.transition_state(capture.id, CaptureState.CAPTIONING)
    assert repository.get(capture.id).ready_at is None

    repository.transition_state(capture.id, CaptureState.READY)
    ready_at = repository.get(capture.id).ready_at

    assert ready_at is not None
    assert ready_at.endswith("Z")
    assert ready_at >= capture.created_at


def test_device_id_round_trips(tmp_path: Path) -> None:
    _, repository = make_repository(tmp_path)
    capture = replace(make_capture(), device_id="cores3-lite-1")
    repository.insert(capture)

    assert repository.get(capture.id).device_id == "cores3-lite-1"
