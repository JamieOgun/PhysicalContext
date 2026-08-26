from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile, status
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from physical_context.capture_service import CaptureService, InvalidCaptureError
from physical_context.capture_processor import CaptureTaskRunner
from physical_context.models import CaptureState

router = APIRouter()


class CaptureResponse(BaseModel):
    capture_id: str
    short_id: str
    deduplicated: bool
    sharpness: float | None
    brightness: float | None
    is_blurry: bool | None
    is_dark: bool | None


@router.post("/capture", response_model=CaptureResponse, status_code=status.HTTP_201_CREATED)
async def create_capture(
    request: Request,
    response: Response,
    image: Annotated[UploadFile, File()],
    device_ts: Annotated[int, Form()],
    device_id: Annotated[str, Form(min_length=1, max_length=128)],
    client_capture_id: Annotated[str, Form(min_length=1, max_length=128)],
) -> CaptureResponse:
    if image.content_type != "image/jpeg":
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="image must use image/jpeg",
        )

    service: CaptureService = request.app.state.capture_service
    try:
        result = await run_in_threadpool(
            service.ingest,
            image.file,
            device_ts=device_ts,
            device_id=device_id,
            client_capture_id=client_capture_id,
        )
    except InvalidCaptureError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        ) from error

    if result.deduplicated:
        response.status_code = status.HTTP_200_OK

    if result.capture.state == CaptureState.PENDING:
        capture_tasks: CaptureTaskRunner = request.app.state.capture_tasks
        capture_tasks.schedule(result.capture.id)

    return CaptureResponse(
        capture_id=result.capture.id,
        short_id=result.capture.id[:8],
        deduplicated=result.deduplicated,
        sharpness=result.capture.sharpness,
        brightness=result.capture.brightness,
        is_blurry=result.capture.is_blurry,
        is_dark=result.capture.is_dark,
    )
