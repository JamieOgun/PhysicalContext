from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from physical_context.api import router as capture_router
from physical_context.capture_service import CaptureService
from physical_context.config import Settings
from physical_context.image_quality import ImageQualityAnalyzer
from physical_context.repository import CaptureRepository
from physical_context.runtime import initialize_storage


def create_app(settings: Settings | None = None) -> FastAPI:
    active_settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = active_settings
        app.state.database = initialize_storage(active_settings)
        repository = CaptureRepository(app.state.database)
        repository.requeue_captioning()
        app.state.capture_service = CaptureService(
            repository,
            active_settings.captures_dir,
            ImageQualityAnalyzer(
                sharpness_threshold=active_settings.sharpness_threshold,
                brightness_threshold=active_settings.brightness_threshold,
            ),
        )
        yield

    application = FastAPI(title="Physical Context Layer", lifespan=lifespan)
    application.include_router(capture_router)

    @application.get("/health", tags=["operations"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return application


app = create_app()
