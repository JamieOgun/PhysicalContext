from dataclasses import replace
from pathlib import Path

from physical_context.captions import StructuredCaption
from physical_context.capture_processor import CaptureProcessor
from physical_context.database import Database
from physical_context.embeddings import (
    EMBEDDING_DIMENSIONS,
    EmbeddingInputType,
    EmbeddingProviderError,
    UnavailableEmbeddingProvider,
)
from physical_context.models import Capture, CaptureState
from physical_context.repository import CaptureRepository


def make_repository(tmp_path: Path) -> tuple[Database, CaptureRepository]:
    database = Database(tmp_path / "physical_context.db")
    database.migrate()
    return database, CaptureRepository(database)


def make_capture(tmp_path: Path) -> Capture:
    image_path = tmp_path / "current.jpg"
    image_path.write_bytes(b"jpeg")
    return Capture(
        id="current",
        client_capture_id="current-client-id",
        created_at="2026-08-26T12:00:00Z",
        device_ts=1_777_000_000,
        image_path=str(image_path),
        state=CaptureState.PENDING,
    )


def make_caption() -> StructuredCaption:
    return StructuredCaption(
        summary="A person sits in front of closed curtains.",
        details=["The person is wearing a light-colored shirt."],
        visible_text=[],
        spatial_relationships=["The person is centered in front of the curtains."],
        changes=["The framing is wider than in the previous capture."],
        uncertainties=["The room beyond the curtains is not visible."],
    )


class RecordingProvider:
    def __init__(self) -> None:
        self.previous_caption: str | None = None

    def caption(self, image_path: Path, previous_caption: str | None) -> StructuredCaption:
        self.previous_caption = previous_caption
        return make_caption()


class FailingProvider:
    def caption(self, image_path: Path, previous_caption: str | None) -> StructuredCaption:
        raise RuntimeError("provider unavailable")


class MalformedProvider:
    def caption(self, image_path: Path, previous_caption: str | None) -> StructuredCaption:
        return {"summary": "Incomplete response"}  # type: ignore[return-value]


class CountingProvider:
    def __init__(self) -> None:
        self.calls = 0

    def caption(self, image_path: Path, previous_caption: str | None) -> StructuredCaption:
        self.calls += 1
        return make_caption()


class RecordingEmbeddingProvider:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []

    def embed(self, text: str, *, input_type: EmbeddingInputType) -> tuple[float, ...]:
        self.requests.append((text, input_type))
        return tuple(0.5 for _ in range(EMBEDDING_DIMENSIONS))


class FailingEmbeddingProvider:
    def __init__(self) -> None:
        self.calls = 0

    def embed(self, text: str, *, input_type: EmbeddingInputType) -> tuple[float, ...]:
        self.calls += 1
        raise EmbeddingProviderError("embedding provider unavailable")


def fts_matches(database: Database, term: str) -> list[str]:
    with database.connect() as connection:
        rows = connection.execute(
            """
            SELECT captures.id
            FROM captures_fts
            JOIN captures ON captures.rowid = captures_fts.rowid
            WHERE captures_fts MATCH ?
            """,
            (term,),
        ).fetchall()
    return [row["id"] for row in rows]


def test_caption_success_stores_normalized_text_and_previous_context(tmp_path: Path) -> None:
    _, repository = make_repository(tmp_path)
    previous = replace(
        make_capture(tmp_path),
        id="previous",
        client_capture_id="previous-client-id",
        created_at="2026-08-26T11:00:00Z",
        caption="A close view of a person in front of curtains.",
        state=CaptureState.READY,
    )
    current = make_capture(tmp_path)
    repository.insert(previous)
    repository.insert(current)
    provider = RecordingProvider()

    CaptureProcessor(
        repository,
        provider,
        UnavailableEmbeddingProvider("not configured"),
    ).process(current.id)

    captioned = repository.get(current.id)
    assert captioned.state == CaptureState.READY
    assert captioned.caption == make_caption().to_search_text()
    assert provider.previous_caption == previous.caption


def test_caption_provider_failure_reaches_ready_with_null_caption(tmp_path: Path) -> None:
    _, repository = make_repository(tmp_path)
    capture = make_capture(tmp_path)
    repository.insert(capture)

    CaptureProcessor(
        repository,
        FailingProvider(),
        UnavailableEmbeddingProvider("not configured"),
    ).process(capture.id)

    failed = repository.get(capture.id)
    assert failed.state == CaptureState.READY
    assert failed.caption is None


def test_malformed_caption_reaches_ready_with_null_caption(tmp_path: Path) -> None:
    _, repository = make_repository(tmp_path)
    capture = make_capture(tmp_path)
    repository.insert(capture)

    CaptureProcessor(
        repository,
        MalformedProvider(),
        UnavailableEmbeddingProvider("not configured"),
    ).process(capture.id)

    malformed = repository.get(capture.id)
    assert malformed.state == CaptureState.READY
    assert malformed.caption is None


def test_missing_image_is_pruned_without_calling_caption_provider(tmp_path: Path) -> None:
    _, repository = make_repository(tmp_path)
    capture = make_capture(tmp_path)
    Path(capture.image_path).unlink()
    repository.insert(capture)
    caption_provider = CountingProvider()

    CaptureProcessor(
        repository,
        caption_provider,
        UnavailableEmbeddingProvider("not configured"),
    ).process(capture.id)

    assert repository.get(capture.id) is None
    assert caption_provider.calls == 0


def test_successful_caption_embeds_as_document_and_stores_vector(tmp_path: Path) -> None:
    database, repository = make_repository(tmp_path)
    capture = make_capture(tmp_path)
    repository.insert(capture)
    embedding_provider = RecordingEmbeddingProvider()

    CaptureProcessor(repository, RecordingProvider(), embedding_provider).process(capture.id)

    embedded = repository.get(capture.id)
    assert embedded.state == CaptureState.READY
    assert repository.has_embedding(capture.id) is True
    assert embedding_provider.requests == [(make_caption().to_search_text(), "document")]
    assert repository.list_ids_missing_embeddings() == ()


def test_caption_failure_skips_embedding_entirely(tmp_path: Path) -> None:
    _, repository = make_repository(tmp_path)
    capture = make_capture(tmp_path)
    repository.insert(capture)
    embedding_provider = RecordingEmbeddingProvider()

    CaptureProcessor(repository, FailingProvider(), embedding_provider).process(capture.id)

    assert embedding_provider.requests == []
    assert repository.has_embedding(capture.id) is False
    assert repository.list_ids_missing_embeddings() == ()


def test_embedding_failure_keeps_caption_searchable_and_queues_backfill(tmp_path: Path) -> None:
    database, repository = make_repository(tmp_path)
    capture = make_capture(tmp_path)
    repository.insert(capture)

    CaptureProcessor(repository, RecordingProvider(), FailingEmbeddingProvider()).process(
        capture.id
    )

    stored = repository.get(capture.id)
    assert stored.state == CaptureState.READY
    assert stored.caption == make_caption().to_search_text()
    assert fts_matches(database, "curtains") == [capture.id]
    assert repository.has_embedding(capture.id) is False
    assert repository.list_ids_missing_embeddings() == (capture.id,)


def test_backfill_embeds_ready_capture_without_recaptioning(tmp_path: Path) -> None:
    _, repository = make_repository(tmp_path)
    capture = replace(
        make_capture(tmp_path),
        caption=make_caption().to_search_text(),
        state=CaptureState.READY,
    )
    repository.insert(capture)
    caption_provider = CountingProvider()
    embedding_provider = RecordingEmbeddingProvider()

    CaptureProcessor(repository, caption_provider, embedding_provider).process(capture.id)

    assert caption_provider.calls == 0
    assert embedding_provider.requests == [(capture.caption, "document")]
    assert repository.has_embedding(capture.id) is True
    assert repository.list_ids_missing_embeddings() == ()


def test_backfill_is_skipped_when_embedding_already_exists(tmp_path: Path) -> None:
    _, repository = make_repository(tmp_path)
    capture = replace(
        make_capture(tmp_path),
        caption=make_caption().to_search_text(),
        state=CaptureState.READY,
    )
    repository.insert(capture)
    repository.write_embedding(capture.id, [0.25] * EMBEDDING_DIMENSIONS)
    embedding_provider = RecordingEmbeddingProvider()

    CaptureProcessor(repository, CountingProvider(), embedding_provider).process(capture.id)

    assert embedding_provider.requests == []


def test_backfill_failure_leaves_capture_eligible_for_a_later_retry(tmp_path: Path) -> None:
    _, repository = make_repository(tmp_path)
    capture = replace(
        make_capture(tmp_path),
        caption=make_caption().to_search_text(),
        state=CaptureState.READY,
    )
    repository.insert(capture)
    embedding_provider = FailingEmbeddingProvider()

    CaptureProcessor(repository, CountingProvider(), embedding_provider).process(capture.id)

    assert embedding_provider.calls == 1
    assert repository.has_embedding(capture.id) is False
    assert repository.list_ids_missing_embeddings() == (capture.id,)


def test_missing_capture_is_ignored(tmp_path: Path) -> None:
    _, repository = make_repository(tmp_path)
    embedding_provider = RecordingEmbeddingProvider()

    CaptureProcessor(repository, CountingProvider(), embedding_provider).process("missing")

    assert embedding_provider.requests == []
