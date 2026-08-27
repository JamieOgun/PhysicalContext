import time
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from physical_context.app import create_app
from physical_context.captions import CaptionProvider, StructuredCaption
from physical_context.config import Settings
from physical_context.embeddings import EMBEDDING_DIMENSIONS, EmbeddingInputType, EmbeddingProvider
from physical_context.models import Capture, CaptureState
from physical_context.repository import CaptureRepository
from physical_context.runtime import initialize_storage


def make_jpeg(brightness: int = 128) -> bytes:
    pixels = np.full((32, 32, 3), brightness, dtype=np.uint8)
    encoded, jpeg = cv2.imencode(".jpg", pixels)
    assert encoded
    return jpeg.tobytes()


JPEG_BYTES = make_jpeg()


def make_client(
    tmp_path: Path,
    *,
    sharpness_threshold: float | None = None,
    brightness_threshold: float | None = None,
    caption_provider: CaptionProvider | None = None,
    embedding_provider: EmbeddingProvider | None = None,
    project_root: Path | None = None,
) -> TestClient:
    settings = Settings(
        data_root=tmp_path,
        project_root=project_root,
        sharpness_threshold=sharpness_threshold,
        brightness_threshold=brightness_threshold,
        _env_file=None,
    )
    return TestClient(
        create_app(
            settings,
            caption_provider=caption_provider,
            embedding_provider=embedding_provider,
        )
    )


def post_capture(
    client: TestClient,
    *,
    client_capture_id: str = "client-capture-1",
    image: bytes = JPEG_BYTES,
    content_type: str = "image/jpeg",
):
    return client.post(
        "/capture",
        files={"image": ("capture.jpg", image, content_type)},
        data={
            "device_ts": "1777000000",
            "device_id": "cores3-lite-1",
            "client_capture_id": client_capture_id,
        },
    )


def wait_for_state(
    repository: CaptureRepository,
    capture_id: str,
    expected: CaptureState,
) -> Capture:
    for _ in range(100):
        capture = repository.get(capture_id)
        if capture is not None and capture.state == expected:
            return capture
        time.sleep(0.01)
    raise AssertionError(f"Capture {capture_id} did not reach {expected}")


class StaticCaptionProvider:
    def caption(self, image_path: Path, previous_caption: str | None) -> StructuredCaption:
        return StructuredCaption(
            summary="A uniformly lit test image.",
            details=["The frame is a single solid color."],
            visible_text=[],
            spatial_relationships=[],
            changes=[],
            uncertainties=[],
        )


class StaticEmbeddingProvider:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []

    def embed(self, text: str, *, input_type: EmbeddingInputType) -> tuple[float, ...]:
        self.requests.append((text, input_type))
        return tuple(0.5 for _ in range(EMBEDDING_DIMENSIONS))


def test_capture_stores_image_and_reaches_ready(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = post_capture(client)

        assert response.status_code == 201
        body = response.json()
        assert len(body["capture_id"]) == 32
        assert body["short_id"] == body["capture_id"][:8]
        assert body["deduplicated"] is False

        repository = CaptureRepository(client.app.state.database)
        capture = wait_for_state(repository, body["capture_id"], CaptureState.READY)
        assert capture.client_capture_id == "client-capture-1"
        assert capture.device_ts == 1_777_000_000
        assert capture.caption is None
        assert capture.created_at.endswith("Z")
        assert capture.sharpness is not None
        assert capture.brightness == pytest.approx(128, abs=1)
        assert capture.is_blurry is None
        assert capture.is_dark is None
        assert Path(capture.image_path).read_bytes() == JPEG_BYTES


def test_capture_runs_configured_caption_provider_automatically(tmp_path: Path) -> None:
    with make_client(tmp_path, caption_provider=StaticCaptionProvider()) as client:
        response = post_capture(client)
        repository = CaptureRepository(client.app.state.database)
        capture = wait_for_state(
            repository,
            response.json()["capture_id"],
            CaptureState.READY,
        )

    assert response.status_code == 201
    assert capture.caption == (
        "A uniformly lit test image.\nDetails: The frame is a single solid color."
    )


def test_capture_status_reports_processing_progress(tmp_path: Path) -> None:
    with make_client(tmp_path, caption_provider=StaticCaptionProvider()) as client:
        created = post_capture(client)
        capture_id = created.json()["capture_id"]
        repository = CaptureRepository(client.app.state.database)
        capture = wait_for_state(repository, capture_id, CaptureState.READY)

        response = client.get(f"/capture/{capture_id}/status")

    assert response.status_code == 200
    assert response.json() == {
        "capture_id": capture_id,
        "short_id": capture_id[:8],
        "state": "ready",
        "caption_available": True,
    }
    assert capture.caption is not None


def test_capture_status_returns_not_found(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = client.get("/capture/missing/status")

    assert response.status_code == 404


def test_reupload_is_idempotent(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        first = post_capture(client)
        second = post_capture(client, image=b"replacement")

        assert first.status_code == 201
        assert second.status_code == 200
        assert second.json()["capture_id"] == first.json()["capture_id"]
        assert second.json()["short_id"] == first.json()["short_id"]
        assert second.json()["deduplicated"] is True

        stored_images = list((tmp_path / "captures").glob("*.jpg"))
        assert len(stored_images) == 1
        assert stored_images[0].read_bytes() == JPEG_BYTES


def test_reupload_replaces_stale_uncaptioned_capture_missing_image(tmp_path: Path) -> None:
    settings = Settings(data_root=tmp_path, _env_file=None)
    repository = CaptureRepository(initialize_storage(settings))
    stale = Capture(
        id="stale-capture",
        client_capture_id="client-capture-1",
        created_at="2026-08-26T12:00:00Z",
        device_ts=1_777_000_000,
        image_path=str(tmp_path / "captures" / "stale-capture.jpg"),
        state=CaptureState.READY,
    )
    repository.insert(stale)

    with TestClient(create_app(settings)) as client:
        response = post_capture(client)
        repository = CaptureRepository(client.app.state.database)

        assert response.status_code == 201
        assert response.json()["deduplicated"] is False
        assert response.json()["capture_id"] != stale.id
        assert repository.get(stale.id) is None
        assert Path(repository.get(response.json()["capture_id"]).image_path).is_file()


def test_capture_validates_image_and_required_fields(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        wrong_type = post_capture(client, content_type="application/octet-stream")
        empty_image = post_capture(client, image=b"")
        invalid_jpeg = post_capture(client, image=b"not-a-jpeg")
        missing_field = client.post(
            "/capture",
            files={"image": ("capture.jpg", JPEG_BYTES, "image/jpeg")},
            data={"device_ts": "1777000000", "device_id": "cores3-lite-1"},
        )

    assert wrong_type.status_code == 415
    assert empty_image.status_code == 400
    assert invalid_jpeg.status_code == 400
    assert missing_field.status_code == 422
    assert list((tmp_path / "captures").glob("*.jpg")) == []


def test_capture_applies_configured_quality_thresholds(tmp_path: Path) -> None:
    with make_client(
        tmp_path,
        sharpness_threshold=1.0,
        brightness_threshold=1.0,
    ) as client:
        response = post_capture(client, image=make_jpeg(0))

    assert response.status_code == 201
    assert response.json()["is_blurry"] is True
    assert response.json()["is_dark"] is True


def test_startup_requeues_and_prunes_missing_captioning_capture(tmp_path: Path) -> None:
    settings = Settings(data_root=tmp_path, _env_file=None)
    repository = CaptureRepository(initialize_storage(settings))
    capture = Capture(
        id="captioning-capture",
        client_capture_id="captioning-client-id",
        created_at="2026-08-26T12:00:00Z",
        device_ts=1_777_000_000,
        image_path=str(tmp_path / "captures" / "captioning-capture.jpg"),
        state=CaptureState.CAPTIONING,
    )
    repository.insert(capture)

    with TestClient(create_app(settings)):
        pass

    assert repository.get(capture.id) is None


def test_capture_embeds_the_caption_it_just_generated(tmp_path: Path) -> None:
    embedding_provider = StaticEmbeddingProvider()
    with make_client(
        tmp_path,
        caption_provider=StaticCaptionProvider(),
        embedding_provider=embedding_provider,
    ) as client:
        response = post_capture(client)
        repository = CaptureRepository(client.app.state.database)
        capture = wait_for_state(
            repository,
            response.json()["capture_id"],
            CaptureState.READY,
        )

    assert embedding_provider.requests == [(capture.caption, "document")]
    assert repository.has_embedding(capture.id) is True
    assert repository.list_ids_missing_embeddings() == ()


def test_startup_backfills_captions_left_without_an_embedding(tmp_path: Path) -> None:
    settings = Settings(data_root=tmp_path, _env_file=None)
    repository = CaptureRepository(initialize_storage(settings))
    capture = Capture(
        id="unembedded-capture",
        client_capture_id="unembedded-client-id",
        created_at="2026-08-26T12:00:00Z",
        device_ts=1_777_000_000,
        image_path=str(tmp_path / "captures" / "unembedded-capture.jpg"),
        caption="A soldering iron rests beside a circuit board.",
        state=CaptureState.READY,
    )
    repository.insert(capture)
    assert repository.list_ids_missing_embeddings() == (capture.id,)

    caption_provider = StaticCaptionProvider()
    embedding_provider = StaticEmbeddingProvider()
    with TestClient(
        create_app(
            settings,
            caption_provider=caption_provider,
            embedding_provider=embedding_provider,
        )
    ):
        pass

    assert embedding_provider.requests == [(capture.caption, "document")]
    assert repository.has_embedding(capture.id) is True
    assert repository.get(capture.id).caption == capture.caption
    assert repository.list_ids_missing_embeddings() == ()


def test_capture_records_ambient_context_at_ingest(tmp_path: Path, git_repository) -> None:
    repository_root = git_repository(tmp_path, "project")
    with make_client(tmp_path / "data", project_root=repository_root) as client:
        response = post_capture(client)
        repository = CaptureRepository(client.app.state.database)
        capture = wait_for_state(
            repository,
            response.json()["capture_id"],
            CaptureState.READY,
        )

    assert capture.hostname
    assert capture.git_repo == "project"
    assert capture.git_branch == "main"
    assert capture.git_sha is not None
    assert capture.created_at.endswith("Z")


def test_capture_without_a_configured_project_still_records_host_and_time(
    tmp_path: Path,
) -> None:
    with make_client(tmp_path) as client:
        response = post_capture(client)
        repository = CaptureRepository(client.app.state.database)
        capture = wait_for_state(
            repository,
            response.json()["capture_id"],
            CaptureState.READY,
        )

    assert capture.hostname
    assert capture.created_at.endswith("Z")
    assert (capture.git_repo, capture.git_branch, capture.git_sha) == (None, None, None)


def test_unresolvable_git_context_never_blocks_ingest(tmp_path: Path) -> None:
    with make_client(tmp_path / "data", project_root=tmp_path / "not-a-repo") as client:
        response = post_capture(client)
        repository = CaptureRepository(client.app.state.database)
        capture = wait_for_state(
            repository,
            response.json()["capture_id"],
            CaptureState.READY,
        )

    assert response.status_code == 201
    assert capture.hostname
    assert capture.git_repo is None


def test_capture_persists_the_posting_device_and_ready_time(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = post_capture(client)
        repository = CaptureRepository(client.app.state.database)
        capture = wait_for_state(
            repository,
            response.json()["capture_id"],
            CaptureState.READY,
        )

    assert capture.device_id == "cores3-lite-1"
    assert capture.ready_at is not None
    assert capture.ready_at >= capture.created_at
