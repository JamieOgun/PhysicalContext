import os
import shutil
import sqlite3
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from physical_context.ambient_context import AmbientContextResolver
from physical_context.image_quality import ImageDecodeError, ImageQualityAnalyzer
from physical_context.models import Capture, CaptureState
from physical_context.repository import CaptureRepository, utc_timestamp


class InvalidCaptureError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class IngestResult:
    capture: Capture
    deduplicated: bool


class CaptureService:
    def __init__(
        self,
        repository: CaptureRepository,
        captures_dir: Path,
        quality_analyzer: ImageQualityAnalyzer,
        context_resolver: AmbientContextResolver | None = None,
    ) -> None:
        self.repository = repository
        self.captures_dir = captures_dir
        self.quality_analyzer = quality_analyzer
        self.context_resolver = context_resolver or AmbientContextResolver()

    def ingest(
        self,
        image: BinaryIO,
        *,
        device_ts: int,
        device_id: str,
        client_capture_id: str,
    ) -> IngestResult:
        if not device_id.strip():
            raise InvalidCaptureError("device_id must not be blank")
        if not client_capture_id.strip():
            raise InvalidCaptureError("client_capture_id must not be blank")

        existing = self.repository.get_by_client_capture_id(client_capture_id)
        if existing is not None:
            if _can_replace_stale_capture(existing):
                self.repository.delete(existing.id)
            else:
                return IngestResult(capture=existing, deduplicated=True)

        capture_id = uuid.uuid4().hex
        image_path = self.captures_dir / f"{capture_id}.jpg"
        self._write_image(image, image_path)

        context = self.context_resolver.resolve()
        capture = Capture(
            id=capture_id,
            client_capture_id=client_capture_id,
            created_at=utc_timestamp(),
            device_ts=device_ts,
            device_id=device_id,
            image_path=str(image_path),
            hostname=context.hostname,
            git_repo=context.git_repo,
            git_branch=context.git_branch,
            git_sha=context.git_sha,
            state=CaptureState.UPLOADED,
        )

        try:
            self.repository.insert(capture)
        except sqlite3.IntegrityError:
            image_path.unlink(missing_ok=True)
            existing = self.repository.get_by_client_capture_id(client_capture_id)
            if existing is not None:
                return IngestResult(capture=existing, deduplicated=True)
            raise
        except Exception:
            image_path.unlink(missing_ok=True)
            raise

        try:
            quality = self.quality_analyzer.measure(image_path)
            self.repository.record_quality(
                capture.id,
                sharpness=quality.sharpness,
                brightness=quality.brightness,
                is_blurry=quality.is_blurry,
                is_dark=quality.is_dark,
            )
        except ImageDecodeError as error:
            self.repository.delete(capture.id)
            image_path.unlink(missing_ok=True)
            raise InvalidCaptureError("image is not a decodable JPEG") from error
        except Exception:
            self.repository.delete(capture.id)
            image_path.unlink(missing_ok=True)
            raise

        processed_capture = self.repository.get(capture.id)
        if processed_capture is None:
            raise RuntimeError(f"Capture disappeared after processing: {capture.id}")
        return IngestResult(capture=processed_capture, deduplicated=False)

    def _write_image(self, image: BinaryIO, destination: Path) -> None:
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=".capture-",
                suffix=".tmp",
                dir=self.captures_dir,
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                image.seek(0)
                shutil.copyfileobj(image, temporary)
                if temporary.tell() == 0:
                    raise InvalidCaptureError("image must not be empty")
                temporary.flush()
                os.fsync(temporary.fileno())

            if destination.exists():
                raise FileExistsError(destination)
            temporary_path.replace(destination)
            temporary_path = None
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)


def _can_replace_stale_capture(capture: Capture) -> bool:
    return capture.caption is None and not Path(capture.image_path).is_file()
