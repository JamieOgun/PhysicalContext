import asyncio
import base64
from pathlib import Path

import cv2
import numpy as np
import pytest
from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError

from physical_context.config import Settings
from physical_context.embeddings import EMBEDDING_DIMENSIONS, EmbeddingInputType
from physical_context.images import MAX_IMAGE_EDGE
from physical_context.mcp_server import MAX_LIMIT, CaptureTools, create_mcp_server
from physical_context.models import Capture, CaptureState
from physical_context.repository import CaptureRepository
from physical_context.runtime import initialize_storage
from physical_context.search import CaptureSearch

CAPTION = (
    "Header J4 soldered onto the board\nDetails: the joint is bright and even\nVisible text: J4"
)


class StubEmbeddingProvider:
    def embed(self, text: str, *, input_type: EmbeddingInputType) -> tuple[float, ...]:
        return tuple([1.0] + [0.0] * (EMBEDDING_DIMENSIONS - 1))


def write_jpeg(path: Path, *, width: int, height: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pixels = np.random.default_rng(0).integers(0, 255, (height, width, 3), dtype=np.uint8)
    encoded, buffer = cv2.imencode(".jpg", pixels)
    assert encoded
    path.write_bytes(buffer.tobytes())


def make_environment(tmp_path: Path) -> tuple[Settings, CaptureRepository]:
    settings = Settings(data_root=tmp_path, _env_file=None)
    return settings, CaptureRepository(initialize_storage(settings))


def seed(
    repository: CaptureRepository,
    capture_id: str,
    *,
    caption: str | None = CAPTION,
    created_at: str = "2026-08-26T12:00:00Z",
    image_path: Path | None = None,
    state: CaptureState = CaptureState.READY,
) -> Capture:
    capture = Capture(
        id=capture_id,
        client_capture_id=f"client-{capture_id}",
        created_at=created_at,
        device_ts=1_777_000_000,
        image_path=str(image_path or Path(f"/tmp/{capture_id}.jpg")),
        hostname="jamie-laptop",
        git_repo="PhysicalContext",
        git_branch="master",
        git_sha="abc123",
        state=CaptureState.PENDING,
    )
    repository.insert(capture)
    if state == CaptureState.PENDING:
        return capture

    repository.transition_state(capture_id, CaptureState.CAPTIONING)
    repository.write_search_indexes(capture_id, caption=caption, tags=("solder",), embedding=None)
    repository.transition_state(capture_id, CaptureState.READY)
    return capture


def make_tools(repository: CaptureRepository) -> CaptureTools:
    return CaptureTools(repository, CaptureSearch(repository, StubEmbeddingProvider()))


def call(server, name: str, arguments: dict):
    return asyncio.run(server.call_tool(name, arguments))


# --- Tool surface -----------------------------------------------------------


def test_server_exposes_exactly_the_four_v1_tools(tmp_path: Path) -> None:
    settings, _ = make_environment(tmp_path)
    server = create_mcp_server(settings, embedding_provider=StubEmbeddingProvider())

    names = {tool.name for tool in asyncio.run(server.list_tools())}

    assert names == {"search_captures", "get_capture", "list_recent", "get_image"}


def test_no_bulk_image_retrieval_tool_exists(tmp_path: Path) -> None:
    settings, _ = make_environment(tmp_path)
    server = create_mcp_server(settings, embedding_provider=StubEmbeddingProvider())

    tools = asyncio.run(server.list_tools())
    image_tools = [tool for tool in tools if "image" in tool.name]

    assert [tool.name for tool in image_tools] == ["get_image"]
    # The single image tool takes one capture id, not a list or a limit.
    assert set(image_tools[0].input_schema["properties"]) == {"capture_id"}


# --- search_captures --------------------------------------------------------


def test_search_returns_text_metadata_only(tmp_path: Path) -> None:
    _, repository = make_environment(tmp_path)
    seed(repository, "capture-1")

    result = make_tools(repository).search_captures("J4 header")

    assert len(result.matches) == 1
    match = result.matches[0]
    assert match.capture_id == "capture-1"
    assert match.created_at == "2026-08-26T12:00:00Z"
    assert match.summary == "Header J4 soldered onto the board"
    assert match.tags == ["solder"]
    assert "image" not in str(result).lower()


def test_search_no_match_is_explicit(tmp_path: Path) -> None:
    _, repository = make_environment(tmp_path)
    seed(repository, "capture-1")

    result = make_tools(repository).search_captures("xylophone")

    assert result.matches == []
    assert result.note is not None
    assert "xylophone" in result.note


def test_search_limit_is_validated_and_capped(tmp_path: Path) -> None:
    _, repository = make_environment(tmp_path)
    tools = make_tools(repository)
    for index in range(MAX_LIMIT + 5):
        seed(repository, f"capture-{index}", created_at=f"2026-08-26T12:00:{index:02d}Z")

    with pytest.raises(ValueError, match="at least 1"):
        tools.search_captures("J4", limit=0)
    assert len(tools.search_captures("J4 header", limit=999).matches) == MAX_LIMIT


# --- get_capture ------------------------------------------------------------


def test_get_capture_returns_full_caption_and_metadata(tmp_path: Path) -> None:
    _, repository = make_environment(tmp_path)
    image_path = tmp_path / "captures" / "capture-1.jpg"
    write_jpeg(image_path, width=64, height=64)
    seed(repository, "capture-1", image_path=image_path)

    detail = make_tools(repository).get_capture("capture-1")

    assert detail.caption == CAPTION
    assert detail.state == "ready"
    assert detail.git_branch == "master"
    assert detail.has_image is True
    assert not hasattr(detail, "image_path")


def test_get_capture_reports_a_missing_image_file(tmp_path: Path) -> None:
    _, repository = make_environment(tmp_path)
    seed(repository, "capture-1", image_path=tmp_path / "gone.jpg")

    assert make_tools(repository).get_capture("capture-1").has_image is False


def test_get_capture_rejects_an_unknown_id_with_a_recoverable_message(tmp_path: Path) -> None:
    _, repository = make_environment(tmp_path)

    with pytest.raises(LookupError) as error:
        make_tools(repository).get_capture("nope")

    assert "nope" in str(error.value)
    assert "search_captures" in str(error.value)


# --- list_recent ------------------------------------------------------------


def test_list_recent_is_newest_first_and_includes_unprocessed_captures(tmp_path: Path) -> None:
    _, repository = make_environment(tmp_path)
    seed(repository, "older", created_at="2026-08-26T10:00:00Z")
    seed(repository, "newer", created_at="2026-08-26T11:00:00Z")
    seed(repository, "processing", created_at="2026-08-26T12:00:00Z", state=CaptureState.PENDING)

    recent = make_tools(repository).list_recent()

    assert [item.capture_id for item in recent] == ["processing", "newer", "older"]
    assert recent[0].state == "pending"
    assert recent[0].summary is None
    assert recent[1].summary == "Header J4 soldered onto the board"


def test_list_recent_honours_its_limit(tmp_path: Path) -> None:
    _, repository = make_environment(tmp_path)
    for index in range(8):
        seed(repository, f"capture-{index}", created_at=f"2026-08-26T1{index}:00:00Z")

    assert len(make_tools(repository).list_recent(limit=3)) == 3
    assert len(make_tools(repository).list_recent()) == 5


# --- get_image --------------------------------------------------------------


def test_get_image_downscales_to_the_max_edge(tmp_path: Path) -> None:
    _, repository = make_environment(tmp_path)
    image_path = tmp_path / "captures" / "capture-1.jpg"
    write_jpeg(image_path, width=2048, height=1536)
    seed(repository, "capture-1", image_path=image_path)

    payload = make_tools(repository).get_image_bytes("capture-1")

    decoded = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
    height, width = decoded.shape[:2]
    assert max(height, width) == MAX_IMAGE_EDGE
    assert (width, height) == (1024, 768)


def test_get_image_leaves_a_small_image_alone(tmp_path: Path) -> None:
    _, repository = make_environment(tmp_path)
    image_path = tmp_path / "captures" / "capture-1.jpg"
    write_jpeg(image_path, width=320, height=240)
    seed(repository, "capture-1", image_path=image_path)

    payload = make_tools(repository).get_image_bytes("capture-1")

    decoded = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape[:2] == (240, 320)


def test_get_image_rejects_an_unknown_id(tmp_path: Path) -> None:
    _, repository = make_environment(tmp_path)

    with pytest.raises(LookupError, match="nope"):
        make_tools(repository).get_image_bytes("nope")


def test_get_image_reports_a_capture_whose_file_is_gone(tmp_path: Path) -> None:
    _, repository = make_environment(tmp_path)
    seed(repository, "capture-1", image_path=tmp_path / "gone.jpg")

    with pytest.raises(RuntimeError, match="no image file"):
        make_tools(repository).get_image_bytes("capture-1")


# --- Through the MCP dispatch layer ----------------------------------------


def test_search_dispatches_through_mcp_with_structured_content(tmp_path: Path) -> None:
    settings, repository = make_environment(tmp_path)
    seed(repository, "capture-1")
    server = create_mcp_server(settings, embedding_provider=StubEmbeddingProvider())

    result = call(server, "search_captures", {"query": "J4 header"})

    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["matches"][0]["capture_id"] == "capture-1"


def test_get_image_dispatches_as_image_content(tmp_path: Path) -> None:
    settings, repository = make_environment(tmp_path)
    image_path = tmp_path / "captures" / "capture-1.jpg"
    write_jpeg(image_path, width=2048, height=1536)
    seed(repository, "capture-1", image_path=image_path)
    server = create_mcp_server(settings, embedding_provider=StubEmbeddingProvider())

    result = call(server, "get_image", {"capture_id": "capture-1"})

    assert result.is_error is False
    block = result.content[0]
    assert block.type == "image"
    assert block.mime_type == "image/jpeg"
    decoded = cv2.imdecode(
        np.frombuffer(base64.b64decode(block.data), dtype=np.uint8), cv2.IMREAD_COLOR
    )
    assert max(decoded.shape[:2]) == MAX_IMAGE_EDGE


def test_an_invalid_id_reaches_the_model_as_an_explained_tool_error(tmp_path: Path) -> None:
    """The SDK preserves a ToolError's message but strips every other exception's.

    Raising the repository's own CaptureNotFoundError here would reach the
    model as a bare "Error executing tool get_capture", which is why the tool
    boundary translates expected failures into ToolError.
    """
    settings, _ = make_environment(tmp_path)
    server = create_mcp_server(settings, embedding_provider=StubEmbeddingProvider())

    with pytest.raises(ToolError) as error:
        call(server, "get_capture", {"capture_id": "nope"})

    assert not isinstance(error.value, UnexpectedToolError)
    assert "nope" in str(error.value)
    assert "search_captures" in str(error.value)


def test_a_bad_limit_reaches_the_model_as_an_explained_tool_error(tmp_path: Path) -> None:
    settings, _ = make_environment(tmp_path)
    server = create_mcp_server(settings, embedding_provider=StubEmbeddingProvider())

    with pytest.raises(ToolError) as error:
        call(server, "list_recent", {"limit": 0})

    assert not isinstance(error.value, UnexpectedToolError)
    assert "at least 1" in str(error.value)
