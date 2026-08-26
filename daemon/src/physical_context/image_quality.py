from dataclasses import dataclass
from pathlib import Path

import cv2


class ImageDecodeError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class QualityMeasurement:
    sharpness: float
    brightness: float
    is_blurry: bool | None
    is_dark: bool | None


class ImageQualityAnalyzer:
    def __init__(
        self,
        *,
        sharpness_threshold: float | None = None,
        brightness_threshold: float | None = None,
    ) -> None:
        self.sharpness_threshold = sharpness_threshold
        self.brightness_threshold = brightness_threshold

    def measure(self, image_path: Path) -> QualityMeasurement:
        grayscale = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
        if grayscale is None:
            raise ImageDecodeError(f"Could not decode image: {image_path.name}")

        denoised = cv2.GaussianBlur(grayscale, (3, 3), 0)
        laplacian = cv2.Laplacian(denoised, cv2.CV_64F, ksize=3)
        sharpness = float(laplacian.var())
        brightness = float(grayscale.mean())

        return QualityMeasurement(
            sharpness=sharpness,
            brightness=brightness,
            is_blurry=(
                sharpness < self.sharpness_threshold
                if self.sharpness_threshold is not None
                else None
            ),
            is_dark=(
                brightness < self.brightness_threshold
                if self.brightness_threshold is not None
                else None
            ),
        )
