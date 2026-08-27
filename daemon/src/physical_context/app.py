from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from physical_context.anthropic_caption import AnthropicCaptionProvider
from physical_context.api import router as capture_router
from physical_context.captions import CaptionProvider, UnavailableCaptionProvider
from physical_context.capture_processor import CaptureProcessor, CaptureTaskRunner
from physical_context.capture_service import CaptureService
from physical_context.config import Settings
from physical_context.embeddings import EmbeddingProvider, UnavailableEmbeddingProvider
from physical_context.image_quality import ImageQualityAnalyzer
from physical_context.models import CaptureState
from physical_context.repository import CaptureRepository
from physical_context.runtime import initialize_storage
from physical_context.search import CaptureSearch
from physical_context.voyage_embedding import VoyageEmbeddingProvider


def create_app(
    settings: Settings | None = None,
    caption_provider: CaptionProvider | None = None,
    embedding_provider: EmbeddingProvider | None = None,
) -> FastAPI:
    active_settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = active_settings
        app.state.database = initialize_storage(active_settings)
        repository = CaptureRepository(app.state.database)
        repository.requeue_captioning()
        active_caption_provider = caption_provider or _build_caption_provider(active_settings)
        active_embedding_provider = embedding_provider or _build_embedding_provider(active_settings)
        app.state.capture_tasks = CaptureTaskRunner(
            CaptureProcessor(repository, active_caption_provider, active_embedding_provider)
        )
        app.state.capture_search = CaptureSearch(
            repository,
            active_embedding_provider,
            max_semantic_distance=active_settings.semantic_distance_threshold,
        )
        app.state.capture_service = CaptureService(
            repository,
            active_settings.captures_dir,
            ImageQualityAnalyzer(
                sharpness_threshold=active_settings.sharpness_threshold,
                brightness_threshold=active_settings.brightness_threshold,
            ),
        )
        for capture_id in repository.list_ids_by_state(CaptureState.PENDING):
            app.state.capture_tasks.schedule(capture_id)
        for capture_id in repository.list_ids_missing_embeddings():
            app.state.capture_tasks.schedule(capture_id)
        try:
            yield
        finally:
            await app.state.capture_tasks.close()

    application = FastAPI(title="Physical Context Layer", lifespan=lifespan)
    application.include_router(capture_router)

    @application.get("/health", tags=["operations"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return application


def _build_caption_provider(settings: Settings) -> CaptionProvider:
    if settings.local_caption:
        return UnavailableCaptionProvider("Local captioning is deferred to T-015")

    api_key = settings.anthropic_api_key
    model = settings.anthropic_model
    if api_key is None or model is None or not model.strip():
        return UnavailableCaptionProvider(
            "Set PCL_ANTHROPIC_API_KEY and PCL_ANTHROPIC_MODEL to enable captioning"
        )

    return AnthropicCaptionProvider(
        api_key=api_key.get_secret_value(),
        model=model,
    )


def _build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    if settings.local_embed:
        return UnavailableEmbeddingProvider("Local embedding is deferred to T-016")

    api_key = settings.voyage_api_key
    model = settings.voyage_model
    if api_key is None or model is None or not model.strip():
        return UnavailableEmbeddingProvider(
            "Set PCL_VOYAGE_API_KEY and PCL_VOYAGE_MODEL to enable embeddings"
        )

    return VoyageEmbeddingProvider(
        api_key=api_key.get_secret_value(),
        model=model,
    )


app = create_app()
