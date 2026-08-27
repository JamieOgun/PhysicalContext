import base64
import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

from mcp.server import MCPServer
from mcp.server.mcpserver import Image
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, ImageContent, TextContent

from physical_context.config import Settings
from physical_context.embeddings import EmbeddingProvider
from physical_context.image_quality import ImageDecodeError
from physical_context.images import MAX_IMAGE_EDGE, load_downscaled_jpeg
from physical_context.models import Capture
from physical_context.providers import build_embedding_provider
from physical_context.repository import CaptureNotFoundError, CaptureRepository
from physical_context.runtime import initialize_storage
from physical_context.search import DEFAULT_LIMIT, CaptureSearch

logger = logging.getLogger(__name__)

SERVER_NAME = "physical-context"
MAX_LIMIT = 25

# Search results carry pixels at half the edge of a get_image call: enough to
# tell captures apart at a glance, a quarter of the tokens, and it leaves
# get_image as the tier you escalate to when you need to read fine detail.
SEARCH_IMAGE_EDGE = 512


class ImageUnavailableError(RuntimeError):
    pass


class InvalidToolInputError(ValueError):
    pass


# Errors a caller can act on: the MCP boundary forwards their text to the model.
# Anything else is a crash, and the SDK deliberately strips its message.
EXPECTED_TOOL_ERRORS = (
    CaptureNotFoundError,
    ImageUnavailableError,
    ImageDecodeError,
    InvalidToolInputError,
)


@contextmanager
def _explained_errors() -> Iterator[None]:
    """Re-raise expected failures as ToolError so the model is told what went wrong.

    The SDK only preserves the message of a `ToolError`; every other exception
    reaches the model as a bare "Error executing tool <name>", which would make
    an invalid capture ID indistinguishable from a daemon bug.
    """
    try:
        yield
    except EXPECTED_TOOL_ERRORS as error:
        raise ToolError(str(error)) from error


@dataclass
class CaptureMatch:
    capture_id: str
    created_at: str
    summary: str
    tags: list[str]
    matched_by: str
    score: float


@dataclass
class SearchCapturesResult:
    query: str
    matches: list[CaptureMatch]
    note: str | None = None


@dataclass
class CaptureDetail:
    capture_id: str
    created_at: str
    ready_at: str | None
    state: str
    caption: str | None
    tags: list[str]
    device_id: str | None
    device_ts: int | None
    # Named for its owner: this is the machine running the daemon, not where the
    # picture was taken. The bare name reads as the latter and gets misreported.
    daemon_hostname: str | None
    git_repo: str | None
    git_branch: str | None
    git_sha: str | None
    sharpness: float | None
    brightness: float | None
    is_blurry: bool | None
    is_dark: bool | None
    has_image: bool


@dataclass
class RecentCapture:
    capture_id: str
    created_at: str
    state: str
    summary: str | None
    tags: list[str]


class CaptureTools:
    """Tool behaviour, independent of the MCP transport that exposes it."""

    def __init__(self, repository: CaptureRepository, search: CaptureSearch) -> None:
        self.repository = repository
        self.search = search

    def search_captures(self, query: str, limit: int = DEFAULT_LIMIT) -> SearchCapturesResult:
        response = self.search.search(query, limit=_checked_limit(limit))
        return SearchCapturesResult(
            query=response.query,
            matches=[
                CaptureMatch(
                    capture_id=result.capture_id,
                    created_at=result.created_at,
                    summary=result.snippet,
                    tags=list(result.tags),
                    matched_by=str(result.matched_by),
                    score=result.score,
                )
                for result in response.results
            ],
            note=response.note,
        )

    def get_capture(self, capture_id: str) -> CaptureDetail:
        capture = self._require(capture_id)
        return CaptureDetail(
            capture_id=capture.id,
            created_at=capture.created_at,
            ready_at=capture.ready_at,
            state=str(capture.state),
            caption=capture.caption,
            tags=list(capture.tags),
            device_id=capture.device_id,
            device_ts=capture.device_ts,
            daemon_hostname=capture.hostname,
            git_repo=capture.git_repo,
            git_branch=capture.git_branch,
            git_sha=capture.git_sha,
            sharpness=capture.sharpness,
            brightness=capture.brightness,
            is_blurry=capture.is_blurry,
            is_dark=capture.is_dark,
            has_image=Path(capture.image_path).is_file(),
        )

    def list_recent(self, limit: int = DEFAULT_LIMIT) -> list[RecentCapture]:
        return [
            RecentCapture(
                capture_id=capture.id,
                created_at=capture.created_at,
                state=str(capture.state),
                summary=_summary_line(capture.caption),
                tags=list(capture.tags),
            )
            for capture in self.repository.list_recent(limit=_checked_limit(limit))
        ]

    def thumbnails(self, capture_ids: list[str]) -> list[tuple[str, bytes]]:
        """Small images for search results, skipping any capture missing its file.

        A capture whose file is gone must still appear as a text match, so a
        missing or unreadable image is dropped rather than raised.
        """
        found: list[tuple[str, bytes]] = []
        for capture_id in capture_ids:
            capture = self.repository.get(capture_id)
            if capture is None:
                continue
            image_path = Path(capture.image_path)
            if not image_path.is_file():
                continue
            try:
                found.append(
                    (capture_id, load_downscaled_jpeg(image_path, max_edge=SEARCH_IMAGE_EDGE))
                )
            except ImageDecodeError:
                logger.warning("thumbnail_unreadable capture_id=%s", capture_id)
        return found

    def get_image_bytes(self, capture_id: str) -> bytes:
        capture = self._require(capture_id)
        image_path = Path(capture.image_path)
        if not image_path.is_file():
            raise ImageUnavailableError(
                f"Capture {capture_id!r} has no image file on disk at {image_path.name}."
            )
        return load_downscaled_jpeg(image_path, max_edge=MAX_IMAGE_EDGE)

    def _require(self, capture_id: str) -> Capture:
        capture = self.repository.get(capture_id)
        if capture is None:
            raise CaptureNotFoundError(
                f"No capture with ID {capture_id!r}. "
                "Use search_captures or list_recent to find valid capture IDs."
            )
        return capture


def _checked_limit(limit: int) -> int:
    if limit < 1:
        raise InvalidToolInputError("limit must be at least 1")
    return min(limit, MAX_LIMIT)


def _summary_line(caption: str | None) -> str | None:
    if caption is None:
        return None
    return caption.split("\n", 1)[0].strip()


def create_mcp_server(
    settings: Settings | None = None,
    *,
    embedding_provider: EmbeddingProvider | None = None,
) -> MCPServer:
    active_settings = settings or Settings()
    repository = CaptureRepository(initialize_storage(active_settings))
    tools = CaptureTools(
        repository,
        CaptureSearch(
            repository,
            embedding_provider or build_embedding_provider(active_settings),
            max_semantic_distance=active_settings.semantic_distance_threshold,
        ),
    )

    mcp = MCPServer(SERVER_NAME)

    @mcp.tool()
    def search_captures(
        query: str,
        limit: int = DEFAULT_LIMIT,
        include_images: bool = True,
    ) -> SearchCapturesResult:
        """Find captures of the user's physical workspace by describing them.

        Searches caption text two ways at once: keyword matching for precise
        references ("J4", "U3"), and semantic similarity for descriptions that
        do not share the caption's wording ("looked burnt").

        Each match carries the caption's summary line, and by default the photo
        itself at reduced size, so you can see what was captured without a
        second call. Pass include_images=False when you only need to scan text,
        or when a wide limit would return more pictures than you want to look
        at. Call get_image for one capture at full size when you need to read
        fine detail, and get_capture for the complete structured caption.

        If nothing matches, `matches` is empty and `note` says so rather than
        returning unrelated captures.
        """
        with _explained_errors():
            result = tools.search_captures(query, limit)
            if not include_images or not result.matches:
                return result

            blocks: list[TextContent | ImageContent] = []
            for capture_id, image in tools.thumbnails(
                [match.capture_id for match in result.matches]
            ):
                # Label each image so the model can tie pixels to the right match.
                blocks.append(TextContent(type="text", text=f"capture {capture_id}:"))
                blocks.append(
                    ImageContent(
                        type="image",
                        data=base64.b64encode(image).decode("ascii"),
                        mimeType="image/jpeg",
                    )
                )
            if not blocks:
                return result
            return CallToolResult(content=blocks, structured_content=asdict(result))

    @mcp.tool()
    def get_capture(capture_id: str) -> CaptureDetail:
        """Full caption and metadata for one capture ID, without image bytes.

        Use after search_captures or list_recent to read the complete
        structured caption: observable details, visible text, spatial
        relationships, changes from the previous capture, and uncertainties.
        """
        with _explained_errors():
            return tools.get_capture(capture_id)

    @mcp.tool()
    def list_recent(limit: int = DEFAULT_LIMIT) -> list[RecentCapture]:
        """The most recent captures, newest first, without image bytes.

        Answers "what was I just working on" when there is no obvious search
        term. Includes captures still being processed, so `state` may be
        `pending` or `captioning` with no summary yet.
        """
        with _explained_errors():
            return tools.list_recent(limit)

    @mcp.tool()
    def get_image(capture_id: str) -> Image:
        """The photo for one capture ID, downscaled to a 1024px longest edge.

        Use when the caption does not answer the question and the pixels
        themselves matter: reading a part number, checking a wire colour,
        seeing a bent pin. One image per call, and it costs roughly a thousand
        tokens, so prefer get_capture when the caption would do.
        """
        with _explained_errors():
            return Image(data=tools.get_image_bytes(capture_id), format="jpeg")

    return mcp


def main() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    create_mcp_server().run()


if __name__ == "__main__":
    main()
