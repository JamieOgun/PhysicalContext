from pathlib import Path

import cv2
import numpy as np
import pytest

from physical_context.image_quality import ImageDecodeError, ImageQualityAnalyzer


def write_image(path: Path, pixels: np.ndarray) -> None:
    assert cv2.imwrite(str(path), pixels)


def test_measurement_distinguishes_flat_and_detailed_images(tmp_path: Path) -> None:
    flat_path = tmp_path / "flat.jpg"
    detailed_path = tmp_path / "detailed.jpg"
    flat = np.full((64, 64), 128, dtype=np.uint8)
    detailed = ((np.indices((64, 64)).sum(axis=0) % 2) * 255).astype(np.uint8)
    write_image(flat_path, flat)
    write_image(detailed_path, detailed)
    analyzer = ImageQualityAnalyzer()

    flat_result = analyzer.measure(flat_path)
    detailed_result = analyzer.measure(detailed_path)

    assert flat_result.brightness == pytest.approx(128, abs=1)
    assert detailed_result.sharpness > flat_result.sharpness
    assert flat_result.is_blurry is None
    assert flat_result.is_dark is None


def test_thresholds_classify_measurements(tmp_path: Path) -> None:
    image_path = tmp_path / "black.jpg"
    write_image(image_path, np.zeros((32, 32), dtype=np.uint8))
    analyzer = ImageQualityAnalyzer(sharpness_threshold=1.0, brightness_threshold=1.0)

    result = analyzer.measure(image_path)

    assert result.is_blurry is True
    assert result.is_dark is True


def test_invalid_image_is_rejected(tmp_path: Path) -> None:
    image_path = tmp_path / "invalid.jpg"
    image_path.write_bytes(b"not-an-image")

    with pytest.raises(ImageDecodeError):
        ImageQualityAnalyzer().measure(image_path)
