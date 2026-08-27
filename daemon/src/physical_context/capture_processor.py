import asyncio
import logging
from pathlib import Path

from physical_context.captions import CaptionProvider, StructuredCaption
from physical_context.embeddings import EmbeddingProvider, validate_embedding
from physical_context.models import Capture, CaptureState
from physical_context.repository import CaptureRepository

logger = logging.getLogger(__name__)


class CaptureProcessor:
    def __init__(
        self,
        repository: CaptureRepository,
        caption_provider: CaptionProvider,
        embedding_provider: EmbeddingProvider,
    ) -> None:
        self.repository = repository
        self.caption_provider = caption_provider
        self.embedding_provider = embedding_provider

    def process(self, capture_id: str) -> None:
        capture = self.repository.get(capture_id)
        if capture is None:
            logger.warning("processing_skipped capture_id=%s reason=not_found", capture_id)
            return
        if capture.state == CaptureState.PENDING:
            self._caption_and_embed(capture)
        elif (
            capture.state == CaptureState.READY
            and capture.caption is not None
            and not self.repository.has_embedding(capture.id)
        ):
            self._backfill_embedding(capture)

    def _caption_and_embed(self, capture: Capture) -> None:
        image_path = Path(capture.image_path)
        if not image_path.is_file():
            logger.warning(
                "caption_skipped capture_id=%s reason=image_missing image_path=%s",
                capture.id,
                image_path,
            )
            self.repository.delete(capture.id)
            return

        self.repository.transition_state(capture.id, CaptureState.CAPTIONING)
        try:
            result = self.caption_provider.caption(
                image_path,
                self.repository.get_previous_caption(capture.id),
            )
            caption = StructuredCaption.model_validate(result).to_search_text()
        except Exception:
            logger.exception("caption_failed capture_id=%s", capture.id)
            self.repository.write_search_indexes(
                capture.id,
                caption=None,
                tags=capture.tags,
                embedding=None,
            )
            self.repository.transition_state(capture.id, CaptureState.READY)
            return

        embedding = self._generate_embedding(capture.id, caption)
        self.repository.write_search_indexes(
            capture.id,
            caption=caption,
            tags=capture.tags,
            embedding=embedding,
        )
        self.repository.transition_state(capture.id, CaptureState.READY)

    def _backfill_embedding(self, capture: Capture) -> None:
        embedding = self._generate_embedding(capture.id, capture.caption)
        if embedding is not None:
            self.repository.write_embedding(capture.id, embedding)

    def _generate_embedding(self, capture_id: str, caption: str) -> tuple[float, ...] | None:
        try:
            result = self.embedding_provider.embed(caption, input_type="document")
            return validate_embedding(result)
        except Exception:
            logger.exception("embedding_failed capture_id=%s", capture_id)
            return None


class CaptureTaskRunner:
    def __init__(self, processor: CaptureProcessor) -> None:
        self.processor = processor
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def schedule(self, capture_id: str) -> None:
        existing = self._tasks.get(capture_id)
        if existing is not None and not existing.done():
            return

        task = asyncio.create_task(asyncio.to_thread(self.processor.process, capture_id))
        self._tasks[capture_id] = task
        task.add_done_callback(lambda completed: self._task_finished(capture_id, completed))

    async def close(self) -> None:
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks.values()), return_exceptions=True)

    def _task_finished(self, capture_id: str, task: asyncio.Task[None]) -> None:
        self._tasks.pop(capture_id, None)
        if task.cancelled():
            return

        error = task.exception()
        if error is not None:
            logger.error(
                "processing_task_failed capture_id=%s",
                capture_id,
                exc_info=(type(error), error, error.__traceback__),
            )
