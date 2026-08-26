from dataclasses import replace
from pathlib import Path

from physical_context.captions import StructuredCaption
from physical_context.capture_processor import CaptureProcessor
from physical_context.database import Database
from physical_context.embeddings import UnavailableEmbeddingProvider
from physical_context.models import Capture, CaptureState
from physical_context.repository import CaptureRepository


def make_repository(tmp_path: Path) -> CaptureRepository:
    database = Database(tmp_path / "physical_context.db")
    database.migrate()
    return CaptureRepository(database)


def make_capture() -> Capture:
    return Capture(
        id="current",
        client_capture_id="current-client-id",
        created_at="2026-08-26T12:00:00Z",
        device_ts=1_777_000_000,
        image_path="/tmp/current.jpg",
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


def test_caption_success_stores_normalized_text_and_previous_context(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    previous = replace(
        make_capture(),
        id="previous",
        client_capture_id="previous-client-id",
        created_at="2026-08-26T11:00:00Z",
        caption="A close view of a person in front of curtains.",
        state=CaptureState.READY,
    )
    current = make_capture()
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
    repository = make_repository(tmp_path)
    capture = make_capture()
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
    repository = make_repository(tmp_path)
    capture = make_capture()
    repository.insert(capture)

    CaptureProcessor(
        repository,
        MalformedProvider(),
        UnavailableEmbeddingProvider("not configured"),
    ).process(capture.id)

    malformed = repository.get(capture.id)
    assert malformed.state == CaptureState.READY
    assert malformed.caption is None
