from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from physical_context.config import Settings
from physical_context.runtime import initialize_storage


def create_app(settings: Settings | None = None) -> FastAPI:
    active_settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        initialize_storage(active_settings)
        app.state.settings = active_settings
        yield

    application = FastAPI(title="Physical Context Layer", lifespan=lifespan)

    @application.get("/health", tags=["operations"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return application


app = create_app()
